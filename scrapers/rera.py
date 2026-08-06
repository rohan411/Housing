"""RERA scraper (source #1) — the authoritative registry anchor.

Pulls APPROVED projects for a district from the official Karnataka
Mahiti Kanaja RERA JSON API, filters to residential projects whose
possession falls on/before the criteria cutoff, resolves the promoter to a
Tier-1 builder (apartments) or keeps name-indicated villas (any builder),
and upserts them as canonical `projects` + primary `listings`.

RERA has no price/locality-per-unit; those are enriched later by builder
scrapers, matched via `rera_id` / project name. Dedup is natural: projects
are keyed on the unique RERA registration number.

Usage:
    python -m scrapers.rera                         # default: Bengaluru Urban, recent years
    python -m scrapers.rera --years 2024 2025
    python -m scrapers.rera --district "Bengaluru Urban" --min-score 88
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from playwright.sync_api import sync_playwright

from src.config import CRITERIA
from src.db import connect
from src import store
from src.normalize import (
    content_hash,
    epoch_ms_to_date,
    looks_like_villa,
    match_builder,
    norm_name,
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE_URL = ("https://mahitikanaja.karnataka.gov.in/Rera/GetProjectDetails"
            "?ServiceId=5501&Type=WEBAPI&DepartmentId=3160")
API_URL = "https://mahitikanaja.karnataka.gov.in/Rera/api/Rera/ProjectViewDetails"

RESIDENTIAL_TYPES = {"Residential/Group Housing", "Mixed Development"}
DEFAULT_YEARS = ("2023", "2024", "2025")


def fetch_year(ctx, district: str, year: str) -> list[dict]:
    """Call the RERA JSON API for one (district, year); return responseData."""
    body = json.dumps({"districtName": district, "status": "APPROVED", "year": year})
    payload = {
        "RequestBody": body, "ServiceID": 5501, "ReqType": "ReraProjectViewDetails",
        "districtName": district, "status": "APPROVED", "year": year,
    }
    resp = ctx.request.post(
        API_URL, data=json.dumps(payload),
        headers={"content-type": "application/json"}, timeout=90000,
    )
    outer = json.loads(resp.text())
    inner = json.loads(outer["Content"])
    if inner.get("code") != 200:
        raise RuntimeError(f"RERA API error for {district}/{year}: {inner.get('message')}")
    return inner.get("responseData", [])


def in_possession_window(completion: date | None, cutoff: date, today: date) -> bool:
    return bool(completion) and today <= completion <= cutoff


def classify(project_name: str, builder_name: str | None) -> tuple[str, str | None]:
    """Return (property_type, note). Villas allowed for any builder."""
    if looks_like_villa(project_name):
        return "villa", None
    return "apartment", None


def run(years=DEFAULT_YEARS, district="Bengaluru Urban", min_score=88, verbose=True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, "Karnataka RERA")
    builders = store.load_builders(conn)
    cutoff = date.fromisoformat(CRITERIA["possession_cutoff"])
    today = date.today()

    run_id = store.start_run(conn, source_id)
    found = kept = new = updated = errors = 0
    log_lines: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)  # establish session cookies

        for year in years:
            try:
                records = fetch_year(ctx, district, year)
            except Exception as exc:  # noqa: BLE001 - record and continue
                errors += 1
                log_lines.append(f"year {year}: FETCH ERROR {type(exc).__name__}: {exc}")
                continue
            found += len(records)

            for rec in records:
                if rec.get("projectType") not in RESIDENTIAL_TYPES:
                    continue
                completion = epoch_ms_to_date(rec.get("completionDate"))
                if not in_possession_window(completion, cutoff, today):
                    continue

                promoter = rec.get("promoterName") or ""
                bname, score = match_builder(promoter, builders)
                project_name = rec.get("projectName") or ""
                ptype, _ = classify(project_name, bname)

                is_tier1_match = score >= min_score
                # Apartments: Tier-1 only. Villas: any builder allowed.
                if ptype == "apartment" and not is_tier1_match:
                    continue
                if ptype == "villa" and not is_tier1_match:
                    bname = None  # keep villa, builder unresolved

                rera_id = rec.get("projectRegistrationNo")
                bid = store.builder_id(conn, bname) if is_tier1_match else None
                poss = completion.isoformat() if completion else None

                project_id = store.upsert_project(
                    conn, builder_id=bid, project_name_norm=norm_name(project_name),
                    property_type=ptype, locality=None, rera_id=rera_id, possession_date=poss,
                )
                _, is_new, _ = store.upsert_listing(
                    conn, source_id=source_id, source_listing_id=rera_id,
                    url=rec.get("certificatePath"), property_type=ptype, listing_type="primary",
                    builder_id=bid, project_id=project_id, possession_date=poss, locality=None,
                    raw_address=district, description=f"{promoter} | {project_name}",
                    content_hash=content_hash(rera_id, project_name, promoter),
                )
                kept += 1
                new += int(is_new)
                updated += int(not is_new)
                if verbose:
                    tag = bname or ("VILLA/any" if ptype == "villa" else "unmatched")
                    print(f"  [{ptype:9}] {tag:20} | {project_name[:38]:38} | {poss} | new={is_new}")

        browser.close()

    store.finish_run(conn, run_id, found=found, new=new, updated=updated,
                     price_changed=0, errors=errors, log="\n".join(log_lines))
    store.mark_source_run(conn, source_id)
    conn.commit()
    summary = {"found": found, "kept": kept, "new": new, "updated": updated, "errors": errors}
    print(f"\nRERA run complete: {summary}")
    if log_lines:
        print("notes:\n  " + "\n  ".join(log_lines))
    conn.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Karnataka RERA registry scraper")
    ap.add_argument("--years", nargs="+", default=list(DEFAULT_YEARS))
    ap.add_argument("--district", default="Bengaluru Urban")
    ap.add_argument("--min-score", type=int, default=88)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(years=tuple(args.years), district=args.district,
        min_score=args.min_score, verbose=not args.quiet)


if __name__ == "__main__":
    main()
