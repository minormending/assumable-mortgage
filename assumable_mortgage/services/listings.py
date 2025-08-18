from __future__ import annotations

import csv
from typing import Iterable, Mapping
import logging

log = logging.getLogger(__name__)


def write_listings_to_csv(listings: Iterable[Mapping], filename: str = "listings.csv") -> None:
    fieldnames = [
        "ListingId",
        "Cash",
        "Price",
        "Location",
        "Content",
        "Rate",
        "Payment",
        "EstimatedPayment",
        "DetailsLink",
        "PhotoLink",
    ]

    log.info("csv.write_start", extra={"file": filename})
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        count = 0
        for item in listings:
            writer.writerow(
                {
                    "ListingId": item.get("ListingId"),
                    "Price": item.get("PriceHtml"),
                    "Cash": item.get("CashFormat"),
                    "Location": item.get("Location"),
                    "Content": item.get("Content"),
                    "Rate": item.get("MainFeatures", {}).get("Rate"),
                    "Payment": item.get("MainFeatures", {}).get("PaymentFormat"),
                    "EstimatedPayment": item.get("MainFeatures", {}).get("EstimatedPayFormat"),
                    "DetailsLink": item.get("DetailsLink"),
                    "PhotoLink": item.get("PhotoLink"),
                }
            )
            count += 1
    log.info("csv.write_complete", extra={"file": filename, "rows": count})
