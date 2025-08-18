from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import folium
from folium.plugins import TagFilterButton

from ..clients.greatschools import GreatSchoolsClient

log = logging.getLogger(__name__)


# ---------------------------
# Data structures and helpers
# ---------------------------

@dataclass(frozen=True)
class MapPoint:
    """A map point for a property pin.

    Attributes:
        lat: Latitude of the property.
        lon: Longitude of the property.
        popup_html: Pre-rendered HTML content for the popup.
        color: Marker color string for folium.Icon.
        group: Human-readable bucket used for feature grouping.
    """

    lat: float
    lon: float
    popup_html: str
    color: str
    group: str


def _price_to_category(price: int) -> tuple[str, str]:
    """Bucket price into a label and color."""
    if price >= 300_000:
        return "$300k+", "red"
    if price >= 200_000:
        return "$200k - $299k", "lightred"
    if price >= 100_000:
        return "$100k - $199k", "orange"
    if price > 0:
        return "Cash < $100k", "green"
    return "Unknown", "gray"


def _safe_int_from_money(value: Any) -> int:
    """Parse currency-like strings (e.g. "$123,456") into an int.

    Returns 0 on failure or empty input to keep downstream logic simple.
    """
    try:
        s = str(value).replace("$", "").replace(",", "").strip()
        return int(float(s)) if s else 0
    except Exception:
        return 0


def _listing_to_point(listing: dict[str, Any]) -> MapPoint | None:
    """Convert a raw listing dict into a MapPoint, or None if invalid."""
    centroid = listing.get("Centroid", {})
    lat = centroid.get("latitude")
    lon = centroid.get("longitude")
    if not lat or not lon:
        return None

    price = _safe_int_from_money(listing.get("CashFormat", ""))

    photo_link = listing.get("PhotoLink", "")
    zpid: str | None = None
    address_link = listing.get("Location", "").replace(" ", "-").lower()
    if photo_link:
        try:
            zpid = photo_link.split("/")[-1].split("_")[0]
        except Exception:
            zpid = None
    zillow_suffix = f"/{zpid}_zpid/" if zpid else "/"
    zillow_link = f"https://www.zillow.com/homedetails/{address_link}{zillow_suffix}"

    popup_html = f"""
    <div style=\"width:300px\">
        <img src=\"{photo_link}\" alt=\"Property Image\" style=\"width:100%; border-radius:6px; margin-bottom:8px;\"><br>
        <strong>{listing.get("PriceHtml", "N/A")}</strong><br>
        <strong>Cash:</strong> {listing.get("CashFormat", "N/A")}<br>
        <em>{listing.get("Location", "")}</em><br><br>
        {listing.get("Content", "")}<br><br>
        <strong>Rate:</strong> {listing.get("MainFeatures", {}).get("Rate", "N/A")}<br>
        <strong>Monthly:</strong> {listing.get("MainFeatures", {}).get("PaymentFormat", "N/A")}<br>
        <strong>Estimated:</strong> {listing.get("MainFeatures", {}).get("EstimatedPayFormat", "N/A")}<br>
        <a href=\"{zillow_link}\" target=\"_blank\">View on Zillow</a>
    </div>
    """

    group, color = _price_to_category(price)
    return MapPoint(lat=float(lat), lon=float(lon), popup_html=popup_html, color=color, group=group)


def _load_points_from_cache(cache_dir: str) -> list[MapPoint]:
    """Load and transform cached listing pages into map points."""
    start = time.perf_counter()
    cache = Path(cache_dir)
    files = sorted(cache.glob("page_*.json"))
    log.info("map.cache_scan", extra={"files": len(files), "dir": str(cache)})

    points: list[MapPoint] = []
    skipped = 0
    for cache_file in files:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            log.exception("map.cache_read_failed", extra={"file": cache_file.name})
            continue

        listings = (
            data.get("response", {}).get("MapList", {}).get("ListingsSummaryVM", [])
        )
        log.debug("map.cache_page", extra={"file": cache_file.name, "listings": len(listings)})

        for listing in listings:
            pt = _listing_to_point(listing)
            if pt is None:
                skipped += 1
                continue
            points.append(pt)

    dur_ms = int((time.perf_counter() - start) * 1000)
    log.info(
        "map.cache_loaded",
        extra={"points": len(points), "skipped": skipped, "ms": dur_ms},
    )
    return points


