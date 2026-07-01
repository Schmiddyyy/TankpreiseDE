"""
Tankerkoenig Data Collector
============================
Sammelt Tankstellenpreise flächendeckend für Deutschland via Tankerkoenig-API.
Speichert Ergebnisse als CSV + SQLite. Trackt Netzwerk-Traffic am Ende.

Verwendung:
    pip install requests pandas tqdm
    python tankstellen_collector.py --api-key DEIN_API_KEY
"""

import requests
import pandas as pd
import sqlite3
import json
import time
import argparse
import sys
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# KONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://creativecommons.tankerkoenig.de/json"

# Deutschland: Bounding Box (lat/lng)
GER_LAT_MIN, GER_LAT_MAX = 47.3, 55.1
GER_LNG_MIN, GER_LNG_MAX = 5.9, 15.1

# Gitter-Abstand in Grad (~50 km Abstand zwischen Punkten)
# 0.5° Lat  ≈ 55 km | 0.7° Lng ≈ 50 km
GRID_STEP_LAT = 0.5
GRID_STEP_LNG = 0.7

# Suchradius pro Gitterpunkt (in km) – leichte Überlappung zum Lückenschließen
RADIUS_KM = 35

# Kraftstofftypen
FUEL_TYPES = ["e5", "e10", "diesel"]

# Wartezeit zwischen Requests (API Fair-Use: max 10 req/min empfohlen)
REQUEST_DELAY_SEC = 6.5

api_key = "e7cb4062-ee37-4916-803a-bb2f65fc26fd"

# Output-Pfade
OUTPUT_DIR = Path("./tankstellen_data")
CSV_PATH = OUTPUT_DIR / "tankstellen_preise.csv"
DB_PATH  = OUTPUT_DIR / "tankstellen.db"
LOG_PATH = OUTPUT_DIR / "collection_log.json"


# ─────────────────────────────────────────────────────────────────────────────
# NETZWERK-TRAFFIC-TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class TrafficTracker:
    """Misst gesendete und empfangene Bytes über alle API-Requests."""

    def __init__(self):
        self.bytes_sent     = 0
        self.bytes_received = 0
        self.request_count  = 0
        self.error_count    = 0

    def record(self, response: requests.Response):
        """Registriert einen abgeschlossenen Request."""
        # Gesendete Bytes = Request-Header + Body
        req = response.request
        header_bytes = sum(
            len(k) + len(v) + 4          # ": " + "\r\n"
            for k, v in req.headers.items()
        )
        body_bytes = len(req.body) if req.body else 0
        self.bytes_sent += len(req.method) + len(req.url) + header_bytes + body_bytes

        # Empfangene Bytes = Response-Header + Body
        resp_header_bytes = sum(
            len(k) + len(v) + 4
            for k, v in response.headers.items()
        )
        self.bytes_received += resp_header_bytes + len(response.content)
        self.request_count  += 1

    def record_error(self):
        self.error_count += 1

    def total_bytes(self):
        return self.bytes_sent + self.bytes_received

    def _fmt(self, n_bytes: int) -> str:
        """Formatiert Bytes lesbar."""
        for unit in ["B", "KB", "MB", "GB"]:
            if n_bytes < 1024:
                return f"{n_bytes:.2f} {unit}"
            n_bytes /= 1024
        return f"{n_bytes:.2f} TB"

    def summary(self) -> dict:
        return {
            "requests_total":    self.request_count,
            "requests_errors":   self.error_count,
            "bytes_sent":        self.bytes_sent,
            "bytes_received":    self.bytes_received,
            "bytes_total":       self.total_bytes(),
            "sent_human":        self._fmt(self.bytes_sent),
            "received_human":    self._fmt(self.bytes_received),
            "total_human":       self._fmt(self.total_bytes()),
        }

    def print_report(self):
        s = self.summary()
        print("\n" + "═" * 50)
        print("  📡  NETZWERK-TRAFFIC-BERICHT")
        print("═" * 50)
        print(f"  Requests gesamt : {s['requests_total']:>8}")
        print(f"  Fehler          : {s['requests_errors']:>8}")
        print(f"  Gesendet        : {s['sent_human']:>10}")
        print(f"  Empfangen       : {s['received_human']:>10}")
        print(f"  GESAMT          : {s['total_human']:>10}")
        print("─" * 50)

        # Verbleibendes Datenvolumen (300 GB Limit)
        limit_gb   = 300
        used_gb    = self.total_bytes() / (1024 ** 3)
        remain_gb  = limit_gb - used_gb
        pct        = (used_gb / limit_gb) * 100
        print(f"  Monatsvolumen   : {limit_gb} GB")
        print(f"  Verbraucht      : {used_gb:.4f} GB  ({pct:.4f} %)")
        print(f"  Verbleibend     : {remain_gb:.2f} GB")
        print("═" * 50 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# GITTER-GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_germany_grid() -> list[tuple[float, float]]:
    """Erzeugt ein reguläres Gitter über Deutschland."""
    points = []
    lat = GER_LAT_MIN
    while lat <= GER_LAT_MAX:
        lng = GER_LNG_MIN
        while lng <= GER_LNG_MAX:
            points.append((round(lat, 4), round(lng, 4)))
            lng += GRID_STEP_LNG
        lat += GRID_STEP_LAT
    return points


# ─────────────────────────────────────────────────────────────────────────────
# API-FUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def fetch_stations(api_key: str, lat: float, lng: float,
                   tracker: TrafficTracker) -> list[dict]:
    """
    Lädt alle Tankstellen in RADIUS_KM um (lat, lng).
    Gibt eine Liste von Station-Dicts zurück.
    """
    params = {
        "lat":    lat,
        "lng":    lng,
        "rad":    RADIUS_KM,
        "type":   "all",
        "apikey": api_key,
        "sort":   "dist",
    }
    try:
        r = requests.get(f"{BASE_URL}/stations/search.json",
                         params=params, timeout=15)
        tracker.record(r)
        r.raise_for_status()
        data = r.json()
        if data.get("ok"):
            return data.get("stations", [])
        else:
            print(f"  ⚠ API-Fehler bei ({lat}, {lng}): {data.get('message')}")
            return []
    except Exception as e:
        tracker.record_error()
        print(f"  ✗ Request-Fehler bei ({lat}, {lng}): {e}")
        return []


