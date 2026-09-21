"""Explainable scoring and risk-adjusted opportunity evaluation.

Two ideas carry most of the weight here.

**Every score explains itself.** A score is a number plus the list of reasons
that moved it, each with its direction and magnitude. No component of this
system returns an unexplained 82.

**Ranking uses a lower quantile of the profit distribution, not the point
estimate.** The point estimate of ROI is the number that looks best on a card
with three sales, a provisional condition prior and no population data. Ranking
on the 25th percentile of simulated ROI systematically demotes exactly those,
which is the asymmetry the brief asks for.

Before scoring happens at all, hard gates run. An opportunity failing a gate is
**suppressed**, not ranked low. It does not appear in a list where a tired
person on a Tokyo shop floor might tap it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Callable, Mapping, Optional, Protocol, Sequence

from ..condition.model import condition_confidence
from ..liquidity.model import LiquidityBand, LiquidityProfile
from ..types import ConditionDistribution, DataQuality, FairValue, Money


class _EconomicsResult(Protocol):
    """Structural view of the arbitrage engine's result.

    Declared structurally so this module keeps no import edge to the arbitrage
    engine, which would make the dependency cyclic and, worse, tempt a future
    reader into recomputing the cost stack here.
    """

    roi: Decimal

    @property
    def capital_deployed_eur(self) -> Money: ...

    @property
    def landed(self): ...


#: Buy side frozen, resale value varying. Supplied by the caller.
EconomicsFn = Callable[[Money], _EconomicsResult]

__all__ = [
    "ScoreReason",
    "Score",
    "SuppressionReason",
    "OpportunityAssessment",
    "RiskConfig",
    "assess_opportunity",
    "compute_data_quality",
]


@dataclass(frozen=True, slots=True)
class ScoreReason:
    text: str
    direction: str  # "up" | "down" | "neutral"
    magnitude: Decimal


@dataclass(frozen=True, slots=True)
class Score:
    name: str
    value: Optional[Decimal]
    reasons: tuple[ScoreReason, ...]

    def explain(self) -> str:
        if self.value is None:
            return f"{self.name}: not measurable"
        lines = [f"{self.name}: {self.value}"]
        lines += [f"  {r.direction:>7} {r.magnitude:>5}  {r.text}" for r in self.reasons]
        return "\n".join(lines)


class SuppressionReason(str, Enum):
    MATCH_CONFIDENCE = "match_confidence_below_floor"
    FAIR_VALUE_INSUFFICIENT = "fair_value_insufficient_evidence"
    DATA_QUALITY = "data_quality_below_floor"
    LIQUIDITY = "liquidity_below_floor"
    LIQUIDITY_UNMEASURABLE = "liquidity_not_measurable"
    STALE_DATA = "data_too_stale"
    CONDITION_CONFIDENCE = "condition_confidence_below_floor"
    LANDED_COST_UNRESOLVED = "landed_cost_unresolved"
    ABOVE_MAX_BUY = "asking_price_above_maximum"
    BELOW_REQUIRED_RETURN = "below_required_return"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Suppression floors and simulation settings.

    Defaults are deliberately strict. Loosening them is a decision the operator
    makes knowingly, which is the point.
    """

    min_match_confidence: Decimal = Decimal("0.98")
    min_data_quality: Decimal = Decimal("45")
    min_liquidity_score: Decimal = Decimal("25")
    #: Deliberately low. A Japanese shop grade genuinely spans several
    #: European grades, and that spread is inherent in the measurement, not a
    #: data defect. It is already paid for twice: in the probability-weighted
    #: expected value, and in the simulation haircut. Gating on it at a normal
    #: level would suppress every shop-graded card, which defeats the product.
    #: This floor exists only to catch the pathological near-uniform case,
    #: where the distribution says nothing at all.
    min_condition_confidence: Decimal = Decimal("0.08")
    max_data_age_days: int = 120
    allow_unmeasurable_liquidity: bool = False
    #: Quantile of simulated ROI used for ranking.
    ranking_quantile: float = 0.25
    simulations: int = 4000
    #: Relative standard deviation applied to fair value, scaled by evidence.
    fair_value_rel_sigma: float = 0.18
    random_seed: int = 20260920


