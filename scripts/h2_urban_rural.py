"""
H2 – Urban vs. Rural Analyse (Einzelstations-Ebene)
=====================================================
Vergleicht Dieselpreise einzelner Tankstellen nach ihrer Distanz
zur nächsten deutschen Großstadt (> 100k Einwohner).

Methode:
    1. Lade Einzelstationsdaten + Preise aus Tankerkönig-Repo
    2. Berechne Distanz jeder Tankstelle zum nächsten Stadtkern
    3. Klassifiziere in Zonen:
         Kernstadt:  ≤ 5 km
         Suburban:   5–20 km
         Semi-Rural: 20–50 km
         Rural:      > 50 km
    4. Vergleiche Ø-Dieselpreis pro Zone
    5. Interaktive Karte mit Großstadt-Markern + Einzelpunkten

Verwendung:
    pip install geopandas folium branca duckdb matplotlib scipy tqdm
    python h2_urban_rural.py --repo-path ./tankerkoenig-data --year 2024

    # Ohne Neuberechnung (wenn Cache vorhanden):
    python h2_urban_rural.py --repo-path ./tankerkoenig-data --year 2024 --use-cache
"""

import argparse
import webbrowser
from pathlib import Path

import branca.colormap as cm
import duckdb
import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from tqdm import tqdm

OUTPUT_DIR      = Path("./tankstellen_data")
STATIONS_CACHE  = OUTPUT_DIR / "h2_stations_with_distances.geojson"

GROSSSTAEDTE = [
    ("Berlin",          52.520, 13.405, 3645000),
    ("Hamburg",         53.550,  9.993, 1841000),
    ("München",         48.137, 11.576, 1488000),
    ("Köln",            50.938,  6.960, 1084000),
    ("Frankfurt",       50.110,  8.682,  759000),
    ("Stuttgart",       48.775,  9.182,  626000),
    ("Düsseldorf",      51.227,  6.773,  619000),
    ("Leipzig",         51.340, 12.374,  601000),
    ("Dortmund",        51.514,  7.466,  588000),
    ("Essen",           51.455,  7.011,  579000),
    ("Bremen",          53.079,  8.801,  563000),
    ("Dresden",         51.050, 13.738,  554000),
    ("Hannover",        52.374,  9.738,  532000),
    ("Nürnberg",        49.453, 11.077,  515000),
    ("Duisburg",        51.435,  6.762,  496000),
    ("Bochum",          51.482,  7.216,  364000),
    ("Wuppertal",       51.257,  7.150,  355000),
    ("Bielefeld",       52.021,  8.532,  333000),
    ("Bonn",            50.733,  7.099,  329000),
    ("Münster",         51.962,  7.626,  314000),
    ("Karlsruhe",       49.009,  8.404,  308000),
    ("Mannheim",        49.488,  8.466,  305000),
    ("Augsburg",        48.371, 10.898,  295000),
    ("Wiesbaden",       50.083,  8.240,  278000),
    ("Gelsenkirchen",   51.517,  7.086,  259000),
    ("Mönchengladbach", 51.185,  6.441,  261000),
    ("Braunschweig",    52.269, 10.523,  248000),
    ("Chemnitz",        50.832, 12.924,  246000),
    ("Kiel",            54.323, 10.123,  246000),
    ("Aachen",          50.776,  6.084,  245000),
    ("Halle",           51.483, 11.970,  238000),
    ("Magdeburg",       52.120, 11.628,  232000),
    ("Freiburg",        47.995,  7.842,  229000),
    ("Krefeld",         51.338,  6.585,  226000),
    ("Lübeck",          53.869, 10.687,  216000),
    ("Oberhausen",      51.470,  6.851,  210000),
    ("Erfurt",          50.978, 11.029,  213000),
    ("Mainz",           49.999,  8.274,  214000),
    ("Rostock",         54.092, 12.099,  208000),
    ("Kassel",          51.313,  9.481,  200000),
]

