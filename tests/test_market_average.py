"""Market averages via TCGdex: parser, acceptance rules, CLI and Quick Check.

Every parser test runs on a response recorded from the live API (see
tests/fixtures/tcgdex/README.md). Where a test needs a case the live API did
not produce, it builds a derived copy of a recorded response and says so.
Nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "tests"))

from pokearb_core.adapters.live import (  # noqa: E402
    TcgdexPricingAdapter,
    parse_tcgdex_cardmarket,
    same_name_sibling_ids,
    tcgdex_card_id_candidates,
)
from pokearb_core.arbitrage.reference_policy import ROWS  # noqa: E402
from pokearb_core.types import (  # noqa: E402
    CardVariant,
    Currency,
    Language,
    MarketAverage,
    Printing,
)
from pokearb_core.valuation.benchmark import (  # noqa: E402
    BASIS,
    BenchmarkConfig,
    assess_market_average,
)

FIX = ROOT / "tests" / "fixtures" / "tcgdex"
FETCHED = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def recorded(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def parse(name: str, printing: Printing, **kw):
    return parse_tcgdex_cardmarket(
        recorded(name), printing=printing, variant_id="v", known_at=FETCHED, **kw
    )


def obs_with(**fields) -> MarketAverage:
    base = dict(
        variant_id="v", provider="cardmarket", via="tcgdex", product_id="1",
        finish="base", currency=Currency.EUR,
        provider_updated_at=datetime(2026, 9, 20, 22, 54, tzinfo=timezone.utc),
        known_at=FETCHED,
    )
    base.update(fields)
    return MarketAverage(**base)


# ------------------------------------------------------------------ parser --

def test_recorded_japanese_card_parses_to_its_own_cardmarket_product():
    obs, reasons = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    assert obs is not None, reasons
    assert obs.product_id == "719626"
    assert obs.finish == "base"
    assert obs.currency is Currency.EUR
    assert (obs.avg7, obs.avg30, obs.trend) == (
        Decimal("32.26"), Decimal("33.35"), Decimal("28.29"))
    assert obs.market == "EU"


def test_japanese_and_english_printings_are_different_products():
    """Language separation, from recorded responses: not the same listing."""
    ja, _ = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    en, _ = parse("en_cards_sv03.5-173.json", Printing.HOLO)
    assert ja is not None and en is not None
    assert ja.product_id != en.product_id
    assert ja.avg30 != en.avg30


def test_a_published_zero_is_not_a_price():
    """The recorded SV2a-173 response carries "trend-holo": 0."""
    assert recorded("ja_cards_SV2a-173.json")["pricing"]["cardmarket"]["trend-holo"] == 0
    obs, reasons = parse("ja_cards_SV2a-173.json", Printing.REVERSE_HOLO)
    assert obs is None
    assert "reverse" in reasons[0]


def test_card_without_pricing_is_refused_with_a_reason():
    obs, reasons = parse("ja_cards_M-P-023.json", Printing.NON_HOLO)
    assert obs is None
    assert "no Cardmarket pricing" in reasons[0]


def test_reverse_holo_reads_the_holo_suffixed_fields():
    base, _ = parse("en_cards_swsh3-136.json", Printing.NON_HOLO)
    rev, _ = parse("en_cards_swsh3-136.json", Printing.REVERSE_HOLO)
    assert base is not None and rev is not None
    assert base.finish == "base" and rev.finish == "reverse"
    assert rev.avg30 == Decimal("0.3") and base.avg30 == Decimal("0.08")


def test_printing_the_card_does_not_have_is_refused():
    obs, reasons = parse("ja_cards_SV2a-173.json", Printing.NON_HOLO)
    assert obs is None
    assert "no non-holo printing" in reasons[0]


def test_ambiguous_base_printing_is_refused():
    """Derived: a card listed as both normal and holo. Not a recorded case."""
    payload = copy.deepcopy(recorded("ja_cards_SV2a-173.json"))
    payload["variants"]["normal"] = True
    payload["variants"]["holo"] = True
    obs, reasons = parse_tcgdex_cardmarket(
        payload, printing=Printing.HOLO, variant_id="v", known_at=FETCHED)
    assert obs is None
    assert "could describe either" in reasons[0]


def test_sibling_lookup_uses_the_recorded_set_listing():
    siblings = same_name_sibling_ids(recorded("ja_sets_SV2a.json"), "SV2a-173")
    assert "SV2a-025" in siblings
    assert "SV2a-173" not in siblings


def test_card_id_candidates_pad_the_number():
    """TCGdex resolves SV2a-025 but not SV2a-25 (checked against the live API)."""
    assert tcgdex_card_id_candidates("SV2A", "25")[0] == "SV2A-025"
    assert tcgdex_card_id_candidates("SV2a", "173/165")[0] == "SV2a-173"
    assert tcgdex_card_id_candidates("", "1") == []


# ------------------------------------------------------- acceptance rules --

def test_a_clean_recorded_average_is_accepted_at_the_lowest_figure():
    obs, reasons = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    bench = assess_market_average(obs, as_of=AS_OF, sibling_product_ids=["719467"],
                                  parser_reasons=reasons)
    assert bench.accepted, bench.reasons
    assert bench.value.amount == Decimal("28.29"), "the lowest of 7-day, 30-day and trend"
    assert bench.basis == BASIS
    assert "trend" in bench.statistic


def test_figures_that_disagree_are_refused():
    """Recorded: M2-115 publishes avg 21.62 against a 7-day average of 39.97."""
    payload = recorded("ja_cards_M2-115.json")
    printing = Printing.HOLO if payload["variants"].get("holo") else Printing.NON_HOLO
    obs, reasons = parse_tcgdex_cardmarket(
        payload, printing=printing, variant_id="v", known_at=FETCHED)
    assert obs is not None, reasons
    bench = assess_market_average(obs, as_of=AS_OF, sibling_product_ids=[])
    assert bench.refused
    assert "disagree" in bench.reasons[-1]


def test_a_mapping_collision_is_refused():
    """Derived: SV2a-025 given SV2a-173's product id.

    A scan of 168 live cards across six Japanese sets found no real
    collision, so this case is constructed. The defect it guards against is
    documented by TCGdex itself at https://tcgdex.dev/faq.
    """
    obs, _ = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    bench = assess_market_average(obs, as_of=AS_OF, sibling_product_ids=["719626"])
    assert bench.refused
    assert "also mapped to another printing" in bench.reasons[-1]


def test_an_unchecked_mapping_is_refused():
    obs, _ = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    bench = assess_market_average(obs, as_of=AS_OF, sibling_product_ids=None)
    assert bench.refused
    assert "could not be run" in bench.reasons[-1]


def test_missing_product_id_is_refused():
    bench = assess_market_average(
        obs_with(product_id=None, avg7=Decimal("10"), avg30=Decimal("10")),
        as_of=AS_OF, sibling_product_ids=[])
    assert bench.refused


def test_stale_figures_are_refused():
    obs, _ = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    bench = assess_market_average(
        obs, as_of=obs.provider_updated_at + timedelta(days=5), sibling_product_ids=[])
    assert bench.refused
    assert "stale" in bench.reasons[-1]


def test_figures_fetched_after_the_calculation_time_are_excluded():
    obs, _ = parse("ja_cards_SV2a-173.json", Printing.HOLO)
    bench = assess_market_average(
        obs, as_of=FETCHED - timedelta(minutes=1), sibling_product_ids=[])
    assert bench.refused
    assert "look-ahead" in bench.reasons[-1]


def test_too_few_figures_are_refused():
    bench = assess_market_average(
        obs_with(avg7=Decimal("10")), as_of=AS_OF, sibling_product_ids=[])
    assert bench.refused
    assert "at least 2" in bench.reasons[-1]


def test_the_lowest_listing_ask_is_never_used_as_the_value():
    """``low`` is the cheapest open offer, not a traded price."""
    bench = assess_market_average(
        obs_with(low=Decimal("5"), avg7=Decimal("10"), avg30=Decimal("11"),
                 trend=Decimal("10.5")),
        as_of=AS_OF, sibling_product_ids=[])
    assert bench.accepted
    assert bench.value.amount == Decimal("10.00")


def test_adapter_runs_the_sibling_check_through_recorded_responses():
    served = {
        "cards/SV2a-173": recorded("ja_cards_SV2a-173.json"),
        "cards/SV2a-025": recorded("ja_cards_SV2a-025.json"),
        "sets/SV2a": recorded("ja_sets_SV2a.json"),
    }

    def fetch(url: str) -> bytes:
        key = url.split("/v2/ja/")[1]
        if key not in served:
            raise RuntimeError(f"unexpected fetch {key}")
        return json.dumps(served[key]).encode("utf-8")

    seen = TcgdexPricingAdapter(fetcher=fetch).observe(
        "SV2a-173", printing=Printing.HOLO, variant_id="v", language="ja")
    assert seen["sibling_ids"] == ["SV2a-025"]
    assert seen["sibling_product_ids"] == ["719467"]
    assert seen["observation"].product_id == "719626"
    assert len(seen["raw"]) == 3, "every payload fetched is kept for persistence"


def test_pricing_adapter_claims_only_what_it_does():
    caps = TcgdexPricingAdapter().capabilities()
    assert caps["individual_sales"] is False
    assert caps["sale_counts"] is False
    assert caps["parser_implemented"] is True


# -------------------------------------------------------- reference policy --

def test_reference_policy_matches_the_seed():
    """The CLI's database-free policy copy may not drift from seed/policy.sql."""
    sql = (ROOT / "seed" / "policy.sql").read_text(encoding="utf-8")
    rows = re.findall(
        r"\('([a-z_.]+)',\s*([0-9.]+),\s*'(\w+)',\s*'([0-9-]+)',\s*(NULL|'[0-9-]+')",
        sql,
    )
    seeded = {
        (key, Decimal(value), date.fromisoformat(start),
         None if end == "NULL" else date.fromisoformat(end.strip("'")))
        for key, value, _unit, start, end in rows
    }
    mirrored = {(r.key, r.value, r.valid_from, r.valid_to) for r in ROWS}
    assert seeded == mirrored