def compute_data_quality(
    fair_value: FairValue,
    *,
    as_of: datetime,
    source_count: int,
    match_confidence: Decimal,
    condition_dist: Optional[ConditionDistribution] = None,
) -> DataQuality:
    """Explainable 0 to 100 data-quality assessment."""
    reasons: list[str] = []
    score = Decimal("0")

    n = fair_value.n_sales
    if n >= 12:
        score += Decimal("35")
        reasons.append(f"{n} completed sales give a solid base (+35)")
    elif n >= 6:
        score += Decimal("26")
        reasons.append(f"{n} completed sales are workable (+26)")
    elif n >= 3:
        score += Decimal("15")
        reasons.append(f"only {n} completed sales, thin evidence (+15)")
    else:
        reasons.append(f"{n} completed sales is below the minimum evidence rule (+0)")

    ages = [
        int((_aware(as_of) - _aware(ref.observed_at)).total_seconds() // 86400)
        for ref in fair_value.inputs
    ]
    oldest = max(ages) if ages else None
    newest = min(ages) if ages else None

    if newest is None:
        reasons.append("no dated inputs (+0)")
    elif newest <= 14:
        score += Decimal("25")
        reasons.append(f"most recent sale is {newest} days old (+25)")
    elif newest <= 45:
        score += Decimal("16")
        reasons.append(f"most recent sale is {newest} days old (+16)")
    elif newest <= 120:
        score += Decimal("7")
        reasons.append(f"most recent sale is {newest} days old, going stale (+7)")
    else:
        reasons.append(f"most recent sale is {newest} days old, stale (+0)")

    if source_count >= 3:
        score += Decimal("15")
        reasons.append(f"{source_count} independent sources agree the market exists (+15)")
    elif source_count == 2:
        score += Decimal("9")
        reasons.append("two independent sources (+9)")
    else:
        score += Decimal("3")
        reasons.append("single source, no cross-check (+3)")

    if fair_value.dispersion is None:
        reasons.append("dispersion not measurable (+0)")
    elif fair_value.dispersion < Decimal("0.25"):
        score += Decimal("15")
        reasons.append(f"tight price agreement, IQR/median {fair_value.dispersion:.2f} (+15)")
    elif fair_value.dispersion < Decimal("0.5"):
        score += Decimal("8")
        reasons.append(f"moderate dispersion, IQR/median {fair_value.dispersion:.2f} (+8)")
    else:
        reasons.append(f"wide dispersion, IQR/median {fair_value.dispersion:.2f} (+0)")

    score += match_confidence * Decimal("10")
    reasons.append(f"match confidence {match_confidence} (+{match_confidence * Decimal('10'):.1f})")

    if condition_dist is not None and condition_dist.is_provisional:
        score -= Decimal("8")
        reasons.append("condition prior is provisional, not calibrated (-8)")

    if fair_value.excluded:
        reasons.append(
            f"{len(fair_value.excluded)} input(s) excluded or flagged; see provenance"
        )

    return DataQuality(
        score=max(Decimal("0"), min(Decimal("100"), score)).quantize(Decimal("0.1")),
        reasons=tuple(reasons),
        n_sales=n,
        oldest_input_age_days=oldest,
        newest_input_age_days=newest,
        source_count=source_count,
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class OpportunityAssessment:
    suppressed: bool
    suppression_reasons: tuple[SuppressionReason, ...]
    suppression_detail: tuple[str, ...]
    point_roi: Optional[Decimal]
    roi_p10: Optional[Decimal]
    roi_p25: Optional[Decimal]
    roi_median: Optional[Decimal]
    ranking_roi: Optional[Decimal]
    annualised_roi: Optional[Decimal]
    expected_days_to_sell: Optional[Decimal]
    data_quality: DataQuality
    scores: tuple[Score, ...]

    def explain(self) -> str:
        if self.suppressed:
            head = "SUPPRESSED: " + ", ".join(r.value for r in self.suppression_reasons)
            return "\n".join([head, *(f"  {d}" for d in self.suppression_detail)])
        return "\n".join(s.explain() for s in self.scores)


def assess_opportunity(
    *,
    economics: "EconomicsFn",
    gross_resale: Money,
    fair_value: FairValue,
    liquidity: LiquidityProfile,
    condition_dist: Optional[ConditionDistribution],
    match_confidence: Decimal,
    as_of: datetime,
    source_count: int = 1,
    config: Optional[RiskConfig] = None,
    required_roi: Optional[Decimal] = None,
    max_buy_jpy: Optional[Money] = None,
    purchase_jpy: Optional[Money] = None,
) -> OpportunityAssessment:
    """Assess one opportunity, suppressing it if any hard gate fails.

    ``economics`` is the arbitrage engine's own calculation with the buy side
    frozen, so profit, ROI and every simulated draw run through the same cost
    model that produced the purchase limits. The previous version applied its
    own flat assumption that net proceeds were 90 percent of gross resale,
    which meant the scored ROI and the engine's ROI were different numbers
    wearing the same name.
    """
    cfg = config or RiskConfig()
    dq = compute_data_quality(
        fair_value, as_of=as_of, source_count=source_count,
        match_confidence=match_confidence, condition_dist=condition_dist,
    )

    gates: list[SuppressionReason] = []
    detail: list[str] = []

    if match_confidence < cfg.min_match_confidence:
        gates.append(SuppressionReason.MATCH_CONFIDENCE)
        detail.append(
            f"match confidence {match_confidence} is below the {cfg.min_match_confidence} "
            "floor; the card has not been identified well enough to price"
        )

    if not fair_value.sufficient or fair_value.value is None:
        gates.append(SuppressionReason.FAIR_VALUE_INSUFFICIENT)
        detail.append(
            "fair value has insufficient completed-sale evidence; no resale "
            "benchmark means no opportunity, regardless of how cheap the card looks"
        )

    if dq.score < cfg.min_data_quality:
        gates.append(SuppressionReason.DATA_QUALITY)
        detail.append(f"data quality {dq.score} is below the {cfg.min_data_quality} floor")

    if not liquidity.is_measurable:
        if not cfg.allow_unmeasurable_liquidity:
            gates.append(SuppressionReason.LIQUIDITY_UNMEASURABLE)
            detail.append(
                "liquidity is not measurable from available sales; an exit route "
                "has not been demonstrated"
            )
    elif liquidity.score is not None and liquidity.score < cfg.min_liquidity_score:
        gates.append(SuppressionReason.LIQUIDITY)
        detail.append(
            f"liquidity score {liquidity.score} is below the {cfg.min_liquidity_score} "
            "floor; a wide spread on a card that rarely trades is not a trade"
        )

    cond_conf = (
        condition_confidence(condition_dist) if condition_dist is not None else Decimal("0")
    )
    if condition_dist is not None and cond_conf < cfg.min_condition_confidence:
        gates.append(SuppressionReason.CONDITION_CONFIDENCE)
        detail.append(
            f"condition confidence {cond_conf} is below the "
            f"{cfg.min_condition_confidence} floor"
        )

    if dq.newest_input_age_days is not None and dq.newest_input_age_days > cfg.max_data_age_days:
        gates.append(SuppressionReason.STALE_DATA)
        detail.append(
            f"newest evidence is {dq.newest_input_age_days} days old, past the "
            f"{cfg.max_data_age_days} day ceiling"
        )

    if gates:
        return OpportunityAssessment(
            True, tuple(gates), tuple(detail), None, None, None, None, None, None,
            liquidity.expected_days_to_sell, dq, (),
        )

    assert fair_value.value is not None
    base_econ = economics(gross_resale)
    if base_econ.landed.unresolved:
        gates.append(SuppressionReason.LANDED_COST_UNRESOLVED)
        detail.extend(base_econ.landed.unresolved)
        return OpportunityAssessment(
            True, tuple(gates), tuple(detail), None, None, None, None, None, None,
            liquidity.expected_days_to_sell, dq, (),
        )

    point_roi = base_econ.roi
    cost = base_econ.capital_deployed_eur.amount

    # The purchase limit is part of the recommendation, not a display field: a
    # price above it cannot clear the required return by construction.
    if (
        max_buy_jpy is not None
        and purchase_jpy is not None
        and purchase_jpy.amount > max_buy_jpy.amount
    ):
        gates.append(SuppressionReason.ABOVE_MAX_BUY)
        detail.append(
            f"asking price {purchase_jpy.amount:.0f} JPY is above the "
            f"{max_buy_jpy.amount:.0f} JPY maximum that clears the required return"
        )
    if required_roi is not None and point_roi < required_roi:
        gates.append(SuppressionReason.BELOW_REQUIRED_RETURN)
        detail.append(
            f"expected return {point_roi:.1%} is below the required {required_roi:.1%}"
        )
    if gates:
        return OpportunityAssessment(
            True, tuple(gates), tuple(detail), None, None, None, None, None, None,
            liquidity.expected_days_to_sell, dq, (),
        )

    rng = random.Random(cfg.random_seed)
    # Evidence tightens the fair-value distribution: more sales, less spread.
    sigma = cfg.fair_value_rel_sigma / max(1.0, (fair_value.n_sales / 4.0) ** 0.5)
    if fair_value.dispersion is not None:
        sigma = max(sigma, float(fair_value.dispersion) / 3.0)
    cond_penalty = 1.0 - 0.4 * (1.0 - float(cond_conf))

    draws: list[float] = []
    base = gross_resale.amount
    currency = gross_resale.currency
    for _ in range(cfg.simulations):
        shock = Decimal(str(max(0.05, rng.gauss(1.0, sigma)) * cond_penalty))
        # Recalculate through the engine rather than scaling a stored ROI: the
        # cost stack has fixed components, so profit is not proportional to the
        # resale value and a scaled ROI would overstate the downside tail.
        draws.append(float(economics(Money(base * shock, currency)).roi))
    draws.sort()

    def q(p: float) -> Decimal:
        idx = min(len(draws) - 1, max(0, int(p * len(draws))))
        return Decimal(str(round(draws[idx], 4)))

    ranking_roi = q(cfg.ranking_quantile)
    days = liquidity.expected_days_to_sell
    annualised = None
    if days and days > 0:
        annualised = (ranking_roi * Decimal("365") / days).quantize(Decimal("0.0001"))

    scores = (
        Score(
            "Liquidity Score",
            liquidity.score,
            tuple(ScoreReason(r, "neutral", Decimal("0")) for r in liquidity.reasons),
        ),
        Score(
            "Data Quality Score",
            dq.score,
            tuple(ScoreReason(r, "neutral", Decimal("0")) for r in dq.reasons),
        ),
        Score(
            "Risk Adjusted Opportunity Score",
            (ranking_roi * Decimal("100")).quantize(Decimal("0.1")),
            (
                ScoreReason(
                    f"ranked on the {int(cfg.ranking_quantile * 100)}th percentile of "
                    f"simulated ROI, not the {point_roi:.1%} point estimate",
                    "down" if ranking_roi < point_roi else "up",
                    abs((point_roi - ranking_roi) * Decimal("100")).quantize(Decimal("0.1")),
                ),
                ScoreReason(
                    f"fair value uncertainty sigma {sigma:.3f} from {fair_value.n_sales} sales",
                    "neutral", Decimal("0"),
                ),
                ScoreReason(
                    f"condition confidence {cond_conf} applied as a {(1 - cond_penalty):.1%} haircut",
                    "down" if cond_penalty < 1 else "neutral",
                    Decimal(str(round((1 - cond_penalty) * 100, 1))),
                ),
            ),
        ),
    )

    return OpportunityAssessment(
        suppressed=False,
        suppression_reasons=(),
        suppression_detail=(),
        point_roi=point_roi.quantize(Decimal("0.0001")),
        roi_p10=q(0.10),
        roi_p25=q(0.25),
        roi_median=q(0.50),
        ranking_roi=ranking_roi,
        annualised_roi=annualised,
        expected_days_to_sell=days,
        data_quality=dq,
        scores=scores,
    )
