# ADR 0002: the Europe Steal Finder does not continuously poll Cardmarket

Status: accepted, 2026-09-20

## Context

The brief asks for an engine that continuously scans European marketplaces for
underpriced listings. Cardmarket is the main European venue. Its API terms,
read during Phase 1 at
https://api.cardmarket.com/ws/documentation/API:Auth_Overview, restrict access
to professional sellers with manual approval, and state that a Dedicated App
may not constantly request only the public Marketplace resources on consecutive
days. A broad continuous scanner is exactly that pattern.

## Decision

The Cardmarket adapter is implemented, tested and shipped disabled. It refuses
the continuous public-marketplace polling pattern in code rather than relying on
an operator remembering the terms. The default compliant design, documented in
`docs/01-data-sources.md`, is a narrow watchlist-scoped poll over cards the user
has explicitly registered, at low frequency.

## Consequences

Europe-wide steal discovery is not available at launch. Watchlist-scoped
discovery is. This is a deliberate reduction in scope rather than a missing
feature: building the broad scanner first would produce a system that works
until the account is revoked, and then produces nothing at all.
