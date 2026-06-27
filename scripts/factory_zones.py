#!/usr/bin/env python3
"""Tag each station with its distance to the nearest German car factory.

Adds `factory_km` (and the nearest plant's name) to every station in
snapshot.json and writes snapshot_factories.json. Stations within --zone km
(default 10) are the "factory zone"; the script prints the average price there
vs. everywhere else.

The plant list is embedded below (major OEM passenger-car / commercial assembly
sites). Coordinates are approximate plant centres — fine for a 10 km radius.
"""

from __future__ import annotations

import argparse
import json
import math
import os

from paths import DATA_DIR

# (name, latitude, longitude) — major German vehicle assembly plants.
FACTORIES = [
    ("VW Wolfsburg", 52.4319, 10.7865),
    ("VW Emden", 53.3958, 7.2065),
    ("VW Zwickau", 50.7100, 12.4900),
    ("VW Osnabrück", 52.2720, 8.0500),
    ("VW Nutzfahrzeuge Hannover", 52.4000, 9.7000),
    ("VW Dresden", 51.0420, 13.7600),
    ("Audi Ingolstadt", 48.7665, 11.4258),
    ("Audi Neckarsulm", 49.1939, 9.2240),
    ("BMW München", 48.1773, 11.5560),
    ("BMW Dingolfing", 48.6371, 12.4920),
    ("BMW Regensburg", 48.9931, 12.1340),
    ("BMW Leipzig", 51.4030, 12.2960),
    ("Mercedes-Benz Sindelfingen", 48.7100, 9.0030),
    ("Mercedes-Benz Stuttgart-Untertürkheim", 48.7900, 9.2400),
    ("Mercedes-Benz Bremen", 53.0660, 8.7460),
    ("Mercedes-Benz Rastatt", 48.8580, 8.2030),
    ("Porsche Stuttgart-Zuffenhausen", 48.8330, 9.1530),
    ("Porsche Leipzig", 51.4080, 12.2960),
    ("Opel Rüsselsheim", 49.9920, 8.4130),
    ("Opel Eisenach", 50.9930, 10.3320),
    ("Ford Köln", 50.9970, 6.9290),
    ("Ford Saarlouis", 49.3260, 6.7570),
]

EARTH_KM = 6371.0


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_KM * 2 * math.asin(math.sqrt(a))


def nearest_factory(lat, lng):
    best_km, best_name = math.inf, None
    for name, flat, flng in FACTORIES:
        d = haversine_km(lat, lng, flat, flng)
        if d < best_km:
            best_km, best_name = d, name
    return round(best_km, 1), best_name


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    # Build on the border-tagged snapshot when present so the output carries
    # both border_km and factory_km (the map's combined view needs both).
    default_in = os.path.join(DATA_DIR, "snapshot_borders.json")
    if not os.path.exists(default_in):
        default_in = os.path.join(DATA_DIR, "snapshot.json")
    ap.add_argument("--in", dest="inp", default=default_in)
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "snapshot_factories.json"))
    ap.add_argument("--zone", type=float, default=10.0, help="factory-zone radius in km")
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as fh:
        snap = json.load(fh)

    near_sum = {"e5": 0.0, "e10": 0.0, "diesel": 0.0}
    near_n = {"e5": 0, "e10": 0, "diesel": 0}
    far_sum = {"e5": 0.0, "e10": 0.0, "diesel": 0.0}
    far_n = {"e5": 0, "e10": 0, "diesel": 0}

    for s in snap["stations"]:
        km, name = nearest_factory(s["lat"], s["lng"])
        s["factory_km"] = km
        s["factory"] = name
        tgt_s, tgt_n = (near_sum, near_n) if km <= args.zone else (far_sum, far_n)
        for fuel in ("e5", "e10", "diesel"):
            if s.get(fuel) is not None:
                tgt_s[fuel] += s[fuel]
                tgt_n[fuel] += 1

    snap["factory_zone_km"] = args.zone
    snap["factories"] = [{"name": n, "lat": la, "lng": lo} for n, la, lo in FACTORIES]
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))

    n_near = sum(1 for s in snap["stations"] if s["factory_km"] <= args.zone)
    print(f"Wrote {args.out}")
    print(f"Factory zone (≤{args.zone:g} km): {n_near} stations | rest: "
          f"{len(snap['stations']) - n_near}  ({len(FACTORIES)} plants)")
    print(f"\n{'fuel':<8}{'near avg':>12}{'rest avg':>12}{'diff (ct)':>12}")
    for fuel in ("e5", "e10", "diesel"):
        nv = near_sum[fuel] / near_n[fuel] if near_n[fuel] else float("nan")
        fv = far_sum[fuel] / far_n[fuel] if far_n[fuel] else float("nan")
        print(f"{fuel:<8}{nv:>12.3f}{fv:>12.3f}{(nv - fv) * 100:>+12.2f}")


if __name__ == "__main__":
    main()
