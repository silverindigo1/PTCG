"""Market price, discount and range, on hand-built offers."""

from __future__ import annotations

from pokearb.models import CanonicalProduct, Language, Offer, ProductType


def _offer(shop: str, total: float, in_stock: bool = True) -> Offer:
    offer = Offer(
        shop_key=shop, shop_name=shop, country="DK", currency="DKK",
        amount=total, in_stock=in_stock, title="t", url="u", captured_at="2026-09-24",
    )
    offer.amount_dkk = total
    offer.shipping_dkk = 0.0
    offer.shipping_verified = True
    return offer


def _product(*offers: Offer) -> CanonicalProduct:
    return CanonicalProduct(
        set_id="sv08", set_name="Surging Sparks", set_release_date=None, set_matched=True,
        product_type=ProductType.ETB, language=Language.EN, offers=list(offers),
    )


def test_market_price_is_the_median_of_in_stock_shops():
    p = _product(_offer("a", 1000), _offer("b", 1100), _offer("c", 1500), _offer("d", 900, in_stock=False))
    assert p.market_price == 1100  # the sold-out 900 does not count
    assert p.discount_pct == round(100 * (1100 - 1000) / 1100, 1)


def test_one_expensive_shop_does_not_inflate_the_market():
    p = _product(_offer("a", 1000), _offer("b", 1020), _offer("c", 3000))
    assert p.market_price == 1020
    assert p.discount_pct <= 2.0


def test_no_market_price_below_three_shops():
    p = _product(_offer("a", 1000), _offer("b", 1200))
    assert p.market_price is None
    assert p.discount_pct is None
    assert p.price_range == (1000, 1200)


def test_even_count_takes_the_midpoint():
    p = _product(_offer("a", 1000), _offer("b", 1100), _offer("c", 1300), _offer("d", 1400))
    assert p.market_price == 1200


def test_pre_order_is_read_from_the_title():
    # Recorded 2026-09-23, sealedcardzz /products.json
    assert _titled("Pre-Order | Pokemon - Storm Emeralda Display (M6) (JP)").pre_order
    for title in ("Vorbestellung: Pokemon Mega Evolution Display", "Preventa Caja Pokémon",
                  "Forudbestilling - Pokemon ETB", "Précommande Coffret Dresseur d'Elite"):
        assert _titled(title).pre_order, title
    for title in ("Pokemon Surging Sparks Booster Box", "Perfect Order Elite Trainer Box"):
        assert not _titled(title).pre_order, title


def _titled(title: str) -> Offer:
    offer = _offer("a", 100)
    offer.title = title
    return offer
