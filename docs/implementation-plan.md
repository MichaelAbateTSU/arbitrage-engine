# Implementation plan

Reviewed all three supplied attachments on 2026-09-30. The master specification
defines the deliverables; the conceptual discussion supplies the experiment;
the commercial report supplies the three-venue distinction, immutable rule
versions and honest execution-quality positioning. Its market-size, legal and
revenue assertions are not application facts and are not repeated as guarantees.

## Delivery sequence

1. Verify first-party wire schemas; establish Python/React monorepo, configuration,
   exact decimal domain, SQLAlchemy tables, explicit Alembic migrations and health.
2. Implement three public-data adapters, bounded retries and authenticated market
   streams; deterministic normalization, rule-version matches and resynchronization.
3. Implement both complementary directions, depth/fee-aware sizing, fail-closed
   risk decisions and conservative, persisted paper intents evaluated after latency.
4. Implement authenticated versioned API, audit history, SSE, daily analytics,
   worker leases/heartbeats, alerts, retention and deterministic replay.
5. Implement responsive overview, opportunities/evidence, matching, paper,
   analytics, health and settings pages, including loading/empty/error states.
6. Supply locked dependencies, tests, CI, Docker Compose, Render Blueprint,
   deployment verification, operational and incident documentation.
7. Run CI-equivalent checks, migrations and end-to-end demo; record exact observed
   public-integration and cloud-deployment status, including external blockers.

## Safety decisions

- Read-only scanner and paper execution only. Live configuration fails startup.
  Future execution interfaces fail explicitly; no fake order success.
- Three distinct venue identifiers: `kalshi`, `polymarket_us`,
  `polymarket_international`. Never circumvent geographic controls.
- Unknown rules, nonconstant cancellation payouts, unknown fees, invalid grids,
  stale/disconnected books and changed rule versions cannot qualify.
- $500 means a fee-inclusive maximum on each venue, not equal-dollar hedging.
- Five trades is a reporting target, never a forced count.
- Conservative paper intent survives restart and waits for later, fresh book
  observations. Settlement is separate from locked hypothetical profit.
- Demo and public records are isolated by source mode and deployment database.
- Deployment does not authorize changing unrelated cloud services or automatic
  commits/pushes in this user-owned checkout.

## Verification

Pure financial examples and properties; documented adapter fixtures and failure
injection; database migration/transaction/recovery tests; authenticated API tests;
client schema/component tests; browser demo flows; production asset/startup checks;
Blueprint schema validation; security audit; public endpoint smoke tests.
