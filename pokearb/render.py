"""Static site generation.

Everything is rendered ahead of time into site/. The only runtime JavaScript is
search, filtering, the price chart and the collection, all of which work on
data files written beside the pages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import statistics
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import history
from .config import ASSUMED_SHIPPING_DKK, SHOPS_BY_KEY, SITE_DIR, STATIC_DIR, TEMPLATE_DIR
from .models import CanonicalProduct, slugify

log = logging.getLogger(__name__)

TYPE_ORDER = ["etb", "etb_pc", "booster_box", "booster_bundle", "upc"]


def dkk(value: float | None) -> str:
    if value is None:
        return "-"
    whole = f"{value:,.0f}".replace(",", ".")
    return f"{whole} kr."


def money(value: float | None, currency: str) -> str:
    if value is None:
        return "-"
    formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{formatted} {currency}"


def dk_date(value: str | None) -> str:
    if not value:
        return "ukendt"
    try:
        parsed = datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return value
    months = [
        "januar", "februar", "marts", "april", "maj", "juni",
        "juli", "august", "september", "oktober", "november", "december",
    ]
    return f"{parsed.day}. {months[parsed.month - 1]} {parsed.year}"


# Six duotone pairs, all drawn from the site palette so the tiles read as one
# family. Original artwork: no shop photo is copied, because none of the shops
# whose terms we checked grants image reuse.
TILE_PALETTE = [
    ("#0f1b2d", "#eb6834"),
    ("#1e3352", "#eda100"),
    ("#123b33", "#1baf7a"),
    ("#2b1f3f", "#9085e9"),
    ("#3a1d1d", "#e34948"),
    ("#0d2c3f", "#2a78d6"),
]


def tile_art(product) -> dict:
    """Deterministic original cover art for a product.

    Same product, same tile, every run, so the page does not shimmer between
    builds. Derived from the product key alone.
    """
    digest = hashlib.sha1(product.key.encode("utf-8")).digest()
    dark, bright = TILE_PALETTE[digest[0] % len(TILE_PALETTE)]
    words = [w for w in re.split(r"[^A-Za-z0-9]+", product.set_name) if w]
    if len(words) >= 2:
        initials = (words[0][0] + words[1][0]).upper()
    elif words:
        initials = words[0][:2].upper()
    else:
        initials = "PA"
    return {
        "dark": dark,
        "bright": bright,
        "initials": initials,
        # Three shape parameters, so tiles differ from one another without
        # anyone having to draw them.
        "angle": 20 + (digest[1] % 50),
        "offset": 18 + (digest[2] % 44),
        "band": 6 + (digest[3] % 10),
    }


def price_drops(products: list[CanonicalProduct], all_history: dict) -> list[dict]:
    """Products whose cheapest landed cost fell against their recent baseline.

    The baseline is the median of the previous observations, up to seven, so a
    single odd day neither creates nor hides a drop. Needs three observations
    before it will say anything at all.
    """
    out = []
    for product in products:
        series = history.series_for(product.key, all_history)
        if len(series) < 3:
            continue
        today = series[-1]["v"]
        previous = [point["v"] for point in series[-8:-1]]
        if not previous:
            continue
        baseline = statistics.median(previous)
        if baseline <= 0:
            continue
        change = (baseline - today) / baseline
        if change < 0.03:
            continue
        out.append(
            {
                "product": product,
                "today": today,
                "baseline": round(baseline, 2),
                "drop_dkk": round(baseline - today, 2),
                "drop_pct": round(100 * change, 1),
                "days": len(series),
            }
        )
    out.sort(key=lambda row: row["drop_pct"], reverse=True)
    return out


def price_strip(product) -> dict | None:
    """Where the cheapest in-stock total sits between the lowest and highest.

    Positions are percentages along a bar from min to max. Needs a market price
    (three in-stock shops), otherwise there is no range worth drawing.
    """
    market = product.market_price
    rng = product.price_range
    cheapest = product.cheapest
    if market is None or rng is None or cheapest is None or cheapest.total_dkk is None:
        return None
    lo, hi = rng
    span = hi - lo
    if span <= 0:
        return None

    def pos(value: float) -> float:
        return round(100 * (value - lo) / span, 1)

    return {
        "lo": lo,
        "hi": hi,
        "market": market,
        "market_pos": pos(market),
        "ticks": [pos(t) for t in product._in_stock_totals()],
    }


def deals(products: list[CanonicalProduct], minimum_pct: float = 5.0) -> list[CanonicalProduct]:
    """Products whose cheapest landed total is well under the market price.

    The market price is the median of at least three in-stock shops, so a
    single overpriced shop cannot manufacture a deal on its own.
    """
    out = [p for p in products if p.discount_pct is not None and p.discount_pct >= minimum_pct]
    out.sort(key=lambda p: (p.discount_pct, p.market_price - p.cheapest.total_dkk), reverse=True)
    return out


def shipping_rule_text(shop, assumed: dict) -> str:
    rule = shop.shipping
    if rule.verified and rule.amount is not None:
        text = f"{money(rule.amount, shop.currency)} til Danmark"
        if rule.free_over is not None:
            text += f", fri fragt over {money(rule.free_over, shop.currency)}"
        return text
    guess = assumed.get(shop.country)
    if guess is None:
        return "Oplyses ikke"
    return f"Oplyses ikke. PokeArb antager {dkk(guess)}"


def shop_stats(products: list[CanonicalProduct]) -> dict[str, dict]:
    """Per shop: how much it carries, how often it wins, how it prices.

    "Wins" only counts products that at least two shops have in stock, since
    being the only seller is not the same as being cheapest. The price index
    is the median of (shop total / market price), so 100 means the shop sits
    at the market and 95 means five percent under it.
    """
    stats: dict[str, dict] = {}
    for product in products:
        in_stock_shops = {o.shop_key for o in product.in_stock_offers}
        cheapest = product.cheapest
        market = product.market_price
        for offer in product.offers:
            row = stats.setdefault(
                offer.shop_key,
                {"offers": 0, "in_stock": 0, "wins": 0, "contested": 0, "ratios": [], "best": []},
            )
            row["offers"] += 1
            if not offer.in_stock:
                continue
            row["in_stock"] += 1
            if len(in_stock_shops) >= 2:
                row["contested"] += 1
                if cheapest is offer:
                    row["wins"] += 1
                    row["best"].append(product)
            if market and offer.total_dkk:
                row["ratios"].append(offer.total_dkk / market)
    for row in stats.values():
        row["index"] = round(100 * statistics.median(row["ratios"])) if len(row["ratios"]) >= 3 else None
        row["win_pct"] = round(100 * row["wins"] / row["contested"]) if row["contested"] else None
        row["best"].sort(key=lambda p: p.discount_pct or 0, reverse=True)
        del row["ratios"]
    return stats


def offers_data(products: list[CanonicalProduct], payload: dict, fx_rates: dict) -> dict:
    """Everything the basket optimiser on "Min liste" needs, in DKK.

    Shops with a published rate carry it (and the free-shipping threshold)
    converted to DKK. Shops without one carry ship=None, so the page applies
    the reader's own shipping assumption for that country.
    """
    dkk_per_eur = fx_rates.get("DKK")

    def to_dkk(amount: float | None, currency: str) -> float | None:
        if amount is None:
            return None
        if currency == "DKK":
            return round(amount, 2)
        rate = fx_rates.get(currency)
        if not rate or not dkk_per_eur:
            return None
        return round(amount / rate * dkk_per_eur, 2)

    shops = {}
    for key, shop in SHOPS_BY_KEY.items():
        rule = shop.shipping
        verified = bool(rule.verified and rule.amount is not None)
        shops[key] = {
            "name": shop.name,
            "country": shop.country,
            "ship": to_dkk(rule.amount, shop.currency) if verified else None,
            "free_over": to_dkk(rule.free_over, shop.currency) if verified else None,
        }

    items = {}
    for product in products:
        items[product.key] = {
            "t": product.title_da,
            "o": [
                [o.shop_key, o.amount_dkk, o.url]
                for o in product.in_stock_offers
                if o.amount_dkk is not None
            ],
        }
    return {
        "generated_at": payload.get("generated_at"),
        "assumed": payload.get("assumed_shipping_dkk", dict(ASSUMED_SHIPPING_DKK)),
        "shops": shops,
        "products": items,
    }


def build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["dkk"] = dkk
    env.filters["money"] = money
    env.filters["dk_date"] = dk_date
    env.globals["tile_art"] = tile_art
    env.globals["price_strip"] = price_strip
    env.globals["set_slug"] = set_slug
    return env


def set_slug(product) -> str:
    """The set page a product belongs to. Kept in one place for group_by_set and templates."""
    return slugify(product.set_name) + ("" if product.set_matched else "-uden-match")


def group_by_set(products: list[CanonicalProduct]) -> list[dict]:
    """Newest sets first, unmatched names last."""
    groups: dict[str, dict] = {}
    for product in products:
        key = product.set_id or f"raw:{slugify(product.set_name)}"
        group = groups.setdefault(
            key,
            {
                "id": product.set_id,
                "slug": set_slug(product),
                "name": product.set_name,
                "release_date": product.set_release_date,
                "matched": product.set_matched,
                "products": [],
            },
        )
        group["products"].append(product)

    for group in groups.values():
        group["products"].sort(
            key=lambda p: (
                TYPE_ORDER.index(p.product_type.value) if p.product_type.value in TYPE_ORDER else 99,
                p.language.value,
            )
        )
        prices = [p.cheapest.amount_dkk for p in group["products"] if p.cheapest and p.cheapest.amount_dkk]
        group["cheapest"] = min(prices) if prices else None
        group["offer_count"] = sum(len(p.offers) for p in group["products"])

    # Newest first. Where release dates are missing, the sets compared by the
    # most shops come first, which is more useful than an arbitrary order.
    ordered = sorted(
        groups.values(),
        key=lambda g: (g["matched"], g["release_date"] or "0000-00-00", g["offer_count"]),
        reverse=True,
    )
    return ordered


def search_index(products: list[CanonicalProduct]) -> list[dict]:
    rows = []
    for product in products:
        cheapest = product.cheapest
        rows.append(
            {
                "k": product.key,
                "t": product.title_da,
                "s": product.set_name,
                "ty": product.product_type.value,
                "lg": product.language.value,
                "p": cheapest.amount_dkk if cheapest else None,
                "shop": cheapest.shop_name if cheapest else None,
                "stock": bool(product.in_stock_offers),
                "ship": cheapest.shipping_dkk if cheapest else None,
                "countries": sorted({o.country for o in product.offers}),
            }
        )
    return rows


def render_site(payload: dict, products: list[CanonicalProduct]) -> None:
    env = build_environment()
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    all_history = history.load_all()
    groups = group_by_set(products)

    # "Biggest savings": products where shops differ most, measured in DKK on
    # in-stock offers only, and only where at least two shops are in play.
    savings = [
        p
        for p in products
        if p.spread_dkk is not None and len({o.shop_key for o in p.in_stock_offers}) >= 2
    ]
    savings.sort(key=lambda p: p.spread_dkk or 0, reverse=True)

    shop_rows = []
    for entry in payload.get("shops", []):
        shop = SHOPS_BY_KEY.get(entry["shop"])
        shop_rows.append(
            {
                **entry,
                "country": shop.flag if shop else "",
                "url": shop.base_url if shop else "",
                "shipping_confirmed": shop.shipping_confirmed if shop else True,
                "note": shop.notes if shop else "",
            }
        )

    drops = price_drops(products, all_history)
    deal_list = deals(products)
    assumed = payload.get("assumed_shipping_dkk", dict(ASSUMED_SHIPPING_DKK))
    per_shop = shop_stats(products)
    for row in shop_rows:
        shop = SHOPS_BY_KEY.get(row["shop"])
        row["stats"] = per_shop.get(row["shop"], {})
        row["rule"] = shipping_rule_text(shop, assumed) if shop else ""
        row["rule_url"] = shop.shipping.source_url if shop else ""
        row["rule_verified"] = bool(shop and shop.shipping.verified)
        row["currency"] = shop.currency if shop else ""
        row["country_code"] = shop.country if shop else ""

    countries = sorted({SHOPS_BY_KEY[e["shop"]].flag for e in payload.get("shops", []) if e["shop"] in SHOPS_BY_KEY})
    country_text = (", ".join(countries[:-1]) + " og " + countries[-1]) if len(countries) > 1 else "".join(countries)

    context = {
        "country_text": country_text,
        # Shops that actually contributed prices, fresh or carried over.
        "live_shop_count": sum(1 for e in payload.get("shops", []) if e.get("count")),
        "assumed_shipping": payload.get("assumed_shipping_dkk", dict(ASSUMED_SHIPPING_DKK)),
        "drops": drops[:24],
        "deals": deal_list[:12],
        "deal_count": len(deal_list),
        "country_count": len(countries),
        "generated_at": payload.get("generated_at", date.today().isoformat()),
        "fx": payload.get("fx", {}),
        "stats": payload.get("stats", {}),
        "shops": shop_rows,
        "groups": groups,
        "product_count": len(products),
        "set_count": len(groups),
        "offer_count": sum(len(p.offers) for p in products),
    }

    root_ctx = dict(context, root="")
    nested_ctx = dict(context, root="../")

    _write(SITE_DIR / "index.html", env.get_template("index.html").render(**root_ctx))
    _write(
        SITE_DIR / "besparelser.html",
        env.get_template("savings.html").render(**root_ctx, savings=savings[:60], all_deals=deal_list[:60]),
    )
    _write(SITE_DIR / "liste.html", env.get_template("basket.html").render(**root_ctx))
    _write(SITE_DIR / "butikker.html", env.get_template("shops.html").render(**root_ctx))

    shop_dir = SITE_DIR / "butik"
    shop_dir.mkdir(parents=True, exist_ok=True)
    for row in shop_rows:
        _write(
            shop_dir / f"{row['shop']}.html",
            env.get_template("shop.html").render(**nested_ctx, shop=row),
        )
    _write(SITE_DIR / "samling.html", env.get_template("collection.html").render(**root_ctx))
    _write(SITE_DIR / "om.html", env.get_template("about.html").render(**root_ctx))

    set_dir = SITE_DIR / "saet"
    set_dir.mkdir(parents=True, exist_ok=True)
    for group in groups:
        _write(
            set_dir / f"{group['slug']}.html",
            env.get_template("set.html").render(**nested_ctx, group=group),
        )

    product_dir = SITE_DIR / "produkt"
    product_dir.mkdir(parents=True, exist_ok=True)
    product_template = env.get_template("product.html")
    for product in products:
        series = history.series_for(product.key, all_history)
        _write(
            product_dir / f"{product.key}.html",
            product_template.render(**nested_ctx, product=product, series=series),
        )

    data_dir = SITE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write(
        data_dir / "search-index.json",
        json.dumps(search_index(products), ensure_ascii=False, separators=(",", ":")),
    )

    _write(
        data_dir / "offers.json",
        json.dumps(
            offers_data(products, payload, (payload.get("fx") or {}).get("rates", {})),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )

    assets = SITE_DIR / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    shutil.copytree(STATIC_DIR, assets)

    # GitHub Pages must not run the output through Jekyll.
    (SITE_DIR / ".nojekyll").write_text("", "utf-8")

    log.info("Site bygget: %d saet, %d produktsider", len(groups), len(products))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")
