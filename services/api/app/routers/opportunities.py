"""Opportunity lists and the daily report."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from ..deps import Context, get_context

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("")
async def list_opportunities(
    kind: Optional[str] = Query(None),
    min_roi: float = Query(0.40, ge=0),
    min_liquidity: float = Query(25.0, ge=0),
    include_suppressed: bool = Query(
        False, description="Suppressed opportunities are hidden by default, on purpose"
    ),
    limit: int = Query(50, le=200),
    ctx: Context = Depends(get_context),
) -> dict[str, Any]:
    rows = await ctx.repo._rows(
        """
        SELECT DISTINCT ON (o.opportunity_id)
               o.opportunity_id, o.kind, o.variant_id, s.*
          FROM opportunity o
          JOIN opportunity_snapshot s ON s.opportunity_id = o.opportunity_id
         WHERE o.closed_at IS NULL
           AND ($1::text IS NULL OR o.kind = $1)
           AND (NOT $2 OR TRUE)
         ORDER BY o.opportunity_id, s.computed_at DESC
         LIMIT $3
        """,
        kind, include_suppressed, limit,
    )
    items = [
        {
            "opportunity_id": str(r["opportunity_id"]),
            "kind": r["kind"],
            "variant_id": str(r["variant_id"]),
            "suppressed": r["suppressed"],
            "suppression_reasons": list(r["suppression_reasons"] or []),
            "ranking_roi": str(r["ranking_roi"]) if r["ranking_roi"] is not None else None,
            "point_roi": str(r["point_roi"]) if r["point_roi"] is not None else None,
            "max_buy_jpy": str(r["max_buy_jpy"]) if r["max_buy_jpy"] is not None else None,
            "liquidity_score": str(r["liquidity_score"]) if r["liquidity_score"] is not None else None,
            "data_quality_score": str(r["data_quality_score"]) if r["data_quality_score"] is not None else None,
            "computed_at": r["computed_at"].isoformat(),
            "explanation": r["explanation"],
        }
        for r in rows
    ]
    if not include_suppressed:
        items = [i for i in items if not i["suppressed"]]
    items = [
        i for i in items
        if i["ranking_roi"] is not None and float(i["ranking_roi"]) >= min_roi
        and (i["liquidity_score"] is None or float(i["liquidity_score"]) >= min_liquidity)
    ]
    items.sort(key=lambda i: float(i["ranking_roi"] or 0), reverse=True)
    return {"count": len(items), "items": items}


@router.get("/daily-report")
async def daily_report(ctx: Context = Depends(get_context)) -> dict[str, Any]:
    """Concise daily report.

    If there is nothing meaningful, it says so rather than padding. A report
    that always has content trains you to stop reading it.
    """
    data = await list_opportunities(ctx=ctx)
    if data["count"] == 0:
        return {
            "generated_at": None,
            "summary": "No opportunities met the evidence, liquidity and return thresholds today.",
            "sections": {},
        }
    return {
        "summary": f"{data['count']} opportunity(ies) cleared all gates.",
        "sections": {"japan_arbitrage": data["items"][:10]},
    }
