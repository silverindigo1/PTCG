"""Every adapter must state what it can actually do.

An interface that exists is not an integration. These tests exist because the
previous version claimed, in a docstring, that an eBay parser was implemented
and fixture-tested when neither was true. A claim that nobody checks drifts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

from pokearb_core.adapters.gated import (  # noqa: E402
    CardmarketAdapter,
    EbaySoldAdapter,
    PriceChartingAdapter,
    PsaCertAdapter,
)
from pokearb_core.adapters.live import (  # noqa: E402
    EcbFxAdapter,
    TcgdexCatalogueAdapter,
    TcgdexPricingAdapter,
)

GATED = [EbaySoldAdapter(), PsaCertAdapter(), CardmarketAdapter(), PriceChartingAdapter()]


@pytest.mark.parametrize("adapter", GATED, ids=lambda a: type(a).__name__)
def test_gated_adapters_declare_parser_and_access_state(adapter):
    caps = adapter.capabilities()
    assert "parser_implemented" in caps, (
        "an adapter must say whether a response parser exists, separately from "
        "whether the remote API offers the capability"
    )
    assert "verified_live_access" in caps
    assert caps["verified_live_access"] is False, (
        "no gated source has had live access verified; claiming otherwise in "
        "code is how a stub becomes an 'integration' in a status report"
    )


def test_ebay_sold_declares_no_parser_and_raises_with_the_documentation_url():
    adapter = EbaySoldAdapter()
    assert adapter.capabilities()["parser_implemented"] is False
    with pytest.raises(Exception) as exc:
        adapter.fetch_sales("pikachu", since=None)  # type: ignore[arg-type]
    assert "marketplace-insights" in str(exc.value) or "disabled" in str(exc.value).lower()


def test_psa_cert_declares_no_parser():
    assert PsaCertAdapter().capabilities()["parser_implemented"] is False


def test_pricecharting_parser_is_claimed_and_actually_works():
    """The one gated adapter whose parser genuinely exists, so it is exercised."""
    adapter = PriceChartingAdapter()
    assert adapter.capabilities()["parser_implemented"] is True
    translated = adapter.interpret(
        {"loose-price": 1200, "manual-only-price": 45000, "unrelated": 1}
    )
    assert "unrelated" not in translated
    assert any("PSA 10" in k or "ungraded" in k.lower() for k in translated)


def test_live_adapters_are_the_only_ones_enabled():
    for adapter in (EcbFxAdapter(), TcgdexCatalogueAdapter(), TcgdexPricingAdapter()):
        assert adapter.policy.enabled is True
    for adapter in GATED:
        assert adapter.policy.enabled is False, (
            f"{type(adapter).__name__} must ship disabled until its compliance "
            "review is on file"
        )



def test_the_price_adapter_does_not_pass_averages_off_as_sales():
    """Verified live access, yes. Sale-level data, no. Both are stated."""
    caps = TcgdexPricingAdapter().capabilities()
    assert caps["verified_live_access"] is True
    assert caps["individual_sales"] is False
    assert caps["sale_counts"] is False
