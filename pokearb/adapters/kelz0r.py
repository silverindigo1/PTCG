"""Kelz0r.dk: HTML parsing, because the shop exposes no machine-readable feed.

HONEST WARNING, READ BEFORE TRUSTING THIS ADAPTER
-------------------------------------------------
The selectors below were written without being able to inspect the live DOM.
They follow the osCommerce family conventions the storefront appears to use,
and they are deliberately layered: several candidate selectors per field, and
a generic table fallback. They may still be wrong.

Run the probe before relying on the data:

    python -m pokearb.cli probe-kelz0r

It prints how many rows were found, the first five parsed titles and prices,
and the raw HTML of the first product block when parsing produced nothing.
Fix SELECTORS below until the probe looks right. The pipeline treats a zero-row
result as a shop failure, so a broken selector keeps the last good prices on
the site rather than silently removing the shop.

Permission note: robots.txt allows these category pages for User-agent: *, but
the shop blocks ClaudeBot and AhrefsBot outright and sets Crawl-delay up to 10s
for the bots it does name. We run at 5s here, single-threaded, and never touch
cart, login or search paths.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Offer
from .base import ShopAdapter

log = logging.getLogger(__name__)

# Sealed Pokemon category pages. `language=en` keeps the markup stable and the
# titles in English, which the classifier handles best.
CATEGORY_PATHS = (
    "/magic/pokemon-sealed-product-c-187_377.html?language=en",
    "/magic/pokemon-kort-c-187.html?language=en",
)

MAX_PAGES = 40

SELECTORS = {
    # Each candidate is tried in order until one yields rows.
    "product_block": [
        "div.product-listing-item",
        "div.productListing",
        "table.productListingTable tr.productListing-even, table.productListingTable tr.productListing-odd",
        "tr.productListing-even, tr.productListing-odd",
        "div.product",
        "li.product",
    ],
    "title": ["a.product-name", "h3 a", "td a[href*='p-']", "a[href*='.html']"],
    "price": [
        "span.productSpecialPrice",
        "span.price",
        "div.price",
        "td.productListing-data:nth-of-type(3)",
    ],
    "stock": ["span.stock", "div.availability", "td.stock"],
}

PRICE_RE = re.compile(r"(\d[\d\.\s]*(?:,\d{1,2})?)")
OUT_OF_STOCK_MARKERS = (
    "udsolgt", "ikke paa lager", "ikke på lager", "sold out", "out of stock",
    "restordre", "forudbestilling",
)


def parse_danish_price(text: str) -> float | None:
    """'1.299,50 DKK' -> 1299.50. Returns None when no number is present."""
    if not text:
        return None
    match = PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _first_match(node, selectors: list[str]):
    for selector in selectors:
        found = node.select_one(selector)
        if found is not None:
            return found
    return None


def _blocks(soup: BeautifulSoup) -> tuple[list, str]:
    for selector in SELECTORS["product_block"]:
        rows = soup.select(selector)
        if rows:
            return rows, selector
    return [], ""


class Kelz0rAdapter(ShopAdapter):
    def fetch_offers(self) -> list[Offer]:
        offers: list[Offer] = []
        for path in CATEGORY_PATHS:
            offers.extend(self._fetch_category(path))
        if not offers:
            raise RuntimeError(
                "Kelz0r-adapteren fandt 0 produkter. Selektorerne i "
                "pokearb/adapters/kelz0r.py matcher ikke sidens HTML laengere. "
                "Koer 'python -m pokearb.cli probe-kelz0r' og ret dem."
            )
        log.info("%s: %d produkter hentet", self.shop.key, len(offers))
        return offers

    def _fetch_category(self, path: str) -> list[Offer]:
        base = self.shop.base_url.rstrip("/")
        out: list[Offer] = []
        for page in range(1, MAX_PAGES + 1):
            separator = "&" if "?" in path else "?"
            url = f"{base}{path}{separator}page={page}"
            response = self.session.get(url)
            if response.status_code == 404:
                break
            response.raise_for_status()
            page_offers = self.parse_listing(response.text, url)
            if not page_offers:
                break
            out.extend(page_offers)
        return out

    def parse_listing(self, html: str, page_url: str) -> list[Offer]:
        soup = BeautifulSoup(html, "lxml")
        rows, selector = _blocks(soup)
        if not rows:
            return []
        log.debug("Kelz0r: %d raekker via '%s'", len(rows), selector)

        out: list[Offer] = []
        for row in rows:
            link = _first_match(row, SELECTORS["title"])
            if link is None:
                continue
            title = link.get_text(" ", strip=True)
            href = link.get("href")
            if not title or not href:
                continue

            price_node = _first_match(row, SELECTORS["price"])
            price_text = price_node.get_text(" ", strip=True) if price_node else row.get_text(" ", strip=True)
            amount = parse_danish_price(price_text)
            if amount is None:
                continue

            row_text = row.get_text(" ", strip=True).lower()
            in_stock = not any(marker in row_text for marker in OUT_OF_STOCK_MARKERS)

            out.append(
                self.make_offer(
                    title=title,
                    url=urljoin(page_url, href),
                    amount=amount,
                    in_stock=in_stock,
                )
            )
        return out
