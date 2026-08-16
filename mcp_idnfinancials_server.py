#!/usr/bin/env python
"""MCP server for IDNFinancials (https://www.idnfinancials.com) public data.

Exposes a read-only tool layer over IDNFinancials' public company data JSON
endpoints (the same ones the website's own frontend uses). Built with hard
safety limits:

  * READ-ONLY: only the `company/data/*` GET-style POST endpoints are used.
    No writes, no follow/unfollow, no account access, no premium/fs3 PDFs.
  * RATE LIMIT: at most RATE_LIMIT_INTERVAL seconds between upstream calls.
  * CACHE: successful responses are cached for CACHE_TTL seconds to avoid
    hammering the upstream site.
  * ATTRIBUTION: every response carries the attribution required by the
    site's Terms of Service (IDNFinancials / PT AP&M Indonesia).
  * NON-COMMERCIAL: for personal, non-commercial use only.
  * NO EVASION: normal browser User-Agent, no proxy rotation, no obfuscation.
    The upstream can block this IP at any time; that is an accepted risk.
  * Never expose credentials; no API key is needed for these public endpoints.

The X-NLN header is a static site nonce embedded in every public page; it is
not a secret. It is read once from the homepage (fallback constant below) so
the server keeps working if the site rotates it.

Run:  python mcp_idnfinancials_server.py  (stdio transport, for Hermes)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

BASE_URL = "https://www.idnfinancials.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Fallback nonce; server refreshes it from the homepage on startup/periodically.
FALLBACK_NLN = "6f3ba2b26cdab5cd5630083e15a9b69f"

# ---- hard limits (configurable via env, defaults are the safety baseline) ----
RATE_LIMIT_INTERVAL = float(os.environ.get("IDNF_RATE_LIMIT", "1.2"))  # seconds between upstream calls
CACHE_TTL = int(os.environ.get("IDNF_CACHE_TTL", "300"))               # seconds; 0 disables cache
REQUEST_TIMEOUT = int(os.environ.get("IDNF_TIMEOUT", "25"))            # seconds
MAX_PAGINATE = int(os.environ.get("IDNF_MAX_PAGINATE", "200"))         # cap on offset+limit combos

mcp = FastMCP(
    "idnfinancials",
    instructions=(
        "Read-only access to public IDNFinancials company data (IDX-listed "
        "Indonesian issuers). Personal, non-commercial use only. All data is "
        "delayed/market-informational, NOT investment advice; verify before "
        "trading. Attribution: 'Digunakan dengan izin dari IDNFinancials.com, "
        "layanan dari PT AP&M Indonesia.' Never attempt premium PDF access."
    ),
)

# ---------------------------------------------------------------- plumbing ----
_lock = threading.Lock()
_last_request_ts = 0.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_nln = FALLBACK_NLN


def _refresh_nln() -> str:
    """Read the site nonce from the homepage inline script (best effort)."""
    global _nln
    try:
        req = urllib.request.Request(BASE_URL + "/id/", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        import re

        m = re.search(r'nln:\s*"([a-f0-9]{32})"', html)
        if m:
            _nln = m.group(1)
    except Exception:  # noqa: BLE001 - keep fallback on any failure
        pass
    return _nln


def _throttle() -> None:
    global _last_request_ts
    with _lock:
        now = time.monotonic()
        wait = RATE_LIMIT_INTERVAL - (now - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def _cache_get(key: str) -> dict[str, Any] | None:
    if CACHE_TTL <= 0:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        if hit:
            _cache.pop(key, None)
    return None


def _cache_put(key: str, data: dict[str, Any]) -> None:
    if CACHE_TTL <= 0:
        return
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (time.monotonic(), data)


def _call_section(section: str, params: dict[str, Any]) -> dict[str, Any]:
    """POST to /id/company/data/{section} with the site nonce and rate limit."""
    key = section + "|" + json.dumps(params, sort_keys=True)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    _throttle()
    url = f"{BASE_URL}/id/company/data/{urllib.parse.quote(section)}"
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": UA,
            "X-NLN": _nln,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        # site nonce may rotate between page loads; refresh once on 404-ish shape
        if isinstance(parsed, dict) and parsed.get("success") is False and parsed.get("data") is None:
            _refresh_nln()
        _cache_put(key, parsed)
        return parsed
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - structured errors for MCP
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _wrap(section: str, params: dict[str, Any], *, raw: bool = False) -> dict[str, Any]:
    resp = _call_section(section, params)
    out: dict[str, Any] = {
        "source": "idnfinancials.com",
        "attribution": "Digunakan dengan izin dari IDNFinancials.com, layanan dari PT AP&M Indonesia.",
        "notice": "Data untuk penggunaan pribadi non-komersial. Data pasar tertunda ~15 menit dan bukan nasihat investasi.",
        "requested": {"section": section, "params": params},
    }
    if raw:
        out["raw"] = resp
    elif isinstance(resp, dict) and resp.get("success") is True:
        data = resp.get("data")
        if data is None or data == "e404":
            out["error"] = f"Tidak ada data untuk section '{section}' (kode mungkin salah atau data tidak tersedia)."
            out["data"] = None
        else:
            out["data"] = data
            if isinstance(data, list) and not data:
                out["empty"] = True
            elif isinstance(data, dict):
                vals = list(data.values())
                if not vals or all(v == "e404" or v is None or v == [] for v in vals):
                    out["empty"] = True
    elif isinstance(resp, dict) and resp.get("ok") is False:
        out["error"] = resp.get("error", "upstream error")
    else:
        out["error"] = (resp or {}).get("message") or "no data"
    return out


def _company_params(company: str, **extra: Any) -> dict[str, Any]:
    return {"company": company.upper().strip(), **extra}


# ------------------------------------------------------------------ tools ----
@mcp.tool()
def health() -> dict[str, Any]:
    """Check the IDNFinancials MCP server is up and report its limits."""
    return {
        "ok": True,
        "source": "idnfinancials.com",
        "limits": {
            "rate_limit_seconds": RATE_LIMIT_INTERVAL,
            "cache_ttl_seconds": CACHE_TTL,
            "timeout_seconds": REQUEST_TIMEOUT,
            "max_paginate": MAX_PAGINATE,
            "read_only": True,
            "premium_access": False,
        },
    }


@mcp.tool()
def get_financial_overview(company: str) -> dict[str, Any]:
    """Get quarterly revenue/net-profit overview + report PDF links + IPO prospectus for a company.

    Args:
        company: IDX ticker, e.g. 'BYAN' or 'BBCA'.
    """
    return _wrap("financial", _company_params(company))


@mcp.tool()
def get_price_history(company: str) -> dict[str, Any]:
    """Get full historical price series for a company.

    Returns raw [[unix_ts, price, volume], ...] rows (the site chart source).
    Large series may be truncated by upstream pagination.

    Args:
        company: IDX ticker, e.g. 'BYAN'.
    """
    return _wrap("chart", _company_params(company))


@mcp.tool()
def get_net_foreign(company: str, mode: str = "daily") -> dict[str, Any]:
    """Get foreign investor buy/sell/net volume for a company.

    Args:
        company: IDX ticker, e.g. 'BYAN'.
        mode: 'daily', 'weekly', or 'monthly' (default 'daily').
    """
    if mode not in ("daily", "weekly", "monthly"):
        mode = "daily"
    return _wrap("net-foreign", _company_params(company, mode=mode))


@mcp.tool()
def get_subsidiaries(company: str) -> dict[str, Any]:
    """Get subsidiary companies and ownership percentage."""
    return _wrap("subsidiary", _company_params(company))


@mcp.tool()
def get_ipo(company: str) -> dict[str, Any]:
    """Get IPO dates, IPO bonds, and rights-issue data."""
    return _wrap("ipo", _company_params(company))


@mcp.tool()
def get_dividend(company: str) -> dict[str, Any]:
    """Get dividend history (cash dividend, cum/ex/payment dates, type)."""
    return _wrap("dividend", _company_params(company))


@mcp.tool()
def get_management(company: str) -> dict[str, Any]:
    """Get board of directors / commissioners."""
    return _wrap("management", _company_params(company))


@mcp.tool()
def get_shareholders(company: str) -> dict[str, Any]:
    """Get top shareholders with shares/percentage/holding date."""
    return _wrap("shareholder", _company_params(company))


@mcp.tool()
def get_free_float(company: str, limit: int = 5, offset: int = 0) -> dict[str, Any]:
    """Get free-float history (percentage + shareholder count per period).

    Args:
        company: IDX ticker.
        limit: max rows (capped at 200).
        offset: pagination offset (capped at 2000).
    """
    limit = min(max(1, int(limit)), MAX_PAGINATE)
    offset = min(max(0, int(offset)), MAX_PAGINATE * 10)
    return _wrap("free-float", _company_params(company, limit=limit, offset=offset))


@mcp.tool()
def get_videos(company: str) -> dict[str, Any]:
    """Get company-related videos."""
    return _wrap("videos", _company_params(company))


@mcp.tool()
def get_news(company: str) -> dict[str, Any]:
    """Get company-related news items (title, summary, url, dates)."""
    return _wrap("news", _company_params(company))


@mcp.tool()
def get_announcements(company: str) -> dict[str, Any]:
    """Get corporate announcements incl. source document links."""
    return _wrap("announcement", _company_params(company))


@mcp.tool()
def get_market_hints(company: str) -> dict[str, Any]:
    """Get market hints: shareholder ownership changes over time."""
    return _wrap("hints", _company_params(company))


@mcp.tool()
def get_bonds(company: str) -> dict[str, Any]:
    """Get company bond/debt call data (may be empty for many issuers)."""
    return _wrap("bonds", _company_params(company))


@mcp.tool()
def get_related_companies(company: str, industry: str = "", subindustry: str = "") -> dict[str, Any]:
    """Get peer companies in the same industry with price/valuation/profit history.

    Args:
        company: IDX ticker.
        industry: optional industry code (e.g. 'A12'); blank lets the server try
                  without the filter (upstream may still return peers).
        subindustry: optional sub-industry code (e.g. 'A121').
    """
    params: dict[str, Any] = {"company": company.upper().strip()}
    if industry:
        params["industry"] = industry
    if subindustry:
        params["subindustry"] = subindustry
    return _wrap("related-company", params)


@mcp.tool()
def get_popular_news() -> dict[str, Any]:
    """Get the site-wide popular news widget (HTML list of latest headlines)."""
    key = "news/popular-news"
    cached = _cache_get(key)
    if cached is None:
        _throttle()
        url = f"{BASE_URL}/id/news/popular-news"
        req = urllib.request.Request(
            url,
            data=b"",
            method="POST",
            headers={
                "User-Agent": UA,
                "X-NLN": _nln,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed: dict[str, Any] = {"ok": True, "html": raw[:20000]}
            _cache_put(key, parsed)
        except Exception as exc:  # noqa: BLE001
            parsed = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        cached = parsed
    out: dict[str, Any] = {
        "source": "idnfinancials.com",
        "attribution": "Digunakan dengan izin dari IDNFinancials.com, layanan dari PT AP&M Indonesia.",
        "notice": "Data untuk penggunaan pribadi non-komersial. Feed global berupa HTML widget.",
    }
    if cached.get("ok"):
        out["data"] = cached.get("html")
    else:
        out["error"] = cached.get("error", "upstream error")
    return out


if __name__ == "__main__":
    _refresh_nln()
    mcp.run(transport="stdio")
