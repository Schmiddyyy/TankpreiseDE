#!/usr/bin/env python3
"""Build a per-station WEEKLY price series for the animation page.

Like the oil script, this *samples* one daily file per week (stride 7) rather
than reading all 89 GB: each frame is that day's snapshot — per station, its
last price of the day for one fuel. Writes weekly.json consumed by animation.html.

Payload is kept small: prices are stored as integers in millicents (1.799 -> 1799,
0 = no data that week) and only stations seen at least once are kept.

Cost is ~0.6 s per sampled file, so the whole 2014->today range is only a few
minutes. Use --from/--to to limit the range and --stride to change spacing.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from datetime import date, timedelta

import ingest  # reuse load_stations() (Germany bbox filter + coord rounding)
from paths import DATA_DIR, PRICE_GLOB
FUEL_COL = {"diesel": 2, "e5": 3, "e10": 4}  # column index in prices CSV


def file_date(path: str) -> date:
    y, m, d = map(int, os.path.basename(path)[:10].split("-"))
    return date(y, m, d)


def daily_last_price(path: str, col: int) -> dict[str, float]:
    """uuid -> last (latest) non-zero price of the day for one fuel."""
    last: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) > col:
                last[row[1]] = row[col]
    out: dict[str, float] = {}
    for uuid, raw in last.items():
        try:
            v = float(raw)
        except ValueError:
            continue
        if v > 0:
            out[uuid] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fuel", choices=list(FUEL_COL), default="e5")
    ap.add_argument("--from", dest="frm", help="start date YYYY-MM-DD")
    ap.add_argument("--to", dest="to", help="end date YYYY-MM-DD")
    ap.add_argument("--stride", type=int, default=7,
                    help="sample every Nth daily file (default 7 = weekly)")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "weekly.json"))
    args = ap.parse_args()
    col = FUEL_COL[args.fuel]

    files = sorted(glob.glob(PRICE_GLOB))
    if not files:
        raise SystemExit("No price files found.")
    if args.frm:
        files = [f for f in files if file_date(f) >= date.fromisoformat(args.frm)]
    if args.to:
        files = [f for f in files if file_date(f) <= date.fromisoformat(args.to)]
    files = files[::args.stride]  # one snapshot per week
    print(f"Fuel {args.fuel} | {len(files)} weekly snapshots | "
          f"{file_date(files[0])} .. {file_date(files[-1])}")

    # week label -> uuid -> price (one snapshot per sampled file)
    week_list: list[str] = []
    snaps: list[dict[str, float]] = []
    for i, path in enumerate(files):
        week_list.append(file_date(path).isoformat())
        snaps.append(daily_last_price(path, col))
        if i % 25 == 0:
            print(f"  {i}/{len(files)}  {week_list[-1]}")

    stations = ingest.load_stations()

    # Build per-station int series; drop stations never seen in range.
    out_stations = []
    all_prices = []
    for uuid, meta in stations.items():
        series = []
        seen = False
        for snap in snaps:
            price = snap.get(uuid)
            if price:
                series.append(round(price * 1000))  # millicents
                all_prices.append(price)
                seen = True
            else:
                series.append(0)  # 0 = no data
        if seen:
            out_stations.append({"id": uuid, "name": meta["name"],
                                  "lat": meta["lat"], "lng": meta["lng"], "p": series})

    # Robust color scale (1st..99th percentile) so a few junk prices don't
    # squash the ramp.
    all_prices.sort()
    n = len(all_prices)
    gmin = all_prices[int(0.01 * (n - 1))]
    gmax = all_prices[int(0.99 * (n - 1))]

    lats = [s["lat"] for s in out_stations]
    lngs = [s["lng"] for s in out_stations]
    payload = {
        "fuel": args.fuel,
        "weeks": week_list,
        "scale": {"min": round(gmin, 3), "max": round(gmax, 3)},
        "bounds": {"north": max(lats), "south": min(lats),
                   "east": max(lngs), "west": min(lngs)},
        "stations": out_stations,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    size = os.path.getsize(args.out) / 1e6
    print(f"\nWrote {args.out}: {len(out_stations)} stations × {len(week_list)} weeks "
          f"({size:.1f} MB)")
    print(f"Price scale: {payload['scale']['min']} .. {payload['scale']['max']} €")


if __name__ == "__main__":
    main()
