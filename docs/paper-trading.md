# Paper execution and replay

The default conservative model models concurrent IOC-style protected buys, not
atomic cross-exchange execution. A committed intent includes quantity, limits,
due timestamp, fee/risk snapshot, reserved capital and unique opportunity ID.

State progression is CREATED -> READY -> SUBMITTED, then HEDGED, UNHEDGED,
FAILED, EXPIRED or CANCELLED. Verified two-venue settlement can move HEDGED or
UNHEDGED to SETTLED. Each transition has time/from/to evidence and an audit record;
legs commit with their parent state. Invalid transitions raise.

After the configured latency, **both** fresh observations must have been received
at or after the intent due time. No reusing the detection book while pretending
latency elapsed. Current executable asks must satisfy the saved limits; a
REST observation is conservatively dated at request start, not delayed receipt,
so slow responses cannot masquerade as fresh post-latency liquidity. A
configurable liquidity haircut and extra slippage make the result conservative.
Separate legs can fill partially or fail. Unequal quantities explicitly create
UNHEDGED exposure and a critical in-app alert, not a profitable trade.
Exceeding the configured maximum unhedged quantity also activates the persisted
paper circuit breaker (default maximum zero).

Optimistic mode uses detection-time books and is labeled separately. Observed
mode deliberately reports `OBSERVED_MODEL_REQUIRES_TRADE_AND_QUEUE_EVIDENCE`;
this release cannot honestly infer queue execution from sampled L2 alone.

The paper kill switch cancels pending submissions but never erases historical
exposure. Changed rule versions, unknown expired fees, closed markets and stale
post-latency data fail closed. Active event reservations prevent duplicated
liquidity use across repeated updates. Restart reads persisted submitted intents
and waits for actual later observations, rather than simulating a fresh decision.

Locked hypothetical profit and actual hypothetical settlement are distinct.
Reserved bankroll stays committed until both verified settlement values exist.
Maintenance polls held public positions even after markets leave active discovery:
Kalshi terminal published YES payout, US's dedicated settlement endpoint, and
international Data API v2's resolved per-outcome micro-USDC payout vector. Missing
or preliminary resolution does not release capital. Verified hypothetical
settlement gains/losses adjust the starting paper bankroll; scalar Kalshi outcomes
outside 0/0.5/1 remain pending for settlement-fee reconciliation.
Cancelled/delayed/one-sided exposure is never silently released as a success.
This release does not assume liquidity for emergency live hedges.

Daily reports use UTC, fee/latency/slippage impacts and rejection reasons. Expected,
simulated locked and settlement results are separately labeled. No annualization.

Replay uses retained normalized snapshots, saved matching/fee/fill/risk/software
versions and a source-data fingerprint. It does not create additional bankroll
positions. Insufficient retained timing evidence is `REPLAY_DATA_INCOMPLETE`.
CLI JSONL replay supports speed/step/pause-by-step. It is historical replay, not
a predictive backtest: maker queue identity, sampling loss and survivorship
bias can invalidate fill inference.