# ----------------------------------------------------------------- the CLI --

def _fake_ecb(monkeypatch, fx_map):
    from pokearb_core.adapters import live

    monkeypatch.setattr(live.EcbFxAdapter, "fetch_rates",
                        lambda self, on=None: list(fx_map.values()))


def _fake_tcgdex(monkeypatch, served: dict):
    from pokearb_core.adapters import live

    def fetch(url: str, timeout: int = 20) -> bytes:
        key = url.split("/v2/ja/")[1]
        return json.dumps(served[key]).encode("utf-8")

    monkeypatch.setattr(live, "_get", fetch)
    original = live.TcgdexPricingAdapter.__init__

    def init(self, policy=live.TCGDEX_POLICY, fetcher=None):
        original(self, policy, fetch)

    monkeypatch.setattr(live.TcgdexPricingAdapter, "__init__", init)


def _cli():
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    import price_check

    return importlib.reload(price_check)


def test_price_check_gives_an_indicative_ceiling(monkeypatch, capsys, fx_map):
    _fake_ecb(monkeypatch, fx_map)
    _fake_tcgdex(monkeypatch, {
        "cards/SV2a-173": recorded("ja_cards_SV2a-173.json"),
        "cards/SV2a-025": recorded("ja_cards_SV2a-025.json"),
        "sets/SV2a": recorded("ja_sets_SV2a.json"),
    })
    cli = _cli()
    # Recorded figures age; freshness is tested on its own above, so here the
    # age limit is lifted to keep the CLI test valid on any future date.
    monkeypatch.setattr(cli, "assess_market_average", lambda *a, **k: assess_market_average(
        *a, **{**k, "config": BenchmarkConfig(max_age_days=100000)}))
    code = cli.main(["SV2a-173", "--price", "1500"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "VERDICT      INDICATIVE" in out
    assert "inside the max buy" in out
    assert "OPPORTUNITY" not in out.replace("opportunity call", "")


def test_price_check_refuses_disagreeing_figures(monkeypatch, capsys, fx_map):
    _fake_ecb(monkeypatch, fx_map)
    payload = recorded("ja_cards_M2-115.json")
    set_listing = {"id": "M2", "cards": [{"id": "M2-115", "name": payload["name"]}]}
    _fake_tcgdex(monkeypatch, {"cards/M2-115": payload, "sets/M2": set_listing})
    cli = _cli()
    # Recorded figures age; freshness is tested on its own above, so here the
    # age limit is lifted to keep the CLI test valid on any future date.
    monkeypatch.setattr(cli, "assess_market_average", lambda *a, **k: assess_market_average(
        *a, **{**k, "config": BenchmarkConfig(max_age_days=100000)}))
    code = cli.main(["M2-115", "--price", "3000"])
    out = capsys.readouterr().out
    assert code == 1
    assert "INSUFFICIENT DATA" in out
    assert "disagree" in out


# ------------------------------------------------------------- Quick Check --

class _FakeRepo:
    """Everything Quick Check reads, with no European sales at all."""

    def __init__(self, fx_map, policy):
        self._fx, self._policy = fx_map, policy

    async def sales_for(self, *a, **k):
        return []

    async def active_listings_for(self, *a, **k):
        return []

    async def fx_rates(self):
        return list(self._fx.values())

    async def policy_store(self):
        return self._policy

    async def condition_prior(self, *a, **k):
        return None


class _RecordedPricing:
    def __init__(self, served: dict):
        self.served = served

    def observe(self, card_id, *, printing, variant_id, language="ja"):
        def fetch(url: str) -> bytes:
            key = url.split(f"/v2/{language}/")[1]
            if key not in self.served:
                raise RuntimeError(f"not recorded: {key}")
            return json.dumps(self.served[key]).encode("utf-8")

        return TcgdexPricingAdapter(fetcher=fetch).observe(
            card_id, printing=printing, variant_id=variant_id, language=language)


def _quick_check(pricing, fx_map, policy, price="1500"):
    sys.path.insert(0, str(ROOT / "tests"))
    from _packages import api

    deps, schemas = api("deps"), api("schemas")
    variant = CardVariant(
        variant_id="v-sv2a-173", language=Language.JA, set_code="SV2A",
        number="173", printing=Printing.HOLO, name_ja="ピカチュウ",
    )
    match = SimpleNamespace(
        best=SimpleNamespace(variant=variant, confidence=Decimal("0.99")),
        outcome=SimpleNamespace(value="auto"),
    )
    req = schemas.QuickCheckRequest(
        raw_title="ピカチュウ SV2a 173", price_jpy=Decimal(price), language="ja",
        scenario="A_hand_carry", zero_logistics_cost_is_verified=True,
    )
    ctx = deps.Context(_FakeRepo(fx_map, policy), pricing=pricing)
    return asyncio.run(ctx.assess(req, match))


def test_quick_check_falls_back_to_an_indicative_average(fx_map):
    from pokearb_core.arbitrage.reference_policy import reference_policy

    pricing = _RecordedPricing({
        "cards/SV2A-173": recorded("ja_cards_SV2a-173.json"),
        "cards/SV2a-025": recorded("ja_cards_SV2a-025.json"),
        "sets/SV2a": recorded("ja_sets_SV2a.json"),
    })
    # The recorded figures are dated 2026-09-20; the check runs "now", so pin
    # freshness by accepting any age for this offline test.
    from pokearb_core.valuation import benchmark

    original = benchmark.assess_market_average

    def lenient(obs, **kw):
        kw["config"] = benchmark.BenchmarkConfig(max_age_days=100000)
        return original(obs, **kw)

    import importlib
    from _packages import api
    deps = api("deps")
    deps.assess_market_average = lenient
    try:
        response = _quick_check(pricing, fx_map, reference_policy())
    finally:
        deps.assess_market_average = original

    assert response.verdict.value == "INDICATIVE"
    assert response.valuation_basis == BASIS
    assert response.market_average["product_id"] == "719626"
    assert response.market_average["value_used_eur"] == "28.29"
    assert response.max_buy_price_jpy is not None
    assert response.liquidity_band == "unmeasurable"
    assert any("not an opportunity call" in r for r in response.risk_factors)


def test_quick_check_stays_insufficient_when_the_average_is_refused(fx_map):
    from pokearb_core.arbitrage.reference_policy import reference_policy

    class Offline:
        def observe(self, *a, **k):
            raise RuntimeError("offline")

    response = _quick_check(Offline(), fx_map, reference_policy())
    assert response.verdict.value == "INSUFFICIENT_DATA"
    assert any("failed" in r or "no TCGdex card" in r for r in response.reasons)
