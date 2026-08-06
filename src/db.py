"""SQLite bootstrap: create schema and seed builders + sources.

Usage:
    python -m src.db --init      # create schema + seed reference data
    python -m src.db --stats     # print row counts

Idempotent: safe to re-run. Seeding uses INSERT OR IGNORE / upsert on names.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "properties.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def seed(conn: sqlite3.Connection) -> None:
    from src.config import TIER1_BUILDERS, SOURCES

    for name, aliases in TIER1_BUILDERS.items():
        conn.execute(
            "INSERT INTO builders (name, tier, aliases, active) VALUES (?, 'tier1', ?, 1) "
            "ON CONFLICT(name) DO UPDATE SET aliases=excluded.aliases, tier='tier1', active=1",
            (name, json.dumps(aliases)),
        )

    for s in SOURCES:
        conn.execute(
            "INSERT INTO sources (name, type, base_url, scrape_friendly, enabled, notes) "
            "VALUES (:name, :type, :base_url, :scrape_friendly, 1, :notes) "
            "ON CONFLICT(name) DO UPDATE SET type=excluded.type, base_url=excluded.base_url, "
            "scrape_friendly=excluded.scrape_friendly, notes=excluded.notes",
            {"notes": s.get("notes"), **s},
        )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ["builders", "sources", "projects", "listings", "price_history", "dedup_groups", "runs"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLite bootstrap for property assistant")
    ap.add_argument("--init", action="store_true", help="create schema + seed reference data")
    ap.add_argument("--stats", action="store_true", help="print row counts")
    args = ap.parse_args()

    conn = connect()
    if args.init:
        init_schema(conn)
        seed(conn)
        print(f"Initialized {DB_PATH}")
    if args.stats or args.init:
        for table, count in stats(conn).items():
            print(f"  {table:<14} {count}")
    conn.close()


if __name__ == "__main__":
    main()
