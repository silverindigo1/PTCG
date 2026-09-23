"""The canonical set list, fetched live from TCGdex on every run.

TCGdex is called live, as configured. The brief set list does not always carry
a release date, so per-set detail is fetched once and cached permanently in
data/sets_cache.json: a set's release date never changes, so a set is fetched
at most once in the project's lifetime, and a normal daily run adds nothing.
"""

from __future__ import annotations

import json
import logging

from .classify import normalise
from .config import DATA_DIR, TCGDEX_BASE, TCGDEX_LANGUAGES
from .http import PoliteSession

log = logging.getLogger(__name__)

CACHE_PATH = DATA_DIR / "sets_cache.json"


def _load_cache() -> dict[str, dict]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("Kunne ikke laese saet-cachen, bygger den forfra")
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True, ensure_ascii=False), "utf-8")


def fetch_sets(session: PoliteSession, fetch_details: bool = True) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (sets_by_id, aliases).

    sets_by_id is keyed by the English TCGdex set id.
    aliases maps a normalised name, in every fetched language, to that record.
    """
    cache = _load_cache()
    sets_by_id: dict[str, dict] = {}
    aliases: dict[str, dict] = {}

    english = session.get_json(f"{TCGDEX_BASE}/en/sets", conditional=False)
    for entry in english:
        set_id = entry.get("id")
        name = entry.get("name")
        if not set_id or not name:
            continue
        record = {
            "id": set_id,
            "name": name,
            "cardCount": (entry.get("cardCount") or {}).get("official"),
            "releaseDate": entry.get("releaseDate") or cache.get(set_id, {}).get("releaseDate"),
            "logo": entry.get("logo"),
        }
        sets_by_id[set_id] = record

    # Fill in missing release dates, once per set, ever.
    if fetch_details:
        missing = [sid for sid, rec in sets_by_id.items() if not rec.get("releaseDate")]
        if missing:
            log.info("Henter udgivelsesdato for %d nye saet", len(missing))
        for set_id in missing:
            try:
                detail = session.get_json(f"{TCGDEX_BASE}/en/sets/{set_id}")
            except Exception as exc:  # noqa: BLE001 - one bad set must not stop the run
                log.warning("Kunne ikke hente detaljer for saettet %s: %s", set_id, exc)
                continue
            sets_by_id[set_id]["releaseDate"] = detail.get("releaseDate")
            if logo := detail.get("logo"):
                sets_by_id[set_id]["logo"] = logo

    cache.update(sets_by_id)
    _save_cache(cache)

    for record in sets_by_id.values():
        aliases[normalise(record["name"])] = dict(record, _alias_lang="en")

    # Localised names point at the same English record, so "Rivalen am Abgrund"
    # and "Surging Sparks" collapse to one canonical product.
    for lang in TCGDEX_LANGUAGES:
        if lang == "en":
            continue
        try:
            localised = session.get_json(f"{TCGDEX_BASE}/{lang}/sets")
        except Exception as exc:  # noqa: BLE001
            log.warning("Kunne ikke hente saetlisten paa %s: %s", lang, exc)
            continue
        for entry in localised:
            set_id, name = entry.get("id"), entry.get("name")
            if not set_id or not name:
                continue
            record = sets_by_id.get(set_id)
            if record is None:
                continue
            # A copy per alias, tagged with its language, so the classifier
            # can tell that "Buio Pesto" means an Italian box.
            aliases.setdefault(normalise(name), dict(record, _alias_lang=lang))

    log.info("Saetliste: %d saet, %d navnevarianter", len(sets_by_id), len(aliases))
    return sets_by_id, aliases


def load_cached_sets() -> tuple[dict[str, dict], dict[str, dict]]:
    """Offline fallback used by the tests and by --offline runs."""
    cache = _load_cache()
    aliases = {normalise(rec["name"]): rec for rec in cache.values() if rec.get("name")}
    return cache, aliases
