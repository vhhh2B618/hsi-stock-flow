#!/usr/bin/env python3
"""Fetch HSI stock flow and publish static Pages files. Exit 78 = skip."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = ROOT.parent
SKIP = 78
MIN_USABLE_BARS = 12
SKIP_MARKERS = ("No HK RTH bars", "No bars for session", "No OHLCV data downloaded")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    try:
        from fetch_stock_flow import compute_stock_flows, load_universe

        compute_stock_flows(load_universe(), force=True, interval="5m")
    except Exception as e:
        msg = str(e)
        if any(m in msg for m in SKIP_MARKERS):
            print(f"SKIP: {e}", flush=True)
            return SKIP
        print(f"ERROR fetch: {e}", flush=True)
        return 1

    out_json = ROOT / "out" / "stock_flow.json"
    if not out_json.exists():
        print("SKIP: no stock_flow.json", flush=True)
        return SKIP
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    n_ts = int(payload.get("n_timestamps") or 0)
    if n_ts < 1:
        return SKIP
    existing = PAGES / "stock_flow.json"
    if n_ts < MIN_USABLE_BARS and existing.exists():
        try:
            old = json.loads(existing.read_text(encoding="utf-8"))
            if int(old.get("n_timestamps") or 0) >= MIN_USABLE_BARS:
                print(f"SKIP: thin session bars={n_ts}", flush=True)
                return SKIP
        except Exception:
            pass

    # rebuild expects index.html + out/ relative to ROOT — copy template index from Pages if needed
    idx_src = PAGES / "index.html"
    if idx_src.exists():
        shutil.copy2(idx_src, ROOT / "index.html")
    r2 = subprocess.run([sys.executable, str(ROOT / "rebuild_index.py")], cwd=str(ROOT), check=False)
    if r2.returncode != 0:
        print("ERROR rebuild", flush=True)
        return 1

    for name in ("index.html", "stock_flow.json", "stock_flow_live.json", "stock_flow.csv"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, PAGES / name)
    print(
        f"OK published session={payload.get('session_date')} as_of={payload.get('as_of_hkt')} "
        f"bars={n_ts} tickers={payload.get('n_tickers_downloaded')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
