# ADR 0003: unknown is a value, and it is not zero

Status: accepted, 2026-09-20

## Context

PSA population is unavailable from the official API. Several Japanese sources
are unverified. Some policy parameters, such as the customs duty rate for the
relevant commodity code, were not confirmed. A system that treats any of these
as zero, null-coalesces them to a default, or silently drops them from a
calculation will produce arbitrage numbers that look complete and are wrong.
Under the stated principle that a false signal is worse than a missed trade,
that failure mode is the expensive one.

## Decision

Three mechanisms, all enforced in code rather than convention:

1. `UNKNOWN` is a distinct sentinel in `types.py`, separate from `None`. `None`
   means not applicable; `UNKNOWN` means not measured.
2. `PolicyStore.resolve` raises `PolicyUnverifiedError` for a parameter flagged
   `requires_verification`. The arbitrage engine does not compute a duty-bearing
   case rather than assuming a zero tariff.
3. The API exposes `INSUFFICIENT_DATA` as a verdict distinct from `PASS`, and
   every displayed figure is a `Traceable` carrying `known`, a reason and source
   links.

## Consequences

The system refuses to answer more often than a naive one would. Quick Check can
return "I do not know" while standing at a counter. That is the intended
behaviour: at a counter, "I do not know" and "no" call for different actions,
and a fabricated number calls for the wrong one.
