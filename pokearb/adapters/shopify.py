"""Shopify storefronts via the public /products.json feed.

The feed is a documented, machine-readable catalogue. It is not disallowed by
any of the robots.txt files recorded in SOURCES.md, and it is far lighter on
the shop than walking collection pages in HTML.

Two things the feed does not give us:
  * a currency, which comes from /meta.json and is pinned in config.py
  * a stock count, only variant.available, which is what we surface
"""

from __future__ import annotations

import logging

from ..models import Offer
from .base import ShopAdapter

log = logging.getLogger(__name__)

PAGE_SIZE = 250
MAX_PAGES = 200  # 50k products, a hard stop against a pagination loop


class ShopifyAdapter(ShopAdapter):
    def fetch_offers(self) -> list[Offer]:
        offers: list[Offer] = []
        seen_first_ids: set[int] = set()

        for path in self._feed_paths():
            page = 1
            while page <= MAX_PAGES:
                url = f"{path}?limit={PAGE_SIZE}&page={page}"
                payload = self.session.get_json(url)
                products = payload.get("products", []) if isinstance(payload, dict) else []
                if not products:
                    break

                # Some themes ignore ?page and keep serving page 1. Detect that
                # rather than looping until MAX_PAGES.
                first_id = products[0].get("id")
                if first_id in seen_first_ids:
                    log.warning(
                        "%s gentager side 1 ved page=%d, stopper pagineringen",
                        self.shop.key,
                        page,
                    )
                    break
                seen_first_ids.add(first_id)

                offers.extend(self._offers_from_products(products))
                if page % 5 == 0:
                    log.info("%s: side %d, %d varianter indtil nu", self.shop.key, page, len(offers))
                if len(products) < PAGE_SIZE:
                    break
                page += 1

        log.info("%s: %d varianter hentet", self.shop.key, len(offers))
        return offers

    def _feed_paths(self) -> list[str]:
        base = self.shop.base_url.rstrip("/")
        if self.shop.collections:
            return [f"{base}/collections/{h}/products.json" for h in self.shop.collections]
        return [f"{base}/products.json"]

    def _offers_from_products(self, products: list[dict]) -> list[Offer]:
        base = self.shop.base_url.rstrip("/")
        out: list[Offer] = []
        for product in products:
            handle = product.get("handle")
            title = (product.get("title") or "").strip()
            if not handle or not title:
                continue
            url = f"{base}/products/{handle}"
            variants = product.get("variants") or []
            for variant in variants:
                price = variant.get("price")
                if price in (None, ""):
                    continue
                try:
                    amount = float(price)
                except (TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue

                variant_title = (variant.get("title") or "").strip()
                full_title = title
                if variant_title and variant_title.lower() not in ("default title", "default"):
                    full_title = f"{title} - {variant_title}"

                out.append(
                    self.make_offer(
                        title=full_title,
                        url=url,
                        amount=amount,
                        in_stock=bool(variant.get("available")),
                    )
                )
        return out
