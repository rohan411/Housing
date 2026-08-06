# Bangalore Premium Property Assistant

Personal assistant that collects, deduplicates, and tracks premium residential
properties in **Whitefield / Marathahalli**, Bangalore, and (later) lets you
query them in natural language.

## Criteria
Full brief in **[REQUIREMENTS.md](REQUIREMENTS.md)**. In short:
- **Localities:** Whitefield, Marathahalli
- **Size:** 2000+ sq ft
- **Budget:** ₹3–5 Cr
- **Possession:** on/before 30 Jun 2028
- **Types:** apartments (Tier-1 builders only) **and** villas (any builder)
- **Tier-1 builders:** Prestige, Sobha, Brigade, Puravankara, Godrej, Embassy,
  Total Environment, Assetz, Mahindra Lifespaces, Lodha

## Architecture
Layered by design — AI is used to *build/maintain/query*, not to scrape live:

| Layer | Tech | Role |
|-------|------|------|
| Collection | Python + Playwright | Per-source scrapers |
| Registry anchor | Karnataka RERA JSON API | Builder/project/possession/**RERA no** (dedup key) |
| Storage | SQLite (`data/properties.db`) | 7 tables, price history append-only |
| Dedup | natural keys + rapidfuzz | RERA no (primary), fuzzy (resale/villa) |
| Change tracking | `runs` + `price_history` | new listings + price changes |
| NL query | (planned) small LLM → text-to-SQL | ask questions over the DB |

### Database (`db/schema.sql`)
`builders · sources · projects · listings · price_history · dedup_groups · runs`

- `listings` = one row per (source, listing); `UNIQUE(source_id, source_listing_id)`.
- `projects.rera_id` is UNIQUE → the gold dedup key for primary projects.
- Prices are **appended** to `price_history`, never overwritten.

## Run it locally (step by step)
Requires **Python 3.11+**. On macOS/Homebrew Python is "externally managed", so a
virtualenv is mandatory.

```bash
# 1. clone + enter the repo
cd Housing

# 2. create and activate a virtualenv (needed every new shell)
python3 -m venv .venv
. .venv/bin/activate                       # Windows: .venv\Scripts\activate

# 3. install dependencies + the Chromium browser Playwright drives
pip install -r requirements.txt
python -m playwright install chromium

# 4. create the SQLite schema + seed builders/sources
python -m src.db --init

# 5. scrape sources (each is independent + idempotent — safe to re-run)
python -m scrapers.rera                 # source #1: Karnataka RERA registry (anchor)
python -m scrapers.builders --all       # all builder sites (Prestige/Sobha/Brigade/…)
python -m scrapers.housiey              # aggregator (Whitefield; villas + Tier-1 apts)
python -m scrapers.portals --all        # portals (best-effort; Housing.com/NoBroker block)

# 6. publish the data snapshot for the viewer
python -m src.export_web                # writes web/properties.db + web/properties.db.js

# 7a. browse — just open the file (no server needed)
open web/index.html                     # Linux: xdg-open · Windows: start
# 7b. …or serve it (also fine)
cd web && python -m http.server 8777    # http://127.0.0.1:8777
```
> The viewer embeds the DB snapshot (`web/properties.db.js`), so **double-clicking
> `web/index.html` works** — a local server is optional. Re-run step 6 after any
> scrape to refresh what the viewer shows.

Builder scrapers share one engine (`scrapers/builder_base.py`); each builder is
just a `BuilderSpec` (list URL + project-URL regex) in `scrapers/builders.py`.
`python -m scrapers.prestige` still works (thin wrapper kept for compatibility).

## Browse in the browser (static viewer)
`web/index.html` is a **React + Tailwind** single-file app that runs SQLite
**in the browser** via sql.js (WASM) — zero backend, deployable to GitHub Pages.
It loads an embedded snapshot so it works both from `file://` and over HTTP.

Features: live filters (search, locality, type, listing, builder, source, min/max
₹Cr, min sqft, possession) that filter **in-memory** (robust), a one-click
"⭐ My criteria" preset, "only priced" / "only target-area" toggles, summary stat
cards, sortable columns, a collapsible raw read-only **SQL box** over the
`v_listings` view, and CSV export.

```bash
python -m src.export_web               # rebuild web/properties.db(.js) + v_listings view
```

**Deploy to GitHub Pages:** commit `web/` (incl. `web/properties.db` and
`web/properties.db.js`), then repo Settings → Pages → serve from `/web`.

## Query + change tracking (CLI)
```bash
python -m src.query summary
python -m src.query list --criteria                    # matches saved criteria
python -m src.query list --locality whitefield --max-price 4.5
python -m src.query new --since 7d --max-price 4.5     # "new listings this week under 4.5 Cr"
python -m src.query price-changes --since 30d          # price movements
```
`price` filters are in Crore. `list --criteria` treats unknown (NULL) locality/
price/size/possession as "include" so registry anchors surface pending enrichment.

## Status
| Phase | State |
|-------|-------|
| Schema + seed (10 builders, 14 sources) | ✅ |
| Source #1 RERA (registry anchor) | ✅ 25 projects, dedup by RERA no |
| Reusable builder engine (`builder_base` + specs) | ✅ |
| Source #2 Prestige (price + locality) | ✅ 7 projects |
| Source #3 Sobha | ✅ 32 projects, 5 linked to RERA |
| Source #4 Brigade | ✅ 8 projects (listing page lazy-loads → shallow) |
| Dedup (natural keys) | ✅ idempotent, RERA-no cross-source linking |
| Change tracking (new + price changes) | ✅ append-only price history |
| Query CLI | ✅ |
| Static React/sql.js viewer (`web/`) | ✅ embedded snapshot, in-memory filters |
| Source #5 Housiey (aggregator) | ✅ Whitefield; villas + Tier-1 apts, RERA-linked |
| Portals: Housing.com / NoBroker | ⚠️ best-effort (heavy anti-bot) |
| LLM natural-language layer (text-to-SQL) | ⏳ needs an API key |

Current DB: **~98 listings** (RERA · Prestige · Sobha · Brigade · Housiey).

## Source build order
1. **Karnataka RERA** ✅ (registry anchor)
2. **Prestige** ✅ · **Sobha** ✅ · **Brigade** ✅ (builder engine validated)
3. Remaining Tier-1 builder sites (Puravankara, Godrej, Embassy, Total Environment,
   Assetz, Mahindra, Lodha) — add a `BuilderSpec` each
4. housiey.com ✅ (aggregator; Whitefield; villas + Tier-1 apts, RERA-linked)
5. Housing.com / NoBroker ⚠️ best-effort — **currently blocked** (see Notes)
6. Change tracking ✅ → 7. NL query layer

## Adding a new builder
1. Recon one detail page — confirm the ₹/INR price, `PRM/KA/RERA/...` no, and a
   pincode-anchored address are present in page text.
2. Add a `BuilderSpec(name, list_urls, project_re)` to `scrapers/builders.py`
   (`name` must match the seeded builder/source).
3. `python -m scrapers.builders <Name>` and eyeball the printed table, then
   `python -m src.export_web` to refresh the viewer.

## Notes
- RERA gives builder/project/possession/RERA-no but **no price or per-unit
  locality**; those are enriched by builder/aggregator scrapers, matched via RERA no.
- **Portals are block-aware and best-effort.** As of now **Housing.com** returns
  HTTP 406 "Security Alert" (Akamai) and **NoBroker** renders only page chrome
  (results load from an authenticated, bot-protected API). `scrapers/portals.py`
  attempts them, extracts cards *if* they render, and otherwise records the
  block/empty outcome in the `runs` table — it never invents data. Route through
  a residential proxy or a logged-in session to revive them; the extractor is ready.

## Inspect data
```bash
sqlite3 data/properties.db \
  "SELECT property_type, project_name_norm, possession_date FROM projects ORDER BY possession_date;"
```
