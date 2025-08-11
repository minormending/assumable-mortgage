import csv
import os
import argparse
from dotenv import load_dotenv
from curl_cffi import requests
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

def generate_map_from_cache(output_file="map.html"):
    cache_dir = Path(".cache")
    all_points = []

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
                except:
                    price = 0

                # Try to extract ZPID from PhotoLink
                photo_link = listing.get("PhotoLink", "")
                zpid = None
                address_link = listing.get("Location", "").replace(" ", "-").lower()
                if photo_link:
                    try:
                        zpid = photo_link.split("/")[-1].split("_")[0]
                    except IndexError:
                        pass
                zillow_link = f"https://www.zillow.com/homedetails/{address_link}/{zpid}_zpid/"

                def price_to_color(p):
                    if p >= 300_000:
                        return "red"
                    if p >= 200_000:
                        return "lightred"
                    elif p >= 100_000:
                        return "orange"
                    elif p > 0:
                        return "green"
                    return "gray"

                popup_html = f"""
                <div style="width:300px">
                    <img src="{photo_link}" alt="Property Image" style="width:100%; border-radius:6px; margin-bottom:8px;"><br>
                    <strong>{listing.get("PriceHtml", "N/A")}</strong><br>
                    <strong>Cash:</strong> {listing.get("CashFormat", "N/A")}<br>
                    <em>{listing.get("Location", "")}</em><br><br>
                    {listing.get("Content", "")}<br><br>
                    <strong>Rate:</strong> {listing.get("MainFeatures", {}).get("Rate", "N/A")}<br>
                    <strong>Monthly:</strong> {listing.get("MainFeatures", {}).get("PaymentFormat", "N/A")}<br>
                    <strong>Estimated:</strong> {listing.get("MainFeatures", {}).get("EstimatedPayFormat", "N/A")}<br>
                    <a href="{zillow_link}" target="_blank">View on Zillow</a>
                </div>
                """

                all_points.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "popup": popup_html,
                    "color": price_to_color(price)
                })

    if not all_points:
        print("No coordinates found to map.")
        return

    map_center = [all_points[0]["lat"], all_points[0]["lon"]]
    m = folium.Map(location=map_center, zoom_start=11)

    for pt in all_points:
        folium.Marker(
            location=[pt["lat"], pt["lon"]],
            popup=folium.Popup(pt["popup"], max_width=400),
            icon=folium.Icon(color=pt["color"], icon="home")
        ).add_to(m)

    m.save(output_file)
    print(f"Saved map with {len(all_points)} pins to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Scrape listings from Assumable.io")
    parser.add_argument("--output", type=str, default="listings.csv", help="CSV output filename")
    parser.add_argument("--map", action="store_true", help="Also generate a map with pins")
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
        write_listings_to_csv(all_listings, args.output)
        print(f"Saved {len(all_listings)} listings from {total_pages} pages to {args.output}")
        if args.map:
            generate_map_from_cache()
    else:
        print("No listings found.")

if __name__ == "__main__":
    main()
