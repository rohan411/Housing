"""Apply a browser-exported ``overrides.json`` into the SQLite DB.

The static viewer keeps favourites/deletions/rescues in per-browser
``localStorage`` and can only *read* a committed ``web/overrides.json``. To make
selections truly global, export them from the browser (the "Export changes"
button), then run::

    python -m src.apply_overrides path/to/overrides.json

This marks favourited listings with ``favourite = 1``, deletes the rows that
were deleted in the browser, promotes any *rescued* dropped listings into the
main ``listings`` table, and removes dropped rows the user discarded. Afterwards
run ``python -m src.export_web`` to refresh the web snapshot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .db import connect
from . import store
from .normalize import content_hash

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "web" / "overrides.json"


def _promote_rescued(conn, drop_ids: list[int]) -> int:
    """Move rows from ``dropped_listings`` into ``listings`` (as real matches)."""
    promoted = 0
    for did in drop_ids:
        row = conn.execute(
            "SELECT source, source_listing_id, url, property_type, listing_type, "
            "bhk, size_sqft, price_inr, locality, society, builder_guess "
            "FROM dropped_listings WHERE id = ?", (did,)).fetchone()
        if not row:
            continue
        (source, slid, url, ptype, ltype, bhk, size_sqft, price_inr,
         locality, society, builder_guess) = row
        source_id = store.get_source_id(conn, source)
        bid = store.builder_id(conn, builder_guess) if builder_guess and builder_guess != "—" else None
        store.upsert_listing(
            conn, source_id=source_id, source_listing_id=slid, url=url,
            property_type=ptype, listing_type=ltype or "resale", builder_id=bid,
            project_id=None, bhk=bhk, size_sqft=size_sqft, price_inr=price_inr,
            locality=locality, raw_address=None,
            description=f"{society} (rescued from dropped, via NoBroker)",
            content_hash=content_hash(source, slid, society or slid))
        conn.execute("DELETE FROM dropped_listings WHERE id = ?", (did,))
        promoted += 1
    return promoted


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    data = json.loads(src.read_text(encoding="utf-8"))
    favs = [int(i) for i in data.get("favourites", []) if str(i).lstrip("-").isdigit()]
    deleted = [int(i) for i in data.get("deleted", []) if str(i).lstrip("-").isdigit()]
    rescued = [int(i) for i in data.get("rescued", [])]
    deleted_drop = [int(i) for i in data.get("deletedDrop", [])]

    conn = connect()
    if favs:
        conn.execute("UPDATE listings SET favourite = 0")  # reset then set, so it mirrors the export
        conn.executemany("UPDATE listings SET favourite = 1 WHERE id = ?", [(i,) for i in favs])
    if deleted:
        conn.executemany("DELETE FROM price_history WHERE listing_id = ?", [(i,) for i in deleted])
        conn.executemany("DELETE FROM listings WHERE id = ?", [(i,) for i in deleted])
    promoted = _promote_rescued(conn, rescued) if rescued else 0
    if deleted_drop:
        conn.executemany("DELETE FROM dropped_listings WHERE id = ?", [(i,) for i in deleted_drop])
    conn.commit()
    fav_n = conn.execute("SELECT COUNT(*) FROM listings WHERE favourite = 1").fetchone()[0]
    tot = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    drop_tot = conn.execute("SELECT COUNT(*) FROM dropped_listings").fetchone()[0]
    conn.close()
    print(f"Applied overrides from {src}: favourites set={len(favs)} (db total fav={fav_n}), "
          f"deleted={len(deleted)}, rescued/promoted={promoted}, dropped removed={len(deleted_drop)}; "
          f"listings now {tot}, dropped now {drop_tot}.")
    print("Run `python -m src.export_web` to refresh the web snapshot.")


if __name__ == "__main__":
    main()
