"""Reusable builder-site scraper base.

Most Tier-1 builder sites share the same shape: a city listing page linking to
per-project detail pages whose free text reliably contains a ₹ price, a
`PRM/KA/RERA/...` registration number, and a pincode-anchored address. This
module captures that pattern once; individual builders are declared as
`BuilderSpec`s in `scrapers.builders`.

Extraction is deliberately conservative (see per-field notes) — better a NULL
than a wrong value. The RERA reg no is the cross-source dedup key linking a
builder listing to the RERA registry project (source #1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from src.config import CRITERIA
from src.db import connect
from src import store
from src.normalize import (
    classify_locality,
    content_hash,
    extract_address,
    looks_like_villa,
    norm_bhk,
    norm_name,
    parse_inr_price,
    parse_size_sqft,
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
RERA_RE = re.compile(r"(PRM/KA/RERA/[\w/]+?)(?:\s|\)|<|$)")
BHK_RE = re.compile(r"([\d](?:\s*[,&]\s*\d)*\s*(?:&\s*\d\s*)?)\s*BHK", re.I)
TARGET_LOCALITIES = set(CRITERIA["localities"])


@dataclass
class BuilderSpec:
    """Declarative config for one builder site."""
    name: str                       # must match seeded builders/sources name
    list_urls: list[str]            # city listing page(s) to discover projects
    project_re: str                 # regex a project detail URL must match
    default_type: str = "apartment"

    @property
    def project_pattern(self) -> re.Pattern:
        return re.compile(self.project_re)


def discover_project_urls(page, spec: BuilderSpec) -> list[str]:
    urls: set[str] = set()
    for list_url in spec.list_urls:
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
        except Exception:  # noqa: BLE001 - a dead listing page shouldn't abort others
            continue
        hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.href)")
        for h in hrefs:
            if not h:
                continue
            clean = h.split("#")[0].split("?")[0]
            if spec.project_pattern.search(clean):
                urls.add(clean)
    return sorted(urls)


def scrape_project(page, url: str, spec: BuilderSpec) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    body = ""
    for _ in range(3):  # builder pages hydrate late
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        if "₹" in body or "INR" in body or "PRM/KA/RERA" in body:
            break
    h1 = page.query_selector("h1")
    name = h1.inner_text().strip() if h1 else url.rstrip("/").rsplit("/", 1)[-1]
    meta = " ".join(page.eval_on_selector_all(
        "meta[name='description'],meta[property='og:description'],meta[property='og:title']",
        "els => els.map(e => e.content || '')",
    ))
    rera = RERA_RE.search(body)
    bhk = BHK_RE.search(meta) or BHK_RE.search(body[:4000])
    address = extract_address(body)
    locality = classify_locality(address, meta)
    ptype = "villa" if looks_like_villa(name, meta) else spec.default_type
    return {
        "url": url,
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "name": name,
        "price_inr": parse_inr_price(body),
        "size_sqft": parse_size_sqft(body),
        "rera_id": rera.group(1) if rera else None,
        "bhk_text": bhk.group(1).strip() if bhk else None,
        "address": address,
        "locality": locality,
        "property_type": ptype,
    }


def run(spec: BuilderSpec, only_target=False, verbose=True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, spec.name)
    bid = store.builder_id(conn, spec.name)
    run_id = store.start_run(conn, source_id)
    found = kept = new = updated = price_changed = errors = linked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        urls = discover_project_urls(page, spec)
        found = len(urls)
        if verbose:
            print(f"[{spec.name}] discovered {found} Bengaluru project pages")

        for url in urls:
            try:
                rec = scrape_project(page, url, spec)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if verbose:
                    print(f"  ERROR {url}: {type(exc).__name__}: {exc}")
                continue
            if only_target and rec["locality"] not in TARGET_LOCALITIES:
                continue

            existing = None
            possession = None
            if rec["rera_id"]:
                existing = conn.execute(
                    "SELECT id, possession_date FROM projects WHERE rera_id = ?",
                    (rec["rera_id"],),
                ).fetchone()
            if existing:
                project_id, possession = existing[0], existing[1]
                linked += 1
                store.upsert_project(
                    conn, builder_id=bid, project_name_norm=norm_name(rec["name"]),
                    property_type=rec["property_type"], locality=rec["locality"],
                    rera_id=rec["rera_id"], possession_date=None,
                )
            else:
                project_id = store.upsert_project(
                    conn, builder_id=bid, project_name_norm=norm_name(rec["name"]),
                    property_type=rec["property_type"], locality=rec["locality"],
                    rera_id=rec["rera_id"], possession_date=None,
                )

            _, is_new, pc = store.upsert_listing(
                conn, source_id=source_id, source_listing_id=rec["slug"], url=rec["url"],
                property_type=rec["property_type"], listing_type="primary", builder_id=bid,
                project_id=project_id, price_inr=rec["price_inr"], size_sqft=rec["size_sqft"],
                bhk=norm_bhk(rec["bhk_text"]),
                possession_date=possession, locality=rec["locality"], raw_address=rec["address"],
                description=rec["name"],
                content_hash=content_hash(rec["rera_id"] or rec["slug"], rec["name"]),
            )
            kept += 1
            new += int(is_new)
            updated += int(not is_new)
            price_changed += int(pc)
            if verbose:
                price = f"₹{rec['price_inr']/1e7:.2f}Cr" if rec["price_inr"] else "—"
                tgt = "★" if rec["locality"] in TARGET_LOCALITIES else " "
                link = "↔RERA" if existing else "new"
                print(f"  {tgt}[{rec['property_type']:9}] {rec['name'][:32]:32} | "
                      f"{(rec['locality'] or '?'):13} | {price:>9} | {link}")
        browser.close()

    store.finish_run(conn, run_id, found=found, new=new, updated=updated,
                     price_changed=price_changed, errors=errors, log=f"linked_to_rera={linked}")
    store.mark_source_run(conn, source_id)
    conn.commit()
    summary = {"builder": spec.name, "found": found, "kept": kept, "new": new,
               "updated": updated, "price_changed": price_changed,
               "linked_to_rera": linked, "errors": errors}
    print(f"[{spec.name}] run complete: {summary}")
    conn.close()
    return summary
