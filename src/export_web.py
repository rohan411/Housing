"""Export the SQLite DB (and a convenience VIEW) to the static web viewer.

The `web/` page uses sql.js (SQLite compiled to WASM) to run queries entirely
in the browser against a copied snapshot of `data/properties.db`. Because the
page is fully static it deploys to GitHub Pages with no backend.

    python -m src.export_web

This copies the DB to `web/properties.db`. A read-only `v_listings` VIEW is
(re)created in the source DB first so the viewer and CLI share one friendly,
joined shape (builder/source/project names resolved, price in Cr).
"""
from __future__ import annotations

import base64
import shutil

from src.db import DB_PATH, ROOT, connect

WEB_DB = ROOT / "web" / "properties.db"
WEB_DB_JS = ROOT / "web" / "properties.db.js"

VIEW_SQL = """
DROP VIEW IF EXISTS v_listings;
CREATE VIEW v_listings AS
SELECT
    l.id                                   AS id,
    s.name                                 AS source,
    COALESCE(b.name, '—')                  AS builder,
    l.property_type                        AS type,
    l.listing_type                         AS listing,
    l.bhk                                  AS bhk,
    COALESCE(l.locality, p.locality, '?')  AS locality,
    l.price_inr                            AS price_inr,
    ROUND(l.price_inr / 10000000.0, 2)     AS price_cr,
    l.size_sqft                            AS size_sqft,
    COALESCE(l.possession_date, p.possession_date) AS possession,
    p.rera_id                              AS rera_id,
    l.description                          AS description,
    l.url                                  AS url,
    l.first_seen_at                        AS first_seen,
    l.last_seen_at                         AS last_seen
FROM listings l
LEFT JOIN sources  s ON s.id = l.source_id
LEFT JOIN builders b ON b.id = l.builder_id
LEFT JOIN projects p ON p.id = l.project_id;
"""


def main() -> None:
    conn = connect()
    conn.executescript(VIEW_SQL)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    conn.close()

    WEB_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DB_PATH, WEB_DB)

    # Also embed the DB as base64 in a JS file so the viewer works when the
    # HTML is opened directly (file://) — where fetch() of the .db is blocked
    # by the browser — as well as when served over HTTP / GitHub Pages.
    b64 = base64.b64encode(WEB_DB.read_bytes()).decode("ascii")
    WEB_DB_JS.write_text(f'window.__PROPERTIES_DB_B64__ = "{b64}";\n', encoding="utf-8")

    print(f"Exported {n} listings -> {WEB_DB.relative_to(ROOT)} "
          f"({WEB_DB.stat().st_size // 1024} KB) + embedded {WEB_DB_JS.name}. "
          f"Open web/index.html (double-click works) to browse.")


if __name__ == "__main__":
    main()
