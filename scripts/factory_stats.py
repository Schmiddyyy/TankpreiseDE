#!/usr/bin/env python3
"""Build factory_stats.html: average price near car factories vs. elsewhere.

Reads snapshot_factories.json (from factory_zones.py) and writes a standalone
page with:
  - the headline factory-zone vs. rest average per fuel,
  - the same broken down by distance band (0-5, 5-10, 10-25, >25 km),
  - the list of plants used.
"""

from __future__ import annotations

import json
import os
import statistics

from paths import DATA_DIR, WEB_DIR

SRC = os.path.join(DATA_DIR, "snapshot_factories.json")
OUT = os.path.join(WEB_DIR, "factory_stats.html")
FUELS = ("e5", "e10", "diesel")
BANDS = [(0, 5), (5, 10), (10, 25), (25, 100_000)]
BAND_LABELS = ["0–5 km", "5–10 km", "10–25 km", "> 25 km"]


def avg(stations, fuel):
    vals = [s[fuel] for s in stations if s.get(fuel) is not None]
    return round(statistics.mean(vals), 3) if vals else None


def main() -> None:
    snap = json.load(open(SRC, encoding="utf-8"))
    st = snap["stations"]
    zone = snap.get("factory_zone_km", 10)

    near = [s for s in st if s["factory_km"] <= zone]
    rest = [s for s in st if s["factory_km"] > zone]

    headline = {f: {"near": avg(near, f), "rest": avg(rest, f),
                    "diff_ct": round(((avg(near, f) or 0) - (avg(rest, f) or 0)) * 100, 2)}
                for f in FUELS}

    bands = []
    for (lo, hi), label in zip(BANDS, BAND_LABELS):
        grp = [s for s in st if lo <= s["factory_km"] < hi]
        bands.append({"label": label, "n": len(grp), **{f: avg(grp, f) for f in FUELS}})

    payload = {
        "source_date": snap.get("source_date"), "zone": zone,
        "n_near": len(near), "n_rest": len(rest),
        "headline": headline, "bands": bands,
        "factories": snap.get("factories", []),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)))
    print(f"Wrote {OUT}")
    for f in FUELS:
        h = headline[f]
        print(f"  {f:<7} near {h['near']}  rest {h['rest']}  diff {h['diff_ct']:+} ct")


TEMPLATE = """<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autowerke vs. Rest</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 body{font:15px/1.5 system-ui,sans-serif;margin:24px;max-width:900px}
 h1{font-size:22px} table{border-collapse:collapse;margin:14px 0;width:100%}
 th,td{border:1px solid #ddd;padding:6px 10px;text-align:right} th{background:#f5f5f5}
 td:first-child,th:first-child{text-align:left}
 .pos{color:#d73027;font-weight:600} .neg{color:#1a9850;font-weight:600}
 .canvas{max-width:760px;margin:18px 0}
 .muted{color:#777;font-size:13px} nav a{margin-right:14px}
 details{margin-top:10px} summary{cursor:pointer}
</style></head><body>
<nav><a href="index.html">← Karte</a><a href="border_stats.html">Grenznähe</a><a href="fuel_vs_oil.html">Rohöl-Vergleich</a></nav>
<h1>Preise nahe Autowerken vs. übriges Land</h1>
<p class="muted" id="meta"></p>
<table id="head"></table>
<div class="canvas"><canvas id="bar"></canvas></div>
<h2>Nach Entfernung zum nächsten Werk</h2>
<table id="bands"></table>
<div class="canvas"><canvas id="bands_chart"></canvas></div>
<details><summary>Berücksichtigte Werke</summary><div id="plants" class="muted"></div></details>
<p class="muted">„Differenz" = Werkszone minus übriges Land in Cent/Liter. Werks-Koordinaten sind Näherungswerte.</p>
<script>
const D=__DATA__, FUELS=['e5','e10','diesel'], NAMES={e5:'Super E5',e10:'Super E10',diesel:'Diesel'};
document.getElementById('meta').textContent =
  `Stand ${D.source_date} · Werkszone ≤ ${D.zone} km · ${D.n_near.toLocaleString('de')} Stationen nahe Werken / ${D.n_rest.toLocaleString('de')} übrige · ${D.factories.length} Werke`;

let h=`<tr><th>Kraftstoff</th><th>Ø nahe Werk</th><th>Ø übriges Land</th><th>Differenz</th></tr>`;
for(const f of FUELS){const r=D.headline[f];const cls=r.diff_ct>=0?'pos':'neg';
  h+=`<tr><td>${NAMES[f]}</td><td>${r.near?.toFixed(3)??'—'} €</td><td>${r.rest?.toFixed(3)??'—'} €</td>`+
     `<td class="${cls}">${r.diff_ct>=0?'+':''}${r.diff_ct.toFixed(2)} ct</td></tr>`;}
document.getElementById('head').innerHTML=h;

new Chart(document.getElementById('bar'),{type:'bar',
 data:{labels:FUELS.map(f=>NAMES[f]),datasets:[
  {label:'≤ '+D.zone+' km Werk',data:FUELS.map(f=>D.headline[f].near),backgroundColor:'#7b3294'},
  {label:'übriges Land',data:FUELS.map(f=>D.headline[f].rest),backgroundColor:'#a6a6a6'}]},
 options:{scales:{y:{title:{display:true,text:'€/L'},beginAtZero:false}}}});

let b=`<tr><th>Entfernung</th><th>Stationen</th><th>Ø E5</th><th>Ø E10</th><th>Ø Diesel</th></tr>`;
for(const r of D.bands){b+=`<tr><td>${r.label}</td><td>${r.n.toLocaleString('de')}</td>`+
  `<td>${r.e5?.toFixed(3)??'—'}</td><td>${r.e10?.toFixed(3)??'—'}</td><td>${r.diesel?.toFixed(3)??'—'}</td></tr>`;}
document.getElementById('bands').innerHTML=b;

new Chart(document.getElementById('bands_chart'),{type:'line',
 data:{labels:D.bands.map(r=>r.label),datasets:FUELS.map((f,i)=>(
  {label:NAMES[f],data:D.bands.map(r=>r[f]),borderColor:['#d73027','#f46d43','#1a9850'][i],pointRadius:3,tension:.2}))},
 options:{scales:{y:{title:{display:true,text:'€/L'}}}}});

document.getElementById('plants').innerHTML = D.factories.map(f=>f.name).join(' · ');
</script></body></html>"""


if __name__ == "__main__":
    main()
