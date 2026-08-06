"""Portal scrapers (Housing.com, NoBroker) — best-effort, block-aware.

These consumer portals actively defend against automation:
  * **Housing.com** returns HTTP 406 "Security Alert" (Akamai) to non-browser
    traffic — hard blocked.
  * **NoBroker** serves only the page chrome; the actual results load from an
    authenticated, bot-protected API and never render for an anonymous crawler.

So this module is deliberately honest: it *attempts* each portal, and
  - if listing cards render, extracts them generically (₹ price + sq ft + link)
    and stores them as **resale** listings;
  - otherwise it detects the block / empty shell and records the outcome in the
    `runs` audit table instead of crashing or inventing data.

If you later route these through a residential proxy or a logged-in session,
the same extractor will pick up whatever renders. RERA + builder sites + Housiey
remain the reliable core.

Usage:
    python -m scrapers.portals --all
    python -m scrapers.portals "Housing.com"
    python -m scrapers.portals NoBroker
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

from src.db import connect
from src import store
from src.normalize import (
    classify_locality,
    content_hash,
    looks_like_villa,
    parse_inr_price,
    parse_size_sqft,
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BLOCK_SIGNS = ("security alert", "access denied", "are you a human", "captcha",
               "verify you are", "unusual traffic", "just a moment", "blocked")
_CARD_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const card = a.closest('article, li, div');
    if (!card) return;
    const t = (card.innerText || '').trim();
    if (t.includes('\\u20B9') && t.length < 600 && !seen.has(a.href)) {
      seen.add(a.href);
      out.push({ href: a.href, text: t.replace(/\\s+/g, ' ').slice(0, 400) });
    }
  });
  return out.slice(0, 80);
}
"""


@dataclass
class PortalSpec:
    name: str
    search_urls: list[str]


PORTALS: dict[str, PortalSpec] = {
    "Housing.com": PortalSpec(
        name="Housing.com",
        search_urls=[
            "https://housing.com/in/buy/real-estate-whitefield-bangalore",
            "https://housing.com/in/buy/real-estate-marathahalli-bangalore",
        ],
    ),
    "NoBroker": PortalSpec(
        name="NoBroker",
        search_urls=[
            "https://www.nobroker.in/property/sale/bangalore/Whitefield",
            "https://www.nobroker.in/property/sale/bangalore/Marathahalli",
        ],
    ),
}


def _looks_blocked(title: str, body: str) -> bool:
    blob = (title + " " + body[:2000]).lower()
    return any(s in blob for s in BLOCK_SIGNS)


def _parse_card(card: dict) -> dict | None:
    text = card["text"]
    price = parse_inr_price(text)
    if price is None:
        return None
    title = text.split("₹")[0].strip()[:80] or card["href"].rstrip("/").rsplit("/", 1)[-1]
    return {
        "url": card["href"],
        "slug": card["href"].split("?")[0].rstrip("/").rsplit("/", 1)[-1][:120],
        "name": title,
        "price_inr": price,
        "size_sqft": parse_size_sqft(text),
        "locality": classify_locality(text),
        "property_type": "villa" if looks_like_villa(text) else "apartment",
    }


def attempt(spec: PortalSpec, verbose=True) -> dict:
    conn = connect()
    source_id = store.get_source_id(conn, spec.name)
    run_id = store.start_run(conn, source_id)
    kept = new = errors = 0
    outcome = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}).new_page()
        for url in spec.search_urls:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = resp.status if resp else 0
                for _ in range(4):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(2200)
                body = page.inner_text("body")
                title = page.title()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                outcome.append(f"{url} ERROR {type(exc).__name__}")
                continue

            if status >= 400 or _looks_blocked(title, body):
                outcome.append(f"{url} BLOCKED (status={status}, title={title[:40]!r})")
                if verbose:
                    print(f"  [{spec.name}] BLOCKED {url} -> status={status}, title={title[:40]!r}")
                continue

            cards = page.evaluate(_CARD_JS)
            parsed = [c for c in ((_parse_card(x) for x in cards)) if c]
            if not parsed:
                outcome.append(f"{url} EMPTY (no listing cards rendered)")
                if verbose:
                    print(f"  [{spec.name}] EMPTY {url} -> chrome only, no cards")
                continue

            for rec in parsed:
                _, is_new, _ = store.upsert_listing(
                    conn, source_id=source_id, source_listing_id=rec["slug"], url=rec["url"],
                    property_type=rec["property_type"], listing_type="resale", builder_id=None,
                    project_id=None, price_inr=rec["price_inr"], size_sqft=rec["size_sqft"],
                    locality=rec["locality"], raw_address=None,
                    description=f"{rec['name']} (via {spec.name})",
                    content_hash=content_hash(spec.name, rec["slug"], rec["name"]))
                kept += 1
                new += int(is_new)
            outcome.append(f"{url} OK ({len(parsed)} cards)")
            if verbose:
                print(f"  [{spec.name}] OK {url} -> {len(parsed)} listings")
        browser.close()

    store.finish_run(conn, run_id, found=kept, new=new, updated=kept - new,
                     price_changed=0, errors=errors, log=" | ".join(outcome))
    store.mark_source_run(conn, source_id)
    conn.commit()
    summary = {"portal": spec.name, "kept": kept, "new": new, "errors": errors,
               "outcome": outcome}
    print(f"[{spec.name}] attempt complete: kept={kept} new={new} errors={errors}")
    conn.close()
    return summary


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--list":
        print("Portals:", ", ".join(PORTALS))
        return 0
    names = list(PORTALS) if argv[0] == "--all" else [a for a in argv if not a.startswith("--")]
    for name in names:
        spec = PORTALS.get(name)
        if not spec:
            print(f"Unknown portal {name!r}. Known: {', '.join(PORTALS)}")
            continue
        attempt(spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
