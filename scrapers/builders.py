"""Per-builder specs + CLI over the shared `builder_base` crawler.

Usage:
    python -m scrapers.builders <name> [--only-target]
    python -m scrapers.builders --all
    python -m scrapers.builders --list

`name` must match a seeded builder/source name (see src/config.py).
"""
from __future__ import annotations

import sys

from scrapers.builder_base import BuilderSpec, run

SPECS: dict[str, BuilderSpec] = {
    "Prestige": BuilderSpec(
        name="Prestige",
        list_urls=[
            "https://www.prestigeconstructions.com/residential-projects/bangalore",
            "https://www.prestigeconstructions.com/",
        ],
        project_re=r"prestigeconstructions\.com/residential-projects/bangalore/[^/]+$",
    ),
    "Sobha": BuilderSpec(
        name="Sobha",
        list_urls=[
            "https://www.sobha.com/city/bengaluru/",
            "https://www.sobha.com/",
        ],
        project_re=r"sobha\.com/bengaluru/[^/]+/?$",
    ),
    "Brigade": BuilderSpec(
        name="Brigade",
        list_urls=[
            "https://www.brigadegroup.com/residential/projects/bengaluru",
        ],
        # residential-only: skip commercial/retail/hospitality that share /projects/bengaluru
        project_re=r"brigadegroup\.com/residential/projects/bengaluru/[^/]+$",
    ),
    "Puravankara": BuilderSpec(
        name="Puravankara",
        list_urls=[
            "https://www.puravankara.com/residential/bengaluru",
            "https://www.puravankara.com/bengaluru/",
        ],
        # project detail pages: /residential/bengaluru/<project-slug> (the bare
        # /residential/bengaluru listing page has no trailing slug -> excluded)
        project_re=r"puravankara\.com/residential/bengaluru/[^/]+$",
    ),
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--list":
        print("Available builders:", ", ".join(SPECS))
        return 0
    only_target = "--only-target" in argv
    names = list(SPECS) if argv[0] == "--all" else [a for a in argv if not a.startswith("--")]
    for name in names:
        spec = SPECS.get(name)
        if not spec:
            print(f"Unknown builder {name!r}. Known: {', '.join(SPECS)}")
            continue
        run(spec, only_target=only_target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
