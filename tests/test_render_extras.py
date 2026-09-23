"""Deals, price strip, shop statistics and the basket data file."""

from __future__ import annotations

from pokearb import render
from pokearb.config import SHOPS_BY_KEY
from pokearb.models import CanonicalProduct, Language, Offer, ProductType


def _offer(shop: str, total: float, in_stock: bool = True) -> Offer:
    offer = Offer(
        shop_key=shop, shop_name=shop, country="DK", currency="DKK",
        amount=total, in_stock=in_stock, title="t", url=f"https://{shop}.example/p", captured_at="2026-09-24",
    )
    offer.amount_dkk = total
    offer.shipping_dkk = 0.0
    offer.shipping_verified = True
    return offer


def _product(set_id: str, *offers: Offer) -> CanonicalProduct:
    return CanonicalProduct(
        set_id=set_id, set_name=f"Set {set_id}", set_release_date=None, set_matched=True,
        product_type=ProductType.ETB, language=Language.EN, offers=list(offers),
    )


def test_deals_need_a_real_discount_and_are_sorted():
    big = _product("a", _offer("x", 800), _offer("y", 1000), _offer("z", 1050))     # 20 % under
    small = _product("b", _offer("x", 980), _offer("y", 1000), _offer("z", 1010))   # 2 % under
    mid = _product("c", _offer("x", 900), _offer("y", 1000), _offer("z", 1100))     # 10 % under
    two = _product("d", _offer("x", 500), _offer("y", 1000))                        # no market
    assert [p.set_id for p in render.deals([big, small, mid, two])] == ["a", "c"]


def test_price_strip_positions():
    p = _product("a", _offer("x", 800), _offer("y", 1000), _offer("z", 1200))
    strip = render.price_strip(p)
    assert strip["lo"] == 800 and strip["hi"] == 1200
    assert strip["market_pos"] == 50.0
    assert strip["ticks"] == [0.0, 50.0, 100.0]
    assert render.price_strip(_product("b", _offer("x", 800), _offer("y", 900))) is None


def test_shop_stats_counts_only_contested_wins():
    contested = _product("a", _offer("x", 800), _offer("y", 1000), _offer("z", 1200))
    alone = _product("b", _offer("y", 500))
    stats = render.shop_stats([contested, alone])
    assert stats["x"]["wins"] == 1 and stats["x"]["contested"] == 1
    assert stats["y"]["wins"] == 0 and stats["y"]["contested"] == 1  # alone does not count
    assert stats["y"]["in_stock"] == 2
    assert stats["x"]["index"] is None  # fewer than three priced products


def test_offers_data_converts_shipping_rules_to_dkk():
    fx = {"EUR": 1.0, "DKK": 7.46, "SEK": 11.25}
    product = _product("a", _offer("matraws", 700), _offer("gone", 650, in_stock=False))
    data = render.offers_data([product], {"generated_at": "2026-09-24"}, fx)
    assert data["products"][product.key]["o"] == [["matraws", 700, "https://matraws.example/p"]]

    for key, shop in SHOPS_BY_KEY.items():
        entry = data["shops"][key]
        if shop.shipping.verified and shop.shipping.amount is not None:
            assert entry["ship"] is not None
            if shop.currency == "DKK":
                assert entry["ship"] == shop.shipping.amount
        else:
            assert entry["ship"] is None and entry["free_over"] is None

    lich = SHOPS_BY_KEY["lichcards"]
    assert lich.currency == "EUR"
    assert data["shops"]["lichcards"]["free_over"] == round(lich.shipping.free_over * 7.46, 2)
