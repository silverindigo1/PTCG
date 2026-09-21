"""Shared fixtures.

The fixture data is deliberately built around the confusable pairs from the
brief: 114/SM-P versus 144/S-P versus 147/S-P, holo versus reverse holo, 1st
Edition versus Unlimited, Pokemon Center versus standard. Those are the cases
where a matcher that "usually works" loses money.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from pokearb_core.arbitrage.policy import PolicyParameter, PolicyStore  # noqa: E402
from pokearb_core.condition.model import DirichletPrior  # noqa: E402
from pokearb_core.identity.matcher import AliasIndex  # noqa: E402
from pokearb_core.types import (  # noqa: E402
    CardVariant,
    Currency,
    Edition,
    EuCondition,
    FxRate,
    Language,
    Money,
    Printing,
    Sale,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
SKAT_URL = "https://skat.dk/skole/onlineshopping/naar-du-koeber"
JG_URL = "https://www.japan-guide.com/news/tax-free-shopping.html"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def eur(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


def make_sale(
    sale_id: str,
    amount: Decimal,
    days_ago: int,
    *,
    variant_id: str = "v1",
    condition: EuCondition = EuCondition.NM,
    source_id: str = "ebay_sold",
    market: str = "EU",
) -> Sale:
    """A completed European sale.

    ``market`` and ``external_id`` are populated because the engines scope on
    them. A fixture that leaves them blank tests a shape the database never
    produces.
    """
    return Sale(
        sale_id=sale_id,
        variant_id=variant_id,
        source_id=source_id,
        sold_at=NOW - timedelta(days=days_ago),
        price=Money(amount, Currency.EUR),
        condition=condition,
        language=Language.JA,
        source_url=f"https://example.invalid/sale/{sale_id}",
        market=market,
        external_id=sale_id,
    )


@pytest.fixture
def universe() -> list[CardVariant]:
    pikachu_holo = CardVariant(
        variant_id="v-pikachu-114-smp-holo",
        language=Language.JA, set_code="SM-P", number="114",
        printing=Printing.HOLO, name_en="Pikachu", name_ja="ピカチュウ",
        pokemon_slug="pikachu", series="SM-P", year=2018,
        sibling_variant_ids=("v-pikachu-114-smp-reverse",),
    )
    pikachu_reverse = CardVariant(
        variant_id="v-pikachu-114-smp-reverse",
        language=Language.JA, set_code="SM-P", number="114",
        printing=Printing.REVERSE_HOLO, name_en="Pikachu", name_ja="ピカチュウ",
        pokemon_slug="pikachu", series="SM-P", year=2018,
        sibling_variant_ids=("v-pikachu-114-smp-holo",),
    )
    pikachu_144 = CardVariant(
        variant_id="v-pikachu-144-sp-holo",
        language=Language.JA, set_code="S-P", number="144",
        printing=Printing.HOLO, name_en="Pikachu", name_ja="ピカチュウ",
        pokemon_slug="pikachu", series="S-P", year=2021,
    )
    pikachu_147 = CardVariant(
        variant_id="v-pikachu-147-sp-holo",
        language=Language.JA, set_code="S-P", number="147",
        printing=Printing.HOLO, name_en="Pikachu", name_ja="ピカチュウ",
        pokemon_slug="pikachu", series="S-P", year=2021,
    )
    charizard_unlimited = CardVariant(
        variant_id="v-charizard-4-base-unlimited",
        language=Language.EN, set_code="BASE", number="4",
        printing=Printing.HOLO, edition=Edition.UNLIMITED,
        name_en="Charizard", pokemon_slug="charizard", year=1999,
    )
    charizard_first = CardVariant(
        variant_id="v-charizard-4-base-1st",
        language=Language.EN, set_code="BASE", number="4",
        printing=Printing.HOLO, edition=Edition.FIRST,
        name_en="Charizard", pokemon_slug="charizard", year=1999,
    )
    etb_standard = CardVariant(
        variant_id="v-etb-standard",
        language=Language.EN, set_code="ETB-X", number="1",
        printing=Printing.NON_HOLO, name_en="Elite Trainer Box",
    )
    etb_pokemon_center = CardVariant(
        variant_id="v-etb-pokemon-center",
        language=Language.EN, set_code="ETB-X", number="1",
        printing=Printing.NON_HOLO, stamp="pokemon-center",
        name_en="Elite Trainer Box",
    )
    return [
        pikachu_holo, pikachu_reverse, pikachu_144, pikachu_147,
        charizard_unlimited, charizard_first, etb_standard, etb_pokemon_center,
    ]


@pytest.fixture
def aliases() -> AliasIndex:
    return AliasIndex(
        {
            "pikachu": ["ピカチュウ", "Pikachu", "ピカチュー"],
            "umbreon": ["ブラッキー", "Blacky", "Umbreon"],
            "charizard": ["リザードン", "Glurak", "Dracaufeu"],
        }
    )


@pytest.fixture
def condition_prior_a_minus() -> DirichletPrior:
    """Weakly informative starting prior for a Japanese shop 'A minus'.

    These numbers are a documented starting point, not a measurement. They are
    flagged provisional so that condition confidence stays low until the
    calibration job has seen real outcomes.
    """
    return DirichletPrior(
        source_id="jp_shop_generic",
        source_grade_label="A-",
        alpha={
            EuCondition.NM: Decimal("1.30"),
            EuCondition.EX: Decimal("1.00"),
            EuCondition.GD: Decimal("0.40"),
            EuCondition.LP: Decimal("0.20"),
            EuCondition.PL: Decimal("0.07"),
            EuCondition.PO: Decimal("0.03"),
        },
        evidence_n=0,
        is_provisional=True,
        note="Starting prior only. Replace with calibrated values per shop.",
    )


@pytest.fixture
def condition_prior_b() -> DirichletPrior:
    return DirichletPrior(
        source_id="jp_shop_generic",
        source_grade_label="B",
        alpha={
            EuCondition.NM: Decimal("0.25"),
            EuCondition.EX: Decimal("0.90"),
            EuCondition.GD: Decimal("1.00"),
            EuCondition.LP: Decimal("0.60"),
            EuCondition.PL: Decimal("0.20"),
            EuCondition.PO: Decimal("0.05"),
        },
        evidence_n=0,
        is_provisional=True,
        note="Starting prior only.",
    )


@pytest.fixture
def policy_store() -> PolicyStore:
    """Dated policy rows. Every value carries the URL it was verified from."""
    return PolicyStore(
        [
            PolicyParameter(
                "dk.import_vat_rate", Decimal("0.25"), "rate",
                date(2021, 7, 1), None, SKAT_URL,
                note="Danish VAT on goods from outside the EU",
            ),
            PolicyParameter(
                "dk.duty_threshold_eur", Decimal("150"), "EUR",
                date(2021, 7, 1), None, SKAT_URL,
                note="Duty applies above EUR 150 per order, shipping excluded",
            ),
            PolicyParameter(
                "dk.low_value_per_item_charge_eur", Decimal("3"), "EUR",
                date(2026, 7, 1), None, SKAT_URL,
                note="Per-item charge on consignments of EUR 150 or less",
            ),
            PolicyParameter(
                "dk.carrier_handling_fee_dkk", Decimal("200"), "DKK",
                date(2021, 7, 1), None, SKAT_URL,
                note="Carriers typically charge DKK 150-250; midpoint used",
            ),
            PolicyParameter(
                "dk.traveller_allowance_air_dkk", Decimal("3250"), "DKK",
                date(2021, 7, 1), None, SKAT_URL,
                note="Allowance arriving by air or sea from outside the EU",
            ),
            PolicyParameter(
                "jp.consumption_tax_rate", Decimal("0.10"), "rate",
                date(2019, 10, 1), None, JG_URL,
                note="General goods rate",
            ),
            PolicyParameter(
                "jp.tax_free_minimum_jpy", Decimal("5000"), "JPY",
                date(2019, 10, 1), None, JG_URL,
                note="Per store per day, before tax",
            ),
            PolicyParameter(
                "jp.tax_free_refund_at_departure_from", Decimal("1"), "count",
                date(2026, 11, 1), None, JG_URL,
                note="From 2026-11-01 the refund is claimed on leaving Japan",
            ),
            PolicyParameter(
                "jp.tax_free_refund_at_departure_from", Decimal("0"), "count",
                date(2019, 10, 1), date(2026, 10, 31), JG_URL,
                note="Instant exemption at the till under the old regime",
            ),
            # Deliberately unverified: no tariff classification was confirmed.
            PolicyParameter(
                "dk.duty_rate_trading_cards", Decimal("0"), "rate",
                date(2021, 7, 1), None, None,
                requires_verification=True,
                note="Commodity code for collectible trading cards not classified",
            ),
        ]
    )


@pytest.fixture
def fx_map() -> dict[tuple[Currency, Currency], FxRate]:
    """ECB reference rates for 2026-09-18 as published.

    JPY and DKK against EUR come straight from the feed; the inverse pair is
    derived, matching what ``EcbFxAdapter`` produces.
    """
    as_of = date(2026, 9, 18)
    eur_jpy = Decimal("180.94")
    eur_dkk = Decimal("7.4754")
    return {
        (Currency.EUR, Currency.JPY): FxRate(as_of, Currency.EUR, Currency.JPY, eur_jpy, ECB_URL),
        (Currency.JPY, Currency.EUR): FxRate(
            as_of, Currency.JPY, Currency.EUR,
            (Decimal("1") / eur_jpy).quantize(Decimal("0.00000001")), ECB_URL,
        ),
        (Currency.EUR, Currency.DKK): FxRate(as_of, Currency.EUR, Currency.DKK, eur_dkk, ECB_URL),
        (Currency.DKK, Currency.EUR): FxRate(
            as_of, Currency.DKK, Currency.EUR,
            (Decimal("1") / eur_dkk).quantize(Decimal("0.00000001")), ECB_URL,
        ),
    }


@pytest.fixture
def liquid_sales() -> list[Sale]:
    """A card that actually trades: 12 sales across 90 days."""
    return [
        make_sale(f"liq-{i}", Decimal("170") + Decimal(i), 3 + i * 7)
        for i in range(12)
    ]


@pytest.fixture
def thin_sales() -> list[Sale]:
    """A card with a theoretical spread and no demonstrated exit."""
    return [
        make_sale("thin-1", Decimal("250"), 200),
        make_sale("thin-2", Decimal("260"), 330),
    ]
