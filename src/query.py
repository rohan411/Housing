"""Query + change-tracking CLI over the local property DB.

This is the structured query engine that the (future) natural-language layer
will translate into. Everything here is deterministic SQL so results are
verifiable.

Examples:
    python -m src.query list --criteria                 # everything matching saved criteria
    python -m src.query list --locality whitefield --max-price 4.5
    python -m src.query new --since 7d --max-price 4.5   # "new listings this week under 4.5 Cr"
    python -m src.query price-changes --since 30d        # price movements
    python -m src.query summary
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from src.config import CRITERIA
from src.db import connect


def _crore(v: int | None) -> str:
    return f"₹{v/1e7:.2f}Cr" if v else "—"


def _parse_since(s: str) -> str:
    """'7d' / '2w' / '3m' / ISO date -> ISO datetime lower bound."""
    m = re.fullmatch(r"(\d+)([dwm])", s.strip().lower())
    if m:
        n = int(m.group(1))
        days = {"d": 1, "w": 7, "m": 30}[m.group(2)] * n
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return s  # assume ISO date/datetime


def _price_cr_to_inr(v: float | None) -> int | None:
    return int(v * 1_00_00_000) if v is not None else None


def _build_filters(args, apply_criteria: bool) -> tuple[str, list]:
    where, params = ["1=1"], []
    if apply_criteria:
        where.append(f"(l.locality IN ({','.join('?' * len(CRITERIA['localities']))}) OR l.locality IS NULL)")
        params += CRITERIA["localities"]
        where.append("(l.price_inr IS NULL OR l.price_inr BETWEEN ? AND ?)")
        params += [CRITERIA["price_min_inr"], CRITERIA["price_max_inr"]]
        where.append("(l.size_sqft IS NULL OR l.size_sqft >= ?)")
        params.append(CRITERIA["min_sqft"])
        where.append("(l.possession_date IS NULL OR l.possession_date <= ?)")
        params.append(CRITERIA["possession_cutoff"])
    if getattr(args, "locality", None):
        where.append("l.locality = ?")
        params.append(args.locality.lower())
    if getattr(args, "type", None):
        where.append("l.property_type = ?")
        params.append(args.type)
    if getattr(args, "builder", None):
        where.append("b.name = ?")
        params.append(args.builder)
    if getattr(args, "source", None):
        where.append("s.name = ?")
        params.append(args.source)
    if getattr(args, "min_price", None) is not None:
        where.append("l.price_inr >= ?")
        params.append(_price_cr_to_inr(args.min_price))
    if getattr(args, "max_price", None) is not None:
        where.append("l.price_inr <= ?")
        params.append(_price_cr_to_inr(args.max_price))
    if getattr(args, "possession_by", None):
        where.append("l.possession_date <= ?")
        params.append(args.possession_by)
    return " AND ".join(where), params


_BASE = (
    "SELECT s.name src, b.name builder, l.property_type, l.listing_type, l.locality, "
    "l.price_inr, l.size_sqft, l.possession_date, p.rera_id, l.description, l.url, "
    "l.first_seen_at "
    "FROM listings l "
    "LEFT JOIN sources s ON s.id = l.source_id "
    "LEFT JOIN builders b ON b.id = l.builder_id "
    "LEFT JOIN projects p ON p.id = l.project_id "
)


def _print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (no matches)")
        return
    for r in rows:
        print(f"  [{(r['src'] or '?'):14}] {(r['builder'] or '—'):20} | "
              f"{r['property_type']:9} | {(r['locality'] or '?'):13} | {_crore(r['price_inr']):>9} | "
              f"{(r['possession_date'] or '?'):10} | {(r['description'] or '')[:40]}")
    print(f"\n  {len(rows)} result(s)")


def cmd_list(conn, args) -> None:
    where, params = _build_filters(args, args.criteria)
    rows = conn.execute(_BASE + f"WHERE {where} ORDER BY l.price_inr", params).fetchall()
    _print_rows(rows)


def cmd_new(conn, args) -> None:
    since = _parse_since(args.since)
    where, params = _build_filters(args, args.criteria)
    rows = conn.execute(
        _BASE + f"WHERE {where} AND l.first_seen_at >= ? ORDER BY l.first_seen_at DESC",
        params + [since],
    ).fetchall()
    print(f"New listings since {since}:")
    _print_rows(rows)


def cmd_price_changes(conn, args) -> None:
    since = _parse_since(args.since)
    rows = conn.execute(
        "SELECT s.name src, b.name builder, l.locality, l.description, "
        "ph.price_inr, ph.captured_at "
        "FROM price_history ph JOIN listings l ON l.id = ph.listing_id "
        "LEFT JOIN sources s ON s.id = l.source_id LEFT JOIN builders b ON b.id = l.builder_id "
        "WHERE ph.captured_at >= ? "
        "AND l.id IN (SELECT listing_id FROM price_history GROUP BY listing_id HAVING COUNT(*) > 1) "
        "ORDER BY l.id, ph.captured_at",
        (since,),
    ).fetchall()
    print(f"Price changes since {since}:")
    if not rows:
        print("  (none — need >=2 runs with a differing price to register a change)")
        return
    for r in rows:
        print(f"  {r['src']:14} {(r['builder'] or '—'):16} {(r['locality'] or '?'):12} "
              f"{_crore(r['price_inr']):>9} @ {r['captured_at']} | {(r['description'] or '')[:34]}")


def cmd_summary(conn, _args) -> None:
    print("Database summary")
    for label, q in [
        ("listings by source", "SELECT s.name, COUNT(*) FROM listings l JOIN sources s ON s.id=l.source_id GROUP BY 1 ORDER BY 2 DESC"),
        ("by property_type", "SELECT property_type, COUNT(*) FROM listings GROUP BY 1"),
        ("by locality (top)", "SELECT COALESCE(locality,'?'), COUNT(*) FROM listings GROUP BY 1 ORDER BY 2 DESC LIMIT 8"),
        ("priced listings", "SELECT 'count', COUNT(*) FROM listings WHERE price_inr IS NOT NULL"),
        ("matching saved criteria", None),
    ]:
        if q is None:
            where, params = _build_filters(argparse.Namespace(), True)
            n = conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {where}", params).fetchone()[0]
            print(f"  {label}: {n}")
        else:
            rows = conn.execute(q).fetchall()
            print(f"  {label}: " + ", ".join(f"{r[0]}={r[1]}" for r in rows))


def main() -> None:
    ap = argparse.ArgumentParser(description="Property DB query + change tracking")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--criteria", action="store_true", help="apply saved search criteria")
        sp.add_argument("--locality")
        sp.add_argument("--type", choices=["apartment", "villa"])
        sp.add_argument("--builder")
        sp.add_argument("--source")
        sp.add_argument("--min-price", type=float, help="in Crore")
        sp.add_argument("--max-price", type=float, help="in Crore")
        sp.add_argument("--possession-by", help="YYYY-MM-DD")

    sp = sub.add_parser("list"); add_common(sp)
    sp = sub.add_parser("new"); add_common(sp); sp.add_argument("--since", default="7d")
    sp = sub.add_parser("price-changes"); sp.add_argument("--since", default="30d")
    sub.add_parser("summary")

    args = ap.parse_args()
    conn = connect()
    {"list": cmd_list, "new": cmd_new, "price-changes": cmd_price_changes,
     "summary": cmd_summary}[args.cmd](conn, args)
    conn.close()


if __name__ == "__main__":
    main()