URBAN_TYPES = [
    (0,    5,   "Kernstadt (≤ 5km)",    "#e74c3c", True),
    (5,    20,  "Suburban (5–20km)",    "#e67e22", True),
    (20,   50,  "Semi-Rural (20–50km)", "#f1c40f", True),
    (50,  999,  "Rural (> 50km)",       "#27ae60", True),
]


# ─────────────────────────────────────────────────────────────────────────────
# STATIONSDATEN + PREISE LADEN
# ─────────────────────────────────────────────────────────────────────────────

def load_station_prices(repo_path: Path, year: int) -> pd.DataFrame:
    """Lädt Einzelstationsdaten + Jahres-Ø-Dieselpreis via DuckDB."""

    # Stations-Metadaten
    candidates = [
        repo_path / "stations" / "stations.csv",
        repo_path / "stations.csv",
        *list(repo_path.rglob("stations.csv"))[:3],
    ]
    stations_file = next((p for p in candidates if p.exists()), None)
    if stations_file is None:
        raise FileNotFoundError(f"stations.csv nicht gefunden in {repo_path}")

    print(f"  📍 Lade Stationsmetadaten aus {stations_file.name}...")
    stations = pd.read_csv(stations_file, low_memory=True)
    rename = {
        "uuid": "station_uuid", "id": "station_uuid",
        "latitude": "lat",      "longitude": "lng",
    }
    stations = stations.rename(columns={k: v for k, v in rename.items() if k in stations.columns})
    stations = stations.dropna(subset=["lat", "lng"])
    print(f"  ✓ {len(stations):,} Stationen mit Koordinaten")

    # Preis-CSVs
    price_base = repo_path / "prices" / str(year)
    if not price_base.exists():
        raise FileNotFoundError(f"Preisordner nicht gefunden: {price_base}")

    csvs = sorted(price_base.rglob(f"{year}-*-prices.csv"))
    if not csvs:
        csvs = sorted(price_base.rglob(f"{year}-*.csv"))
    if not csvs:
        raise FileNotFoundError(f"Keine Preis-CSVs für {year} in {price_base}")

    print(f"  💰 Aggregiere Dieselpreise aus {len(csvs)} CSVs via DuckDB...")
    csv_pattern = str(price_base / "*" / f"{year}-*-prices.csv").replace("\\", "/")

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
        print(f"  ✓ {len(prices):,} Stationen mit Preisdaten")
    except Exception as e:
        print(f"  ✗ DuckDB Fehler: {e} – Fallback auf pandas (erste 30 Tage)")
        prices = _pandas_fallback(csvs[:30])

    merged = stations.merge(prices, on="station_uuid", how="inner")
    print(f"  ✓ {len(merged):,} Stationen mit Preis + Koordinaten")
    return merged


def _pandas_fallback(csvs: list) -> pd.DataFrame:
    chunks = []
    for csv in tqdm(csvs, desc="  CSVs lesen"):
        try:
            df = pd.read_csv(csv, usecols=["station_uuid", "diesel"],
                             dtype={"diesel": float}, low_memory=True)
            chunks.append(df[df["diesel"].between(0.8, 3.5)])
        except Exception:
            continue
    combined = pd.concat(chunks, ignore_index=True)
    return (combined.groupby("station_uuid")["diesel"]
            .agg(diesel_avg="mean", n_changes="count").reset_index())


# ─────────────────────────────────────────────────────────────────────────────
# DISTANZBERECHNUNG
# ─────────────────────────────────────────────────────────────────────────────

