# 恒生成份股資金流 — HSI Stock Flow

**Live page (Pages snapshot):** https://vhhh2B618.github.io/hsi-stock-flow/

**True LIVE (~5s):** run `python3 server.py` → http://127.0.0.1:8768/


Dark UI for all **95 Hang Seng Index** names with two modes:

1. **A · By sector (Chaikin)** — cumulative Chaikin-style money flow (HKD millions from open), grouped/filterable by Hang Seng Industry Classification-style sectors  
2. **B · HSI pts impact** — estimated session contribution ≈ `weight% × HSI × (last/open − 1)`, sorted by points

Same visual language as [hk-sector-flow](https://vhhh2B618.github.io/hk-sector-flow/) (red = inflow / +pts, green = outflow / −pts). Play / Reset race over 5-minute RTH bars.

**Yahoo free data is delayed. Chaikin and HSI pts are estimates — not exchange tick order-flow. Research only — not financial advice.**

## LIVE (~5 seconds) vs GitHub Pages

| Mode | Freshness | How |
|------|-----------|-----|
| **LIVE** | Soft-poll JSON every **5s**; quote overlay target **5s** (backs off to **10–15s** if Yahoo rate-limits); full Chaikin 5m rebuild every **~120s** | `python3 server.py` → http://127.0.0.1:8768/ |
| **GitHub Pages** | Hourly cron during cash hours (same pattern as sector page) | Static snapshot — client still polls `stock_flow.json` every 5s but the *file* only changes on Actions |

GH Pages **cannot** recompute every 5s (Actions minimum is minutes). True LIVE needs the local server (or another always-on worker) during **09:30–16:00 HKT**.

### Turn LIVE on

```bash
cd /workspace/trading/hsi_stock_flow
python3 fetch_stock_flow.py --force   # first run
python3 server.py                     # LIVE poller + HTTP
# open http://127.0.0.1:8768/
```

- Client: `setInterval` soft-fetch `/api/flow` every 5s (Play state preserved — no hard reload)  
- Server: background quote overlay (~5s); `POST /api/refresh` or periodic bars rebuild (~120s)  
- Status: `GET /api/status` shows actual `poll_interval_s` after backoff  

Optional: `python3 server.py --no-live` to serve without the poller; `python3 server.py --bars-s 60` for faster Chaikin rebuilds.


## Achievable refresh (observed)

Client soft-polls every **5s** (slim JSON ~43KB). Server quote overlay for all 95 names via Yahoo batches currently takes **~8–12s** end-to-end after hours; the LIVE loop therefore **backs off to ~10–15s** automatically when a poll exceeds 4s (tries 5s first). Full Chaikin 5m rebuild remains **~1–2 min** on demand / every 120s. If Yahoo rate-limits, expect 15–30s.

## Formulas

```
Chaikin MF = ((C-L)-(H-C))/(H-L) * Volume * (H+L+C)/3   # 0 if H==L
HSI pts    = weight_pct/100 * HSI_level * (last/open - 1)
```

Weights: Hang Seng Indexes **Sep 2026 After** (8% cap). Session filter: **09:30–12:00** and **13:00–16:00 HKT**.

## Files

- `hsi_universe.json` — 95 tickers, weights, Chinese sector labels  
- `fetch_stock_flow.py` / `live_poll.py` / `server.py` / `rebuild_index.py`  
- `out/stock_flow.json` · `.csv` · `summary.json`  
- Port **8768** (sector flow stays on **8767**)
