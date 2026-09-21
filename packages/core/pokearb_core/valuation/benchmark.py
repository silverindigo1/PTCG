"""Resale value from a marketplace's published averages.

This is the second-best evidence the system accepts, and it is kept apart from
the first. ``compute_fair_value`` values a card from individual completed
sales and knows how many there were. A published average does not say how
many sales produced it, so it gets its own path, its own rules, and a label
that follows it all the way to the verdict.

The rules below are judgement, not findings. Each is a conservatism choice:
when in doubt it refuses, because the brief ranks a false signal as worse
than a missed trade. All thresholds live in ``BenchmarkConfig``.

1. **No look-ahead.** An observation fetched after the calculation time is
   excluded, the same rule completed sales follow.
2. **Fresh.** The provider recomputes daily; figures older than
   ``max_age_days`` are refused.
3. **Mapping must be checkable, and must check out.** TCGdex documents that
   different printings of one Pokemon can be mapped to the same Cardmarket
   listing (https://tcgdex.dev/faq). An observation with no product id cannot
   be checked and is refused; one whose product id is shared by a same-name
   sibling in the set is refused as a collision.
4. **The figures must agree with each other.** If the average, trend, 7-day
   and 30-day averages spread by more than ``max_spread`` of the largest, the
   market is thin or moving, and no single number describes it.
5. **The value is the lowest of the 7-day average, the 30-day average and the
   trend.** Not their mean: the lowest, so that a benchmark can only ever
   understate what a card sells for.

The value is treated as a near-mint equivalent. If Cardmarket's averages in
fact include worse-condition copies, the true near-mint price is higher than
this figure, so the downstream condition adjustment errs on the low side,
which is the intended direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

from ..types import Currency, MarketAverage, Money

__all__ = [
    "BASIS",
    "BenchmarkConfig",
    "MarketBenchmark",
    "assess_market_average",
]

#: The label every average-based number carries, from here to the response.
BASIS = "cardmarket_average_via_tcgdex"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    max_spread: Decimal = Decimal("0.30")
    max_age_days: int = 3
    min_points: int = 2
    #: Upper bound on any confidence derived from an average. Averages carry no
    #: sample size, so they can never be as trustworthy as counted sales.
    confidence_cap: Decimal = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class MarketBenchmark:
    accepted: bool
    value: Optional[Money]
    basis: str
    statistic: Optional[str]
    spread: Optional[Decimal]
    confidence_cap: Decimal
    observation: Optional[MarketAverage]
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def refused(self) -> bool:
        return not self.accepted


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def assess_market_average(
    observation: Optional[MarketAverage],
    *,
    as_of: datetime,
    sibling_product_ids: Optional[Sequence[str]],
    parser_reasons: Sequence[str] = (),
    config: Optional[BenchmarkConfig] = None,
) -> MarketBenchmark:
    """Accept or refuse one published average, and say exactly why.

    ``sibling_product_ids`` is the list of Cardmarket product ids held by other
    cards of the same name in the same set. ``None`` means the check could not
    be run, which is itself grounds for refusal: an unchecked mapping is not a
    clean one.
    """
    cfg = config or BenchmarkConfig()
    as_of = _aware(as_of)

    def refuse(*why: str) -> MarketBenchmark:
        return MarketBenchmark(
            accepted=False, value=None, basis=BASIS, statistic=None,
            spread=None, confidence_cap=cfg.confidence_cap,
            observation=observation, reasons=tuple(parser_reasons) + why,
        )

    if observation is None:
        return refuse() if parser_reasons else refuse("no observation")

    obs = observation
    if _aware(obs.known_at) > as_of:
        return refuse(
            f"figures were fetched at {obs.known_at.isoformat()}, after the "
            "calculation time; excluded to prevent look-ahead"
        )
    updated = _aware(obs.provider_updated_at)
    if updated > as_of:
        return refuse("provider timestamp is after the calculation time")
    age = as_of - updated
    if age > timedelta(days=cfg.max_age_days):
        return refuse(
            f"Cardmarket figures are {age.days} days old; the provider "
            f"updates daily and anything over {cfg.max_age_days} days is stale"
        )

    if not obs.product_id:
        return refuse(
            "no Cardmarket product id, so the mapping to this exact printing "
            "cannot be checked"
        )
    if sibling_product_ids is None:
        return refuse(
            "the same-name sibling check could not be run, so a mapping "
            "collision cannot be ruled out"
        )
    if obs.product_id in set(sibling_product_ids):
        return refuse(
            f"Cardmarket product {obs.product_id} is also mapped to another "
            "printing of the same Pokemon in this set, the defect TCGdex "
            "documents at https://tcgdex.dev/faq; the price could belong to "
            "either card"
        )

    core = {"7-day average": obs.avg7, "30-day average": obs.avg30, "trend": obs.trend}
    present = {k: v for k, v in core.items() if v is not None}
    if len(present) < cfg.min_points:
        return refuse(
            f"only {len(present)} of the 7-day average, 30-day average and trend "
            f"are published; at least {cfg.min_points} are required"
        )

    spread_inputs = [v for v in (obs.avg, obs.trend, obs.avg7, obs.avg30) if v is not None]
    top = max(spread_inputs)
    spread = (top - min(spread_inputs)) / top if top > 0 else Decimal("0")
    if spread > cfg.max_spread:
        figures = ", ".join(
            f"{name} {value}" for name, value in (
                ("avg", obs.avg), ("trend", obs.trend),
                ("7-day", obs.avg7), ("30-day", obs.avg30),
            ) if value is not None
        )
        return refuse(
            f"published figures disagree by {spread:.0%} ({figures}); a market "
            "this thin or fast-moving has no single price worth relying on"
        )

    name, value = min(present.items(), key=lambda kv: kv[1])
    return MarketBenchmark(
        accepted=True,
        value=Money(value, Currency.EUR).quantize(),
        basis=BASIS,
        statistic=f"lowest of the published {', '.join(present)}: the {name}",
        spread=spread.quantize(Decimal("0.0001")),
        confidence_cap=cfg.confidence_cap,
        observation=obs,
        reasons=tuple(parser_reasons) + (
            "Cardmarket averages relayed by TCGdex, not individual sales; the "
            "number of sales behind them is unknown",
            f"figures agree within {spread:.0%}",
        ),
    )
