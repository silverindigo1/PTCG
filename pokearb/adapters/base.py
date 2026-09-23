from __future__ import annotations

import html
import logging
from abc import ABC, abstractmethod
from datetime import date

from ..config import Shop
from ..http import PoliteSession
from ..models import Offer

log = logging.getLogger(__name__)


class ShopAdapter(ABC):
    """One adapter per storefront technology, not per shop."""

    def __init__(self, shop: Shop, session: PoliteSession):
        self.shop = shop
        self.session = session
        self.session.set_host_delay(shop.base_url, shop.min_delay)

    @abstractmethod
    def fetch_offers(self) -> list[Offer]:
        """Return every listing in the shop. Filtering happens downstream."""

    def make_offer(
        self,
        *,
        title: str,
        url: str,
        amount: float,
        in_stock: bool,
        currency: str | None = None,
    ) -> Offer:
        return Offer(
            shop_key=self.shop.key,
            shop_name=self.shop.name,
            country=self.shop.country,
            currency=(currency or self.shop.currency).upper(),
            amount=round(float(amount), 2),
            in_stock=bool(in_stock),
            # WooCommerce returns "Sword &amp; Shield"; left encoded it became
            # a set called "Amp Darkness Ablaze".
            title=html.unescape(title).strip(),
            url=url,
            captured_at=date.today().isoformat(),
        )
