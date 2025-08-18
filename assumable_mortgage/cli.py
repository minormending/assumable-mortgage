from __future__ import annotations

import argparse
import logging
import os
import time

from dotenv import load_dotenv

from .clients.assumable import AssumableClient
from .clients.greatschools import GreatSchoolsClient
from .config import AssumableConfig, GreatSchoolsConfig
from .logging_config import setup_logging
from .services.listings import write_listings_to_csv
from .services.map_builder import generate_map_from_cache


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Scrape listings and build map")
    parser.add_argument("--output", type=str, default="listings.csv", help="CSV output filename")
    parser.add_argument("--map", action="store_true", help="Also generate a map with pins")
    parser.add_argument(
        "--no-schools",
        action="store_true",
        help="Disable schools overlay in the generated map",
    )
    parser.add_argument("--log-level", type=str, default=os.getenv("APP_LOG_LEVEL", "INFO"))
    parser.add_argument("--json-logs", action="store_true", help="Enable JSON logging")
    args = parser.parse_args()

    setup_logging(args.log_level, args.json_logs)
    log = logging.getLogger("cli")

    # Config
    acfg = AssumableConfig.from_env()
    gcfg = GreatSchoolsConfig.from_env()
    log.info(
        "startup",
        extra={
            "json_logs": bool(args.json_logs),
            "level": args.log_level,
            "have_assumable_token": bool(acfg.token),
            "have_gs_csrf": bool(gcfg.csrf_token),
            "have_gs_cookie": bool(gcfg.csrf_cookie),
            "schools_enabled": not args.no_schools,
        },
    )

    # Warn if schools are enabled but credentials are missing
    if args.map and not args.no_schools:
        if not gcfg.csrf_token or not gcfg.csrf_cookie:
            log.warning(
                "greatschools.credentials_missing",
                extra={
                    "have_csrf": bool(gcfg.csrf_token),
                    "have_cookie": bool(gcfg.csrf_cookie),
                    "note": "schools overlay may be empty; set GS_CSRF_TOKEN and GS_COOKIE or use --no-schools",
                },
            )

    cookies = {
        "XSRF-TOKEN": acfg.xsrf_token,
        "cf_clearance": acfg.cf_clearance,
        "botble_session": acfg.botble_session,
    }
    if acfg.remember_account_name and acfg.remember_account:
        cookies[f"remember_account_{acfg.remember_account_name}"] = acfg.remember_account

    # Clients
    assumable = AssumableClient()
    # Defer GS client create until needed and enabled
    gs_client: GreatSchoolsClient | None = None
    if not args.no_schools:
        gs_client = GreatSchoolsClient(gcfg)

    # Fetch all listing pages
    t0 = time.perf_counter()
    first_response = assumable.fetch_listing_page(1, acfg.token, cookies)
    total_pages = first_response.get("SearchPagerBar", {}).get("TotalPages", 1)
    all_listings = first_response.get("MapList", {}).get("ListingsSummaryVM", [])
    log.info("assumable.pagination", extra={"total_pages": total_pages, "first_page_items": len(all_listings)})

    for page in range(2, total_pages + 1):
        response = assumable.fetch_listing_page(page, acfg.token, cookies)
        items = response.get("MapList", {}).get("ListingsSummaryVM", [])
        all_listings.extend(items)
        if not items:
            log.warning("assumable.empty_page", extra={"page": page})

    if not all_listings:
        log.warning("No listings found.")
        return

    write_listings_to_csv(all_listings, args.output)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    log.info("listings.saved", extra={"count": len(all_listings), "pages": total_pages, "file": args.output, "ms": elapsed_ms})

    if args.map:
        generate_map_from_cache(gs_client=gs_client)
        log.info("done", extra={"ms": int((time.perf_counter() - t0) * 1000)})


if __name__ == "__main__":
    main()
