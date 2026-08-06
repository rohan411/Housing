# Project Requirements — Bangalore Premium Property Assistant

Personal, AI-assisted property search assistant. This document captures the
owner's search brief and the functional requirements the system is built to.

## Search criteria (what counts as a match)
| Attribute | Requirement |
|-----------|-------------|
| **Localities** | Whitefield and Marathahalli, Bangalore |
| **Property types** | Apartments **and** villas |
| **Listing types** | Primary (under-construction) **and** resale |
| **Size** | 2000+ sq ft |
| **Budget** | ₹3–5 Cr |
| **Possession** | on/before **30 Jun 2028** |
| **Builders (apartments)** | Tier-1 / reputed only |
| **Builders (villas)** | Any builder (Tier-1 rule **relaxed for villas**) |

### Tier-1 builder list (apartments)
Prestige · Sobha · Brigade · Puravankara · Godrej · Embassy ·
Total Environment · Assetz · Mahindra Lifespaces · Lodha
*(Shriram removed, Lodha added per owner.)*

## Functional requirements (end state)
1. **Collect** matching listings from a defined set of sources:
   - Karnataka **RERA** registry (authoritative anchor: builder, project,
     possession date, RERA registration number).
   - **Builder** websites for the Tier-1 builders active in these areas.
   - Aggregator **Housiey**, and property portals **Housing.com**, **NoBroker**
     (best-effort — heavy anti-bot).
2. **Store** everything in a local **SQLite** database.
3. **Deduplicate** listings that represent the same unit/project across sources
   (primary key = RERA registration number; fuzzy name/builder matching as
   backup).
4. **Track changes** over time — flag new listings and price changes since the
   last run (append-only price history + per-run audit).
5. **Query** the data — CLI filters today, a static in-browser SQL viewer
   (`web/index.html`), and eventually a natural-language (text-to-SQL) layer.

## Non-goals / deferred
- No web UI backend (the viewer is fully static / client-side).
- Portals (Housing.com, NoBroker) are best-effort; blocking is expected and
  acceptable — RERA + builder sites + Housiey are the reliable core.
- LLM natural-language querying is planned but needs an API key.

## Tech approach
Python + Playwright (JS-heavy sites) · SQLite · scheduled runs (cron/APScheduler)
· CLI + static sql.js viewer. AI is used to *build, maintain, and query* the
system — not to scrape live.