def fetch_prices(api_key: str, station_ids: list[str],
                 tracker: TrafficTracker) -> dict:
    """
    Lädt aktuelle Preise für bis zu 10 Stations-IDs gleichzeitig.
    Gibt ein Dict {station_id: {e5, e10, diesel}} zurück.
    """
    if not station_ids:
        return {}
    params = {
        "ids":    ",".join(station_ids),
        "apikey": api_key,
    }
    try:
        r = requests.get(f"{BASE_URL}/prices.json",
                         params=params, timeout=15)
        tracker.record(r)
        r.raise_for_status()
        data = r.json()
        if data.get("ok"):
            return data.get("prices", {})
        else:
            print(f"  ⚠ Preis-API Fehler: {data.get('message')}")
            return {}
    except Exception as e:
        tracker.record_error()
        print(f"  ✗ Preis-Request Fehler: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# DATENSPEICHERUNG
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    """Erstellt die SQLite-Datenbank und Tabellen falls nicht vorhanden."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stations (
            id          TEXT PRIMARY KEY,
            name        TEXT,
            brand       TEXT,
            street      TEXT,
            place       TEXT,
            postcode    TEXT,
            lat         REAL,
            lng         REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            station_id  TEXT,
            timestamp   TEXT,
            e5          REAL,
            e10         REAL,
            diesel      REAL,
            PRIMARY KEY (station_id, timestamp)
        )
    """)
    conn.commit()
    return conn


