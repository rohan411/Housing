"""Normalization + matching helpers shared across scrapers.

Pure functions, no I/O. Used for builder resolution, project-name
normalization, dedup content hashing, and date conversion.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from rapidfuzz import fuzz

# Corporate / legal suffix noise stripped before matching.
_SUFFIX_NOISE = [
    "private limited", "pvt ltd", "pvt. ltd", "private ltd", "limited", "ltd",
    "llp", "developers", "developer", "projects", "project", "properties",
    "property", "group", "enterprises", "enterprise", "constructions",
    "construction", "builders", "builder", "estates", "estate", "homes",
    "housing", "ventures", "venture", "and company", "& co", "co",
]
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def norm_name(s: str | None) -> str:
    """Lowercase, strip punctuation + legal suffixes, collapse whitespace."""
    if not s:
        return ""
    s = _PUNCT.sub(" ", s.lower())
    s = _WS.sub(" ", s).strip()
    for suf in sorted(_SUFFIX_NOISE, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(suf)}\b", " ", s)
    return _WS.sub(" ", s).strip()


def match_builder(promoter: str, builders: dict[str, list[str]]) -> tuple[str | None, int]:
    """Fuzzy-match a promoter name to a known builder.

    `builders` maps canonical name -> list of alias strings.
    Returns (builder_name or None, score 0..100).
    """
    p = norm_name(promoter)
    best_name, best_score = None, 0
    for name, aliases in builders.items():
        for cand in [name, *aliases]:
            score = fuzz.token_set_ratio(norm_name(cand), p)
            if score > best_score:
                best_name, best_score = name, score
    return best_name, best_score


def content_hash(*parts: object) -> str:
    """Stable hash of normalized key fields for exact-dupe detection."""
    joined = "|".join(norm_name(str(p)) if p is not None else "" for p in parts)
    return hashlib.sha1(joined.encode()).hexdigest()


def epoch_ms_to_date(ms: int | None) -> date | None:
    if not ms:
        return None
    return date(1970, 1, 1) + timedelta(milliseconds=ms)


VILLA_HINTS = ("villa", "villas", "villards", "villapark", "row house", "rowhouse")


def looks_like_villa(*texts: str | None) -> bool:
    blob = " ".join(t.lower() for t in texts if t)
    return any(h in blob for h in VILLA_HINTS)


# Canonical Bangalore localities. The first two are the in-criteria targets;
# the rest are tagged accurately so out-of-area projects can be filtered out.
LOCALITY_KEYWORDS = {
    "whitefield": "whitefield", "marathahalli": "marathahalli",
    "varthur": "varthur", "gunjur": "gunjur", "kadugodi": "kadugodi",
    "hoodi": "hoodi", "brookefield": "brookefield", "panathur": "panathur",
    "sarjapur": "sarjapur", "budigere": "budigere", "devanahalli": "devanahalli",
    "yelahanka": "yelahanka", "hebbal": "hebbal", "electronic city": "electronic city",
    "kr puram": "kr puram", "mahadevapura": "mahadevapura", "hennur": "hennur",
    "thanisandra": "thanisandra", "bagalur": "bagalur", "jakkur": "jakkur",
    "akshayanagar": "akshayanagar", "bannerghatta": "bannerghatta",
    "north bangalore": "north bangalore", "south bangalore": "south bangalore",
    "east bangalore": "east bangalore",
}

# Accept ₹, INR, Rs, Rs. as the currency marker (builders differ: Prestige uses
# "₹", Sobha uses "INR 1.65 Cr"). A currency token is required to avoid matching
# stray numbers (e.g. sizes, pincodes).
_PRICE_RE = re.compile(
    r"(?:₹|INR|Rs\.?)\s*([\d.,]+)\s*\*?\s*(crore|cr|lakh|lac)\b", re.I)
# Size like "2415 Sq.Ft." or a range "1063 – 2415 Sq.Ft." -> capture the max.
_SIZE_RANGE_RE = re.compile(
    r"(\d[\d,]{2,6})\s*(?:–|-|to)\s*(\d[\d,]{2,6})\s*sq\.?\s*(?:ft|feet)", re.I)
_SIZE_SINGLE_RE = re.compile(r"(\d[\d,]{2,6})\s*sq\.?\s*(?:ft|feet)", re.I)
_ADDR_PIN_RE = re.compile(
    r"([A-Za-z0-9 .,'&/-]{5,80}?)(?:Bengaluru|Bangalore)[ ,\-]*(?:Karnataka)?[ ,\-]*(5\d{5})",
    re.I,
)


def parse_inr_price(text: str | None) -> int | None:
    """Parse the first '₹/INR X Crore/Lakh' amount to integer rupees."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    return int(val * 1_00_00_000) if unit.startswith("cr") else int(val * 1_00_000)


def parse_size_sqft(text: str | None) -> int | None:
    """Return a representative unit size in sq ft (max of a stated range)."""
    if not text:
        return None
    best = 0
    for m in _SIZE_RANGE_RE.finditer(text):
        hi = int(m.group(2).replace(",", ""))
        if 200 <= hi <= 100000:
            best = max(best, hi)
    if best:
        return best
    for m in _SIZE_SINGLE_RE.finditer(text):
        v = int(m.group(1).replace(",", ""))
        if 200 <= v <= 100000:
            best = max(best, v)
    return best or None


_BHK_NUM_RE = re.compile(r"\d")


def norm_bhk(text: str | None) -> str | None:
    """Normalise a raw BHK token (e.g. '3', '3, 4', '3 & 4 BHK', '3-5') to a
    clean label like '3 BHK' or '3–5 BHK'. Returns None if no digit found."""
    if not text:
        return None
    nums = [int(n) for n in _BHK_NUM_RE.findall(str(text))]
    nums = [n for n in nums if 1 <= n <= 9]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    return f"{lo} BHK" if lo == hi else f"{lo}–{hi} BHK"


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_POSS_RE = re.compile(r"\b([A-Za-z]{3,9})[\s\-,]+((?:19|20)\d{2})\b")


def parse_possession(text: str | None) -> str | None:
    """Parse a possession like 'Dec 2029' / 'December 2029' to 'YYYY-MM-01'."""
    if not text:
        return None
    for m in _POSS_RE.finditer(text):
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return f"{int(m.group(2)):04d}-{mon:02d}-01"
    return None


def extract_address(text: str | None) -> str | None:
    """Return a Bengaluru address string anchored on a 5600xx pincode, if any."""
    if not text:
        return None
    m = _ADDR_PIN_RE.search(text)
    if not m:
        return None
    return f"{m.group(1).strip(' ,-')}, Bengaluru {m.group(2)}"


def classify_locality(*texts: str | None) -> str | None:
    """Match a canonical locality from clean text (address + meta), not full body."""
    blob = " ".join(t.lower() for t in texts if t)
    for kw, canon in LOCALITY_KEYWORDS.items():
        if re.search(rf"\b{re.escape(kw)}\b", blob):
            return canon
    return None
