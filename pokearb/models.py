"""Core data structures shared by the scrapers, the classifier and the renderer."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any


class ProductType(str, Enum):
    ETB = "etb"
    ETB_POKEMON_CENTER = "etb_pc"
    BOOSTER_BOX = "booster_box"
    BOOSTER_BUNDLE = "booster_bundle"
    UPC = "upc"

    @property
    def label_da(self) -> str:
        return {
            "etb": "Elite Trainer Box",
            "etb_pc": "Pokémon Center ETB",
            "booster_box": "Booster box / display",
            "booster_bundle": "Booster bundle",
            "upc": "Ultra Premium Collection",
        }[self.value]


class Language(str, Enum):
    EN = "en"
    JA = "ja"
    DE = "de"
    ZH = "zh"
    KO = "ko"
    FR = "fr"
    ES = "es"
    IT = "it"

    @property
    def label_da(self) -> str:
        return {
            "en": "Engelsk",
            "ja": "Japansk",
            "de": "Tysk",
            "zh": "Kinesisk",
            "ko": "Koreansk",
            "fr": "Fransk",
            "es": "Spansk",
            "it": "Italiensk",
        }[self.value]


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "ukendt"


# Pre-order wording across the shops' languages: EN, DA, DE, NL, ES, IT, FR, SV.
PRE_ORDER_TITLE = re.compile(
    r"(?<![a-z])(?:pre[\s-]?orders?|forudbestil\w*|vorbestell\w*|vorverkauf|voorverkoop|"
    r"pre[\s-]?venta|preordine|pr[eé][\s-]?commande|f[öo]rhandsbok\w*|f[öo]rbest[äa]ll\w*)(?![a-z])"
)


@dataclass
class Offer:
    """One purchasable listing at one shop at one point in time."""

    shop_key: str
    shop_name: str
    country: str
    currency: str
    amount: float
    in_stock: bool
    title: str
    url: str
    captured_at: str  # ISO-8601 date, the day the price was actually read
    stale: bool = False  # True when reused from last_good after a failed run
    amount_dkk: float | None = None
    # Delivery to Denmark, in DKK, after any free-shipping threshold.
    shipping_dkk: float | None = None
    # False when the shop publishes no rate and shipping_dkk is our assumption.
    shipping_verified: bool = False

    @property
    def pre_order(self) -> bool:
        """True when the shop's title says the item has not shipped yet.

        Shopify reports a pre-order as available, so it counts as in stock.
        The site says so next to the price, because paying now for a box that
        arrives in a month is not the same purchase.
        """
        return bool(PRE_ORDER_TITLE.search(self.title.lower()))

    @property
    def total_dkk(self) -> float | None:
        """Landed cost: what actually leaves your account."""
        if self.amount_dkk is None:
            return None
        return round(self.amount_dkk + (self.shipping_dkk or 0.0), 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Offer":
        return cls(**raw)


@dataclass
class CanonicalProduct:
    """A set + product type + language, with every shop offer attached."""

    set_id: str | None          # TCGdex set id, None when the set did not match
    set_name: str               # canonical name, or the raw string when unmatched
    set_release_date: str | None
    set_matched: bool
    product_type: ProductType
    language: Language
    offers: list[Offer] = field(default_factory=list)

    @property
    def key(self) -> str:
        base = f"{self.set_id or 'raw:' + slugify(self.set_name)}|{self.product_type.value}|{self.language.value}"
        # A short stable hash keeps file names and URLs bounded for long raw names.
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        return f"{slugify(self.set_name)[:48]}-{self.product_type.value}-{self.language.value}-{digest}"

    @property
    def title_da(self) -> str:
        return f"{self.set_name} {self.product_type.label_da} ({self.language.label_da})"

    @property
    def in_stock_offers(self) -> list[Offer]:
        return [o for o in self.offers if o.in_stock]

    @property
    def cheapest(self) -> Offer | None:
        """Cheapest landed cost, item price plus delivery to Denmark.

        Ranking on the item price alone points at the wrong shop whenever the
        parcel costs more than the price gap, which across a border it usually
        does.
        """
        candidates = [o for o in self.in_stock_offers if o.total_dkk is not None]
        if not candidates:
            candidates = [o for o in self.offers if o.total_dkk is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda o: o.total_dkk)

    @property
    def cheapest_item_only(self) -> Offer | None:
        """Cheapest before delivery. Shown when it is a different shop."""
        candidates = [o for o in self.in_stock_offers if o.amount_dkk is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda o: o.amount_dkk)

    @property
    def shipping_changes_the_answer(self) -> bool:
        lo, item = self.cheapest, self.cheapest_item_only
        return lo is not None and item is not None and lo.shop_key != item.shop_key

    @property
    def dearest_in_stock(self) -> Offer | None:
        candidates = [o for o in self.in_stock_offers if o.total_dkk is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda o: o.total_dkk)

    @property
    def spread_dkk(self) -> float | None:
        """Saving between the cheapest and dearest in-stock landed cost."""
        lo, hi = self.cheapest, self.dearest_in_stock
        if lo is None or hi is None or lo is hi:
            return None
        if lo.total_dkk is None or hi.total_dkk is None:
            return None
        return round(hi.total_dkk - lo.total_dkk, 2)

    @property
    def spread_pct(self) -> float | None:
        hi = self.dearest_in_stock
        spread = self.spread_dkk
        if spread is None or hi is None or not hi.total_dkk:
            return None
        return round(100.0 * spread / hi.total_dkk, 1)

    @property
    def shop_count(self) -> int:
        return len({o.shop_key for o in self.offers})

    # -- market view ---------------------------------------------------------
    #
    # The market price is the median landed cost across shops that have the
    # box in stock. A median, not a mean, so that one shop asking double does
    # not make everyone else look cheap. It needs three shops before it means
    # anything; below that there is no market price, only two quotes.

    MIN_SHOPS_FOR_MARKET = 3

    def _in_stock_totals(self) -> list[float]:
        return sorted(o.total_dkk for o in self.in_stock_offers if o.total_dkk is not None)

    @property
    def market_price(self) -> float | None:
        totals = self._in_stock_totals()
        if len(totals) < self.MIN_SHOPS_FOR_MARKET:
            return None
        mid = len(totals) // 2
        median = totals[mid] if len(totals) % 2 else (totals[mid - 1] + totals[mid]) / 2
        return round(median, 2)

    @property
    def discount_pct(self) -> float | None:
        """How far the cheapest in-stock landed cost sits below the market price."""
        market, lo = self.market_price, self.cheapest
        if market is None or lo is None or not lo.in_stock or lo.total_dkk is None:
            return None
        return round(100.0 * (market - lo.total_dkk) / market, 1)

    @property
    def price_range(self) -> tuple[float, float] | None:
        totals = self._in_stock_totals()
        if len(totals) < 2:
            return None
        return totals[0], totals[-1]

    def sorted_offers(self) -> list[Offer]:
        """In-stock first, then by landed cost, with priceless offers last."""

        def sort_key(o: Offer):
            return (not o.in_stock, o.total_dkk if o.total_dkk is not None else 1e12)

        return sorted(self.offers, key=sort_key)
