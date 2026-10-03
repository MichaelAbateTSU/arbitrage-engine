# Implementation and verification report

Initial implementation: 2026-09-30. Production deployment verified: 2026-10-02.

## 1. Executive summary

The repository now contains an implemented, locally verified read-only sports
market and paper-execution platform, not a price-comparison mock. The initial
repository contained only README and .gitignore. All three original attachments
were reviewed; the later attachment was byte-identical to the master specification.
The implementation plan preceded the code.

**Render deployment is complete.** After owner authorization and publication,
the paid API/dashboard, three always-on background workers, and private managed
PostgreSQL were provisioned in Ohio. Actual cloud health, database migrations,
worker heartbeats and all three production market-data feeds were verified.
The scanner uses public data in paper-only mode; no real order was submitted.

## 2. Implemented capabilities

Three public venue adapters; normalization and curated/operator aliases; immutable
source/rule versions; deterministic matching and review; snapshot/delta books;
Decimal fee/depth calculations; risk and currency gates; persisted conservative
paper intents, fills, partial exposure, circuit breakers and settlement monitoring;
replay and daily analytics; authenticated APIs/SSE; seven dashboard pages; migrations,
worker leases/heartbeats, alerts, retention, CI, images and Render infrastructure.

## 3. Architecture

Python/FastAPI modular monolith, separately deployable market-data, analysis and
maintenance roles, PostgreSQL source of truth, React/TypeScript/Vite dashboard.
Database leases and transactional reservations avoid unnecessary queue infrastructure.
An optional non-root nginx frontend proxies API/SSE over the same origin.

## 4. Kalshi status

**Passed against public production data:** current sports discovery sample,
series/event fee information, current market state and two complementary books.
Bid-to-ask conversion uses fixed-point dollar/quantity fields and dynamic grids.
Subscribed sequence continuity is subscription-scoped; lifecycle changes invalidate
markets until rediscovery. REST prices are conservatively dated at request start.

Authenticated WebSocket signing and reconstruction are implemented and
fixture-tested. On 2026-10-02 the supplied credentials successfully subscribed
to a real order book and returned two normalized outcome books. The adapter was
updated for Kalshi's currently documented Ed25519 support as well as RSA-PSS;
private key material was neither displayed nor committed.

## 5. Polymarket status

**US public production REST/book probe passed**, using typed long/short market
sides, USD amounts, fee coefficient and gateway books. On 2026-10-02 the supplied
US credentials passed authenticated market-data subscription and returned two
normalized outcome books.

**International public Gamma/CLOB REST and actual public WebSocket passed.**
Outcome token mapping, absolute-size deltas and application heartbeats are wired.
REST protocol probes included a non-sports market and must not be interpreted as
proof that every supported sport has overlapping inventory.

International values are USDC, not automatically fiat USD. Cross-currency paper
pricing requires explicit risk acknowledgement of the parity assumption.

## 6. Matching

Requires league/sport, two canonical participants, verified UTC start, supported
type/period, compatible YES mapping, overtime/draw/cancellation/postponement/source
and unit payout. Unknowns queue review; conflicts reject. Different raw texts
require human review even if extracted fields agree. Price/last-trade similarity
and AI confidence never prove equivalence.

Rule versions bind public identity, schedule, titles, tokens and evidence. Source
reschedules invalidate annotations/approvals. Half-refund complementarity is
supported; independent fair-price cancellation is rejected. No LLM provider is
enabled; validated semantic suggestions cannot enter the pricing hot path.

## 7. Calculation

Both complementary directions, including inverted team mappings where draw
semantics permit them. Sorted asks, matched lot quantity, full consumed depth,
weighted averages, fee versions, conservative slippage and latency buffer.
Six sizing modes retain explicit per-venue, total, bankroll and quantity caps.

The $500 limit is fee/safety-inclusive per venue; allocations need not be equal.
Slow HTTP receipt cannot reset an old request's age. Expiry uses the earliest
relevant request/exchange/receive timestamp, and future clock drift rejects.

## 8. Paper execution