def _add_property_layers(m: folium.Map, points: Iterable[MapPoint]) -> dict[str, folium.FeatureGroup]:
    """Add property markers grouped by price bucket; return created groups."""
    groups: dict[str, folium.FeatureGroup] = {}
    per_group_count: dict[str, int] = {}

    for pt in points:
        group = groups.setdefault(pt.group, folium.FeatureGroup(name=pt.group))
        folium.Marker(
            location=[pt.lat, pt.lon],
            popup=folium.Popup(pt.popup_html, max_width=400),
            icon=folium.Icon(color=pt.color, icon="home"),
        ).add_to(group)
        per_group_count[pt.group] = per_group_count.get(pt.group, 0) + 1

    for g in groups.values():
        g.add_to(m)

    log.info("map.property_groups", extra=per_group_count)
    return groups


def _rating_to_color(rating: int | None) -> str:
    """Map school rating to a color distinct from property pins.

    Use a blue/purple scale to avoid confusion with property colors
    (red/orange/green/gray). Supported marker colors align with
    Leaflet.awesome-markers via folium.Icon.
    """
    if rating is None:
        return "lightgray"
    if rating >= 9:
        return "darkblue"
    if rating >= 7:
        return "blue"
    if rating >= 5:
        return "cadetblue"
    if rating >= 3:
        return "purple"
    return "darkpurple"


def _normalize_school_type(school: dict[str, Any]) -> str:
    """Return a stable school type label: public | charter | private.

    Falls back using boolean hints when explicit type is missing.
    """
    st_raw = (
        school.get("schoolType")
        or school.get("school_type")
        or school.get("type")
    )
    label: str | None = None
    if isinstance(st_raw, str):
        s = st_raw.strip().lower()
        if s in {"public", "private", "charter"}:
            label = s
        elif s in {"district", "magnet"}:
            label = "public"
        elif s in {"religious", "parochial"}:
            label = "private"
    if not label:
        is_private = school.get("isPrivate")
        is_charter = school.get("isCharter")
        if is_private is True:
            label = "private"
        elif is_charter is True:
            label = "charter"
        else:
            label = "public"
    return label

