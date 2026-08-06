"""Housiey aggregator scraper (source: Housiey).

Housiey exposes **locality landing pages** (e.g. `4-bhk-flats-in-whitefield-
bangalore`) that list new-launch projects, each linking to a rich `/projects/
<slug>` detail page carrying price range, size range, possession, config
(BHK/Villa) and — often — the `PRM/KA/RERA/...` number that lets us dedup a
Housiey listing against the RERA registry (source #1) and the builder scrapers.

Housiey currently has **Whitefield** inventory only (no Marathahalli), so we
crawl the larger-configuration Whitefield landing pages (3 BHK and up ≈ our
2000+ sq ft target) and tag every project with `whitefield`.

Criteria rule enforced here (matches the project spec): **villas from any
builder** are kept; **apartments are kept only when the builder resolves to a
Tier-1 name**. Everything else on the aggregator is skipped.

Usage:
    python -m scrapers.housiey
"""
from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from src.db import connect
from src import store
from src.normalize import (
    classify_locality,
    content_hash,
    extract_address,
    looks_like_villa,
    match_builder,
    norm_bhk,
    norm_name,
    parse_inr_price,
    parse_possession,
    parse_size_sqft,
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SOURCE = "Housiey"
PROJECT_RE = re.compile(r"housiey\.com/projects/[^/?#]+$")
RERA_RE = re.compile(r"(PRM/KA/RERA/[\w/]+?)(?:\s|\)|<|$)")
MATCH_THRESHOLD = 88  # rapidfuzz score to accept a Tier-1 builder match

# Whitefield landing pages for 3 BHK and larger (≈ 2000+ sq ft candidates).
LANDING = [
    ("whitefield", b, f"https://housiey.com/{b}-bhk-flats-in-whitefield-bangalore")
    for b in ["3", "3-5", "4", "4-5", "5"]
]

# Villa landing pages. Villas may be from ANY builder (per the criteria), so we
# crawl the Whitefield-specific villa list plus the broader Bangalore/East-
# Bangalore villa lists — this is what surfaces non-Tier-1 villa developers
# (NVT, DSR, Adarsh, etc.) we'd otherwise miss. Housiey mixes other-city
# projects into these lists, so a Bangalore-scope guard in run() drops those.
VILLA_LANDING = [
    "https://housiey.com/villas-in-whitefield-bangalore",
    "https://housiey.com/villas-in-marathahalli-bangalore",
    "https://housiey.com/villas-in-sarjapur-road-bangalore",
    "https://housiey.com/villas-in-varthur-bangalore",
    "https://housiey.com/villas-in-bangalore",
]


def discover(page, url: str) -> list[str]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4500)
    except Exception:  # noqa: BLE001
        return []
    hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.href)")
    return sorted({h.split("?")[0] for h in hrefs if h and PROJECT_RE.search(h.split("?")[0])})


