"""Command line entry points.

    python -m pokearb.cli build              scrape every shop, then build site/
    python -m pokearb.cli build --offline    rebuild site/ from cached data only
    python -m pokearb.cli scrape             scrape only, write data/latest.json
    python -m pokearb.cli render             build site/ from data/latest.json
    python -m pokearb.cli check-shop <key>   fetch one shop and report
    python -m pokearb.cli probe-kelz0r       verify the Kelz0r HTML selectors
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .adapters import build_adapter
from .classify import SetMatcher, classify
from .config import SHOPS, SHOPS_BY_KEY
from .http import PoliteSession
from .pipeline import LATEST_PATH, products_from_payload, run
from .render import render_site
from .sets import load_cached_sets


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scrape(args) -> int:
    shops = _select_shops(args.only)
    run(offline=args.offline, shops=shops)
    return 0


def cmd_render(args) -> int:
    if not LATEST_PATH.exists():
        print(
            "data/latest.json findes ikke. Koer 'python -m pokearb.cli scrape' foerst.",
            file=sys.stderr,
        )
        return 1
    payload = json.loads(LATEST_PATH.read_text("utf-8"))
    render_site(payload, products_from_payload(payload))
    return 0


def cmd_build(args) -> int:
    shops = _select_shops(args.only)
    payload = run(offline=args.offline, shops=shops)
    render_site(payload, products_from_payload(payload))
    return 0


def cmd_check_shop(args) -> int:
    shop = SHOPS_BY_KEY.get(args.key)
    if shop is None:
        print(
            f"Ukendt butik '{args.key}'. Kendte: {', '.join(sorted(SHOPS_BY_KEY))}",
            file=sys.stderr,
        )
        return 1

    session = PoliteSession()
    adapter = build_adapter(shop, session)
    offers = adapter.fetch_offers()
    print(f"{shop.name}: {len(offers)} varianter hentet")

    _, aliases = load_cached_sets()
    matcher = SetMatcher(aliases) if aliases else None
    if matcher is None:
        print("Ingen saet-cache endnu, saa klassifikationen koeres uden saetmatch.")

    accepted, rejected = [], {}
    for offer in offers:
        result = classify(offer.title, matcher)
        if result.accepted:
            accepted.append((offer, result))
        else:
            rejected[result.reason] = rejected.get(result.reason, 0) + 1

    print(f"Accepteret: {len(accepted)}")
    for offer, result in accepted[:25]:
        flag = "" if result.set_matched else "  [saet uden match]"
        print(
            f"  {result.product_type.value:<14} {result.language.value}  "
            f"{result.set_name:<28} {offer.amount:>9.2f} {offer.currency}"
            f"{flag}   {offer.title[:70]}"
        )
    print("Afvist, fordelt paa aarsag:")
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:>6}  {reason}")
    return 0


def cmd_probe_kelz0r(args) -> int:
    from bs4 import BeautifulSoup

    from .adapters.kelz0r import CATEGORY_PATHS, SELECTORS, Kelz0rAdapter, _blocks

    shop = SHOPS_BY_KEY["kelz0r"]
    session = PoliteSession()
    adapter = Kelz0rAdapter(shop, session)

    for path in CATEGORY_PATHS:
        url = shop.base_url.rstrip("/") + path
        print(f"\n=== {url}")
        response = session.get(url)
        print(f"HTTP {response.status_code}, {len(response.text)} tegn")
        if response.status_code != 200:
            continue

        soup = BeautifulSoup(response.text, "lxml")
        rows, selector = _blocks(soup)
        print(f"Produktblokke: {len(rows)} via selektoren '{selector or 'ingen'}'")

        offers = adapter.parse_listing(response.text, url)
        print(f"Parsede varer: {len(offers)}")
        for offer in offers[:5]:
            print(f"  {offer.amount:>9.2f} {offer.currency}  {'lager' if offer.in_stock else 'udsolgt':<8} {offer.title[:70]}")

        if not offers:
            print("\nIngen varer parset. Kandidatselektorer der blev proevet:")
            for key, candidates in SELECTORS.items():
                print(f"  {key}: {candidates}")
            print("\nFoerste 1500 tegn af <body>, saa du kan se den faktiske struktur:")
            body = soup.body
            print((body.decode()[:1500] if body else response.text[:1500]))
    return 0


def _select_shops(only: str | None):
    if not only:
        return SHOPS
    keys = {k.strip() for k in only.split(",") if k.strip()}
    unknown = keys - set(SHOPS_BY_KEY)
    if unknown:
        raise SystemExit(f"Ukendte butikker: {', '.join(sorted(unknown))}")
    return tuple(s for s in SHOPS if s.key in keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pokearb", description="PokeArb prispipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("scrape", cmd_scrape, "hent priser fra butikkerne"),
        ("build", cmd_build, "hent priser og byg sitet"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--offline", action="store_true", help="brug kun cachede data")
        p.add_argument("--only", help="kommasepareret liste af butiksnoegler")
        p.set_defaults(handler=handler)

    p_render = sub.add_parser("render", help="byg sitet ud fra data/latest.json")
    p_render.set_defaults(handler=cmd_render)

    p_check = sub.add_parser("check-shop", help="hent en enkelt butik og vis resultatet")
    p_check.add_argument("key")
    p_check.set_defaults(handler=cmd_check_shop)

    p_probe = sub.add_parser("probe-kelz0r", help="tjek HTML-selektorerne for Kelz0r")
    p_probe.set_defaults(handler=cmd_probe_kelz0r)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.handler(args)
    except RuntimeError as exc:
        # A missing FX cache on a fresh clone is the common case. Say what is
        # wrong in one line rather than dumping a traceback.
        print(f"\nAfbrudt: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAfbrudt af brugeren.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
