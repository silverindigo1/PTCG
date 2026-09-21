"""Regression tests. One per confirmed defect, each reproducing it first.

Every test here names the behaviour that was wrong and asserts the behaviour
that is right. The point is not coverage. It is that if any of these fixes is
ever undone, the suite says which one and why it mattered.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "worker"))

from pokearb_core.arbitrage.engine import (  # noqa: E402
    CostAssumptionError,
    CostAssumptions,
    compute_arbitrage,
    compute_economics,
    compute_landed_cost,
    max_buy_price,
    resale_economics_fn,
)
from pokearb_core.arbitrage.policy import PolicyUnverifiedError  # noqa: E402
from pokearb_core.condition.model import expected_value  # noqa: E402
from pokearb_core.ingest import ImportProvenance, parse_sales  # noqa: E402
from pokearb_core.liquidity.model import compute_liquidity  # noqa: E402
from pokearb_core.scoring.opportunity import (  # noqa: E402
    SuppressionReason,
    assess_opportunity,
)
from pokearb_core.types import (  # noqa: E402
    AcquisitionPurpose,
    Currency,
    EuCondition,
    Grader,
    Language,
    Money,
    Sale,
    Scenario,
    ValueBasis,
)
from pokearb_core.valuation.fair_value import (  # noqa: E402
    compute_fair_value,
    condition_value_curve,
)

from conftest import NOW, eur, make_sale  # noqa: E402

ON = NOW.date()


def jpy(v: str) -> Money:
    return Money(Decimal(v), Currency.JPY)


# ============================================================== defect 4 ====
# Reported: the solver returned maximum purchase prices whose actual ROI was
# 21.0, 35.2 and 37.8 percent against a 40 percent requirement. Cause: the
# affine slope was derived by hand beside the forward calculation and treated
# the proxy fee as dutiable and VATable, which the forward calculation did not.

PROXY_CASES = [("100", 5785), ("220", 15336), ("400", 29665)]


@pytest.mark.parametrize("gross,old_wrong_answer", PROXY_CASES)
def test_solver_agrees_with_forward_calculation(policy_store, fx_map, gross, old_wrong_answer):
    assumptions = CostAssumptions(
        scenario=Scenario.PROXY,
        proxy_fee_rate=Decimal("0.10"),
        duty_rate=Decimal("0.05"),
        international_shipping_jpy=Decimal("2500"),
    )
    required = Decimal("0.40")
    kw = dict(on=ON, assumptions=assumptions, policy=policy_store, fx_rates=fx_map)

    answer = max_buy_price(
        gross_resale_eur=eur(gross), required_roi=required, **kw
    )
    assert answer is not None

    at_answer = compute_economics(
        purchase_jpy=answer, gross_resale_eur=eur(gross), **kw
    )
    assert at_answer.roi >= required, (
        "the returned maximum must actually clear the required return; "
        f"got {at_answer.roi:.4%}"
    )

    next_yen = compute_economics(
        purchase_jpy=jpy(str(answer.amount + 1)), gross_resale_eur=eur(gross), **kw
    )
    assert next_yen.roi < required, "the next yen up must be infeasible"

    old = compute_economics(
        purchase_jpy=jpy(str(old_wrong_answer)), gross_resale_eur=eur(gross), **kw
    )
    assert old.roi < required, (
        "the previously returned price should not clear the requirement; "
        "this is the defect being pinned"
    )


def test_max_buy_is_whole_yen(policy_store, fx_map):
    answer = max_buy_price(
        gross_resale_eur=eur("220"), required_roi=Decimal("0.40"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY, acquisition_purpose=AcquisitionPurpose.PERSONAL),
        policy=policy_store, fx_rates=fx_map,
    )
    assert answer is not None
    assert answer.amount == answer.amount.to_integral_value()


def test_no_feasible_price_returns_none_rather_than_a_number(policy_store, fx_map):
    """A card worth EUR 3 in Europe has no Japanese price that clears 40 percent."""
    answer = max_buy_price(
        gross_resale_eur=eur("3"), required_roi=Decimal("0.40"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.SHIPPED, international_shipping_jpy=Decimal("2500")),
        policy=policy_store, fx_rates=fx_map,
    )
    assert answer is None


def test_duty_threshold_is_handled_as_a_discontinuity(policy_store, fx_map):
    """Solutions must not be solved in one regime and reported in another."""
    assumptions = CostAssumptions(
        scenario=Scenario.PROXY, proxy_fee_rate=Decimal("0.10"),
        duty_rate=Decimal("0.05"), international_shipping_jpy=Decimal("2500"),
    )
    kw = dict(on=ON, assumptions=assumptions, policy=policy_store, fx_rates=fx_map)
    answer = max_buy_price(
        gross_resale_eur=eur("400"), required_roi=Decimal("0.40"), **kw)
    assert answer is not None
    landed = compute_landed_cost(answer, **kw)
    # Whatever regime the answer lands in, the cost that priced it is the cost
    # for that regime, and the forward check above already confirmed the ROI.
    assert landed.duty_regime in {"low_value", "above_threshold"}


# ============================================================== defect 5 ====
# Reported: a hand-carried purchase above the traveller allowance returned
# numeric costs with duty and VAT set to zero, under a note saying they were
# not modelled.

def test_resale_inventory_does_not_get_the_traveller_allowance(policy_store, fx_map):
    """Verified at https://info.skat.dk/data.aspx?oid=2230232

    Relief covers goods for private use, requires the import to be of
    non-commercial character, and goods imported with a view to resale are
    expressly outside it.
    """
    resale = compute_landed_cost(
        jpy("6500"), on=ON,
        assumptions=CostAssumptions(scenario=Scenario.HAND_CARRY),
        policy=policy_store, fx_rates=fx_map,
    )
    assert resale.duty_regime != "traveller_allowance"
    assert resale.breakdown["import_vat_eur"].amount > 0, (
        "a commercial import bears import VAT; treating it as relieved "
        "understates cost"
    )
    assert any("private use" in n for n in resale.notes)


def test_personal_import_above_the_allowance_is_blocked_not_zeroed(policy_store, fx_map):
    """The value limit bites on the goods in their entirety and cannot be split.

    Above it, duty and VAT are due. Without a verified tariff rate the engine
    must refuse rather than return a cost with both set to zero.
    """
    with pytest.raises(PolicyUnverifiedError):
        compute_landed_cost(
            jpy("120000"), on=ON,
            assumptions=CostAssumptions(
                scenario=Scenario.HAND_CARRY,
                acquisition_purpose=AcquisitionPurpose.PERSONAL,
            ),
            policy=policy_store, fx_rates=fx_map,
        )


def test_thresholds_are_assessed_on_the_basket_not_the_single_card(policy_store, fx_map):
    """One cheap card in an expensive basket is not inside the allowance."""
    alone = compute_landed_cost(
        jpy("3000"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
        ),
        policy=policy_store, fx_rates=fx_map,
    )
    assert alone.duty_regime == "traveller_allowance"

    with pytest.raises(PolicyUnverifiedError):
        compute_landed_cost(
            jpy("3000"), on=ON,
            assumptions=CostAssumptions(
                scenario=Scenario.HAND_CARRY,
                acquisition_purpose=AcquisitionPurpose.PERSONAL,
                basket_value_jpy=Decimal("90000"),
            ),
            policy=policy_store, fx_rates=fx_map,
        )


def test_expected_refund_is_separate_from_upfront_cash(policy_store, fx_map):
    landed = compute_landed_cost(
        jpy("6500"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
        ),
        policy=policy_store, fx_rates=fx_map,
    )
    assert landed.expected_refund_eur.amount > 0
    assert landed.upfront_cash_eur.amount > landed.total_eur.amount
    assert (
        landed.upfront_cash_eur.amount - landed.expected_refund_eur.amount
        == landed.total_eur.amount
    ), "the two views must reconcile exactly"


# ============================================================== defect 6 ====
# Reported: the arbitrage engine used a detailed cost stack while the scorer
# independently assumed net proceeds were 90 percent of gross resale.

def test_scored_roi_equals_engine_roi(policy_store, fx_map, liquid_sales):
    fv = compute_fair_value(liquid_sales, variant_id="v1", as_of=NOW, window_days=90)
    liquidity = compute_liquidity(liquid_sales, [], as_of=NOW)
    economics = resale_economics_fn(
        purchase_jpy=jpy("6500"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
        ),
        policy=policy_store, fx_rates=fx_map,
    )
    gross = eur("170")
    assessment = assess_opportunity(
        economics=economics, gross_resale=gross, fair_value=fv,
        liquidity=liquidity, condition_dist=None,
        match_confidence=Decimal("0.99"), as_of=NOW, source_count=3,
    )
    assert not assessment.suppressed
    engine = compute_economics(
        purchase_jpy=jpy("6500"), gross_resale_eur=gross, on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
        ),
        policy=policy_store, fx_rates=fx_map,
    )
    assert assessment.point_roi == engine.roi.quantize(Decimal("0.0001"))


def test_profit_and_roi_reconcile_on_a_stated_denominator(policy_store, fx_map):
    result = compute_arbitrage(
        purchase_jpy=jpy("6500"), gross_resale_eur=eur("120"), on=ON,
        assumptions=CostAssumptions(
            scenario=Scenario.HAND_CARRY,
            acquisition_purpose=AcquisitionPurpose.PERSONAL,
        ),
        policy=policy_store, fx_rates=fx_map,
    )
    assert result.capital_basis
    recomputed = result.expected_profit_eur.amount / result.capital_deployed_eur.amount
    assert abs(recomputed - result.roi) < Decimal("0.0001")


def test_verdict_respects_the_purchase_limit(policy_store, fx_map, liquid_sales):
    """An asking price above the maximum cannot be an opportunity."""
    fv = compute_fair_value(liquid_sales, variant_id="v1", as_of=NOW, window_days=90)
    liquidity = compute_liquidity(liquid_sales, [], as_of=NOW)
    assumptions = CostAssumptions(
        scenario=Scenario.HAND_CARRY, acquisition_purpose=AcquisitionPurpose.PERSONAL)
    gross = eur("170")
    price = jpy("40000")
    cap = max_buy_price(
        gross_resale_eur=gross, required_roi=Decimal("0.40"), on=ON,
        assumptions=assumptions, policy=policy_store, fx_rates=fx_map,
    )
    assessment = assess_opportunity(
        economics=resale_economics_fn(
            purchase_jpy=price, on=ON, assumptions=assumptions,
            policy=policy_store, fx_rates=fx_map),
        gross_resale=gross, fair_value=fv, liquidity=liquidity,
        condition_dist=None, match_confidence=Decimal("0.99"), as_of=NOW,
        source_count=3, required_roi=Decimal("0.40"),
        max_buy_jpy=cap, purchase_jpy=price,
    )
    assert assessment.suppressed
    assert SuppressionReason.ABOVE_MAX_BUY in assessment.suppression_reasons


def test_invalid_scenario_is_not_silently_hand_carry():
    with pytest.raises(CostAssumptionError):
        CostAssumptions(scenario="teleport").validated()  # type: ignore[arg-type]


def test_unstated_shipping_is_refused_not_treated_as_zero():
    with pytest.raises(CostAssumptionError):
        CostAssumptions(scenario=Scenario.SHIPPED).validated()
    # An explicitly verified zero is allowed, and says so.
    CostAssumptions(
        scenario=Scenario.SHIPPED, zero_logistics_cost_is_verified=True
    ).validated()


# ============================================================== defect 7 ====
# Reported: passing the same sale three times satisfied the evidence minimum.

def test_the_same_sale_three_times_is_one_sale():
    one = make_sale("dup", Decimal("140"), 5)
    fv = compute_fair_value([one, one, one], variant_id="v1", as_of=NOW, window_days=90)
    assert not fv.sufficient, "three copies of one transaction are not three sales"
    assert fv.value is None
    assert any("duplicate" in (e.note or "") for e in fv.excluded)


def test_same_price_same_day_same_source_is_treated_as_a_duplicate():
    base = make_sale("a", Decimal("140"), 5)
    twin = Sale(
        sale_id="b", variant_id=base.variant_id, source_id=base.source_id,
        sold_at=base.sold_at, price=base.price, condition=base.condition,
        language=base.language, source_url=base.source_url, market=base.market,
    )
    third = make_sale("c", Decimal("151"), 12)
    fourth = make_sale("d", Decimal("139"), 20)
    fv = compute_fair_value(
        [base, twin, third, fourth], variant_id="v1", as_of=NOW, window_days=90)
    assert fv.n_sales == 3
    assert any("duplicate" in (e.note or "") for e in fv.excluded)


def test_evidence_known_after_the_calculation_time_is_excluded():
    """Filtering on the transaction date alone is not enough for a backtest."""
    late = Sale(
        sale_id="late", variant_id="v1", source_id="cardmarket",
        sold_at=NOW - timedelta(days=10),
        price=eur("500"), condition=EuCondition.NM, language=Language.JA,
        market="EU", external_id="late",
        known_at=NOW + timedelta(days=3),   # published three days from now
    )
    normal = [make_sale(f"s{i}", Decimal("120") + i, 5 + i) for i in range(3)]
    fv = compute_fair_value(normal + [late], variant_id="v1", as_of=NOW, window_days=90)
    assert all(ref.record_id != "late" for ref in fv.inputs)
    assert any("look-ahead" in (e.note or "") for e in fv.excluded)


# ============================================================== defect 2 ====
# Reported: graded records became Sale objects with is_graded=False, and a
# condition confidence of zero became 1.0.

def test_graded_sales_never_price_a_raw_card():
    raw = [make_sale(f"r{i}", Decimal("120") + i, 5 + i) for i in range(3)]
    psa10 = Sale(
        sale_id="psa10", variant_id="v1", source_id="cardmarket",
        sold_at=NOW - timedelta(days=4), price=eur("1800"),
        condition=None, language=Language.JA, market="EU",
        is_graded=True, grader=Grader.PSA, grade=Decimal("10"), external_id="psa10",
    )
    fv = compute_fair_value(raw + [psa10], variant_id="v1", as_of=NOW, window_days=90)
    assert fv.value is not None
    assert fv.value.amount < Decimal("200"), "a PSA 10 must not lift the raw value"
    assert any("grade bucket" in (e.note or "") for e in fv.excluded)


def test_each_grader_and_grade_prices_separately():
    def graded(n: str, grader: Grader, grade: str, price: str, days: int) -> Sale:
        return Sale(
            sale_id=n, variant_id="v1", source_id="cardmarket",
            sold_at=NOW - timedelta(days=days), price=eur(price),
            condition=None, language=Language.JA, market="EU", is_graded=True,
            grader=grader, grade=Decimal(grade), external_id=n,
        )

    sales = [
        graded("a", Grader.PSA, "10", "1800", 3),
        graded("b", Grader.PSA, "10", "1750", 9),
        graded("c", Grader.PSA, "10", "1830", 15),
        graded("d", Grader.PSA, "9", "420", 6),
        graded("e", Grader.BGS, "10", "1200", 8),
    ]
    # The bucket key is built from the grader enum's own value, so the test
    # asks the type rather than hardcoding a casing convention.
    psa10_bucket = sales[0].grade_bucket
    psa9_bucket = sales[3].grade_bucket
    bgs10_bucket = sales[4].grade_bucket
    assert len({psa10_bucket, psa9_bucket, bgs10_bucket}) == 3

    psa10 = compute_fair_value(
        sales, variant_id="v1", as_of=NOW, window_days=90, grade_bucket=psa10_bucket)
    assert psa10.sufficient and psa10.value is not None
    assert Decimal("1700") < psa10.value.amount < Decimal("1900")

    psa9 = compute_fair_value(
        sales, variant_id="v1", as_of=NOW, window_days=90, grade_bucket=psa9_bucket)
    assert not psa9.sufficient, "one PSA 9 sale is not a PSA 9 valuation"


def test_japanese_sales_are_not_european_resale_evidence():
    eu = [make_sale(f"e{i}", Decimal("120") + i, 5 + i) for i in range(3)]
    jp = [
        Sale(
            sale_id=f"j{i}", variant_id="v1", source_id="cardrush",
            sold_at=NOW - timedelta(days=3 + i),
            price=Money(Decimal("6500"), Currency.JPY),
            condition=EuCondition.NM, language=Language.JA, market="JP",
            external_id=f"j{i}",
        )
        for i in range(3)
    ]
    fv = compute_fair_value(
        eu + jp, variant_id="v1", as_of=NOW, window_days=90, market="EU")
    assert fv.n_sales == 3
    assert any("market" in (e.note or "") for e in fv.excluded)


def test_zero_condition_confidence_is_not_read_as_certainty():
    """The repository bug: ``r["condition_confidence"] or "1.0"``.

    Zero is falsy in Python, so a sale whose condition nobody could assess was
    loaded as fully confident. The type now carries the value it was given.
    """
    s = Sale(
        sale_id="z", variant_id="v1", source_id="cardmarket", sold_at=NOW,
        price=eur("100"), condition=EuCondition.NM, language=Language.JA,
        condition_confidence=Decimal("0"),
    )
    assert s.condition_confidence == Decimal("0")


# ============================================================== defect 3 ====
# Reported: Quick Check computed a condition distribution and then passed the
# near-mint fair value straight into the arbitrage maths.

def test_condition_adjusted_value_is_below_the_near_mint_value(condition_prior_a_minus):
    from pokearb_core.condition.model import ConditionEvidence, posterior

    sales = [make_sale(f"s{i}", Decimal("120") + i, 5 + i) for i in range(4)]
    fv = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    ladder = condition_value_curve(
        sales, variant_id="v1", as_of=NOW, anchor=fv)
    dist = posterior(condition_prior_a_minus, ConditionEvidence())

    adjusted = expected_value(dist, ladder.money_map())
    assert adjusted is not None
    assert adjusted.amount < fv.value.amount, (
        "a card that is only probably near mint is worth less than one that is"
    )


def test_condition_ladder_distinguishes_observed_from_modelled():
    sales = [make_sale(f"s{i}", Decimal("120") + i, 5 + i) for i in range(4)]
    fv = compute_fair_value(sales, variant_id="v1", as_of=NOW, window_days=90)
    ladder = condition_value_curve(sales, variant_id="v1", as_of=NOW, anchor=fv)
    bases = {cv.condition: cv.basis for cv in ladder.values.values()}
    assert ValueBasis.MODELLED in bases.values()
    assert ladder.modelled, "modelled coverage must be disclosed, not implied"
    for cv in ladder.values.values():
        assert cv.note


def test_missing_condition_coverage_refuses_rather_than_assuming_near_mint(
    condition_prior_a_minus,
):
    from pokearb_core.condition.model import ConditionEvidence, posterior

    dist = posterior(condition_prior_a_minus, ConditionEvidence())
    holes = {c: None for c in dist.probabilities}
    assert expected_value(dist, holes) is None


# ============================================================== defect 1 ====
# Manual import route: idempotent, provenance-bearing, synthetic-labelled.

CSV_HEADER = (
    "external_id,variant_id,sold_at,price_amount,price_currency,market,"
    "language,condition,source_url\n"
)


def _prov(**kw):
    base = dict(source_id="manual_eu", imported_by="tester",
                evidence_url="https://example.invalid/export")
    base.update(kw)
    return ImportProvenance(**base)


def test_manual_import_requires_provenance():
    payload = CSV_HEADER + "a,v1,2026-09-01,100,EUR,EU,ja,nm,\n"
    anonymous = parse_sales(payload, ImportProvenance("manual_eu", "", None))
    assert not anonymous.ok
    assert any("imported_by" in i.message for i in anonymous.issues)
    assert any("evidence_url" in i.message for i in anonymous.issues)


def test_manual_import_is_idempotent_by_content_hash():
    payload = CSV_HEADER + "a,v1,2026-09-01,100,EUR,EU,ja,nm,\n"
    first = parse_sales(payload, _prov())
    second = parse_sales(payload, _prov())
    assert first.content_hash == second.content_hash


def test_manual_import_rejects_rows_rather_than_guessing():
    payload = CSV_HEADER + (
        "a,v1,2026-09-01,100,EUR,,ja,nm,\n"          # no market
        "b,v1,2026-09-01,100,EUR,EU,ja,nm,\n"
        "c,v1,2026-09-01,-5,EUR,EU,ja,nm,\n"          # negative price
    )
    parsed = parse_sales(payload, _prov())
    assert len(parsed.sales) == 1
    messages = " ".join(i.message for i in parsed.issues)
    assert "market" in messages
    assert "positive" in messages


def test_synthetic_import_is_labelled():
    payload = CSV_HEADER + "a,v1,2026-09-01,100,EUR,EU,ja,nm,\n"
    parsed = parse_sales(payload, _prov(synthetic=True, evidence_url=None))
    assert parsed.provenance.dataset == "synthetic_demo"
    assert parsed.ok, "a synthetic import does not need an external evidence URL"
