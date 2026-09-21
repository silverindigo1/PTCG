# ADR 0001: the core engines perform no I/O

## Status
Accepted.

## Context
The brief requires backtesting with no look-ahead bias, and warns that models
must only ever see what they knew at the time. Look-ahead bias is usually
prevented by discipline: reviewers check that a backtest does not read a table
it should not. Discipline fails silently and the failure looks like a good
model.

## Decision
`pokearb_core` takes plain value objects and returns plain value objects. It
imports no database driver, no HTTP client and no clock beyond what is passed
in as `as_of`. All I/O lives in the API's repository layer or in adapters.

## Consequences
Positive: look-ahead bias becomes structurally impossible rather than a review
item. The backtester replays observation tables up to a cut-off and calls the
same functions production calls. Tests need no database, so the suite runs on a
clean checkout in under a second, which is why people actually run it.

Negative: the repository layer does more translation work, and a query that
could have been pushed into SQL sometimes happens in Python. Accepted: the data
volumes here are small and the correctness guarantee is worth more than the
query optimisation.

Also rejected as a consequence: the suggested Next.js plus Python split. A
TypeScript API in front of a Python statistical core would add a serialisation
boundary and a second place for monetary arithmetic to go wrong.
