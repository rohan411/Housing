-- Bangalore premium property assistant — SQLite schema
-- Design: one row per (source, listing) in `listings` (source-faithful),
-- linked into `dedup_groups` for cross-source sameness, prices appended to
-- `price_history` (never overwritten), runs audited in `runs`.

PRAGMA foreign_keys = ON;

-- Controlled builder list + alias resolution.
CREATE TABLE IF NOT EXISTS builders (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    tier        TEXT DEFAULT 'tier1',      -- tier1 | other
    aliases     TEXT DEFAULT '[]',         -- JSON array of alternate names
    active      INTEGER DEFAULT 1
);

-- Where data comes from.
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,          -- rera | builder | aggregator | portal
    base_url        TEXT,
    robots_ok       INTEGER,                -- 1 ok, 0 disallowed, NULL unknown
    scrape_friendly INTEGER,                -- 1 low .. 5 high
    enabled         INTEGER DEFAULT 1,
    last_run_at     TEXT,
    notes           TEXT
);

-- Canonical development (primary). Anchor for under-construction dedup.
CREATE TABLE IF NOT EXISTS projects (
    id                INTEGER PRIMARY KEY,
    builder_id        INTEGER REFERENCES builders(id),
    project_name_norm TEXT NOT NULL,
    property_type     TEXT,                 -- apartment | villa
    locality          TEXT,                 -- whitefield | marathahalli | other
    rera_id           TEXT UNIQUE,          -- gold dedup key when present
    possession_date   TEXT,
    lat               REAL,
    lng               REAL,
    first_seen_at     TEXT DEFAULT (datetime('now'))
);

-- One row per (source, listing). Raw-ish, source-faithful record.
CREATE TABLE IF NOT EXISTS listings (
    id                INTEGER PRIMARY KEY,
    source_id         INTEGER NOT NULL REFERENCES sources(id),
    source_listing_id TEXT,                 -- site's own id/slug
    url               TEXT,
    property_type     TEXT,                 -- apartment | villa
    listing_type      TEXT,                 -- primary | resale
    builder_id        INTEGER REFERENCES builders(id),
    project_id        INTEGER REFERENCES projects(id),
    tower             TEXT,
    unit_no           TEXT,
    bhk               REAL,
    size_sqft         REAL,
    price_inr         INTEGER,
    price_per_sqft    INTEGER,
    possession_date   TEXT,
    floor             TEXT,
    facing            TEXT,
    locality          TEXT,
    raw_address       TEXT,
    description       TEXT,
    status            TEXT DEFAULT 'active', -- active | inactive
    content_hash      TEXT,                  -- exact-dupe fast path
    canonical_id      INTEGER REFERENCES dedup_groups(id),
    first_seen_at     TEXT DEFAULT (datetime('now')),
    last_seen_at      TEXT DEFAULT (datetime('now')),
    UNIQUE (source_id, source_listing_id)
);

-- Append-only price log. Never mutate a price in place.
CREATE TABLE IF NOT EXISTS price_history (
    id             INTEGER PRIMARY KEY,
    listing_id     INTEGER NOT NULL REFERENCES listings(id),
    price_inr      INTEGER,
    price_per_sqft INTEGER,
    captured_at    TEXT DEFAULT (datetime('now'))
);

-- "Same real-world unit/project" clusters.
CREATE TABLE IF NOT EXISTS dedup_groups (
    id                 INTEGER PRIMARY KEY,
    group_type         TEXT,                -- primary_project | resale_unit | villa_unit
    primary_listing_id INTEGER,             -- best representative listing
    match_confidence   REAL,
    created_at         TEXT DEFAULT (datetime('now'))
);

-- Run audit + change-tracking source of truth.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER REFERENCES sources(id),
    started_at    TEXT DEFAULT (datetime('now')),
    finished_at   TEXT,
    found         INTEGER DEFAULT 0,
    new           INTEGER DEFAULT 0,
    updated       INTEGER DEFAULT 0,
    price_changed INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    log           TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_locality     ON listings(locality);
CREATE INDEX IF NOT EXISTS idx_listings_builder      ON listings(builder_id);
CREATE INDEX IF NOT EXISTS idx_listings_price        ON listings(price_inr);
CREATE INDEX IF NOT EXISTS idx_listings_size         ON listings(size_sqft);
CREATE INDEX IF NOT EXISTS idx_listings_hash         ON listings(content_hash);
CREATE INDEX IF NOT EXISTS idx_listings_canonical    ON listings(canonical_id);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);
CREATE INDEX IF NOT EXISTS idx_projects_rera         ON projects(rera_id);
