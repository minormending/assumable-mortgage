# Assumable Mortgage — Scraper and Map Builder

Scrapes assumable.io listings to CSV and builds an interactive Folium map with property pins. Optionally overlays nearby schools (GreatSchools) with filterable tags.


## Quick Start

- Requirements: Python 3.10+, Poetry
- Install:
  - `poetry install`
- Configure environment:
  - Copy `template.env` to `.env` and fill in values (see Config below)
- Run scraper (CSV only):
  - `poetry run assumable --output listings.csv`
- Run scraper + map:
  - `poetry run assumable --map --output listings.csv --log-level INFO`
- Outputs:
  - `listings.csv` — flattened listing data
  - `map.html` — interactive map with property pins (and optional schools)
  - `.cache/` — cached listing pages and school responses


## CLI

`poetry run assumable [--map] [--no-schools] [--output FILE] [--log-level LEVEL] [--json-logs]`

- `--map`: build `map.html` using cached listing pages
- `--no-schools`: disable the GreatSchools overlay in the map
- `--output`: CSV filename (default `listings.csv`)
- `--log-level`: logging level (e.g., `DEBUG`, `INFO`, `WARN`)
- `--json-logs`: emit JSON logs to stdout

Console scripts: `assumable` and `scrape-assumable` both point to `assumable_mortgage.cli:main`.


## Config

Place secrets in `.env` (loaded by `python-dotenv`). Refer to `template.env` for a starter.

Assumable scraping
- `ASSUMABLE_TOKEN` (required): request token from assumable.io
- Optional cookies (may improve reliability): `XSRF_TOKEN`, `CF_CLEARANCE`, `BOTBLE_SESSION`
- Optional remember-me cookie: `REMEMBER_ACCOUNT_NAME`, `REMEMBER_ACCOUNT`

GreatSchools overlay (optional)
- `GS_USER_AGENT` (optional, default provided)
- `GS_CSRF_TOKEN` and `GS_COOKIE` (optional but usually required to fetch data)
- `GS_CITY` (default: "The Bronx"). Used to construct a `search_prefs` cookie.

Logging
- `APP_LOG_LEVEL` (default `INFO`)
- `APP_JSON_LOGS` (`true`/`false`, default `false`)
- `APP_LOG_FILE` (optional): if set, logs also write to a rotating file


## How It Works

- Scraper (`AssumableClient`)
  - Posts to the assumable.io endpoint per page, caches raw responses under `.cache/page_*.json`
  - Aggregates list items and writes a simple `listings.csv`

- Map Builder (`generate_map_from_cache`)
  - Reads cached listing pages, extracts coordinates, and creates Folium markers grouped by price bucket
  - Optionally fetches schools near the first pin via `GreatSchoolsClient` and adds filterable tag buttons
  - Pass `--no-schools` to skip the schools overlay entirely


## Map Builder Details (Readability + Observability)

The map builder has been refactored for clarity:

- `MapPoint` dataclass: `lat`, `lon`, `popup_html`, `color`, `group`
- Helpers with single responsibility:
  - `_load_points_from_cache(cache_dir)`: read cache and transform listings to `MapPoint`s
  - `_listing_to_point(listing)`: convert one listing dict into a `MapPoint`
  - `_add_property_layers(map, points)`: add grouped property markers to the map
  - `_add_schools_layer(map, lat, lon, gs_client)`: fetch schools and add markers + tag filters

Price buckets
- `$300k+` (red)
- `$200k - $299k` (lightred)
- `$100k - $199k` (orange)
- `Cash < $100k` (green)
- `Unknown` (gray)

School markers
- Icon color reflects rating: higher is greener; `N/A` is gray
- Adds tag filter groups for `rating:*` and `type:*` using TagFilterButton

Structured logging (selected events)
- `map.cache_scan` `{files, dir}`
- `map.cache_loaded` `{points, skipped, ms}`
- `map.property_groups` `{<bucket>: count, ...}`
- `map.schools_loaded` `{count}`
- `map.schools_summary` `{ms, ratings: {..}, types: {..}}`
- `map.saved` `{file, pins, save_ms, total_ms}`

These make behavior measurable at a glance and easy to search when using JSON logs.


## Running With JSON Logs

- CLI flag: `--json-logs`
- Or env: `APP_JSON_LOGS=true`
- Combine with `--log-level DEBUG` for more detail

Example
- `poetry run assumable --map --log-level DEBUG --json-logs`
- `poetry run assumable --map --no-schools` (skip schools overlay)


## Caching

- Listing pages: `.cache/page_*.json` (created by `AssumableClient`)
- Schools: raw pages and an aggregated response are cached using hashed keys (see `utils/cache.py`)
- Deleting `.cache/` forces fresh network calls on next run


## Troubleshooting

- "No coordinates found to map":
  - Ensure the scraper ran and `.cache` contains `page_*.json`
  - Confirm `ASSUMABLE_TOKEN` is set and valid

- Schools don’t appear:
  - Check logs for `map.schools_failed_fetch` (auth or network issue)
  - Ensure `GS_CSRF_TOKEN` and `GS_COOKIE` are set; otherwise the overlay is skipped on error
  - Or explicitly disable with `--no-schools`

- HTTP errors or blocks:
  - These services can change defenses; try updating cookies, tokens, or user agent


## Development Notes

- Python 3.10+, Poetry-managed
- Code lives under `assumable_mortgage/`
- Public API for maps: `assumable_mortgage.services.map_builder.generate_map_from_cache`
- Keep changes focused and minimal; structured logging preferred over prints


## GreatSchools Credentials — How To Obtain

Note: GreatSchools may change their site or defenses. The steps below reflect a common approach and may need adjustments.

Goal: Acquire a CSRF token header value and a `csrf_token` cookie value that allow the API requests to succeed.

Steps
- Open a browser (Chrome/Edge), go to `https://www.greatschools.org/`.
- Open DevTools (F12) → Network tab. Check "Preserve log".
- Interact with the site (search, navigate to a state/city) so API calls appear under the Network tab, typically to `/gsr/api/schools`.
- Click a `schools` request and inspect:
  - Headers → Request headers: copy value of `x-csrf-token` (this is `GS_CSRF_TOKEN`).
  - Cookies: find `csrf_token` (this is `GS_COOKIE`).
- Set both values in `.env`:
  - `GS_CSRF_TOKEN=...`
  - `GS_COOKIE=...`
- Optional: set a realistic `GS_USER_AGENT` copied from your browser’s request headers if needed.

Tips
- Values can expire; if requests start failing, repeat the steps.
- If you’d rather skip the overlay, run with `--no-schools` and the map will build without school markers.
