"""NoBroker scraper — **logged-in** flow via a persistent browser session.

NoBroker renders only page chrome for anonymous crawlers; the actual listings
load from a bot-protected JSON API that only fires for a real, logged-in
session. So this scraper:

  1. Keeps a **persistent** Chromium profile under ``.nbsession/`` (gitignored),
     so you log in **once** and the cookies/session are reused on every run.
  2. On ``--login`` it opens a **headed** browser, you sign in manually
     (phone OTP / Google — handles CAPTCHA too), press Enter, and the session
     is saved to the profile dir.
  3. On a normal run it launches that profile **headless**, opens the buy/sale
     search pages for Whitefield + Marathahalli, and **intercepts the JSON
     responses** the SPA fetches. Listing objects are pulled out generically
     (any dict carrying a price-like + area-like field), so we don't depend on
     brittle DOM selectors.
  4. Matches are stored as **resale** listings and run through the same dedup +
     price-history pipeline as every other source.

Criteria (same as the rest of the project): villas from any builder are kept;
apartments must be a Tier-1 builder (fuzzy-matched against the seeded list).

Usage:
    python -m scrapers.nobroker --login     # one-time: sign in (headed)
    python -m scrapers.nobroker             # scrape using the saved session
    python -m scrapers.nobroker --headed    # scrape but show the browser
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import CRITERIA
from src.db import connect
from src import store
from src.normalize import (
    classify_locality,
    content_hash,
    looks_like_villa,
    match_builder,
    parse_inr_price,
    parse_size_sqft,
)

SOURCE = "NoBroker"
SESSION_DIR = Path(__file__).resolve().parent.parent / ".nbsession"
MATCH_THRESHOLD = 88  # rapidfuzz score to accept a Tier-1 builder for apartments

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Buy/sale search pages for the two target localities.
SEARCH_URLS = [
    "https://www.nobroker.in/property/sale/bangalore/Whitefield",
    "https://www.nobroker.in/property/sale/bangalore/Marathahalli",
]

# Substrings that flag a JSON response worth inspecting for listings.
API_HINTS = ("search", "property", "propertie", "listing", "filter", "getproperty")

# Keys NoBroker uses for the fields we care about (checked case-insensitively).
_PRICE_KEYS = ("price", "formattedprice", "expectedprice", "rent", "cost")
_AREA_KEYS = ("area", "propertysize", "builtuparea", "carpetarea", "size", "sqft")
_TITLE_KEYS = ("title", "heading", "propertytitle", "name", "seotitle")
_URL_KEYS = ("detailurl", "shorturl", "seourl", "url", "propertyurl", "sharelink")
_TYPE_KEYS = ("propertytype", "type", "buildingtype", "apartmenttype")
_LOCALITY_KEYS = ("locality", "location", "area", "localityname", "address")


def _lc_keys(d: dict) -> dict:
    return {k.lower(): v for k, v in d.items()}


def _first(d: dict, keys) -> object:
    lc = _lc_keys(d)
    for k in keys:
        if k in lc and lc[k] not in (None, "", []):
            return lc[k]
    # partial-match fallback (e.g. "superBuiltupArea" contains "area")
    for lk, v in lc.items():
        if any(k in lk for k in keys) and v not in (None, "", []):
            return v
    return None


def _looks_like_listing(d: dict) -> bool:
    lc = {k.lower() for k in d.keys()}
    has_price = any(any(p in k for p in _PRICE_KEYS) for k in lc)
    has_area = any(any(a in k for a in _AREA_KEYS) for k in lc)
    return has_price and has_area


def find_listing_dicts(obj, out: list) -> None:
    """Recursively collect dicts that look like property listings."""
    if isinstance(obj, dict):
        if _looks_like_listing(obj):
            out.append(obj)
        for v in obj.values():
            find_listing_dicts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_listing_dicts(v, out)


def _to_price_inr(raw) -> int | None:
    if isinstance(raw, (int, float)) and raw > 100000:  # already rupees
        return int(raw)
    return parse_inr_price(str(raw)) if raw is not None else None


def _to_size(raw) -> int | None:
    if isinstance(raw, (int, float)) and 200 <= raw <= 100000:
        return int(raw)
    return parse_size_sqft(str(raw)) if raw is not None else None


def _build_url(raw) -> str:
    s = str(raw or "")
    if s.startswith("http"):
        return s.split("?")[0]
    if s.startswith("/"):
        return "https://www.nobroker.in" + s.split("?")[0]
    return "https://www.nobroker.in/property/sale/bangalore"


def normalize_listing(d: dict) -> dict | None:
    price = _to_price_inr(_first(d, _PRICE_KEYS))
    title = str(_first(d, _TITLE_KEYS) or "").strip()[:120]
    url = _build_url(_first(d, _URL_KEYS))
    if not title and url:
        title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")[:120]
    ptype_raw = str(_first(d, _TYPE_KEYS) or "")
    loc_raw = str(_first(d, _LOCALITY_KEYS) or "")
    blob = f"{title} {ptype_raw} {loc_raw}"
    is_villa = looks_like_villa(blob) or "villa" in ptype_raw.lower() \
        or "independent" in ptype_raw.lower() or "row house" in ptype_raw.lower()
    slug = url.rstrip("/").rsplit("/", 1)[-1][:120] or content_hash(title)[:16]
    return {
        "slug": slug,
        "url": url,
        "name": title or "NoBroker listing",
        "price_inr": price,
        "size_sqft": _to_size(_first(d, _AREA_KEYS)),
        "locality": classify_locality(blob),
        "property_type": "villa" if is_villa else "apartment",
    }


def _is_logged_in(page) -> bool:
    """Heuristic: logged-out NoBroker shows a 'Log in' / 'Sign up' affordance."""
    try:
        body = page.inner_text("body")[:4000].lower()
    except Exception:  # noqa: BLE001
        return False
    return "log in" not in body and "login" not in body and "sign up" not in body


def login(verbose=True) -> int:
    SESSION_DIR.mkdir(exist_ok=True)
    import time
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(SESSION_DIR), headless=False, user_agent=UA,
            viewport={"width": 1366, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.nobroker.in/", wait_until="domcontentloaded")
        print("\n" + "=" * 68)
        print("  A Chrome window has opened. Please LOG IN to NoBroker now")
        print("  (phone OTP or Google). Complete any CAPTCHA if shown.")
        print("  I'll auto-detect once you're signed in and save the session.")
        print("  (You can also just close the window when done.)")
        print("=" * 68, flush=True)

        deadline = time.time() + 360  # up to 6 minutes to log in
        ok = False
        while time.time() < deadline:
            try:
                if page.is_closed():
                    break
                if _is_logged_in(page):
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)

        # Give NoBroker a moment to persist auth cookies to the profile dir.
        try:
            if not page.is_closed():
                page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"[NoBroker] session saved to {SESSION_DIR}"
          f"{' (looks logged in ✅)' if ok else ' (could not confirm — if scrape is empty, re-run --login)'}",
          flush=True)
    return 0


def run(headed=False, verbose=True) -> dict:
    if not SESSION_DIR.exists():
        print("[NoBroker] No saved session. Run:  python -m scrapers.nobroker --login")
        return {"portal": SOURCE, "kept": 0, "new": 0, "errors": 0, "outcome": ["no-session"]}

    conn = connect()
    source_id = store.get_source_id(conn, SOURCE)
    builders = store.load_builders(conn)
    run_id = store.start_run(conn, source_id)
    kept = new = updated = skipped = errors = 0
    outcome: list[str] = []
    captured: list = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(SESSION_DIR), headless=not headed, user_agent=UA,
            viewport={"width": 1366, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            try:
                url = resp.url.lower()
                if "application/json" not in (resp.headers.get("content-type", "")):
                    return
                if not any(h in url for h in API_HINTS):
                    return
                captured.append(resp.json())
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)

        logged_in = False
        for url in SEARCH_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                logged_in = logged_in or _is_logged_in(page)
                for _ in range(6):  # trigger infinite-scroll / lazy API calls
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(2000)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                outcome.append(f"{url} ERROR {type(exc).__name__}")
        ctx.close()

    if not logged_in:
        outcome.append("NOT-LOGGED-IN (session missing/expired) — run --login")
        if verbose:
            print("[NoBroker] session not authenticated — run:  python -m scrapers.nobroker --login")

    # Pull listing dicts out of every captured JSON blob, dedup by url.
    raw: list[dict] = []
    for blob in captured:
        find_listing_dicts(blob, raw)
    seen_urls: set[str] = set()
    records = []
    for d in raw:
        rec = normalize_listing(d)
        if not rec or rec["url"] in seen_urls:
            continue
        seen_urls.add(rec["url"])
        records.append(rec)

    if verbose:
        print(f"[NoBroker] captured {len(captured)} JSON payloads -> "
              f"{len(raw)} listing objects -> {len(records)} unique")

    for rec in records:
        # Criteria: villas any builder; apartments must be Tier-1.
        bname, score = match_builder(rec["name"], builders)
        is_tier1 = score >= MATCH_THRESHOLD
        if rec["property_type"] == "apartment" and not is_tier1:
            skipped += 1
            continue
        bid = store.builder_id(conn, bname) if is_tier1 else None
        try:
            _, is_new, pc = store.upsert_listing(
                conn, source_id=source_id, source_listing_id=rec["slug"], url=rec["url"],
                property_type=rec["property_type"], listing_type="resale", builder_id=bid,
                project_id=None, price_inr=rec["price_inr"], size_sqft=rec["size_sqft"],
                locality=rec["locality"], raw_address=None,
                description=f"{rec['name']} (via NoBroker)",
                content_hash=content_hash(SOURCE, rec["slug"], rec["name"]))
            kept += 1
            new += int(is_new)
            updated += int(not is_new)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if verbose:
                print(f"  store error {rec['slug']}: {type(exc).__name__}: {exc}")

    outcome.append(f"captured={len(captured)} records={len(records)} kept={kept} "
                   f"skipped_nontier1_apts={skipped}")
    store.finish_run(conn, run_id, found=len(records), new=new, updated=updated,
                     price_changed=0, errors=errors, log=" | ".join(outcome))
    store.mark_source_run(conn, source_id)
    conn.commit()
    conn.close()
    print(f"[NoBroker] run complete: kept={kept} new={new} "
          f"skipped_nontier1_apts={skipped} errors={errors}")
    return {"portal": SOURCE, "kept": kept, "new": new, "errors": errors, "outcome": outcome}


def main(argv: list[str]) -> int:
    if "--login" in argv:
        return login()
    return 0 if run(headed="--headed" in argv) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
