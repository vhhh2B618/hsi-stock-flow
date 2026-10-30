#!/usr/bin/env python3
"""Fast LIVE quote poll for HSI pts + last price overlay on stock_flow.json."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
UNIVERSE = ROOT / "hsi_universe.json"
HKT = ZoneInfo("Asia/Hong_Kong")


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _batch_last_prices(symbols: list[str], batch: int = 30) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i : i + batch]
        try:
            df = yf.download(
                tickers=" ".join(chunk),
                period="1d",
                interval="1m",
                group_by="ticker",
                auto_adjust=False,
                threads=False,
                progress=False,
                timeout=45,
            )
        except Exception as e:
            print(f"live batch error: {e}", flush=True)
            time.sleep(1.5)
            continue
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    if t not in df.columns.get_level_values(0):
                        continue
                    sub = df[t]
                else:
                    sub = df
                if "Close" not in getattr(sub, "columns", []):
                    continue
                series = sub["Close"].dropna()
                if series.empty:
                    continue
                out[t] = float(series.iloc[-1])
            except Exception:
                continue
        time.sleep(0.15)
    return out


def poll_once(payload: dict | None = None) -> dict:
    path = OUT / "stock_flow.json"
    if payload is None:
        if not path.exists():
            raise RuntimeError("stock_flow.json missing — run fetch_stock_flow.py first")
        payload = json.loads(path.read_text(encoding="utf-8"))

    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    meta = universe["tickers"]
    symbols = sorted(meta.keys())
    hsi_level = float(payload.get("hsi_level") or universe.get("hsi_level_ref") or 23294.42)

    latest_stocks = {r["ticker"]: dict(r) for r in (payload.get("latest_stocks") or [])}
    opens = {t: float(r["open"]) for t, r in latest_stocks.items() if r.get("open")}

    prices = _batch_last_prices(symbols)
    updated = 0
    for t, last in prices.items():
        w = float(meta.get(t, {}).get("weight_pct") or 0.0)
        open_px = opens.get(t)
        if not open_px or open_px <= 0:
            continue
        pct = (last / open_px - 1.0) * 100.0
        pts = w / 100.0 * hsi_level * (last / open_px - 1.0)
        if t in latest_stocks:
            latest_stocks[t]["last"] = round(last, 4)
            latest_stocks[t]["pct_chg"] = round(pct, 4)
            latest_stocks[t]["hsi_pts"] = round(pts, 4)
            updated += 1
        stocks = (payload.get("latest") or {}).get("stocks") or {}
        if t in stocks:
            stocks[t]["last"] = round(last, 4)
            stocks[t]["pct_chg"] = round(pct, 4)
            stocks[t]["hsi_pts"] = round(pts, 4)

    rows = sorted(latest_stocks.values(), key=lambda x: -abs(x.get("hsi_pts") or 0))
    payload["latest_stocks"] = rows
    payload["generated_at_hkt"] = now_hkt()
    payload["live_poll"] = {
        "updated_tickers": updated,
        "polled_at_hkt": now_hkt(),
        "n_quotes": len(prices),
    }
    stock_list = list(latest_stocks.values())
    payload["top_pts_up"] = [
        {"ticker": x["ticker"], "name": x["name"], "hsi_pts": x["hsi_pts"], "pct_chg": x["pct_chg"]}
        for x in sorted(stock_list, key=lambda x: -x["hsi_pts"])[:5]
    ]
    payload["top_pts_down"] = [
        {"ticker": x["ticker"], "name": x["name"], "hsi_pts": x["hsi_pts"], "pct_chg": x["pct_chg"]}
        for x in sorted(stock_list, key=lambda x: x["hsi_pts"])[:5]
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    slim = {
        "ok": True,
        "generated_at_hkt": payload.get("generated_at_hkt"),
        "session_date": payload.get("session_date"),
        "session_label": payload.get("session_label"),
        "as_of_hkt": payload.get("as_of_hkt"),
        "as_of_clock": payload.get("as_of_clock"),
        "hsi_level": payload.get("hsi_level"),
        "n_tickers_downloaded": payload.get("n_tickers_downloaded"),
        "live_poll": payload.get("live_poll"),
        "top_flow_in": payload.get("top_flow_in"),
        "top_flow_out": payload.get("top_flow_out"),
        "top_pts_up": payload.get("top_pts_up"),
        "top_pts_down": payload.get("top_pts_down"),
        "latest_stocks": payload.get("latest_stocks"),
        "latest": payload.get("latest"),
        "sectors": payload.get("sectors"),
        "slim": True,
    }
    (OUT / "stock_flow_live.json").write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = poll_once()
    print(json.dumps(p.get("live_poll"), ensure_ascii=False))
