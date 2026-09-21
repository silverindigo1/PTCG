"""Liquidity model.

Liquidity is not a decoration on an opportunity card. In this system it is a
gate: below a configurable floor an opportunity is suppressed entirely rather
than ranked low, because a 150 percent spread on a card that trades twice a
year is a number, not a trade, and showing it at rank 40 still invites the
mistake.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from ..types import Listing, Money, Sale

__all__ = ["LiquidityBand", "LiquidityProfile", "compute_liquidity"]


class LiquidityBand(str, Enum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LiquidityProfile:
    band: LiquidityBand
    score: Optional[Decimal]  # 0..100, None when unmeasurable
    sales_7d: int
    sales_30d: int
    sales_90d: int
    active_listings: Optional[int]
    listing_to_sales_ratio: Optional[Decimal]
    median_days_between_sales: Optional[Decimal]
    price_dispersion: Optional[Decimal]
    spread_proxy: Optional[Decimal]
    expected_days_to_sell: Optional[Decimal]
    reasons: tuple[str, ...]

    @property
    def is_measurable(self) -> bool:
        return self.score is not None


def _count_within(sales: Sequence[Sale], as_of: datetime, days: int) -> int:
    cutoff = as_of - timedelta(days=days)
    return sum(1 for s in sales if cutoff <= _aware(s.sold_at) <= as_of)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_liquidity(
    sales: Sequence[Sale],
    listings: Sequence[Listing],
    *,
    as_of: datetime,
    min_sales_for_score: int = 2,
) -> LiquidityProfile:
    """Liquidity profile from completed sales and current supply.

    Returns ``score=None`` when there is not enough activity to measure rather
    than returning a low score, because "unmeasurable" and "illiquid" call for
    different handling: the first needs more data, the second needs avoiding.
    """
    as_of = _aware(as_of)
    reasons: list[str] = []

    s7 = _count_within(sales, as_of, 7)
    s30 = _count_within(sales, as_of, 30)
    s90 = _count_within(sales, as_of, 90)

    active = len(listings) if listings else None
    ratio = (
        Decimal(active) / Decimal(max(s90, 1)) if active is not None and s90 else None
    )

    ordered = sorted((_aware(s.sold_at) for s in sales), reverse=True)
    gaps = [
        Decimal(str((ordered[i] - ordered[i + 1]).total_seconds() / 86400.0))
        for i in range(len(ordered) - 1)
    ]
    median_gap = (
        Decimal(str(statistics.median([float(g) for g in gaps]))) if gaps else None
    )

    sale_prices = [s.price.amount for s in sales if s.price.amount > 0]
    dispersion = None
    if len(sale_prices) >= 4:
        med = Decimal(str(statistics.median([float(p) for p in sale_prices])))
        if med > 0:
            q = statistics.quantiles([float(p) for p in sale_prices], n=4)
            dispersion = (Decimal(str(q[2])) - Decimal(str(q[0]))) / med

    spread = None
    if listings and sale_prices:
        listing_prices = [l.price.amount for l in listings if l.price.amount > 0]
        if listing_prices:
            med_list = Decimal(str(statistics.median([float(p) for p in listing_prices])))
            med_sale = Decimal(str(statistics.median([float(p) for p in sale_prices])))
            if med_sale > 0:
                spread = (med_list - med_sale) / med_sale

    if s90 < min_sales_for_score:
        reasons.append(
            f"only {s90} completed sale(s) in 90 days; liquidity is not measurable, "
            "which is different from being low"
        )
        return LiquidityProfile(
            LiquidityBand.UNKNOWN, None, s7, s30, s90, active, ratio, median_gap,
            dispersion, spread, None, tuple(reasons),
        )

    # Velocity is the dominant term. Depth of supply and price agreement adjust it.
    velocity = Decimal(s90) / Decimal("90")  # sales per day
    velocity_score = min(Decimal("70"), velocity * Decimal("700"))
    reasons.append(f"{s90} sale(s) in 90 days drives the base score")

    depth_score = Decimal("0")
    if active is not None:
        if active == 0:
            reasons.append("no active listings; exit route unproven")
        else:
            depth_score = min(Decimal("15"), Decimal(active) * Decimal("1.5"))
            reasons.append(f"{active} active listing(s) confirm a live market")

    agreement_score = Decimal("15")
    if dispersion is not None:
        if dispersion > Decimal("0.6"):
            agreement_score = Decimal("3")
            reasons.append(f"wide price dispersion (IQR/median {dispersion:.2f}) reduces the score")
        elif dispersion > Decimal("0.3"):
            agreement_score = Decimal("9")
            reasons.append(f"moderate price dispersion (IQR/median {dispersion:.2f})")
        else:
            reasons.append(f"tight price agreement (IQR/median {dispersion:.2f}) raises confidence")

    if ratio is not None and ratio > Decimal("8"):
        agreement_score = max(Decimal("0"), agreement_score - Decimal("6"))
        reasons.append(
            f"listing-to-sales ratio {ratio:.1f} suggests supply well ahead of demand"
        )

    score = min(Decimal("100"), velocity_score + depth_score + agreement_score)

    if score >= 80:
        band = LiquidityBand.VERY_HIGH
    elif score >= 60:
        band = LiquidityBand.HIGH
    elif score >= 40:
        band = LiquidityBand.MEDIUM
    elif score >= 20:
        band = LiquidityBand.LOW
    else:
        band = LiquidityBand.VERY_LOW

    expected_days = (
        Decimal("90") / Decimal(s90) if s90 else None
    )
    if expected_days is not None and active:
        # Queue position: more competing listings means a longer wait.
        expected_days = expected_days * (Decimal("1") + Decimal(active) / Decimal("10"))

    return LiquidityProfile(
        band, score.quantize(Decimal("0.1")), s7, s30, s90, active, ratio,
        median_gap, dispersion, spread,
        expected_days.quantize(Decimal("0.1")) if expected_days else None,
        tuple(reasons),
    )