Persisted, idempotent submitted intent; post-latency re-observation, protected
limits, liquidity haircut, matched and one-sided fills. In-app critical alerts
and an audited automatic breaker guard unhedged exposure. Active event
reservations prevent reusing the same liquidity on successive updates.

Conservative is default. Optimistic is separately labeled. Observed queue-based
inference reports an explicit limitation rather than fabricated fills.

Held public markets are polled for final settlement even after removal from open
discovery. Missing/preliminary payouts do not release bankroll. Verified
hypothetical settlement results adjust bankroll; unsupported scalar fee
reconciliation remains pending.

## 9. Risk controls

Capital per leg/total/day/event/league, bankroll and settlement P&L, open and
simultaneous intent limits, daily loss, unhedged quantity, cooldown, confidence,
freshness, skew, tick/lot/minimums, blocked markets/leagues/venues, human-review
requirement, currency acknowledgement and global/venue breakers.

Five per day is a reporting/limit experiment, never a forced opportunity count.
Changes use revision checks and append-only application audits.

## 10. Dashboard

Overview; opportunities with calculation/book/rule evidence; match review and
normalization; paper lifecycle and replay; UTC daily analytics/export; system
health/alerts; risk settings and aliases. Loading, empty and error states, strict
client schemas, responsive mobile navigation, native modal focus behavior and
operator login are implemented.

## 11. Database

Explicit Alembic initial revision `efb2041d5699`. Indexed/unique canonical events,
markets/spec versions/annotations/aliases, matches/reviews, current/history books,
opportunities/observations/rejections, trades/legs, settings/audits, health/leases,
alerts, aggregates, replay, sessions/rate limits.

**Passed on real PostgreSQL 17:** empty-schema migration, drift check, concurrent
initialization, serialized duplicate protection, four submitted/hedged positions
and restart recovery. SQLite is a WAL-enabled test/development fallback only.

## 12. Tests completed

| Check | Result |
|---|---|
| Backend unit/property/adapter/API/integration/settlement suite | **68 passed**, one optional PostgreSQL test skipped when its dedicated URL was absent |
| 2026-10-02 signing compatibility regression suite | **76 passed**, including RSA/Ed25519 PEM/DER signature verification; optional PostgreSQL test skipped in this run |
| 2026-10-02 cloud-discovery and stream-persistence regression suite | **82 passed**, optional dedicated PostgreSQL test skipped when its URL was absent |
| Optional real PostgreSQL regression in isolated migrated database | **1 passed separately** |
| Frontend component/schema tests | **7 passed** |
| Chromium seven-page authenticated/mobile end-to-end flow | **1 passed** |
| Axe WCAG A/AA checks on loading and loaded overview | **Passed**, no violations after correcting status semantics and muted-text contrast |
| Ruff / format / mypy | **Passed** |
| TypeScript / Prettier / production Vite build | **Passed** |
| ESLint | **Passed, zero errors; one nonblocking TanStack/React-compiler warning** |
| Python runtime dependency audit | **No known vulnerabilities reported** |
| npm production dependency audit | **No vulnerabilities reported** |
| Render Blueprint JSON schema | **Passed**, including paper-only invariants |
| API and frontend production images | **Built and inspected as non-root** |
| Docker Compose stack / migrations / API / PostgreSQL / all roles | **Passed** |
| Health / advancing heartbeats / SSE / nginx proxy | **Passed locally** |

