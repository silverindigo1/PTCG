from __future__ import annotations

from ..config import Shop
from ..http import PoliteSession
from .base import ShopAdapter
from .kelz0r import Kelz0rAdapter
from .shopify import ShopifyAdapter
from .woocommerce import WooCommerceAdapter

ADAPTERS: dict[str, type[ShopAdapter]] = {
    "shopify": ShopifyAdapter,
    "woocommerce": WooCommerceAdapter,
    "kelz0r": Kelz0rAdapter,
}


def build_adapter(shop: Shop, session: PoliteSession) -> ShopAdapter:
    try:
        cls = ADAPTERS[shop.adapter]
    except KeyError as exc:
        raise ValueError(
            f"Ukendt adapter '{shop.adapter}' for butikken {shop.key}. "
            f"Kendte adaptere: {', '.join(sorted(ADAPTERS))}"
        ) from exc
    return cls(shop, session)


__all__ = ["ADAPTERS", "ShopAdapter", "build_adapter"]
