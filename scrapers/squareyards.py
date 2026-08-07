"""SquareYards portal scraper (source: SquareYards).

SquareYards is one of the few large portals that serves **fully server-rendered
HTML** for its locality resale-search pages (no JS / anti-bot wall), so a plain
HTTP GET returns the whole listing grid. Each result URL encodes the key facts
in its slug, e.g.:

    /resale-3-bhk-servant-room-4100-sq-ft-villa-in-adarsh-palm-meadows/9473343
     └ listing_type └ bhk           └ size    └ type └ project        └ id

The price sits inline on the card, so we can parse the whole grid from the
search page without visiting every detail page. We crawl the Whitefield and
Marathahalli sale pages (paginated via `?page=N`) — our two target localities —
and keep only **apartments (Tier-1 builders only)** and **villas (any builder)**,
matching the project spec. Plots / office-space / builder-floor are dropped.

Dedup: listings are keyed by (source, numeric id); projects are linked by
normalised name (reusing an existing RERA-anchored project row when the name
matches a builder/RERA project already in the DB).

Usage:
    python -m scrapers.squareyards
"""
from __future__ import annotations

import re
import time
import urllib.request

from src.db import connect
from src import store
from src.normalize import (
    content_hash,
    match_builder,
    norm_bhk,
    norm_name,
    parse_inr_price,
)

SOURCE = "SquareYards"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MATCH_THRESHOLD = 88  # rapidfuzz score to accept a Tier-1 builder match
MAX_PAGES = 6         # per locality; stop early when a page adds nothing new

# Target localities -> SquareYards sale-search slug.
SEARCH = {
    "whitefield":   "https://www.squareyards.com/sale/property-for-sale-in-whitefield-bangalore",
    "marathahalli": "https://www.squareyards.com/sale/property-for-sale-in-marathahalli-bangalore",
}

# Any `/<verb>-...-<size>-sq-ft-<type>-in-<project>/<id>` listing link.
LISTING_RE = re.compile(
    r"squareyards\.com/([a-z]+)-([a-z0-9-]*?)(\d[\d,]*)-sq-ft-"
    r"(apartment|villa|independent-house|builder-floor|residential-plot|plot|office-space|penthouse)"
    r"-in-([a-z0-9-]+)/(\d+)",
    re.I,
)
BHK_RE = re.compile(r"(\d(?:-\d)?)-bhk", re.I)
RERA_RE = re.compile(r"(PRM/KA/RERA/[\w/]+?)(?:\s|\)|<|\.|$)")

