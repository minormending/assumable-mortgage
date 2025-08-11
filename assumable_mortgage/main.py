import csv
import os
import argparse
import time
import random
import re
import atexit
from dotenv import load_dotenv
from curl_cffi import requests
from playwright.sync_api import sync_playwright
import json
from pathlib import Path
import hashlib
import folium

load_dotenv()

def fetch_listing_data(page: int, token: str, cookies: dict) -> dict:
    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
        
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
    cache_file = cache_dir / f"page_{key}.json"
    if cache_file.exists():
        print(f"[Cache] Loading page {page} from cache...")
        with open(cache_file, "r", encoding="utf-8") as f:
            result = json.load(f)
            return result["response"]


    print(f"Fetching page {page}...")
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://app.assumable.io",
        "Referer": f"https://app.assumable.io/?_token={token}&page={page}",
        "X-Requested-With": "XMLHttpRequest",
    }

    response = requests.post(
        url,
        headers=headers,
        cookies=cookies,
        data=data,
        impersonate="chrome110",
        timeout=30,
    )

    if response.status_code == 200:
        result = response.json()
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "request": {
                    "url": url,
                    "data": data
                },
                "response": result
                }, indent=2))
        return result
    else:
        raise RuntimeError(f"Request failed with status: {response.status_code}")

def write_listings_to_csv(listings, filename="listings.csv"):
    fieldnames = [
        "ListingId", "Cash", "Price", "Location", "Content",
        "Rate", "Payment", "EstimatedPayment",
        "DetailsLink", "PhotoLink"
    ]

    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for item in listings:
            writer.writerow({
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
            })


def compute_zillow_link(listing: dict) -> tuple[str | None, str | None]:
    """Return the Zillow URL and ZPID for a listing."""
    photo_link = listing.get("PhotoLink", "")
    zpid = None
    address_link = listing.get("Location", "").replace(" ", "-").lower()
    if photo_link:
        try:
            zpid = photo_link.split("/")[-1].split("_")[0]
        except IndexError:
            pass
    if zpid:
        return f"https://www.zillow.com/homedetails/{address_link}/{zpid}_zpid/", zpid
    return None, None


_SCHOOL_RATE_LIMIT_SECONDS = 1.5
_last_school_request: float = 0.0
_playwright = None
_browser = None


def _close_browser() -> None:
    global _playwright, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None


def _ensure_browser() -> None:
    global _playwright, _browser
    if _browser is None:
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        atexit.register(_close_browser)


def _rate_limit() -> None:
    global _last_school_request
    now = time.time()
    elapsed = now - _last_school_request
    if elapsed < _SCHOOL_RATE_LIMIT_SECONDS:
        time.sleep(_SCHOOL_RATE_LIMIT_SECONDS - elapsed + random.uniform(0, 0.5))
    _last_school_request = time.time()


def _fetch_school_html(url: str) -> str | None:
    _ensure_browser()
    page = _browser.new_page()
    try:
        page.goto(url, timeout=60_000, wait_until="networkidle")
        return page.content()
    except Exception:
        return None
    finally:
        page.close()


