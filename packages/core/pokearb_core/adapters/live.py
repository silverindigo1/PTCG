"""Live adapters: ECB reference rates and the TCGdex catalogue.

These two are live because both are freely available on documented terms and
were verified while writing the Phase 1 document:

* ECB euro reference rates, daily and historical XML
  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
* TCGdex, multi-language Pokemon TCG catalogue including Japanese
  https://tcgdex.dev/

The ECB publishes these rates for information purposes and discourages using
them for transactions, which is why ``CostAssumptions.fx_spread_rate`` exists
as a separate, explicit parameter rather than pretending the reference rate is
executable.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from ..types import Currency, FxRate
from .base import Adapter, CatalogueSource, FxSource, RawRecord, SourcePolicy, VerificationStatus

__all__ = [
    "ECB_POLICY",
    "TCGDEX_POLICY",
    "EcbFxAdapter",
    "TcgdexCatalogueAdapter",
    "TcgdexPricingAdapter",
    "parse_tcgdex_cardmarket",
    "same_name_sibling_ids",
]

_ECB_NS = {"ex": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}

ECB_POLICY = SourcePolicy(
    source_id="ecb",
    display_name="European Central Bank euro reference rates",
    base_url="https://www.ecb.europa.eu/stats/eurofxref/",
    verification_status=VerificationStatus.VERIFIED_API,
    enabled=True,
    has_official_api=True,
    api_docs_url="https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
    max_requests_per_minute=2,
    min_seconds_between_requests=Decimal("30"),
    notes=(
        "Published for information purposes; the ECB discourages transactional "
        "use, so an explicit FX spread is applied on top in the cost model."
    ),
)

TCGDEX_POLICY = SourcePolicy(
    source_id="tcgdex",
    display_name="TCGdex multi-language Pokemon TCG catalogue",
    base_url="https://api.tcgdex.net/v2",
    verification_status=VerificationStatus.VERIFIED_API,
    enabled=True,
    has_official_api=True,
    api_docs_url="https://tcgdex.dev/",
    max_requests_per_minute=30,
    min_seconds_between_requests=Decimal("2"),
    notes="Open source catalogue. Used as a seed, not as the variant authority.",
)


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pokearb/0.1 (+private research)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


class EcbFxAdapter(FxSource):
    """Daily and historical euro reference rates.

    Rates are returned as ``EUR -> X``. The arbitrage engine needs
    ``JPY -> EUR``, which is derived here by inversion, and ``EUR -> DKK``
    directly. Both are stored dated so historical calculations reproduce.
    """

    DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
    HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"

    def __init__(self, policy: SourcePolicy = ECB_POLICY, fetcher=_get) -> None:
        super().__init__(policy)
        self._fetch = fetcher

    def capabilities(self) -> Mapping[str, bool]:
        return {"fx_current": True, "fx_history": True, "fx_intraday": False}

    def fetch_rates(self, on: Optional[date] = None) -> Sequence[FxRate]:
        self._guard("fx_poll")
        url = self.DAILY_URL if on is None else self.HIST_90D_URL
        raw = RawRecord(
            self.source_id, datetime.now(timezone.utc), url,
            None, http_status=None,
        )
        return self.parse(self._fetch(url), url, target_date=on)

    def parse(
        self, xml_bytes: bytes, url: str, target_date: Optional[date] = None
    ) -> list[FxRate]:
        """Parse the ECB envelope into dated rates, including derived pairs."""
        root = ET.fromstring(xml_bytes)
        out: list[FxRate] = []
        for day_cube in root.iterfind(".//ex:Cube[@time]", _ECB_NS):
            as_of = date.fromisoformat(day_cube.attrib["time"])
            if target_date is not None and as_of != target_date:
                continue
            per_currency: dict[Currency, Decimal] = {}
            for cube in day_cube.iterfind("ex:Cube[@currency]", _ECB_NS):
                try:
                    code = Currency(cube.attrib["currency"])
                except ValueError:
                    continue
                per_currency[code] = Decimal(cube.attrib["rate"])

            for code, rate in per_currency.items():
                out.append(FxRate(as_of, Currency.EUR, code, rate, url))
                if rate > 0:
                    out.append(
                        FxRate(
                            as_of, code, Currency.EUR,
                            (Decimal("1") / rate).quantize(Decimal("0.00000001")),
                            url,
                        )
                    )
            # JPY -> DKK via EUR, useful for direct presentation.
            jpy, dkk = per_currency.get(Currency.JPY), per_currency.get(Currency.DKK)
            if jpy and dkk and jpy > 0:
                out.append(
                    FxRate(
                        as_of, Currency.JPY, Currency.DKK,
                        (dkk / jpy).quantize(Decimal("0.00000001")), url,
                    )
                )
        return out

    @staticmethod
    def as_map(rates: Sequence[FxRate]) -> dict[tuple[Currency, Currency], FxRate]:
        latest: dict[tuple[Currency, Currency], FxRate] = {}
        for rate in rates:
            key = (rate.base, rate.quote)
            if key not in latest or rate.as_of > latest[key].as_of:
                latest[key] = rate
        return latest


class TcgdexCatalogueAdapter(CatalogueSource):
    """Canonical card seed data, including Japanese sets and promos.

    TCGdex is a seed, not the variant authority. External catalogues do not
    model holo versus reverse-holo versus stamped printings consistently, and
    those distinctions are exactly what the arbitrage maths depends on, so the
    loader creates one internal variant per *printing* and records which
    discriminators the catalogue did not supply.
    """

    def __init__(self, policy: SourcePolicy = TCGDEX_POLICY, fetcher=_get, language: str = "ja") -> None:
        super().__init__(policy)
        self._fetch = fetcher
        self.language = language

    @property
    def _base(self) -> str:
        return f"{self.policy.base_url}/{self.language}"

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "sets": True,
            "cards": True,
            "images": True,
            "prices": False,
            "printings": False,  # not modelled consistently upstream
        }

    def fetch_sets(self) -> Sequence[RawRecord]:
        self._guard("catalogue")
        url = f"{self._base}/sets"
        import json as _json

        payload = _json.loads(self._fetch(url))
        return [
            RawRecord(self.source_id, datetime.now(timezone.utc), url, payload).with_hash()
        ]

    def fetch_cards(self, set_code: str) -> Sequence[RawRecord]:
        self._guard("catalogue")
        url = f"{self._base}/sets/{set_code}"
        import json as _json

        payload = _json.loads(self._fetch(url))
        return [
            RawRecord(self.source_id, datetime.now(timezone.utc), url, payload).with_hash()
        ]


# --------------------------------------------------------------- pricing ----

#: TCGdex relays Cardmarket's published averages per card, in EUR, updated
#: daily, with no key required. Documented at https://tcgdex.dev/markets-prices
#: and https://tcgdex.dev/faq. The FAQ also documents the known defect that
#: shapes the parser below: different printings of one Pokemon can be mapped to
#: the same Cardmarket listing and so show identical prices.
TCGDEX_PRICING_DOCS = "https://tcgdex.dev/markets-prices"
TCGDEX_FAQ = "https://tcgdex.dev/faq"

_PRICE_FIELDS = ("avg", "low", "trend", "avg1", "avg7", "avg30")


def _price(value) -> Optional[Decimal]:
    """A published average, or ``None``. Zero and negatives are not prices.

    Recorded responses carry ``"trend-holo": 0`` for cards that have no
    reverse-holo market at all, so reading zero as a price would value a card
    at nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return d if d > 0 else None


