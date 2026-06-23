#!/usr/bin/env python3
"""Build a compact, map-ready snapshot from the tankerkoenig bulk CSV repo.

The bulk repo stores every price-*change event* (billions of rows since 2014)
plus a daily station list. A web map only needs one question answered:
"what does every station charge *right now*, and where is it?"

This script collapses the history to that single moment:

  1. Forward-fill: walk the daily prices/ files newest-first and keep, for each
     station, the most recent non-removed price it can find. That throws away
     the entire time series and keeps one row per station.
  2. Join: attach name + coordinates from stations/stations.csv.
  3. Trim & round: keep only the fields the map needs; round coords to 5
     decimals (~1 m) and prices to 3 (cent/10).

Output (snapshot.json):
  {
    "generated_at": "<iso>",
    "source_date":  "<latest prices file date>",
    "bounds": { "north": .., "south": .., "east": .., "west": .. },  # 4 border fields
    "stations": [ { "id","name","lat","lng","e5","e10","diesel" }, ... ]
  }

Stdlib only — no pandas — so a low-bandwidth teammate can run it with a plain
Python install. Result is ~3-5 MB for all ~15k German stations vs. tens of GB
of raw CSVs.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from datetime import datetime, timezone

from paths import DATA_DIR, PRICE_GLOB, PRICES_DIR, STATIONS_CSV

# Fuels we surface on the map. A value of 0.000 means "not offered / unknown"
# in this dataset and is treated as missing (null), not a real price.
FUELS = ("diesel", "e5", "e10")
# change flag 2 == station removed that fuel; ignore those rows for that fuel.
REMOVED = "2"


def latest_price_per_station(max_days: int) -> tuple[dict[str, dict], str | None]:
    """Return {uuid: {diesel,e5,e10}} using the most recent known price per
    fuel, scanning the newest `max_days` daily files. Also returns the date of
    the newest file scanned (the snapshot's source date)."""
    files = sorted(glob.glob(PRICE_GLOB), reverse=True)
    if not files:
        raise SystemExit(f"No price files found under {PRICES_DIR}")

    source_date = os.path.basename(files[0]).replace("-prices.csv", "")
    prices: dict[str, dict] = {}

    for path in files[:max_days]:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            # Within a day, later rows are newer; keep the last seen per fuel.
            for row in reader:
                uuid = row["station_uuid"]
                slot = prices.setdefault(uuid, {})
                for fuel in FUELS:
                    if fuel in slot:  # already have a newer value from a later file/row this pass
                        continue
                    if row.get(f"{fuel}change") == REMOVED:
                        continue
                    try:
                        val = float(row[fuel])
                    except (ValueError, KeyError):
                        continue
                    if val > 0:
                        slot[fuel] = round(val, 3)
        # We scan newest file first, but within each file we must take the LAST
        # row per station. csv reads top-to-bottom, so for the newest file we
        # would keep the *earliest* row of the day. Re-read newest file
        # reversed below to fix that for same-day accuracy.
        if path == files[0]:
            _take_last_row_of_day(path, prices)

    return prices, source_date


def _take_last_row_of_day(path: str, prices: dict[str, dict]) -> None:
    """For the newest file, prefer the last (chronologically latest) row of the
    day per station/fuel, overriding the first-row value taken in the forward pass."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in reversed(rows):
        uuid = row["station_uuid"]
        slot = prices.setdefault(uuid, {})
        for fuel in FUELS:
            marker = f"__last_{fuel}"
            if marker in slot:
                continue
            if row.get(f"{fuel}change") == REMOVED:
                continue
            try:
                val = float(row[fuel])
            except (ValueError, KeyError):
                continue
            if val > 0:
                slot[fuel] = round(val, 3)
                slot[marker] = True
    for slot in prices.values():
        for fuel in FUELS:
            slot.pop(f"__last_{fuel}", None)


# Plausible bounding box for Germany (padded). Rows outside it — chiefly
# 0.0/0.0 placeholders — are bad data and are dropped so they don't poison
# the map bounds.
LAT_RANGE = (47.0, 55.2)
LNG_RANGE = (5.5, 15.5)


def load_stations() -> dict[str, dict]:
    """Return {uuid: {name, lat, lng}} from the current station list."""
    stations: dict[str, dict] = {}
    with open(STATIONS_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                lat = round(float(row["latitude"]), 5)
                lng = round(float(row["longitude"]), 5)
            except (ValueError, KeyError):
                continue
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
                continue
            stations[row["uuid"]] = {
                "name": (row.get("name") or "").strip(),
                "lat": lat,
                "lng": lng,
            }
    return stations


def build_snapshot(max_days: int) -> dict:
    prices, source_date = latest_price_per_station(max_days)
    stations = load_stations()

    records = []
    north = east = float("-inf")
    south = west = float("inf")

    for uuid, meta in stations.items():
        slot = prices.get(uuid)
        if not slot:
            continue  # no recent price -> not useful on a price map
        lat, lng = meta["lat"], meta["lng"]
        records.append(
            {
                "id": uuid,
                "name": meta["name"],
                "lat": lat,
                "lng": lng,
                "e5": slot.get("e5"),
                "e10": slot.get("e10"),
                "diesel": slot.get("diesel"),
            }
        )
        north, south = max(north, lat), min(south, lat)
        east, west = max(east, lng), min(west, lng)

    if not records:
        raise SystemExit("No stations matched a recent price; nothing to write.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_date": source_date,
        "bounds": {"north": north, "south": south, "east": east, "west": west},
        "stations": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--out", default=os.path.join(DATA_DIR, "snapshot.json"),
        help="output path (default: web/data/snapshot.json)",
    )
    parser.add_argument(
        "--max-days", type=int, default=30,
        help="how many recent daily price files to scan for forward-fill (default: 30)",
    )
    args = parser.parse_args()

    snapshot = build_snapshot(args.max_days)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(args.out) / 1e6
    b = snapshot["bounds"]
    print(f"Wrote {len(snapshot['stations'])} stations to {args.out} ({size_mb:.1f} MB)")
    print(f"Source date: {snapshot['source_date']}")
    print(f"Bounds  N {b['north']}  S {b['south']}  E {b['east']}  W {b['west']}")


if __name__ == "__main__":
    main()
