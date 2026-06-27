#!/usr/bin/env python3
"""Build border_stats.html: average price near the border vs. inland.

Reads snapshot_borders.json (produced by border_zones.py) and writes a
standalone page with:
  - a headline border-zone vs. inland average per fuel,
  - the same broken down by distance band (0-10, 10-25, 25-50, >50 km).
"""

from __future__ import annotations

import json
import os
import statistics

from paths import DATA_DIR, WEB_DIR

SRC = os.path.join(DATA_DIR, "snapshot_borders.json")
OUT = os.path.join(WEB_DIR, "border_stats.html")
FUELS = ("e5", "e10", "diesel")
BANDS = [(0, 10), (10, 25), (25, 50), (50, 10_000)]
BAND_LABELS = ["0–10 km", "10–25 km", "25–50 km", "> 50 km"]


def avg(stations, fuel):
    vals = [s[fuel] for s in stations if s.get(fuel) is not None]
    return round(statistics.mean(vals), 3) if vals else None


def main() -> None:
    snap = json.load(open(SRC, encoding="utf-8"))
    st = snap["stations"]
    zone = snap.get("border_zone_km", 25)

    border = [s for s in st if s["border_km"] <= zone]
    inland = [s for s in st if s["border_km"] > zone]

    headline = {f: {"border": avg(border, f), "inland": avg(inland, f),
                    "diff_ct": round((avg(border, f) - avg(inland, f)) * 100, 2)}
                for f in FUELS}

    bands = []
    for (lo, hi), label in zip(BANDS, BAND_LABELS):
        grp = [s for s in st if lo <= s["border_km"] < hi]
        bands.append({"label": label, "n": len(grp),
                      **{f: avg(grp, f) for f in FUELS}})

    payload = {
        "source_date": snap.get("source_date"),
        "zone": zone,
        "n_border": len(border), "n_inland": len(inland),
        "headline": headline, "bands": bands,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE.replace("__DATA__", json.dumps(payload)))
    print(f"Wrote {OUT}")
    for f in FUELS:
        h = headline[f]
        print(f"  {f:<7} border {h['border']}  inland {h['inland']}  diff {h['diff_ct']:+} ct")


TEMPLATE = """<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grenznähe vs. Inland</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 body{font:15px/1.5 system-ui,sans-serif;margin:24px;max-width:900px}
 h1{font-size:22px} table{border-collapse:collapse;margin:14px 0;width:100%}
 th,td{border:1px solid #ddd;padding:6px 10px;text-align:right} th{background:#f5f5f5}
 td:first-child,th:first-child{text-align:left}
 .pos{color:#d73027;font-weight:600} .canvas{max-width:760px;margin:18px 0}
 .muted{color:#777;font-size:13px} nav a{margin-right:14px}
</style></head><body>
<nav><a href="index.html">← Karte</a><a href="factory_stats.html">Autowerke</a><a href="fuel_vs_oil.html">Rohöl-Vergleich</a></nav>
<h1>Preise an der Grenze vs. Inland</h1>
<p class="muted" id="meta"></p>
<table id="head"></table>
<div class="canvas"><canvas id="bar"></canvas></div>
<h2>Nach Entfernung zur Grenze</h2>
<table id="bands"></table>
<div class="canvas"><canvas id="bands_chart"></canvas></div>
<p class="muted">Nur deutsche Stationen. „Differenz" = Grenzzone minus Inland in Cent/Liter.
Vergleich mit tatsächlichen Preisen jenseits der Grenze ist hier nicht enthalten.</p>
<script>
const D=__DATA__, FUELS=['e5','e10','diesel'], NAMES={e5:'Super E5',e10:'Super E10',diesel:'Diesel'};
document.getElementById('meta').textContent =
  `Stand ${D.source_date} · Grenzzone ≤ ${D.zone} km · ${D.n_border.toLocaleString('de')} Grenz- / ${D.n_inland.toLocaleString('de')} Inland-Stationen`;

let h=`<tr><th>Kraftstoff</th><th>Ø Grenzzone</th><th>Ø Inland</th><th>Differenz</th></tr>`;
for(const f of FUELS){const r=D.headline[f];
  h+=`<tr><td>${NAMES[f]}</td><td>${r.border.toFixed(3)} €</td><td>${r.inland.toFixed(3)} €</td>`+
     `<td class="pos">${r.diff_ct>=0?'+':''}${r.diff_ct.toFixed(2)} ct</td></tr>`;}
document.getElementById('head').innerHTML=h;

new Chart(document.getElementById('bar'),{type:'bar',
 data:{labels:FUELS.map(f=>NAMES[f]),datasets:[
  {label:'Grenzzone',data:FUELS.map(f=>D.headline[f].border),backgroundColor:'#d73027'},
  {label:'Inland',data:FUELS.map(f=>D.headline[f].inland),backgroundColor:'#4575b4'}]},
 options:{scales:{y:{title:{display:true,text:'€/L'},beginAtZero:false}}}});

let b=`<tr><th>Entfernung</th><th>Stationen</th><th>Ø E5</th><th>Ø E10</th><th>Ø Diesel</th></tr>`;
for(const r of D.bands){b+=`<tr><td>${r.label}</td><td>${r.n.toLocaleString('de')}</td>`+
  `<td>${r.e5?.toFixed(3)??'—'}</td><td>${r.e10?.toFixed(3)??'—'}</td><td>${r.diesel?.toFixed(3)??'—'}</td></tr>`;}
document.getElementById('bands').innerHTML=b;

new Chart(document.getElementById('bands_chart'),{type:'line',
 data:{labels:D.bands.map(r=>r.label),datasets:FUELS.map((f,i)=>(
  {label:NAMES[f],data:D.bands.map(r=>r[f]),borderColor:['#d73027','#f46d43','#1a9850'][i],
   pointRadius:3,tension:.2}))},
 options:{scales:{y:{title:{display:true,text:'€/L'}}}}});
</script></body></html>"""


if __name__ == "__main__":
    main()
