"""
H4 – Ost/West, Marken & Wettbewerbsdichte (Zeitverlauf 2014–2026)
====================================================================
Drei ergänzende Analysen auf Basis derselben Tankerkönig-Stationsdaten,
ausgewertet als MONATLICHER Zeitverlauf über den gesamten verfügbaren
Datenzeitraum (Juni 2014 – Juni 2026):

  A) OST/WEST   – Klassifikation je Tankstelle via Bundesland-Polygon
                  (neue Bundesländer + Berlin-Sonderfall vs. alte
                  Bundesländer). "Mauer im Kopf"-These: persistente
                  Preisunterschiede 35+ Jahre nach der Wiedervereinigung?
  B) MARKE      – Klassifikation nach Marken-Kategorie (Premium /
                  Discounter / Supermarkt / Sonstige). Räumliche Frage:
                  sind Premium-Marken eher städtisch, Discounter eher
                  peripher? (Kreuztabelle mit Urban/Rural-Zone)
  C) WETTBEWERB – Anzahl Konkurrenten im 1/2/5-km-Radius (KD-Tree
                  Spatial Query). Klassische Mikroökonomie-These:
                  mehr Wettbewerb = niedrigerer Preis?

Methode:
    1. Stationsmetadaten einmalig laden (stations.csv)
    2. Statische Klassifikation je Station (Ost/West, Marke, Urban/Rural,
       Wettbewerbsdichte) – wird gecacht, da zeitunabhängig
    3. Für jeden Monat im Zeitraum: Preise via DuckDB direkt aus den
       Tankerkönig-CSVs aggregieren (kein Laden des Gesamtdatensatzes
       in den Speicher!) und mit der statischen Klassifikation verknüpfen
    4. Lange Zeitreihen-Tabelle (Jahr, Monat, Dimension, Gruppe, Ø-Preise)
       + stationsweite Gesamtdurchschnitte exportieren
    5. Umfassende Konsolen-Statistik (t-Tests, ANOVA, Korrelation,
       Kreuztabellen, Jahresverlaufs-Tabellen)

WICHTIG: Dieses Script liefert nur DATEN (CSV-Exports + Statistik-Konsole),
keine Karte/Plots – siehe h1/h2/h3 für die Kartenvisualisierung mit
demselben ZONES-Stil.

Verwendung:
    pip install geopandas pandas duckdb requests scipy tqdm shapely
    python h4_ost_west_marke_wettbewerb.py --repo-path ./tankerkoenig-data

    # Anderer Zeitraum:
    python h4_ost_west_marke_wettbewerb.py --repo-path ./tankerkoenig-data --start 2018-01 --end 2024-12

    # Nur Statistik aus Cache neu drucken (kein Neuladen der Preise):
    python h4_ost_west_marke_wettbewerb.py --repo-path ./tankerkoenig-data --use-cache
"""

import argparse
from collections import defaultdict
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy import stats
from scipy.spatial import cKDTree
from tqdm import tqdm

OUTPUT_DIR = Path("./tankstellen_data")
STATIONS_CACHE   = OUTPUT_DIR / "h4_stations_classified.geojson"
TIMESERIES_CACHE = OUTPUT_DIR / "h4_zeitverlauf_monatlich.csv"
STATIONS_CSV_OUT = OUTPUT_DIR / "h4_stationen_mit_klassifikation.csv"

BUNDESLAND_GEOJSON_URL = (
    "https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/"
    "main/2_bundeslaender/4_niedrig.geo.json"
)
BUNDESLAND_CACHE = OUTPUT_DIR / "bundeslaender.geojson"

# ISO3166-2 Codes der "neuen Bundesländer" (ehem. DDR-Gebiet)
OST_BUNDESLAENDER = {"DE-BB", "DE-MV", "DE-SN", "DE-ST", "DE-TH"}
BERLIN_CODE = "DE-BE"  # Sonderfall: historisch geteilt, separat ausgewiesen

# Markenkategorien (Substring-Match auf Großschreibung, einfach erweiterbar)
BRAND_CATEGORIES = {
    "ARAL": "Premium", "SHELL": "Premium", "ESSO": "Premium",
    "TOTAL": "Premium", "AVIA": "Premium",
    "JET": "Discounter", "STAR": "Discounter", "HEM": "Discounter",
    "AGIP": "Discounter", "ENI": "Discounter", "OMV": "Discounter",
    "EDEKA": "Supermarkt", "REWE": "Supermarkt", "GLOBUS": "Supermarkt",
    "REAL": "Supermarkt", "MARKANT": "Supermarkt",
}
DEFAULT_BRAND_CATEGORY = "Sonstige/Frei"

