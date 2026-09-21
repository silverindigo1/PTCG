"""PokeArb API.

Quick Check is the highest-priority endpoint in the whole product: it is the
one used standing in a shop with one hand on a phone. Its contract is
therefore stricter than the others.

* It answers from cache when the cache is fresh, and refreshes in the
  background otherwise, so the response does not wait on a slow source.
* It never invents a field. Every number is either measured, or returned as
  ``null`` with a reason attached.
* It returns a verdict, not just data. ``STRONG_OPPORTUNITY``, ``OPPORTUNITY``,
  ``PASS`` or ``INSUFFICIENT_DATA``. ``INSUFFICIENT_DATA`` is a real answer and
  is deliberately not merged into ``PASS``: "I do not know" and "no" call for
  different behaviour at a counter.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .deps import Context, get_context
from .schemas import QuickCheckRequest, QuickCheckResponse, Verdict
from .routers import cards, opportunities, watchlist

app = FastAPI(
    title="PokeArb",
    version="0.1.0",
    description=(
        "Autonomous Pokemon TCG market intelligence. Private by default. "
        "Every number is traceable to source data or returned as unknown."
    ),
)


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """Private by default. There is no anonymous access path."""
    expected = os.environ.get("POKEARB_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="POKEARB_API_KEY is not configured. Refusing to serve unauthenticated.",
        )
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key.")


app.include_router(cards.router, dependencies=[Depends(require_api_key)])
app.include_router(opportunities.router, dependencies=[Depends(require_api_key)])
app.include_router(watchlist.router, dependencies=[Depends(require_api_key)])


@app.get("/health")
async def health(ctx: Context = Depends(get_context)) -> dict[str, Any]:
    """Health includes per-source status, because degraded is not down.

    If a source has failed, the system keeps running and marks the affected
    data stale rather than substituting anything.
    """
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "sources": await ctx.source_status(),
    }


@app.post(
    "/quick-check",
    response_model=QuickCheckResponse,
    summary="Stand in a Japanese shop, type a price, get a verdict",
)
async def quick_check(
    req: QuickCheckRequest,
    ctx: Context = Depends(get_context),
    _: None = Depends(require_api_key),
) -> QuickCheckResponse:
    """Full assessment for one card at one Japanese asking price.

    Ordering matters here. Identity is resolved first and the request stops if
    it cannot be resolved, because everything downstream is meaningless
    otherwise. That is the single most important line in this file.
    """
    match = await ctx.resolve_identity(req)
    if match.outcome.value != "auto_match":
        return QuickCheckResponse(
            verdict=Verdict.INSUFFICIENT_DATA,
            headline="Card not identified with enough confidence to price",
            match_confidence=match.best.confidence if match.best else Decimal("0"),
            match_outcome=match.outcome.value,
            candidates=[
                {
                    "variant_id": c.variant.variant_id,
                    "canonical_key": c.variant.canonical_key,
                    "confidence": str(c.confidence),
                    "penalties": list(c.penalties),
                }
                for c in match.considered[:5]
            ],
            reasons=list(match.explanation),
        )

    return await ctx.assess(req, match)
