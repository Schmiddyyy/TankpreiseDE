# Tankstellen-Karte & Analysen

A small, dependency-free toolkit that turns the **tankerkoenig-data** bulk CSV
repo into a browsable price map, a weekly price animation, and two analyses
(border zones, fuel vs. crude oil). Pure Python 3 standard library — no
`pip install` needed.

> ⚠️ **This project must live *inside* a clone of the tankerkoenig-data repo.**
> The scripts read `prices/` and `stations/` from the repo root, so this project
> has to sit at the root of that clone (alongside those directories). It will not
> work standalone.

## 1. Get the data (the tankerkoenig-data repo)

A free [Tankerkönig](https://www.tankerkoenig.de) account is required to access
the repo. Clone it (it is large — tens of GB):

```sh
git clone --depth 1 https://tankerkoenig@dev.azure.com/tankerkoenig/tankerkoenig-data/_git/tankerkoenig-data
cd tankerkoenig-data
```

After cloning you should have, at the repo root:

```
prices/      # daily price-change CSVs, 2014 .. today
stations/    # daily station lists + stations/stations.csv
TANKKOENIG.md    # upstream data description & license
```

## 2. Add these project files

Place this project **in that same `tankerkoenig-data/` folder** (copy it in, or
clone this repo on top of it). Layout:

```
tankerkoenig-data/        ← repo root (the bulk clone)
├── prices/               bulk source data, gitignored (2014 .. today)
├── stations/             bulk source data, gitignored
├── TANKKOENIG.md         upstream data description & license
├── README.md             this file
├── DATA_STRATEGY.md      why bulk repo, not the API
├── .gitignore
├── scripts/              all the Python (stdlib only)
│   ├── paths.py          central path config — the only place paths live
│   ├── ingest.py
│   ├── border_zones.py
│   ├── border_stats.py
│   ├── factory_zones.py
│   ├── factory_stats.py
│   ├── oil_vs_fuel.py
│   └── weekly_series.py
└── web/                  the static site (served by http.server)
    ├── index.html        price map + border-zone view
    ├── animation.html    weekly price time-lapse
    ├── border_stats.html generated
    ├── factory_stats.html generated
    ├── fuel_vs_oil.html  generated
    └── data/             generated JSON/CSV the pages fetch (gitignored)
```

`.gitignore` excludes `prices/`, `stations/`, `web/data/`, the generated pages
and `__pycache__/`, so only source is committed.

## 3. Build the data products

Run from the repo root (internet needed once for the border outline and Brent
crude). Outputs land in `web/data/`:

```sh
python3 scripts/ingest.py          # -> web/data/snapshot.json         (price per station)
python3 scripts/border_zones.py    # -> web/data/snapshot_borders.json (+ border distance)
python3 scripts/border_stats.py    # -> web/border_stats.html          (border vs inland)
python3 scripts/factory_zones.py   # -> web/data/snapshot_factories.json (+ factory distance)
python3 scripts/factory_stats.py   # -> web/factory_stats.html         (near car plants vs rest)
python3 scripts/oil_vs_fuel.py     # -> web/data/fuel_vs_oil.csv + .html (weekly; ~6 min)
python3 scripts/weekly_series.py --from 2021-01-01   # -> web/data/weekly.json (animation)
```

Useful flags: `ingest.py --max-days N`, `border_zones.py --zone KM`,
`factory_zones.py --zone KM`,
`oil_vs_fuel.py --stride 1` (full daily resolution, ~40 min),
`weekly_series.py --fuel diesel --from 2014-06-08 --stride 7`.

## 4. View it

The pages `fetch` JSON, so they need a web server (not `file://`). Serve `web/`:

```sh
cd web && python3 -m http.server 8000
```

Then open **http://localhost:8000/** :

| Page | What it shows |
|------|---------------|
| `index.html` | Map of all stations, colored by price; toggle to a border-zone view |
| `animation.html` | Weekly price time-lapse with play/pause + slider |
| `border_stats.html` | Average price near the border vs. inland, by fuel and distance band |
| `factory_stats.html` | Average price near car factories (≤10 km) vs. the rest, by distance band |
| `fuel_vs_oil.html` | National avg pump price (2014→today) vs. Brent crude |

## Notes

- **Why bulk repo and not the API?** See [DATA_STRATEGY.md](DATA_STRATEGY.md).
- The border comparison is **German stations only** — it does not yet compare to
  actual prices across the border.
- Data license is the upstream one (CC BY-NC-SA 4.0); see `TANKKOENIG.md`.
