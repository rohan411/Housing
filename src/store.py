"""Persistence layer: upserts for projects/listings with dedup + price history.

Dedup rules enforced here:
  * projects  — natural key `rera_id` (UNIQUE). Same RERA no => same project.
  * listings  — natural key (source_id, source_listing_id). Re-scrapes update
                the same row; price changes append to `price_history`.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def get_source_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    if not row:
        raise ValueError(f"Unknown source: {name!r} (run `python -m src.db --init`)")
    return row[0]


def load_builders(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {r["name"]: json.loads(r["aliases"]) for r in conn.execute("SELECT name, aliases FROM builders")}


def builder_id(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM builders WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def upsert_project(conn, *, builder_id, project_name_norm, property_type,
                   locality, rera_id, possession_date) -> int:
    """Insert-or-update a canonical project keyed by rera_id. Returns project id."""
    if rera_id:
        existing = conn.execute("SELECT id FROM projects WHERE rera_id = ?", (rera_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE projects SET builder_id=COALESCE(?, builder_id), "
                "property_type=COALESCE(?, property_type), locality=COALESCE(?, locality), "
                "possession_date=COALESCE(?, possession_date) WHERE id=?",
                (builder_id, property_type, locality, possession_date, existing[0]),
            )
            return existing[0]
    cur = conn.execute(
        "INSERT INTO projects (builder_id, project_name_norm, property_type, locality, "
        "rera_id, possession_date) VALUES (?,?,?,?,?,?)",
        (builder_id, project_name_norm, property_type, locality, rera_id, possession_date),
    )
    return cur.lastrowid


def upsert_listing(conn, *, source_id, source_listing_id, url, property_type, listing_type,
                   builder_id=None, project_id=None, tower=None, unit_no=None, bhk=None,
                   size_sqft=None, price_inr=None, price_per_sqft=None, possession_date=None,
                   floor=None, facing=None, locality=None, raw_address=None, description=None,
                   content_hash=None) -> tuple[int, bool, bool]:
    """Insert or update a listing. Returns (listing_id, is_new, price_changed)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT id, price_inr FROM listings WHERE source_id=? AND source_listing_id=?",
        (source_id, source_listing_id),
    ).fetchone()

    if row:
        listing_id, old_price = row[0], row[1]
        price_changed = price_inr is not None and price_inr != old_price
        conn.execute(
            "UPDATE listings SET url=?, property_type=?, listing_type=?, builder_id=?, project_id=?, "
            "tower=?, unit_no=?, bhk=?, size_sqft=?, price_inr=?, price_per_sqft=?, possession_date=?, "
            "floor=?, facing=?, locality=?, raw_address=?, description=?, content_hash=?, "
            "status='active', last_seen_at=? WHERE id=?",
            (url, property_type, listing_type, builder_id, project_id, tower, unit_no, bhk,
             size_sqft, price_inr, price_per_sqft, possession_date, floor, facing, locality,
             raw_address, description, content_hash, now, listing_id),
        )
        if price_changed:
            conn.execute(
                "INSERT INTO price_history (listing_id, price_inr, price_per_sqft) VALUES (?,?,?)",
                (listing_id, price_inr, price_per_sqft),
            )
        return listing_id, False, price_changed

    cur = conn.execute(
        "INSERT INTO listings (source_id, source_listing_id, url, property_type, listing_type, "
        "builder_id, project_id, tower, unit_no, bhk, size_sqft, price_inr, price_per_sqft, "
        "possession_date, floor, facing, locality, raw_address, description, content_hash, "
        "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, source_listing_id, url, property_type, listing_type, builder_id, project_id,
         tower, unit_no, bhk, size_sqft, price_inr, price_per_sqft, possession_date, floor, facing,
         locality, raw_address, description, content_hash, now, now),
    )
    listing_id = cur.lastrowid
    if price_inr is not None:
        conn.execute(
            "INSERT INTO price_history (listing_id, price_inr, price_per_sqft) VALUES (?,?,?)",
            (listing_id, price_inr, price_per_sqft),
        )
    return listing_id, True, False


def start_run(conn, source_id: int) -> int:
    return conn.execute("INSERT INTO runs (source_id) VALUES (?)", (source_id,)).lastrowid


def finish_run(conn, run_id: int, *, found, new, updated, price_changed, errors=0, log=None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=datetime('now'), found=?, new=?, updated=?, "
        "price_changed=?, errors=?, log=? WHERE id=?",
        (found, new, updated, price_changed, errors, log, run_id),
    )


def mark_source_run(conn, source_id: int) -> None:
    conn.execute("UPDATE sources SET last_run_at=datetime('now') WHERE id=?", (source_id,))