KEEP_TYPES = {"apartment", "villa", "independent-house", "penthouse"}
VILLA_TYPES = {"villa", "independent-house"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (trusted host)
        return r.read().decode("utf-8", "replace")


def parse_cards(html: str, locality: str) -> list[dict]:
    """Extract one record per listing card from a search-results page."""
    # First position of each listing id, in document order (a card repeats its
    # link for image + title; we slice from this id's start to the next id's).
    hits = []  # (pos, match)
    seen = set()
    for m in LISTING_RE.finditer(html):
        lid = m.group(6)
        if lid in seen:
            continue
        seen.add(lid)
        hits.append((m.start(), m))
    records = []
    for i, (pos, m) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else min(pos + 4000, len(html))
        card = html[pos:end]
        verb, pre, size_s, ptype, project, lid = m.groups()
        ptype = ptype.lower()
        if ptype not in KEEP_TYPES:
            continue
        prop_type = "villa" if ptype in VILLA_TYPES else "apartment"
        size = int(size_s.replace(",", "")) or None
        bhk_m = BHK_RE.search(m.group(0))
        bhk = norm_bhk(bhk_m.group(1)) if bhk_m else None
        rera_m = RERA_RE.search(card)
        records.append({
            "id": lid,
            "full_url": "https://www." + m.group(0),
            "listing_type": "resale" if verb.lower() == "resale" else "primary",
            "property_type": prop_type,
            "size_sqft": size,
            "bhk": bhk,
            "project": project.replace("-", " ").strip(),
            "price_inr": parse_inr_price(card),
            "rera_id": rera_m.group(1) if rera_m else None,
            "locality": locality,
        })
    return records


def link_project(conn, *, builder_id, name_norm, property_type, locality, rera_id, possession):
    """Reuse a project row by RERA (via store) or by normalised name; else insert."""
    row = None
    if not rera_id:
        row = conn.execute(
            "SELECT id FROM projects WHERE project_name_norm = ? LIMIT 1", (name_norm,)
        ).fetchone()
    if row:
        return row[0]
    return store.upsert_project(
        conn, builder_id=builder_id, project_name_norm=name_norm,
        property_type=property_type, locality=locality, rera_id=rera_id,
        possession_date=possession)


def run(verbose=True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, SOURCE)
    builders = store.load_builders(conn)
    run_id = store.start_run(conn, source_id)
    found = kept = new = updated = price_changed = errors = skipped = 0
    seen_ids: set[str] = set()

    for locality, base in SEARCH.items():
        for page in range(1, MAX_PAGES + 1):
            url = base if page == 1 else f"{base}?page={page}"
            try:
                html = fetch(url)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if verbose:
                    print(f"  ERROR {url}: {type(exc).__name__}: {exc}")
                break
            cards = parse_cards(html, locality)
            fresh = [c for c in cards if c["id"] not in seen_ids]
            if not fresh:
                break  # pagination exhausted (no new ids on this page)
            for c in fresh:
                seen_ids.add(c["id"])
                found += 1
                bname, score = match_builder(c["project"], builders)
                is_tier1 = score >= MATCH_THRESHOLD
                # Criteria: apartments must be Tier-1; villas may be any builder.
                if c["property_type"] == "apartment" and not is_tier1:
                    skipped += 1
                    continue
                bid = store.builder_id(conn, bname) if is_tier1 else None
                name_norm = norm_name(c["project"])
                project_id = link_project(
                    conn, builder_id=bid, name_norm=name_norm,
                    property_type=c["property_type"], locality=c["locality"],
                    rera_id=c["rera_id"], possession=None)
                _, is_new, pc = store.upsert_listing(
                    conn, source_id=source_id, source_listing_id=c["id"],
                    url=c["full_url"], property_type=c["property_type"],
                    listing_type=c["listing_type"], builder_id=bid,
                    project_id=project_id, price_inr=c["price_inr"],
                    size_sqft=c["size_sqft"], bhk=c["bhk"],
                    possession_date=None, locality=c["locality"], raw_address=None,
                    description=f"{c['project'].title()} — {c['bhk'] or ''} {c['property_type']} (via SquareYards, resale)".strip(),
                    content_hash=content_hash(c["rera_id"] or c["id"], name_norm))
                kept += 1
                new += int(is_new)
                updated += int(not is_new)
                price_changed += int(pc)
                if verbose:
                    price = f"₹{c['price_inr']/1e7:.2f}Cr" if c["price_inr"] else "—"
                    b = bname if is_tier1 else "(non-tier1)"
                    print(f"  ★[{c['property_type']:9}] {c['project'][:28]:28} | {b:12} | "
                          f"{price:>9} | {c['bhk'] or '?':8} | {c['size_sqft'] or '?'} sqft | {locality}")
            time.sleep(1.0)  # be polite

    store.finish_run(conn, run_id, found=found, new=new, updated=updated,
                     price_changed=price_changed, errors=errors,
                     log=f"skipped_nontier1_apts={skipped}")
    store.mark_source_run(conn, source_id)
    conn.commit()
    summary = {"found": found, "kept": kept, "new": new, "updated": updated,
               "price_changed": price_changed, "skipped_nontier1_apts": skipped,
               "errors": errors}
    print(f"[SquareYards] run complete: {summary}")
    conn.close()
    return summary


if __name__ == "__main__":
    run()
