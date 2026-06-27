#!/usr/bin/env python3
"""Build a fuel-price-vs-crude-oil time series from the historical prices/ CSVs.

For each sampled day it computes the national average pump price per fuel
(taking each station's last price that day, then averaging across stations),
and joins it with daily Brent crude (downloaded from stooq.com).

Output:
  fuel_vs_oil.csv   columns: date,diesel,e5,e10,brent_usd
  fuel_vs_oil.html  a self-contained dual-axis chart (fuel €/L vs Brent $/bbl)

Reading all ~4,400 daily files is ~40 min; --stride samples every Nth file
(default 7 = weekly) for a fast, perfectly adequate trend.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import urllib.request
from datetime import date

from paths import DATA_DIR, PRICE_GLOB, WEB_DIR
# Brent crude, daily USD/bbl (1987->present). FRED/stooq are blocked or
# JS-walled from many environments; this GitHub-hosted dataset is reachable.
BRENT_URL = "https://raw.githubusercontent.com/datasets/oil-prices/main/data/brent-daily.csv"


def daily_fuel_avg(path: str) -> dict | None:
    """National avg of each fuel for one daily file (last price per station)."""
    last: dict[str, tuple[str, str, str]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) >= 5:
                last[row[1]] = (row[2], row[3], row[4])  # diesel, e5, e10
    sums = {"diesel": 0.0, "e5": 0.0, "e10": 0.0}
    cnt = {"diesel": 0, "e5": 0, "e10": 0}
    for d, e5, e10 in last.values():
        for k, v in (("diesel", d), ("e5", e5), ("e10", e10)):
            try:
                x = float(v)
            except ValueError:
                continue
            if x > 0:
                sums[k] += x
                cnt[k] += 1
    if not any(cnt.values()):
        return None
    return {k: round(sums[k] / cnt[k], 3) if cnt[k] else None for k in sums}


def load_brent() -> dict[str, float]:
    print("Downloading Brent crude (stooq)…")
    req = urllib.request.Request(BRENT_URL, headers={"User-Agent": "Mozilla/5.0"})
    text = urllib.request.urlopen(req, timeout=30).read().decode()
    out: dict[str, float] = {}
    for row in csv.DictReader(text.splitlines()):
        price = row.get("Price") or row.get("Close")  # dataset uses "Price"
        try:
            out[row["Date"]] = float(price)
        except (KeyError, ValueError, TypeError):
            continue
    print(f"Brent: {len(out)} daily closes")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stride", type=int, default=7,
                    help="sample every Nth daily file (default 7 = weekly)")
    ap.add_argument("--out-csv", default=os.path.join(DATA_DIR, "fuel_vs_oil.csv"))
    ap.add_argument("--out-html", default=os.path.join(WEB_DIR, "fuel_vs_oil.html"))
    args = ap.parse_args()

    files = sorted(glob.glob(PRICE_GLOB))[::args.stride]
    brent = load_brent()
    print(f"Processing {len(files)} daily files (stride {args.stride})…")

    rows = []
    for i, path in enumerate(files):
        d = os.path.basename(path)[:10]  # YYYY-MM-DD
        avg = daily_fuel_avg(path)
        if not avg:
            continue
        # nearest available Brent close on/just before this date
        b = brent.get(d)
        if b is None:
            y, m, dd = map(int, d.split("-"))
            for back in range(1, 6):
                from datetime import timedelta
                prev = (date(y, m, dd) - timedelta(days=back)).isoformat()
                if prev in brent:
                    b = brent[prev]
                    break
        rows.append({"date": d, **avg, "brent_usd": b})
        if i % 50 == 0:
            print(f"  {i}/{len(files)}  {d}")

    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "diesel", "e5", "e10", "brent_usd"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {args.out_csv} ({len(rows)} points)")

    write_html(args.out_html, rows)
    print(f"Wrote {args.out_html}")


def write_html(path: str, rows: list[dict]) -> None:
    data = json.dumps(rows, separators=(",", ":"))
    html = """<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>Kraftstoff vs. Rohöl (Brent)</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{font:14px system-ui;margin:24px}#c{max-width:1100px}nav a{margin-right:14px}</style></head>
<body><nav><a href="index.html">← Karte</a><a href="border_stats.html">Grenznähe-Statistik</a><a href="factory_stats.html">Autowerke</a></nav>
<h1>Kraftstoffpreise vs. Rohöl (Brent)</h1>
<p>Bundesweiter Durchschnitt €/L (linke Achse) gegen Brent USD/Barrel (rechte Achse).</p>
<div id="c"><canvas id="chart"></canvas></div>
<script>
const rows=__DATA__;
const labels=rows.map(r=>r.date);
const ser=(k)=>rows.map(r=>r[k]);
new Chart(document.getElementById('chart'),{type:'line',
 data:{labels,datasets:[
  {label:'Diesel €/L',data:ser('diesel'),borderColor:'#1a9850',pointRadius:0,yAxisID:'y'},
  {label:'E5 €/L',data:ser('e5'),borderColor:'#d73027',pointRadius:0,yAxisID:'y'},
  {label:'E10 €/L',data:ser('e10'),borderColor:'#f46d43',pointRadius:0,yAxisID:'y'},
  {label:'Brent $/bbl',data:ser('brent_usd'),borderColor:'#2166ac',borderDash:[5,4],pointRadius:0,yAxisID:'y1'}
 ]},
 options:{interaction:{mode:'index',intersect:false},
  scales:{y:{position:'left',title:{display:true,text:'€/L'}},
          y1:{position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'Brent $/bbl'}}}}});
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html.replace("__DATA__", data))


if __name__ == "__main__":
    main()
