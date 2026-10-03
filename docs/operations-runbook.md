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

Migrate to `6e9f3a2c7d10` before deploying validation workers. Keep live execution
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

Enter actual per-venue settlement/rebalancing/fixed-cost assumptions with
provenance and daily operating costs separately. Unknown costs stay blocked.
Account attestations expire; international US close-only restrictions cannot be
overridden. Never paste secrets in any evidence field.

Inspect `/api/v1/validation/summary`, `/candidates`, `/episodes` and
`/shadow-trials` under the normal authenticated reader policy.
Configuration updates require admin/CSRF, current revision and an audit reason.
After 7-14 days, compare distinct episodes, available coverage, baseline versus
injected stress, unsettled versus observed-settlement P&L, failure losses and
capital lockup after operating costs. Missing evidence or zero profitable
episodes does not justify relaxing limits. No timer or result enables live mode.
