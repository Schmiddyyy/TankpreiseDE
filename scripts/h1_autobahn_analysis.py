"""
H1 – Autobahn Preisvergleich
==============================
Vergleicht Dieselpreise von Tankstellen direkt an der Autobahn
mit Tankstellen in 500m–5km Entfernung.

Methode:
    1. Lade deutsche Autobahnen als Linien via Overpass API (OSM)
    2. Lade Einzelstations-Daten aus Tankerkönig-Repo
    3. Klassifiziere jede Tankstelle nach Autobahnabstand:
         Zone A: ≤ 500m   → direkt an der Autobahn
         Zone B: 500m–5km → Nahbereich
         Zone C: > 5km    → kein Autobahnbezug
    4. Vergleiche Ø-Dieselpreis pro Zone
    5. Interaktive Karte mit Autobahn-Linien + Stationspunkten

Verwendung:
    pip install geopandas folium branca requests matplotlib scipy duckdb tqdm
    python h1_autobahn_analysis.py --repo-path ./tankerkoenig-data
        → Öffnet Jahres-Schieberegler, lädt dann Juni des gewählten Jahres

    python h1_autobahn_analysis.py --repo-path ./tankerkoenig-data --year 2024
        → Direktstart ohne Schieberegler (wie bisher)

    python h1_autobahn_analysis.py --repo-path ./tankerkoenig-data --use-cache
        → Nur Karte ohne Neuberechnung (wenn Daten schon gecacht)
"""

import argparse
import json
import time
import webbrowser
from pathlib import Path

import branca.colormap as cm
import duckdb
import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from scipy import stats
from shapely.geometry import LineString, MultiLineString, Point
from tqdm import tqdm

# ── Jahres-Schieberegler (aus gemeinsamen Hilfsmodul) ──────────────────────
try:
    from year_slider import ask_year
    _HAS_SLIDER = True
except ImportError:
    _HAS_SLIDER = False

OUTPUT_DIR     = Path("./tankstellen_data")
AUTOBAHN_CACHE = OUTPUT_DIR / "osm_autobahnen.geojson"
STATIONS_CACHE = OUTPUT_DIR / "h1_stations_with_distances.geojson"

OVERPASS_URL   = "https://overpass-api.de/api/interpreter"

# Distanzzonen in Metern
ZONES = [
    (0,    500,   "≤ 500m (Autobahn)",  "#e74c3c", True),
    (500,  5000,  "500m – 5km",         "#e67e22", True),
    (5000, 99999, "> 5km",              "#2ecc71", True),
]


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 1: AUTOBAHNEN ALS LINIEN (OSM)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_autobahnen() -> gpd.GeoDataFrame:
    """Lädt alle deutschen Autobahnen (A-Straßen) als Linien aus OSM."""

    if AUTOBAHN_CACHE.exists():
        print("  ✓ Autobahn-Linien aus Cache geladen")
        return gpd.read_file(AUTOBAHN_CACHE)

    print("  ↓ Lade Autobahn-Linien via Overpass API...")
    print("    (kann 1–2 Minuten dauern)")

    # Alle highway=motorway in Deutschland
    query = """
    [out:json][timeout:180];
    area["ISO3166-1"="DE"][admin_level=2]->.de;
    way["highway"="motorway"](area.de);
    out geom;
    """

    try:
        r = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=200,
            headers={"User-Agent": "SpatialHumanities/1.0 (student research)"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ Overpass Fehler: {e}")
        print("  → Nutze vereinfachte Autobahn-Geometrie als Fallback")
        return _fallback_autobahnen()

    elements = data.get("elements", [])
    print(f"  → {len(elements)} Autobahnabschnitte gefunden")

    rows = []
    for el in elements:
        geom_pts = el.get("geometry", [])
        if len(geom_pts) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in geom_pts]
        tags   = el.get("tags", {})
        rows.append({
            "osm_id": el["id"],
            "ref":    tags.get("ref", ""),       # z.B. "A 9"
            "name":   tags.get("name", ""),
            "geometry": LineString(coords),
        })

    if not rows:
        return _fallback_autobahnen()

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    gdf.to_file(AUTOBAHN_CACHE, driver="GeoJSON")
    print(f"  ✓ {len(gdf)} Autobahnabschnitte gecacht")
    return gdf


