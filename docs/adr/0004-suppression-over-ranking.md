# ADR 0004: failing opportunities are suppressed, not ranked low

## Status
Accepted.

## Context
The brief's governing principle is that a false arbitrage signal is worse than
a missed trade. The obvious implementation is to fold data quality, liquidity
and match confidence into a composite score and let weak opportunities sink.

That does not work in the environment this tool is used in. The user is in a
shop, on a phone, with one hand free, under time pressure, possibly with a
queue behind them. A weak opportunity at rank 40 is still tappable, still
renders the same confident-looking card, and still shows an ROI.

## Decision
Hard gates run before scoring. An opportunity failing any of them is
suppressed: it does not appear in lists, and requesting it directly returns
`INSUFFICIENT_DATA` with the reasons. The gates are match confidence, fair
value sufficiency, data quality, liquidity, condition confidence and staleness.

Ranking among survivors uses the 25th percentile of simulated net ROI rather
than the point estimate, so residual weakness still costs rank.

## Consequences
The tool will show fewer opportunities than a competitor, including some real
ones. That is the trade the brief asked for, stated explicitly.

The gates are visible in `RiskConfig` and can be loosened deliberately, which
is different from them quietly not existing. `include_suppressed=true` exposes
suppressed items with their reasons for debugging, and is off by default.

The condition-confidence floor is set deliberately low (0.08). A Japanese shop
grade genuinely spans several European grades, and that spread is already paid
for twice, in the probability-weighted value and in the simulation haircut.
Gating it at a normal level would suppress every shop-graded card and defeat
the core use case. It catches only the near-uniform case where the distribution
says nothing.
