# Data strategy: bulk repo only

This documents *why* the project builds its station map from this bulk CSV repo
and deliberately does **not** use the Tankerkönig live API for the build. It
exists so that "why didn't we just use the API?" has a recorded answer.

## Decision

**Build the full station catalog + price baseline from the bulk repo (this git
clone). Do not use the API for the data build.** The API may later be layered on
top for on-demand live price refresh of the handful of stations a user is
actually looking at — but that is optional and out of scope for the snapshot.

## Why the API cannot build the all-Germany map

| Constraint | Consequence |
|------------|-------------|
| No "all stations" endpoint — only `list.php` with a **25 km radius cap** | Covering Germany needs hundreds of overlapping circle queries |
| Free tier rate limit **1 request/minute** | A full national sweep would take 5–8+ hours |
| Bulk-collection pattern is **explicitly forbidden** | Doing the above gets the API key deactivated |
| API serves **current prices only** | The history since 2014 lives *only* in this repo |

## What the API *is* good for (later, optional)

- On-demand local lookups: live prices within 25 km of a point (`list.php`).
- Refreshing the ≤10 stations a user clicked (`prices.php`).
- Keeping a small, in-view set of prices real-time fresh.

It changes nothing for a low-bandwidth teammate: they still consume the
committed snapshot. An API key only matters for whoever runs a live refresh.

## Why the snapshot is tiny vs. the raw CSVs

`ingest.py` collapses the data three ways, biggest first:

1. **Events → state.** The CSVs log every price-change event (a single day's
   `prices.csv` is hundreds of thousands of rows; the full history is billions).
   The snapshot keeps **one row per station** — each station's latest price.
2. **History → one moment.** The CSVs are a time series; the snapshot has no
   time dimension. All of 2014–today is discarded.
3. **Fewer columns, rounded values.** Only `id, name, lat, lng, e5, e10, diesel`
   survive; coords rounded to 5 decimals (~1 m), prices to 3.

Result: ~300 bytes/station → ~3–5 MB for all ~15k stations, vs. one day of raw
prices alone (~10–30 MB) and tens of GB for the whole repo.

## Known trade-off

The snapshot is a single moment. Any time-based analysis — cheapest hour of day,
multi-year trends, seasonal cross-border gaps, the 2022 Tankrabatt — still
requires going back to the raw CSVs, which is exactly the history we collapsed
away here.

## How to build

```sh
python3 ingest.py              # writes snapshot.json
python3 ingest.py --max-days 7 # scan only the last 7 daily files
```

The `bounds` block in `snapshot.json` (north/south/east/west) is the map's
fit-to-view bounding box.
