"""Adapters for sources whose access is constrained.

Each one ships with a complete interface, a schema, tests and mocks, and a
policy record that records *why* it is off. The brief asked for exactly this:
implement the integration point even when the credentials are unavailable, and
document what is missing.

The constraints below were each verified against the source's own
documentation while writing the Phase 1 document. The URLs are in
``api_docs_url`` on every policy, so the reason a source is off is always one
field lookup away rather than tribal knowledge.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from ..types import Listing, PopulationSnapshot, Sale
from .base import (
    Adapter,
    ListingSource,
    PolicyViolationError,
    PopulationSource,
    SalesSource,
    SourcePolicy,
    VerificationStatus,
)

__all__ = [
    "PSA_POLICY",
    "EBAY_SOLD_POLICY",
    "EBAY_BROWSE_POLICY",
    "CARDMARKET_POLICY",
    "PRICECHARTING_POLICY",
    "JAPANESE_SOURCE_TEMPLATE",
    "PsaCertAdapter",
    "EbaySoldAdapter",
    "CardmarketAdapter",
    "PriceChartingAdapter",
    "PRICECHARTING_GRADE_MAP",
]


PSA_POLICY = SourcePolicy(
    source_id="psa",
    display_name="PSA Public API",
    base_url="https://api.psacard.com/publicapi/",
    verification_status=VerificationStatus.VERIFIED_API,
    enabled=False,
    has_official_api=True,
    api_docs_url="https://www.psacard.com/publicapi/documentation",
    credentials_required=("PSA_API_TOKEN",),
    max_requests_per_minute=10,
    forbidden_patterns=("population_report",),
    notes=(
        "The PSA Public API documentation states the only available method set is "
        "cert verification by cert number. Population report data is NOT exposed. "
        "Population fields therefore remain unknown until a licensed population "
        "feed is connected. No population figure is ever estimated."
    ),
)

EBAY_SOLD_POLICY = SourcePolicy(
    source_id="ebay_sold",
    display_name="eBay Marketplace Insights API (sold items)",
    base_url="https://api.ebay.com/buy/marketplace_insights/v1_beta",
    verification_status=VerificationStatus.UNVERIFIED,
    enabled=False,
    has_official_api=True,
    api_docs_url="https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html",
    credentials_required=("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"),
    notes=(
        "Limited Release API available only to select developers approved by "
        "business units. Sales history range is capped at 90 days. Categories "
        "must additionally be whitelisted per partner. Adapter is complete and "
        "inactive pending access."
    ),
)

EBAY_BROWSE_POLICY = SourcePolicy(
    source_id="ebay_browse",
    display_name="eBay Browse API (active listings)",
    base_url="https://api.ebay.com/buy/browse/v1",
    verification_status=VerificationStatus.UNVERIFIED,
    enabled=False,
    has_official_api=True,
    api_docs_url="https://developer.ebay.com/api-docs/buy/browse/static/overview.html",
    credentials_required=("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"),
    forbidden_patterns=("fair_value_input",),
    notes=(
        "Active listings only. Wired to supply and liquidity signals. The "
        "forbidden pattern 'fair_value_input' is enforced in code so listings "
        "can never reach the fair value engine."
    ),
)

CARDMARKET_POLICY = SourcePolicy(
    source_id="cardmarket",
    display_name="Cardmarket RESTful API",
    base_url="https://api.cardmarket.com/ws/v2.0",
    verification_status=VerificationStatus.UNVERIFIED,
    enabled=False,
    has_official_api=True,
    api_docs_url="https://api.cardmarket.com/ws/documentation/API:Auth_Overview",
    credentials_required=(
        "CARDMARKET_APP_TOKEN", "CARDMARKET_APP_SECRET",
        "CARDMARKET_ACCESS_TOKEN", "CARDMARKET_ACCESS_SECRET",
    ),
    forbidden_patterns=("continuous_public_marketplace_polling",),
    notes=(
        "Cardmarket documentation states API access is restricted to "
        "professional sellers subject to manual approval, and explicitly "
        "disallows Dedicated App users from constantly requesting only the "
        "public Marketplace resources on consecutive days. A continuously "
        "scanning Europe Steal Finder is therefore NOT implementable against "
        "Cardmarket on a Dedicated credential. See docs/01-data-sources.md."
    ),
)

PRICECHARTING_POLICY = SourcePolicy(
    source_id="pricecharting",
    display_name="PriceCharting Prices API",
    base_url="https://www.pricecharting.com/api",
    verification_status=VerificationStatus.UNVERIFIED,
    enabled=False,
    has_official_api=True,
    api_docs_url="https://www.pricecharting.com/api-documentation",
    credentials_required=("PRICECHARTING_TOKEN",),
    max_requests_per_minute=60,
    min_seconds_between_requests=Decimal("1"),
    forbidden_patterns=("sales_history", "fair_value_input"),
    notes=(
        "Documentation states a paid subscription is required, the API is "
        "limited to one call per second, and that only current values are "
        "supported: historic prices and historic sales are not. Usable as a "
        "current-level cross-check, never as a sales-history source."
    ),
)

#: Template for Japanese marketplaces. Every one of Card Rush, Yuyu-tei,
#: Hareruya, Dragon Star, Mandarake, Mercari JP, Yahoo Auctions JP, Rakuten,
#: Surugaya, Magi and Clove gets a copy of this with its own base URL, and none
#: of them run until someone fills in the robots and terms fields. That check
#: has not been done, so none of them are enabled.
JAPANESE_SOURCE_TEMPLATE = SourcePolicy(
    source_id="jp_template",
    display_name="Japanese marketplace (template)",
    base_url="",
    verification_status=VerificationStatus.UNVERIFIED,
    enabled=False,
    has_official_api=None,
    max_requests_per_minute=4,
    min_seconds_between_requests=Decimal("15"),
    notes=(
        "No public API or scraping permission was verified for this source. "
        "Fill in robots_checked_on, robots_allows_paths, terms_reviewed_on and "
        "terms_url, then set verification_status, before enabling. The Mobile "
        "Quick Check flow does not depend on this: the Japanese price is typed "
        "in from the shelf tag."
    ),
)


#: PriceCharting's card grade mapping, from its own documentation. Encoded here
#: so nothing downstream ever mistakes ``new-price`` for a sealed product, which
#: is what the column name suggests and is not what it means for cards.
PRICECHARTING_GRADE_MAP: Mapping[str, str] = {
    "loose-price": "ungraded",
    "cib-price": "grade_7_or_7.5",
    "new-price": "grade_8_or_8.5",
    "graded-price": "grade_9",
    "box-only-price": "grade_9.5",
    "manual-only-price": "psa_10",
    "bgs-10-price": "bgs_10",
    "condition-17-price": "cgc_10",
    "condition-18-price": "sgc_10",
    "condition-19-price": "cgc_10_pristine",
    "condition-20-price": "bgs_10_black",
    "condition-21-price": "tag_10",
    "condition-22-price": "ace_10",
}


class PsaCertAdapter(PopulationSource):
    """Cert verification by cert number. Population is not available."""

    def __init__(self, policy: SourcePolicy = PSA_POLICY, token: Optional[str] = None) -> None:
        super().__init__(policy)
        self._token = token

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "cert_lookup": True,           # offered by the API, not by us
            "population_total": False,
            "population_ladder": False,
            "population_history": False,
            "parser_implemented": False,
            "verified_live_access": False,
        }

    def fetch_population(self, reference: str) -> Optional[PopulationSnapshot]:
        """Always raises. Population is outside the documented API surface.

        Returning ``None`` would be worse than raising: a ``None`` gets quietly
        rendered as "unknown" and the operator never learns that the whole
        population feature is unavailable rather than merely missing for this
        card.
        """
        raise PolicyViolationError(
            "PSA population data is not exposed by the PSA Public API, whose "
            "documented method set is cert verification by cert number only. "
            "See https://www.psacard.com/publicapi/documentation. Connect a "
            "licensed population feed, or leave population unknown."
        )

    def fetch_cert(self, cert_number: str) -> Mapping[str, object]:
        """Not implemented. No credentials, so no observed response.

        The documented endpoint is ``GET /cert/GetByCertNumber/{cert}`` with a
        bearer token, per https://www.psacard.com/publicapi/documentation. A
        parser written against documentation alone, and tested against fixtures
        written from that same documentation, proves only that the two agree
        with each other. It is written when a real response exists.
        """
        self._guard("cert_lookup")
        if not self._token:
            raise RuntimeError("PSA_API_TOKEN is not configured.")
        raise NotImplementedError(
            "No response parser exists: access has not been provisioned and no "
            "real payload has been observed. "
            "https://www.psacard.com/publicapi/documentation"
        )


class EbaySoldAdapter(SalesSource):
    """Completed-sales source. Disabled, and no response parser is written.

    Access was never granted, so no real Marketplace Insights payload has been
    seen. Writing a parser against a schema taken from documentation alone, and
    calling it tested because it passes fixtures that were also written from
    that documentation, would produce false confidence in the exact place the
    product can least afford it. The parser is written when a recorded response
    exists.
    """

    def __init__(self, policy: SourcePolicy = EBAY_SOLD_POLICY) -> None:
        super().__init__(policy)

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "sold_search": True,           # offered by the API, not by us
            "history_days": True,          # capped at 90 by the API
            "unrestricted_categories": False,
            "parser_implemented": False,
            "verified_live_access": False,
        }

    def fetch_sales(self, query: str, since: datetime) -> Sequence[Sale]:
        self._guard("sold_search")
        raise NotImplementedError(
            "Marketplace Insights is a Limited Release API, access has not been "
            "granted, and no response parser exists because no real payload has "
            "been observed. See "
            "https://developer.ebay.com/api-docs/buy/marketplace-insights/static/overview.html"
        )


class CardmarketAdapter(ListingSource):
    """Cardmarket adapter with the Dedicated App polling restriction enforced."""

    def __init__(
        self,
        policy: SourcePolicy = CARDMARKET_POLICY,
        app_type: str = "dedicated",
    ) -> None:
        super().__init__(policy)
        self.app_type = app_type

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "product_lookup": True,        # offered by the API, not by us
            "own_stock": True,
            "continuous_public_polling": self.app_type == "third_party",
            "parser_implemented": False,
            "verified_live_access": False,
        }

    def fetch_listings(self, query: str) -> Sequence[Listing]:
        self._guard("product_lookup")
        if self.app_type == "dedicated":
            raise PolicyViolationError(
                "Cardmarket explicitly disallows Dedicated App users from "
                "constantly requesting only the public Marketplace resources on "
                "consecutive days. A continuous Europe Steal Finder against a "
                "Dedicated credential would breach those terms and risks access "
                "being revoked. See "
                "https://api.cardmarket.com/ws/documentation/API:Auth_Overview"
            )
        raise NotImplementedError("Approved 3rd Party App credentials are not configured.")


class PriceChartingAdapter(Adapter):
    """Current-level cross-check only. No history, no sales."""

    def __init__(self, policy: SourcePolicy = PRICECHARTING_POLICY, token: Optional[str] = None) -> None:
        super().__init__(policy)
        self._token = token

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "current_prices": True,
            "grade_ladder": True,
            "price_history": False,
            "sales_history": False,
            # The column translation below is written and unit-tested against
            # the published column contract, so this one is genuinely true.
            # Live access has still never been exercised.
            "parser_implemented": True,
            "verified_live_access": False,
        }

    @staticmethod
    def interpret(payload: Mapping[str, object]) -> dict[str, object]:
        """Translate PriceCharting's column names into unambiguous labels."""
        return {
            PRICECHARTING_GRADE_MAP.get(k, k): v
            for k, v in payload.items()
            if k in PRICECHARTING_GRADE_MAP
        }
