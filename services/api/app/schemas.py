"""API request and response models.

Response shapes carry ``null`` plus a reason rather than a default, everywhere.
A client rendering this can always distinguish "measured as X", "not measured"
and "measured but insufficient evidence", which is what keeps the UI honest.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    STRONG_OPPORTUNITY = "STRONG_OPPORTUNITY"
    OPPORTUNITY = "OPPORTUNITY"
    PASS = "PASS"
    #: Deliberately separate from PASS. "I do not know" and "no" call for
    #: different behaviour when you are holding the card.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class QuickCheckRequest(BaseModel):
    raw_title: str = Field(..., description="What the shop tag or card says")
    price_jpy: Decimal = Field(..., gt=0)
    source_grade_label: Optional[str] = Field(
        None, description="Shop's own grade, e.g. 'A-'. Not mapped to a fixed EU grade."
    )
    shop_source_id: Optional[str] = Field(
        None, description="Which shop, since grade scales differ between shops"
    )
    set_code: Optional[str] = None
    number: Optional[str] = None
    language: Optional[str] = "ja"
    printing: Optional[str] = None
    edition: Optional[str] = None
    stamp: Optional[str] = None
    scenario: str = Field("A_hand_carry", description="A_hand_carry | B_shipped | C_proxy")
    required_roi: Decimal = Field(Decimal("0.40"), ge=0)
    photo_ids: list[str] = Field(default_factory=list)

    # Material cost inputs. Exposed and validated rather than assumed, because
    # an unfilled shipping cost silently costed at zero produces a maximum buy
    # price that is too generous in exactly the direction that loses money.
    acquisition_purpose: str = Field(
        "resale",
        description=(
            "resale | personal. Danish relief for travellers' goods covers goods "
            "for private use only and is unavailable for goods imported with a "
            "view to resale, so this decides whether the allowance and the "
            "Japanese departure refund may be modelled at all. "
            "https://info.skat.dk/data.aspx?oid=2230232"
        ),
    )
    duty_rate: Optional[Decimal] = Field(
        None, ge=0, le=1,
        description="Verified tariff rate for the commodity code. None blocks above-threshold cases.",
    )
    fx_spread_rate: Decimal = Field(Decimal("0.015"), ge=0, le=1)
    proxy_fee_rate: Decimal = Field(Decimal("0"), ge=0, le=1)
    proxy_fee_fixed_jpy: Decimal = Field(Decimal("0"), ge=0)
    domestic_jp_shipping_jpy: Decimal = Field(Decimal("0"), ge=0)
    international_shipping_jpy: Decimal = Field(Decimal("0"), ge=0)
    marketplace_fee_rate: Decimal = Field(Decimal("0.05"), ge=0, le=1)
    payment_fee_rate: Decimal = Field(Decimal("0.029"), ge=0, le=1)
    outbound_shipping_eur: Decimal = Field(Decimal("0"), ge=0)
    expected_loss_rate: Decimal = Field(Decimal("0.02"), ge=0, le=1)
    items_in_consignment: int = Field(1, ge=1)
    basket_value_jpy: Optional[Decimal] = Field(
        None, gt=0,
        description="Total receipt or consignment value. Thresholds bite here, not per card.",
    )
    zero_logistics_cost_is_verified: bool = Field(
        False,
        description="Assert that a zero shipping or proxy cost is measured, not unfilled.",
    )


class Traceable(BaseModel):
    """A number with its provenance, or an explicit absence with a reason."""

    value: Optional[str] = None
    currency: Optional[str] = None
    known: bool = True
    reason: Optional[str] = None
    as_of: Optional[str] = None
    sources: list[str] = Field(default_factory=list)


class ConditionBreakdown(BaseModel):
    probabilities: dict[str, str]
    confidence: str
    is_provisional: bool
    source_grade_label: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class QuickCheckResponse(BaseModel):
    verdict: Verdict
    headline: str

    match_confidence: Decimal
    match_outcome: str
    variant_id: Optional[str] = None
    canonical_key: Optional[str] = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)

    # Market picture. Every one of these may legitimately be unknown.
    japanese_market_price: Traceable = Traceable(known=False, reason="not yet collected")
    european_fair_value: Traceable = Traceable(known=False, reason="not yet computed")
    condition_adjusted_fair_value: Traceable = Traceable(known=False)
    condition_value_ladder: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Value at each condition, with observed vs modelled stated per point.",
    )
    recent_sales: list[dict[str, Any]] = Field(default_factory=list)
    european_supply: Traceable = Traceable(known=False)
    japanese_supply: Traceable = Traceable(known=False)
    psa_total_population: Traceable = Traceable(
        known=False,
        reason=(
            "PSA population is not exposed by the PSA Public API, whose documented "
            "method set is cert verification only"
        ),
        sources=["https://www.psacard.com/publicapi/documentation"],
    )
    psa_10_population: Traceable = Traceable(known=False, reason="see psa_total_population")
    population_growth: Traceable = Traceable(known=False, reason="see psa_total_population")

    # Economics
    expected_net_resale: Traceable = Traceable(known=False)
    expected_profit_eur: Traceable = Traceable(known=False)
    expected_profit_dkk: Traceable = Traceable(known=False)
    expected_roi: Optional[str] = None
    roi_p25: Optional[str] = None
    gross_multiple: Optional[str] = None
    net_multiple: Optional[str] = None
    break_even_resale_eur: Optional[str] = None
    capital_deployed_eur: Traceable = Traceable(known=False)
    capital_basis: Optional[str] = None
    expected_tax_refund_eur: Traceable = Traceable(known=False)
    unresolved_costs: list[str] = Field(default_factory=list)

    # The numbers you actually act on
    max_buy_price_jpy: Optional[str] = None
    strong_buy_price_jpy: Optional[str] = None
    target_buy_price_jpy: Optional[str] = None
    do_not_buy_above_jpy: Optional[str] = None

    # Confidence and risk
    liquidity_band: Optional[str] = None
    liquidity_score: Optional[str] = None
    estimated_days_to_sell: Optional[str] = None
    data_quality_score: Optional[str] = None
    condition: Optional[ConditionBreakdown] = None
    risk_factors: list[str] = Field(default_factory=list)
    suppression_reasons: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    computed_at: Optional[str] = None
    data_age_days: Optional[int] = None