def _extract_schools_from_html(html: str) -> list[dict]:
    """Best-effort extraction of school info from Zillow HTML."""
    # Zillow embeds JSON in a script tag with id="__NEXT_DATA__".
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    def find_schools(obj):
        if isinstance(obj, dict):
            if "schools" in obj and isinstance(obj["schools"], list):
                return obj["schools"]
            for v in obj.values():
                res = find_schools(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_schools(item)
                if res:
                    return res
        return []

    raw_schools = find_schools(data) or []
    schools = []
    for s in raw_schools:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("schoolName") or s.get("title")
        rating = s.get("rating") or s.get("schoolRating") or s.get("gsRating")
        if name:
            schools.append({"name": name, "rating": rating})
    return schools


def get_school_data(zpid: str, url: str, fetch: bool) -> list[dict]:
    """Return school info for a ZPID, fetching and caching as needed."""
    cache_dir = Path(".cache") / "schools"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{zpid}.html"

    html = ""
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                html = f.read()
        except Exception:
            pass
    if not html:
        if not fetch:
            return []
        _rate_limit()
        html = _fetch_school_html(url)
        if not html:
            return []
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(html)
    schools = _extract_schools_from_html(html)
    return schools


def prefetch_school_data(listings: list[dict]):
    """Fetch and cache school data for all listings."""
    for listing in listings:
        url, zpid = compute_zillow_link(listing)
        if url and zpid:
            get_school_data(zpid, url, fetch=True)


def generate_map_from_cache(output_file="map.html", fetch_schools: bool = False):
    cache_dir = Path(".cache")
    all_points: list[dict] = []

    for cache_file in sorted(cache_dir.glob("page_*.json")):
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            listings = data.get("response", {}).get("MapList", {}).get("ListingsSummaryVM", [])
            for listing in listings:
                centroid = listing.get("Centroid", {})
                lat = centroid.get("latitude")
                lon = centroid.get("longitude")

                if not lat or not lon:
                    continue

                try:
                    price_str = listing.get("CashFormat", "").replace("$", "").replace(",", "")
                    price = int(float(price_str)) if price_str else 0
                except Exception:
                    price = 0

                zillow_link, zpid = compute_zillow_link(listing)

                def price_to_category(p):
                    if p >= 300_000:
                        return "$300k+", "red"
                    elif p >= 200_000:
                        return "$200k - $299k", "lightred"
                    elif p >= 100_000:
                        return "$100k - $199k", "orange"
                    elif p > 0:
                        return "Cash < $100k", "green"
                    return "Unknown", "gray"

                schools = []
                if zpid and zillow_link:
                    schools = get_school_data(zpid, zillow_link, fetch_schools)

                school_html = ""
                best_rating = 0
                if schools:
                    school_html += "<strong>Schools:</strong><br>"
                    for s in schools:
                        rating = s.get("rating", "N/A")
                        school_html += f"{s.get('name', 'Unknown')} ({rating})<br>"
                        try:
                            best_rating = max(best_rating, int(rating))
                        except Exception:
                            pass

                def rating_to_category(r):
                    if r >= 8:
                        return "School rating 8+"
                    elif r >= 5:
                        return "School rating 5-7"
                    elif r > 0:
                        return "School rating <5"
                    return "School rating unknown"

                school_group = rating_to_category(best_rating)

                popup_html = f"""
                <div style=\"width:300px\">
                    <img src=\"{listing.get('PhotoLink', '')}\" alt=\"Property Image\" style=\"width:100%; border-radius:6px; margin-bottom:8px;\"><br>
                    <strong>{listing.get("PriceHtml", "N/A")}</strong><br>
                    <strong>Cash:</strong> {listing.get("CashFormat", "N/A")}<br>
                    <em>{listing.get("Location", "")}</em><br><br>
                    {listing.get("Content", "")}<br><br>
                    <strong>Rate:</strong> {listing.get("MainFeatures", {}).get("Rate", "N/A")}<br>
                    <strong>Monthly:</strong> {listing.get("MainFeatures", {}).get("PaymentFormat", "N/A")}<br>
                    <strong>Estimated:</strong> {listing.get("MainFeatures", {}).get("EstimatedPayFormat", "N/A")}<br>
                    {school_html}
                    <a href=\"{zillow_link}\" target=\"_blank\">View on Zillow</a>
                </div>
                """

                price_group, color = price_to_category(price)
                all_points.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "popup": popup_html,
                    "color": color,
                    "price_group": price_group,
                    "school_group": school_group,
                })

    if not all_points:
        print("No coordinates found to map.")
        return

    map_center = [all_points[0]["lat"], all_points[0]["lon"]]
    m = folium.Map(location=map_center, zoom_start=11)

    price_groups: dict[str, folium.FeatureGroup] = {}
    school_groups: dict[str, folium.FeatureGroup] = {}

    for pt in all_points:
        pg = price_groups.setdefault(pt["price_group"], folium.FeatureGroup(name=pt["price_group"]))
        sg = school_groups.setdefault(pt["school_group"], folium.FeatureGroup(name=pt["school_group"]))
        marker = folium.Marker(
            location=[pt["lat"], pt["lon"]],
            popup=folium.Popup(pt["popup"], max_width=400),
            icon=folium.Icon(color=pt["color"], icon="home"),
        )
        marker.add_to(pg)
        marker.add_to(sg)

    for group in list(price_groups.values()) + list(school_groups.values()):
        group.add_to(m)

    folium.LayerControl().add_to(m)

    m.save(output_file)
    print(f"Saved map with {len(all_points)} pins to {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Scrape listings from Assumable.io")
    parser.add_argument("--output", type=str, default="listings.csv", help="CSV output filename")
    parser.add_argument("--map", action="store_true", help="Also generate a map with pins")
    parser.add_argument("--schools", action="store_true", help="Fetch Zillow school ratings")
    args = parser.parse_args()

    token: str = os.getenv("ASSUMABLE_TOKEN", "")
    remember_account: str = os.getenv("REMEMBER_ACCOUNT_NAME", "")
    cookies = {
        "XSRF-TOKEN": os.getenv("XSRF_TOKEN"),
        "cf_clearance": os.getenv("CF_CLEARANCE"),
        "botble_session": os.getenv("BOTBLE_SESSION"),
        "remember_account_" + remember_account: os.getenv("REMEMBER_ACCOUNT"),
    }

    first_response = fetch_listing_data(1, token, cookies)
    total_pages = first_response.get("SearchPagerBar", {}).get("TotalPages", 1)

    all_listings = first_response.get("MapList", {}).get("ListingsSummaryVM", [])

    for page in range(2, total_pages + 1):
        response = fetch_listing_data(page, token, cookies)
        all_listings.extend(response.get("MapList", {}).get("ListingsSummaryVM", []))

    if all_listings:
        if args.schools:
            prefetch_school_data(all_listings)
        write_listings_to_csv(all_listings, args.output)
        print(f"Saved {len(all_listings)} listings from {total_pages} pages to {args.output}")
        if args.map:
            generate_map_from_cache(fetch_schools=args.schools)
    else:
        print("No listings found.")

if __name__ == "__main__":
    main()
