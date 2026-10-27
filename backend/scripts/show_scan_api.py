"""Fetch and print the scan exactly as the dashboard receives it."""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with urllib.request.urlopen("http://127.0.0.1:8000/api/scan", timeout=120) as response:
    payload = json.load(response)

print("ready      :", payload["ready"])
print("stats      :", json.dumps(payload["stats"]))
print("candidates :", len(payload["candidates"]))
print()
header = f"{'SYMBOL':<15}{'REGIME':<10}{'ATR':>6}{'STOP':>7}{'FEES/STOP':>11}{'NEED':>6}"
print(header)
for c in payload["candidates"][:10]:
    print(
        f"{c['symbol']:<15}{c['regime']:<10}"
        f"{c['atr_pct']:>5.2f}%{c['stop_distance_pct']:>6.2f}%"
        f"{c['cost_to_stop'] * 100:>10.0f}%{c['breakeven_win_rate'] * 100:>5.0f}%"
    )
