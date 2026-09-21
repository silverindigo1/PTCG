"""Card search, detail and full provenance."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import Context, get_context

router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    language: Optional[str] = None,
    set_code: Optional[str] = None,
    number: Optional[str] = None,
    ctx: Context = Depends(get_context),
) -> dict[str, Any]:
    from pokearb_core.identity.matcher import AliasIndex, ObservedCard, match_card
    from pokearb_core.types import Language

    observed = ObservedCard(
        raw_title=q,
        language=Language(language) if language else None,
        set_code=set_code,
        number=number,
        name=q,
    )
    universe = await ctx.repo.candidate_variants(
        language=observed.language, set_code=set_code, number=number
    )
    aliases = AliasIndex(await ctx.repo.alias_map())
    result = match_card(observed, universe, aliases)
    return {
        "outcome": result.outcome.value,
        "results": [
            {
                "variant_id": c.variant.variant_id,
                "canonical_key": c.variant.canonical_key,
                "set_code": c.variant.set_code,
                "number": c.variant.number,
                "printing": c.variant.printing.value,
                "edition": c.variant.edition.value,
                "stamp": c.variant.stamp,
                "confidence": str(c.confidence),
                "penalties": list(c.penalties),
            }
            for c in result.considered[:25]
        ],
        # Rejections are returned too. "We looked and ruled these out, here is
        # why" is more useful than a silently shorter list.
        "rejected": [
            {
                "variant_id": c.variant.variant_id,
                "gate_failures": list(c.gate_failures),
            }
            for c in result.rejected[:25]
        ],
    }


@router.get("/{variant_id}/fair-value")
async def fair_value(variant_id: str, ctx: Context = Depends(get_context)) -> dict[str, Any]:
    """Fair value across all windows, with the sales behind each number."""
    from datetime import datetime, timezone

    from pokearb_core.adapters.live import EcbFxAdapter
    from pokearb_core.valuation.fair_value import compute_fair_value_curve

    sales = await ctx.repo.sales_for(variant_id)
    fx = EcbFxAdapter.as_map(await ctx.repo.fx_rates())
    curve = compute_fair_value_curve(
        sales, variant_id=variant_id, as_of=datetime.now(timezone.utc), fx_rates=fx
    )
    return {
        window: {
            "value": str(fv.value.amount) if fv.value else None,
            "currency": fv.value.currency.value if fv.value else None,
            "sufficient": fv.sufficient,
            "n_sales": fv.n_sales,
            "n_effective": str(fv.n_effective),
            "dispersion": str(fv.dispersion) if fv.dispersion is not None else None,
            "method": fv.method,
            "notes": list(fv.notes),
            "inputs": [
                {
                    "sale_id": r.record_id, "source": r.source_id,
                    "sold_at": r.observed_at.isoformat(),
                    "weight": str(r.weight), "url": r.source_url, "detail": r.note,
                }
                for r in fv.inputs
            ],
            "excluded": [
                {"sale_id": r.record_id, "reason": r.note, "url": r.source_url}
                for r in fv.excluded
            ],
        }
        for window, fv in curve.items()
    }


@router.get("/{variant_id}/population")
async def population(variant_id: str, ctx: Context = Depends(get_context)) -> dict[str, Any]:
    """Grade ladder, or an honest statement that it is unavailable."""
    return {
        "variant_id": variant_id,
        "known": False,
        "reason": (
            "PSA population data is not exposed by the PSA Public API, whose "
            "documented method set is cert verification by cert number only. "
            "No population figure is estimated."
        ),
        "source": "https://www.psacard.com/publicapi/documentation",
    }
