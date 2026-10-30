#!/usr/bin/env python3
"""HSI stock-level Chaikin money flow + estimated HSI pts contribution.

Same Chaikin methodology as hk_sector_flow/fetch_flow.py, but emits per-ticker
series and HSI pts ≈ weight% × HSI × (%chg from open) / 100.
Yahoo delayed bars — research only.
"""
from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "out"
UNIVERSE_PATH = ROOT / "hsi_universe.json"
HKT = ZoneInfo("Asia/Hong_Kong")
AM_START, AM_END = dtime(9, 30), dtime(12, 0)
PM_START, PM_END = dtime(13, 0), dtime(16, 0)
BATCH = 25
MIN_USABLE_BARS = 12
SECTOR_ORDER = [
    "Financials",
    "Properties & Construction",
    "Information Technology",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Materials",
    "Industrials",
    "Utilities",
    "Telecommunications",
    "Healthcare",
    "Conglomerates",
]


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def load_universe() -> dict:
    return json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))


def chaikin_flow(h, l, c, v) -> float:
    rng = h - l
    if rng == 0 or np.isnan(rng) or np.isnan(h) or np.isnan(l) or np.isnan(c) or np.isnan(v):
        return 0.0
    mult = ((c - l) - (h - c)) / rng
    avg = (h + l + c) / 3.0
    return float(mult * v * avg)


def is_rth(t: dtime) -> bool:
    return (AM_START <= t <= AM_END) or (PM_START <= t <= PM_END)


def pick_last_rth_session(index: pd.DatetimeIndex):
    if index.tz is None:
        idx = index.tz_localize("UTC").tz_convert(HKT)
    else:
        idx = index.tz_convert(HKT)
    rth = idx[[is_rth(ts.time()) for ts in idx]]
    if len(rth) == 0:
        raise RuntimeError("No HK RTH bars in downloaded data")
    days = sorted(set(rth.normalize()))

    def bars_for(day):
        return rth[rth.normalize() == day]

    newest = days[-1]
    newest_bars = bars_for(newest)
    if len(newest_bars) >= MIN_USABLE_BARS:
        return newest, newest_bars
    for day in reversed(days[:-1]):
        day_bars = bars_for(day)
        if len(day_bars) >= MIN_USABLE_BARS:
            print(
                f"Session picker: newest {newest.date()} has {len(newest_bars)} bars "
                f"(<{MIN_USABLE_BARS}); using {day.date()} ({len(day_bars)} bars)",
                flush=True,
            )
            return day, day_bars
    return newest, newest_bars