# Großstädte (> 100k EW) für Urban/Rural-Klassifikation (wie H2)
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
    (0,    5,   "Kernstadt (≤ 5km)"),
    (5,    20,  "Suburban (5–20km)"),
    (20,   50,  "Semi-Rural (20–50km)"),
    (50,  999,  "Rural (> 50km)"),
]

# Wettbewerbsdichte: Radien in km + Bucket-Grenzen (basierend auf 2-km-Radius)
COMPETITION_RADII_KM = [1, 2, 5]
COMPETITION_BINS = [
    (0,  1,   "Isoliert (0 Konkurrenten <2km)"),
    (1,  4,   "Wenig Wettbewerb (1–3 <2km)"),
    (4,  9,   "Mittel (4–8 <2km)"),
    (9,  9999, "Viel Wettbewerb (≥9 <2km)"),
]

DEFAULT_START = "2014-06"
DEFAULT_END   = "2026-06"


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 1: STATIONSMETADATEN (einmalig, zeitunabhängig)
# ─────────────────────────────────────────────────────────────────────────────

def load_station_master(repo_path: Path) -> pd.DataFrame:
    """Lädt die aktuellste Stationsliste (Name, Marke, Koordinaten)."""
    candidates = [
        repo_path / "stations" / "stations.csv",
        repo_path / "stations.csv",
        *list(repo_path.rglob("stations.csv"))[:3],
    ]
    stations_file = next((p for p in candidates if p.exists()), None)
    if stations_file is None:
        raise FileNotFoundError(
            f"stations.csv nicht gefunden in {repo_path}\n"
            "Prüfe die Ordnerstruktur des Repos."
        )

    print(f"  📍 Lade Stationsmetadaten aus {stations_file.name}...")
    stations = pd.read_csv(stations_file, low_memory=True)

    rename = {
        "uuid": "station_uuid", "id": "station_uuid",
        "latitude": "lat", "longitude": "lng",
    }
    stations = stations.rename(columns={k: v for k, v in rename.items() if k in stations.columns})
    stations = stations.dropna(subset=["lat", "lng", "station_uuid"]).drop_duplicates("station_uuid")
    print(f"  ✓ {len(stations):,} Stationen mit Koordinaten")
    return stations


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 2: STATISCHE KLASSIFIKATION (Ost/West, Marke, Urban/Rural, Wettbewerb)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_bundeslaender() -> gpd.GeoDataFrame:
    """Lädt Bundesländer-Polygone (GeoJSON, isellsoap/deutschlandGeoJSON)."""
    if BUNDESLAND_CACHE.exists():
        print("  ✓ Bundesländer-Polygone aus Cache geladen")
        return gpd.read_file(BUNDESLAND_CACHE)

    print("  ↓ Lade Bundesländer-Polygone (GitHub: isellsoap/deutschlandGeoJSON)...")
    r = requests.get(BUNDESLAND_GEOJSON_URL, timeout=60)
    r.raise_for_status()
    BUNDESLAND_CACHE.write_text(r.text, encoding="utf-8")
    gdf = gpd.read_file(BUNDESLAND_CACHE)
    print(f"  ✓ {len(gdf)} Bundesländer geladen")
    return gdf


