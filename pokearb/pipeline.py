"""Orchestration: fetch every shop, classify, convert, persist.

Failure policy, stated once and enforced here: a shop that errors on a given
day keeps its last good prices, each stamped with the date it was actually
captured and flagged stale, rather than vanishing from the site.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date

from .adapters import build_adapter
from .classify import SetMatcher, classify
from .config import (
    ASSUMED_SHIPPING_DKK,
    ASSUMED_SHIPPING_FALLBACK_DKK,
    DATA_DIR,
    LAST_GOOD_DIR,
    SHOPS,
    SHOPS_BY_KEY,
    Shop,
)
from .fx import FxRates, fetch_rates, load_cached_rates
from .http import PoliteSession
from .models import CanonicalProduct, Language, Offer, ProductType
from .sets import fetch_sets, load_cached_sets

log = logging.getLogger(__name__)

LATEST_PATH = DATA_DIR / "latest.json"
RUNLOG_PATH = DATA_DIR / "run_log.json"


# --------------------------------------------------------------------------
# last-good persistence
# --------------------------------------------------------------------------


def _last_good_path(shop_key: str):
    return LAST_GOOD_DIR / f"{shop_key}.json"


def save_last_good(shop: Shop, offers: list[Offer]) -> None:
    LAST_GOOD_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"captured_at": date.today().isoformat(), "offers": [o.to_dict() for o in offers]}
    _last_good_path(shop.key).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8"
    )


def load_last_good(shop: Shop) -> list[Offer]:
    path = _last_good_path(shop.key)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        log.error("Kunne ikke laese sidste gode data for %s", shop.key)
        return []
    offers = []
    for raw in payload.get("offers", []):
        try:
            offer = Offer.from_dict(raw)
        except TypeError:
            continue
        offer.stale = True
        offers.append(offer)
    return offers


# --------------------------------------------------------------------------
# shipping
# --------------------------------------------------------------------------


def apply_shipping(offer: Offer, fx: FxRates) -> None:
    """Attach delivery to Denmark, in DKK, to one offer.

    Where the shop publishes a rate we use it and mark it verified. Where it
    does not, we use the per-country assumption from config and mark it
    unverified, so the page can say which is which and let the reader change
    the number.
    """
    shop = SHOPS_BY_KEY.get(offer.shop_key)
    rule = shop.shipping if shop else None

    if rule is not None and rule.verified and rule.amount is not None:
        # The threshold is quoted in the shop's own currency, so compare there.
        if rule.free_over is not None and offer.amount >= rule.free_over:
            offer.shipping_dkk = 0.0
        else:
            offer.shipping_dkk = fx.to_dkk(rule.amount, offer.currency)
        offer.shipping_verified = True
        return

    offer.shipping_dkk = ASSUMED_SHIPPING_DKK.get(
        offer.country, ASSUMED_SHIPPING_FALLBACK_DKK
    )
    offer.shipping_verified = False


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def collect_offers(session: PoliteSession, shops=SHOPS) -> tuple[list[Offer], list[dict]]:
    all_offers: list[Offer] = []
    run_log: list[dict] = []

    for shop in shops:
        entry = {"shop": shop.key, "name": shop.name, "status": "ok", "count": 0}
        try:
            adapter = build_adapter(shop, session)
            offers = adapter.fetch_offers()
            if not offers:
                raise RuntimeError("0 varer returneret")
            save_last_good(shop, offers)
            entry["count"] = len(offers)
            entry["captured_at"] = date.today().isoformat()
        except Exception as exc:  # noqa: BLE001 - one shop must not stop the run
            log.error("%s fejlede: %s", shop.key, exc)
            offers = load_last_good(shop)
            entry["status"] = "stale" if offers else "failed"
            entry["error"] = str(exc)[:300]
            entry["count"] = len(offers)
            if offers:
                entry["captured_at"] = offers[0].captured_at
        all_offers.extend(offers)
        run_log.append(entry)

    return all_offers, run_log


def build_products(
    offers: list[Offer], matcher: SetMatcher, fx: FxRates
) -> tuple[list[CanonicalProduct], dict[str, int]]:
    grouped: dict[tuple, CanonicalProduct] = {}
    stats = {"accepted": 0, "rejected": 0, "unmatched_set": 0, "no_rate": 0}
    rejections: dict[str, int] = {}

    for offer in offers:
        result = classify(offer.title, matcher)
        if not result.accepted:
            stats["rejected"] += 1
            rejections[result.reason] = rejections.get(result.reason, 0) + 1
            continue

        amount_dkk = fx.to_dkk(offer.amount, offer.currency)
        if amount_dkk is None:
            stats["no_rate"] += 1
            continue
        offer.amount_dkk = amount_dkk
        apply_shipping(offer, fx)

        # Unmatched names group on their sorted words, so that
        # "Mega Evolution Enhanced" and "Enhanced Mega Evolution" are one
        # product rather than two.
        raw_key = "raw:" + " ".join(sorted(result.set_name.lower().split()))
        key = (result.set_id or raw_key, result.product_type, result.language)
        product = grouped.get(key)
        if product is None:
            product = CanonicalProduct(
                set_id=result.set_id,
                set_name=result.set_name,
                set_release_date=result.set_release_date,
                set_matched=result.set_matched,
                product_type=result.product_type,
                language=result.language,
            )
            grouped[key] = product
            if not result.set_matched:
                stats["unmatched_set"] += 1

        product.offers.append(offer)
        stats["accepted"] += 1

    stats["rejection_breakdown"] = dict(
        sorted(rejections.items(), key=lambda kv: -kv[1])[:20]
    )
    return list(grouped.values()), stats


def _dedupe_within_shop(products: list[CanonicalProduct]) -> None:
    """Keep only the cheapest listing per shop per product.

    A shop often lists the same box several times (different variants, a
    pre-order and a stock line). Showing all of them makes the offer table
    noisy without adding information.
    """
    for product in products:
        best: dict[str, Offer] = {}
        for offer in product.offers:
            current = best.get(offer.shop_key)
            if current is None:
                best[offer.shop_key] = offer
                continue
            # Prefer in-stock, then the lower price.
            better = (offer.in_stock, -(offer.total_dkk or 1e12)) > (
                current.in_stock,
                -(current.total_dkk or 1e12),
            )
            if better:
                best[offer.shop_key] = offer
        product.offers = list(best.values())


def run(offline: bool = False, shops=SHOPS) -> dict:
    session = PoliteSession()

    if offline:
        _, aliases = load_cached_sets()
        fx = load_cached_rates()
        offers: list[Offer] = []
        run_log = []
        for shop in shops:
            cached = load_last_good(shop)
            offers.extend(cached)
            run_log.append(
                {"shop": shop.key, "name": shop.name, "status": "stale", "count": len(cached)}
            )
    else:
        _, aliases = fetch_sets(session)
        try:
            fx = fetch_rates(session)
        except RuntimeError:
            raise
        offers, run_log = collect_offers(session, shops)
        session.save_validators()

    matcher = SetMatcher(aliases)
    products, stats = build_products(offers, matcher, fx)
    _dedupe_within_shop(products)
    products = [p for p in products if p.offers]

    from . import history

    history.record(products)

    payload = {
        "generated_at": date.today().isoformat(),
        "fx": {"as_of": fx.as_of, "stale": fx.stale, "rates": fx.rates},
        "assumed_shipping_dkk": dict(ASSUMED_SHIPPING_DKK),
        "shops": run_log,
        "stats": stats,
        "products": [_product_to_dict(p) for p in products],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8"
    )
    RUNLOG_PATH.write_text(json.dumps({"generated_at": payload["generated_at"], "shops": run_log, "stats": stats}, ensure_ascii=False, indent=1), "utf-8")

    log.info(
        "Faerdig: %d produkter, %d tilbud accepteret, %d afvist",
        len(products),
        stats["accepted"],
        stats["rejected"],
    )
    return payload


def _product_to_dict(product: CanonicalProduct) -> dict:
    return {
        "key": product.key,
        "set_id": product.set_id,
        "set_name": product.set_name,
        "set_release_date": product.set_release_date,
        "set_matched": product.set_matched,
        "product_type": product.product_type.value,
        "language": product.language.value,
        "offers": [asdict(o) for o in product.sorted_offers()],
    }


def products_from_payload(payload: dict) -> list[CanonicalProduct]:
    out = []
    for raw in payload.get("products", []):
        product = CanonicalProduct(
            set_id=raw.get("set_id"),
            set_name=raw["set_name"],
            set_release_date=raw.get("set_release_date"),
            set_matched=bool(raw.get("set_matched")),
            product_type=ProductType(raw["product_type"]),
            language=Language(raw["language"]),
            offers=[Offer.from_dict(o) for o in raw.get("offers", [])],
        )
        out.append(product)
    return out
