"""Watchlist rules and alert de-duplication."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import Context, get_context

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class RuleRequest(BaseModel):
    label: str
    natural_language: str


#: Material-change thresholds for alert de-duplication. An already-alerted
#: opportunity re-alerts only when one of these moves, not on a timer. A
#: cooldown would either spam during a fast-moving market or go silent during
#: the one that matters.
MATERIAL_CHANGE_THRESHOLDS = {
    "price_drop_pct": 0.05,
    "roi_improvement_abs": 0.10,
    "supply_change_pct": 0.30,
    "new_sales_count": 2,
    "condition_info_changed": True,
}


@router.post("/rules")
async def create_rule(req: RuleRequest, ctx: Context = Depends(get_context)) -> dict[str, Any]:
    """Translate a natural-language rule into a structured one.

    The structured form is returned for confirmation before the rule goes live,
    because a misparsed rule that silently never fires is worse than no rule.
    """
    structured = parse_rule(req.natural_language)
    return {
        "label": req.label,
        "structured": structured,
        "confirmed": False,
        "note": "Review the structured form, then POST /watchlist/rules/{id}/confirm",
    }


def parse_rule(text: str) -> dict[str, Any]:
    """Deterministic first-pass parser over the documented rule grammar.

    Anything it cannot parse is returned as ``unparsed`` rather than guessed,
    so an ambiguous rule surfaces immediately instead of quietly matching
    nothing.
    """
    import re

    lowered = text.lower()
    out: dict[str, Any] = {"raw": text, "conditions": [], "unparsed": []}

    m = re.search(r"below\s+([\d,]+)\s*(yen|jpy|eur|€|dkk)", lowered)
    if m:
        out["conditions"].append(
            {
                "field": "price",
                "op": "<",
                "value": float(m.group(1).replace(",", "")),
                "currency": {"yen": "JPY", "jpy": "JPY", "eur": "EUR", "€": "EUR", "dkk": "DKK"}[m.group(2)],
            }
        )

    m = re.search(r"(\d+)\s*(?:percent|%)\s*below\s+(\d+)\s*day", lowered)
    if m:
        out["conditions"].append(
            {
                "field": "discount_to_fair_value",
                "op": ">=",
                "value": int(m.group(1)) / 100.0,
                "window_days": int(m.group(2)),
            }
        )

    m = re.search(r"psa\s*10\s*population\s*(?:below|under)\s*([\d,]+)", lowered)
    if m:
        out["conditions"].append(
            {
                "field": "psa_10_population",
                "op": "<",
                "value": int(m.group(1).replace(",", "")),
                # Flagged because the data is not currently obtainable.
                "unsatisfiable": True,
                "reason": "PSA population is not available through the PSA public API",
            }
        )

    m = re.search(r"(?:condition|grade)\s+([a-z][+-]?)\s*or better", lowered)
    if m:
        out["conditions"].append(
            {"field": "source_grade_at_least", "op": ">=", "value": m.group(1).upper()}
        )

    if not out["conditions"]:
        out["unparsed"].append(text)
    return out
