"""Fair value from completed sales only.

Non-negotiable rules encoded here:

* **Only completed sales.** The function does not accept listings. There is no
  parameter that would let one in. The brief's Test 4 (one absurd listing must
  not become fair value) is satisfied by the type signature, not by a check.
* **Below the minimum evidence count, there is no number.** The window returns
  ``sufficient=False``. It does not return a wide interval or a shaky point
  estimate, because a shaky point estimate is what gets spent at a counter.
* **Outliers are down-weighted and recorded, never deleted.** A 790 EUR sale
  among 180 EUR sales is suspicious in both directions: it might be a different
  variant, and so might a suspiciously cheap one.
* **Every returned value carries the sales that produced it**, with weights, so
  the UI can show the working.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional, Sequence

from ..types import (
    Currency,
    EuCondition,
    EU_CONDITION_LADDER,
    EvidenceRef,
    FairValue,
    FxRate,
    Language,
    Money,
    Sale,
    ValueBasis,
)

__all__ = [
    "FairValueConfig",
    "ConditionValue",
    "ConditionValueCurve",
    "compute_fair_value",
    "compute_fair_value_curve",
    "condition_value_curve",
    "weighted_median",
    "mad_outliers",
    "DEFAULT_CONDITION_MULTIPLIERS",
]

#: Multipliers converting a sale at condition C to an NM-equivalent price.
#: These are a documented starting prior, not a measurement. The calibration
#: job replaces them per category with estimates from observed data, and the
#: resulting fair value records which source was used in ``method``.
DEFAULT_CONDITION_MULTIPLIERS: Mapping[EuCondition, Decimal] = {
    EuCondition.NM: Decimal("1.00"),
    EuCondition.EX: Decimal("0.85"),
    EuCondition.GD: Decimal("0.72"),
    EuCondition.LP: Decimal("0.62"),
    EuCondition.PL: Decimal("0.45"),
    EuCondition.PO: Decimal("0.28"),
}


@dataclass(frozen=True, slots=True)
class FairValueConfig:
    """Tuning knobs. Defaults lean conservative in every direction."""

    #: Minimum Kish effective sample size before a window produces a number.
    #: Kish n_eff = (sum w)^2 / sum(w^2). For equally-weighted sales this equals
    #: the raw count; when one ancient or unreliable sale dominates the weights
    #: it collapses toward 1, which is exactly the situation where a point
    #: estimate should not be produced.
    min_effective_sales: Decimal = Decimal("2.5")
    #: Minimum raw sale count regardless of weights.
    min_raw_sales: int = 3
    #: Recency half-life in days. Newer sales dominate without one sale
    #: being able to move the median on its own.
    half_life_days: Decimal = Decimal("21")
    #: Median-absolute-deviation multiplier for outlier flagging.
    mad_k: Decimal = Decimal("3.5")
    #: Reliability weight per source id. Unlisted sources get this default.
    default_source_reliability: Decimal = Decimal("0.7")
    source_reliability: Mapping[str, Decimal] = None  # type: ignore[assignment]
    condition_multipliers: Mapping[EuCondition, Decimal] = None  # type: ignore[assignment]
    #: Sales with condition unknown are down-weighted rather than dropped,
    #: because dropping them biases toward whichever conditions get stated.
    unknown_condition_weight: Decimal = Decimal("0.35")
    target_currency: Currency = Currency.EUR

    def __post_init__(self) -> None:
        if self.source_reliability is None:
            object.__setattr__(self, "source_reliability", {})
        if self.condition_multipliers is None:
            object.__setattr__(
                self, "condition_multipliers", dict(DEFAULT_CONDITION_MULTIPLIERS)
            )


def weighted_median(pairs: Sequence[tuple[Decimal, Decimal]]) -> Optional[Decimal]:
    """Weighted median of ``(value, weight)`` pairs.

    Used instead of a mean throughout. A mean lets one bad print run, one fake,
    or one auction-sniping accident move the number that gets spent.
    """
    usable = [(v, w) for v, w in pairs if w > 0]
    if not usable:
        return None
    usable.sort(key=lambda p: p[0])
    total = sum(w for _, w in usable)
    half = total / Decimal("2")
    running = Decimal("0")
    for value, weight in usable:
        running += weight
        if running >= half:
            return value
    return usable[-1][0]


def _median(values: Sequence[Decimal]) -> Optional[Decimal]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def mad_outliers(
    values: Sequence[Decimal], k: Decimal = Decimal("3.5")
) -> tuple[frozenset[int], Optional[Decimal], Optional[Decimal]]:
    """Flag outliers by median absolute deviation.

    Returns ``(indices, median, mad)``. Both tails are flagged: a suspiciously
    low sale usually means wrong variant, damage, or a counterfeit, and is at
    least as dangerous as a high one because it drags fair value down.
    """
    med = _median(values)
    if med is None or len(values) < 4:
        return frozenset(), med, None
    deviations = [abs(v - med) for v in values]
    mad = _median(deviations)
    if mad is None or mad == 0:
        return frozenset(), med, mad
    # 1.4826 scales MAD to a normal-consistent standard deviation estimate.
    scaled = mad * Decimal("1.4826")
    flagged = {
        i for i, v in enumerate(values) if abs(v - med) > k * scaled
    }
    return frozenset(flagged), med, mad


def _recency_weight(age_days: Decimal, half_life: Decimal) -> Decimal:
    if half_life <= 0:
        return Decimal("1")
    try:
        decay = Decimal(str(math.pow(0.5, float(age_days / half_life))))
    except (InvalidOperation, ValueError, OverflowError):  # pragma: no cover
        return Decimal("0")
    return max(Decimal("0"), decay)


def _to_target(
    sale: Sale, fx: Mapping[tuple[Currency, Currency], FxRate], target: Currency
) -> Optional[Money]:
    if sale.price.currency is target:
        return sale.price
    rate = fx.get((sale.price.currency, target))
    if rate is None:
        return None
    return rate.convert(sale.price)


def compute_fair_value(
    sales: Sequence[Sale],
    *,
    variant_id: str,
    as_of: datetime,
    window_days: Optional[int],
    fx_rates: Optional[Mapping[tuple[Currency, Currency], FxRate]] = None,
    config: Optional[FairValueConfig] = None,
    normalize_to: EuCondition = EuCondition.NM,
    grade_bucket: str = "raw",
    market: Optional[str] = None,
    language: Optional[Language] = None,
) -> FairValue:
    """Compute condition-normalised fair value for one variant in one window.

    ``sales`` must already be restricted to a single variant; the function
    asserts rather than silently filtering, because a caller that passes mixed
    variants has a bug worth surfacing loudly.

    ``grade_bucket``, ``market`` and ``language`` are the other three scoping
    keys and they are enforced here rather than trusted to the caller. A PSA 10
    is a different asset from the raw card and from a PSA 9; a Japanese sale is
    evidence of a Japanese price and not of a European resale price. Sales
    outside the scope are recorded in ``excluded`` with the reason, not dropped
    silently.
    """
    cfg = config or FairValueConfig()
    fx = fx_rates or {}

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    scoped = [s for s in sales if s.variant_id == variant_id]
    if len(scoped) != len(sales):
        raise ValueError(
            "compute_fair_value received sales for more than one variant. "
            "Cross-variant pooling is never permitted."
        )

    cutoff = as_of - timedelta(days=window_days) if window_days else None
    notes: list[str] = []

    prepared: list[tuple[Sale, Decimal, Decimal, EvidenceRef]] = []
    excluded: list[EvidenceRef] = []
    seen_identity: set[tuple[str, str]] = set()
    seen_fingerprint: set[tuple[str, str, str, str]] = set()

    def drop(sale: Sale, when: datetime, reason: str) -> None:
        excluded.append(
            EvidenceRef(
                "sale", sale.sale_id, sale.source_id, sale.source_url, when,
                Decimal("0"), reason,
            )
        )

    for sale in scoped:
        sold_at = sale.sold_at if sale.sold_at.tzinfo else sale.sold_at.replace(tzinfo=timezone.utc)

        # Scope first. A price only counts as evidence for the thing it is
        # evidence of: the same grade bucket, the same market, the same
        # language. A PSA 10 is not a raw card and a Tokyo sale is not a
        # European resale benchmark.
        if sale.grade_bucket != grade_bucket:
            drop(sale, sold_at,
                 f"grade bucket {sale.grade_bucket} is not {grade_bucket}; "
                 "raw and each grader/grade combination price separately")
            continue
        if market is not None and (sale.market or "").upper() != market.upper():
            drop(sale, sold_at,
                 f"market {sale.market or 'unknown'} is not {market}; "
                 "out-of-market sales are not resale evidence for this market")
            continue
        if language is not None and sale.language is not language:
            drop(sale, sold_at,
                 f"language {sale.language.value} is not {language.value}")
            continue

        # Known-at. Filtering on the transaction date alone lets a backtest use
        # a sale that had not yet been published on the calculation date.
        known_at = sale.known_at or sold_at
        if known_at.tzinfo is None:
            known_at = known_at.replace(tzinfo=timezone.utc)
        if known_at > as_of:
            drop(sale, sold_at,
                 f"evidence became known at {known_at.isoformat()}, after the "
                 "calculation time; excluded to prevent look-ahead")
            continue

        if cutoff and sold_at < cutoff:
            continue
        if sold_at > as_of:
            continue

        # Deduplicate on stable transaction identity, then on a content
        # fingerprint. Re-importing the same file, or the same sale arriving
        # from one source twice, must not manufacture independent evidence.
        identity = sale.dedupe_key
        if identity in seen_identity:
            drop(sale, sold_at,
                 f"duplicate of already-counted transaction {identity[1]} from "
                 f"{identity[0]}; not independent evidence")
            continue
        fingerprint = (
            sale.source_id,
            sold_at.date().isoformat(),
            str(sale.price.amount),
            sale.price.currency.value,
        )
        if fingerprint in seen_fingerprint:
            drop(sale, sold_at,
                 "same source, same day, same price as an already-counted sale; "
                 "treated as a duplicate rather than independent evidence")
            continue
        seen_identity.add(identity)
        seen_fingerprint.add(fingerprint)

        converted = _to_target(sale, fx, cfg.target_currency)
        if converted is None:
            excluded.append(
                EvidenceRef(
                    "sale", sale.sale_id, sale.source_id, sale.source_url, sold_at,
                    Decimal("0"),
                    f"no dated FX rate {sale.price.currency.value}->{cfg.target_currency.value}",
                )
            )
            continue

        if sale.condition is None:
            multiplier = cfg.condition_multipliers[normalize_to]
            condition_weight = cfg.unknown_condition_weight
        else:
            multiplier = cfg.condition_multipliers[sale.condition]
            condition_weight = Decimal("1")
        if multiplier <= 0:  # pragma: no cover - defensive
            continue

        target_multiplier = cfg.condition_multipliers[normalize_to]
        normalized = converted.amount * (target_multiplier / multiplier)

        age_days = Decimal(str((as_of - sold_at).total_seconds() / 86400.0))
        weight = (
            _recency_weight(age_days, cfg.half_life_days)
            * cfg.source_reliability.get(sale.source_id, cfg.default_source_reliability)
            * condition_weight
            * sale.condition_confidence
        )

        ref = EvidenceRef(
            "sale", sale.sale_id, sale.source_id, sale.source_url, sold_at, weight,
            f"{converted.amount} {cfg.target_currency.value} at {sale.condition.value if sale.condition else 'condition unknown'}",
        )
        prepared.append((sale, normalized, weight, ref))

    if not prepared:
        return FairValue(
            window_days=window_days,
            value=None,
            sufficient=False,
            n_sales=0,
            n_effective=Decimal("0"),
            dispersion=None,
            method="weighted-median/mad",
            inputs=(),
            excluded=tuple(excluded),
            notes=("no completed sales in window",),
        )

    values = [p[1] for p in prepared]
    flagged, median_value, mad = mad_outliers(values, cfg.mad_k)

    final_inputs: list[EvidenceRef] = []
    pairs: list[tuple[Decimal, Decimal]] = []
    for idx, (sale, normalized, weight, ref) in enumerate(prepared):
        if idx in flagged:
            direction = "above" if normalized > (median_value or 0) else "below"
            excluded.append(
                EvidenceRef(
                    ref.kind, ref.record_id, ref.source_id, ref.source_url, ref.observed_at,
                    Decimal("0"),
                    f"MAD outlier {direction} median at k={cfg.mad_k}; "
                    "flagged for review, not deleted",
                )
            )
            continue
        pairs.append((normalized, weight))
        final_inputs.append(ref)

    weight_sum = sum(w for _, w in pairs)
    weight_sq_sum = sum(w * w for _, w in pairs)
    n_effective = (
        (weight_sum * weight_sum) / weight_sq_sum if weight_sq_sum > 0 else Decimal("0")
    )
    n_raw = len(pairs)

    if flagged:
        notes.append(
            f"{len(flagged)} sale(s) flagged as outliers and down-weighted to zero; "
            "investigate for wrong variant, damage or counterfeit"
        )

    if n_raw < cfg.min_raw_sales or n_effective < cfg.min_effective_sales:
        notes.append(
            f"insufficient evidence: {n_raw} usable sale(s), Kish effective "
            f"sample size {n_effective:.2f}, need {cfg.min_raw_sales} and "
            f"{cfg.min_effective_sales}"
        )
        return FairValue(
            window_days=window_days,
            value=None,
            sufficient=False,
            n_sales=n_raw,
            n_effective=Decimal(n_effective),
            dispersion=None,
            method="weighted-median/mad",
            inputs=tuple(final_inputs),
            excluded=tuple(excluded),
            notes=tuple(notes),
        )

    centre = weighted_median(pairs)
    assert centre is not None

    ordered = sorted(v for v, _ in pairs)
    q1 = ordered[max(0, int(len(ordered) * 0.25) - (0 if len(ordered) % 4 else 1))]
    q3 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.75))]
    dispersion = (q3 - q1) / centre if centre > 0 else None

    return FairValue(
        window_days=window_days,
        value=Money(centre, cfg.target_currency).quantize(),
        sufficient=True,
        n_sales=n_raw,
        n_effective=Decimal(n_effective),
        dispersion=dispersion,
        method=f"weighted-median/mad(k={cfg.mad_k}),half_life={cfg.half_life_days}d,normalized_to={normalize_to.value}",
        inputs=tuple(final_inputs),
        excluded=tuple(excluded),
        notes=tuple(notes),
    )


def compute_fair_value_curve(
    sales: Sequence[Sale],
    *,
    variant_id: str,
    as_of: datetime,
    fx_rates: Optional[Mapping[tuple[Currency, Currency], FxRate]] = None,
    config: Optional[FairValueConfig] = None,
    windows: Sequence[int] = (7, 30, 90, 365),
    grade_bucket: str = "raw",
    market: Optional[str] = None,
    language: Optional[Language] = None,
) -> dict[str, FairValue]:
    """All windows plus a blended current estimate.

    The blend prefers the shortest window that meets the evidence rule, which
    keeps the number responsive without letting a thin 7-day window speak for
    a market that only trades monthly.
    """
    out: dict[str, FairValue] = {}
    for w in windows:
        out[f"{w}d"] = compute_fair_value(
            sales, variant_id=variant_id, as_of=as_of, window_days=w,
            fx_rates=fx_rates, config=config,
            grade_bucket=grade_bucket, market=market, language=language,
        )

    current = next(
        (out[f"{w}d"] for w in sorted(windows) if out[f"{w}d"].sufficient),
        None,
    )
    if current is None:
        out["current"] = FairValue(
            window_days=None, value=None, sufficient=False, n_sales=0,
            n_effective=Decimal("0"), dispersion=None,
            method="blend/shortest-sufficient",
            notes=("no window met the minimum evidence rule",),
        )
    else:
        out["current"] = FairValue(
            window_days=current.window_days,
            value=current.value,
            sufficient=True,
            n_sales=current.n_sales,
            n_effective=current.n_effective,
            dispersion=current.dispersion,
            method=f"blend/shortest-sufficient -> {current.window_days}d",
            inputs=current.inputs,
            excluded=current.excluded,
            notes=current.notes,
        )
    return out


# ------------------------------------------------- per-condition valuation --

@dataclass(frozen=True, slots=True)
class ConditionValue:
    """Resale value at one point on the condition ladder, and where it came from."""

    condition: EuCondition
    value: Optional[Money]
    basis: ValueBasis
    n_sales: int
    note: str


@dataclass(frozen=True, slots=True)
class ConditionValueCurve:
    """Value across the whole ladder, with coverage stated rather than implied."""

    values: Mapping[EuCondition, ConditionValue]
    anchor: FairValue
    #: Conditions priced from their own completed sales.
    observed: tuple[EuCondition, ...]
    #: Conditions derived from the anchor by multiplier. These are a documented
    #: prior, not a measurement, and the distinction is carried to the response.
    modelled: tuple[EuCondition, ...]
    notes: tuple[str, ...] = ()

    def money_map(self) -> dict[EuCondition, Optional[Money]]:
        return {c: v.value for c, v in self.values.items()}


def condition_value_curve(
    sales: Sequence[Sale],
    *,
    variant_id: str,
    as_of: datetime,
    anchor: FairValue,
    fx_rates: Optional[Mapping[tuple[Currency, Currency], FxRate]] = None,
    config: Optional[FairValueConfig] = None,
    window_days: Optional[int] = 365,
    grade_bucket: str = "raw",
    market: Optional[str] = None,
    language: Optional[Language] = None,
) -> ConditionValueCurve:
    """Value at every condition, measured where possible and modelled otherwise.

    The condition distribution from the shop's grade label is worthless unless
    there is a value to attach to each condition. Where a condition has its own
    completed sales, it is priced from them. Where it does not, it is derived
    from the anchor by the documented multiplier and labelled ``MODELLED``, so
    a reader can see which numbers are measurements and which are assumptions.
    """
    cfg = config or FairValueConfig()
    values: dict[EuCondition, ConditionValue] = {}
    observed: list[EuCondition] = []
    modelled: list[EuCondition] = []
    notes: list[str] = []

    anchor_value = anchor.value if anchor.sufficient else None

    for cond in EU_CONDITION_LADDER:
        own = [s for s in sales if s.condition is cond]
        fv = None
        if own:
            fv = compute_fair_value(
                own, variant_id=variant_id, as_of=as_of, window_days=window_days,
                fx_rates=fx_rates, config=cfg, normalize_to=cond,
                grade_bucket=grade_bucket, market=market, language=language,
            )
        if fv is not None and fv.sufficient and fv.value is not None:
            values[cond] = ConditionValue(
                cond, fv.value, ValueBasis.OBSERVED, fv.n_sales,
                f"priced from {fv.n_sales} completed sale(s) at {cond.value}",
            )
            observed.append(cond)
            continue

        if anchor_value is None:
            values[cond] = ConditionValue(
                cond, None, ValueBasis.MODELLED, 0,
                "no anchor value and no sales at this condition",
            )
            continue

        multiplier = cfg.condition_multipliers[cond]
        base_multiplier = cfg.condition_multipliers[EuCondition.NM]
        derived = anchor_value.amount * (multiplier / base_multiplier)
        values[cond] = ConditionValue(
            cond, Money(derived, anchor_value.currency).quantize(),
            ValueBasis.MODELLED, len(own),
            f"derived from the NM anchor by the documented {multiplier} multiplier; "
            "a prior, not a measurement",
        )
        modelled.append(cond)

    if modelled:
        notes.append(
            f"{len(modelled)} of {len(EU_CONDITION_LADDER)} ladder points are "
            "modelled from the NM anchor rather than measured: "
            + ", ".join(c.value for c in modelled)
        )
    if observed:
        notes.append(
            "measured from their own sales: " + ", ".join(c.value for c in observed)
        )

    return ConditionValueCurve(
        values=values,
        anchor=anchor,
        observed=tuple(observed),
        modelled=tuple(modelled),
        notes=tuple(notes),
    )
