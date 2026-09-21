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

__all__ = ["ECB_POLICY", "TCGDEX_POLICY", "EcbFxAdapter", "TcgdexCatalogueAdapter"]

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
