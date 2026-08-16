# IDNFinancials MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that exposes public company data from [IDNFinancials](https://www.idnfinancials.com) (IDX-listed Indonesian issuers) as 17 MCP tools.

The server talks directly to the same public JSON endpoints the IDNFinancials frontend uses — no API key, no account, no premium content. It is built around a strict safety baseline: read-only access, throttled requests, response caching, and mandatory attribution.

> **Disclaimer & Terms of Service**
> This project is provided for **personal, non-commercial use only**. Data is delayed/market-informational and **not investment advice**; verify before trading.
> Per IDNFinancials' terms, attribution is required: *"Digunakan dengan izin dari IDNFinancials.com, layanan dari PT AP&M Indonesia."* The site's `robots.txt` disallows crawling of `/company/data/`; this server replicates the website's own read-only requests at a polite rate without evasions (no proxy rotation, no obfuscation, normal browser user-agent). The upstream may block any IP at any time; that is an accepted risk. **Premium content (quarterly report PDFs, member areas) is never accessed.** Respect the site's terms and this project's license.

## Features

- 17 tools covering financials, prices, foreign flows, subsidiaries, IPO, dividends, management, shareholders, free float, news, announcements, market hints, bonds, and peers.
- No API key required (the site nonce is read automatically from the homepage and cached with a fallback).
- Rate limiting and TTL caching built in as a politeness baseline.
- Consistent response shape on every tool, including empty/error cases.

## Tools (17)

| Tool | Description |
|---|---|
| `health` | Server status + active limits (rate, cache, timeout, read-only). |
| `get_financial_overview` | Quarterly revenue/net-profit overview, report PDF links, IPO prospectus. |
| `get_price_history` | Full historical `[unix_ts, price, volume]` series (site chart source). |
| `get_net_foreign` | Foreign investor buy/sell/net volume (`daily`, `weekly`, `monthly`). |
| `get_subsidiaries` | Subsidiary companies and ownership percentages. |
| `get_ipo` | IPO dates, IPO bonds, rights-issue data. |
| `get_dividend` | Dividend history: cash dividend, cum/ex/payment dates, type. |
| `get_management` | Board of directors / commissioners. |
| `get_shareholders` | Top shareholders with shares, percentage, holding date. |
| `get_free_float` | Free-float history (percentage + shareholder count per period), paginated. |
| `get_videos` | Company-related videos. |
| `get_news` | Company news items (title, summary, URL, dates). |
| `get_announcements` | Corporate announcements incl. source document links. |
| `get_market_hints` | Shareholder ownership changes over time. |
| `get_bonds` | Company bond / debt-call data (empty for many issuers). |
| `get_related_companies` | Peers in the same industry with price/valuation/profit data. |
| `get_popular_news` | Site-wide popular-news widget (HTML snippet of latest headlines). |

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mcp
```

## Run (stdio MCP)

```bash
python mcp_idnfinancials_server.py
```

The server speaks MCP over stdio, so wire it as a stdio MCP server in any MCP client (Claude Desktop, Cursor, Hermes, etc.):

```json
{
  "mcpServers": {
    "idnfinancials": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_idnfinancials_server.py"],
      "env": {
        "IDNF_RATE_LIMIT": "1.2",
        "IDNF_CACHE_TTL": "300"
      }
    }
  }
}
```

## Configuration

All limits are env-configurable; defaults are the polite safety baseline:

| Variable | Default | Meaning |
|---|---|---|
| `IDNF_RATE_LIMIT` | `1.2` | Seconds between requests to the upstream site. |
| `IDNF_CACHE_TTL` | `300` | TTL (seconds) for successful response cache; `0` disables caching. |
| `IDNF_TIMEOUT` | `25` | HTTP timeout (seconds) for upstream calls. |
| `IDNF_MAX_PAGINATE` | `200` | Cap on pagination `limit` (free-float); offsets capped at 10× this. |

Constraints are hard-coded on top of env vars: the server is read-only (no writes, no follow/unfollow, no account access, no premium PDFs) and never evades blocks.

## Response shape

Every tool returns a consistent envelope:

```json
{
  "source": "idnfinancials.com",
  "attribution": "Digunakan dengan izin dari IDNFinancials.com, layanan dari PT AP&M Indonesia.",
  "notice": "Data untuk penggunaan pribadi non-komersial. Data pasar tertunda ~15 menit dan bukan nasihat investasi.",
  "requested": {"section": "...", "params": {...}},
  "data": {...},
  "empty": true
}
```

- Success → `data` (list or dict). Empty result → `data` present plus `empty: true`.
- Unknown ticker / missing section → `error` with a clear message.
- Upstream HTTP failure → `error` such as `HTTP 404`, `HTTP 403` (premium PDFs are never fetched), or `TimeoutError: ...`.

`get_popular_news` returns `data` as an HTML snippet instead of JSON.

## Notes from reconnaissance (2026-08-16)

- **Endpoints**: data comes from `POST https://www.idnfinancials.com/id/company/data/{section}` with `company=CODE`, plus `POST /id/news/popular-news` for the global feed widget. The site's `X-NLN` nonce is public (embedded in every page); the server refreshes it from the homepage on startup and on rotation failures.
- **Coverage**: the company sitemap lists ~964 issuers (`/sitemap-company-id.xml`), i.e. essentially all IDX-listed companies.
- **Free vs premium**: every `company/data/*` section is freely reachable via this path; only quarterly report PDFs (`fs3`) and member pages require a premium login, and this server never attempts them.
- **Verification (2026-08-16)**: `py_compile` clean; direct function smoke tests for financial/dividend/net-foreign/management/news/price-history OK; MCP stdio handshake OK with all 17 tools registered; `get_dividend(BBCA)` returned 3 rows, `get_news(BBRI)` 10 rows, unknown ticker `NOPE` → `empty: true`.

## License

MIT License — Copyright (c) 2026 Aldiansyah / BeaverNest. See [LICENSE](LICENSE).

> Community note: if you publish or distribute anything derived from this data, follow IDNFinancials' Terms of Service (non-commercial use, attribution). This project's MIT license does not override the upstream site's terms.