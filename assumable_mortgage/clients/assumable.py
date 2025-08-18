from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any

from curl_cffi import requests

log = logging.getLogger(__name__)


class AssumableClient:
    def __init__(self, cache_dir: str = ".cache") -> None:
        self.cache = Path(cache_dir)
        self.cache.mkdir(exist_ok=True)

    def fetch_listing_page(self, page: int, token: str, cookies: dict[str, str]) -> dict[str, Any]:
        url = f"https://app.assumable.io/?_token={token}"
        data = {
            "_token": token,
            "location": "New York",
            "search_mode": "location",
            "geopicker_type": "viewport",
            "page": page,
            "SelectedView": "map_view",
            "LocationGeoId": 3269,
            "viewport": "-76.8612404491507,37.73641064455742,-72.41452414055695,43.07531462025779",
            "zoom": 1,
            "ajax": 1,
        }

        key = hashlib.md5(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
        cache_file = self.cache / f"page_{key}.json"
        if cache_file.exists():
            log.debug("cache.hit assumable page", extra={"page": page})
            with open(cache_file, "r", encoding="utf-8") as f:
                result = json.load(f)
                return result["response"]

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://app.assumable.io",
            "Referer": f"https://app.assumable.io/?_token={token}&page={page}",
            "X-Requested-With": "XMLHttpRequest",
        }

        start = time.perf_counter()
        log.info("assumable.request", extra={"page": page, "url": url})
        response = requests.post(
            url,
            headers=headers,
            cookies=cookies,
            data=data,
            impersonate="chrome110",
            timeout=30,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)

        if response.status_code != 200:
            log.error(
                "assumable.error",
                extra={"page": page, "status": response.status_code, "ms": duration_ms},
            )
            raise RuntimeError(f"Assumable request failed with status: {response.status_code}")

        result = response.json()
        items = len(result.get("response", {}).get("MapList", {}).get("ListingsSummaryVM", []))
        log.info(
            "assumable.response",
            extra={"page": page, "status": response.status_code, "ms": duration_ms, "items": items},
        )
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "request": {"url": url, "data": data},
                        "response": result,
                    },
                    indent=2,
                )
            )
        return result
