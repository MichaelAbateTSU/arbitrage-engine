# Live trading is unavailable

This release installs only `DisabledExecution`. Every attempted order/cancel/status
operation raises `LIVE_EXECUTION_NOT_IMPLEMENTED`. All live/execution flags and
`TRADING_MODE=live` fail startup, even with credentials and a deactivated paper
kill switch. No frontend field or server activation string overrides this.

A future implementation must independently obtain legal/venue/geographic
eligibility, separate live identities/secrets, explicit server-side activation,
preview exclusion, balance/position limits, kill switches and operator confirmation.
The deliberately inactive confirmation text from the specification is not an
active example environment value.

The order protocol should persist intent and unique idempotency key before venue
submission; persist responses; query/reconcile uncertain submissions before any
retry; retain append-only decisions; track partial fills; cancel remaining sizes;
and hedge only under explicit emergency bounds. Venue guarantees must be verified,
not faked by optimistic SDK behavior. Account/fill/portfolio methods are not
implemented in this read-only release; they must not return pretend data.

Cross-venue trading is non-atomic. An exchange timeout can coexist with a filled
order. Restart, reconciliation, legal clearance and continuous monitoring are
requirements, not optional wrappers around a price-comparison script.