def save_to_db(conn: sqlite3.Connection,
               stations: list[dict], prices: dict, timestamp: str):
    """Speichert Station-Metadaten und Preise in SQLite."""
    for s in stations:
        sid = s.get("id")
        conn.execute("""
            INSERT OR IGNORE INTO stations
                (id, name, brand, street, place, postcode, lat, lng)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sid,
            s.get("name"),
            s.get("brand"),
            s.get("street"),
            s.get("place"),
            s.get("postCode"),
            s.get("lat"),
            s.get("lng"),
        ))

        if sid in prices:
            p = prices[sid]
            conn.execute("""
                INSERT OR IGNORE INTO prices
                    (station_id, timestamp, e5, e10, diesel)
                VALUES (?, ?, ?, ?, ?)
            """, (
                sid, timestamp,
                p.get("e5"),
                p.get("e10"),
                p.get("diesel"),
            ))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# HAUPTPROGRAMM
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tankerkoenig – flächendeckende Datenerhebung für Deutschland"
    )
    parser.add_argument("--api-key", required=True,
                        help="Dein Tankerkoenig API-Key")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Gitter berechnen, keine API-Calls")
    parser.add_argument("--max-points", type=int, default=None,
                        help="Nur N Gitterpunkte abfragen (zum Testen)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tracker = TrafficTracker()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Gitter erzeugen
    grid = generate_germany_grid()
    print(f"🗺  Gitter: {len(grid)} Punkte über Deutschland")

    if args.dry_run:
        print("  [dry-run] Kein API-Aufruf – Abbruch.")
        print(f"  Geschätzter Traffic: ~{len(grid) * 5} Requests")
        print(f"  Tipps: Starte mit --max-points 5 zum Testen!")
        sys.exit(0)

    if args.max_points:
        grid = grid[: args.max_points]
        print(f"  ℹ Begrenzung auf {len(grid)} Punkte (--max-points)")

    # DB initialisieren
    conn = init_db(DB_PATH)

    all_rows   = []
    seen_ids   = set()

    print(f"\n🚀 Starte Datensammlung – {len(grid)} Gitterpunkte\n")

    for i, (lat, lng) in enumerate(grid, 1):
        print(f"[{i:>4}/{len(grid)}]  ({lat}, {lng})", end="  ", flush=True)

        # 1) Tankstellen im Umkreis laden
        stations = fetch_stations(args.api_key, lat, lng, tracker)
        new_stations = [s for s in stations if s["id"] not in seen_ids]
        seen_ids.update(s["id"] for s in stations)
        print(f"→ {len(stations)} Stationen ({len(new_stations)} neu)", end="  ")

        # 2) Preise für neue Stationen laden (max 10 pro Request)
        prices = {}
        new_ids = [s["id"] for s in new_stations]
        for chunk_start in range(0, len(new_ids), 10):
            chunk = new_ids[chunk_start: chunk_start + 10]
            chunk_prices = fetch_prices(args.api_key, chunk, tracker)
            prices.update(chunk_prices)
            if chunk_start + 10 < len(new_ids):
                time.sleep(REQUEST_DELAY_SEC)

        # 3) In DB speichern
        save_to_db(conn, new_stations, prices, timestamp)

        # 4) Für CSV merken
        for s in new_stations:
            sid = s.get("id")
            p   = prices.get(sid, {})
            all_rows.append({
                "id":        sid,
                "name":      s.get("name"),
                "brand":     s.get("brand"),
                "street":    s.get("street"),
                "place":     s.get("place"),
                "postcode":  s.get("postCode"),
                "lat":       s.get("lat"),
                "lng":       s.get("lng"),
                "e5":        p.get("e5"),
                "e10":       p.get("e10"),
                "diesel":    p.get("diesel"),
                "timestamp": timestamp,
            })

        # Traffic-Zwischenstand alle 10 Punkte
        if i % 10 == 0:
            total_mb = tracker.total_bytes() / (1024 ** 2)
            print(f"\n   📊 Traffic bisher: {total_mb:.2f} MB "
                  f"| Stationen gesamt: {len(seen_ids)}\n")
        else:
            print("✓")

        time.sleep(REQUEST_DELAY_SEC)

    # ── CSV exportieren ────────────────────────────────────────────────────
    df = pd.DataFrame(all_rows)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8")
    print(f"\n✅ CSV gespeichert:    {CSV_PATH}  ({len(df)} Einträge)")
    print(f"✅ SQLite gespeichert: {DB_PATH}")

    # ── Log speichern ──────────────────────────────────────────────────────
    log = {
        "timestamp":       timestamp,
        "grid_points":     len(grid),
        "stations_found":  len(seen_ids),
        "traffic":         tracker.summary(),
    }
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"✅ Log gespeichert:    {LOG_PATH}")

    # ── Traffic-Bericht ────────────────────────────────────────────────────
    tracker.print_report()

    conn.close()


if __name__ == "__main__":
    main()