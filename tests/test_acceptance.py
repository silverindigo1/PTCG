"""Acceptance tests from the brief.

Each test is named for the numbered requirement it enforces. These are the
tests that decide whether the product is finished, so they are written against
the behaviour the brief describes rather than against the implementation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pokearb_core.adapters.gated import (
    CardmarketAdapter,
    PsaCertAdapter,
    PriceChartingAdapter,
)
from pokearb_core.adapters.base import PolicyViolationError, SourceDisabledError
from pokearb_core.arbitrage.engine import (
    CostAssumptionError,
    CostAssumptions,
    compute_arbitrage,
    max_buy_price,
    resale_economics_fn,
)
from pokearb_core.condition.model import (
    ConditionEvidence,
    DirichletPrior,
    condition_confidence,
    expected_value,
    posterior,
)
from pokearb_core.identity.matcher import (
    AliasIndex,
    MatchOutcome,
    ObservedCard,
    match_card,
)
from pokearb_core.liquidity.model import LiquidityBand, compute_liquidity
from pokearb_core.scoring.opportunity import (
    RiskConfig,
    SuppressionReason,
    assess_opportunity,
)
from pokearb_core.types import (
    AcquisitionPurpose,
    CardVariant,
    Currency,
    Edition,
    EuCondition,
    FairValue,
    FxRate,
    Language,
    Listing,
    Money,
    Printing,
    Sale,
    Scenario,
)
from pokearb_core.valuation.fair_value import FairValueConfig, compute_fair_value

from conftest import NOW, eur, make_sale


# ---------------------------------------------------------------- Test 1 ----
def test_1_exact_variant_identified(universe, aliases):
    observed = ObservedCard(
        raw_title="ピカチュウ 114/SM-P プロモ ホロ",
        language=Language.JA,
        set_code="SM-P",
        number="114",
        printing=Printing.HOLO,
        edition=Edition.NOT_APPLICABLE,
        stamp="none",
        name="ピカチュウ",
    )
    result = match_card(observed, universe, aliases)
    assert result.outcome is MatchOutcome.AUTO
    assert result.variant_id == "v-pikachu-114-smp-holo"


# ---------------------------------------------------------------- Test 2 ----
def test_2_different_card_numbers_are_rejected(universe, aliases):
    """Same Pokemon, different printed number, must never match."""
    observed = ObservedCard(
        raw_title="Pikachu 147/S-P",
        language=Language.JA,
        set_code="S-P",
        number="147",
        printing=Printing.HOLO,
        name="Pikachu",
    )
    result = match_card(observed, universe, aliases)
    # The 144/S-P variant exists in the universe and must be rejected outright.
    rejected_ids = {c.variant.variant_id for c in result.rejected}
    assert "v-pikachu-144-sp-holo" in rejected_ids
    failure_text = " ".join(
        f for c in result.rejected for f in c.gate_failures
    )
    assert "card number differs" in failure_text


def test_2b_holo_never_matches_reverse_holo(universe, aliases):
    observed = ObservedCard(
        raw_title="Pikachu 114/SM-P reverse holo",
        language=Language.JA, set_code="SM-P", number="114",
        printing=Printing.REVERSE_HOLO, name="Pikachu",
    )
    result = match_card(observed, universe, aliases)
    assert result.best is not None
    assert result.best.variant.printing is Printing.REVERSE_HOLO


def test_2c_first_edition_never_matches_unlimited(universe, aliases):
    observed = ObservedCard(
        raw_title="Charizard 4/102 1st Edition",
        language=Language.EN, set_code="BASE", number="4",
        printing=Printing.HOLO, edition=Edition.FIRST, name="Charizard",
    )
    result = match_card(observed, universe, aliases)
    rejected_ids = {c.variant.variant_id for c in result.rejected}
    assert "v-charizard-4-base-unlimited" in rejected_ids


def test_2d_japanese_promo_never_matches_english_printing(universe, aliases):
    observed = ObservedCard(
        raw_title="Pikachu 114 English promo",
        language=Language.EN, set_code="SM-P", number="114",
        printing=Printing.HOLO, name="Pikachu",
    )
    result = match_card(observed, universe, aliases)
    for candidate in result.rejected:
        if candidate.variant.variant_id == "v-pikachu-114-smp-holo":
            assert any("language differs" in f for f in candidate.gate_failures)
            break
    else:  # pragma: no cover
        pytest.fail("Japanese variant was not rejected on language")


def test_2e_language_alias_does_not_override_number(universe, aliases):
    """ブラッキー and Umbreon are the same Pokemon. That is not enough."""
    assert aliases.same_pokemon("ブラッキー", "Umbreon") is True
    observed = ObservedCard(
        raw_title="ブラッキー",
        language=Language.JA, set_code="SM-P", number="999",
        printing=Printing.HOLO, name="ブラッキー",
    )
    result = match_card(observed, universe, aliases)
    assert result.outcome is MatchOutcome.REJECT


def test_ambiguous_printing_routes_to_manual_review(universe, aliases):
    """Printing unstated while holo and reverse both exist: never auto-match."""
    observed = ObservedCard(
        raw_title="ピカチュウ 114/SM-P",
        language=Language.JA, set_code="SM-P", number="114",
        printing=None, name="ピカチュウ",
    )
    result = match_card(observed, universe, aliases)
    assert result.outcome is not MatchOutcome.AUTO
    assert result.best is not None
    assert any("sibling" in p or "unstated" in p for p in result.best.penalties)


# ---------------------------------------------------------------- Test 3 ----
def test_3_condition_b_is_not_compared_against_nm(condition_prior_b):
    """A Japanese B grade must not be priced at the NM level."""
    dist = posterior(condition_prior_b)
    assert dist.probabilities[EuCondition.NM] < Decimal("0.35")

    values = {
        EuCondition.NM: eur("200"),
        EuCondition.EX: eur("160"),
        EuCondition.GD: eur("140"),
        EuCondition.LP: eur("120"),
        EuCondition.PL: eur("90"),
        EuCondition.PO: eur("55"),
    }
    weighted = expected_value(dist, values)
    assert weighted is not None
    assert weighted.amount < values[EuCondition.NM].amount
    assert weighted.amount < Decimal("160")


def test_3b_same_label_different_evidence_gives_different_distributions(condition_prior_a_minus):
    clean = posterior(
        condition_prior_a_minus,
        ConditionEvidence(has_photos=True, photo_count=4, origin="listing photos"),
    )
    worn = posterior(
        condition_prior_a_minus,
        ConditionEvidence(
            defect_weights={"edge_whitening": Decimal("0.3"), "corner_wear": Decimal("0.2")},
            has_photos=True, photo_count=4, origin="listing photos",
        ),
    )
    assert worn.probabilities[EuCondition.NM] < clean.probabilities[EuCondition.NM]


def test_3c_missing_condition_value_refuses_to_substitute_nm(condition_prior_a_minus):
    dist = posterior(condition_prior_a_minus)
    partial = {
        EuCondition.NM: eur("200"),
        EuCondition.EX: None,
        EuCondition.GD: eur("140"),
        EuCondition.LP: eur("120"),
        EuCondition.PL: eur("90"),
        EuCondition.PO: eur("55"),
    }
    assert expected_value(dist, partial) is None


# ---------------------------------------------------------------- Test 4 ----
def test_4_single_expensive_listing_cannot_become_fair_value():
    """Listings are not accepted by the fair value engine at all."""
    from pokearb_core.valuation import fair_value as fv_module
    import inspect

    signature = inspect.signature(fv_module.compute_fair_value)
    assert "listings" not in signature.parameters

    lonely_listing = Listing(
        "l1", "v1", "cardmarket", NOW, eur("500"), EuCondition.NM, Language.JA
    )
    # A listing is not a Sale and cannot be coerced into the pipeline.
    assert not isinstance(lonely_listing, Sale)

    result = compute_fair_value([], variant_id="v1", as_of=NOW, window_days=90)
    assert result.sufficient is False
    assert result.value is None


# ---------------------------------------------------------------- Test 5 ----
def test_5_recent_completed_sales_drive_fair_value():
    sales = [
        make_sale("s1", Decimal("175"), 5),
        make_sale("s2", Decimal("180"), 9),
        make_sale("s3", Decimal("185"), 14),
        make_sale("s4", Decimal("190"), 20),
    ]
    result = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    assert result.sufficient
    assert result.value is not None
    assert Decimal("175") <= result.value.amount <= Decimal("190")
    assert len(result.inputs) == 4


def test_5b_high_outlier_is_flagged_not_averaged_in():
    sales = [
        make_sale("s1", Decimal("170"), 3),
        make_sale("s2", Decimal("180"), 7),
        make_sale("s3", Decimal("185"), 12),
        make_sale("s4", Decimal("190"), 18),
        make_sale("s5", Decimal("195"), 25),
        make_sale("s6", Decimal("790"), 30),
    ]
    result = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    assert result.sufficient
    assert result.value.amount < Decimal("250")
    excluded_ids = {e.record_id for e in result.excluded}
    assert "s6" in excluded_ids
    assert any("outlier" in e.note for e in result.excluded if e.record_id == "s6")


def test_5c_suspiciously_low_sale_is_also_flagged():
    sales = [
        make_sale("s1", Decimal("180"), 3),
        make_sale("s2", Decimal("182"), 6),
        make_sale("s3", Decimal("185"), 10),
        make_sale("s4", Decimal("188"), 14),
        make_sale("s5", Decimal("190"), 18),
        make_sale("low", Decimal("18"), 20),
    ]
    result = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    assert "low" in {e.record_id for e in result.excluded}


def test_5d_below_minimum_evidence_returns_no_number():
    sales = [make_sale("s1", Decimal("180"), 3), make_sale("s2", Decimal("185"), 6)]
    result = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    assert result.sufficient is False
    assert result.value is None
    assert any("insufficient evidence" in n for n in result.notes)


def test_5e_cross_variant_pooling_raises():
    sales = [
        make_sale("s1", Decimal("180"), 3),
        make_sale("s2", Decimal("185"), 6, variant_id="v2"),
    ]
    with pytest.raises(ValueError, match="more than one variant"):
        compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)


# --- shared economics helper -------------------------------------------------
# The scorer no longer carries its own proceeds assumption. Tests supply the
# engine's own calculation with the buy side frozen, which is the same object
# the API and the worker pass, so a test cannot pass against a model the
# product does not use.

def econ_fn(policy, fx, *, purchase_jpy="6500", scenario=Scenario.HAND_CARRY,
            purpose=AcquisitionPurpose.PERSONAL, on=date(2026, 9, 20), **kw):
    return resale_economics_fn(
        purchase_jpy=Money(Decimal(purchase_jpy), Currency.JPY),
        on=on,
        assumptions=CostAssumptions(
            scenario=scenario, acquisition_purpose=purpose,
            zero_logistics_cost_is_verified=True, **kw,
        ),
        policy=policy, fx_rates=fx,
    )


# ---------------------------------------------------------------- Test 6 ----
def test_6_low_population_with_no_sales_is_not_a_strong_investment(fx_map, policy_store):
    """20 PSA 10s and almost no sales must not score as an opportunity."""
    sales = [make_sale("s1", Decimal("400"), 40)]
    liquidity = compute_liquidity(sales, [], as_of=NOW)
    assert liquidity.band is LiquidityBand.UNKNOWN
    assert liquidity.score is None

    fv = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=365)
    assessment = assess_opportunity(
        economics=econ_fn(policy_store, fx_map), gross_resale=eur("400"),
        fair_value=fv, liquidity=liquidity, condition_dist=None,
        match_confidence=Decimal("0.99"), as_of=NOW,
    )
    assert assessment.suppressed
    assert SuppressionReason.LIQUIDITY_UNMEASURABLE in assessment.suppression_reasons


# ---------------------------------------------------------------- Test 7 ----
def test_7_huge_spread_with_low_liquidity_is_demoted(liquid_sales, thin_sales, policy_store, fx_map):
    """A 150 percent theoretical spread on an illiquid card must not win."""
    liquid_profile = compute_liquidity(
        liquid_sales, [Listing(f"l{i}", "v1", "cm", NOW, eur("190"), EuCondition.NM, Language.JA) for i in range(5)],
        as_of=NOW,
    )
    thin_profile = compute_liquidity(thin_sales, [], as_of=NOW)

    assert liquid_profile.score is not None
    assert thin_profile.score is None or thin_profile.score < liquid_profile.score

    fv_thin = compute_fair_value(thin_sales, variant_id="v1", as_of=NOW, window_days=365)
    thin = assess_opportunity(
        economics=econ_fn(policy_store, fx_map), gross_resale=eur("250"),
        fair_value=fv_thin, liquidity=thin_profile, condition_dist=None,
        match_confidence=Decimal("0.99"), as_of=NOW,
    )

    fv_liquid = compute_fair_value(liquid_sales, variant_id="v1", as_of=NOW, window_days=90)
    liquid = assess_opportunity(
        economics=econ_fn(policy_store, fx_map), gross_resale=eur("170"),
        fair_value=fv_liquid, liquidity=liquid_profile, condition_dist=None,
        match_confidence=Decimal("0.99"), as_of=NOW, source_count=3,
    )

    assert thin.suppressed, "illiquid 150% spread must be suppressed, not merely ranked low"
    assert not liquid.suppressed
    assert liquid.ranking_roi is not None


def test_7b_ranking_uses_lower_quantile_not_point_estimate(liquid_sales, policy_store, fx_map):
    profile = compute_liquidity(
        liquid_sales,
        [Listing(f"l{i}", "v1", "cm", NOW, eur("190"), EuCondition.NM, Language.JA) for i in range(4)],
        as_of=NOW,
    )
    fv = compute_fair_value(liquid_sales, variant_id="v1", as_of=NOW, window_days=90)
    result = assess_opportunity(
        economics=econ_fn(policy_store, fx_map), gross_resale=eur("180"),
        fair_value=fv, liquidity=profile, condition_dist=None,
        match_confidence=Decimal("0.99"), as_of=NOW, source_count=3,
    )
    assert not result.suppressed
    assert result.ranking_roi < result.point_roi


# ---------------------------------------------------------------- Test 8 ----
def test_8_yen_price_produces_net_roi_after_costs(policy_store, fx_map):
    assumptions = CostAssumptions(scenario=Scenario.HAND_CARRY)
    result = compute_arbitrage(
        purchase_jpy=Money(Decimal("6500"), Currency.JPY),
        gross_resale_eur=eur("120"),
        on=date(2026, 9, 20),
        assumptions=assumptions,
        policy=policy_store,
        fx_rates=fx_map,
    )
    assert result.landed.total_eur.amount > 0
    assert result.net_proceeds_eur.amount < result.gross_resale_eur.amount
    expected = result.net_proceeds_eur.amount - result.landed.total_eur.amount
    assert abs(result.expected_profit_eur.amount - expected) <= Decimal("0.02")
    assert result.roi != 0
    assert result.expected_profit_dkk is not None


# ---------------------------------------------------------------- Test 9 ----
def test_9_raising_required_roi_lowers_max_buy_price(policy_store, fx_map):
    kw = dict(
        gross_resale_eur=eur("220"),
        on=date(2026, 9, 20),
        assumptions=CostAssumptions(scenario=Scenario.HAND_CARRY),
        policy=policy_store,
        fx_rates=fx_map,
    )
    at_40 = max_buy_price(required_roi=Decimal("0.40"), **kw)
    at_80 = max_buy_price(required_roi=Decimal("0.80"), **kw)
    assert at_40 is not None and at_80 is not None
    assert at_80.amount < at_40.amount
    assert at_40.currency is Currency.JPY


def test_9b_max_buy_price_actually_clears_the_hurdle(policy_store, fx_map):
    """Buying at exactly the maximum must deliver at least the required ROI."""
    assumptions = CostAssumptions(scenario=Scenario.HAND_CARRY)
    on = date(2026, 9, 20)
    required = Decimal("0.60")
    cap = max_buy_price(
        gross_resale_eur=eur("220"), required_roi=required, on=on,
        assumptions=assumptions, policy=policy_store, fx_rates=fx_map,
    )
    assert cap is not None
    result = compute_arbitrage(
        purchase_jpy=cap, gross_resale_eur=eur("220"), on=on,
        assumptions=assumptions, policy=policy_store, fx_rates=fx_map,
        required_roi=required,
    )
    assert result.roi >= required - Decimal("0.02")


# --------------------------------------------------------------- Test 11 ----
def test_11_failed_source_does_not_invent_data():
    """PSA population is unavailable; the adapter raises rather than guessing."""
    adapter = PsaCertAdapter()
    with pytest.raises(PolicyViolationError, match="not exposed"):
        adapter.fetch_population("any-reference")
    assert adapter.capabilities()["population_total"] is False


def test_11b_cardmarket_dedicated_app_refuses_continuous_polling():
    adapter = CardmarketAdapter(app_type="dedicated")
    with pytest.raises((PolicyViolationError, SourceDisabledError)):
        adapter.fetch_listings("pikachu")


def test_11c_unverified_source_cannot_run():
    from pokearb_core.adapters.gated import JAPANESE_SOURCE_TEMPLATE

    with pytest.raises(SourceDisabledError):
        JAPANESE_SOURCE_TEMPLATE.assert_runnable()


# --------------------------------------------------------------- Test 12 ----
def test_12_every_number_traces_to_source_data():
    sales = [
        make_sale("s1", Decimal("175"), 5),
        make_sale("s2", Decimal("180"), 9),
        make_sale("s3", Decimal("185"), 14),
    ]
    result = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    assert result.sufficient
    assert {ref.record_id for ref in result.inputs} == {"s1", "s2", "s3"}
    assert all(ref.weight is not None for ref in result.inputs)
    assert all(ref.source_url for ref in result.inputs)
    assert "weighted-median" in result.method


def test_12b_landed_cost_exposes_policy_sources(policy_store, fx_map):
    from pokearb_core.arbitrage.engine import compute_landed_cost

    landed = compute_landed_cost(
        Money(Decimal("6500"), Currency.JPY),
        on=date(2026, 9, 20),
        assumptions=CostAssumptions(
            scenario=Scenario.SHIPPED,
            international_shipping_jpy=Decimal("2500"),
        ),
        policy=policy_store,
        fx_rates=fx_map,
    )
    assert landed.policy_sources["dk.import_vat_rate"] is not None
    assert "skat.dk" in landed.policy_sources["dk.import_vat_rate"]
    assert landed.breakdown["import_vat_eur"].amount > 0


# --------------------------------------------------------------- Test 14 ----
def test_14_similar_product_names_different_codes_do_not_merge(universe, aliases):
    """Pokemon Center ETB versus standard ETB, via the stamp discriminator."""
    observed = ObservedCard(
        raw_title="Elite Trainer Box", language=Language.EN, set_code="ETB-X",
        number="1", printing=Printing.NON_HOLO, stamp="none", name="Elite Trainer Box",
    )
    result = match_card(observed, universe, aliases)
    for candidate in result.rejected:
        if candidate.variant.variant_id == "v-etb-pokemon-center":
            assert any("stamp" in f for f in candidate.gate_failures)
            break
    else:  # pragma: no cover
        pytest.fail("Pokemon Center product was not separated from the standard one")


# --------------------------------------------------------------- Test 15 ----
def test_15_same_card_across_marketplaces_keeps_independent_listings(universe, aliases):
    observed = ObservedCard(
        raw_title="ピカチュウ 114/SM-P holo", language=Language.JA,
        set_code="SM-P", number="114", printing=Printing.HOLO,
        stamp="none", name="ピカチュウ",
    )
    a = match_card(observed, universe, aliases)
    b = match_card(observed, universe, aliases)
    assert a.variant_id == b.variant_id

    listings = [
        Listing("cm-1", a.variant_id, "cardmarket", NOW, eur("180"), EuCondition.NM, Language.JA),
        Listing("eb-1", a.variant_id, "ebay_browse", NOW, eur("176"), EuCondition.NM, Language.JA),
    ]
    profile = compute_liquidity([], listings, as_of=NOW)
    assert profile.active_listings == 2, "independent listings must not be deduplicated away"


# ------------------------------------------------------- supporting rules ----
def test_pricecharting_columns_are_translated_not_assumed():
    payload = {"loose-price": 1234, "new-price": 5678, "manual-only-price": 9999}
    interpreted = PriceChartingAdapter.interpret(payload)
    assert interpreted["ungraded"] == 1234
    assert interpreted["grade_8_or_8.5"] == 5678
    assert interpreted["psa_10"] == 9999
    assert PriceChartingAdapter().capabilities()["sales_history"] is False


def test_money_rejects_floats():
    with pytest.raises(TypeError):
        Money(1.5, Currency.EUR)  # type: ignore[arg-type]


def test_money_refuses_cross_currency_arithmetic():
    with pytest.raises(ValueError, match="without an explicit FX conversion"):
        eur("10") + Money(Decimal("10"), Currency.JPY)


def test_provisional_condition_prior_caps_confidence(condition_prior_a_minus):
    dist = posterior(condition_prior_a_minus)
    assert dist.is_provisional
    assert condition_confidence(dist) <= Decimal("0.6")
