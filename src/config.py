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

# --- Accepted builders (apartments) ----------------------------------------
# name -> list of alias strings used for normalization / matching.
# Apartments are kept only if their project matches one of these builders
# (villas are kept regardless of builder). Kept as TIER1_BUILDERS for import
# compatibility; BUILDER_TIERS below records the true tier for each.
TIER1_BUILDERS = {
    # --- Tier 1 ---
    "Prestige":            ["Prestige Group", "Prestige Estates", "Prestige Estates Projects"],
    "Sobha":               ["Sobha Ltd", "Sobha Limited", "Sobha Developers"],
    "Brigade":             ["Brigade Group", "Brigade Enterprises"],
    "Godrej":              ["Godrej Properties", "Godrej Properties Ltd", "GPL"],
    "Puravankara":         ["Puravankara Ltd", "Purva", "Provident", "Provident Housing"],
    "Embassy":             ["Embassy Group", "Embassy Property Developments"],
    "Total Environment":   ["Total Environment Building Systems", "TE",
                            "Pursuit of a Radical Rhapsody", "Windmills of Your Mind",
                            "After the Rain", "In That Quiet Earth", "Learning to Fly",
                            "The Magic Faraway Tree", "Down by the Water", "Jaj's Serein",
                            "Songs of the Wind", "Two Story Wonders"],
    "Birla Estates":       ["Birla Estate", "Birla", "Birla Trimaya"],
    "Tata Housing":        ["Tata Realty", "Tata Value Homes", "Tata Housing Development"],
    # --- Tier 2 ---
    "Shriram Properties":  ["Shriram", "Shriram Properties Ltd"],
    "Salarpuria Sattva":   ["Salarpuria", "Sattva Group", "Sattva", "Salarpuria Sattva Group"],
    "Mahindra Lifespaces": ["Mahindra Lifespace", "Mahindra Lifespaces Developers", "Mahindra Happinest"],
    "Assetz":              ["Assetz Property Group", "Assetz Homes"],
    "Myhna":               ["Myhna Homes", "Mynha"],
    "Century Real Estate": ["Century", "Century Group", "Century Real Estate Holdings"],
    "L&T Realty":          ["L&T", "Larsen & Toubro Realty", "LnT Realty"],
    "Mantri Developers":   ["Mantri", "Mantri Developers Pvt Ltd"],
    "Casagrand":           ["Casagrand Builder", "Casa Grand", "Casagrand Builder Pvt Ltd"],
    "Rohan Builders":      ["Rohan", "Rohan Builders & Developers"],
    "Concorde Group":      ["Concorde", "Concorde Group Builders"],
    "Adarsh Developers":   ["Adarsh", "Adarsh Developers Pvt Ltd", "Adarsh Palm Retreat"],
    "Nitesh Estates":      ["Nitesh", "Nitesh Estates Ltd"],
    # --- Tier 3 (only SNN per user) ---
    "SNN Builders":        ["SNN", "SNN Raj", "SNN Estates"],
    # --- retained (not on user list but active locally) ---
    "Lodha":               ["Lodha Group", "Macrotech", "Macrotech Developers"],
}

# True tier per builder (DB `builders.tier`); default tier1 if unlisted.
BUILDER_TIERS = {
    "Prestige": "tier1", "Sobha": "tier1", "Brigade": "tier1", "Godrej": "tier1",
    "Puravankara": "tier1", "Embassy": "tier1", "Total Environment": "tier1",
    "Birla Estates": "tier1", "Tata Housing": "tier1",
    "Shriram Properties": "tier2", "Salarpuria Sattva": "tier2",
    "Mahindra Lifespaces": "tier2", "Assetz": "tier2", "Myhna": "tier2",
    "Century Real Estate": "tier2", "L&T Realty": "tier2", "Mantri Developers": "tier2",
    "Casagrand": "tier2", "Rohan Builders": "tier2", "Concorde Group": "tier2",
    "Adarsh Developers": "tier2", "Nitesh Estates": "tier2",
    "SNN Builders": "tier3",
    "Lodha": "tier1",
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
