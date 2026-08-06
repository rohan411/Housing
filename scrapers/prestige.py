"""Prestige builder scraper (source #2) — thin wrapper over `builder_base`.

The reusable crawl/extract/store logic now lives in `scrapers.builder_base`;
this module keeps Prestige's original CLI (`python -m scrapers.prestige`) and
its `run()` entry point for backwards compatibility. The Prestige spec is
defined centrally in `scrapers.builders`.

Extraction contract (unchanged): name, price, RERA reg no, address/locality are
reliable; the RERA reg no is the cross-source dedup key linking to the RERA
registry project (source #1). Per-unit size + possession are usually absent and
back-filled from the linked RERA project.
"""
from __future__ import annotations

import argparse

from scrapers.builder_base import run as _run
from scrapers.builders import SPECS


def run(only_target=False, verbose=True) -> dict:
    return _run(SPECS["Prestige"], only_target=only_target, verbose=verbose)


def main() -> None:
    ap = argparse.ArgumentParser(description="Prestige builder scraper")
    ap.add_argument("--only-target-localities", action="store_true",
                    help="store only Whitefield/Marathahalli projects")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(only_target=args.only_target_localities, verbose=not args.quiet)


if __name__ == "__main__":
    main()
