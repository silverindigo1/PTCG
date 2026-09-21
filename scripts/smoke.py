"""End-to-end smoke test against live ECB rates.

Runs the actual pipeline on a worked example and prints every intermediate
number with its provenance, so you can see what the system would tell you
standing in a shop, and see where each figure came from.

Usage:  PYTHONPATH=packages/core python scripts/smoke.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pokearb_core.adapters.live import EcbFxAdapter
from pokearb_core.arbitrage.engine import (
    CostAssumptions,
    compute_arbitrage,
    resale_economics_fn,
)
from pokearb_core.arbitrage.policy import PolicyParameter, PolicyStore
from pokearb_core.condition.model import (
    ConditionEvidence,
    DirichletPrior,
    condition_confidence,
    expected_value,
    posterior,
)
from pokearb_core.identity.matcher import AliasIndex, ObservedCard, match_card
from pokearb_core.liquidity.model import compute_liquidity
from pokearb_core.scoring.opportunity import assess_opportunity
from pokearb_core.types import (
    CardVariant,
    Currency,
    EuCondition,
    Language,
    Listing,
    Money,
    Printing,
    Sale,
    Scenario,
)
from pokearb_core.valuation.fair_value import compute_fair_value_curve

NOW = datetime.now(timezone.utc)
SKAT = "https://skat.dk/skole/onlineshopping/naar-du-koeber"
JG = "https://www.japan-guide.com/news/tax-free-shopping.html"


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> None:
    # ---------------------------------------------------------- identity ----
    rule("1. IDENTITY")
    universe = [
        CardVariant(
            variant_id="v-pikachu-114-smp-holo", language=Language.JA,
            set_code="SM-P", number="114", printing=Printing.HOLO,
            name_en="Pikachu", name_ja="ピカチュウ", pokemon_slug="pikachu",
            year=2018, sibling_variant_ids=("v-pikachu-114-smp-reverse",),
        ),
        CardVariant(
            variant_id="v-pikachu-114-smp-reverse", language=Language.JA,
            set_code="SM-P", number="114", printing=Printing.REVERSE_HOLO,
            name_en="Pikachu", name_ja="ピカチュウ", pokemon_slug="pikachu",
            year=2018, sibling_variant_ids=("v-pikachu-114-smp-holo",),
        ),
    ]
    aliases = AliasIndex({"pikachu": ["ピカチュウ", "Pikachu"]})

    vague = ObservedCard(
        raw_title="ピカチュウ 114/SM-P", language=Language.JA,
        set_code="SM-P", number="114", name="ピカチュウ",
    )
    vague_result = match_card(vague, universe, aliases)
    print(f"  tag says only 'ピカチュウ 114/SM-P' -> {vague_result.outcome.value}")
    for p in vague_result.best.penalties:
        print(f"    penalty: {p}")

    precise = ObservedCard(
        raw_title="ピカチュウ 114/SM-P ホロ", language=Language.JA,
        set_code="SM-P", number="114", printing=Printing.HOLO, name="ピカチュウ",
    )
    result = match_card(precise, universe, aliases)
    print(f"  tag also says holo        -> {result.outcome.value} "
          f"@ {result.best.confidence}")
    print(f"  canonical key: {result.best.variant.canonical_key}")

    # --------------------------------------------------------- fair value ----
    rule("2. FAIR VALUE from completed sales only")
    sales = [
        Sale(f"s{i}", "v-pikachu-114-smp-holo", "ebay_sold",
             NOW - timedelta(days=d), Money(Decimal(p), Currency.EUR),
             c, Language.JA, f"https://example.invalid/sale/s{i}")
        for i, (p, d, c) in enumerate(
            [("175", 4, EuCondition.NM), ("168", 11, EuCondition.EX),
             ("182", 19, EuCondition.NM), ("176", 28, EuCondition.NM),
             ("159", 41, EuCondition.EX), ("188", 55, EuCondition.NM),
             ("790", 60, EuCondition.NM)], start=1)
    ]
    fx = EcbFxAdapter.as_map(EcbFxAdapter().fetch_rates())
    curve = compute_fair_value_curve(
        sales, variant_id="v-pikachu-114-smp-holo", as_of=NOW, fx_rates=fx
    )
    for window, fv in curve.items():
        val = f"{fv.value.amount} EUR" if fv.value else "INSUFFICIENT EVIDENCE"
        print(f"  {window:>8}: {val:<26} n={fv.n_sales} n_eff={fv.n_effective:.2f}")
    fv = curve["current"]
    for ex in fv.excluded:
        print(f"    excluded {ex.record_id}: {ex.note}")

    # ---------------------------------------------------------- condition ----
    rule("3. CONDITION: shop grade 'A-' as a distribution, not a mapping")
    prior = DirichletPrior(
        "cardrush", "A-",
        {EuCondition.NM: Decimal("1.30"), EuCondition.EX: Decimal("1.00"),
         EuCondition.GD: Decimal("0.40"), EuCondition.LP: Decimal("0.20"),
         EuCondition.PL: Decimal("0.07"), EuCondition.PO: Decimal("0.03")},
        evidence_n=0, is_provisional=True,
    )
    dist = posterior(prior, ConditionEvidence(has_photos=True, photo_count=3))
    for cond, p in dist.probabilities.items():
        print(f"  {cond.value}: {p:.1%}")
    print(f"  condition confidence: {condition_confidence(dist)}")
    for n in dist.notes:
        print(f"    note: {n}")

    base = fv.value.amount
    ladder = {
        EuCondition.NM: Money(base, Currency.EUR),
        EuCondition.EX: Money(base * Decimal("0.85"), Currency.EUR),
        EuCondition.GD: Money(base * Decimal("0.72"), Currency.EUR),
        EuCondition.LP: Money(base * Decimal("0.62"), Currency.EUR),
        EuCondition.PL: Money(base * Decimal("0.45"), Currency.EUR),
        EuCondition.PO: Money(base * Decimal("0.28"), Currency.EUR),
    }
    adjusted = expected_value(dist, ladder)
    print(f"  NM fair value            : {base} EUR")
    print(f"  condition-adjusted value : {adjusted.amount.quantize(Decimal('0.01'))} EUR")

    # ---------------------------------------------------------- liquidity ----
    rule("4. LIQUIDITY")
    listings = [
        Listing(f"l{i}", "v-pikachu-114-smp-holo", "cardmarket", NOW,
                Money(Decimal("195"), Currency.EUR), EuCondition.NM, Language.JA)
        for i in range(6)
    ]
    liq = compute_liquidity(sales, listings, as_of=NOW)
    print(f"  band={liq.band.value} score={liq.score} "
          f"days_to_sell={liq.expected_days_to_sell}")
    for r in liq.reasons:
        print(f"    {r}")

    # ---------------------------------------------------------- arbitrage ----
    rule("5. ARBITRAGE at a 6,500 JPY shelf price")
    policy = PolicyStore([
        PolicyParameter("dk.import_vat_rate", Decimal("0.25"), "rate", date(2021, 7, 1), None, SKAT),
        PolicyParameter("dk.duty_threshold_eur", Decimal("150"), "EUR", date(2021, 7, 1), None, SKAT),
        PolicyParameter("dk.low_value_per_item_charge_eur", Decimal("3"), "EUR", date(2026, 7, 1), None, SKAT),
        PolicyParameter("dk.carrier_handling_fee_dkk", Decimal("200"), "DKK", date(2021, 7, 1), None, SKAT),
        PolicyParameter("dk.traveller_allowance_air_dkk", Decimal("3250"), "DKK", date(2021, 7, 1), None, SKAT),
        PolicyParameter("jp.consumption_tax_rate", Decimal("0.10"), "rate", date(2019, 10, 1), None, JG),
        PolicyParameter("jp.tax_free_minimum_jpy", Decimal("5000"), "JPY", date(2019, 10, 1), None, JG),
        PolicyParameter("jp.tax_free_refund_at_departure_from", Decimal("1"), "count", date(2026, 11, 1), None, JG),
    ])
    assumptions = CostAssumptions(scenario=Scenario.HAND_CARRY)
    arb = compute_arbitrage(
        purchase_jpy=Money(Decimal("6500"), Currency.JPY),
        gross_resale_eur=adjusted, on=NOW.date(), assumptions=assumptions,
        policy=policy, fx_rates=fx, required_roi=Decimal("0.40"),
    )
    print(f"  landed cost        : {arb.landed.total_eur.amount} EUR "
          f"/ {arb.landed.total_dkk.amount} DKK")
    print(f"  net proceeds       : {arb.net_proceeds_eur.amount} EUR")
    print(f"  expected profit    : {arb.expected_profit_eur.amount} EUR "
          f"/ {arb.expected_profit_dkk.amount} DKK")
    print(f"  point ROI          : {arb.roi:.1%}")
    print(f"  gross / net multiple: {arb.gross_multiple:.2f}x / {arb.net_multiple:.2f}x")
    print(f"  break-even resale  : {arb.break_even_resale_eur.amount} EUR")
    print(f"  MAX BUY            : {arb.max_buy_jpy.amount} JPY  (40% ROI)")
    print(f"  target buy         : {arb.target_buy_jpy.amount} JPY  (60% ROI)")
    print(f"  strong buy         : {arb.strong_buy_jpy.amount} JPY  (80% ROI)")
    for n in arb.notes:
        print(f"    note: {n}")

    # --------------------------------------------------------- assessment ----
    rule("6. RISK-ADJUSTED ASSESSMENT")
    assessment = assess_opportunity(
        economics=resale_economics_fn(
            purchase_jpy=Money(Decimal("6500"), Currency.JPY),
            on=NOW.date(), assumptions=assumptions, policy=policy, fx_rates=fx,
        ),
        gross_resale=adjusted,
        fair_value=fv, liquidity=liq, condition_dist=dist,
        match_confidence=result.best.confidence, as_of=NOW, source_count=1,
        required_roi=Decimal("0.40"),
        max_buy_jpy=arb.max_buy_jpy,
        purchase_jpy=Money(Decimal("6500"), Currency.JPY),
    )
    if assessment.suppressed:
        print("  VERDICT: SUPPRESSED")
        for d in assessment.suppression_detail:
            print(f"    {d}")
    else:
        print(f"  point ROI    : {assessment.point_roi:.1%}")
        print(f"  ROI p10/p25  : {assessment.roi_p10:.1%} / {assessment.roi_p25:.1%}")
        print(f"  RANKING ROI  : {assessment.ranking_roi:.1%}  (p25, not the point estimate)")
        if assessment.annualised_roi:
            print(f"  annualised   : {assessment.annualised_roi:.1%}")
    print()
    for s in assessment.scores:
        print(s.explain())
    print("\n  data quality:")
    for r in assessment.data_quality.reasons:
        print(f"    {r}")

    rule("7. FX PROVENANCE")
    for pair in [(Currency.JPY, Currency.EUR), (Currency.EUR, Currency.DKK)]:
        r = fx[pair]
        print(f"  {r.base.value}->{r.quote.value} = {r.rate}  as of {r.as_of}")
        print(f"    {r.source_url}")


if __name__ == "__main__":
    main()