def _iso(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_tcgdex_cardmarket(
    payload: Mapping,
    *,
    printing: "Printing",
    variant_id: str,
    known_at: datetime,
    language: Optional["Language"] = None,
    source_url: Optional[str] = None,
    raw_hash: Optional[str] = None,
) -> tuple[Optional["MarketAverage"], tuple[str, ...]]:
    """Read Cardmarket's averages for one printing out of a TCGdex card payload.

    Returns ``(observation, reasons)``. ``observation`` is ``None`` whenever the
    payload does not say, unambiguously, what this printing sells for, and
    ``reasons`` then says why. Nothing is guessed.

    Which fields describe which printing is decided from the card's own
    ``variants`` block, because Cardmarket publishes one set of base fields and
    one set of ``-holo`` fields per product:

    * reverse holo: the ``-holo`` fields, and only if the card has a reverse;
    * the base printing: the plain fields, and only if exactly one of normal
      and holo exists, since otherwise the plain fields could describe either;
    * anything else (textured, other foils, first editions): refused, because
      the provider does not publish those separately.
    """
    from ..types import Currency, MarketAverage, Printing  # local: avoid cycles

    reasons: list[str] = []
    pricing = payload.get("pricing") or {}
    cm = pricing.get("cardmarket")
    if not cm:
        return None, ("TCGdex publishes no Cardmarket pricing for this card",)

    if (cm.get("unit") or "").upper() != "EUR":
        return None, (f"unexpected Cardmarket currency {cm.get('unit')!r}",)
    if not cm.get("updated"):
        return None, ("Cardmarket pricing carries no update time; freshness unknowable",)

    variants = payload.get("variants") or {}
    has_normal = bool(variants.get("normal"))
    has_holo = bool(variants.get("holo"))
    has_reverse = bool(variants.get("reverse"))

    if variants.get("firstEdition"):
        return None, (
            "card exists in a first edition; Cardmarket's averages may mix "
            "editions, so no edition-specific price can be read",
        )

    if printing is Printing.REVERSE_HOLO:
        if not has_reverse:
            return None, ("card has no reverse-holo printing on TCGdex",)
        suffix, finish = "-holo", "reverse"
    elif printing in (Printing.NON_HOLO, Printing.HOLO):
        if has_normal and has_holo:
            return None, (
                "card exists as both normal and holo, and Cardmarket's base "
                "averages could describe either; refusing rather than guessing",
            )
        wanted_holo = printing is Printing.HOLO
        if wanted_holo and not has_holo:
            return None, ("card has no holo printing on TCGdex",)
        if not wanted_holo and not has_normal:
            return None, ("card has no non-holo printing on TCGdex",)
        suffix, finish = "", "base"
    else:
        return None, (
            f"{printing.value} printings are not priced separately by the provider",
        )

    values = {f: _price(cm.get(f + suffix)) for f in _PRICE_FIELDS}
    if not any(values.values()):
        return None, (f"no usable Cardmarket figures for the {finish} printing",)

    product = cm.get("idProduct")
    obs = MarketAverage(
        variant_id=variant_id,
        provider="cardmarket",
        via="tcgdex",
        product_id=str(product) if product not in (None, "", 0) else None,
        finish=finish,
        currency=Currency.EUR,
        provider_updated_at=_iso(str(cm["updated"])),
        known_at=known_at,
        market="EU",
        language=language,
        source_url=source_url,
        raw_hash=raw_hash,
        **values,
    )
    return obs, tuple(reasons)


def same_name_sibling_ids(set_payload: Mapping, card_id: str) -> list[str]:
    """Other cards in the same set with the same name.

    These are the cards at risk of sharing this card's Cardmarket listing, per
    the defect TCGdex documents: different printings of one Pokemon mapped to
    the same external listing.
    """
    cards = set_payload.get("cards") or []
    mine = next((c for c in cards if c.get("id") == card_id), None)
    if mine is None:
        return []
    return [
        c["id"] for c in cards
        if c.get("name") == mine.get("name") and c.get("id") != card_id
    ]


class TcgdexPricingAdapter(Adapter):
    """Live Cardmarket averages via TCGdex, with the sibling collision check.

    Parser written and tested against recorded responses; live access
    verified. See tests/fixtures/tcgdex/README.md.
    """

    def __init__(self, policy: SourcePolicy = TCGDEX_POLICY, fetcher=_get) -> None:
        super().__init__(policy)
        self._fetch = fetcher

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "market_averages": True,
            "individual_sales": False,   # averages only; never sale-level data
            "sale_counts": False,        # how many sales sit behind an average
            "parser_implemented": True,
            "verified_live_access": True,
        }

    def _json(self, url: str) -> tuple[dict, RawRecord]:
        import json as _json

        payload = _json.loads(self._fetch(url))
        raw = RawRecord(self.source_id, datetime.now(timezone.utc), url, payload).with_hash()
        return payload, raw

    def card(self, card_id: str, language: str = "ja") -> tuple[dict, RawRecord]:
        self._guard("catalogue")
        return self._json(f"{self.policy.base_url}/{language}/cards/{card_id}")

    def set_listing(self, set_id: str, language: str = "ja") -> tuple[dict, RawRecord]:
        self._guard("catalogue")
        return self._json(f"{self.policy.base_url}/{language}/sets/{set_id}")

    def observe(
        self,
        card_id: str,
        *,
        printing: "Printing",
        variant_id: str,
        language: str = "ja",
    ) -> dict:
        """Fetch one card's averages plus the product ids of its same-name siblings.

        Returns a dict with ``observation``, ``reasons``, ``sibling_product_ids``,
        ``sibling_ids`` and ``raw`` (every payload fetched, for persistence).
        """
        from ..types import Language, Printing

        payload, raw = self.card(card_id, language)
        lang = Language(language) if language in {l.value for l in Language} else None
        obs, reasons = parse_tcgdex_cardmarket(
            payload, printing=printing, variant_id=variant_id,
            known_at=raw.fetched_at, language=lang,
            source_url=raw.request_url, raw_hash=raw.content_hash,
        )
        raws = [raw]
        sibling_products: list[str] = []
        sibling_ids: list[str] = []
        set_id = ((payload.get("set") or {}).get("id")) or card_id.rsplit("-", 1)[0]
        try:
            set_payload, set_raw = self.set_listing(set_id, language)
            raws.append(set_raw)
            sibling_ids = same_name_sibling_ids(set_payload, card_id)
            for sid in sibling_ids[:6]:
                sp, sraw = self.card(sid, language)
                raws.append(sraw)
                prod = ((sp.get("pricing") or {}).get("cardmarket") or {}).get("idProduct")
                if prod not in (None, "", 0):
                    sibling_products.append(str(prod))
        except Exception as exc:  # noqa: BLE001
            # The collision check could not run. Say so; the benchmark
            # refuses an observation whose mapping could not be checked.
            reasons = tuple(reasons) + (f"sibling check failed: {exc}",)
            sibling_products = None  # type: ignore[assignment]
        return {
            "observation": obs,
            "reasons": tuple(reasons),
            "sibling_product_ids": sibling_products,
            "sibling_ids": sibling_ids,
            "raw": raws,
        }


def tcgdex_card_id_candidates(set_code: str, number: str) -> list[str]:
    """TCGdex ids to try for a canonical set code and number.

    TCGdex matches the set part case-insensitively but needs the number
    zero-padded to three digits (``SV2a-025`` resolves, ``SV2a-25`` does not),
    verified against the live API when this was written.
    """
    num = (number or "").split("/")[0].strip()
    if not set_code or not num:
        return []
    out = []
    if num.isdigit():
        out.append(f"{set_code}-{num.zfill(3)}")
    out.append(f"{set_code}-{num}")
    return list(dict.fromkeys(out))
