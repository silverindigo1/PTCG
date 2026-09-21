"""The seeded policy parameters, as Python, for tools that run without a database.

``seed/policy.sql`` is the production source of truth. This module mirrors it
so a command-line price check can run with nothing but network access. A test
(``tests/test_market_average.py::test_reference_policy_matches_the_seed``)
parses the SQL and fails if the two ever drift apart, so this copy cannot
quietly go stale.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .policy import PolicyParameter, PolicyStore

SKAT = "https://skat.dk/skole/onlineshopping/naar-du-koeber"
JG = "https://www.japan-guide.com/news/tax-free-shopping.html"

ROWS: tuple[PolicyParameter, ...] = (
    PolicyParameter("dk.import_vat_rate", Decimal("0.25"), "rate", date(2021, 7, 1), None, SKAT),
    PolicyParameter("dk.duty_threshold_eur", Decimal("150"), "EUR", date(2021, 7, 1), None, SKAT),
    PolicyParameter("dk.low_value_per_item_charge_eur", Decimal("3"), "EUR", date(2026, 7, 1), None, SKAT),
    PolicyParameter("dk.carrier_handling_fee_dkk", Decimal("200"), "DKK", date(2021, 7, 1), None, SKAT),
    PolicyParameter("dk.traveller_allowance_air_dkk", Decimal("3250"), "DKK", date(2021, 7, 1), None, SKAT),
    PolicyParameter("dk.traveller_allowance_other_dkk", Decimal("2230"), "DKK", date(2021, 7, 1), None, SKAT),
    PolicyParameter("jp.consumption_tax_rate", Decimal("0.10"), "rate", date(2019, 10, 1), None, JG),
    PolicyParameter("jp.tax_free_minimum_jpy", Decimal("5000"), "JPY", date(2019, 10, 1), None, JG),
    PolicyParameter("jp.tax_free_refund_at_departure_from", Decimal("0"), "count",
                    date(2019, 10, 1), date(2026, 10, 31), JG),
    PolicyParameter("jp.tax_free_refund_at_departure_from", Decimal("1"), "count",
                    date(2026, 11, 1), None, JG),
    PolicyParameter("jp.tax_free_purchase_window_days", Decimal("90"), "count",
                    date(2026, 11, 1), None, JG),
    PolicyParameter("dk.duty_rate_trading_cards", Decimal("0"), "rate", date(2021, 7, 1), None,
                    None, requires_verification=True,
                    note="Commodity code for collectible trading cards not classified"),
)


def reference_policy() -> PolicyStore:
    return PolicyStore(list(ROWS))
