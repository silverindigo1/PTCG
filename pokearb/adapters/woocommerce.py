"""WooCommerce storefronts via the public Store API (/wp-json/wc/store/v1).

Better than the Shopify feed in two ways: it states the currency explicitly and
it reports stock status. Prices arrive in minor units, so "14900" with
currency_minor_unit 2 means 149.00.
"""

from __future__ import annotations

import logging

from ..models import Offer
from .base import ShopAdapter

log = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 200


class WooCommerceAdapter(ShopAdapter):
    def fetch_offers(self) -> list[Offer]:
        base = self.shop.base_url.rstrip("/")
        endpoint = f"{base}/wp-json/wc/store/v1/products"
        offers: list[Offer] = []
        page = 1

        while page <= MAX_PAGES:
            url = f"{endpoint}?per_page={PAGE_SIZE}&page={page}&orderby=id&order=asc"
            response = self.session.get(url)
            if response.status_code == 400:
                # The Store API answers 400 once you page past the last page.
                break
            response.raise_for_status()
            products = response.json()
            if not isinstance(products, list) or not products:
                break

            for product in products:
                offer = self._offer_from_product(product)
                if offer is not None:
                    offers.append(offer)

            if page % 5 == 0:
                log.info("%s: side %d, %d produkter indtil nu", self.shop.key, page, len(offers))
            total_pages = response.headers.get("X-WP-TotalPages")
            if total_pages and page >= int(total_pages):
                break
            if len(products) < PAGE_SIZE:
                break
            page += 1

        log.info("%s: %d produkter hentet", self.shop.key, len(offers))
        return offers

    def _offer_from_product(self, product: dict) -> Offer | None:
        title = (product.get("name") or "").strip()
        url = product.get("permalink")
        if not title or not url:
            return None

        prices = product.get("prices") or {}
        raw = prices.get("price")
        if raw in (None, ""):
            return None
        minor = prices.get("currency_minor_unit")
        minor = 2 if minor is None else int(minor)
        try:
            amount = float(raw) / (10**minor)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None

        in_stock = product.get("is_in_stock")
        if in_stock is None:
            in_stock = (product.get("stock_availability") or {}).get("text", "") != ""
        if product.get("is_purchasable") is False:
            in_stock = False

        return self.make_offer(
            title=title,
            url=url,
            amount=amount,
            in_stock=bool(in_stock),
            currency=prices.get("currency_code") or self.shop.currency,
        )
