#!/usr/bin/env python3
"""Live price check for one card, from a shop price to a maximum buy price.

    python scripts/price_check.py SV2a-173 --price 4500
    python scripts/price_check.py SV2a-173 --price 4500 --scenario C_proxy \\
        --proxy-rate 0.10 --shipping-jpy 2500
    make price-check CARD=SV2a-173 PRICE=4500

Everything is fetched live: Cardmarket's published averages for the card via
TCGdex, and the ECB reference rates. Nothing needs a database. The card id is
TCGdex's own, which for Japanese cards is the set code and the number, for
example SV2a-173 (the Japanese 151 Pikachu illustration rare) or M2-115.

What it will and will not tell you:

* The resale value is **average-based**, never sales-based. The verdict is
  therefore INDICATIVE at best: it shows whether the price is inside the
  maximum buy price, but it never claims an opportunity, because the number of
  sales behind the averages and the card's liquidity are both unknown.
* Condition is not modelled here (no shop grade priors without the database),
  so the value is a near-mint equivalent. A card in worse condition is worth
  less than this.
* When any acceptance rule fails, it says which one and stops.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from pokearb_core.adapters.live import EcbFxAdapter, TcgdexPricingAdapter  # noqa: E402
from pokearb_core.arbitrage.engine import (  # noqa: E402
    CostAssumptionError,
    CostAssumptions,
    compute_arbitrage,
)
from pokearb_core.arbitrage.policy import PolicyUnverifiedError  # noqa: E402
from pokearb_core.arbitrage.reference_policy import reference_policy  # noqa: E402
from pokearb_core.types import (  # noqa: E402
    AcquisitionPurpose,
    Currency,
    Money,
    Printing,
    Scenario,
)
from pokearb_core.valuation.benchmark import assess_market_average  # noqa: E402


def _default_printing(variants: dict) -> Printing | None:
    has_normal, has_holo = bool(variants.get("normal")), bool(variants.get("holo"))
    if has_holo and not has_normal:
        return Printing.HOLO
    if has_normal and not has_holo:
        return Printing.NON_HOLO
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("card", help="TCGdex card id, e.g. SV2a-173")
    ap.add_argument("--price", type=Decimal, required=True, help="shelf price in JPY")
    ap.add_argument("--lang", default="ja", help="card language on TCGdex (default ja)")
    ap.add_argument("--printing", choices=[p.value for p in Printing],
                    help="default: inferred when the card has only one base printing")
    ap.add_argument("--scenario", default=Scenario.HAND_CARRY.value,
                    choices=[s.value for s in Scenario])
    ap.add_argument("--purpose", default=AcquisitionPurpose.RESALE.value,
                    choices=[p.value for p in AcquisitionPurpose])
    ap.add_argument("--roi", type=Decimal, default=Decimal("0.40"),
                    help="required return on capital (default 0.40)")
    ap.add_argument("--proxy-rate", type=Decimal, default=Decimal("0"))
    ap.add_argument("--shipping-jpy", type=Decimal, default=Decimal("0"),
                    help="international shipping in JPY")
    ap.add_argument("--outbound-eur", type=Decimal, default=Decimal("0"),
                    help="your cost to ship to the European buyer")
    ap.add_argument("--duty-rate", type=Decimal, default=None,
                    help="verified tariff rate; without it, above-threshold cases refuse")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    print(f"Price check  {args.card}  at {args.price:.0f} JPY   ({now:%Y-%m-%d %H:%M} UTC)")
    print("-" * 72)

    tcg = TcgdexPricingAdapter()
    try:
        card_payload, _ = tcg.card(args.card, args.lang)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not fetch {args.card} from TCGdex: {exc}")
        print("If this runs in a Claude Code cloud session, allow api.tcgdex.net")
        print("and www.ecb.europa.eu in the environment's network settings.")
        return 2
    if "id" not in card_payload:
        print(f"TCGdex has no card {args.card!r} in language {args.lang!r}.")
        return 2

    name = card_payload.get("name")
    rarity = card_payload.get("rarity")
    set_name = (card_payload.get("set") or {}).get("name")
    print(f"Card         {name}  |  {rarity}  |  {set_name}")

    printing = Printing(args.printing) if args.printing else _default_printing(
        card_payload.get("variants") or {}
    )
    if printing is None:
        print(
            "This card exists in more than one base printing. Say which one with "
            "--printing non-holo, holo or reverse-holo."
        )
        return 2

    seen = tcg.observe(args.card, printing=printing, variant_id=args.card, language=args.lang)
    bench = assess_market_average(
        seen["observation"], as_of=datetime.now(timezone.utc),
        sibling_product_ids=seen["sibling_product_ids"],
        parser_reasons=seen["reasons"],
    )
    obs = bench.observation
    if obs is not None:
        figures = "  ".join(
            f"{k} {v}" for k, v in (
                ("avg", obs.avg), ("7d", obs.avg7), ("30d", obs.avg30),
                ("trend", obs.trend), ("low", obs.low),
            ) if v is not None
        )
        print(f"Cardmarket   {figures}  EUR   (product {obs.product_id}, "
              f"updated {obs.provider_updated_at:%Y-%m-%d})")
    print(f"Siblings     {len(seen['sibling_ids'])} same-name card(s) checked for "
          "a shared Cardmarket listing")

    if bench.refused:
        print("\nVERDICT      INSUFFICIENT DATA")
        for r in bench.reasons:
            print(f"  - {r}")
        return 1

    print(f"EU value     {bench.value.amount} EUR  ({bench.statistic})")

    try:
        fx = EcbFxAdapter.as_map(EcbFxAdapter().fetch_rates())
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not fetch ECB exchange rates: {exc}")
        print("If this runs in a Claude Code cloud session, allow www.ecb.europa.eu")
        print("and api.tcgdex.net in the environment's network settings.")
        return 2
    jpy_eur = fx[(Currency.JPY, Currency.EUR)]
    print(f"FX           1 JPY = {jpy_eur.rate} EUR  (ECB, {jpy_eur.as_of})")

    try:
        assumptions = CostAssumptions(
            scenario=Scenario(args.scenario),
            acquisition_purpose=AcquisitionPurpose(args.purpose),
            proxy_fee_rate=args.proxy_rate,
            international_shipping_jpy=args.shipping_jpy,
            outbound_shipping_eur=args.outbound_eur,
            duty_rate=args.duty_rate,
        ).validated()
        arb = compute_arbitrage(
            purchase_jpy=Money(args.price, Currency.JPY),
            gross_resale_eur=bench.value,
            on=now.date(),
            assumptions=assumptions,
            policy=reference_policy(),
            fx_rates=fx,
            required_roi=args.roi,
        )
    except (CostAssumptionError, PolicyUnverifiedError) as exc:
        print("\nVERDICT      INSUFFICIENT DATA")
        print(f"  - {exc}")
        return 1

    lc = arb.landed
    print(f"Landed cost  {lc.total_eur.amount} EUR economic, "
          f"{lc.upfront_cash_eur.amount} EUR cash up front")
    print(f"Net proceeds {arb.net_proceeds_eur.amount} EUR after selling costs")
    print(f"Profit       {arb.expected_profit_eur.amount} EUR   ROI {arb.roi:.1%} "
          f"on cash deployed")
    cap = arb.max_buy_jpy
    print(f"Max buy      {cap.amount:.0f} JPY for a {args.roi:.0%} return"
          if cap else f"Max buy      none: no price clears a {args.roi:.0%} return")

    within = cap is not None and args.price <= cap.amount
    print("\nVERDICT      INDICATIVE, " + (
        f"inside the max buy by {cap.amount - args.price:.0f} JPY" if within
        else "above the max buy" if cap else "no price works"
    ))
    print("  - average-based: the number of sales behind the Cardmarket figures")
    print("    and the card's liquidity are unknown, so this is never an")
    print("    opportunity call, only a price ceiling")
    print("  - condition not modelled: the value is a near-mint equivalent")
    for n in lc.notes:
        print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
