"""Central configuration: search criteria, Tier-1 builders, and data sources.

Nothing here talks to the network — it's pure data used by db.py to seed the
database and by scrapers/query code to filter results.
"""

# --- Search criteria -------------------------------------------------------
CRITERIA = {
    "localities": ["whitefield", "marathahalli"],
    "min_sqft": 2000,
    "price_min_inr": 30_000_000,   # 3 Cr
    "price_max_inr": 50_000_000,   # 5 Cr
    "possession_cutoff": "2028-06-30",  # on/before mid-2028
    # Apartments: Tier-1 builders only. Villas: any builder allowed.
    "property_types": ["apartment", "villa"],
    "tier1_only_for": ["apartment"],
}

# --- Tier-1 builders (apartments) ------------------------------------------
# name -> list of alias strings used for normalization / matching.
TIER1_BUILDERS = {
    "Prestige":            ["Prestige Group", "Prestige Estates", "Prestige Estates Projects"],
    "Sobha":               ["Sobha Ltd", "Sobha Limited", "Sobha Developers"],
    "Brigade":             ["Brigade Group", "Brigade Enterprises"],
    "Puravankara":         ["Puravankara Ltd", "Purva", "Provident"],
    "Godrej":              ["Godrej Properties", "Godrej Properties Ltd", "GPL"],
    "Embassy":             ["Embassy Group", "Embassy Property Developments"],
    "Total Environment":   ["Total Environment Building Systems", "TE",
                            "Pursuit of a Radical Rhapsody", "Windmills of Your Mind",
                            "After the Rain", "In That Quiet Earth", "Learning to Fly",
                            "The Magic Faraway Tree", "Down by the Water", "Jaj's Serein",
                            "Songs of the Wind", "Two Story Wonders"],
    "Assetz":              ["Assetz Property Group", "Assetz Homes"],
    "Mahindra Lifespaces": ["Mahindra Lifespace", "Mahindra Lifespaces Developers", "Mahindra Happinest"],
    "Lodha":               ["Lodha Group", "Macrotech", "Macrotech Developers"],
}

# --- Data sources (seed rows for `sources`) --------------------------------
# scrape_friendly: 1 (low / heavy anti-bot) .. 5 (high / structured & stable)
SOURCES = [
    {"name": "Karnataka RERA", "type": "rera", "base_url": "https://rera.karnataka.gov.in/",
     "scrape_friendly": 4, "notes": "Official. Possession dates + RERA reg numbers. Anchor source."},
    {"name": "Prestige",   "type": "builder", "base_url": "https://www.prestigeconstructions.com/",
     "scrape_friendly": 3, "notes": "First builder validation slice."},
    {"name": "Sobha",      "type": "builder", "base_url": "https://www.sobha.com/",              "scrape_friendly": 3},
    {"name": "Brigade",    "type": "builder", "base_url": "https://www.brigadegroup.com/",       "scrape_friendly": 3},
    {"name": "Puravankara","type": "builder", "base_url": "https://www.puravankara.com/",        "scrape_friendly": 3},
    {"name": "Godrej",     "type": "builder", "base_url": "https://www.godrejproperties.com/",   "scrape_friendly": 3},
    {"name": "Embassy",    "type": "builder", "base_url": "https://www.embassyindia.com/",       "scrape_friendly": 3},
    {"name": "Total Environment",  "type": "builder", "base_url": "https://www.total-environment.com/", "scrape_friendly": 3},
    {"name": "Assetz",     "type": "builder", "base_url": "https://www.assetzproperty.com/",     "scrape_friendly": 3},
    {"name": "Mahindra Lifespaces", "type": "builder", "base_url": "https://www.mahindralifespaces.com/", "scrape_friendly": 3},
    {"name": "Lodha",      "type": "builder", "base_url": "https://www.lodhagroup.in/",          "scrape_friendly": 3},
    {"name": "Housiey",    "type": "aggregator", "base_url": "https://housiey.com/",
     "scrape_friendly": 3, "notes": "New-launch aggregator; cross-fill primary; dedup via RERA id / project name."},
    {"name": "Housing.com","type": "portal", "base_url": "https://housing.com/",
     "scrape_friendly": 2, "notes": "Best-effort. Resale + cross-check."},
    {"name": "NoBroker",   "type": "portal", "base_url": "https://www.nobroker.in/",
     "scrape_friendly": 2, "notes": "Best-effort. Resale."},
    {"name": "SquareYards","type": "portal", "base_url": "https://www.squareyards.com/",
     "scrape_friendly": 4, "notes": "Server-rendered resale listings (Whitefield/Marathahalli); slug carries BHK/size/type/project; dedup via project+id, RERA where present."},
]
