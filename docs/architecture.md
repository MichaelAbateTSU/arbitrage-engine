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
