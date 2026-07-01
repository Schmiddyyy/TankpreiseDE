"""
H3 – Grenznähe Analyse (Einzelstations-Ebene)
===============================================
Untersucht ob Tankstellen nahe der deutschen Staatsgrenze
günstigere Dieselpreise haben als weiter im Inland.

Verwendung:
    pip install geopandas folium branca requests matplotlib scipy duckdb tqdm
    python h3_border_analysis.py --repo-path ./tankerkoenig-data --year 2024

    # Cache nutzen (nach erstem Durchlauf):
    python h3_border_analysis.py --repo-path ./tankerkoenig-data --year 2024
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
import requests
from scipy import stats
from tqdm import tqdm

OUTPUT_DIR     = Path("./tankstellen_data")
BORDER_CACHE   = OUTPUT_DIR / "countries.geojson"
STATIONS_CACHE = OUTPUT_DIR / "h3_stations_with_distances.geojson"

BORDER_URL = (
    "https://raw.githubusercontent.com/datasets/geo-countries/"
    "master/data/countries.geojson"
)

ZONES = [
    (0,   20,  "0–20 km",   "#c0392b", True),
    (20,  50,  "20–50 km",  "#e67e22", True),
    (50,  100, "50–100 km", "#f1c40f", True),
    (100, 999, "> 100 km",  "#27ae60", True),
]


# ─────────────────────────────────────────────────────────────────────────────
# STAATSGRENZE LADEN
# ─────────────────────────────────────────────────────────────────────────────

def load_germany_border() -> gpd.GeoDataFrame:
    if not BORDER_CACHE.exists():
        print("  ↓ Lade Ländergrenzen-GeoJSON...")
        r = requests.get(BORDER_URL, timeout=60)
        r.raise_for_status()
        BORDER_CACHE.write_bytes(r.content)

    gdf = gpd.read_file(BORDER_CACHE)

    de = gpd.GeoDataFrame()
    for col in ["ADMIN", "name", "NAME", "sovereignt", "SOVEREIGNT"]:
        if col in gdf.columns:
            mask = gdf[col].str.contains("Germany|Deutschland", case=False, na=False)
            if mask.any():
                de = gdf[mask].copy()
                print(f"  ✓ Deutschland gefunden via '{col}'")
                break

    if de.empty:
        for col in ["ISO3166-1-Alpha-2", "iso_a2", "ISO_A2"]:
            if col in gdf.columns:
                de = gdf[gdf[col] == "DE"].copy()
                if not de.empty:
                    print(f"  ✓ Deutschland gefunden via ISO '{col}'")
                    break

    if de.empty:
        raise ValueError("Deutschland nicht gefunden! Spalten: " + str(list(gdf.columns)))

    return de.to_crs("EPSG:25832")


# ─────────────────────────────────────────────────────────────────────────────
# STATIONSDATEN + PREISE LADEN
# ─────────────────────────────────────────────────────────────────────────────

def load_station_prices(repo_path: Path, year: int) -> pd.DataFrame:
    """Lädt Einzelstationsdaten + Jahres-Ø-Dieselpreis via DuckDB."""

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

def compute_border_distances(stations_df: pd.DataFrame,
                             border_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Berechnet Distanz jeder Tankstelle zur Staatsgrenze in km."""

    print("  📐 Berechne Distanzen zur Staatsgrenze...")

    stations_gdf = gpd.GeoDataFrame(
        stations_df,
        geometry=gpd.points_from_xy(stations_df["lng"], stations_df["lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")

    # Grenzlinie (nur Außenkante, nicht Fläche)
    border_line = border_gdf.geometry.boundary.unary_union

    print(f"  → {len(stations_gdf):,} Stationen werden berechnet...")
    stations_gdf["dist_border_km"] = (
        stations_gdf.geometry.distance(border_line) / 1000
    )

    def assign_zone(d):
        if pd.isna(d):
            return "Unbekannt", "#aaaaaa"
        for lo, hi, label, color, _ in ZONES:
            if lo <= d < hi:
                return label, color
        return ZONES[-1][2], ZONES[-1][3]

    stations_gdf[["zone_label", "zone_color"]] = pd.DataFrame(
        stations_gdf["dist_border_km"].apply(assign_zone).tolist(),
        index=stations_gdf.index,
    )

    print(f"\n  Zonen-Verteilung:")
    for _, _, label, _, _ in ZONES:
        n   = (stations_gdf["zone_label"] == label).sum()
        avg = stations_gdf[stations_gdf["zone_label"] == label]["diesel_avg"].mean()
        print(f"    {label:<15} {n:>5} Stationen  Ø {avg:.3f} €/L")

    return stations_gdf.to_crs("EPSG:4326")


# ─────────────────────────────────────────────────────────────────────────────
# STATISTIK + BOXPLOT
# ─────────────────────────────────────────────────────────────────────────────

def run_statistics(stations: gpd.GeoDataFrame, year: int) -> dict:
    print(f"\n{'═'*70}")
    print(f"  H3: Grenznähe vs. Dieselpreis – {year}")
    print(f"{'═'*70}")
    print(f"  {'Zone':<18} {'N':>7} {'Ø Preis':>10} {'Median':>10} {'Std':>8}")
    print(f"{'─'*70}")

    zone_groups = {}
    for _, _, label, _, _ in ZONES:
        group = stations[stations["zone_label"] == label]["diesel_avg"].dropna()
        zone_groups[label] = group
        if len(group) > 0:
            print(f"  {label:<18} {len(group):>7,} "
                  f"{group.mean():>9.3f}€ "
                  f"{group.median():>9.3f}€ "
                  f"{group.std():>7.3f}")

    # ANOVA
    groups = [g for g in zone_groups.values() if len(g) > 1]
    if len(groups) >= 2:
        f_stat, p_val = stats.f_oneway(*groups)
        print(f"\n  ANOVA: F={f_stat:.3f}, p={p_val:.5f}", end="  ")
        print("→ ✅ Signifikant (p < 0.05)" if p_val < 0.05 else "→ ❌ Nicht signifikant")

    # Direktvergleich nah vs. weit
    nah  = zone_groups.get("0–20 km", pd.Series())
    weit = zone_groups.get("> 100 km", pd.Series())
    if len(nah) > 1 and len(weit) > 1:
        t_stat, p_val = stats.ttest_ind(nah, weit)
        diff = nah.mean() - weit.mean()
        print(f"\n  Direktvergleich 0–20 km vs. > 100 km:")
        print(f"    0–20 km:   {nah.mean():.3f} €/L")
        print(f"    > 100 km:  {weit.mean():.3f} €/L")
        print(f"    Differenz: {diff:+.4f} €/L = {diff*100:+.2f} Cent/L  "
              f"(t={t_stat:.2f}, p={p_val:.5f})")
        if p_val < 0.05:
            print(f"    → ✅ Grenznahe Tankstellen sind signifikant "
                  f"{'GÜNSTIGER' if diff < 0 else 'TEURER'}")

    print(f"{'═'*70}\n")
    return zone_groups


def save_boxplot(zone_groups: dict, year: int):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    labels = [z[2] for z in ZONES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    colors = [z[3] for z in ZONES if z[2] in zone_groups and len(zone_groups[z[2]]) > 0]
    data   = [zone_groups[l].tolist() for l in labels]
    means  = [np.mean(d) for d in data]

    bp = ax1.boxplot(data, patch_artist=True, notch=False,
                     medianprops=dict(color="black", linewidth=2),
                     showfliers=False)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    ax1.plot(range(1, len(means)+1), means, "D", color="#2c3e50",
             zorder=5, markersize=8, label="Mittelwert")
    ax1.set_xticklabels(labels, fontsize=10)
    ax1.set_ylabel("Diesel (€/L)", fontsize=12)
    ax1.set_title(f"H3: Dieselpreis nach Grenzdistanz – {year}",
                  fontweight="bold", pad=12)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_facecolor("#f9f9f9")
    ax1.legend(fontsize=10)

    bars = ax2.bar(labels, means, color=colors, alpha=0.85,
                   edgecolor="white", width=0.5)
    for bar, mean in zip(bars, means):
        ax2.text(bar.get_x() + bar.get_width()/2, mean + 0.0005,
                 f"{mean:.3f}€", ha="center", va="bottom",
                 fontsize=11, fontweight="bold")

    # Differenz nah vs. weit
    if len(means) >= 2:
        diff = means[0] - means[-1]
        ax2.annotate(
            f"Δ Grenze–Inland: {diff:+.4f} €/L",
            xy=(1.5, max(means)), xytext=(1.5, max(means) + 0.004),
            ha="center", fontsize=9, color="#c0392b", fontweight="bold",
        )

    all_m = [m for m in means if m > 0]
    if all_m:
        ax2.set_ylim(min(all_m) - 0.015, max(all_m) + 0.015)
    ax2.set_ylabel("Ø Diesel (€/L)", fontsize=12)
    ax2.set_title(f"Ø Dieselpreis je Distanzzone – {year}",
                  fontweight="bold", pad=12)
    ax2.set_facecolor("#f9f9f9")
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_xticklabels(labels, fontsize=10)

    plt.suptitle(
        "H3: Sind Grenz-Tankstellen günstiger? (Tankerkönig + Natural Earth)",
        fontsize=13, y=1.01,
    )
    plt.tight_layout()
    out = OUTPUT_DIR / f"h3_boxplot_{year}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ Boxplot: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# INTERAKTIVE KARTE
# ─────────────────────────────────────────────────────────────────────────────

def build_map(stations: gpd.GeoDataFrame,
              border_gdf: gpd.GeoDataFrame,
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
        caption=f"Diesel (€/L) – {year}",
    )
    price_colormap.add_to(m)

    # ── STAATSGRENZE ──────────────────────────────────────────────────────────
    folium.GeoJson(
        border_gdf.to_crs("EPSG:4326").__geo_interface__,
        name="🗺 Staatsgrenze",
        style_function=lambda f: {
            "fillColor": "transparent",
            "color":     "#1a1a2e",
            "weight":    2.5,
            "fillOpacity": 0,
        },
    ).add_to(m)

    # ── LAYER 0: TANKSTELLEN (Preis-Füllung) – immer sichtbar ─────────────────
    points_layer = folium.FeatureGroup(name="⛽ Tankstellen (Preis)", show=True)
    points_layer.add_to(m)

    # ── ZONE (nur Rand) – einzeln zu-/abschaltbar ─────────────────────────────
    for _, _, label, color, enabled in ZONES:
        subset = stations[stations["zone_label"] == label]
        if subset.empty:
            continue

        emoji = "🔴" if "20" in label and "–" not in label[:3] else \
                "🟠" if "20–50" in label else \
                "🟡" if "50–100" in label else "🟢"

        layer = folium.FeatureGroup(
            name=f"{emoji} {label} – Rand (N={len(subset):,})",
            show=enabled,
        )

        # Max 3000 Punkte pro Zone
        sample = subset if len(subset) <= 3000 else subset.sample(3000, random_state=42)

        for _, row in sample.iterrows():
            diesel = row.get("diesel_avg")
            name   = row.get("name", row.get("brand", "Tankstelle"))
            brand  = row.get("brand", "")
            dist   = row.get("dist_border_km", 0)
            loc    = [row.geometry.y, row.geometry.x]
            fill   = price_colormap(diesel) if pd.notna(diesel) else color

            tooltip = (
                f"<b>{name}</b>"
                + (f" <span style='color:#888'>({brand})</span>"
                   if brand and brand != name else "")
                + f"<br>Ø Diesel: <b style='color:#c0392b'>{diesel:.3f} €/L</b><br>"
                + f"Distanz zur Grenze: {dist:.1f} km<br>"
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

            # Rand (Distanzzonen-Farbe) – nur in diesem Zone-Layer
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
        <b>🗺 H3: Grenznähe – {year}</b><br>
        <span style="color:#666;font-size:11px">
            Füllung = Preis (immer sichtbar) · Rand = Distanzzone (rechts einzeln zuschaltbar)
        </span>
        <hr style="margin:7px 0;border:none;border-top:1px solid #eee">
        {zone_rows}
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
        description="H3: Dieselpreise nach Grenznähe (Einzelstationsebene)"
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
    print(f"  H3 Grenznähe-Analyse – Diesel {args.year}")
    print(f"{'═'*60}\n")

    print("[1/5] Staatsgrenze laden...")
    border = load_germany_border()

    print("\n[2/5] Stationsdaten + Preise laden...")
    if STATIONS_CACHE.exists() and not args.clear_cache:
        print("  ✓ Lade aus Cache...")
        stations = gpd.read_file(STATIONS_CACHE)
    else:
        stations_df = load_station_prices(Path(args.repo_path), args.year)

        print("\n[3/5] Distanzen zur Staatsgrenze berechnen...")
        stations = compute_border_distances(stations_df, border)
        stations.to_file(STATIONS_CACHE, driver="GeoJSON")
        print(f"  ✓ Gecacht: {STATIONS_CACHE}")

    print(f"\n[4/5] Statistik...")
    zone_groups = run_statistics(stations, args.year)
    save_boxplot(zone_groups, args.year)

    print(f"[5/5] Karte erstellen...")
    m = build_map(stations, border, args.year)

    out = OUTPUT_DIR / f"h3_border_map_{args.year}.html"
    m.save(str(out))
    print(f"  ✅ Karte: {out}")

    if not args.no_browser:
        webbrowser.open(out.resolve().as_uri())
        print("  🌐 Öffne im Browser...")


if __name__ == "__main__":
    main()