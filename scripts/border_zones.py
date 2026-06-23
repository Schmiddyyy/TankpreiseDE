#!/usr/bin/env python3
"""Tag each station with its distance to the nearest German national border.

Adds a `border_km` field to every station in snapshot.json and writes
snapshot_borders.json. Stations within --zone km (default 25) are flagged as
the "border zone"; the script prints the average price in that zone vs. inland
so you can see whether border stations price differently.

Needs the German national outline as GeoJSON (downloaded once to germany.geo.json
if not already present). No foreign price data is involved — this compares
German border-zone stations to German inland stations.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request

from paths import DATA_DIR

GEOJSON_PATH = os.path.join(DATA_DIR, "germany.geo.json")
# National outline (single polygon), medium resolution — small but accurate enough.
GEOJSON_URL = (
    "https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/"
    "main/1_deutschland/3_mittel.geo.json"
)

# Equirectangular projection around central Germany -> kilometres.
LAT0 = math.radians(51.0)
KM_PER_DEG_LAT = 111.32
KM_PER_DEG_LNG = 111.32 * math.cos(LAT0)


def fetch_border() -> list[tuple[float, float]]:
    """Return the border as a flat list of (x_km, y_km) ring vertices."""
    if not os.path.exists(GEOJSON_PATH):
        print(f"Downloading German outline -> {GEOJSON_PATH}")
        urllib.request.urlretrieve(GEOJSON_URL, GEOJSON_PATH)
    with open(GEOJSON_PATH, encoding="utf-8") as fh:
        gj = json.load(fh)

    rings: list[list] = []
    for feat in gj.get("features", [gj]):
        geom = feat.get("geometry", feat)
        gtype, coords = geom["type"], geom["coordinates"]
        polys = [coords] if gtype == "Polygon" else coords  # MultiPolygon
        for poly in polys:
            for ring in poly:  # exterior + holes; holes are still border lines
                rings.append(ring)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for ring in rings:
        pts = [(lng * KM_PER_DEG_LNG, lat * KM_PER_DEG_LAT) for lng, lat in ring]
        for a, b in zip(pts, pts[1:]):
            segments.append((a, b))
    print(f"Border: {len(segments)} segments")
    return segments


def point_seg_km(px, py, ax, ay, bx, by) -> float:
    """Distance (km) from point to segment AB, all in projected km."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_border_km(lat, lng, segments) -> float:
    px, py = lng * KM_PER_DEG_LNG, lat * KM_PER_DEG_LAT
    best = math.inf
    for (ax, ay), (bx, by) in segments:
        # cheap bounding reject before the exact segment distance
        if abs(px - ax) - best > 0 and abs(px - bx) - best > 0 and \
           ((px < ax and px < bx) or (px > ax and px > bx)) and \
           abs(px - ax) > best:
            continue
        d = point_seg_km(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return round(best, 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", default=os.path.join(DATA_DIR, "snapshot.json"))
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "snapshot_borders.json"))
    ap.add_argument("--zone", type=float, default=25.0, help="border-zone width in km")
    args = ap.parse_args()

    segments = fetch_border()
    with open(args.inp, encoding="utf-8") as fh:
        snap = json.load(fh)

    zone_sum = {"e5": 0.0, "e10": 0.0, "diesel": 0.0}
    zone_n = {"e5": 0, "e10": 0, "diesel": 0}
    in_sum = {"e5": 0.0, "e10": 0.0, "diesel": 0.0}
    in_n = {"e5": 0, "e10": 0, "diesel": 0}

    for s in snap["stations"]:
        km = nearest_border_km(s["lat"], s["lng"], segments)
        s["border_km"] = km
        tgt_s, tgt_n = (zone_sum, zone_n) if km <= args.zone else (in_sum, in_n)
        for fuel in ("e5", "e10", "diesel"):
            if s.get(fuel) is not None:
                tgt_s[fuel] += s[fuel]
                tgt_n[fuel] += 1

    snap["border_zone_km"] = args.zone
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))

    n_zone = sum(1 for s in snap["stations"] if s["border_km"] <= args.zone)
    print(f"\nWrote {args.out}")
    print(f"Border zone (≤{args.zone:g} km): {n_zone} stations | inland: "
          f"{len(snap['stations']) - n_zone}")
    print(f"\n{'fuel':<8}{'border avg':>12}{'inland avg':>12}{'diff (ct)':>12}")
    for fuel in ("e5", "e10", "diesel"):
        bz = zone_sum[fuel] / zone_n[fuel] if zone_n[fuel] else float("nan")
        il = in_sum[fuel] / in_n[fuel] if in_n[fuel] else float("nan")
        print(f"{fuel:<8}{bz:>12.3f}{il:>12.3f}{(bz - il) * 100:>+12.2f}")


if __name__ == "__main__":
    main()