Zod's bundled pure-annotation messages are nonblocking vendor build warnings.
The original local handoff preceded publication. After owner authorization,
implementation commit `7e4fe69640668b7bd270b7e2f9d469d68050fb3c` was pushed to
`main`. [GitHub Validation run 36775574221](https://github.com/MichaelAbateTSU/arbitrage-engine/actions/runs/36775574221)
**passed all four jobs: backend, frontend, system and secret-scan**, including
Linux migrations, browser/accessibility validation and container builds.

## 13. Security review

A dedicated read-only security review reported no exploitable vulnerabilities
within its reviewed scope. This is not a security certification or legal opinion.

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| — | — | Reviewed implementation | — | No findings | — |

Argon2id, opaque HttpOnly/production-Secure/SameSite-strict sessions, revocation on
secret rotation, CSRF/exact origins, persistent login limits, sanitized validation
errors, ORM parameters, request/frame limits, timeouts/retries, secure headers,
hash locks and non-root images are present. No venue secrets reach browser code.

## 14. Render deployment status

**Provisioned and live**, using paid Starter compute for the API and each worker.
Managed PostgreSQL 17 uses Basic 256 MB compute and a 10 GB disk, with public
database access disabled. The API container serves the compiled dashboard directly;
no additional frontend service is billed. All services run in Ohio without
free-tier sleep. Venue credentials are confined to the market-data worker.

| Service | Render ID | Role |
|---|---|---|
| arb-api | srv-davso449v7es7392a7vg | HTTPS dashboard/API and migrations |
| arb-market-data-worker | srv-davsom9srm7s73d8e500 | Discovery and live order books |
| arb-analysis-worker | srv-davsomqd0e5s7398ea9g | Matching, pricing and conservative paper lifecycle |
| arb-maintenance-worker | srv-davsonflk1mc73cduicg | Retention, settlement checks and daily analytics |

No unrelated TradeAgent services were altered. `render.yaml` remains the complete
Blueprint, including an optional separate frontend proxy. Deployments were
triggered and their actual source SHAs verified through Render's API; GitHub
repository-integration access is still required for reliable automatic push deployment.

## 15. Application URLs

Production dashboard: **https://arb-api-jabx.onrender.com**.
It requires the operator login configured in the owner's environment.
Public readiness endpoints are `/health/live` and `/health/ready`; protected
market-data requests correctly return HTTP 401 without a session.
Local development remains available through README.

## 16. Health results

Local `/health/live`, `/health/ready`, `/health/dependencies`, API routes, SPA and
SSE passed. Worker timestamps were checked again after seven seconds and advanced;
a cached lease alone was not accepted as process-health proof. Same-origin nginx
frontend successfully proxied readiness and configuration.

The browser's populated-state checks also exposed a long-running analytics
hot-path issue. Daily reports now select scalar projections instead of archived
raw books/calculation inputs, and paper evaluation runs independently of slower
discovery/detection sweeps. Both changes passed the full lifecycle regression.

**Cloud verification passed.** The HTTPS dashboard, liveness and readiness returned
HTTP 200; PostgreSQL and Alembic revision `efb2041d5699` were healthy. Read-only
verification ran inside the deployment rather than exposing database credentials
or bypassing dashboard authentication.

At 2026-10-02 19:34 UTC, all three feeds were connected with no current feed errors
and zero reconnects since the reliability rollout. Three worker heartbeats were
fresh. The database contained **1,405 public-source markets** (660 Kalshi,
543 Polymarket international, 202 Polymarket US), **190 candidate matches**, and
32 fresh synchronized outcome books in that sample. Each venue monitors up to
24 markets concurrently; this is not a claim that all discovered markets have
live books. No qualified opportunities or paper fills were observed.

Cloud observations exposed and fixed incomplete provider pagination, skipped
event-lifecycle sequence consumers, nested Render command quoting, and database
backpressure on socket readers. Every delta is reconstructed; a bounded latest-book
buffer coalesces persistence at 100ms cadence without refreshing quote timestamps.

## 17. Owner configuration still required

Production authentication, exact HTTPS origin, private PostgreSQL URL and public
source mode have been configured securely. Owner environment values were not
printed or committed. No execution credentials are required.

Optional authenticated data streams: Kalshi key/RSA or Ed25519 private key and US
Polymarket key/Ed25519 secret. Keep them worker-side. Review data rights,
eligibility and legal geography before commercial distribution.

## 18. Limitations

- Public rules often omit reliable kickoff or policies; such pairs stay unapproved.
- Discovery uses a bounded simple-market sports universe, not every prop/league.
- International streams do not provide provable sequence continuity; periodic
  snapshots/reconnects help but do not create an exchange guarantee.
- Queue-observed fill inference, external email/webhook delivery, commercial
  billing, production LLM providers and private order APIs are not enabled.
  GET-only account evidence is implemented by the validation extension below.
- Scalar settlement fees outside supported fixed binary/refund payouts need
  reconciliation. Changes to an already-final payout need operator investigation.
- USDC parity, chain/redemption/transfer costs and venue/commercial/legal
  eligibility are not guaranteed by executable order books.
- No empirical multiweek opportunity-frequency/profitability claim is made.

## 19. Live-trading status

**Intentionally unavailable.** Every unsafe live flag or live mode refuses
startup; execution methods raise. No real order was submitted.

## 20. Final validation commands

Executed with the project virtual environment and isolated validation databases:

```powershell
.\.venv\Scripts\ruff.exe check backend scripts
.\.venv\Scripts\ruff.exe format --check backend scripts
.\.venv\Scripts\mypy.exe backend\app
.\.venv\Scripts\pytest.exe backend\tests -q
.\.venv\Scripts\pytest.exe backend\tests\test_postgres.py -q
# From backend, against the isolated PostgreSQL URL:
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\alembic.exe check
# From frontend:
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
npm run test:e2e
# From repository root:
.\.venv\Scripts\python.exe scripts\public_probe.py
.\.venv\Scripts\python.exe scripts\validate_blueprint.py
.\.venv\Scripts\python.exe scripts\smoke_test.py --workers
.\.venv\Scripts\pip-audit.exe -r backend\requirements.lock --disable-pip --no-deps
docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ -t arb-validation:9218 .
docker build -t arb-frontend-validation:9218 -f frontend\Dockerfile .
docker compose -p arb-validation-9218 up -d --no-build --wait --wait-timeout 120
git diff --check
```

An isolated `ARB_TEST_POSTGRES_URL` was supplied for the PostgreSQL test, and
`ARB_IMAGE=arb-validation:9218` for Compose. Test authentication used explicitly
test-only configuration, never production credentials.

The standard PyPI download host failed TLS handshakes on this network. The API
image was built using an explicitly selected verified-HTTPS mirror **with official
SHA256 hashes still enforced**; TLS verification was not disabled. Both production
images and the resulting stack ran successfully.

## 21. Git / handoff

The owner authorized publication of the tested implementation on the original
main branch. Implementation commit `7e4fe69` was pushed and its remote SHA verified;
the complete hosted Validation workflow passed. No branch switch or PR was needed.
Deployment fixes were also committed and pushed. The stream-reliability release
`715f5fb` passed the complete hosted
[Validation workflow](https://github.com/MichaelAbateTSU/arbitrage-engine/actions/runs/37054489721).
Render deployments and private runtime checks verified the real cloud result.
Paid services continue independently of the local terminal or this agent session.
Operator settlement-equivalence review and risk acknowledgements remain mandatory;
190 candidate matches are not 190 validated arbitrages. Real trading is unavailable.
The plan, research, runbooks, safeguards and deployment steps are linked from README.

## 22. Opportunity-validation extension

Implemented an eighth **Opportunity validation** dashboard and independent
analysis loops. Both directions are diagnosed despite unapproved matches,
with settlement scenario matrices, current rule hashes, depth-adjusted asks,
priced contracts, ages, fees, evidenced additional costs and all blockers.
The persisted 10-20-pair review watchlist prioritizes same-currency observed
depth; selection does not approve contracts.

Market-data credentials support signed **GET-only** buying-power evidence.
Product identity, market connectivity, account-read access, jurisdiction,
KYC, market-specific restrictions and order permission are separate facts.
International Polymarket remains US close-only; cloud geography cannot bypass it.
Public shadow candidates require independent hash-bound human review and verified
costs, even when ordinary paper risk does not require human review.

Persistent shadow trials use delayed fresh books, protected limits and lot-valid
partial fills. Baseline, injected second-leg rejection and injected partial fill
are separate evidence. Emergency unwinds use bid-side depth and current fees;
remaining hedge/residual capital stays reserved until final public settlement.
Virtual wallets conservatively reserve the combined cap on each participating
venue, aggregate Kalshi exposure across both products, and deduct realized losses.
Restart idempotency and current operator rejections are respected.

Reports distinguish distinct episodes from repeated observations, baseline from
stress, unsettled from observed-settlement/failed-hedge P&L, aggregate seconds
from capital-weighted USD-seconds, and evidenced from unknown operating costs.
The configured 7-14-day window is diagnostic only. No profitable edge or
automatic permission to trade is claimed.

Local verification includes **102 backend tests passed**, one optional PostgreSQL
test skipped in that run, the dedicated PostgreSQL regression passed separately,
Alembic upgrade/drift/downgrade/re-upgrade checks on isolated PostgreSQL 17,
**9 frontend component/schema tests** and authenticated eight-page Chromium/WCAG checks,
types/lints/build and Render Blueprint invariants. Migration revision is
`6e9f3a2c7d10`. Production rollout and real shortlist observations are recorded
separately after deployment; the earlier cloud numbers are historical snapshots.

The first new cloud sweep exposed derived slippage with more than eight decimal
places on fractional public prices. Computed monetary amounts now retain exact
Decimal precision, without rounding away costs; venue quantities and input
limits retain their bounded precision. The new fractional-price regression
covers calculations, paper fills and exposure accounting. Runtime verification
also requires active worker heartbeat build identities to match the release,
not merely a Render deployment marked live while old leases drain.

## 23. Validation-phase production acceptance

Verified on **2026-10-03 at 08:19 UTC**. All four dedicated Render services
deployed executable release `38f1f4411ff60869b1b152ff9aa92c8ca2ca9617`.
The three active worker heartbeat build identities matched that release.
HTTPS dashboard/readiness returned 200; the validation API correctly required
authentication (401 without a session). Migration `6e9f3a2c7d10` was active.
The full hosted
[Validation run 37108810744](https://github.com/MichaelAbateTSU/arbitrage-engine/actions/runs/37108810744)
passed backend, frontend, system and secret-scan jobs.

Read-only acceptance job `job-db0bl06gekts73909n5g` succeeded. Its snapshot
contained **1,540 public markets, 240 current candidate pairs, 480 fresh
directional diagnostics, 12 fresh synchronized outcome books, zero qualified
directions, zero opportunity episodes and zero shadow trials**. All three
market-data feeds were connected. These are observations, not promised ongoing
counts; a book can legitimately become stale between samples. No freshness or
profit threshold was lowered and no timestamp was reset.

Kalshi and Polymarket US authenticated **GET-only account-read evidence passed**.
Order permission, KYC, operator jurisdiction and market-specific eligibility
remain independently unverified. International Polymarket is explicitly
US-close-only. Account balances and credentials are not included in this report.

| Blocking condition | Distinct pairs in this snapshot |
|---|---:|
| Unapproved matching / unproven settlement coverage | 240 |
| Independent human review missing | 240 |
| Additional execution/settlement costs unverified | 240 |
| Missing one or both monitored outcome books | 232 |
| International product US-close-only restriction | 114 |
| No gross spread at the priced size | 8 |
| Stale/skewed/unsynchronized books in directional evidence | 8 |
| Insufficient observed balance on each priced venue | 8 |

Reasons overlap; they must not be added to obtain a candidate total. The paper
kill switch remains active and live execution remains unavailable.
The independent 14-day collection window began **2026-10-03 08:03:46 UTC**.
It collects diagnostics now; actual virtual shadow trials wait for reviewed,
cost-evidenced, profitable candidates. A multiweek trading edge has not been
demonstrated.

### Actual settlement-review findings

Inspected the exact current public rule text, normalized fields and rule hashes
for **20 selected Kalshi/Polymarket US pairs across 13 distinct NFL events**.
Eight had nonzero observed matched depth in the review snapshot; that is not
proof of continuously fresh executable liquidity for all twenty.

| Event | Candidate pairs inspected |
|---|---:|
| Denver Broncos / San Francisco 49ers, Oct 4 | 2 |
| Los Angeles Rams / Philadelphia Eagles, Oct 4 | 1 |
| Los Angeles Chargers / Seattle Seahawks, Oct 4 | 2 |
| Detroit Lions / Carolina Panthers, Oct 4 | 2 |
| Miami Dolphins / Minnesota Vikings, Oct 4 | 1 |
| Atlanta Falcons / New Orleans Saints, Oct 5 | 2 |
| Denver Broncos / Los Angeles Chargers, Oct 11 | 1 |
| Chicago Bears / Green Bay Packers, Oct 11 | 2 |
| Minnesota Vikings / New Orleans Saints, Oct 11 | 2 |
| Indianapolis Colts / Pittsburgh Steelers, Oct 11 | 1 |
| New York Giants / Washington Commanders, Oct 11 | 2 |
| Cleveland Browns / New York Jets, Oct 11 | 1 |
| Buffalo Bills / Los Angeles Rams, Oct 12 | 1 |

**None was approved.** Normal winner outcomes and half-refund ties could be
complementary with the correct YES mapping, but that does not cover every
settlement scenario. The actual Kalshi text retains the game only if it begins
within 48 hours, then uses a fair-price fallback. Polymarket US uses a two-week
rescheduling window and last-fair-market-price fallback. One leg could settle
while the other remains exposed; independent fair-price payouts need not sum
to one. This is a substantive incompatibility, not a reason to loosen a price
threshold.

Polymarket US explicitly includes overtime and names NFL as its outcome source.
Kalshi's inspected descriptions use the generic governing-league source and
do not explicitly state overtime; reliable Kalshi kickoff remains unverified.
Both raw descriptions leave cancellation/void details requiring further
independent evidence. Some available raw clauses remain unnormalized by the
deliberately conservative parser. Source review must distinguish that from
truly absent evidence rather than auto-filling unknowns. The exported inspection
retained the exact rule hashes; no operator attestation, source annotation,
human-review flag or approval was fabricated.

## 24. Focused defensible-validation phase

The next implementation replaces a broad blocked watchlist with a bounded
family-first screen, explicit instrument/cost evidence and persistent usable
coverage. Migration `93ad7c201b46` adds policy-family, instrument-monitoring and
daily coverage tables without resetting validation configuration creation time.
The original collection clock and the old 20-pair selection are retained.

Scenario matrices now bound normal winners, ties, cancellation/void, postponement
within/between/beyond policy windows and discretionary decisions. They distinguish
unknown bounds from the conservative 0..2 combined envelope for independently
chosen binary fair-price payouts. The envelope does not assert an attained
outcome; it shows that no tighter cross-venue hedge floor has been verified.
Pricing distinguishes conditional unit-payout profit, the known bounded-scenario
net floor and the complete all-scenario net floor (unknown if any scenario lacks
evidence). Full governing-terms review can attest that discretionary exceptions
are excluded, but cannot contradict fair-price language in the source.
Ordinary paper detection and delayed shadow fills share these fail-closed gates.

Family screens include matched candidates and bounded inventory-only rule samples
for other overlapping leagues. An inventory-only sample is not fabricated into
an event pair. Families never create approvals. The default focused sample uses
five distinct events, bounded to 5-10 when configured, with compatible/unproven
potentially eligible families first. If none has candidates, non-close-only
representatives are labeled diagnostic-only. Exact source/hash changes retire
old proofs and refresh selection; legacy watchlist IDs remain available.

Instrument evidence identifies monitoring-cap exclusion, invalid metadata/token
mapping, actual subscription request/confirmation/rejection, closed state,
missing snapshot, stale book and invalid reconstruction. Socket observers only
enqueue compact status; separate persistence never blocks reconstruction. Public
REST probes independently report endpoint response, depth and unchanged source
age, without replacing sequenced stream state. Generation/integrity epochs prevent
usable-time credit across reconnects or short invalid intermediate states.

Cost models distinguish verified amount, verified zero, not applicable and
unknown for funding, conversion, withdrawal, settlement, rebalancing and network
expenses. Every verified component identifies its allocation basis/path,
provenance and expiry. Legacy aggregate charges remain priced but cannot silently
become six verified zeros; nonzero legacy/new component models cannot coexist.
Operation-specific opening and emergency-unwind allocations are included in simulated losses and
are not charged again when held exposure eventually settles.

GET-only Kalshi scope evidence is separate from account-read permission:
trade scope, primary/subaccount/institutional binding and location-attestation
expiry are checked without storing any API key ID/name/response. Account and
scope freshness bind to the active build. Retail US order permission remains
unknown pending independent evidence; no preview/order is used to test it.
International US-close-only pairs remain outside the automatic executable focus.

Independent subsecond coverage sampling reads only focused records. Persistent
daily pair-seconds separate settlement approval, usable two-direction books,
complete financial evidence and funded/eligible observation. Positive funded-size
spread time and well-observed nonqualifying spread time are separate; distinct
directional windows use the existing episode-gap policy rather than counting
every book tick as a new opportunity. Adjacent samples require unchanged evidence,
generation/epoch and <=2-second gaps, and are clipped to original quote/fee/cost/
account deadlines. Earlier coverage is explicitly unknown, not reconstructed.
This is conservative sampled coverage, not proof of atomic order execution.

### Fresh baseline inspection before this release

Read-only Render job `job-db0igdfavr4c73fsf2eg` succeeded. At **2026-10-03
16:07:30 UTC**, the deployment still had 240 pairs: 56 NFL/US, 70 NBA/US,
56 NFL/international and 58 NBA/international. All 240 lacked approval/cost
evidence, 213 lacked one or both monitored books, and 27 pairs with
priced evidence also showed stale/skewed books and no gross spread. Counts
overlap and are observations, not promised current totals.

Representative GET-only books resolved successfully using the actual Kalshi
ticker and retail US slug: Detroit/Carolina NFL and Golden State/LA Clippers NBA.
The US source timestamp ages were approximately 146 seconds and 434 seconds,
respectively; those responses were not genuinely empty books, and their timestamps
were not refreshed. This does not retroactively prove why every missing
subscription failed; the new per-instrument records make subsequent causes
auditable.

Cloud acceptance initially exposed a focused-monitoring handoff defect:
two newly selected contracts still had cap-exclusion evidence from the preceding
subscription generation. The fix retains valid current targets and watches
focused match signatures every two seconds, resubscribing authoritatively after
selection/specification changes instead of waiting for the five-minute periodic
snapshot. Neither freshness thresholds nor the original clock were changed.
The next acceptance check detected delayed broad diagnostics. Repeated purchase
size searches on stale/invalid books were removed as unnecessary work.
Diagnostics now retain the original BBO, observed depth, age and failure reasons
but do not present stale/unsynchronized/skewed ladders as current executable net
profit. Unapproved pairs with genuinely usable books still receive both-direction
depth pricing. A subsequent status snapshot showed diagnostics were still delayed:
the inventory family loop kept building scenario proofs after its 20-family cap
was full. The screen now returns immediately when matched families fill the cap
and stops inventory evaluation as soon as remaining slots are filled. A
deterministic call-count regression checks both paths. These corrections bound
the actual work, not just the output, without changing timestamps or thresholds.
Cloud profiling then distinguished computation (20-family screen ~0.3-0.4s,
480-direction diagnosis ~0.9-1.2s) from a database stall: an older deployment's
unfinished candidate-update transaction held the current writer's row locks.
A targeted, precondition-checked rollback of that abandoned backend released
only its uncommitted diagnostics. PostgreSQL app connections now have build/source
identity and bounded command, statement, lock and idle-transaction deadlines;
worker command timeouts are explicitly reported before retry. A real PostgreSQL
regression checks lock-timeout rollback and the subsequent successful attempt.
After recovery, remaining load was reduced at the high-rate consumers: empty paper
and shadow ticks no longer read the entire market/book universe, and detection
reads only markets/books referenced by current matches. Active paper handling,
shadow unwind/settlement and qualification checks remain unchanged.
The cloud verifier also samples book freshness immediately after reading books,
after its larger diagnostic report, and publishes both capture times. This avoids
aging its own snapshot through unrelated report queries; original exchange/receipt
timestamps and the production freshness threshold are unchanged.

The NBA US sample uses two calendar days, unlike the NFL sample's two weeks.
Both still contain independent fair-price fallback and do not establish a
constant complementary payout. No approval, free-cost assertion, account
permission or profitable real trading edge has been fabricated. A zero-result
period without fully evidenced eligible coverage is explicitly inconclusive.
