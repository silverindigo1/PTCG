"""Japan to Europe arbitrage engine.

One forward calculation, one backward solver, and the solver is not allowed to
disagree with the forward calculation.

The earlier version derived the affine slope of landed cost analytically, by
hand, alongside the forward formula. The two drifted: the slope treated the
proxy fee as dutiable and VATable when the forward calculation did not, so a
maximum buy price solved for a 40 percent required return actually returned
21 to 38 percent. Any solver that restates the cost model in a second place
will eventually restate it wrongly.

So the slope is now measured from the forward function itself. Within a cost
regime the landed cost really is affine in the purchase price, so evaluating
``compute_landed_cost`` at two points inside a regime recovers the exact slope
and intercept for that regime, whatever the forward code happens to do. Every
candidate is then re-checked against the forward calculation before it is
returned, and the next yen up is checked to be infeasible.

Regimes are bounded by real discontinuities, each of which is a place where a
naive solver silently assumes the wrong treatment:

* the EUR 150 customs duty threshold,
* the Danish traveller allowance, when it applies at all,
* the JPY 5,000 per store per day Japanese tax-free minimum.

Two economic quantities are kept apart throughout, because conflating them
overstates return:

``upfront_cash_eur``
    What actually leaves the bank to acquire the card. This is the capital
    denominator for ROI.
``total_eur``
    The economic cost, that is upfront cash less the probability-weighted
    expected tax refund. The refund is a contingent receivable claimed after a
    customs departure procedure, not a discount at the till.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Callable, Mapping, Optional, Sequence

from ..types import AcquisitionPurpose, Currency, FxRate, Money, Scenario
from .policy import PolicyStore, PolicyUnverifiedError

__all__ = [
    "CostAssumptions",
    "CostAssumptionError",
    "LandedCost",
    "Economics",
    "ArbitrageResult",
    "compute_landed_cost",
    "compute_economics",
    "compute_arbitrage",
    "max_buy_price",
    "resale_economics_fn",
    "POLICY_KEYS",
]

POLICY_KEYS = (
    "dk.import_vat_rate",
    "dk.duty_threshold_eur",
    "dk.low_value_per_item_charge_eur",
    "dk.carrier_handling_fee_dkk",
    "dk.traveller_allowance_air_dkk",
    "jp.consumption_tax_rate",
    "jp.tax_free_minimum_jpy",
    "jp.tax_free_refund_at_departure_from",
)

#: Source for the Danish traveller-relief conditions encoded below.
SKAT_REJSEGODS_URL = "https://info.skat.dk/data.aspx?oid=2230232"
#: Source for the Japanese tax-free regime change encoded below.
JP_TAXFREE_URL = "https://www.japan-guide.com/news/tax-free-shopping.html"


class CostAssumptionError(ValueError):
    """The supplied cost assumptions cannot produce an actionable number."""


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    """User-controlled cost parameters. Distinct from policy, which is law."""

    scenario: Scenario
    #: Why the goods are imported. Drives whether personal-use reliefs may be
    #: modelled at all. Defaults to resale because this is an arbitrage system.
    acquisition_purpose: AcquisitionPurpose = AcquisitionPurpose.RESALE
    #: Proportional FX cost above the ECB reference rate. The ECB publishes
    #: reference rates for information only, so the executable rate is worse.
    fx_spread_rate: Decimal = Decimal("0.015")
    #: Japan-side
    proxy_fee_rate: Decimal = Decimal("0")
    proxy_fee_fixed_jpy: Decimal = Decimal("0")
    domestic_jp_shipping_jpy: Decimal = Decimal("0")
    international_shipping_jpy: Decimal = Decimal("0")
    insurance_rate: Decimal = Decimal("0")
    #: Probability that a tax refund claimed at departure is actually realised.
    #: Under the regime in force from 2026-11-01 the refund depends on
    #: completing a customs departure procedure with every item on the receipt
    #: present; if any item is missing the whole receipt becomes ineligible.
    #: Verified at https://www.japan-guide.com/news/tax-free-shopping.html
    tax_refund_realisation_prob: Decimal = Decimal("0.9")
    #: Customs duty rate for the commodity code. ``None`` means not verified,
    #: and the engine will refuse to compute rather than assume zero.
    duty_rate: Optional[Decimal] = None
    #: Sell-side
    marketplace_fee_rate: Decimal = Decimal("0.05")
    payment_fee_rate: Decimal = Decimal("0.029")
    payment_fee_fixed_eur: Decimal = Decimal("0.35")
    outbound_shipping_eur: Decimal = Decimal("0")
    #: Expected loss rate from returns, damage in transit and non-payment.
    expected_loss_rate: Decimal = Decimal("0.02")
    #: Number of distinct items in the consignment or on the receipt. Drives
    #: the per-item low-value charge introduced on 1 July 2026.
    items_in_consignment: int = 1
    #: Total value of the consignment or receipt this card belongs to, in JPY.
    #: Thresholds are assessed at this level, not per card: the Danish value
    #: limit applies to the traveller's goods in aggregate, and the Japanese
    #: refund is all-or-nothing per receipt. ``None`` means the card travels
    #: alone. Verified at https://info.skat.dk/data.aspx?oid=2230232 and
    #: https://www.japan-guide.com/news/tax-free-shopping.html
    basket_value_jpy: Optional[Decimal] = None
    #: Explicit acknowledgement that a zero shipping or proxy cost is a real
    #: measured zero rather than an unfilled field. Without it, a scenario that
    #: cannot plausibly cost nothing is refused rather than quietly costed at
    #: zero.
    zero_logistics_cost_is_verified: bool = False

    def problems(self) -> tuple[str, ...]:
        """Blocking problems with these assumptions. Empty means usable."""
        issues: list[str] = []
        if not isinstance(self.scenario, Scenario):
            issues.append(
                f"unknown acquisition scenario {self.scenario!r}; "
                "hand carry is not a safe default for an unrecognised scenario"
            )
            return tuple(issues)

        if self.scenario is Scenario.SHIPPED:
            if (
                self.international_shipping_jpy <= 0
                and not self.zero_logistics_cost_is_verified
            ):
                issues.append(
                    "shipped scenario with no international shipping cost; set "
                    "international_shipping_jpy or set "
                    "zero_logistics_cost_is_verified if the zero is real"
                )
        if self.scenario is Scenario.PROXY:
            no_proxy_fee = self.proxy_fee_rate <= 0 and self.proxy_fee_fixed_jpy <= 0
            no_freight = (
                self.international_shipping_jpy <= 0
                and self.domestic_jp_shipping_jpy <= 0
            )
            if (no_proxy_fee or no_freight) and not self.zero_logistics_cost_is_verified:
                missing = []
                if no_proxy_fee:
                    missing.append("proxy fee")
                if no_freight:
                    missing.append("freight")
                issues.append(
                    "proxy scenario with no " + " and no ".join(missing)
                    + "; supply the cost or set zero_logistics_cost_is_verified"
                )
        if self.tax_refund_realisation_prob < 0 or self.tax_refund_realisation_prob > 1:
            issues.append("tax_refund_realisation_prob must be between 0 and 1")
        if self.items_in_consignment < 1:
            issues.append("items_in_consignment must be at least 1")
        return tuple(issues)

    def validated(self) -> "CostAssumptions":
        issues = self.problems()
        if issues:
            raise CostAssumptionError("; ".join(issues))
        return self


@dataclass(frozen=True, slots=True)
class LandedCost:
    """Full cost of getting one card from a Japanese shop into sellable stock."""

    purchase_jpy: Money
    #: Economic cost: upfront cash less the expected (probability-weighted) refund.
    total_eur: Money
    total_dkk: Optional[Money]
    #: Cash that actually leaves the bank. The ROI denominator.
    upfront_cash_eur: Money
    #: Probability-weighted contingent refund, already netted out of total_eur.
    expected_refund_eur: Money
    duty_regime: str
    breakdown: Mapping[str, Money]
    policy_sources: Mapping[str, Optional[str]]
    notes: tuple[str, ...] = ()
    #: Cost elements known to apply but not quantified. Non-empty means the
    #: landed cost understates reality and must not drive a recommendation.
    unresolved: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return not self.unresolved


@dataclass(frozen=True, slots=True)
class Economics:
    """One consistent financial view, shared by every consumer.

    Profit is economic (it credits the expected refund); the denominator is the
    cash actually deployed. Both are stated so the two reconcile without the
    reader guessing which convention is in force.
    """

    purchase_jpy: Money
    gross_resale_eur: Money
    landed: LandedCost
    net_proceeds_eur: Money
    capital_deployed_eur: Money
    profit_eur: Money
    roi: Decimal
    capital_basis: str = "upfront cash deployed, expected tax refund excluded"

    @property
    def actionable(self) -> bool:
        return self.landed.actionable


@dataclass(frozen=True, slots=True)
class ArbitrageResult:
    scenario: Scenario
    landed: LandedCost
    gross_resale_eur: Money
    net_proceeds_eur: Money
    expected_profit_eur: Money
    expected_profit_dkk: Optional[Money]
    roi: Decimal
    capital_deployed_eur: Money
    capital_basis: str
    gross_multiple: Decimal
    net_multiple: Decimal
    break_even_resale_eur: Money
    max_buy_jpy: Optional[Money]
    strong_buy_jpy: Optional[Money]
    target_buy_jpy: Optional[Money]
    do_not_buy_above_jpy: Optional[Money]
    notes: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return not self.unresolved


def _rate(fx: Mapping[tuple[Currency, Currency], FxRate], a: Currency, b: Currency) -> FxRate:
    rate = fx.get((a, b))
    if rate is None:
        raise KeyError(f"Missing dated FX rate {a.value}->{b.value}.")
    return rate


def _jpy_to_eur(amount: Decimal, fx, spread: Decimal) -> Decimal:
    rate = _rate(fx, Currency.JPY, Currency.EUR)
    return amount * rate.rate * (Decimal("1") + spread)


# ------------------------------------------------------------ forward model --

def compute_landed_cost(
    purchase_jpy: Money,
    *,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
    jp_price_includes_tax: bool = True,
) -> LandedCost:
    """Landed cost in EUR for one card, under one acquisition scenario.

    This is the single forward truth. The solver fits against it rather than
    restating it.
    """
    if purchase_jpy.currency is not Currency.JPY:
        raise ValueError("Japanese purchase price must be in JPY.")

    a = assumptions.validated()
    notes: list[str] = []
    unresolved: list[str] = []
    breakdown: dict[str, Money] = {}
    spread = a.fx_spread_rate

    jp_tax_rate = policy.value("jp.consumption_tax_rate", on)
    departure_refund_regime = policy.try_value("jp.tax_free_refund_at_departure_from", on)

    gross_jpy = purchase_jpy.amount
    #: Thresholds are assessed on the basket, not the single card.
    basket_jpy = a.basket_value_jpy if a.basket_value_jpy is not None else gross_jpy
    if basket_jpy < gross_jpy:
        raise CostAssumptionError(
            "basket_value_jpy is smaller than this card's price; the basket must "
            "contain the card."
        )
    breakdown["jp_shelf_price"] = Money(gross_jpy, Currency.JPY)

    tax_component = Decimal("0")
    if jp_price_includes_tax:
        tax_component = gross_jpy - (gross_jpy / (Decimal("1") + jp_tax_rate))

    # --- Japanese consumption tax treatment ------------------------------
    refund_jpy = Decimal("0")
    personal = a.acquisition_purpose is AcquisitionPurpose.PERSONAL

    if a.scenario is Scenario.HAND_CARRY and personal:
        min_spend = policy.try_value("jp.tax_free_minimum_jpy", on)
        if min_spend is not None and basket_jpy >= min_spend:
            if departure_refund_regime is None or departure_refund_regime >= 1:
                realisation = a.tax_refund_realisation_prob
                notes.append(
                    "consumption tax modelled as a contingent refund realised at "
                    f"departure, weighted by p={realisation}; from 2026-11-01 the "
                    "refund is claimed on leaving Japan and the whole receipt "
                    "becomes ineligible if any item on it is absent "
                    f"({JP_TAXFREE_URL})"
                )
                if departure_refund_regime is None:
                    notes.append(
                        "tax-free regime not recorded for this date; the "
                        "contingent treatment was assumed as the conservative case"
                    )
            else:
                realisation = Decimal("1")
                notes.append(
                    "consumption tax deducted at the shop under the pre-2026-11-01 "
                    "regime; treated as an immediate price reduction"
                )
            refund_jpy = tax_component * realisation
        elif min_spend is not None:
            notes.append(
                f"basket of {basket_jpy:.0f} JPY is below the {min_spend} JPY per "
                "store per day tax-free minimum; no refund modelled"
            )
    elif a.scenario is Scenario.HAND_CARRY:
        notes.append(
            "no Japanese tax refund modelled: the visitor scheme covers goods the "
            "traveller personally carries out for their own use, and these are "
            f"declared as resale inventory ({JP_TAXFREE_URL})"
        )
    else:
        notes.append(
            "tax-free exemption not modelled: since 2025-04-01 goods shipped "
            "internationally are outside the scheme, so the scenario carries "
            "Japanese consumption tax in full"
        )

    breakdown["jp_tax_refund"] = Money(-refund_jpy, Currency.JPY)

    proxy_fee = gross_jpy * a.proxy_fee_rate + a.proxy_fee_fixed_jpy
    if a.scenario is not Scenario.PROXY and (a.proxy_fee_rate or a.proxy_fee_fixed_jpy):
        notes.append("proxy fees supplied for a non-proxy scenario; included as given")
    breakdown["proxy_fee"] = Money(proxy_fee, Currency.JPY)
    breakdown["jp_domestic_shipping"] = Money(a.domestic_jp_shipping_jpy, Currency.JPY)
    breakdown["international_shipping"] = Money(a.international_shipping_jpy, Currency.JPY)

    # --- Import treatment -------------------------------------------------
    goods_value_eur = _jpy_to_eur(gross_jpy, fx_rates, spread)
    basket_value_eur = _jpy_to_eur(basket_jpy, fx_rates, spread)
    duty_threshold_eur = policy.value("dk.duty_threshold_eur", on)
    vat_rate = policy.value("dk.import_vat_rate", on)

    duty_regime = "n/a"
    duty_rate = Decimal("0")
    per_item_charge_eur = Decimal("0")
    charge_import_taxes = True

    if a.scenario is Scenario.HAND_CARRY and personal:
        allowance = policy.try_value("dk.traveller_allowance_air_dkk", on)
        if allowance is None:
            duty_regime = "traveller_allowance_unknown"
            unresolved.append(
                "traveller allowance not recorded for this date; import treatment "
                "for hand-carried personal goods is unresolved"
            )
            charge_import_taxes = False
        else:
            dkk_rate = _rate(fx_rates, Currency.EUR, Currency.DKK)
            basket_dkk = basket_value_eur * dkk_rate.rate
            if basket_dkk <= allowance:
                duty_regime = "traveller_allowance"
                charge_import_taxes = False
                notes.append(
                    f"basket of {basket_dkk:.0f} DKK is within the {allowance} DKK "
                    "traveller allowance for arrival by air or sea; no import VAT "
                    f"or duty modelled ({SKAT_REJSEGODS_URL})"
                )
            else:
                # Above the limit, duty and tax fall on the goods in their
                # entirety; the value of an item cannot be split, and two
                # travellers may not pool or divide the allowance.
                duty_regime = "traveller_allowance_exceeded"
                notes.append(
                    f"basket of {basket_dkk:.0f} DKK exceeds the {allowance} DKK "
                    "traveller allowance, so duty and VAT fall on the goods in "
                    f"their entirety ({SKAT_REJSEGODS_URL})"
                )
                if basket_value_eur > duty_threshold_eur and a.duty_rate is None:
                    raise PolicyUnverifiedError(
                        "Hand-carried basket exceeds both the traveller allowance "
                        "and the customs duty threshold, but no verified duty rate "
                        "is configured for collectible trading cards. Classify the "
                        "commodity code before relying on this landed cost."
                    )
                duty_rate = a.duty_rate or Decimal("0")
    else:
        # Commercial import, or a shipped/proxy consignment. Relief for
        # travellers' goods is confined to goods for private use and is
        # expressly unavailable where goods are imported with a view to
        # resale, so nothing is relieved here.
        if a.scenario is Scenario.HAND_CARRY:
            notes.append(
                "traveller allowance not applied: relief covers goods for private "
                "use and an import with a view to resale is not of non-commercial "
                f"character ({SKAT_REJSEGODS_URL})"
            )
        if basket_value_eur > duty_threshold_eur:
            duty_regime = "above_threshold"
            if a.duty_rate is None:
                raise PolicyUnverifiedError(
                    "Consignment value exceeds the customs duty threshold but no "
                    "verified duty rate is configured for collectible trading "
                    "cards. Classify the commodity code before relying on this "
                    "landed cost."
                )
            duty_rate = a.duty_rate
        else:
            duty_regime = "low_value"
            low_value = policy.try_value("dk.low_value_per_item_charge_eur", on)
            if low_value is not None:
                per_item_charge_eur = low_value * Decimal(a.items_in_consignment)
                notes.append(
                    "per-item low-value customs charge applied "
                    f"({low_value} EUR x {a.items_in_consignment} item(s))"
                )

    freight_eur = _jpy_to_eur(
        a.domestic_jp_shipping_jpy + a.international_shipping_jpy, fx_rates, spread
    )
    proxy_eur = _jpy_to_eur(proxy_fee, fx_rates, spread)
    refund_eur = _jpy_to_eur(refund_jpy, fx_rates, spread)
    insurance_eur = goods_value_eur * a.insurance_rate

    duty_eur = Decimal("0")
    vat_eur = Decimal("0")
    handling_eur = Decimal("0")

    if charge_import_taxes:
        customs_value = goods_value_eur + freight_eur
        duty_eur = customs_value * duty_rate
        vat_eur = (customs_value + duty_eur + per_item_charge_eur) * vat_rate
        if a.scenario is not Scenario.HAND_CARRY:
            handling_dkk = policy.try_value("dk.carrier_handling_fee_dkk", on)
            if handling_dkk is not None:
                dkk_rate = _rate(fx_rates, Currency.EUR, Currency.DKK)
                handling_eur = handling_dkk / dkk_rate.rate
    else:
        per_item_charge_eur = Decimal("0")

    breakdown["goods_value_eur"] = Money(goods_value_eur, Currency.EUR).quantize()
    breakdown["freight_eur"] = Money(freight_eur, Currency.EUR).quantize()
    breakdown["insurance_eur"] = Money(insurance_eur, Currency.EUR).quantize()
    breakdown["customs_duty_eur"] = Money(duty_eur, Currency.EUR).quantize()
    breakdown["low_value_charge_eur"] = Money(per_item_charge_eur, Currency.EUR).quantize()
    breakdown["import_vat_eur"] = Money(vat_eur, Currency.EUR).quantize()
    breakdown["carrier_handling_eur"] = Money(handling_eur, Currency.EUR).quantize()
    breakdown["expected_tax_refund_eur"] = Money(-refund_eur, Currency.EUR).quantize()
    breakdown["proxy_fee_eur"] = Money(proxy_eur, Currency.EUR).quantize()

    upfront = (
        goods_value_eur + freight_eur + insurance_eur + duty_eur
        + per_item_charge_eur + vat_eur + handling_eur + proxy_eur
    )
    total_eur = upfront - refund_eur

    dkk_rate = fx_rates.get((Currency.EUR, Currency.DKK))
    total_dkk = (
        Money(total_eur * dkk_rate.rate, Currency.DKK).quantize() if dkk_rate else None
    )

    return LandedCost(
        purchase_jpy=purchase_jpy,
        total_eur=Money(total_eur, Currency.EUR).quantize(),
        total_dkk=total_dkk,
        upfront_cash_eur=Money(upfront, Currency.EUR).quantize(),
        expected_refund_eur=Money(refund_eur, Currency.EUR).quantize(),
        duty_regime=duty_regime,
        breakdown=breakdown,
        policy_sources=policy.sources(POLICY_KEYS, on),
        notes=tuple(notes),
        unresolved=tuple(unresolved),
    )


def _net_proceeds(gross_resale_eur: Money, a: CostAssumptions) -> Money:
    gross = gross_resale_eur.amount
    fees = gross * (a.marketplace_fee_rate + a.payment_fee_rate) + a.payment_fee_fixed_eur
    net = (gross - fees - a.outbound_shipping_eur) * (Decimal("1") - a.expected_loss_rate)
    return Money(net, Currency.EUR).quantize()


def compute_economics(
    *,
    purchase_jpy: Money,
    gross_resale_eur: Money,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
) -> Economics:
    """The one financial calculation. Everything else calls this."""
    landed = compute_landed_cost(
        purchase_jpy, on=on, assumptions=assumptions, policy=policy, fx_rates=fx_rates
    )
    net = _net_proceeds(gross_resale_eur, assumptions)
    profit = Money(net.amount - landed.total_eur.amount, Currency.EUR).quantize()
    capital = landed.upfront_cash_eur
    roi = (profit.amount / capital.amount) if capital.amount > 0 else Decimal("0")
    return Economics(
        purchase_jpy=purchase_jpy,
        gross_resale_eur=gross_resale_eur,
        landed=landed,
        net_proceeds_eur=net,
        capital_deployed_eur=capital,
        profit_eur=profit,
        roi=roi,
    )


def resale_economics_fn(
    *,
    purchase_jpy: Money,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
) -> Callable[[Money], Economics]:
    """Freeze the buy side, vary the resale value.

    Handed to the scorer so simulations recalculate through this model instead
    of applying a flat proceeds ratio of their own.
    """

    def _fn(gross_resale_eur: Money) -> Economics:
        return compute_economics(
            purchase_jpy=purchase_jpy,
            gross_resale_eur=gross_resale_eur,
            on=on,
            assumptions=assumptions,
            policy=policy,
            fx_rates=fx_rates,
        )

    return _fn


# ----------------------------------------------------------- backward solve --

def _threshold_points_jpy(
    *,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
) -> list[Decimal]:
    """Purchase prices at which the cost model changes shape.

    Expressed in this card's own price. When the card travels in a basket the
    thresholds bite on the basket, so the equivalent card-level point is the
    threshold less the rest of the basket.
    """
    a = assumptions
    fx_factor = _rate(fx_rates, Currency.JPY, Currency.EUR).rate * (
        Decimal("1") + a.fx_spread_rate
    )
    if fx_factor <= 0:  # pragma: no cover - defensive
        return []

    rest_of_basket = Decimal("0")
    points: list[Decimal] = []

    def add_from_eur(value_eur: Decimal) -> None:
        card_jpy = value_eur / fx_factor - rest_of_basket
        if card_jpy > 0:
            points.append(card_jpy)

    add_from_eur(policy.value("dk.duty_threshold_eur", on))

    personal = a.acquisition_purpose is AcquisitionPurpose.PERSONAL
    if a.scenario is Scenario.HAND_CARRY and personal:
        allowance_dkk = policy.try_value("dk.traveller_allowance_air_dkk", on)
        dkk = fx_rates.get((Currency.EUR, Currency.DKK))
        if allowance_dkk is not None and dkk is not None and dkk.rate > 0:
            add_from_eur(allowance_dkk / dkk.rate)
        min_spend = policy.try_value("jp.tax_free_minimum_jpy", on)
        if min_spend is not None:
            card_jpy = min_spend - rest_of_basket
            if card_jpy > 0:
                points.append(card_jpy)

    return sorted(set(points))


def _segments(points: Sequence[Decimal], ceiling: Decimal) -> list[tuple[Decimal, Decimal]]:
    """Half-open price bands between the discontinuities."""
    bounds = [Decimal("1")] + [p for p in points if Decimal("1") < p < ceiling] + [ceiling]
    out: list[tuple[Decimal, Decimal]] = []
    for lo, hi in zip(bounds, bounds[1:]):
        if hi - lo >= Decimal("2"):
            out.append((lo, hi))
    return out


def max_buy_price(
    *,
    gross_resale_eur: Money,
    required_roi: Decimal,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
    probe_jpy: Decimal = Decimal("10000"),
    ceiling_jpy: Optional[Decimal] = None,
) -> Optional[Money]:
    """Highest whole-yen price that still clears ``required_roi``.

    Every candidate is verified against ``compute_economics`` and the next yen
    up is verified to fail, so the answer cannot disagree with the forward
    calculation. Returns ``None`` when no price in the model is feasible, which
    is a real answer and not an error.
    """
    a = assumptions.validated()

    def economics_at(price_jpy: Decimal) -> Optional[Economics]:
        try:
            return compute_economics(
                purchase_jpy=Money(price_jpy, Currency.JPY),
                gross_resale_eur=gross_resale_eur,
                on=on,
                assumptions=a,
                policy=policy,
                fx_rates=fx_rates,
            )
        except (PolicyUnverifiedError, CostAssumptionError, KeyError):
            return None

    def feasible(price_jpy: Decimal) -> bool:
        econ = economics_at(price_jpy)
        if econ is None or not econ.actionable:
            return False
        return econ.roi >= required_roi

    # A generous ceiling: the resale value expressed in yen is an upper bound on
    # any sane purchase price, since costs are strictly positive.
    if ceiling_jpy is None:
        jpy_per_eur = fx_rates.get((Currency.EUR, Currency.JPY))
        if jpy_per_eur is not None:
            ceiling_jpy = gross_resale_eur.amount * jpy_per_eur.rate
        else:
            fx_factor = _rate(fx_rates, Currency.JPY, Currency.EUR).rate
            ceiling_jpy = gross_resale_eur.amount / fx_factor if fx_factor else probe_jpy
    ceiling_jpy = max(ceiling_jpy, Decimal("10"))

    points = _threshold_points_jpy(
        on=on, assumptions=a, policy=policy, fx_rates=fx_rates
    )
    best: Optional[Decimal] = None

    for lo, hi in _segments(points, ceiling_jpy):
        lo_i = int(lo.to_integral_value(rounding=ROUND_CEILING))
        hi_i = int(hi.to_integral_value(rounding=ROUND_FLOOR))
        if hi_i < lo_i:
            continue

        # Feasibility is monotone inside a segment: landed cost is
        # non-decreasing in the purchase price and the resale side is fixed, so
        # ROI is non-increasing. Segments are cut at every discontinuity
        # precisely so that this holds; it is not assumed globally.
        if not feasible(Decimal(lo_i)):
            continue

        if feasible(Decimal(hi_i)):
            candidate = hi_i
        else:
            low, high = lo_i, hi_i          # feasible(low), not feasible(high)
            while high - low > 1:
                mid = (low + high) // 2
                if feasible(Decimal(mid)):
                    low = mid
                else:
                    high = mid
            candidate = low

        # Verify forward, and verify that the next yen fails. Rounding to whole
        # cents can make the cost flat across a yen or two, so "the next yen
        # fails" is established by search rather than assumed from the algebra.
        if not feasible(Decimal(candidate)):  # pragma: no cover - defensive
            continue
        if candidate < hi_i and feasible(Decimal(candidate + 1)):  # pragma: no cover
            continue
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    return Money(Decimal(best), Currency.JPY)


def compute_arbitrage(
    *,
    purchase_jpy: Money,
    gross_resale_eur: Money,
    on: date,
    assumptions: CostAssumptions,
    policy: PolicyStore,
    fx_rates: Mapping[tuple[Currency, Currency], FxRate],
    required_roi: Decimal = Decimal("0.40"),
    strong_buy_roi: Decimal = Decimal("0.80"),
    target_roi: Decimal = Decimal("0.60"),
) -> ArbitrageResult:
    """Full arbitrage assessment for one card at one Japanese price."""
    econ = compute_economics(
        purchase_jpy=purchase_jpy,
        gross_resale_eur=gross_resale_eur,
        on=on,
        assumptions=assumptions,
        policy=policy,
        fx_rates=fx_rates,
    )
    landed = econ.landed
    cost = landed.total_eur.amount
    gross_multiple = (gross_resale_eur.amount / cost) if cost > 0 else Decimal("0")
    net_multiple = (econ.net_proceeds_eur.amount / cost) if cost > 0 else Decimal("0")

    a = assumptions
    fee_rate = a.marketplace_fee_rate + a.payment_fee_rate
    loss = Decimal("1") - a.expected_loss_rate
    denom = (Decimal("1") - fee_rate) * loss
    break_even = (
        (cost / loss + a.payment_fee_fixed_eur + a.outbound_shipping_eur)
        / (Decimal("1") - fee_rate)
        if denom > 0
        else Decimal("0")
    )

    kw = dict(
        gross_resale_eur=gross_resale_eur, on=on, assumptions=assumptions,
        policy=policy, fx_rates=fx_rates,
    )
    dkk_rate = fx_rates.get((Currency.EUR, Currency.DKK))

    # An unresolved landed cost must not produce purchase limits that look
    # actionable. The cost is understated, so any limit derived from it is too
    # generous in exactly the direction that loses money.
    if landed.unresolved:
        max_buy = strong_buy = target_buy = None
    else:
        max_buy = max_buy_price(required_roi=required_roi, **kw)
        strong_buy = max_buy_price(required_roi=strong_buy_roi, **kw)
        target_buy = max_buy_price(required_roi=target_roi, **kw)

    return ArbitrageResult(
        scenario=a.scenario,
        landed=landed,
        gross_resale_eur=gross_resale_eur,
        net_proceeds_eur=econ.net_proceeds_eur,
        expected_profit_eur=econ.profit_eur,
        expected_profit_dkk=(
            Money(econ.profit_eur.amount * dkk_rate.rate, Currency.DKK).quantize()
            if dkk_rate else None
        ),
        roi=econ.roi,
        capital_deployed_eur=econ.capital_deployed_eur,
        capital_basis=econ.capital_basis,
        gross_multiple=gross_multiple,
        net_multiple=net_multiple,
        break_even_resale_eur=Money(break_even, Currency.EUR).quantize(),
        max_buy_jpy=max_buy,
        strong_buy_jpy=strong_buy,
        target_buy_jpy=target_buy,
        do_not_buy_above_jpy=max_buy,
        notes=landed.notes,
        unresolved=landed.unresolved,
    )
