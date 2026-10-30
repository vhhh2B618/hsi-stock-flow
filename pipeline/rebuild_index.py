#!/usr/bin/env python3
"""Embed slim snapshot into index.html; full series stays in stock_flow.json."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
full = json.loads((OUT / "stock_flow.json").read_text(encoding="utf-8"))
slim_path = OUT / "stock_flow_live.json"
if slim_path.exists():
    embed = json.loads(slim_path.read_text(encoding="utf-8"))
else:
    embed = {k: full.get(k) for k in (
        "ok","generated_at_hkt","session_date","session_label","as_of_hkt","as_of_clock",
        "hsi_level","n_tickers_downloaded","top_flow_in","top_flow_out","top_pts_up","top_pts_down",
        "latest_stocks","latest","sectors","session_hours","unit_flow","unit_pts",
        "formula_chaikin","formula_hsi_pts","disclaimer_zh","disclaimer_en","live_note",
    )}
    embed["slim"] = True
    embed["series"] = []  # Play loads after softFetch of full json
embed.setdefault("series", [])
# Always ship full + slim + csv next to index for Pages
for name in ("stock_flow.json", "stock_flow_live.json", "stock_flow.csv"):
    src = OUT / name
    if src.exists():
        shutil.copy2(src, ROOT / name)

html_path = ROOT / "index.html"
html = html_path.read_text(encoding="utf-8")
payload = json.dumps(embed, ensure_ascii=False).replace("</", "<\\/")
new_html, n = re.subn(
    r'(<script id="data" type="application/json">)(.*?)(</script>)',
    lambda m: m.group(1) + payload + m.group(3),
    html,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit("Could not find embed script#data in index.html")
html_path.write_text(new_html, encoding="utf-8")
print(f"updated {html_path} ({html_path.stat().st_size} bytes) embed_slim=1")
