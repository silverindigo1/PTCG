"""Currency conversion to DKK using ECB daily reference rates.

ECB quotes every rate as units of the foreign currency per 1 EUR, so a
cross rate is rate_DKK / rate_CUR. Rates are cached; if the ECB call fails the
last good rates are reused and the site says which day they are from.

Attribution required by the ECB reuse terms is rendered in the site footer:
"Kilde: Den Europaeiske Centralbank", plus a note that PokeArb converts the
published reference rates, which counts as a modification.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date

from .config import DATA_DIR, ECB_BASE, ECB_CURRENCIES
from .http import PoliteSession

log = logging.getLogger(__name__)

FX_PATH = DATA_DIR / "fx.json"


class FxRates:
    def __init__(self, rates: dict[str, float], as_of: str, stale: bool = False):
        # rates[CUR] = units of CUR per 1 EUR. EUR is 1.0 by definition.
        self.rates = dict(rates)
        self.rates.setdefault("EUR", 1.0)
        self.as_of = as_of
        self.stale = stale

    def to_dkk(self, amount: float, currency: str) -> float | None:
        currency = currency.upper()
        if currency == "DKK":
            return round(amount, 2)
        dkk = self.rates.get("DKK")
        src = self.rates.get(currency)
        if not dkk or not src:
            log.warning("Ingen ECB-kurs for %s, springer prisen over", currency)
            return None
        return round(amount * dkk / src, 2)

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "rates": self.rates}


def _parse_csvdata(text: str) -> tuple[str | None, float | None]:
    reader = csv.DictReader(io.StringIO(text))
    period, value = None, None
    for row in reader:
        raw = row.get("OBS_VALUE") or row.get("obsValue")
        if raw in (None, "", "NaN"):
            continue
        period = row.get("TIME_PERIOD") or row.get("timePeriod")
        value = float(raw)
    return period, value


def fetch_rates(session: PoliteSession) -> FxRates:
    rates: dict[str, float] = {"EUR": 1.0}
    as_of: str | None = None

    for currency in ECB_CURRENCIES:
        url = (
            f"{ECB_BASE}/D.{currency}.EUR.SP00.A"
            "?lastNObservations=1&format=csvdata"
        )
        try:
            response = session.get(url)
            response.raise_for_status()
            period, value = _parse_csvdata(response.text)
        except Exception as exc:  # noqa: BLE001
            log.warning("ECB-kurs for %s fejlede: %s", currency, exc)
            continue
        if value is None:
            continue
        rates[currency] = value
        as_of = max(as_of, period) if as_of and period else (period or as_of)

    if "DKK" not in rates:
        log.error("Ingen DKK-kurs fra ECB, falder tilbage til sidste gode kurser")
        return load_cached_rates(stale=True)

    fx = FxRates(rates, as_of or date.today().isoformat())
    FX_PATH.parent.mkdir(parents=True, exist_ok=True)
    FX_PATH.write_text(json.dumps(fx.to_dict(), indent=1, sort_keys=True), "utf-8")
    return fx


def load_cached_rates(stale: bool = True) -> FxRates:
    if FX_PATH.exists():
        try:
            raw = json.loads(FX_PATH.read_text("utf-8"))
            return FxRates(raw["rates"], raw["as_of"], stale=stale)
        except (json.JSONDecodeError, KeyError, OSError):
            log.error("Kunne ikke laese %s", FX_PATH)
    raise RuntimeError(
        "Ingen valutakurser tilgaengelige: ECB svarede ikke, og der er ingen "
        "cache i data/fx.json. Koer pipelinen igen naar ECB er oppe."
    )
