"""NoBroker scraper — anonymous **radius-filter API** via a real Chromium session.

NoBroker renders only page chrome for anonymous crawlers and its canonical
``/property/sale/...`` SEO pages are thin shells (no embedded listings), so DOM
scraping yields nothing. Its *locality-search* ``filter`` endpoint also returns
an empty envelope when called with only ``city=bangalore``.

The breakthrough this scraper uses: the **radius** form of the same endpoint —

    /api/v3/multi/property/BUY/filter?latitude=<lat>&longitude=<lon>&radius=<km>
        &pageNo=<n>&city=bangalore

returns the **full listing JSON anonymously** (rich records/page) as long as the
request carries the SPA's headers (``appversion`` + a nobroker ``referer``). We
drive a real headless Chromium (Playwright) purely to (a) obtain valid NoBroker
cookies from a homepage visit and (b) issue the API calls through the browser's
request context, so no manual login / OTP is required.

For each target locality we page through the radius results and keep:
  * **apartments** only if they meet the *full* project criteria — a Tier-1
    builder AND 2000+ sq.ft AND price in the CRITERIA band (resale ready units
    satisfy the "possession by mid-2028" rule);
  * **villas** with **relaxed** criteria — any builder, any size/price — since
    premium villa resale inventory is thin and the user asked to relax it.

Everything is stored as **resale** and flows through the same dedup +
price-history pipeline as every other source.

Usage:
    python -m scrapers.nobroker            # scrape (headless, no login needed)
    python -m scrapers.nobroker --headed   # same, but show the browser
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

from src.config import CRITERIA
from src.db import connect
from src import store
from src.normalize import (
    classify_locality,
    content_hash,
    looks_like_villa,
    match_builder,
    norm_bhk,
)

SOURCE = "NoBroker"
MATCH_THRESHOLD = 88  # rapidfuzz score to accept a Tier-1 builder for apartments

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Villa search centres (wide radius below). Kept tight to the two core areas;
# the 25km villa radius already reaches the whole ORR belt from here.
LOCALITIES = {
    "whitefield":   (12.9698, 77.7499),
    "marathahalli": (12.9591, 77.6974),
}
# Apartment search centres — the ORR belt (Sarjapur -> Bellandur -> Marathahalli
# -> Whitefield) plus a north (Hebbal) and an east (KR Puram) anchor. With a
# 7km radius these overlapping circles cover the whole corridor the user cares
# about while the Tier-1 + 2000sqft + price-band filters keep the results clean.
APT_LOCALITIES = {
    "sarjapur":     (12.8646, 77.7866),
    "bellandur":    (12.9260, 77.6762),
    "marathahalli": (12.9591, 77.6974),
    "whitefield":   (12.9698, 77.7499),
    "hebbal":       (13.0358, 77.5970),   # north Bangalore
    "kr puram":     (13.0075, 77.6957),   # east Bangalore / Old Madras Road
}
APT_RADIUS_KM = 7       # wider radius for apartments (ORR corridor)
VILLA_RADIUS_KM = 25    # wide radius for villas (premium villa stock is thin;
                        # user asked NOT to restrict villas by radius or price)
MAX_PAGES = 25          # per locality; stop early when a page returns no data
API = ("https://www.nobroker.in/api/v3/multi/property/BUY/filter"
       "?latitude={lat}&longitude={lon}&radius={r}&pageNo={pg}&city=bangalore")
# Headers the SPA sends; without these the endpoint returns an empty envelope.
API_HEADERS = {
    "appversion": "2.0",
    "referer": "https://www.nobroker.in/property/sale/bangalore",
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json",
}

_MIN_SQFT = CRITERIA["min_sqft"]
_PRICE_MIN = CRITERIA["price_min_inr"]
_PRICE_MAX = CRITERIA["price_max_inr"]


def _bhk_from_type(t):
    """NoBroker encodes BHK as e.g. 'BHK3' / 'BHK2'."""
    if not t:
        return None
    digits = "".join(ch for ch in str(t) if ch.isdigit())
    return norm_bhk(f"{digits} BHK") if digits else None


def _detail_url(d: dict) -> str:
    u = str(d.get("detailUrl") or d.get("shortUrl") or "")
    if u.startswith("http"):
        return u.split("?")[0]
    if u.startswith("/"):
        return "https://www.nobroker.in" + u.split("?")[0]
    return "https://www.nobroker.in/property/sale/bangalore"


def normalize_listing(d: dict):
    title = str(d.get("propertyTitle") or d.get("title") or "").strip()[:160]
    if not title:
        return None
    loc_raw = str(d.get("locality") or d.get("localityTruncated") or "")
    project = str(d.get("projectUrl") or "")
    blob = f"{title} {loc_raw} {project}"
    is_villa = looks_like_villa(blob) or any(
        w in title.lower() for w in ("villa", "independent house", "row house", "villament")
    )
    price = d.get("price")
    size = d.get("propertySize")
    return {
        "id": str(d.get("id") or content_hash(title)[:24]),
        "url": _detail_url(d),
        "name": title,
        "price_inr": int(price) if isinstance(price, (int, float)) and price > 0 else None,
        "size_sqft": int(size) if isinstance(size, (int, float)) and size > 0 else None,
        "bhk": _bhk_from_type(d.get("type")),
        "locality": classify_locality(blob) or (loc_raw.lower() or None),
        "property_type": "villa" if is_villa else "apartment",
    }


def _fetch_locality(ctx, lat: float, lon: float, radius: float) -> list:
    out: list = []
    for pg in range(1, MAX_PAGES + 1):
        ep = API.format(lat=lat, lon=lon, r=radius, pg=pg)
        try:
            r = ctx.request.get(ep, headers=API_HEADERS, timeout=25000)
            data = (r.json() or {}).get("data") or []
        except Exception:  # noqa: BLE001
            break
        if not data:
            break
        out.extend(data)
        if len(data) < 10:  # last (partial) page
            break
    return out


def run(headed: bool = False, verbose: bool = True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, SOURCE)
    builders = store.load_builders(conn)
    run_id = store.start_run(conn, source_id)
    found = kept = new = updated = price_changed = errors = 0
    skipped_apt = skipped_villa_loc = 0
    seen: set = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        try:
            page.goto("https://www.nobroker.in/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            pass

        # Pass 1: apartment corridor -> apartments (strict) + villas that fall
        #         within these tighter circles.
        # Pass 2: wide radius -> villas only (relaxed; no radius/price limit).
        # Each raw record is tagged with whether it came from the wide pass so
        # the filter loop can drop wide-pass apartments (they must stay within
        # the ORR corridor); villas are accepted from either pass.
        raw: list = []
        for loc, (lat, lon) in APT_LOCALITIES.items():
            recs = _fetch_locality(ctx, lat, lon, APT_RADIUS_KM)
            if verbose:
                print(f"[NoBroker] {loc}: fetched {len(recs)} raw listings "
                      f"(radius {APT_RADIUS_KM} km)")
            raw.extend((d, False) for d in recs)
        for loc, (lat, lon) in LOCALITIES.items():
            recs = _fetch_locality(ctx, lat, lon, VILLA_RADIUS_KM)
            if verbose:
                print(f"[NoBroker] {loc}: fetched {len(recs)} raw listings "
                      f"(villa radius {VILLA_RADIUS_KM} km)")
            raw.extend((d, True) for d in recs)
        browser.close()

    for d, wide in raw:
        rec = normalize_listing(d)
        if not rec or rec["id"] in seen:
            continue
        # Wide pass contributes villas only; its apartments are too far out.
        if wide and rec["property_type"] != "villa":
            continue
        seen.add(rec["id"])
        found += 1

        bname, score = match_builder(rec["name"], builders)
        is_tier1 = score >= MATCH_THRESHOLD

        if rec["property_type"] == "apartment":
            if not is_tier1:
                skipped_apt += 1
                continue
            if not rec["size_sqft"] or rec["size_sqft"] < _MIN_SQFT:
                skipped_apt += 1
                continue
            if not rec["price_inr"] or not (_PRICE_MIN <= rec["price_inr"] <= _PRICE_MAX):
                skipped_apt += 1
                continue
            bid = store.builder_id(conn, bname)
        else:
            if rec["locality"] is None:
                skipped_villa_loc += 1
                continue
            bid = store.builder_id(conn, bname) if is_tier1 else None

        try:
            _, is_new, pc = store.upsert_listing(
                conn, source_id=source_id, source_listing_id=rec["id"], url=rec["url"],
                property_type=rec["property_type"], listing_type="resale", builder_id=bid,
                project_id=None, bhk=rec["bhk"], size_sqft=rec["size_sqft"],
                price_inr=rec["price_inr"], locality=rec["locality"], raw_address=None,
                description=f"{rec['name']} (via NoBroker)",
                content_hash=content_hash(SOURCE, rec["id"], rec["name"]))
            kept += 1
            new += int(is_new)
            updated += int(not is_new)
            price_changed += int(pc)
            if verbose:
                pr = f"₹{rec['price_inr']/1e7:.2f}Cr" if rec["price_inr"] else "—"
                b = bname if bid else "(any)"
                sz = f"{rec['size_sqft']}sqft" if rec["size_sqft"] else "—"
                print(f"  ★[{rec['property_type']:9}] {rec['name'][:34]:34} | {b:12} | "
                      f"{pr:>9} | {sz:>9} | {rec['locality']}")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if verbose:
                print(f"  store error {rec['id']}: {type(exc).__name__}: {exc}")

    store.finish_run(conn, run_id, found=found, new=new, updated=updated,
                     price_changed=price_changed, errors=errors,
                     log=f"skipped_apts={skipped_apt} skipped_villa_noloc={skipped_villa_loc}")
    store.mark_source_run(conn, source_id)
    conn.commit()
    conn.close()
    print(f"[NoBroker] run complete: found={found} kept={kept} new={new} "
          f"updated={updated} price_changed={price_changed} "
          f"skipped_apts={skipped_apt} errors={errors}")
    return {"portal": SOURCE, "found": found, "kept": kept, "new": new, "errors": errors}


def main(argv: list) -> int:
    return 0 if run(headed="--headed" in argv) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