def _fallback_autobahnen() -> gpd.GeoDataFrame:
    """Leerer GeoDataFrame wenn Overpass nicht erreichbar."""
    print("  ⚠ Kein Autobahn-Layer verfügbar (Overpass nicht erreichbar)")
    return gpd.GeoDataFrame(
        {"osm_id": [], "ref": [], "name": [], "geometry": []},
        crs="EPSG:4326"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 2: STATIONSDATEN MIT PREISEN (nur Juni)
# ─────────────────────────────────────────────────────────────────────────────

def load_station_prices(repo_path: Path, year: int) -> pd.DataFrame:
    """
    Lädt Einzelstationsdaten + Ø-Dieselpreis für Juni des gewählten Jahres.
    Nutzt DuckDB für effizienten Zugriff auf die großen Preis-CSVs.
    """
    # Stations-Metadaten
    stations_candidates = [
        repo_path / "stations" / "stations.csv",
        repo_path / "stations.csv",
        *list(repo_path.rglob("stations.csv"))[:3],
    ]
    stations_file = next((p for p in stations_candidates if p.exists()), None)
    if stations_file is None:
        raise FileNotFoundError(
            f"stations.csv nicht gefunden in {repo_path}\n"
            "Prüfe die Ordnerstruktur des Repos."
        )

    print(f"  📍 Lade Stationsmetadaten aus {stations_file.name}...")
    stations = pd.read_csv(stations_file, low_memory=True)

    # Spalten normalisieren
    rename = {
        "uuid": "station_uuid", "id": "station_uuid",
        "latitude": "lat", "longitude": "lng",
    }
    stations = stations.rename(columns={k: v for k, v in rename.items() if k in stations.columns})
    stations = stations.dropna(subset=["lat", "lng"])
    print(f"  ✓ {len(stations):,} Stationen mit Koordinaten")

    # Nur Juni-CSVs laden (Monat 06)
    price_base = repo_path / "prices" / str(year)
    if not price_base.exists():
        raise FileNotFoundError(f"Preisordner nicht gefunden: {price_base}")

    # Nur Juni-Unterordner: prices/YEAR/06/
    june_dir = price_base / "06"
    if june_dir.exists():
        csvs = sorted(june_dir.glob(f"{year}-06-*-prices.csv"))
        if not csvs:
            csvs = sorted(june_dir.glob(f"{year}-06-*.csv"))
    else:
        # Fallback: alle CSVs mit -06- im Namen
        csvs = sorted(price_base.rglob(f"{year}-06-*-prices.csv"))
        if not csvs:
            csvs = sorted(price_base.rglob(f"{year}-06-*.csv"))

    if not csvs:
        raise FileNotFoundError(
            f"Keine Juni-CSVs für {year} gefunden.\n"
            f"Erwartet in: {price_base / '06'} oder {price_base}"
        )

    print(f"  💰 Aggregiere Dieselpreise aus {len(csvs)} Juni-CSVs ({year}-06) via DuckDB...")
    # DuckDB Glob
    csv_pattern = str(june_dir / f"{year}-06-*-prices.csv").replace("\\", "/") \
        if june_dir.exists() else \
        str(price_base / "06" / f"{year}-06-*-prices.csv").replace("\\", "/")

    con = duckdb.connect()
    try:
        prices = con.execute(f"""
            SELECT
                station_uuid,
                AVG(CAST(diesel AS DOUBLE)) AS diesel_avg,
                COUNT(*) AS n_changes
            FROM read_csv_auto('{csv_pattern}', ignore_errors=true)
            WHERE diesel IS NOT NULL
              AND CAST(diesel AS DOUBLE) BETWEEN 0.8 AND 3.5
            GROUP BY station_uuid
        """).df()
        print(f"  ✓ {len(prices):,} Stationen mit Preisdaten (Juni {year})")
    except Exception as e:
        print(f"  ✗ DuckDB Fehler: {e} – versuche Einzeldatei-Fallback")
        prices = _pandas_price_fallback(csvs)

    # Zusammenführen
    merged = stations.merge(prices, on="station_uuid", how="inner")
    print(f"  ✓ {len(merged):,} Stationen mit Preis + Koordinaten")
    return merged


def _pandas_price_fallback(csvs: list) -> pd.DataFrame:
    chunks = []
    for csv in tqdm(csvs, desc="  CSVs lesen"):
        try:
            df = pd.read_csv(csv, usecols=["station_uuid", "diesel"],
                             dtype={"diesel": float}, low_memory=True)
            df = df[df["diesel"].between(0.8, 3.5)]
            chunks.append(df)
        except Exception:
            continue
    combined = pd.concat(chunks, ignore_index=True)
    return (combined.groupby("station_uuid")["diesel"]
            .agg(diesel_avg="mean", n_changes="count").reset_index())


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 3: DISTANZBERECHNUNG
# ─────────────────────────────────────────────────────────────────────────────

def compute_autobahn_distances(stations_df: pd.DataFrame,
                               autobahn_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Berechnet den Abstand jeder Tankstelle zur nächsten Autobahn in Metern.
    Arbeitet in UTM Zone 32N (metrisch, genau für Deutschland).
    """
    print("  📐 Berechne Abstände zur nächsten Autobahn...")
    print(f"     ({len(stations_df):,} Stationen × {len(autobahn_gdf):,} Autobahnabschnitte)")

    # Stationen als GeoDataFrame
    stations_gdf = gpd.GeoDataFrame(
        stations_df,
        geometry=gpd.points_from_xy(stations_df["lng"], stations_df["lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")

    if autobahn_gdf.empty:
        stations_gdf["dist_autobahn_m"] = np.nan
        stations_gdf["zone_label"] = "Unbekannt"
        stations_gdf["zone_color"] = "#aaaaaa"
        return stations_gdf.to_crs("EPSG:4326")

    # Autobahnen als vereinigte Geometrie (schneller als Einzelvergleich)
    autobahn_m = autobahn_gdf.to_crs("EPSG:25832")
    autobahn_union = autobahn_m.geometry.unary_union

    print("  → Distanzberechnung läuft (kann 1–2 Min dauern)...")
    stations_gdf["dist_autobahn_m"] = stations_gdf.geometry.distance(autobahn_union)

    # Zonen zuweisen
    def assign_zone(d):
        if pd.isna(d):
            return "Unbekannt", "#aaaaaa"
        for lo, hi, label, color, _ in ZONES:
            if lo <= d < hi:
                return label, color
        return ZONES[-1][2], ZONES[-1][3]

    stations_gdf[["zone_label", "zone_color"]] = pd.DataFrame(
        stations_gdf["dist_autobahn_m"].apply(assign_zone).tolist(),
        index=stations_gdf.index,
    )

    # Statistik
    for _, _, label, _, _ in ZONES:
        n = (stations_gdf["zone_label"] == label).sum()
        avg = stations_gdf[stations_gdf["zone_label"] == label]["diesel_avg"].mean()
        print(f"    {label:<22} {n:>5} Stationen  Ø {avg:.3f} €/L")

    return stations_gdf.to_crs("EPSG:4326")


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 4: STATISTIK + BOXPLOT
# ─────────────────────────────────────────────────────────────────────────────

def run_statistics(stations: gpd.GeoDataFrame, year: int) -> dict:
    print(f"\n{'═'*70}")
    print(f"  H1: Autobahnnähe vs. Dieselpreis – Juni {year}")
    print(f"{'═'*70}")
    print(f"  {'Zone':<25} {'N':>7} {'Ø Preis':>10} {'Median':>10} {'Std':>8}")
    print(f"{'─'*70}")

    zone_groups = {}
    for _, _, label, _, _ in ZONES:
        group = stations[stations["zone_label"] == label]["diesel_avg"].dropna()
        zone_groups[label] = group
        if len(group) > 0:
            print(f"  {label:<25} {len(group):>7,} "
                  f"{group.mean():>9.3f}€ "
                  f"{group.median():>9.3f}€ "
                  f"{group.std():>7.3f}")

    # ANOVA über alle Zonen
    groups = [g for g in zone_groups.values() if len(g) > 1]
    if len(groups) >= 2:
        f_stat, p_val = stats.f_oneway(*groups)
        print(f"\n  ANOVA: F={f_stat:.3f}, p={p_val:.5f}", end="  ")
        print("→ ✅ Signifikant" if p_val < 0.05 else "→ ❌ Nicht signifikant")

    # Direktvergleich Zone A vs Zone B (der interessanteste Vergleich!)
    zone_a = zone_groups.get("≤ 500m (Autobahn)", pd.Series())
    zone_b = zone_groups.get("500m – 5km", pd.Series())
    if len(zone_a) > 1 and len(zone_b) > 1:
        t_stat, p_val = stats.ttest_ind(zone_a, zone_b)
        diff = zone_a.mean() - zone_b.mean()
        print(f"\n  Direktvergleich Autobahn vs. Nahbereich:")
        print(f"    ≤ 500m:    {zone_a.mean():.3f} €/L")
        print(f"    500m–5km:  {zone_b.mean():.3f} €/L")
        print(f"    Differenz: {diff:+.4f} €/L  "
              f"= {diff*100:+.2f} Cent/L  "
              f"(t={t_stat:.2f}, p={p_val:.5f})")
        if p_val < 0.05:
            print(f"    → ✅ Autobahn-Tankstellen sind signifikant "
                  f"{'TEURER' if diff > 0 else 'GÜNSTIGER'}")

    print(f"{'═'*70}\n")
    return zone_groups


def save_boxplot(zone_groups: dict, year: int):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    labels = [z[2] for z in ZONES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    colors = [z[3] for z in ZONES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    data   = [zone_groups[l].tolist() for l in labels]
    means  = [np.mean(d) for d in data]

    # Boxplot
    bp = ax1.boxplot(data, patch_artist=True, notch=False,
                     medianprops=dict(color="black", linewidth=2),
                     showfliers=False)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    ax1.plot(range(1, len(means)+1), means, "D", color="#2c3e50",
             zorder=5, markersize=8, label="Mittelwert")
    ax1.set_xticklabels(labels, fontsize=9)
    ax1.set_ylabel("Diesel (€/L)", fontsize=12)
    ax1.set_title(f"H1: Dieselpreis nach Autobahnabstand – Juni {year}",
                  fontweight="bold", pad=12)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_facecolor("#f9f9f9")
    ax1.legend(fontsize=10)

    # Balkendiagramm mit Differenz-Annotation
    bars = ax2.bar(labels, means, color=colors, alpha=0.85, edgecolor="white",
                   width=0.5)
    for bar, mean in zip(bars, means):
        ax2.text(bar.get_x() + bar.get_width()/2, mean + 0.0005,
                 f"{mean:.3f}€", ha="center", va="bottom",
                 fontsize=11, fontweight="bold")

    # Differenz-Linie zwischen Zone A und B einzeichnen
    if len(means) >= 2:
        diff = means[0] - means[1]
        ax2.annotate(
            f"Δ {diff:+.4f} €/L",
            xy=(0.5, max(means[0], means[1])),
            xytext=(0.5, max(means[0], means[1]) + 0.003),
            ha="center", fontsize=10, color="#c0392b", fontweight="bold",
        )

    all_m = [m for m in means if m > 0]
    if all_m:
        ax2.set_ylim(min(all_m) - 0.015, max(all_m) + 0.015)
    ax2.set_ylabel("Ø Diesel (€/L)", fontsize=12)
    ax2.set_title(f"Ø Dieselpreis je Zone – Juni {year}", fontweight="bold", pad=12)
    ax2.set_facecolor("#f9f9f9")
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_xticklabels(labels, fontsize=9)

    plt.suptitle(
        f"H1: Sind Autobahn-Tankstellen teurer? (Tankerkönig + OSM) – Juni {year}",
        fontsize=13, y=1.01
    )
    plt.tight_layout()
    out = OUTPUT_DIR / f"h1_boxplot_{year}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ Boxplot: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 5: INTERAKTIVE KARTE
# ─────────────────────────────────────────────────────────────────────────────

def build_map(stations: gpd.GeoDataFrame,
              autobahn_gdf: gpd.GeoDataFrame,
              year: int) -> folium.Map:

    m = folium.Map(
        location=[51.2, 10.5], zoom_start=6,
        tiles="CartoDB positron", prefer_canvas=True,
    )

    # Preisfarbskala
    vmin = stations["diesel_avg"].quantile(0.05)
    vmax = stations["diesel_avg"].quantile(0.95)
    price_colormap = cm.LinearColormap(
        colors=["#2ecc71", "#f1c40f", "#e74c3c"],
        vmin=vmin, vmax=vmax,
        caption=f"Diesel (€/L) – Juni {year}",
    )
    price_colormap.add_to(m)

    # ── AUTOBAHN-LINIEN ───────────────────────────────────────────────────────
    if not autobahn_gdf.empty:
        autobahn_layer = folium.FeatureGroup(
            name="🛣 Autobahnen (OSM)", show=True
        )
        autobahn_wgs = autobahn_gdf.to_crs("EPSG:4326")

        for _, row in autobahn_wgs.iterrows():
            if row.geometry is None:
                continue
            ref  = row.get("ref", "")
            name = row.get("name", "")

            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda f: {
                    "color":   "#c0392b",
                    "weight":  2.0,
                    "opacity": 0.75,
                },
                tooltip=folium.Tooltip(
                    f"<b>{ref}</b> {name}".strip(),
                    sticky=False,
                ) if ref or name else None,
            ).add_to(autobahn_layer)

        autobahn_layer.add_to(m)

    # ── LAYER 0: TANKSTELLEN (Preis-Füllung) – immer sichtbar ─────────────────
    points_layer = folium.FeatureGroup(name="⛽ Tankstellen (Preis)", show=True)
    points_layer.add_to(m)

    # ── LAYER 1: ZONE (nur Rand) – einzeln zu-/abschaltbar ────────────────────
    for _, _, label, color, enabled in ZONES:
        subset = stations[stations["zone_label"] == label]
        if subset.empty:
            continue

        layer = folium.FeatureGroup(
            name=f"{'🔴' if '500m' in label and 'km' not in label else '🟠' if 'km' in label and '5' not in label else '🟢'} {label} – Rand (N={len(subset):,})",
            show=enabled,
        )

        sample = subset if len(subset) <= 3000 else subset.sample(3000, random_state=42)

        for _, row in sample.iterrows():
            diesel = row.get("diesel_avg")
            name   = row.get("name", row.get("brand", "Tankstelle"))
            dist   = row.get("dist_autobahn_m", 0)
            brand  = row.get("brand", "")
            loc    = [row.geometry.y, row.geometry.x]
            fill   = price_colormap(diesel) if pd.notna(diesel) else color

            tooltip = (
                f"<b>{name}</b>"
                + (f" <span style='color:#888'>({brand})</span>" if brand and brand != name else "")
                + f"<br>Ø Diesel: <b style='color:#c0392b'>{diesel:.3f} €/L</b><br>"
                + f"Abstand Autobahn: {dist:.0f} m<br>"
                + f"Zone: <b>{label}</b>"
            )

            folium.CircleMarker(
                location=loc,
                radius=4,
                weight=0,
                fill=True,
                fill_color=fill,
                fill_opacity=0.85,
                tooltip=folium.Tooltip(tooltip, sticky=True),
            ).add_to(points_layer)

            folium.CircleMarker(
                location=loc,
                radius=4,
                color=color,
                weight=1,
                fill=False,
                tooltip=folium.Tooltip(tooltip, sticky=True),
            ).add_to(layer)

        layer.add_to(m)

    # ── LEGENDE ───────────────────────────────────────────────────────────────
    zone_rows = ""
    for _, _, label, color, _ in ZONES:
        zone_rows += (
            f'<div style="margin:4px 0">'
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:{color};border-radius:50%;border:1px solid #555;'
            f'margin-right:7px;vertical-align:middle"></span>'
            f'<b>{label}</b></div>'
        )
    legend_html = f"""
    <div style="position:fixed;bottom:40px;left:40px;z-index:1000;
        background:white;padding:14px 18px;border-radius:8px;
        box-shadow:0 2px 10px rgba(0,0,0,0.2);
        font-family:Arial,sans-serif;font-size:13px;
        border-left:4px solid #c0392b;max-width:290px;">
        <b>🛣 H1: Autobahn-Preisvergleich – Juni {year}</b><br>
        <span style="color:#666;font-size:11px">
            Punkt-Füllung = Preis (immer sichtbar) · Rand = Distanzzone (über Layer-Liste rechts einzeln zuschaltbar)
        </span>
        <hr style="margin:7px 0;border:none;border-top:1px solid #eee">
        <div style="margin:4px 0">
            <span style="display:inline-block;width:28px;height:3px;
            background:#c0392b;margin-right:7px;vertical-align:middle"></span>
            <b>Autobahn</b> (OSM)
        </div>
        {zone_rows}
        <hr style="margin:7px 0;border:none;border-top:1px solid #eee">
        <span style="color:#999;font-size:11px">
            Tankerkönig CC BY 4.0 · OpenStreetMap ODbL<br>
            Stichprobe: max. 3.000 Punkte/Zone
        </span>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=False).add_to(m)

    return m


# ─────────────────────────────────────────────────────────────────────────────
# HAUPTPROGRAMM
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="H1: Autobahn-Tankstellen vs. Nahbereich analysieren"
    )
    parser.add_argument("--repo-path", required=True,
                        help="Pfad zum tankerkoenig-data Repo")
    parser.add_argument("--year", type=int, default=None,
                        help="Analysejahr (2014–2026). Ohne Angabe → Schieberegler öffnet sich.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Alle Caches löschen und neu berechnen")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Jahresauswahl: Schieberegler oder CLI-Argument ────────────────────────
    if args.year is not None:
        year = args.year
    elif _HAS_SLIDER:
        year = ask_year(title="H1 – Autobahn-Preisvergleich", default_year=2024)
    else:
        print("⚠ year_slider.py nicht gefunden – bitte --year angeben.")
        parser.error("--year ist erforderlich wenn year_slider.py fehlt")

    print(f"\n{'═'*60}")
    print(f"  H1 Autobahn-Preisvergleich – Diesel Juni {year}")
    print(f"{'═'*60}\n")

    if args.clear_cache:
        for f in [AUTOBAHN_CACHE, STATIONS_CACHE]:
            if f.exists():
                f.unlink()
                print(f"  🗑 Cache gelöscht: {f.name}")

    # 1) Autobahnen laden
    print("[1/5] Autobahn-Linien laden (OSM)...")
    autobahn = fetch_autobahnen()

    # 2) Stationsdaten + Juni-Preise laden
    print(f"\n[2/5] Stationsdaten + Juni-Preise {year} laden...")
    if STATIONS_CACHE.exists() and not args.clear_cache:
        cached = gpd.read_file(STATIONS_CACHE)
        # Cache nur nutzen wenn gleiches Jahr
        if "year" in cached.columns and cached["year"].iloc[0] == year:
            print("  ✓ Stationsdaten aus Cache")
            stations = cached
        else:
            print(f"  ⚠ Cache aus anderem Jahr – lade neu für {year}")
            stations = None
    else:
        stations = None

    if stations is None:
        stations_df = load_station_prices(Path(args.repo_path), year)

        # 3) Distanzen berechnen
        print(f"\n[3/5] Distanzen zur Autobahn berechnen...")
        stations = compute_autobahn_distances(stations_df, autobahn)
        stations["year"] = year
        stations.to_file(STATIONS_CACHE, driver="GeoJSON")
        print(f"  ✓ Ergebnisse gecacht: {STATIONS_CACHE}")

    # 4) Statistik
    print(f"\n[4/5] Statistik...")
    zone_groups = run_statistics(stations, year)
    save_boxplot(zone_groups, year)

    # 5) Karte
    print(f"[5/5] Karte erstellen...")
    m = build_map(stations, autobahn, year)

    out = OUTPUT_DIR / f"h1_autobahn_map_{year}.html"
    m.save(str(out))
    print(f"  ✅ Karte: {out}")

    if not args.no_browser:
        webbrowser.open(out.resolve().as_uri())
        print("  🌐 Öffne im Browser...")


if __name__ == "__main__":
    main()