"""Central paths for the project, derived from this file's location.

Layout (repo root = project root):

    <root>/
      prices/                 bulk source data (from tankerkoenig-data)
      stations/stations.csv   bulk source data
      scripts/                this code
      web/                    static site served by `python3 -m http.server`
        data/                 generated JSON/CSV the pages fetch

All scripts import from here so moving the project only changes one file.
"""

import os

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPTS_DIR)          # repo root

# Bulk source data lives at the repo root (prices/ and stations/).
PRICES_DIR = os.path.join(PROJECT_DIR, "prices")
STATIONS_CSV = os.path.join(PROJECT_DIR, "stations", "stations.csv")
PRICE_GLOB = os.path.join(PRICES_DIR, "*", "*", "*-prices.csv")

# Web output.
WEB_DIR = os.path.join(PROJECT_DIR, "web")
DATA_DIR = os.path.join(WEB_DIR, "data")
