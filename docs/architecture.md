# Architecture

One typed Python domain, three venue wire boundaries, PostgreSQL source of truth,
and separately deployable API, market-data, analysis and maintenance processes.
React is a same-origin SPA; Render's optional frontend service proxies its API/SSE
to the API over private networking. Redis is optional, not required for coordination.

## Data flow

1. Discovery paginates and retains public raw metadata. A complete sweep may mark
   removed markets unavailable; partial sweeps never pretend an empty universe.
2. Normalization retains unknown fields. Operator annotations bind raw public
   specification hashes; changed rules, identity or schedule invalidate an
   annotation and all old approvals.
3. Matching persists versioned specs, candidate explanations and canonical events.
4. Snapshots/deltas update current books and short-retention immutable snapshots.
5. Analysis consumes approved pairs and current books. Every calculation records
   book/rule/fee/risk versions and exact inputs. Rejections have reason codes.
6. Paper reservation locks the persisted risk row and rechecks current books,
   limits, event exclusivity and idempotency, then commits a submitted intent.
7. Later observations simulate protected IOC-style concurrent legs. State and leg
   records commit together with audit/critical unhedged alerts.
8. API exposes scoped records, analytics and update notifications. SSE prompts
   refetches; PostgreSQL remains the durable source, not browser state.
9. Independent validation evaluates every pair in both directions, even when
   unapproved, retaining all rule/book/fee/cost/account blockers. Latest
   diagnostics, rule-versioned episodes, eligibility evidence and configuration
   use dedicated PostgreSQL tables. Repeated observations do not become trades.
10. Virtual shadow reservations recheck current approvals and fresh source
    versions transactionally. Delayed fills, rejected-second-leg and partial-fill
    stress cases have distinct persisted intents. Bid-side emergency unwinds
    obey lot and minimum constraints; remaining hedges/residuals retain capital
    until both public final settlements. No order endpoint is called.

## Eligibility and validation evidence

Public market access, authenticated GET-only buying power, operator attestations
and order permission are different facts. International Gamma/CLOB and the
Polymarket US gateway/account API are separate products. US/GA operator location
is explicit configuration, not the Render host region. International US
close-only status cannot be attested away. No live execution exists even for a
fully evidenced account.

Public shadow qualification always requires independent settlement review plus
verified additional-cost assumptions. Scenario proofs cover each team win and
applicable draw/void outcomes; unknowns and fair-price cancellations fail closed.
Diagnostics distinguish financial/settlement, operator-execution and shadow
blockers. Fees and additional costs reduce both profit and available sizing.

The bounded watchlist preferentially selects same-currency pairs with observed
depth, without approval. Collection defaults to 14 days, $500 virtual balances,
$25 virtual leg caps, ten baseline trials/day and a $25 daily loss cutoff.
The combined capital cap is conservatively reserved against both participating
virtual wallets, including aggregate Kalshi exposure across Polymarket products.
Realized losses reduce available wallets; projected profits never replenish them.
Baseline and injected stress results remain separate, with P&L basis labels,
aggregate trial-seconds, capital-weighted USD-seconds and evidenced operating
costs. An elapsed diagnostic window never enables trading.

```mermaid
sequenceDiagram
  participant D as Data worker
  participant DB as PostgreSQL
  participant A as Analysis
  participant UI as API / UI
  D->>DB: validated current book + immutable snapshot
  A->>DB: read approved rule versions and fresh books
  A->>DB: save reproducible opportunity
  A->>DB: lock risk; reserve; persist intent
  D->>DB: post-latency observation
  A->>DB: persist two-leg fill result and audit
  UI->>DB: read evidence and hypothetical metrics
```

## Failure boundaries

Sequence gaps, malformed frames, disconnects and lifecycle changes invalidate the
affected feed's books. Pricing resumes only from a new snapshot. Cached books
always carry their receive/source timestamps; restarts never refresh stale data
by assigning new timestamps. Kalshi sequence continuity is subscription-scoped,
not erroneously market-scoped.

Workers own database leases and heartbeat every five seconds with 30-second
expiration. Only the owner renews/releases a lease. Risk and paper reservations
are database transactions; network requests never occur inside them.
Paper evaluation has its own fast task within the analysis role so a long
multi-market detection sweep cannot defer due intents. Daily analytics selects
small scalar projections, not every archived raw calculation/book payload.
Stream reconstruction is independent of database persistence: every delta is
applied in sequence, then latest full normalized books are coalesced in a bounded
per-market/outcome buffer and flushed at 100ms cadence. Database latency cannot
stall socket heartbeats; original receive/exchange timestamps are retained.

Focused validation persists versioned policy-family screens, per-instrument
monitoring and daily observation coverage in three additional record tables.
Family classification never creates individual approval. Scenario matrices
separate normal winners, ties, postponement windows, cancellation/void and
discretionary decisions; unknown bounds and independent fair-price envelopes
remain unproven. The pricing view shows conditional profit, bounded-scenario
net floor and all-scenario net floor separately.

Socket callbacks only update bounded in-memory monitoring queues; independent
writers persist selection/subscription/reconstruction evidence. Feed generations
and integrity epochs prevent credit across short lifecycle interruptions even
when the latest valid book coalesces past an invalid intermediate state.
Focused REST probes are diagnostic evidence, never unsequenced book replacements.
The coverage task reads only selected matches/markets/books and intersects
adjacent samples with actual freshness/evidence deadlines. Daily pair-seconds and
distinct funded-size spread windows survive restarts, preserve the original
diagnostic start and do not imply atomic execution or automatic live permission.

Application readiness checks PostgreSQL migration version and optional configured
Redis; venue outages remain separately visible. Detailed snapshots expire after
seven days, rejections/observations/log events after 180 days, daily aggregates
remain. Opportunities, paper evidence and audit records are not silently deleted.

## Scaling

This bounded implementation prioritizes correctness over HFT throughput. Pricing
caps the monitored universe, uses short poll/SSE refresh intervals and archives
each normalized observation. Scale by partitioning roles/venue universes and
moving high-rate archival records to object storage only after measured need.
Do not add Kafka/Kubernetes before a demonstrated bottleneck.
