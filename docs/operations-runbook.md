# Operations runbook

## Startup / shutdown

Migrate first; API checks schema and dependencies; launch each worker role.
Worker leases expire after 30 seconds and are renewed every five. Use normal
process termination; graceful cancellation releases leases and closes sockets.
Unexpected restarts retain settings, snapshots and paper intents.

## Venue outage / reconnects

Inspect `system/health`, `system/venues`, JSON error codes, reconnect counts and
last-book/discovery timestamps. Book corruption or a missing sequence invalidates
the feed. Reconnection requests authoritative snapshots and re-subscribes.
Do not refresh a book by manually changing timestamps or weakening quote-age limits.
Rate limits back off with jitter; no infinite request retries.

Unknown fields and fee types remain unavailable, not assumed healthy/zero.
Partial discovery does not close all markets. API application readiness can
remain healthy while a venue is unusable.

## Database failure / worker missing

Enable the paper kill switch when reachable. Fix PostgreSQL availability or the
deployment environment; inspect schema revision and worker errors. Never replace
the production database with an automatically generated schema.
Missing worker heartbeat creates a critical in-app alert and an unhealthy status.
External paging is not configured in this release; operators must monitor health.

## Paper unhedged

Review both simulated leg quantities, protected limits, consumed depth and timing.
An UNHEDGED state triggers a critical alert and cooldown. Keep the exposure visible;
do not count it as locked profit or erase/recreate its intent to achieve a daily
target. No actual order or emergency hedge is sent.

## Kill switch

Settings > Activate paper kill switch, or authenticated/CSRF-protected
`POST /api/v1/settings/kill-switch/activate`. Pending simulations cancel; committed
historical exposure and audit evidence remain. Deactivation never enables live.

## Retention / export

Maintenance computes daily aggregates before short-retention snapshots/operational
observations expire. Keep opportunities, paper evidence and audits.
`scripts/export_opportunities.py` exports JSONL; `--books` exports retained books.
Record export source mode and software version; do not call incomplete samples a
full backtest. Verify backups/restore behavior in an isolated database.

## Credential rotation

Rotate operator hash/session secret, revoke compromised venue data keys at their
source, replace worker-only secrets and restart feeds. Never log credential
headers. See SECURITY.md and incident response.

## Opportunity validation

Migrate to `93ad7c201b46` before deploying focused-validation workers. Keep live execution
disabled and the ordinary paper kill switch active. The independent shadow
engine is separately enabled/paused through its audited configuration and uses
only virtual balances; the paper kill switch does not stop diagnostic collection
or virtual shadow trials.

Use **Opportunity validation** to inspect each direction's depth-adjusted asks,
priced quantity, book age, profit and every blocker. Filter counts are distinct
pairs, not sums of book updates. Account-read success is not permission to trade.
Review the persisted watchlist's outcomes, deadlines, resolution sources,
overtime, draw, cancellation and postponement evidence. Approve only when the
scenario matrix is complete and the independent review is documented.

Review at most 20 policy families before allocating book monitoring. The automatic
focus selects up to five distinct events initially (configurable to 5-10).
Known incompatible/independent fair-price families are not called promising.
If no potentially eligible family has candidate pairs, the non-close-only sample
is explicitly diagnostic-only. Inventory-only family samples are not event pairs.
Historical watchlist IDs are retained; current selections refresh after rule/hash
changes without changing `collection_started_at`.

For each selected instrument inspect cap selection, actual subscription request,
confirmation/rejection, mapping evidence, generation, reconstruction and timestamp.
Public REST probes distinguish endpoint availability from missing/stale stream
snapshots, but never overwrite sequenced streams or freshen exchange timestamps.
Subscription status is not inferred from venue-level connected health.
Valid existing focused targets are retained when unrelated families appear.
A two-second target-signature watcher detects selection or rule-version changes
and requests a new authoritative subscription immediately rather than waiting
five minutes for periodic rediscovery. Reconfiguration invalidates old books;
it never extends freshness or claims a continuous interval across the restart.

Enter funding/conversion/withdrawal/settlement/rebalancing/network components as
`verified_amount`, `verified_zero`, `not_applicable` or `unknown`, with amount,
allocation basis, execution path, evidence reference and explicit expiry.
Amounts allocate per filled contract or applicable executed leg; components
specify opening, emergency unwind, or both. Settlement expenses are planned
opening/holding allocations, not charged again on a sale. Fee models
remain separate. Do not use a generic USD quote or fee page to certify an unknown
funding method. Legacy aggregate amounts remain in historical data and numerical
pricing but cannot substitute for six-component proof. Nonzero legacy and new
component amounts cannot coexist. Enter daily operating costs separately.
Unknown/expired cost evidence blocks qualification and delayed public shadow fills.
Account attestations expire; international US close-only restrictions cannot be
overridden. Never paste secrets in any evidence field.

Kalshi also checks GET `/trade-api/v2/api_keys`: only the current key's sanitized
trade-scope/binding/region-expiry verdict is retained. No key ID, key name or raw
key response is stored. Restricted subaccount/institutional bindings cannot
certify this pipeline's primary buying power. Write scope is not KYC/venue-account
permission. The US retail balance endpoint has no documented account-permission
field; institutional identity endpoints are not substituted and no preview/order
is sent to test access.

Inspect `/api/v1/validation/summary`, `/focused`, `/candidates`, `/episodes` and
`/shadow-trials` under the normal authenticated reader policy.
Configuration updates require admin/CSRF, current revision and an audit reason.
After 7-14 days, compare distinct episodes, available coverage, baseline versus
injected stress, unsettled versus observed-settlement P&L, failure losses and
capital lockup after operating costs. Daily coverage counters report aggregate
pair-seconds, not elapsed calendar days: approved, usable books, approved+priced,
cost-verified and funded/eligible stages. Sampling is independent of broad
diagnostic sweeps. Intervals require adjacent <=2-second samples, unchanged
evidence/generation/integrity epoch and original quote/fee/cost/account expiry.
Outages, superseded proofs, failed read probes and stale/absent books receive no
qualified credit. Earlier coverage remains unknown, never reconstructed from
wall time. Funded-size positive windows are distinct per direction with the
configured episode-gap deduplication; they are hypothetical decisions, not fills.
Observe positive and nonqualifying spread time separately. Missing evidence or zero profitable
episodes does not justify relaxing limits. No timer or result enables live mode.
