"""Apply a browser-exported ``overrides.json`` into the SQLite DB.

The static viewer keeps favourites/deletions in per-browser ``localStorage`` and
can only *read* a committed ``web/overrides.json``. To make selections truly
global, export them from the browser (the "Export changes" button), then run::

    python -m src.apply_overrides path/to/overrides.json

This marks favourited listings with ``favourite = 1`` and deletes the rows that
were deleted in the browser. Afterwards it re-exports the web snapshot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .db import connect

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "web" / "overrides.json"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    data = json.loads(src.read_text(encoding="utf-8"))
    favs = [int(i) for i in data.get("favourites", [])]
    deleted = [int(i) for i in data.get("deleted", [])]

    conn = connect()
    if favs:
        conn.execute("UPDATE listings SET favourite = 0")  # reset then set, so it mirrors the export
        conn.executemany("UPDATE listings SET favourite = 1 WHERE id = ?", [(i,) for i in favs])
    if deleted:
        conn.executemany("DELETE FROM price_history WHERE listing_id = ?", [(i,) for i in deleted])
        conn.executemany("DELETE FROM listings WHERE id = ?", [(i,) for i in deleted])
    conn.commit()
    fav_n = conn.execute("SELECT COUNT(*) FROM listings WHERE favourite = 1").fetchone()[0]
    tot = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    conn.close()
    print(f"Applied overrides from {src}: favourites set={len(favs)} (db total fav={fav_n}), "
          f"deleted={len(deleted)}; listings now {tot}.")
    print("Run `python -m src.export_web` to refresh the web snapshot.")


if __name__ == "__main__":
    main()
