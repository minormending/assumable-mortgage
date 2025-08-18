from __future__ import annotations

import json
import logging
import random
import urllib.parse
from typing import Any
import time

from curl_cffi import requests

from ..config import GreatSchoolsConfig
from ..utils.cache import FileCache


log = logging.getLogger(__name__)


class GreatSchoolsClient:
    BASE_URL = "https://www.greatschools.org/gsr/api/schools"

    def __init__(self, cfg: GreatSchoolsConfig, cache: FileCache | None = None) -> None:
        self.cfg = cfg
        self.cache = cache or FileCache()

    def _random_public_ipv4(self) -> str:
        while True:
            a, b, c, d = (random.randint(1, 255) for _ in range(4))
            if a in (0, 10, 127, 255):
                continue
            if a == 100 and 64 <= b <= 127:
                continue
            if a == 169 and b == 254:
                continue
            if a == 172 and 16 <= b <= 31:
                continue
            if a == 192 and b == 168:
                continue
            if a == 198 and b in (18, 19):
                continue
            if 224 <= a <= 239:
                continue
            if 240 <= a <= 255:
                continue
            return f"{a}.{b}.{c}.{d}"

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "user-agent": self.cfg.user_agent,
        }
        if self.cfg.csrf_token:
            headers["x-csrf-token"] = self.cfg.csrf_token
        return headers

    def _build_cookies(self, lat: float, lon: float, state: str) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.cfg.csrf_cookie:
            cookies["csrf_token"] = self.cfg.csrf_cookie

        search_prefs = {
            "location": {
                "ip": self._random_public_ipv4(),
                "city": self.cfg.city,
                "lat": lat,
                "lon": lon,
                "state": state,
                "locationType": "state",
            }
        }
        cookies["search_prefs"] = urllib.parse.quote(
            json.dumps(search_prefs, separators=(",", ":"))
        )
        return cookies

    def fetch_schools(
        self,
        lat: float,
        lon: float,
        *,
        distance: int = 18,
        state: str = "NY",
    ) -> dict[str, Any]:
        """Fetch school data, follow pagination via `links.next`, and cache pages.

        Returns an aggregated response with all `items` combined.
        """
        params: dict[str, Any] = {
            "state": state,
            "sort": "rating",
            "limit": 2000,
            "url": "/gsr/api/schools",
            "countsOnly": "false",
            "level_code": "e,e",
            "lat": lat,
            "lon": lon,
            "distance": distance,
            "extras": "students_per_teacher,review_summary,saved_schools",
            "locationType": "state",
        }

        # Return cached aggregated result if present
        agg_key = self.cache.make_key(params)
        agg_path = self.cache.path_for("schools", agg_key)
        cached = self.cache.read_json(agg_path)
        if cached:
            log.debug("cache.hit schools aggregated", extra={"items": len(cached.get("response", {}).get("items", []))})
            return cached["response"]

        headers = self._build_headers()
        cookies = self._build_cookies(lat, lon, state)

        # First page
        log.info("greatschools.request", extra={"url": self.BASE_URL, "params": {"lat": lat, "lon": lon, "distance": distance, "state": state}})
        start = time.perf_counter()
        resp = requests.get(
            self.BASE_URL,
            headers=headers,
            cookies=cookies,
            params=params,
            impersonate="chrome110",
            timeout=30,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code != 200:
            log.error("greatschools.error", extra={"status": resp.status_code, "ms": duration_ms})
            raise RuntimeError(f"School request failed with status: {resp.status_code}")

        first_result = resp.json()

        # Cache first page raw
        first_req = {"url": self.BASE_URL, "params": params}
        first_key = self.cache.make_key(first_req)
        first_path = self.cache.path_for("schools_page", first_key)
        self.cache.write_json(first_path, {"request": first_req, "response": first_result})

        page_items = list(first_result.get("items", []))
        log.info("greatschools.response", extra={"status": resp.status_code, "ms": duration_ms, "items": len(page_items)})
        all_items = page_items
        next_link = (first_result.get("links") or {}).get("next")
        page_no = 1

        # Follow pagination
        while next_link:
            page_no += 1
            log.info("greatschools.request", extra={"url": next_link, "page": page_no})
            start = time.perf_counter()
            nresp = requests.get(
                next_link,
                headers=headers,
                cookies=cookies,
                impersonate="chrome110",
                timeout=30,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            if nresp.status_code != 200:
                log.warning("greatschools.page_failed", extra={"status": nresp.status_code, "url": next_link, "page": page_no, "ms": duration_ms})
                break

            page_result = nresp.json()
            page_count = len(page_result.get("items", []) or [])
            all_items.extend(page_result.get("items", []))
            log.info("greatschools.response", extra={"status": nresp.status_code, "page": page_no, "items": page_count, "ms": duration_ms})

            # Cache page raw using full URL
            page_key = self.cache.make_key(nresp.url)
            page_path = self.cache.path_for("schools_page", page_key)
            self.cache.write_json(page_path, {"request": {"url": nresp.url}, "response": page_result})

            next_link = (page_result.get("links") or {}).get("next")
            if not next_link:
                log.debug("greatschools.pagination_end", extra={"pages": page_no, "total_items": len(all_items)})

        aggregated = dict(first_result)
        aggregated["items"] = all_items

        # Cache aggregated
        self.cache.write_json(
            agg_path,
            {"request": {"url": self.BASE_URL, "params": params}, "response": aggregated},
        )
        log.info("greatschools.completed", extra={"items": len(all_items), "pages": page_no})
        return aggregated