def download_batch(tickers: list[str], period="5d", interval="5m") -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    try:
        df = yf.download(
            tickers=" ".join(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=False,
            threads=False,
            progress=False,
            timeout=60,
        )
    except Exception as e:
        print(f"  batch error: {e}")
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def extract_ohlcv(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            level0 = df.columns.get_level_values(0)
            if ticker in level0:
                sub = df[ticker].copy()
            elif ticker in df.columns.get_level_values(-1):
                sub = df.xs(ticker, axis=1, level=-1).copy()
            else:
                return None
        else:
            sub = df.copy()
        need = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in sub.columns for c in need):
            return None
        sub = sub[need].dropna(how="all")
        return None if sub.empty else sub
    except Exception:
        return None


def fetch_hsi_level() -> float | None:
    try:
        h = yf.download("^HSI", period="5d", interval="1d", progress=False, auto_adjust=False, threads=False)
        if h is None or h.empty:
            return None
        if isinstance(h.columns, pd.MultiIndex):
            # yfinance sometimes returns ("Close", ^HSI)
            closes = h["Close"]
            if isinstance(closes, pd.DataFrame):
                closes = closes.iloc[:, 0]
        else:
            closes = h["Close"] if "Close" in h.columns else h.iloc[:, 0]
        val = closes.dropna().iloc[-1]
        return float(val)
    except Exception as e:
        print(f"HSI level fetch failed: {e}")
        return None


def compute_stock_flows(universe: dict, force: bool = False, interval: str = "5m") -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    tickers_meta = universe["tickers"]
    symbols = sorted(tickers_meta.keys())
    cache_meta_path = RAW / "cache_meta.json"
    raw_parquet = RAW / "ohlcv.parquet"
    failed: list[str] = []

    reuse = False
    if not force and raw_parquet.exists() and cache_meta_path.exists():
        try:
            meta = json.loads(cache_meta_path.read_text(encoding="utf-8"))
            age_h = (time.time() - meta.get("fetched_unix", 0)) / 3600
            cached_session = meta.get("session_date")
            today_hk = datetime.now(HKT).date().isoformat()
            if age_h < 0.25 or (age_h < 12 and cached_session == today_hk):
                # live mode: reuse only if <15 min old unless same session <12h
                if age_h < 0.25 or (meta.get("interval") == interval and age_h < 6):
                    reuse = True
                    print(f"Reusing cached OHLCV (session={cached_session}, age={age_h:.2f}h)")
        except Exception:
            reuse = False

    if reuse:
        wide = pd.read_parquet(raw_parquet)
    else:
        print(f"Downloading {len(symbols)} tickers interval={interval} batches of {BATCH}…")
        frames = []
        failed = []
        for i in range(0, len(symbols), BATCH):
            batch = symbols[i : i + BATCH]
            print(f"  batch {i // BATCH + 1}/{(len(symbols) + BATCH - 1) // BATCH}: {batch[0]}…{batch[-1]}")
            df = download_batch(batch, period="5d", interval=interval)
            if df is None or df.empty:
                failed.extend(batch)
                time.sleep(1)
                continue
            for t in batch:
                sub = extract_ohlcv(df, t)
                if sub is None:
                    try:
                        one = yf.download(
                            t, period="5d", interval=interval, auto_adjust=False,
                            progress=False, timeout=45, threads=False,
                        )
                        sub = extract_ohlcv(one, t)
                    except Exception:
                        sub = None
                if sub is None or sub.empty:
                    failed.append(t)
                    continue
                sub = sub.copy()
                sub["Ticker"] = t
                frames.append(sub.reset_index().rename(columns={"Datetime": "Datetime", "index": "Datetime"}))
            time.sleep(0.35)

        if not frames:
            raise RuntimeError("No OHLCV data downloaded")

        long_df = pd.concat(frames, ignore_index=True)
        dtcol = None
        for c in long_df.columns:
            if str(c).lower() in ("datetime", "date", "index"):
                dtcol = c
                break
        if dtcol is None:
            dtcol = long_df.columns[0]
        long_df = long_df.rename(columns={dtcol: "Datetime"})
        long_df["Datetime"] = pd.to_datetime(long_df["Datetime"], utc=True)
        wide = long_df
        wide.to_parquet(raw_parquet, index=False)
        print(
            f"Saved raw parquet: rows={len(wide)} tickers_ok={wide['Ticker'].nunique()} "
            f"failed={len(failed)} {failed[:12]}"
        )

    wide["Datetime"] = pd.to_datetime(wide["Datetime"], utc=True)
    idx_all = pd.DatetimeIndex(wide["Datetime"].unique()).tz_convert(HKT)
    session_day, _ = pick_last_rth_session(idx_all)
    session_date = session_day.date().isoformat() if hasattr(session_day, "date") else str(session_day)[:10]

    wide_hkt = wide.copy()
    wide_hkt["hkt"] = wide_hkt["Datetime"].dt.tz_convert(HKT)
    if isinstance(session_day, pd.Timestamp):
        sd = session_day.tz_convert(HKT).normalize()
    else:
        sd = pd.Timestamp(session_day, tz=HKT).normalize()
    day_mask = wide_hkt["hkt"].dt.normalize() == sd
    rth_mask = wide_hkt["hkt"].dt.time.map(is_rth)
    sess = wide_hkt[day_mask & rth_mask].copy()
    if sess.empty:
        raise RuntimeError(f"No bars for session {session_date}")

    cache_meta_path.write_text(
        json.dumps(
            {
                "session_date": session_date,
                "fetched_unix": time.time(),
                "fetched_at_hkt": now_hkt(),
                "n_rows": len(wide),
                "n_tickers": int(wide["Ticker"].nunique()),
                "failed": failed,
                "interval": interval,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    hsi_level = fetch_hsi_level()
    if hsi_level is None:
        hsi_level = float(universe.get("hsi_level_ref") or 23294.42)
        hsi_src = "ref"
    else:
        hsi_src = "yahoo_^HSI"

    # Per-ticker bar flows + open/last for HSI pts
    ticker_rows = []
    for ticker, g in sess.groupby("Ticker"):
        g = g.sort_values("hkt")
        meta = tickers_meta.get(ticker) or {}
        sector = meta.get("sector", "Other")
        sector_zh = meta.get("sector_zh", "其他")
        name = meta.get("name", ticker)
        w = float(meta.get("weight_pct") or 0.0)
        open_px = float(g.iloc[0]["Open"])
        last_px = float(g.iloc[-1]["Close"])
        pct = ((last_px / open_px) - 1.0) * 100.0 if open_px else 0.0
        hsi_pts = w / 100.0 * hsi_level * (pct / 100.0)
        cum = 0.0
        for _, row in g.iterrows():
            f = chaikin_flow(row["High"], row["Low"], row["Close"], row["Volume"])
            cum += f
            ticker_rows.append(
                {
                    "Datetime": row["hkt"],
                    "Ticker": ticker,
                    "name": name,
                    "sector": sector,
                    "sector_zh": sector_zh,
                    "weight_pct": w,
                    "flow": f,
                    "cum_flow": cum,
                    "open": open_px,
                    "last": float(row["Close"]),
                    "pct_chg": ((float(row["Close"]) / open_px) - 1.0) * 100.0 if open_px else 0.0,
                    "hsi_pts": w / 100.0 * hsi_level * (((float(row["Close"]) / open_px) - 1.0) if open_px else 0.0),
                }
            )
        # store final snapshot fields via last loop values
        _ = (pct, hsi_pts, last_px)

    flow_df = pd.DataFrame(ticker_rows)
    timestamps = sorted(flow_df["Datetime"].unique())

    # Sector aggregate (for sector view / drill)
    sec_agg = (
        flow_df.groupby(["Datetime", "sector", "sector_zh"], as_index=False)["flow"]
        .sum()
        .sort_values(["Datetime", "sector"])
    )
    sec_agg["cum_flow"] = sec_agg.groupby("sector")["flow"].cumsum()
    sec_agg["cum_flow_m"] = sec_agg["cum_flow"] / 1e6
    sec_agg["flow_m"] = sec_agg["flow"] / 1e6

    zh_map = dict(universe.get("sectors_zh", {}))
    zh_map.setdefault("Other", "其他")
    sectors_present = [s for s in SECTOR_ORDER if s in set(sec_agg["sector"])] + sorted(
        set(sec_agg["sector"]) - set(SECTOR_ORDER)
    )

    series = []
    for ts in timestamps:
        snap_t = flow_df[flow_df["Datetime"] == ts]
        snap_s = sec_agg[sec_agg["Datetime"] == ts]
        hkt_str = pd.Timestamp(ts).tz_convert(HKT).strftime("%Y-%m-%d %H:%M")
        clock = pd.Timestamp(ts).tz_convert(HKT).strftime("%H:%M")
        stocks = {}
        for _, r in snap_t.iterrows():
            stocks[r["Ticker"]] = {
                "name": r["name"],
                "sector": r["sector"],
                "sector_zh": r["sector_zh"],
                "weight_pct": round(float(r["weight_pct"]), 4),
                "cum_m": round(float(r["cum_flow"]) / 1e6, 3),
                "bar_m": round(float(r["flow"]) / 1e6, 3),
                "open": round(float(r["open"]), 4),
                "last": round(float(r["last"]), 4),
                "pct_chg": round(float(r["pct_chg"]), 4),
                "hsi_pts": round(float(r["hsi_pts"]), 4),
            }
        secs = {}
        for _, r in snap_s.iterrows():
            secs[r["sector"]] = {
                "zh": r["sector_zh"],
                "cum_m": round(float(r["cum_flow_m"]), 3),
                "bar_m": round(float(r["flow_m"]), 3),
            }
        series.append({"timestamp_hkt": hkt_str, "clock": clock, "stocks": stocks, "sectors": secs})

    last = series[-1] if series else {"stocks": {}, "sectors": {}}
    stock_list = [
        {"ticker": t, **d} for t, d in last.get("stocks", {}).items()
    ]
    top_flow_in = sorted([x for x in stock_list if x["cum_m"] > 0], key=lambda x: -x["cum_m"])[:5]
    top_flow_out = sorted([x for x in stock_list if x["cum_m"] < 0], key=lambda x: x["cum_m"])[:5]
    top_pts = sorted(stock_list, key=lambda x: -x["hsi_pts"])[:5]
    bot_pts = sorted(stock_list, key=lambda x: x["hsi_pts"])[:5]

    session_end = pd.Timestamp(timestamps[-1]).tz_convert(HKT) if timestamps else None
    session_start = pd.Timestamp(timestamps[0]).tz_convert(HKT) if timestamps else None
    weekday_zh = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    wd = weekday_zh[session_start.weekday()] if session_start is not None else ""

    # Latest flat table for CSV / HSI pts mode
    latest_rows = []
    for t, d in sorted(last.get("stocks", {}).items(), key=lambda kv: -abs(kv[1]["hsi_pts"])):
        latest_rows.append(
            {
                "ticker": t,
                "name": d["name"],
                "sector": d["sector"],
                "sector_zh": d["sector_zh"],
                "weight_pct": d["weight_pct"],
                "open": d["open"],
                "last": d["last"],
                "pct_chg": d["pct_chg"],
                "hsi_pts": d["hsi_pts"],
                "cum_m": d["cum_m"],
            }
        )
    csv_df = pd.DataFrame(latest_rows)
    csv_path = OUT / "stock_flow.csv"
    csv_df.to_csv(csv_path, index=False)

    payload = {
        "ok": True,
        "mode": "stock",
        "generated_at_hkt": now_hkt(),
        "session_date": session_date,
        "session_label": f"{session_date} ({wd})" if wd else session_date,
        "session_hours": "09:30–12:00 · 13:00–16:00 HKT",
        "as_of_hkt": session_end.strftime("%Y-%m-%d %H:%M HKT") if session_end is not None else None,
        "as_of_clock": session_end.strftime("%H:%M") if session_end is not None else None,
        "n_timestamps": len(timestamps),
        "n_tickers_universe": len(symbols),
        "n_tickers_downloaded": int(sess["Ticker"].nunique()),
        "n_tickers_failed": len(failed),
        "failed_tickers": failed[:30],
        "hsi_level": round(hsi_level, 2),
        "hsi_level_source": hsi_src,
        "interval": interval,
        "sectors": [{"en": s, "zh": zh_map.get(s, s)} for s in sectors_present],
        "unit_flow": "百萬港元 (HKD millions, cumulative Chaikin-style)",
        "unit_pts": "estimated HSI index points from open",
        "formula_chaikin": "((C-L)-(H-C))/(H-L) * Volume * (H+L+C)/3 ; 0 if H==L",
        "formula_hsi_pts": "weight_pct/100 * HSI_level * (last/open - 1)",
        "data_source": "Yahoo Finance via yfinance (delayed) · HSI Sep 2026 After weights",
        "disclaimer_zh": "Yahoo 免費數據有延遲，非即時。Chaikin 與 HSI 點數貢獻皆為估算。僅供研究參考，不構成投資建議。",
        "disclaimer_en": "Yahoo free data is delayed. Chaikin and HSI pts are estimates — not tick order-flow. Research only — not financial advice.",
        "live_note": "GH Pages = cron snapshot. LIVE ~5–15s needs local server.py (polls Yahoo; may back off on rate limits).",
        "top_flow_in": [{"ticker": x["ticker"], "name": x["name"], "cum_m": x["cum_m"]} for x in top_flow_in],
        "top_flow_out": [{"ticker": x["ticker"], "name": x["name"], "cum_m": x["cum_m"]} for x in top_flow_out],
        "top_pts_up": [{"ticker": x["ticker"], "name": x["name"], "hsi_pts": x["hsi_pts"], "pct_chg": x["pct_chg"]} for x in top_pts],
        "top_pts_down": [{"ticker": x["ticker"], "name": x["name"], "hsi_pts": x["hsi_pts"], "pct_chg": x["pct_chg"]} for x in bot_pts],
        "latest_stocks": latest_rows,
        "series": series,
        "latest": last,
    }

    json_path = OUT / "stock_flow.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    slim = {k: payload.get(k) for k in (
        "ok","generated_at_hkt","session_date","session_label","as_of_hkt","as_of_clock",
        "hsi_level","n_tickers_downloaded","top_flow_in","top_flow_out","top_pts_up","top_pts_down",
        "latest_stocks","latest","sectors",
    )}
    slim["slim"] = True
    (OUT / "stock_flow_live.json").write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    # slim summary
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at_hkt": payload["generated_at_hkt"],
                "session_date": session_date,
                "as_of_hkt": payload["as_of_hkt"],
                "n_timestamps": payload["n_timestamps"],
                "n_tickers_downloaded": payload["n_tickers_downloaded"],
                "n_tickers_failed": len(failed),
                "failed_tickers": failed[:20],
                "hsi_level": payload["hsi_level"],
                "top_flow_in": payload["top_flow_in"],
                "top_flow_out": payload["top_flow_out"],
                "top_pts_up": payload["top_pts_up"],
                "top_pts_down": payload["top_pts_down"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"OK session={session_date} bars={len(timestamps)} tickers={payload['n_tickers_downloaded']} "
        f"failed={len(failed)} HSI={hsi_level:.0f} → {json_path}",
        flush=True,
    )
    if top_flow_in:
        print(f"  top flow in: {top_flow_in[0]['ticker']} {top_flow_in[0]['cum_m']:+.1f}M", flush=True)
    if top_pts:
        print(f"  top HSI pts: {top_pts[0]['ticker']} {top_pts[0]['hsi_pts']:+.1f} pts", flush=True)
    return payload


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--interval", default="5m", choices=["1m", "5m", "15m"])
    args = ap.parse_args()
    compute_stock_flows(load_universe(), force=args.force, interval=args.interval)


if __name__ == "__main__":
    main()