def compute_city_distances(stations_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Berechnet Distanz jeder Tankstelle zur nächsten Großstadt (in km)."""

    print("  📐 Berechne Distanzen zu Großstädten...")

    cities_df = pd.DataFrame(GROSSSTAEDTE, columns=["city_name", "lat", "lng", "population"])
    cities_gdf = gpd.GeoDataFrame(
        cities_df,
        geometry=gpd.points_from_xy(cities_df["lng"], cities_df["lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")

    stations_gdf = gpd.GeoDataFrame(
        stations_df,
        geometry=gpd.points_from_xy(stations_df["lng"], stations_df["lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")

    city_points = cities_gdf.geometry.values
    city_names  = cities_df["city_name"].values

    nearest_city = []
    dist_km      = []
    zone_label   = []
    zone_color   = []

    print(f"  → {len(stations_gdf):,} Stationen werden berechnet...")
    for geom in stations_gdf.geometry:
        dists = [geom.distance(c) / 1000 for c in city_points]
        min_d = min(dists)
        idx   = int(np.argmin(dists))

        nearest_city.append(city_names[idx])
        dist_km.append(round(min_d, 2))

        # Zone zuweisen
        assigned = False
        for lo, hi, label, color, _ in URBAN_TYPES:
            if lo <= min_d < hi:
                zone_label.append(label)
                zone_color.append(color)
                assigned = True
                break
        if not assigned:
            zone_label.append(URBAN_TYPES[-1][2])
            zone_color.append(URBAN_TYPES[-1][3])

    stations_gdf = stations_gdf.copy()
    stations_gdf["nearest_city"] = nearest_city
    stations_gdf["dist_city_km"] = dist_km
    stations_gdf["zone_label"]   = zone_label
    stations_gdf["zone_color"]   = zone_color

    # Verteilung ausgeben
    print(f"\n  Zonen-Verteilung:")
    for _, _, label, _, _ in URBAN_TYPES:
        n   = (stations_gdf["zone_label"] == label).sum()
        avg = stations_gdf[stations_gdf["zone_label"] == label]["diesel_avg"].mean()
        print(f"    {label:<25} {n:>5} Stationen  Ø {avg:.3f} €/L")

    return stations_gdf.to_crs("EPSG:4326")


# ─────────────────────────────────────────────────────────────────────────────
# STATISTIK + BOXPLOT
# ─────────────────────────────────────────────────────────────────────────────

def run_statistics(stations: gpd.GeoDataFrame, year: int) -> dict:
    print(f"\n{'═'*70}")
    print(f"  H2: Urban vs. Rural – Dieselpreis {year}")
    print(f"{'═'*70}")
    print(f"  {'Zone':<28} {'N':>7} {'Ø Preis':>10} {'Median':>10} {'Std':>8}")
    print(f"{'─'*70}")

    zone_groups = {}
    for _, _, label, _, _ in URBAN_TYPES:
        group = stations[stations["zone_label"] == label]["diesel_avg"].dropna()
        zone_groups[label] = group
        if len(group) > 0:
            print(f"  {label:<28} {len(group):>7,} "
                  f"{group.mean():>9.3f}€ "
                  f"{group.median():>9.3f}€ "
                  f"{group.std():>7.3f}")

    # ANOVA
    groups = [g for g in zone_groups.values() if len(g) > 1]
    if len(groups) >= 2:
        f_stat, p_val = stats.f_oneway(*groups)
        print(f"\n  ANOVA: F={f_stat:.3f}, p={p_val:.5f}", end="  ")
        print("→ ✅ Signifikant (p < 0.05)" if p_val < 0.05 else "→ ❌ Nicht signifikant")

    # Direktvergleich Kernstadt vs. Rural
    kern  = zone_groups.get("Kernstadt (≤ 5km)", pd.Series())
    rural = zone_groups.get("Rural (> 50km)", pd.Series())
    if len(kern) > 1 and len(rural) > 1:
        t_stat, p_val = stats.ttest_ind(kern, rural)
        diff = kern.mean() - rural.mean()
        print(f"\n  Direktvergleich Kernstadt vs. Rural:")
        print(f"    Kernstadt: {kern.mean():.3f} €/L")
        print(f"    Rural:     {rural.mean():.3f} €/L")
        print(f"    Differenz: {diff:+.4f} €/L = {diff*100:+.2f} Cent/L  "
              f"(t={t_stat:.2f}, p={p_val:.5f})")
        if p_val < 0.05:
            print(f"    → ✅ Kernstadt-Tankstellen sind signifikant "
                  f"{'TEURER' if diff > 0 else 'GÜNSTIGER'} als Rural")

    print(f"{'═'*70}\n")
    return zone_groups


def save_boxplot(zone_groups: dict, year: int):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    labels = [z[2] for z in URBAN_TYPES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    colors = [z[3] for z in URBAN_TYPES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    data   = [zone_groups[l].tolist() for l in labels]
    means  = [np.mean(d) for d in data]

    # Boxplot (showfliers=False bei vielen Punkten für Lesbarkeit)
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
    ax1.set_title(f"H2: Dieselpreis nach Stadtdistanz – {year}",
                  fontweight="bold", pad=12)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_facecolor("#f9f9f9")
    ax1.legend(fontsize=10)

    # Balkendiagramm
    bars = ax2.bar(labels, means, color=colors, alpha=0.85,
                   edgecolor="white", width=0.5)
    for bar, mean in zip(bars, means):
        ax2.text(bar.get_x() + bar.get_width()/2, mean + 0.0005,
                 f"{mean:.3f}€", ha="center", va="bottom",
                 fontsize=11, fontweight="bold")

    # Differenz Kernstadt vs Rural einzeichnen
    if len(means) >= 2:
        diff = means[0] - means[-1]
        ax2.annotate(
            f"Δ Kernstadt–Rural: {diff:+.4f} €/L",
            xy=(1.5, max(means)), xytext=(1.5, max(means) + 0.004),
            ha="center", fontsize=9, color="#c0392b", fontweight="bold",
        )

    all_m = [m for m in means if m > 0]
    if all_m:
        ax2.set_ylim(min(all_m) - 0.015, max(all_m) + 0.015)
    ax2.set_ylabel("Ø Diesel (€/L)", fontsize=12)
    ax2.set_title(f"Ø Dieselpreis je Zone – {year}", fontweight="bold", pad=12)
    ax2.set_facecolor("#f9f9f9")
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_xticklabels(labels, fontsize=9)

    plt.suptitle(
        "H2: Sind Stadtankstellen teurer? (Tankerkönig + Destatis)",
        fontsize=13, y=1.01,
    )
    plt.tight_layout()
    out = OUTPUT_DIR / f"h2_boxplot_{year}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ Boxplot: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# INTERAKTIVE KARTE
# ─────────────────────────────────────────────────────────────────────────────

def build_map(stations: gpd.GeoDataFrame, year: int) -> folium.Map:

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
        caption=f"Diesel (€/L) – {year}",
    )
    price_colormap.add_to(m)

    # ── LAYER 0: TANKSTELLEN (Preis-Füllung) – immer sichtbar ─────────────────
    points_layer = folium.FeatureGroup(name="⛽ Tankstellen (Preis)", show=True)
    points_layer.add_to(m)

    # ── LAYER 1: ZONE (nur Rand) – einzeln zu-/abschaltbar ────────────────────
    for _, _, label, color, enabled in URBAN_TYPES:
        subset = stations[stations["zone_label"] == label]
        if subset.empty:
            continue

        emoji = "🔴" if "Kern" in label else "🟠" if "Sub" in label else "🟡" if "Semi" in label else "🟢"
        layer = folium.FeatureGroup(
            name=f"{emoji} {label} – Rand (N={len(subset):,})",
            show=enabled,
        )

        # Max 3000 Punkte pro Zone für Browser-Performance
        sample = subset if len(subset) <= 3000 else subset.sample(3000, random_state=42)

        for _, row in sample.iterrows():
            diesel = row.get("diesel_avg")
            name   = row.get("name", row.get("brand", "Tankstelle"))
            brand  = row.get("brand", "")
            city   = row.get("nearest_city", "–")
            dist   = row.get("dist_city_km", 0)
            loc    = [row.geometry.y, row.geometry.x]
            fill   = price_colormap(diesel) if pd.notna(diesel) else color

            tooltip = (
                f"<b>{name}</b>"
                + (f" <span style='color:#888'>({brand})</span>"
                   if brand and brand != name else "")
                + f"<br>Ø Diesel: <b style='color:#c0392b'>{diesel:.3f} €/L</b><br>"
                + f"Nächste Großstadt: {city} ({dist:.1f} km)<br>"
                + f"Zone: <b>{label}</b>"
            )

            # Punkt (Preis) – liegt im immer sichtbaren points_layer
            folium.CircleMarker(
                location=loc,
                radius=4,
                weight=0,
                fill=True,
                fill_color=fill,
                fill_opacity=0.85,
                tooltip=folium.Tooltip(tooltip, sticky=True),
            ).add_to(points_layer)

            # Rand (Zonen-Farbe) – nur in diesem Zone-Layer
            folium.CircleMarker(
                location=loc,
                radius=4,
                color=color,
                weight=1,
                fill=False,
                tooltip=folium.Tooltip(tooltip, sticky=True),
            ).add_to(layer)

        layer.add_to(m)

    # ── LAYER 2: Großstädte als skalierte Marker ──────────────────────────────
    city_layer = folium.FeatureGroup(name="🏙 Großstädte (> 100k EW)", show=True)
    for city_name, lat, lng, pop in GROSSSTAEDTE:
        folium.CircleMarker(
            location=[lat, lng],
            radius=max(5, min(18, pop / 150000)),
            color="#1a1a2e",
            fill=True,
            fill_color="#1a1a2e",
            fill_opacity=0.6,
            weight=2,
            tooltip=folium.Tooltip(
                f"<b>{city_name}</b><br>{pop/1000:.0f}k Einwohner",
                sticky=False,
            ),
        ).add_to(city_layer)
    city_layer.add_to(m)

    # ── LEGENDE ───────────────────────────────────────────────────────────────
    zone_rows = ""
    for _, _, label, color, _ in URBAN_TYPES:
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
        border-left:4px solid #e74c3c;max-width:300px;">
        <b>🏙 H2: Urban vs. Rural – {year}</b><br>
        <span style="color:#666;font-size:11px">
            Füllung = Preis (immer sichtbar) · Rand = Zone (rechts einzeln zuschaltbar)
        </span>
        <hr style="margin:7px 0;border:none;border-top:1px solid #eee">
        {zone_rows}
        <div style="margin:4px 0">
            <span style="display:inline-block;width:12px;height:12px;
            background:#1a1a2e;border-radius:50%;
            margin-right:7px;vertical-align:middle"></span>
            <b>Großstadt</b> (Punkt skaliert nach EW)
        </div>
        <hr style="margin:7px 0;border:none;border-top:1px solid #eee">
        <span style="color:#999;font-size:11px">
            Tankerkönig CC BY 4.0 · Stichprobe max. 3.000/Zone
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
        description="H2: Dieselpreise Urban vs. Rural (Einzelstationsebene)"
    )
    parser.add_argument("--repo-path", required=True,
                        help="Pfad zum tankerkoenig-data Repo")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Cache löschen und neu berechnen")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.clear_cache and STATIONS_CACHE.exists():
        STATIONS_CACHE.unlink()
        print("  🗑 Cache gelöscht")

    print(f"\n{'═'*60}")
    print(f"  H2 Urban vs. Rural – Diesel {args.year}")
    print(f"{'═'*60}\n")

    # Stationsdaten laden oder Cache nutzen
    print("[1/4] Stationsdaten + Preise laden...")
    if STATIONS_CACHE.exists() and not args.clear_cache:
        print("  ✓ Lade aus Cache...")
        stations = gpd.read_file(STATIONS_CACHE)
    else:
        stations_df = load_station_prices(Path(args.repo_path), args.year)

        print("\n[2/4] Distanzen zu Großstädten berechnen...")
        stations = compute_city_distances(stations_df)
        stations.to_file(STATIONS_CACHE, driver="GeoJSON")
        print(f"  ✓ Gecacht: {STATIONS_CACHE}")

    print(f"\n[3/4] Statistik...")
    zone_groups = run_statistics(stations, args.year)
    save_boxplot(zone_groups, args.year)

    print(f"[4/4] Karte erstellen...")
    m = build_map(stations, args.year)

    out = OUTPUT_DIR / f"h2_urban_rural_map_{args.year}.html"
    m.save(str(out))
    print(f"  ✅ Karte: {out}")

    if not args.no_browser:
        webbrowser.open(out.resolve().as_uri())
        print("  🌐 Öffne im Browser...")


if __name__ == "__main__":
    main()