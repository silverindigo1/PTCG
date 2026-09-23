"""End-to-end smoke test against a couple of live shops.

The set list and the FX rates are STUBBED here, deliberately: this script
exercises the adapters, the classifier, the grouping and the renderer without
depending on TCGdex or the ECB being reachable from wherever you run it. The
real run (`python -m pokearb.cli build`) calls both of those live.

    python scripts/smoke_test.py tcgviert pocketmonster
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokearb import history  # noqa: E402
from pokearb.classify import SetMatcher, normalise  # noqa: E402
from pokearb.config import SHOPS_BY_KEY  # noqa: E402
from pokearb.fx import FxRates  # noqa: E402
from pokearb.http import PoliteSession  # noqa: E402
from pokearb.pipeline import _dedupe_within_shop, build_products, collect_offers  # noqa: E402
from pokearb.render import render_site  # noqa: E402

# Placeholder rates. NOT ECB data, and never written to data/fx.json.
STUB_RATES = {"EUR": 1.0, "DKK": 7.46, "SEK": 11.0}

STUB_SETS = [
    # A local stand-in for the live TCGdex list, used ONLY by this script so it
    # can run without reaching api.tcgdex.net. The real pipeline fetches the
    # list live. Covers the Western, Japanese, Korean and Chinese lines that
    # actually turned up in the shops' catalogues.
    ("meg", "Mega Evolution", "2026-09-26"),
    ("m4", "Ninja Spinner", "2026-08-08"),
    ("m3", "Nihil Zero", "2026-06-12"),
    ("m2", "Inferno X", "2026-04-24"),
    ("m1", "Mega Brave", "2026-02-06"),
    ("m6", "Storm Emeralda", None),
    ("cel30", "30th Celebration", "2026-02-27"),
    ("sv11b", "Black Bolt", "2025-07-18"),
    ("sv11w", "White Flare", "2025-07-18"),
    ("sv10", "Destined Rivals", "2025-05-30"),
    ("sv09", "Journey Together", "2025-03-28"),
    ("sv08.5", "Prismatic Evolutions", "2025-01-17"),
    ("sv08", "Surging Sparks", "2024-11-08"),
    ("sv07", "Stellar Crown", "2024-09-13"),
    ("sv06.5", "Shrouded Fable", "2024-08-02"),
    ("sv06", "Twilight Masquerade", "2024-05-24"),
    ("sv05", "Temporal Forces", "2024-03-22"),
    ("sv04.5", "Paldean Fates", "2024-01-26"),
    ("sv04", "Paradox Rift", "2023-11-03"),
    ("sv03.5", "151", "2023-09-22"),
    ("sv03", "Obsidian Flames", "2023-08-11"),
    ("sv02", "Paldea Evolved", "2023-06-09"),
    ("sv01", "Scarlet and Violet", "2023-03-31"),
    ("swsh12.5", "Crown Zenith", "2023-01-20"),
    ("swsh12", "Silver Tempest", "2022-11-11"),
    ("swsh11", "Lost Origin", "2022-09-09"),
    ("swsh10", "Astral Radiance", "2022-05-27"),
    ("swsh9", "Brilliant Stars", "2022-02-25"),
    ("swsh8", "Fusion Strike", "2021-11-12"),
    ("swsh7", "Evolving Skies", "2021-08-27"),
    ("swsh6", "Chilling Reign", "2021-06-18"),
    ("swsh5", "Battle Styles", "2021-03-19"),
    ("cel25", "Celebrations", "2021-10-08"),
    # Japanese and Korean lines, Latin-script names as TCGdex lists them
    ("sv9a", "Heat Wave Arena", "2025-02-28"),
    ("sv9", "Battle Partners", "2025-01-24"),
    ("sv8a", "Terastal Festival", "2024-10-18"),
    ("sv7a", "Paradise Dragona", "2024-09-13"),
    ("sv7", "Stellar Miracle", "2024-07-19"),
    ("sv6a", "Night Wanderer", "2024-06-07"),
    ("sv6", "Mask of Change", "2024-04-26"),
    ("sv5m", "Cyber Judge", "2024-01-26"),
    ("sv5k", "Wild Force", "2024-01-26"),
    ("sv4k", "Ancient Roar", "2023-10-27"),
    ("sv4m", "Future Flash", "2023-10-27"),
    ("sv4a", "Shiny Treasure ex", "2023-12-01"),
    ("sv3a", "Raging Surf", "2023-09-22"),
    ("sv2d", "Clay Burst", "2023-04-14"),
    ("sv2a", "Pokemon Card 151", "2023-06-16"),
    ("sv1a", "Triplet Beat", "2023-03-10"),
    ("s12a", "VSTAR Universe", "2022-12-02"),
    ("s11a", "Incandescent Arcana", "2022-09-02"),
    ("s8b", "VMAX Climax", "2021-12-17"),
    ("s6a", "Eevee Heroes", "2021-05-28"),
    ("s7r", "Blue Sky Stream", "2021-07-09"),
    ("s3a", "Legendary Heartbeat", "2020-07-10"),
    ("sv5a", "Crimson Haze", "2024-02-23"),
    ("sv8", "Super Electric Breaker", "2024-11-08"),
    ("sv1s", "Scarlet ex", "2023-01-20"),
    ("sv1v", "Violet ex", "2023-01-20"),
    # Chinese line
    ("cs1c", "Eternal Birth", "2023-06-01"),
    ("cs2c", "Miracle Journey", "2023-09-01"),
    ("cs3c", "Primordial Arts", "2024-01-01"),
    ("cs3.5c", "Scorching Skies", "2024-03-01"),
    ("cs4c", "Bonus Round", "2024-06-01"),
    ("cs5.5c", "Shadow of Glory", "2024-11-01"),
    ("csv3c", "Fearless Terastal", "2025-03-01"),
    ("cbb5c", "Gem Pack Vol 5", "2025-05-01"),
    ("c151", "Collect 151 Journey", "2024-08-01"),
    ("ctp", "Towering Perfection", "2024-05-01"),
    ("cbc", "Brilliant Counterattack", "2025-01-01"),
    ("cvsg", "Victory Star Guide", "2024-10-01"),
]
STUB_SETS = [{"id": i, "name": n, "releaseDate": d} for i, n, d in STUB_SETS]


def main(keys: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    shops = tuple(SHOPS_BY_KEY[k] for k in keys)
    session = PoliteSession()
    offers, run_log = collect_offers(session, shops)
    print(f"\nRaa varianter hentet: {len(offers)}")

    matcher = SetMatcher({normalise(s["name"]): s for s in STUB_SETS})
    fx = FxRates(STUB_RATES, "PLACEHOLDER-IKKE-ECB")
    products, stats = build_products(offers, matcher, fx)
    _dedupe_within_shop(products)
    products = [p for p in products if p.offers]

    print(f"Produkter: {len(products)}")
    print(f"Accepteret: {stats['accepted']}, afvist: {stats['rejected']}")
    print("Top afvisningsaarsager:")
    for reason, count in list(stats["rejection_breakdown"].items())[:10]:
        print(f"  {count:>6}  {reason}")

    print("\nEksempler paa accepterede produkter:")
    for product in products[:15]:
        cheapest = product.cheapest
        price = f"{cheapest.amount_dkk:.0f} kr" if cheapest else "ingen pris"
        flag = "" if product.set_matched else "  [uden saetmatch]"
        print(f"  {price:>10}  {product.title_da}{flag}")

    history.record(products)
    payload = {
        "generated_at": "2026-09-22",
        "fx": {"as_of": fx.as_of, "stale": True, "rates": fx.rates},
        "shops": run_log,
        "stats": stats,
        "products": [],
    }
    render_site(payload, products)
    print("\nSite bygget i site/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["tcgviert"]))