def classify_ost_west(stations_gdf: gpd.GeoDataFrame,
                      bundeslaender: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ordnet jede Station per Punkt-in-Polygon ihrem Bundesland zu."""
    print("  🗺  Klassifiziere Ost/West via Bundesland-Polygon...")

    bl = (bundeslaender[["id", "name", "geometry"]]
          .rename(columns={"id": "bundesland_code", "name": "bundesland"})
          .to_crs("EPSG:4326"))

    joined = gpd.sjoin(stations_gdf, bl, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    missing = joined["bundesland_code"].isna()
    if missing.any():
        print(f"    ⚠ {missing.sum()} Stationen ohne direkten Treffer "
              "(Grenzlage) – nutze nächstgelegenes Bundesland")
        sub = stations_gdf.loc[missing].drop(columns=["index_right"], errors="ignore").to_crs("EPSG:25832")
        bl_m = bl.to_crs("EPSG:25832")
        nearest = gpd.sjoin_nearest(sub, bl_m, how="left")
        nearest = nearest[~nearest.index.duplicated(keep="first")]
        joined.loc[missing, "bundesland_code"] = nearest["bundesland_code"].values
        joined.loc[missing, "bundesland"]      = nearest["bundesland"].values

    def region(code):
        if code in OST_BUNDESLAENDER:
            return "Ost"
        if code == BERLIN_CODE:
            return "Berlin (Sonderfall)"
        return "West"

    joined["region"] = joined["bundesland_code"].apply(region)
    return joined.drop(columns=["index_right"], errors="ignore")


def categorize_brand(raw_brand) -> str:
    if raw_brand is None or (isinstance(raw_brand, float) and pd.isna(raw_brand)):
        return DEFAULT_BRAND_CATEGORY
    b = str(raw_brand).upper()
    for key, category in BRAND_CATEGORIES.items():
        if key in b:
            return category
    return DEFAULT_BRAND_CATEGORY


def classify_brand(stations_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print("  🏷  Klassifiziere Markenkategorie...")
    brand_col = (stations_gdf["brand"] if "brand" in stations_gdf.columns
                else pd.Series([""] * len(stations_gdf), index=stations_gdf.index))
    stations_gdf["brand_category"] = brand_col.apply(categorize_brand)
    unmatched = sorted(
        stations_gdf.loc[stations_gdf["brand_category"] == DEFAULT_BRAND_CATEGORY, "brand"]
        .dropna().astype(str).str.upper().unique()
    )[:15]
    if unmatched:
        print(f"    ℹ Beispiele unter '{DEFAULT_BRAND_CATEGORY}' "
              f"(BRAND_CATEGORIES ggf. erweitern): {', '.join(unmatched)}")
    return stations_gdf


def classify_urban_rural(stations_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Distanz zur nächsten Großstadt via KD-Tree (siehe H2)."""
    print("  🏙  Berechne Distanz zur nächsten Großstadt...")

    cities_df = pd.DataFrame(GROSSSTAEDTE, columns=["city_name", "lat", "lng", "population"])
    cities_gdf = gpd.GeoDataFrame(
        cities_df, geometry=gpd.points_from_xy(cities_df["lng"], cities_df["lat"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:25832")

    stations_m = stations_gdf.to_crs("EPSG:25832")
    city_coords    = np.column_stack([cities_gdf.geometry.x, cities_gdf.geometry.y])
    station_coords = np.column_stack([stations_m.geometry.x, stations_m.geometry.y])

    tree = cKDTree(city_coords)
    dist_m, idx = tree.query(station_coords, k=1)

    stations_gdf["nearest_city"] = cities_df["city_name"].values[idx]
    stations_gdf["dist_city_km"] = dist_m / 1000

    def zone(d):
        for lo, hi, label in URBAN_TYPES:
            if lo <= d < hi:
                return label
        return URBAN_TYPES[-1][2]

    stations_gdf["urban_rural_zone"] = stations_gdf["dist_city_km"].apply(zone)
    return stations_gdf


def compute_competition_density(stations_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Anzahl Konkurrenten im 1/2/5-km-Radius via KD-Tree (radius query)."""
    print(f"  🏪 Berechne Wettbewerbsdichte (Radien: {COMPETITION_RADII_KM} km)...")

    stations_m = stations_gdf.to_crs("EPSG:25832")
    coords = np.column_stack([stations_m.geometry.x, stations_m.geometry.y])
    tree = cKDTree(coords)

    for km in COMPETITION_RADII_KM:
        neighbor_lists = tree.query_ball_point(coords, r=km * 1000)
        counts = np.array([len(n) - 1 for n in neighbor_lists])  # -1: sich selbst ausschließen
        stations_gdf[f"n_competitors_{km}km"] = counts

    ref_col = f"n_competitors_{COMPETITION_RADII_KM[min(1, len(COMPETITION_RADII_KM) - 1)]}km"

    def bucket(n):
        for lo, hi, label in COMPETITION_BINS:
            if lo <= n < hi:
                return label
        return COMPETITION_BINS[-1][2]

    stations_gdf["competition_bucket"] = stations_gdf[ref_col].apply(bucket)
    return stations_gdf


def build_static_classification(repo_path: Path, use_cache: bool) -> gpd.GeoDataFrame:
    if use_cache and STATIONS_CACHE.exists():
        print("  ✓ Statische Klassifikation aus Cache geladen")
        return gpd.read_file(STATIONS_CACHE)

    stations_df = load_station_master(repo_path)
    stations_gdf = gpd.GeoDataFrame(
        stations_df,
        geometry=gpd.points_from_xy(stations_df["lng"], stations_df["lat"]),
        crs="EPSG:4326",
    )

    bundeslaender = fetch_bundeslaender()
    stations_gdf = classify_ost_west(stations_gdf, bundeslaender)
    stations_gdf = classify_brand(stations_gdf)
    stations_gdf = classify_urban_rural(stations_gdf)
    stations_gdf = compute_competition_density(stations_gdf)

    stations_gdf.to_file(STATIONS_CACHE, driver="GeoJSON")
    print(f"  ✓ Klassifikation gecacht: {STATIONS_CACHE}")
    return stations_gdf


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 3: MONATLICHE PREISE (DuckDB, ein Monat = ein schlanker Query)
# ─────────────────────────────────────────────────────────────────────────────

def month_range(start: str, end: str) -> list:
    """['2014-06', ...] -> [(2014, 6), (2014, 7), ..., (2026, 6)]"""
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    months = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def load_monthly_prices(repo_path: Path, year: int, month: int) -> pd.DataFrame:
    """Aggregiert Ø Diesel/E5/E10 + Anzahl Preisänderungen je Station für einen Monat."""
    price_dir = repo_path / "prices" / str(year)
    if not price_dir.exists():
        return pd.DataFrame()

    pattern = f"{year}-{month:02d}-*-prices.csv"
    if next(price_dir.rglob(pattern), None) is None:
        return pd.DataFrame()

    glob_path = str(price_dir / "**" / pattern).replace("\\", "/")
    con = duckdb.connect()
    try:
        df = con.execute(f"""
            SELECT
                station_uuid,
                AVG(CASE WHEN CAST(diesel AS DOUBLE) BETWEEN 0.8 AND 3.5
                         THEN CAST(diesel AS DOUBLE) END) AS diesel_avg,
                AVG(CASE WHEN CAST(e5 AS DOUBLE) BETWEEN 0.8 AND 3.5
                         THEN CAST(e5 AS DOUBLE) END) AS e5_avg,
                AVG(CASE WHEN CAST(e10 AS DOUBLE) BETWEEN 0.8 AND 3.5
                         THEN CAST(e10 AS DOUBLE) END) AS e10_avg,
                COUNT(*) AS n_changes
            FROM read_csv_auto('{glob_path}', ignore_errors=true)
            GROUP BY station_uuid
        """).df()
        return df
    except Exception as e:
        print(f"    ✗ DuckDB Fehler {year}-{month:02d}: {e}")
        return pd.DataFrame()
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 4: ZEITREIHE AUFBAUEN (3 Dimensionen je Monat + Gesamtperiode/Station)
# ─────────────────────────────────────────────────────────────────────────────

DIMENSIONS = [
    ("region",             "Ost/West"),
    ("brand_category",     "Marke"),
    ("urban_rural_zone",   "Urban/Rural"),
    ("competition_bucket", "Wettbewerb"),
]


def build_timeseries(repo_path: Path, stations: pd.DataFrame,
                     start: str, end: str) -> tuple:
    """
    Liefert:
      - timeseries_df: lange Tabelle (Jahr, Monat, Dimension, Gruppe, Ø-Preise, N)
      - station_overall: Series mit gewichtetem Gesamt-Ø-Dieselpreis je Station
                          über den vollen Zeitraum (für Wettbewerbs-Korrelation)
    """
    months = month_range(start, end)
    print(f"\n  📅 Verarbeite {len(months)} Monate ({start} – {end})...")

    static_cols = ["station_uuid"] + [c for c, _ in DIMENSIONS]
    static = stations[static_cols].drop_duplicates("station_uuid")

    rows = []
    skipped = []
    diesel_sum = defaultdict(float)
    diesel_n   = defaultdict(int)

    for year, month in tqdm(months, desc="  Monate"):
        monthly = load_monthly_prices(repo_path, year, month)
        if monthly.empty:
            skipped.append(f"{year}-{month:02d}")
            continue

        for uuid, avg, n in zip(monthly["station_uuid"], monthly["diesel_avg"], monthly["n_changes"]):
            if pd.notna(avg) and n:
                diesel_sum[uuid] += avg * n
                diesel_n[uuid]   += n

        merged = monthly.merge(static, on="station_uuid", how="inner")
        if merged.empty:
            skipped.append(f"{year}-{month:02d} (kein Match mit Stationsliste)")
            continue

        ym = f"{year}-{month:02d}"
        for dim_col, dim_name in DIMENSIONS:
            grp = (merged.groupby(dim_col)
                   .agg(n_stations=("station_uuid", "count"),
                        diesel_avg=("diesel_avg", "mean"),
                        e5_avg=("e5_avg", "mean"),
                        e10_avg=("e10_avg", "mean"))
                   .reset_index()
                   .rename(columns={dim_col: "group"}))
            grp.insert(0, "year", year)
            grp.insert(1, "month", month)
            grp.insert(2, "year_month", ym)
            grp.insert(3, "dimension", dim_name)
            rows.append(grp)

    if skipped:
        print(f"  ⚠ {len(skipped)} von {len(months)} Monaten ohne Preisdaten "
              f"übersprungen (z.B. {skipped[0]})")

    timeseries_df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    station_overall = pd.Series(
        {uuid: diesel_sum[uuid] / diesel_n[uuid] for uuid in diesel_n if diesel_n[uuid] > 0},
        name="diesel_avg_gesamtzeitraum",
    )
    return timeseries_df, station_overall


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 5: STATISTIK
# ─────────────────────────────────────────────────────────────────────────────

def print_station_overview(stations: pd.DataFrame):
    print(f"\n{'═'*72}")
    print("  STATIONSÜBERSICHT (statische Klassifikation, aktueller Snapshot)")
    print(f"{'═'*72}")
    print(f"  Gesamt: {len(stations):,} Stationen\n")

    for dim_col, dim_name in DIMENSIONS:
        print(f"  ── {dim_name} ──")
        counts = stations[dim_col].value_counts()
        for label, n in counts.items():
            print(f"    {label:<35} {n:>6,}  ({n/len(stations)*100:5.1f}%)")
        print()


def print_ost_west_analysis(timeseries_df: pd.DataFrame):
    print(f"{'═'*72}")
    print("  A) OST/WEST – 'Mauer im Kopf'-Test (Diesel, Zeitverlauf)")
    print(f"{'═'*72}")

    df = timeseries_df[timeseries_df["dimension"] == "Ost/West"]
    if df.empty:
        print("  (keine Daten)")
        return

    overall = df.groupby("group")["diesel_avg"].mean().sort_values(ascending=False)
    print("  Ø Diesel über den GESAMTEN Zeitraum (Mittel der Monatsmittel):")
    for label, val in overall.items():
        print(f"    {label:<22} {val:.3f} €/L")

    if "Ost" in overall.index and "West" in overall.index:
        diff = overall["Ost"] - overall["West"]
        print(f"\n  Differenz Ost−West (Gesamtperiode): {diff:+.4f} €/L "
              f"= {diff*100:+.2f} Cent/L")

    print(f"\n  {'Jahr':<6} {'West':>8} {'Ost':>8} {'Berlin':>8} "
          f"{'Diff Ost-West':>15} {'t-Test (p)':>12}")
    print(f"  {'─'*70}")
    for year in sorted(df["year"].unique()):
        yr = df[df["year"] == year]
        piv = yr.groupby("group")["diesel_avg"]
        west = yr[yr["group"] == "West"]["diesel_avg"]
        ost  = yr[yr["group"] == "Ost"]["diesel_avg"]
        berlin_vals = yr[yr["group"] == "Berlin (Sonderfall)"]["diesel_avg"]
        w_mean = west.mean() if len(west) else float("nan")
        o_mean = ost.mean() if len(ost) else float("nan")
        b_mean = berlin_vals.mean() if len(berlin_vals) else float("nan")
        diff = o_mean - w_mean
        p_str = "–"
        if len(west) > 1 and len(ost) > 1:
            _, p = stats.ttest_ind(ost, west, equal_var=False)
            p_str = f"{p:.4f}" + (" ✅" if p < 0.05 else "")
        print(f"  {year:<6} {w_mean:>8.3f} {o_mean:>8.3f} {b_mean:>8.3f} "
              f"{diff:>+14.4f} {p_str:>12}")

    print("\n  Hinweis: t-Test je Jahr auf Basis der Monatsmittelwerte (n≈12/Jahr),")
    print("  nicht auf Einzelpreisänderungen – konservativer, aber transparenter Test.")
    print(f"{'═'*72}\n")


def print_brand_analysis(stations: pd.DataFrame, timeseries_df: pd.DataFrame):
    print(f"{'═'*72}")
    print("  B) MARKE – Verteilung Stadt/Land + Preisniveau (Zeitverlauf)")
    print(f"{'═'*72}")

    print("  Verteilung je Markenkategorie über Urban/Rural-Zonen (Spaltenanteile %):")
    cross = pd.crosstab(stations["brand_category"], stations["urban_rural_zone"], normalize="index") * 100
    zone_order = [z[2] for z in URBAN_TYPES if z[2] in cross.columns]
    cross = cross[zone_order]
    print(f"  {'':<16}" + "".join(f"{z[:18]:>20}" for z in zone_order))
    for brand_cat, row in cross.iterrows():
        print(f"  {brand_cat:<16}" + "".join(f"{v:>19.1f}%" for v in row.values))

    df = timeseries_df[timeseries_df["dimension"] == "Marke"]
    if not df.empty:
        print("\n  Ø Diesel über den GESAMTEN Zeitraum je Markenkategorie:")
        overall = df.groupby("group")["diesel_avg"].mean().sort_values(ascending=False)
        for label, val in overall.items():
            print(f"    {label:<18} {val:.3f} €/L")

        groups = [g["diesel_avg"].dropna().values for _, g in df.groupby("group") if len(g) > 1]
        if len(groups) >= 2:
            f_stat, p_val = stats.f_oneway(*groups)
            print(f"\n  ANOVA über Markenkategorien (Monatsmittel): F={f_stat:.3f}, p={p_val:.5f}", end="  ")
            print("→ ✅ Signifikant" if p_val < 0.05 else "→ ❌ Nicht signifikant")
    print(f"{'═'*72}\n")


def print_competition_analysis(stations: pd.DataFrame, station_overall: pd.Series,
                               timeseries_df: pd.DataFrame):
    print(f"{'═'*72}")
    print("  C) WETTBEWERBSDICHTE – Korrelation mit Preisniveau")
    print(f"{'═'*72}")

    merged = stations.set_index("station_uuid").join(
        station_overall, how="inner"
    )
    print(f"  ({len(merged):,} Stationen mit Preisdaten im Gesamtzeitraum)\n")

    for km in COMPETITION_RADII_KM:
        col = f"n_competitors_{km}km"
        sub = merged[[col, "diesel_avg_gesamtzeitraum"]].dropna()
        if len(sub) > 2:
            r, p = stats.pearsonr(sub[col], sub["diesel_avg_gesamtzeitraum"])
            slope = np.polyfit(sub[col], sub["diesel_avg_gesamtzeitraum"], 1)[0]
            print(f"  Radius {km:>2} km:  r={r:+.4f}  p={p:.5f}  "
                  f"Steigung≈{slope*100:+.3f} Cent/L pro Konkurrent  "
                  f"({'✅ signifikant' if p < 0.05 else '❌ nicht signifikant'})")

    print("\n  Ø Diesel (Gesamtzeitraum) je Wettbewerbs-Bucket "
          f"(Referenz: {COMPETITION_RADII_KM[min(1, len(COMPETITION_RADII_KM)-1)]}-km-Radius):")
    bucket_order = [b[2] for b in COMPETITION_BINS]
    bucket_means = merged.groupby("competition_bucket")["diesel_avg_gesamtzeitraum"].agg(["mean", "count"])
    for label in bucket_order:
        if label in bucket_means.index:
            m, n = bucket_means.loc[label]
            print(f"    {label:<32} {m:.3f} €/L   (N={n:,.0f})")

    df = timeseries_df[timeseries_df["dimension"] == "Wettbewerb"]
    if not df.empty and len(bucket_order) >= 2:
        df_early = df[df["year"] <= df["year"].min() + 2]
        df_late  = df[df["year"] >= df["year"].max() - 2]
        for label_phase, sub in [("erste 3 Jahre", df_early), ("letzte 3 Jahre", df_late)]:
            piv = sub.groupby("group")["diesel_avg"].mean()
            spread = piv.max() - piv.min() if len(piv) > 1 else float("nan")
            print(f"\n  Preisspanne zwischen Wettbewerbs-Buckets ({label_phase}): "
                  f"{spread:.4f} €/L")
    print(f"{'═'*72}\n")


def print_control_table(stations: pd.DataFrame, station_overall: pd.Series):
    """Region × Urban/Rural – Kontrolle für Konfundierung (Simpson's Paradox-Check)."""
    print(f"{'═'*72}")
    print("  KONTROLL-TABELLE: Region × Urban/Rural (Gesamtzeitraum)")
    print("  → prüft, ob der Ost/West-Unterschied durch unterschiedliche")
    print("    Stadt/Land-Verteilung konfundiert ist")
    print(f"{'═'*72}")

    merged = stations.set_index("station_uuid").join(station_overall, how="inner")
    piv = merged.pivot_table(
        index="region", columns="urban_rural_zone",
        values="diesel_avg_gesamtzeitraum", aggfunc="mean",
    )
    zone_order = [z[2] for z in URBAN_TYPES if z[2] in piv.columns]
    piv = piv[zone_order]
    print(f"  {'':<22}" + "".join(f"{z[:16]:>18}" for z in zone_order))
    for region, row in piv.iterrows():
        print(f"  {region:<22}" + "".join(
            f"{v:>17.3f}€" if pd.notna(v) else f"{'–':>18}" for v in row.values
        ))
    print(f"{'═'*72}\n")


# ─────────────────────────────────────────────────────────────────────────────
# HAUPTPROGRAMM
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="H4: Ost/West, Marken- und Wettbewerbsanalyse (Zeitverlauf)"
    )
    parser.add_argument("--repo-path", required=True, help="Pfad zum tankerkoenig-data Repo")
    parser.add_argument("--start", default=DEFAULT_START, help="Startmonat YYYY-MM (default 2014-06)")
    parser.add_argument("--end", default=DEFAULT_END, help="Endmonat YYYY-MM (default 2026-06)")
    parser.add_argument("--use-cache", action="store_true",
                        help="Statische Klassifikation aus Cache laden (kein erneuter Bundesland-Download)")
    parser.add_argument("--clear-cache", action="store_true", help="Alle Caches löschen und neu berechnen")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = Path(args.repo_path)

    if args.clear_cache:
        for f in [STATIONS_CACHE, BUNDESLAND_CACHE, TIMESERIES_CACHE]:
            if f.exists():
                f.unlink()
                print(f"  🗑 Cache gelöscht: {f.name}")

    print(f"\n{'═'*72}")
    print(f"  H4: Ost/West · Marke · Wettbewerb – {args.start} bis {args.end}")
    print(f"{'═'*72}\n")

    print("[1/4] Statische Klassifikation (Ost/West, Marke, Urban/Rural, Wettbewerb)...")
    stations = build_static_classification(repo_path, use_cache=args.use_cache and not args.clear_cache)
    stations_df = pd.DataFrame(stations.drop(columns="geometry"))

    print("\n[2/4] Monatliche Preise laden + Zeitreihe aufbauen...")
    timeseries_df, station_overall = build_timeseries(repo_path, stations_df, args.start, args.end)

    print("\n[3/4] Exportiere CSVs...")
    if timeseries_df.empty:
        print("  ⚠ Keine Preisdaten im gewählten Zeitraum gefunden – prüfe --repo-path/--start/--end.")
        return
    timeseries_df.to_csv(TIMESERIES_CACHE, index=False, encoding="utf-8")
    print(f"  ✅ Zeitreihe: {TIMESERIES_CACHE}  ({len(timeseries_df):,} Zeilen)")

    stations_export = stations_df.merge(
        station_overall.rename("diesel_avg_gesamtzeitraum"),
        left_on="station_uuid", right_index=True, how="left",
    )
    stations_export.to_csv(STATIONS_CSV_OUT, index=False, encoding="utf-8")
    print(f"  ✅ Stationen + Klassifikation: {STATIONS_CSV_OUT}  ({len(stations_export):,} Zeilen)")

    print("\n[4/4] Statistik...")
    print_station_overview(stations_df)
    print_ost_west_analysis(timeseries_df)
    print_brand_analysis(stations_df, timeseries_df)
    print_competition_analysis(stations_df, station_overall, timeseries_df)
    print_control_table(stations_df, station_overall)


if __name__ == "__main__":
    main()