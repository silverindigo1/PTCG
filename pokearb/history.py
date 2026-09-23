"""Daily price history, stored as JSON in the repository.

One file per year keeps any single file small enough to diff and to load in a
browser. The shape is deliberately flat:

    {"<product key>": {"2026-09-22": {"dkk": 389.0, "shop": "godofcards"}}}

Only the cheapest in-stock offer per product per day is kept. That is what the
chart plots and what "biggest savings" is measured against, and it keeps the
repository from growing by every shop's price every day.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .config import HISTORY_DIR
from .models import CanonicalProduct

log = logging.getLogger(__name__)

# Trim any product series that has had no observation for this long.
RETENTION_DAYS = 730


def _path_for_year(year: int) -> Path:
    return HISTORY_DIR / f"{year}.json"


def load_year(year: int) -> dict[str, dict[str, dict]]:
    path = _path_for_year(year)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        log.error("Kunne ikke laese %s, starter aaret forfra", path)
        return {}


def load_all() -> dict[str, dict[str, dict]]:
    merged: dict[str, dict[str, dict]] = {}
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(HISTORY_DIR.glob("*.json")):
        try:
            year_data = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            log.error("Springer ulaeselig historikfil over: %s", path)
            continue
        for key, observations in year_data.items():
            merged.setdefault(key, {}).update(observations)
    return merged


def record(products: list[CanonicalProduct], on: date | None = None) -> None:
    """Append today's cheapest in-stock price for every product."""
    today = (on or date.today()).isoformat()
    year = int(today[:4])
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    data = load_year(year)

    written = 0
    for product in products:
        cheapest = product.cheapest
        if cheapest is None or cheapest.amount_dkk is None:
            continue
        # A stale price is a price we did not observe today. Do not chart it as
        # if we had.
        if cheapest.stale:
            continue
        data.setdefault(product.key, {})[today] = {
            "dkk": cheapest.amount_dkk,
            "shop": cheapest.shop_key,
            "stock": cheapest.in_stock,
        }
        written += 1

    _path_for_year(year).write_text(
        json.dumps(data, indent=0, sort_keys=True, separators=(",", ":")), "utf-8"
    )
    log.info("Historik: %d priser skrevet for %s", written, today)


def series_for(key: str, all_history: dict[str, dict[str, dict]]) -> list[dict]:
    """Chronological [{'d': '2026-09-22', 'v': 389.0}, ...] for the chart."""
    observations = all_history.get(key, {})
    return [
        {"d": day, "v": entry["dkk"]}
        for day, entry in sorted(observations.items())
        if isinstance(entry, dict) and entry.get("dkk") is not None
    ]