def scrape_detail(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    body = ""
    for _ in range(3):
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        if "₹" in body or "PRM/KA/RERA" in body:
            break
    h1 = page.query_selector("h1")
    name = h1.inner_text().strip() if h1 else url.rstrip("/").rsplit("/", 1)[-1]
    rera = RERA_RE.search(body)
    # possession: parse the first "Mon YYYY" appearing just after "Possession"
    poss = None
    idx = body.find("Possession")
    if idx != -1:
        poss = parse_possession(body[idx:idx + 60])
    return {
        "url": url,
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "name": name,
        "price_inr": parse_inr_price(body),
        "size_sqft": parse_size_sqft(body),
        "rera_id": rera.group(1) if rera else None,
        "possession": poss,
        # Bangalore-scope guard: villa landing pages mix in other-city projects.
        "in_blr": ("bangalore" in body.lower() or "bengaluru" in body.lower()),
        # Classify from the project name + address; do NOT force the landing
        # locality (Housiey landing pages leak nearby-area projects). Names
        # usually carry the real locality (e.g. "…Whitefield", "…Sarjapur").
        "locality": classify_locality(name, extract_address(body)),
        "property_type": "villa" if looks_like_villa(name, body[:1500]) else "apartment",
    }


def run(verbose=True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, SOURCE)
    builders = store.load_builders(conn)
    run_id = store.start_run(conn, source_id)
    found = kept = new = updated = price_changed = errors = linked = skipped = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(user_agent=UA).new_page()

        targets: dict[str, str] = {}  # url -> locality (dedup across landing pages)
        bhk_seen: dict[str, set[int]] = {}  # url -> BHK numbers from landing pages
        for locality, btok, lurl in LANDING:
            nums = {int(n) for n in btok.split("-")}
            for u in discover(page, lurl):
                targets.setdefault(u, locality)
                bhk_seen.setdefault(u, set()).update(nums)
        for lurl in VILLA_LANDING:  # villa lists (any builder); locality from detail
            for u in discover(page, lurl):
                targets.setdefault(u, None)
        found = len(targets)
        if verbose:
            print(f"[Housiey] discovered {found} unique Whitefield project pages")

        for url in sorted(targets):
            try:
                rec = scrape_detail(page, url)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if verbose:
                    print(f"  ERROR {url}: {type(exc).__name__}: {exc}")
                continue

            bname, score = match_builder(rec["name"], builders)
            is_tier1 = score >= MATCH_THRESHOLD
            # Bangalore-scope guard: drop other-city projects Housiey mixes into
            # the villa lists (keep anything that resolved to a Bangalore locality).
            if not rec["in_blr"] and not rec["locality"]:
                skipped += 1
                continue
            # Criteria: apartments must be Tier-1; villas may be any builder.
            if rec["property_type"] == "apartment" and not is_tier1:
                skipped += 1
                continue
            bid = store.builder_id(conn, bname) if is_tier1 else None

            existing = None
            if rec["rera_id"]:
                existing = conn.execute(
                    "SELECT id, possession_date FROM projects WHERE rera_id = ?",
                    (rec["rera_id"],),
                ).fetchone()
            if existing:
                project_id = existing[0]
                possession = existing[1] or rec["possession"]
                linked += 1
                store.upsert_project(
                    conn, builder_id=bid, project_name_norm=norm_name(rec["name"]),
                    property_type=rec["property_type"], locality=rec["locality"],
                    rera_id=rec["rera_id"], possession_date=rec["possession"])
            else:
                possession = rec["possession"]
                project_id = store.upsert_project(
                    conn, builder_id=bid, project_name_norm=norm_name(rec["name"]),
                    property_type=rec["property_type"], locality=rec["locality"],
                    rera_id=rec["rera_id"], possession_date=rec["possession"])

            _, is_new, pc = store.upsert_listing(
                conn, source_id=source_id, source_listing_id=rec["slug"], url=rec["url"],
                property_type=rec["property_type"], listing_type="primary", builder_id=bid,
                project_id=project_id, price_inr=rec["price_inr"], size_sqft=rec["size_sqft"],
                bhk=norm_bhk("".join(map(str, sorted(bhk_seen.get(url, set())))) or None),
                possession_date=possession, locality=rec["locality"], raw_address=None,
                description=f"{rec['name']} (via Housiey)",
                content_hash=content_hash(rec["rera_id"] or rec["slug"], rec["name"]))
            kept += 1
            new += int(is_new)
            updated += int(not is_new)
            price_changed += int(pc)
            if verbose:
                price = f"₹{rec['price_inr']/1e7:.2f}Cr" if rec["price_inr"] else "—"
                b = bname if is_tier1 else "(non-tier1)"
                link = "↔RERA" if existing else "new"
                print(f"  ★[{rec['property_type']:9}] {rec['name'][:30]:30} | {b:14} | "
                      f"{price:>9} | {rec['possession'] or '?':10} | {link}")
        browser.close()

    store.finish_run(conn, run_id, found=found, new=new, updated=updated,
                     price_changed=price_changed, errors=errors,
                     log=f"linked_to_rera={linked} skipped_nontier1_apts={skipped}")
    store.mark_source_run(conn, source_id)
    conn.commit()
    summary = {"found": found, "kept": kept, "new": new, "updated": updated,
               "price_changed": price_changed, "linked_to_rera": linked,
               "skipped_nontier1_apts": skipped, "errors": errors}
    print(f"[Housiey] run complete: {summary}")
    conn.close()
    return summary


if __name__ == "__main__":
    run()