def _add_schools_layer(
    m: folium.Map, lat: float, lon: float, gs_client: GreatSchoolsClient | None
) -> None:
    """Fetch schools and add markers with a rating filter.

    Adds a FeatureGroup named "Schools" containing school markers. Each school
    marker is tagged with its rating (e.g., "10", "9", ..., "N/A"). A
    TagFilterButton control is attached to the map to provide a multi-select UI
    where selected ratings are combined with OR semantics.
    """
    if not gs_client:
        log.debug("map.schools_skipped", extra={"reason": "no_client"})
        return

    start = time.perf_counter()
    try:
        schools = gs_client.fetch_schools(lat, lon).get("items", [])
    except Exception:
        log.exception("map.schools_failed_fetch")
        return

    log.info("map.schools_loaded", extra={"count": len(schools)})

    # Group schools together and collect available rating tags
    schools_group = folium.FeatureGroup(name="Schools")
    rating_tags: set[str] = set()
    type_tags: set[str] = set()

    for school in schools:
        s_lat = school.get("lat")
        s_lon = school.get("lon")
        if s_lat is None or s_lon is None:
            continue

        rating_raw = school.get("rating")
        if isinstance(rating_raw, (int, float)):
            rating: int | None = int(rating_raw)
        elif isinstance(rating_raw, str) and rating_raw.strip().isdigit():
            rating = int(rating_raw.strip())
        else:
            rating = None
        # Normalize type to stable buckets
        school_type = _normalize_school_type(school)
        type_tag = school_type
        type_tags.add(type_tag)
        address = school.get("address", {})
        popup_html = f"""
        <div style='width:250px'>
            <strong>{school.get("name", "")}</strong><br>
            Rating: {rating if rating is not None else 'N/A'}<br>
            Type: {school_type}<br>
            {address.get("street1", '')}, {address.get("city", '')}
        </div>
        """
        # Build rating tag string for filter
        rating_tag = str(rating) if rating is not None else "N/A"
        rating_tags.add(rating_tag)

        # Attach `tags` to marker options so TagFilterButton can filter them
        folium.Marker(
            location=[s_lat, s_lon],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color=_rating_to_color(rating), icon="graduation-cap", prefix="fa"),
            tags=[rating_tag, type_tag],  # consumed by leaflet-tag-filter-button
        ).add_to(schools_group)

    # Add the schools layer to the map (even if empty to keep controls stable)
    schools_group.add_to(m)

    # Add a multi-select OR filter for ratings if we have any tags
    if rating_tags:
        try:
            TagFilterButton(
                data=sorted(rating_tags, key=lambda x: (x == "N/A", -int(x) if x.isdigit() else 0)),
                icon="fa-filter",
                clear_text="clear",
                # Ensure OR semantics when multiple ratings are selected
                filter_type="or",  # gracefully ignored if plugin version doesn't support it
                position="topleft",
            ).add_to(m)
        except Exception:
            # If the plugin API differs, fall back silently; the map still works
            log.exception("map.schools_tagfilter_failed")

    # Add a separate school type filter; within this control selects are OR,
    # and combined with the ratings control the result is AND (intersection),
    # because both controls hide non-matching markers independently.
    if type_tags:
        try:
            TagFilterButton(
                data=sorted(type_tags),
                icon="fa-school",
                clear_text="clear",
                filter_type="or",
                position="topleft",
            ).add_to(m)
        except Exception:
            log.exception("map.schools_type_tagfilter_failed")

    dur_ms = int((time.perf_counter() - start) * 1000)
    log.info(
        "map.schools_summary",
        extra={"ms": dur_ms},
    )


# --------------
# Public API
# --------------

def generate_map_from_cache(
    cache_dir: str = ".cache",
    output_file: str = "map.html",
    gs_client: GreatSchoolsClient | None = None,
) -> None:
    """Generate a folium map from cached listing JSON and save to HTML.

    Improves readability and observability by:
      - splitting logic into focused helpers,
      - adding type hints and dataclasses for clarity,
      - emitting structured logs with counts and durations.
    """

    build_start = time.perf_counter()

    points = _load_points_from_cache(cache_dir)
    if not points:
        log.warning("map.no_points")
        print("No coordinates found to map.")
        return

    map_center = [points[0].lat, points[0].lon]
    m = folium.Map(location=map_center, zoom_start=11)

    _add_property_layers(m, points)
    _add_schools_layer(m, map_center[0], map_center[1], gs_client)

    folium.LayerControl().add_to(m)

    save_start = time.perf_counter()
    m.save(output_file)

    # Post-process generated HTML to guard TagFilter plugin usage so a missing plugin
    # does not break the rest of the map (e.g., LayerControl overlays).
    try:
        html = Path(output_file).read_text(encoding="utf-8")
        # Replace direct constructor call with a safe no-op fallback if plugin missing
        html = html.replace(
            "L.control.tagFilterButton(",
            "(L && L.control && L.control.tagFilterButton ? L.control.tagFilterButton : function(){return {addTo:function(){}}})(",
        )
        Path(output_file).write_text(html, encoding="utf-8")
    except Exception:
        log.exception("map.postprocess_tagfilter_guard_failed", extra={"file": output_file})
    save_ms = int((time.perf_counter() - save_start) * 1000)
    total_ms = int((time.perf_counter() - build_start) * 1000)

    log.info(
        "map.saved",
        extra={"file": output_file, "pins": len(points), "save_ms": save_ms, "total_ms": total_ms},
    )
    print(f"Saved map with {len(points)} pins to {output_file}")
