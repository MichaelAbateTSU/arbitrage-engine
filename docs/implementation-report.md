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
An additional cloud profile identified an orphan from the preceding build still
active in PostgreSQL's extended-protocol client-read phase; per-statement and
client deadlines alone did not bound its transaction. The existing PostgreSQL 17
deployment now also applies a 60-second **whole-transaction** server deadline.
Its real-database regression verifies backend termination and pool recovery.

The NBA US sample uses two calendar days, unlike the NFL sample's two weeks.
Both still contain independent fair-price fallback and do not establish a
constant complementary payout. No approval, free-cost assertion, account
permission or profitable real trading edge has been fabricated. A zero-result
period without fully evidenced eligible coverage is explicitly inconclusive.

### Verified focused release and bounded-review outcome

Executable release **`b6768380e693304c8ceaad51fc0c3f14d924b30e`** was deployed
API-first to the same four existing Render services. Hosted validation run
**37144421154** passed backend/PostgreSQL, frontend, authenticated system/browser,
container and secret-scan checks. Local validation finished with **133 backend
tests**, plus **four isolated PostgreSQL regressions**; frontend's 12 tests,
lint/types/format/build and the authenticated eight-page/WCAG workflow passed.
The pre-existing TanStack React Compiler warning remains nonblocking.

Read-only acceptance job **`job-db0kkdegekts73a4ajn0` succeeded**. At **2026-10-03
18:34:22 UTC**, all three active heartbeat builds matched the executable release,
schema was `93ad7c201b46`, all **480 direction diagnostics** for **240 current
candidate pairs** were fresh, and **14 persisted books** met the unchanged
freshness/integrity checks at their explicit capture time. HTTPS root/readiness
returned 200; unauthenticated `/api/v1/validation/focused` returned 401.
The ordinary paper kill switch was active and live execution remained unavailable.
There were no opportunities, paper trades, qualified shadow directions or distinct
funded-spread windows. A fresh book somewhere is not continuous two-venue coverage.

The bounded 20-family screen found **three nonconstant-hedge profiles and 17
restricted profiles**, not a compatible proven family. The nonrestricted profiles
were NFL/US, NBA/US and an explicitly inventory-only MLB/US sample with zero event
pairs. International profiles remain US-close-only. The retained five-event focus
is therefore **diagnostic-only**, not an executable universe; the previous
20-match watchlist remains historical evidence.

All ten focused instruments were selected with focused priority and both outcomes
had subscription confirmation from actual data. GET probes returned nonempty
snapshots for each ticker/slug. The accepted stream observation was:

| Diagnostic event | Kalshi age | Retail US age | Book explanation |
| --- | --- | --- | --- |
| Atlanta / New Orleans | 2.936s | 200.723s | Kalshi usable; US stale |
| Tennessee / Baltimore | 16.567s | 90.804s | Both stale |
| LA Lakers / Golden State | 28.777s | 67,287.285s | Both stale |
| Golden State / LA Clippers | 18.598s | 187.314s | Both stale |
| Utah / Denver | 26.465s | 7.325s | Both stale |

These were not cap exclusions, rejected subscriptions, failed endpoint mappings
or empty purchase ladders at that capture. Retail US GET responses also retained
old original timestamps, approximately 14.930s to 67,257.918s old in the latest
probes. REST availability cannot certify the freshness of sequenced stream state.
The diagnostic event labels do not approve event dates or settlement identity;
unknown start/period/overtime/source evidence remains blocked.

Kalshi and retail US account reads were verified. Kalshi's GET-only key probe
reported trade scope, primary-account binding and current location-attestation
expiry, but **not account/KYC/market-specific order permission**. Retail US still
has no authoritative permission verdict from its balance endpoint. Both venues'
order permission remains unverified; the international account remains restricted.
All six additional-cost components remain unknown for every venue: no funding
path, zero-cost assumption or independent permission was fabricated.

The original collection start remains **2026-10-03 08:03:46.679185 UTC** and
coverage instrumentation start remains **17:16:55.508191 UTC**. Acceptance recorded
4,218.480189 sampled pair-seconds and 9.699989 usable-book pair-seconds.
Independent follow-up job **`job-db0kmbdg1s2s73egmoi0`**, at **18:36:36 UTC**,
confirmed the same active builds/clocks and advancing persisted counters:
**4,621.906919 sampled pair-seconds** and **10.927609 usable-book pair-seconds**.
Every settlement-approved, cost-complete, funded/eligible and profitable stage
remained zero. Historical versions remain included, and counters are pair-time,
not wall time or backfilled coverage.

**No fully validated real pair or trading edge was established.** The implemented,
deployed pipeline now makes that defensible negative/inconclusive decision and
preserves its evidence. Further time alone cannot close settlement, permission
and cost gaps. Within this reviewed scope, reconsider the venue/contract family
or obtain genuinely different compatible governing terms; do not accept independent
fair-price settlement, freshen stale books or lower thresholds to create trades.

## 25. Bounded feasibility pass: evidence, not another feature release

**Decision: no fully evidenced executable pair was established. Pause the
provider-dependent validation gates; do not enable trading or interpret this as
an economically negative result.** The reviewed sports universe does not offer
an approved hedge. A materially different, real Bitcoin up/down candidate was
identified outside the implemented sports-only discovery scope, so this pass
does **not** establish that the entire Kalshi/Polymarket US combination is
impossible.

The pass used existing services and read-only public/account evidence. It added
no dashboard features, servers, live orders, order previews, approvals, funding
actions, production cost attestations or relaxed thresholds. The executable
deployment remains `b6768380e693304c8ceaad51fc0c3f14d924b30e`.
Only documentation and focused fee regressions changed in the repository.
The original collection and coverage clocks were not restarted.

### Scope and settlement classifications

Read-only cloud job `job-db0ob1id0e5s73cgradg` succeeded. At **2026-10-03
22:45:38.774821 UTC**, its current inventory contained **242 candidate pairs**:
72 NBA/US, 56 NFL/US, 58 NBA/international and 56 NFL/international.
It exported eight normalized sports profiles under a 12-profile review cap,
including three explicitly inventory-only comparisons. Duplicate NFL/
international postponement policies are collapsed below instead of treating
additional games or missing overtime text as new settlement discoveries.

Here, **proven incompatible** means documented terms break the proposed
constant-$1 complementary payout. It does not mean no deeply discounted,
separately bounded strategy could ever exist. **Insufficient evidence** means
the relevant payout floor or contract identity has not been established.
**Potentially compatible** is a research priority, never an individual approval.
The engine's conservative 0..2 fair-price envelope is not evidence that either
endpoint actually occurs.

| Family comparison | Current scope | Classification | Source-backed reason |
| --- | --- | --- | --- |
| NFL: Kalshi / US | 56 candidates | Proven incompatible with the constant-$1 hedge | Kalshi switches to fair-price settlement when the game has not started within 48 hours; US permits rescheduling within two weeks. A game starting on day three can settle one leg at an independent fair price and the other at a binary game result. |
| NBA: Kalshi / US | 72 candidates | Insufficient evidence at family level; the sampled pair has a proven identity mismatch | The sampled Kalshi rule names October 7, while the US rule names October 16. Even a corrected fixture still needs the 48-hour versus two-calendar-day interpretation and independent fair-price exceptions reconciled. |
| MLB: Kalshi / US | Inventory-only; zero candidate pairs | Proven incompatible with the constant-$1 hedge | The sampled Kalshi rule switches to fair price for cancellation or rescheduling beyond two days; US permits two weeks. Matching the sampled teams/date does not repair the postponement payout mismatch. |
| NHL: Kalshi / US | Inventory-only; zero candidate pairs | Insufficient evidence | The samples are different fixtures, not a fabricated pair. Kalshi's 48-hour/cancellation rule and US's two-calendar-day rule do not establish shared fair-price, cancellation or discretionary-settlement bounds. |
| NBA/NHL policy: Kalshi / international | 58 candidates in this policy group | Proven incompatible with the constant-$1 hedge; separately restricted | Kalshi's 48-hour/fair-price policy differs from holding until the rescheduled game finishes and a 50-50 cancellation settlement. International access remains US-close-only. |
| NFL: Kalshi / international | 56 candidates across two normalized profiles | Proven incompatible with the constant-$1 hedge; separately restricted | The same 48-hour versus until-completed difference remains after collapsing the duplicate postponement policy. Missing explicit overtime text does not create another defensible hedge. |
| MLB: Kalshi / international | Inventory-only; zero candidate pairs | Proven incompatible with the constant-$1 hedge; separately restricted | Two-day fair-price fallback differs from until-completed/50-50 settlement; the inventory samples also name different event dates. |

The postponement counterexample does not need to assert a particular actual
fair-price settlement: if the Kalshi-held outcome settles at an intermediate
fair price and the later game makes the opposite venue's purchased outcome
worth zero, the combined payout is that fair price, not $1. An illustrative
$0.40 payout would leave only $0.40 before costs. This is a permitted-rule
counterexample, **not an observed loss, provider-confirmed $0.40 floor or proof
that the engine's zero bound is attained**.

The concrete NBA identity rejection is:
`KXNBAGAME-26OCT07GSWPOR-GSW` versus
`aec-nba-por-gs-2026-10-16`. Their full rule texts specify October 7 and
October 16 respectively; the US `gameStartTime` is October 17 at 02:00 UTC.
This is not inferred solely from slug formatting. Source rule hashes were
`dfaf389cd474ca7773bc4fa3bece49eb973a5433b11710e8f2e9cf31943ad0c4`
and `cf9e4ba89477696c478f826c15dced7ea5d79fe11ebfbd60c4261b0565ed027c`.
Do not approve this candidate as the same game. The existing unproven-identity
and independent-approval gates continue to prevent qualification.

#### Materially different production families

The pass also queried the documented retail category filter, rather than
assuming the sports scanner represents every product on either venue.
At **22:52:15 UTC**, public production GET requests for active, nonclosed
markets returned **zero weather listings and 63 crypto listings**, below the
100-record first-page cap. This is a time-bound result for those public filters,
not proof that weather can never be listed or that every returned crypto
contract is still inside its resolution window. Some metadata marked OPEN
even when the rule-defined observation deadline had already passed.

Four different crypto comparisons were then bounded to public rule samples:

| Comparison | Classification | Finding |
| --- | --- | --- |
| 15-minute BTC up/down | Potentially compatible; individual proof incomplete | An actual pair shared BRTI, the 22:45-23:00 UTC window, an inclusive Up criterion and the $84,780.82 opening reference. Exact sample boundaries, rounding, missing data, revisions and independent review remain unproven. |
| Annual BTC $150k one-touch | Insufficient evidence | Both sampled terms use a 60-second 20%-trimmed BRTI mean, but issuance windows and the 23:59 versus midnight cutoff are not identical. A directional dominance proof must cover the full observation intervals and exceptional settlement, not merely the same headline target. |
| BTC price range | Insufficient evidence; sampled contracts are not equivalent | The sampled Kalshi contract uses a simple 60-second mean and an October 4 expiry; the US year-end contract uses a 20%-trimmed mean at January 1 midnight. This is neither a shared statistic nor a matched expiry. |
| Monthly BTC one-touch | Insufficient evidence; sampled methodologies differ | The sampled Kalshi monthly rule describes cumulative minute-by-minute trimming and an explicit missing-data No outcome; the US one-touch rule describes rolling sixty-second trimming. Similar titles do not establish equal payouts or a directional guarantee. |

At **22:53:53 UTC**, the concrete potentially compatible pair was
`KXBTC15M-26OCT031900-00` and
`cpc-btc-updown-15m-2026-10-03-2245z`. Both were reported active/open,
and both closing conditions were "at least" the same opening reference.
The Kalshi series pointed to the full
[CRYPTO governing terms](https://assets.kalshi.com/contract_terms/CRYPTO.pdf),
which were read in this pass, not just the market summary. They specify a
60-second simple BRTI average, exclusion of post-expiration revisions,
missing/incomplete data resolving affected strikes to No, and independent
review/payout powers under the Kalshi rulebook.

The US [crypto FAQ](https://docs.polymarket.us/faqs/crypto-faqs) specifies
the inclusive sample interval `[T - 59 seconds, T]`, two-decimal rounding,
and deferral until complete data or exchange review. Kalshi's "sixty seconds
prior" wording does not independently prove the same set of seconds.
Equal displayed opening prices do not settle this question.
The FAQ's rollout note said automated families were in preprod, but the
authenticated venue is not being inferred from that note: the actual sampled
production retail response contained typed `assetPriceTerms` for these windows.

The useful next review direction is **Kalshi NO + US Up**, conditional on
identical normal-resolution values. With complete identical values it pays $1
whether the price rises, falls or ties. Under the documented Kalshi missing-data
No rule, that Kalshi leg alone would pay $1, provided the normal fallback is not
overridden. Independent discretionary review, different sample sets or different
revision handling still have no verified cross-venue floor. The reverse
direction does not inherit this missing-data protection.

This is therefore **not a fully approved pair, a measured profitable spread,
continuous two-sided pricing or a trading recommendation**. The sampled window
ends at 23:00 UTC on October 3 and is not an evergreen instrument. Any future
instance requires fresh exact identities, governing terms, prices and evidence.
No crypto instrument was added to the current scanner or coverage counters.

### Retail US feed: two short traces, with exact instrument identity

The first 45-second control trace ran **22:45:26.865791-22:46:12.654463 UTC**
and received 18 marketData frames. It included the retained older
`aec-nba-gs-lal-2026-10-13` instrument, not the currently selected
`aec-nba-lal-gs-2026-10-06`. Those are distinct contracts; the old book was
not substituted for the current one.

A second trace used the **five exact slugs identified by the 22:45 cloud audit**.
Its 30-second subscription window began **22:49:29.691104 UTC**; post-trace
REST sampling finished **22:50:01.444434 UTC**.
The socket authenticated at the documented retail endpoint
`wss://api.polymarket.us/v1/ws/markets`, subscribed with
`SUBSCRIPTION_TYPE_MARKET_DATA`, and received **22 marketData frames** echoing
request ID `bounded-blocker-trace`. All were `MARKET_STATE_OPEN`.
WebSocket ping/pong succeeded. There was **no separate subscription-ack frame**;
matching actual data envelopes confirm subscription service, not an invented ack.

| Exact retail instrument | Frames / distinct ladder hashes | Latest provider timestamp (UTC) | Corresponding receipt timestamp (UTC) | Interpretation during this trace |
| --- | --- | --- | --- | --- |
| `aec-nfl-ten-bal-2026-10-04` | 3 / 3 | 22:49:50.315355072 | 22:49:50.406336 | Actual changes delivered in 61-91 ms after the initial snapshot. |
| `aec-nfl-atl-no-2026-10-05` | 4 / 3 | 22:49:55.392173360 | 22:49:55.508255 | Actual changes delivered in 116-146 ms after the initial snapshot. |
| `aec-nba-lal-gs-2026-10-06` | 7 / 1 | 22:49:52.153704462 | 22:49:52.275032 | Repeated identical ladders had advancing provider timestamps, delivered in 107-149 ms. |
| `aec-nba-den-uta-2026-10-06` | 7 / 1 | 22:49:54.828987793 | 22:49:54.907522 | Repeated identical ladders had advancing provider timestamps, delivered in 48-131 ms. |
| `aec-nba-gs-lac-2026-10-04` | 1 / 1 | 22:32:32.271818111 | 22:49:29.852972 | Only an initial snapshot; matching before/after REST snapshots retained the old source time. |

This rules out a **global feed stall, rejected subscriptions or inaccessible
institutional-only integration during these windows**. It does not certify
every market's continuous health. The quiet Clippers book is consistent with
no book change observed; a market-specific stale publisher/cache cannot be
excluded from 30 seconds and transport pong alone.

REST was not a freshness substitute: the Tennessee post-trace response still
returned 22:49:30.718325038 after the socket had delivered 22:49:50.315355072,
and Atlanta REST retained 22:48:53.537174927 after the socket reached
22:49:55.392173360. These were older snapshots, not merely rounding noise.

The actual `USBookData` parser converted
`2026-10-03T22:49:27.531638892Z` to
`2026-10-03T22:49:27.531638+00:00`. It preserved UTC, seconds and microseconds;
discarding sub-microsecond precision cannot explain minutes or hours of age.
`us_books()` preserves this provider time as `Book.exchange_at`; it does not
replace it with receipt time. The
[retail book schema](https://docs.polymarket.us/api-reference/markets/get-market-book)
declares `transactTime` as date-time without a last-change/heartbeat/currentness
definition. Advancing timestamps on identical ladders also rule out assuming
it always means the last ladder change.

The implemented interface matches the
[retail market socket documentation](https://docs.polymarket.us/api-reference/websocket/markets):
market slugs and Ed25519 API-key handshake, alongside public gateway metadata/books.
The institutional data interface uses symbols, reference data, gRPC and different
authentication/firm-account concepts. Retail credentials were not repurposed to
probe institutional entitlements.

Raw public subscription/data frames and before/after snapshots were retained
as session artifacts `blocker-current-us-raw-public.jsonl`; the condensed trace
is `blocker-current-us-trace-public.json`. No keys, headers, signatures,
account bodies or balances were exported. Conservative freshness limits remain
unchanged pending the provider's per-market currentness semantics.

### Fees and the six route-specific cost components

The deployed function, not a reimplementation, returned **$1.74 for 100 contracts
at $0.50** using coefficient `0.0695`. It returned **$0.02** and **$0.04** for
exact half-cent controls $0.025 and $0.035. All **286 mapped US moneyline
instruments** in that cloud audit used `us_quadratic:0.0695`.
The official [US schedule](https://docs.polymarket.us/fees) was effective
**October 1, 2026 at 10 AM ET**, so it applied at the audit time.

The calculator sums the exact fees across consumed levels and half-even rounds
the cumulative amount. The schedule caps split-fill commission at that amount
and permits downward adjustments. Thus the deployed number is a **conservative
upper bound for multiple fills**, not an exact reconstruction of each collected
fill's fee. Maker rebates and volume tiers are not assumed. The separate combo
curve is not applicable to the single-instrument moneyline or up/down comparison.
Added regressions cover the requested 100-lot example and both half-even ties.

For additional costs, this pass defines a **proposed, not adopted**, USD-only
bank/ACH route: fully cleared own-name USD bank funds prefund both venues;
purchased complementary positions are held to settlement; withdrawals return
to their original funding accounts. No FX, crypto wallet or in-episode
cross-venue funding transfer is assumed. The user has not confirmed this route,
bank fees or allocation policy; these findings were not silently promoted to
production attestations.

| Exact component | Applicable route / allocation | Sourced status | Evidence still required |
| --- | --- | --- | --- |
| `funding` | USD bank ACH into each venue; amortize any actual transfer/processor charge over its funded contracts, without double charging prefunded episodes | Kalshi venue ACH charge: **verified zero** from [Bank Deposits](https://help.kalshi.com/en/articles/13823798-bank-deposits). US Aeropay ACH charge: **unresolved**; [its route page](https://docs.polymarket.us/learn/deposits/deposit-methods/bank-transfer) gives no explicit fee amount. | Confirm the actual route, US venue/processor deposit fee and originating bank's charges; exclude uncleared instant-credit assumptions. |
| `conversion` | USD bank funds, USD collateral and USD contract payouts throughout | **Not applicable, conditional on this exact USD-only route**; public contract/book currencies are USD. | Confirm no card, FX, USDC or other conversion is actually used. This is not a global verified-zero conversion claim. |
| `withdrawal` | USD ACH back to each original own-name funding bank; allocate any actual withdrawal cost once per withdrawal, not per price observation | Venue charges: **verified zero** from [Kalshi Bank Withdrawals](https://help.kalshi.com/en/articles/13823803-bank-withdrawals) and [US withdrawal overview](https://docs.polymarket.us/learn/deposits/withdraw-funds/overview). All-in bank-side cost: **unresolved**. | Confirm destination bank charges and adopted allocation. US funds must clear and return to the original funding source; its published typical arrival is 3-4 business days. |
| `settlement` | Automatic cash settlement of held event contracts; any separate clearing/settlement charge allocated to settled quantity | Kalshi's indexed official [July 7, 2026 schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) explicitly says no settlement fee; current-version direct reconfirmation is **unresolved** because the PDF request returned 429. US separate settlement charge is **unresolved**: [automatic balance settlement](https://docs.polymarket.us/learn/markets/contract-settlement) is not an explicit zero-fee statement. | Confirm the current Kalshi schedule and whether US charges a distinct retail settlement/clearing fee in addition to execution commission. Do not infer zero from silence or retry around a rate limit. |
| `rebalancing` | No transfer between venues during one strictly prefunded buy-and-hold episode | **Not applicable to that proposed episode only**. | Confirm that operating policy. Later replenishment belongs to its actual funding/withdrawal route and allocation; emergency unwind trading fees, slippage and failed-hedge losses remain separately charged, not zeroed. |
| `network` | Bank ACH plus venue-hosted USD trading/settlement; no on-chain transaction | Blockchain gas: **not applicable, conditional on the proposed route**. | Confirm no crypto transfer/wallet route. Render, database, internet and other operating expenses remain separate and must be deducted when assessing economic results. |

No arbitrary amounts, fee-free deposit assumptions or undocumented settlement
zeros were invented. Complete additional-cost evidence remains unavailable in
production until these source/route/operator gaps close. Trading commissions
are separate from this six-component table; $1.74 is not the pair's all-in cost.
Capital lockup and operating expense are also not proved zero by this table.

### Current order permission and precise provider questions

The fresh stored permission audit verified account read access for Kalshi and
US. Kalshi's GET-only key evidence verified trading scope, primary-account
binding and current location-attestation expiry. **Neither venue returned an
authoritative current account/KYC/market-specific permission verdict.**
US key trading-scope probing was reported unsupported by the implemented
retail probe, not as proof its key cannot trade.
International remained restricted/US-close-only. No inference was drawn from
the Render location.

The US [authentication documentation](https://docs.polymarket.us/api-reference/authentication)
describes identity verification before key creation, but the
[account-review documentation](https://docs.polymarket.us/learn/get-started/account-under-review)
also describes continuing KYC/AML review. Past onboarding plus working reads
is not proof of present unrestricted permission. The
[trading restrictions](https://docs.polymarket.us/learn/trading/access-and-limits/trading-restrictions)
also include contract-specific participant restrictions. No real order or
preview was submitted to test these conditions.

The dependencies are now specific enough for a provider/app evidence request:

| Dependency | Exact answer needed before proceeding |
| --- | --- |
| Crypto settlement | For the exact BRTI up/down family, confirm both venues' 60 sample timestamps, endpoint inclusion, two-decimal tie rounding, immutable opening reference, revision cutoff and all missing-data/cancellation/review outcomes. Reconcile Kalshi's incomplete-data No and discretionary rulebook powers with US deferral/review; establish a payout floor for Kalshi NO + US Up, not just matching normal descriptions. |
| Retail book currentness | Define `transactTime` for full retail marketData. How is an unchanged book certified current? Is there a guaranteed per-market heartbeat, sequence or authoritative snapshot timestamp? Explain advancing times with unchanged ladders, quiet initial snapshots, and older REST snapshots than the socket. |
| US additional charges | Confirm venue and Aeropay processing fees for own-name USD ACH deposits, and any separate retail settlement/clearing charge beyond the published trading commission; obtain the user's bank tariff and chosen allocation. |
| Account permission | Obtain current app/account/provider evidence for approved identity/jurisdiction, unrestricted new-position trading, this retail API key's order entitlement and the selected contract's participant eligibility. Ask for a supported read-only entitlement endpoint if one exists; do not substitute balance access or institutional identity APIs. |

These are drafted requests, **not support messages already sent**. No credentials
or personal/account identifiers are included in this public report.

**Pass outcome:** retain the existing diagnostic history and disabled execution.
Sports approvals remain blocked; the newly identified crypto family is a bounded
alternative research candidate, not permission to expand live execution.
Resume qualifying shadow trials only after one exact, independently reviewed
pair has a verified all-scenario payout floor, reliable two-sided books, current
order permission, complete route-specific costs and funded executable-size
pricing. Until then, zero qualified opportunities is **inconclusive about
economic edge**, not evidence that spreads cannot beat costs.

## 26. Provider clarification requests: initial preparation

This section records the earlier unsent stage. Subsequent authorized delivery
and provider replies are recorded in section 27.

Following authorization to proceed with the evidence requests, verified public
contact sources identified
[Kalshi support](https://help.kalshi.com/en/articles/13823855-contact-kalshi-support)
and [Polymarket US support](https://docs.polymarket.us/learn/faq/contact-support).
The published addresses are `support@kalshi.com` and `support@polymarket.us`.
Kalshi prefers its authenticated support messenger and recommends using the
email registered to the account if email contact is necessary. Existing open
support conversations should not be duplicated.

Two complete, separate RFC 822 email drafts were prepared as session artifacts:
`provider-kalshi-support.eml` and `provider-polymarket-us-support.eml`.
Both omit the From field and carry `X-Unsent: 1`; neither is a send receipt.
They contain only the necessary public contract references, historical public
market-data observations and precise requests for current governing rules,
read-only eligibility verification and additional-cost schedules.
No account IDs, balances, API credentials, signatures or personal identifiers
are included.

The available mail connection is a work mailbox. Its suitability as the
venue-contact sender could not be established, and the user was unavailable
to select a sender. Therefore **neither message was sent, no support ticket
number exists, and no provider response or permission approval was obtained**.
No email was sent from an unconfirmed work address or substituted account.
Private session artifact `provider-requests-status.json` records the exact
prepared/not-sent state and empty receipt fields.

The Kalshi request covers the exact sixty sampled seconds, price rounding,
immutable opening reference, revisions, data deadlines, missing-data No rule,
review/payout exceptions, effective settlement/funding charges and a secure
GET-only/account-screen entitlement-verification process. The US request adds
retail `transactTime` semantics, unchanged-book currentness, per-market heartbeat/
sequence/cache guarantees, Aeropay deposit charges and separate clearing charges.
The expired October 3 22:45-23:00 UTC pair is explicitly historical evidence,
not a requested trade.

Sending remains dependent on an appropriate confirmed sender/channel.
Account-specific verification must use the provider's secure process; an email
answer about a general API feature cannot by itself approve an individual
account or independently validate every settlement scenario.
BTC scanner expansion and qualification remain gated on actual answers and
complete evidence. Production configuration, approvals, live execution settings,
orders, previews, services and original collection clocks were not changed.

## 27. Authorized provider submission and external evidence dependency

After the user authorized continued autonomous work with the disclosed sender,
both existing requests were submitted at **2026-10-04 00:22:47 UTC**.
Each send action returned HTTP 202, and each message was independently found
exactly once in Sent Items with `isDraft=false`, the expected subject and the
published support recipient. The private session manifest stores message/
conversation receipts; personal sender addresses and mailbox identifiers are
not copied into this public report. Neither message contained API credentials,
account balances, private account identifiers or repository source code.

Both venues subsequently replied in the same conversations, acknowledging
delivery. These were **automated support responses, not independent human
contract review or account permission**:

| Provider response | Received at (UTC) | Evidentiary result |
| --- | --- | --- |
| Kalshi Support AI | October 4, 00:24:11 | Pointed to general API documentation/read-only key creation and typical settlement timing, but explicitly did not confirm specific rule interpretations. No sample-set, exceptional-payout, cost or current account-entitlement gap closed. |
| Polymarket US automated support | October 4, 00:24:40 | Could not verify crypto rulebook details, retail currentness guarantees, account eligibility or separate ACH/clearing charges. Cited an obsolete July 1 taker coefficient of 0.06, conflicting with the published October 1 coefficient 0.0695 and current market metadata. Its unsourced cost-basis adjustment claim was not accepted as a verified settlement rule. |
| Polymarket US correction/routing acknowledgment | October 4, 00:30:28 | Acknowledged that its earlier fee reference was not current/authoritative and said the conversation was being passed to its team for the BTC settlement-rule questions. This confirms routing, not the requested payout floor. |

At **00:29:36 UTC**, one focused follow-up in each existing thread requested
human contract-rules/market-operations review. Both follow-ups were verified in
Sent Items. No duplicate initial requests or new parallel support cases were
created. The follow-ups prioritized settlement first, distinguished contract
interpretation from proprietary implementation guidance, and deferred the
remaining permission/data/cost questions until this dependency closes.
Kalshi human routing was not yet confirmed at this capture.

The US follow-up explicitly corrected the stale fee reference using the current
official schedule. **The calculator and metadata rate remain 0.0695.** An
automated answer cannot override a newer dated primary source or justify
undercharging fees. A repeated, appropriately delayed direct Kalshi fee-PDF
check still returned 429; its current-version confirmation remains unresolved.

### Independent normal-settlement check

The expired BTC example was also checked through public resolution endpoints,
without submitting a trade:

| Instrument | Opening BRTI reference | Closing BRTI reference | Final result |
| --- | --- | --- | --- |
| `KXBTC15M-26OCT031900-00` | $84,780.82 | $84,767.33 | `finalized`, result No, YES payout $0.0000; settlement timestamp October 3, 23:00:07.566029 UTC |
| `cpc-btc-updown-15m-2026-10-03-2245z` | $84,780.82 | $84,767.33 | `MARKET_STATUS_RESOLVED`, closed; retail settlement endpoint returned Up/long payout 0 |

The published settlements imply **$1 combined gross payout for a hypothetical
Kalshi NO + US Up pair** in this single normal-resolution example.
The index price $84,767.33 is not a contract
payout or an executable purchase price. Identical references/outcomes in one
window do **not** prove identical sample sets at every boundary, independent
review behavior, a complete all-scenario floor or positive net profit.
No acquisition costs or fills were measured and no trading edge was claimed.
The public evidence is preserved in `provider-normal-settlement-public.json`;
the paired contract remains unapproved.

### Continuation policy

There is now a specific external dependency, not a missing implementation:
human/provider clarification of the contract samples and exceptional payout
rules. Any reply is retained with source and time; automated answers, general
API access and support-ticket acknowledgment are not treated as approvals.
Account-specific verification must still bind to the actual venue account/key
through its secure process. The proposed bank/ACH route is still not an adopted
or funded operator route.

Bounded follow-up checks use only the two existing support conversations, every
four hours for up to 72 hours from submission. They must not resend existing
messages, create duplicate cases, repeatedly chase automated responders,
publish private mailbox records, weaken thresholds or reset collection clocks.
If authoritative clarification arrives, continue the corresponding evidence
review and only advance shadow monitoring after every existing gate is actually
satisfied. If providers cannot establish a defensible floor, reject the family.
If the deadline expires without sufficient answers, stop automated checks and
retain the explicit blocker rather than poll indefinitely.

The same-session continuation was registered and independently read back at
**October 4, 00:35:48 UTC**, with a 240-minute cadence and first scheduled wake
**October 4, 04:35:48 UTC**. The support-query cutoff is **October 7,
00:22:47 UTC**; a wake at or beyond that cutoff must clear the automation
without querying the providers again. This is a registered continuation, not
a claim that an offline host can run or that a provider will answer by then.
The last immediate check found no unprocessed substantive reply. Further
progress now depends on external clarification, not additional feature work.

Live execution remains disabled. BTC discovery/monitoring, approvals, account
settings, funding, orders, previews and production configuration remain unchanged.

## 28. Bitcoin 15-minute investigation and paper pipeline (October 5, 2026)

**Outcome:** implemented and tested a local, opt-in BTC research pipeline.
Observed genuine matching windows and both authenticated market-data feeds.
No approved executable opportunity, measured fill or realized profit was
established. The current venue combination remains **insufficiently evidenced**,
not proven universally impossible. No safeguards were lowered to produce trades.

The preceding section describes historical provider follow-up registration.
The user stopped that automation on October 4; it was cleared and has not been
restarted. This pass did not send support messages, change account permission,
fund venues, place orders, alter the private environment file or deploy changes.

### Primary-source findings

Sources read during this pass:

- [Polymarket US crypto FAQ](https://docs.polymarket.us/faqs/crypto-faqs).
- [US market API](https://docs.polymarket.us/api-reference/market/overview).
- [US fee schedule](https://docs.polymarket.us/fees).
- [Kalshi market API](https://docs.kalshi.com/api-reference/market/get-market).
- Complete [Kalshi CRYPTO governing terms](https://assets.kalshi.com/contract_terms/CRYPTO.pdf),
  SHA256 `fde90b9c0825df277b0b2b2be6239af221eafd01a624c1b2d3a9eaff2d6fe75c`.

| Question | Evidence and consequence |
| --- | --- |
| Normal benchmark | Both describe sixty-sample BRTI averages, a two-decimal reference and Up/YES on equality. Matching published opening values is necessary, not sufficient. |
| Exact sampled seconds | US specifies an inclusive interval ending at the observation time. Kalshi's before/prior wording does not establish identical endpoints. Its policy stays unknown. |
| Benchmark tie rounding | Nearest-two-decimals language does not establish half-even versus half-up. Trading-fee rounding is not benchmark-rounding evidence. |
| Revisions | Kalshi excludes revisions after expiration; that is not evidence of an identical cutoff at the measurement endpoint. |
| Incomplete data | Kalshi describes affected strikes resolving No; US requires complete data or review rather than a partial-data calculation. Exceptional combined payouts remain unproven. |
| Discretionary review | Current Kalshi governing terms retain independent review/payout powers. The API cannot annotate that known provision away. |
| Fees | Observed BTC metadata supplied Kalshi coefficient 0.07 and US 0.0695. US's applicable October 1 schedule remains 0.0695; announced October 7 combo/table-tennis changes do not establish a BTC straight-trade change. |

At 100 contracts priced $0.50, the US calculation charges $1.74, before the
other leg and additional costs. No zero-fee assumption was introduced.

An explicitly conditional synthetic counterexample tests why endpoint precision
matters: two alternative sixty-sample intervals can round to 100.01 and 99.99
around an opening value of 100.00, making hypothetical Kalshi NO + US Up pay
zero. This is **not** a claim about Kalshi's actual endpoint interpretation or an
observed loss. Separate regressions cover benchmark rounding ties.

### Implemented behavior

- Typed, exact-decimal BTC policy and UTC 900-second quarter-hour windows.
  Existing market payloads remain compatible; no database schema migration was
  required for the new JSON fields.
- Kalshi discovery uses `KXBTC15M`, shared series enumeration and effective fee
  overrides. Measurement times come from explicit primary-rule timestamps,
  checked against the close time; trading `open_time` is not substituted.
  US discovery uses paginated crypto metadata and `assetPriceTerms`.
  Its later `endDate` is not the measurement endpoint.
- Matching requires equal asset, benchmark, actual endpoints, published opening
  reference and Up/YES mapping. Known disagreements reject; missing evidence
  remains review. Unknown all-scenario payout floors remain null, not an assumed
  $1. Invalid identity cannot claim covered exceptional scenarios.
- Public approvals still require independent, hash-bound review. Changes to the
  opening reference, window or policy invalidate prior evidence. Annotation
  cannot overwrite known source contingency policies.
- BTC-only discovery refresh, current-window subscription priority, hash-change
  resubscription and expired-match retirement are integrated with existing
  workers. Sports refresh does not overwrite fast-loop BTC lifecycle ownership.
- Both complementary directions use depth-adjusted asks, actual fee parameters,
  additional-cost evidence, age/skew checks and existing risk limits. Window
  availability is checked again at delayed paper/shadow fills. Public BTC shadow
  qualification also requires operator execution-eligibility evidence.
- The dashboard exposes BTC window/reference/policy evidence and a BTC
  opportunity filter. Safe system configuration exposes the opt-in flag and
  refresh interval, never credentials.

### Sizing and persistence defects found while testing

Actual fractional venue increments are preserved. Exhaustively sizing entries
at 0.01 contracts had generated thousands of candidates per direction and
blocked the first diagnostic harness's event loop. Strategy entry increments
now default to **one contract**, separately configurable in risk Settings.
Fractional fills, partial hedges and emergency unwinds still use actual venue
increments. More than 10,000 candidate sizes rejects explicitly with
`SIZING_GRID_TOO_LARGE`; incompatible increments reject with
`QUANTITY_ROUNDING_MISMATCH`. There is no silent coarse-size fallback.
Production diagnostic work already uses a thread; the private feed harness was
corrected to do the same. A subsequent high-volume test exposed a separate
reconstruction/backpressure problem. Kalshi now validates every sequenced delta
in order, offloads reconstruction and coalesces depth publication on a 100-ms
cadence instead of rebuilding full books for every message. This intentionally
adds publication delay; original receipt/provider timestamps are preserved so
age checks include it. Quiet final updates are flushed and lifecycle invalidation
is immediate. Gaps still fail closed. The intermediate prototype's zero-quantity
deletion handling was corrected and added to regression coverage.

Testing also found that lexical whole-string timestamp ordering could discard
a genuinely newer book when crossing from zero fractional seconds to a
one-microsecond timestamp. Persistence now compares normalized UTC seconds and
six-digit fractions. SQLite and real PostgreSQL regressions confirm that the
newer update is retained, an older update is ignored and state survives restart.

### Real market-data results and limitations

Evidence files are retained outside the public repository. They contain public
instrument/price observations, not authentication headers or account responses.
Failed runs were retained, not overwritten.

| Run | Observation | Interpretation |
| --- | --- | --- |
| First REST sample, 14:03 UTC | `KXBTC15M-26OCT051015-15` / `cpc-btc-updown-15m-2026-10-05-1400z`, shared opening 86409.81; US route returned 404 | Catalog presence did not establish consistent individual-route availability. |
| Three REST samples, 14:13 UTC | Same pair; both book routes answered, but US provider age increased from about 3.2 to 16.6 seconds | Stale/empty executable inputs were rejected; the earlier 404 was not assumed permanent. |
| First stream attempt, 14:17:41-14:19:29 UTC | 18,949 Kalshi and 109 US batches; both ultimately closed | Requested roughly 40 seconds but took 107 seconds under CPU-heavy synchronous sizing/reconstruction. This trace does not isolate their contributions or establish provider-feed unreliability. |
| Corrected stream test, 14:29:17-14:29:57 UTC | `KXBTC15M-26OCT051030-30` / `cpc-btc-updown-15m-2026-10-05-1415z`, shared opening **86650.13**, actual 14:15-14:30 UTC window; **1,886 Kalshi and 77 US book batches**, no stream errors | Both subscriptions delivered data; explicit Kalshi acknowledgment and receipt events were recorded. Forty seconds is not continuous-uptime proof. |
| Final three REST samples, 14:32 UTC | Next 14:30-14:45 window, shared opening **86279.12**; Kalshi asks changed from 0.15/0.86 to 0.17/0.84; US individual route returned 404 | Explicit missing-book diagnostics, not fabricated depth or a guessed alternate integration. |
| Higher-volume retest, 14:45:09-14:46:29 UTC | 11,897 Kalshi / 117 US batches; both streams closed; sampling took 80 seconds | The earlier near-expiry success did not establish high-volume reliability. |
| Ordered thread-only reconstruction, 14:53:20-14:54:00 UTC | 6,188 Kalshi / 390 US batches; Kalshi closed with local code 1011, no received close code | Keeping timers responsive alone did not remove full-depth reconstruction/backpressure. It was not treated as a proven provider outage. |
| Intermediate coalescing prototype, 14:58 UTC | Stopped on validation of a legitimate zero-quantity level deletion | Prototype bug fixed; zero-level removal now has regression coverage. Failed artifact retained. |
| Final coalesced test, 15:01:50-15:02:30 UTC | `KXBTC15M-26OCT051115-15` / `cpc-btc-updown-15m-2026-10-05-1500z`, shared opening **85605.89**, actual 15:00-15:15 window; **363 Kalshi / 385 US published book batches**, no stream errors | Every sampled direction had usable two-sided pricing; all 16 depth/fee calculations completed. Batches are coalesced publications, not raw-message counts. |

At 14:45:07 UTC, a separate unauthenticated route trace of the previous
14:30-14:45 US instrument returned HTTP 200 for individual metadata, book and
BBO routes. This confirms inconsistent availability over time, not the precise
cause of the earlier 404 responses.

In the corrected near-expiry stream run, each direction lacked one required
ask and had zero available complementary quantity. Kalshi book ages were
56-168 ms; US ages were 144-1,933 ms, with six direction observations exceeding
the existing inter-venue skew bound. There were **zero qualified directions**.
No conditional profit calculation could overcome missing executable depth.
Neither this short sample nor the REST route failures proves that all BTC
windows lack liquidity or that the strategy is economically negative.

The final early-window test removes that particular missing-depth ambiguity.
Across eight samples, Kalshi book ages were 76-162 ms and US ages 125-243 ms.
Both directions had current depth. Sixteen conditional 100-contract calculations
were negative: **-$2.27 to -$6.47** after known venue fees and configured buffers,
even with unresolved additional costs provisionally zero and an assumed $1
combined payout. Fourteen direction observations had no gross spread at size;
the two positive gross spreads were smaller than fees. No direction qualified.
These are hypothetical acquisition calculations, not actual losses, and they
establish an economically negative sampled window, not a universally negative
BTC strategy.

Additional-cost components and account trading permission were deliberately
left unresolved in these read-only probes. A working authenticated data stream
is not account permission; missing route evidence is not repaired by placing an
order or weakening freshness checks. International Polymarket was not added to
the US operator's executable universe.

### Verification and operation

The complete backend suite passed **182 tests**, including all five tests
against a dedicated, migrated PostgreSQL 17 test database; no PostgreSQL tests
were skipped in that run. Ruff and application mypy passed. BTC regressions
cover discovery pagination, identity/reference changes, policy uncertainty,
sampling/rounding, expiry, authenticated annotation restrictions, persisted
paper hedging, explicit fractional sizing and delayed partial/rejected-second-leg
stress with loss-recording bid-side unwinds.

Frontend type checking, **14 unit tests**, formatting, lint and production build passed. Existing
TanStack compiler and vendor annotation warnings remained nonfatal.
Synthetic successes and injected losses are labeled simulation, not actual
exchange executions or earnings.

Operation is opt-in with `BTC_15M_ENABLED=true` and a 15-second default
`BTC_DISCOVERY_INTERVAL_SECONDS`. Existing persisted risk settings must also
include `BTC`; they are not silently rewritten. `entry_quantity_step` defaults
to `"1"` and can explicitly request a supported fractional increment within the
bounded grid. The credential-free probe command is documented in the README.

**Decision:** the implementation can discover genuine overlaps, receive real
books and exercise the paper pipeline, but public BTC contracts are still
unapproved pending authoritative benchmark/exception evidence, account
permission and complete applicable costs. Observe qualifying real-data shadow
episodes only after those gates are satisfied. The new code remains local and
undeployed; production configuration, live execution and collection clocks
remain unchanged.

## 29. Render diagnosis and profit-maximizing sizing (October 8, 2026)

### Cloud root cause and financial boundary

Read-only inspection of the four existing, repository-bound Render services
found the shared `arb-postgres` instance **suspended**, with `suspenders: user`
and its last update October 6 at 05:02 UTC. API and worker logs showed database
hostname resolution failures. The API returned HTTP 502; its October 7 release
failed predeploy. Background services marked `live` were not healthy agents.
The cause was an unavailable shared dependency, not absent venue credentials
or evidence of a trading edge.

Resuming this existing basic-256mb instance would resume normal database billing.
Approval was requested but the owner was unavailable. The database was **not**
resumed, replaced, resized or exposed, and no other paid service was created.
Cloud availability remains blocked on that explicit owner decision; local
test results are not cloud acceptance.

Worker initialization now retries transient DNS/connection errors using
structured error classes rather than raw exception/credential output. Exhaustion
still raises `DATABASE_OR_MIGRATIONS_UNAVAILABLE`. No healthy role lease is
acquired before successful initialization, and failed startup disposes the pool.
Build provenance prefers Render's actual `RENDER_GIT_COMMIT` over the obsolete
manual `BUILD_VERSION`, retaining the latter as a non-Render fallback.

### Next model improvement: maximize profit, not volume

The previous size search retained the **last** size meeting price thresholds,
which could buy deeper into less favorable prices and reduce total net profit
while still passing minimum thresholds. Ordinary per-venue, total-notional,
fixed-quantity-cap and bankroll modes now choose the greatest **qualifying net
dollar profit** over the bounded, lot-valid grid. Equal-profit choices retain
the smaller size/capital commitment. Explicit max-depth continues selecting the
largest qualifying size; target-profit still stops at its first qualifying
target. All existing budget, quantity, minimum-notional, fee, buffer and risk
limits remain unchanged.

When no size passes, the best modeled result is diagnostic only. It does not
authorize a least-losing trade. The calculation records evaluated/qualifying
size counts, the best modeled size/profit, largest evaluated size/profit and any
early target stop. New calculations use `depth-decimal-v2`; old stored
calculations remain readable without invented sizing evidence.

A deterministic two-tier regression demonstrates the defect: the best modeled
size is 100 contracts, while the still-profitable 200-contract size produces less
net profit after walking deeper prices. Profit mode chooses 100; explicit
max-depth chooses 200. Additional tests cover equal-profit capital ties,
zero-profit rejection, target stopping and preservation of unknown-cost gates.

Candidate diagnostics also expose a current-price **cost hurdle**: modeled fees,
slippage, latency buffer and additional costs, plus the greater of the existing
minimum dollar profit and return requirement. The dashboard reports required
gross profit and its shortfall. This is **not a new executable quote** or a
claim that fees would stay constant if prices improved. Unknown additional
costs remain visibly incomplete; settlement and account permission still gate
qualification separately. No maker-fee exemption, queue fill, funding route or
provider permission was assumed.

### Bounded real-data result

On October 8, 05:16:41-05:17:21 UTC, authenticated market-data-only adapters
observed `KXBTC15M-26OCT080130-30` and
`cpc-btc-updown-15m-2026-10-08-0515z`. Both published opening reference
**82700.52** for the actual 05:15-05:30 UTC window.

Both streams stayed connected: **372 Kalshi and 377 US published book batches**,
no stream errors. Sixteen directional calculations each examined 100 sizes.
No size qualified. Best modeled one-contract results were negative, roughly
**-$0.027 to -$0.078**; their 100-contract comparisons ranged roughly
**-$2.23 to -$6.47**. The existing $1 net-profit requirement left positive
shortfalls, and all additional-cost completeness flags remained false.
These were conditional estimates, not positions, observed losses or proof about
all future windows. No account/order/preview/transfer endpoint was called.

A one-time, bounded read of the existing support conversations found no new
authoritative compatibility or permission evidence. The new Kalshi message
received October 7 at 01:28:03 UTC was an automated conversation-rating prompt.
The earlier US routing acknowledgment remained nonauthoritative. No message was
sent and the cancelled scheduled follow-up was not restarted.

### Verification and release boundary

The full backend suite passed **191 tests**, including all five tests on a
dedicated migrated PostgreSQL 17 database. Ruff and application mypy passed.
The frontend passed **15 unit tests**, types, lint and production build.
The browser test additionally checks the actual sizing/cost-hurdle evidence
panel, guarded paper controls and the existing authenticated workflow.
Linux CI also exposed a hovered-row contrast defect in the directional labels:
the former 4.22:1 contrast did not meet 4.5:1 for small text. The label color was
adjusted to 5.43:1 on that background, and the browser audit now explicitly
hovers the row instead of depending on incidental pointer position.

The requested GitHub release includes the previously local gated BTC pipeline
and this model/recovery work. Live trading remains unavailable. Restoring Render
availability requires resuming the existing database with billing approval and
then verifying migrations, readiness, actual-source worker leases and current
feeds; a pushed commit or `live` service label alone is not a restored deployment.

## 30. US-500: a separate perpetual-futures feasibility investigation

### Verified product, not a binary hedge

The [August 18 CFTC submission](https://www.cftc.gov/filings/ptc/ptc08182617972.pdf)
identifies US500 as a perpetual future on the **MerQube US Large Cap price-return
index (MQ5C)**, without fixed expiry or delivery. Its reference excludes dividends.
Appendix A describes daily funding at the regular equity-market close, or early
close, only on index business days; no premium is measured while the index is
not calculating or its feed is unavailable. Positive funding transfers value
from longs to shorts; negative funding reverses the payment. The filing also
permits discretionary settlement/margin decisions and prospective methodology
changes. Funding is not a guaranteed interest payment.

The downloaded 32-page filing has SHA256
`81d77bf2996bada00a37dedd2748e37641fb0c2c5fa11c16bec6a17fa9934be6`.
It is a submission, **not proof of the currently effective account/market terms**.
The old predictions-series response has fee type
`margin_market_maker_program_fees` and multiplier zero, with an unusable `.pdf`
terms link. That zero is not a verified zero trading fee.

Primary API references:

- [Perps connectivity and rollout](https://docs.kalshi.com/margin.md):
  production REST uses `external-api.kalshi.com/trade-api/v2/margin`;
  account enablement is separate and rollout is member-specific.
- [Market metadata](https://docs.kalshi.com/margin-rest/market/get-market.md)
  and [direct bid/ask depth](https://docs.kalshi.com/margin-rest/market/get-market-orderbook.md).
- [Provisional funding estimate](https://docs.kalshi.com/margin-rest/funding/get-funding-rate-estimate.md)
  and [applied funding history](https://docs.kalshi.com/margin-rest/funding/get-historical-funding-rates.md).
- [Account enablement](https://docs.kalshi.com/margin-rest/exchange/get-enabled-status.md),
  [account-specific notional fee rates](https://docs.kalshi.com/margin-rest/fees/get-fee-tiers.md),
  and [system margin parameters](https://docs.kalshi.com/margin-rest/risk/get-risk-parameters.md).

The generic funding-estimate documentation describes a per-second time-weighted
calculation; the filing describes equally weighted per-minute trade premiums.
No equivalence or current methodology approval was invented. The diagnostic
uses provider-reported applied payments and identifies future estimates as
provisional; it does not reconstruct a supposedly authoritative funding rate.

### Bounded live observations and actual account blocker

Public GETs successfully returned market metadata, direct depth, funding and
system risk parameters. At October 8, 05:48 UTC, metadata identified
`KXUS500PERP`, API contract size **0.001000**, underlying multiplier **1**,
dollar tick **0.0001**, and **fractional trading disabled**. Thus an entry of
1,000 API contracts represented one full underlying index-contract unit; the
entry grid was whole API contracts. The filing's proposed full-contract tick and
minimum fraction were not substituted for this active metadata.

A separately signed **GET-only** account check at 05:50 UTC returned HTTP 200
with **enabled = false**. The account fee endpoint was not queried while disabled.
No margin enablement, transfer, position, order, preview or account mutation
endpoint was called. No private account identifiers or credentials were exported.

Three subsequent live samples at **05:56:20-05:56:26 UTC** completed without
provider errors. At 1,000 API contracts, depth-adjusted purchase cost was
**$13,717.8088**; immediate sale proceeds were **$13,716.5810** in the first two
samples and **$13,716.5450** in the third. Same-snapshot round trips therefore
lost **$1.2278-$1.2638 before fees**, not an arbitrage.

Applied funding history reported:

| Applied time (UTC) | Rate per funding event | Gross short payment per $10,000 event notional |
| --- | --- | --- |
| October 6, 20:00 | 0.0003212319441842 | $3.212319441842 |
| October 7, 20:00 | 0.0010869999556274 | $10.869999556274 |

These are **two historical gross observations**, not realized portfolio returns,
an annual yield or a forecast. At a fixed 1,000 API-contract quantity, the
respective gross credits would have been approximately **$4.4252 and $14.9202**
using each event's reported mark. No historical hedge entry, funding costs,
inventory ownership or successful fill was fabricated.

The next funding estimate was **zero and provisional**, scheduled for October 8
at 20:00 UTC. The cached index reference timestamp was October 7, 20:20 UTC:
an overnight reference is not a fresh tradable hedge quote, and does not by
itself establish a stalled feed. The REST book contained no provider timestamp;
the diagnostic preserved actual request/receipt times rather than refreshing a
fictional exchange timestamp. Live REST ladder ordering contradicted the
documentation, so the adapter canonicalizes ordering and rejects duplicate,
crossed, malformed, negative and off-grid depth.

### What could earn money, and what remains unproven

**Funding carry is a risk-bearing candidate**, not this engine's binary
arbitrage. A short perpetual with an independently executable long hedge could
receive positive funding. The inverse may receive negative funding, but a short
hedge incurs borrowing, dividend obligations and recall risk. MerQube's index is
not automatically identical to SPY, SPX or ES. An exact basket requires
constituent/weight evidence, reliable replication, rebalancing, financing,
dividend treatment and executable broker access.

Net economics must include both perpetual fills/fees, hedge entry/exit,
financing, borrow costs, signed dividends, execution failures and residual exit
basis. No expiry forces the perpetual to converge at a chosen exit time.
Current positive funding can reverse, become zero or be changed under governing
rules. Gains at a separate hedge venue cannot automatically meet a Kalshi
margin call; a nominally neutral portfolio can still be liquidated.

The separate typed Decimal model calculates conditional price/funding/hedge
cashflows, conservative per-leg fee estimates, a break-even funding cashflow,
and venue-local equity both **before and after funding**. A later funding credit
cannot rescue a position that already breached its maintenance requirement.
Unknown essential costs leave net profit null; no scenario qualifies as
arbitrage or authorizes an order.

Five explicitly **synthetic** stress cases seed their price from the observed
mark but assume their own fees, hedge costs, collateral and funding. They cover
favorable funding, reversal, zero funding, hedge failure/basis widening and
liquidation despite offsetting hedge gains. A favorable synthetic result is not
live profitability. Tests also exercise exact numeric JSON parsing, quote/quantity
scaling, direct-book depth, stale/future timestamps, duplicate history, disabled
accounts, missing fees and exclusion from complementary binary adapters.

**Decision:** continue read-only research, not execution. The account is not
perps-enabled, no exact executable hedge is verified, current governing terms
are unbound, and complete account-specific costs/margin survival remain unknown.
The bounded `scripts/us500_probe.py` diagnostic makes those blockers reproducible.
It is deliberately not wired into the binary workers or enabled as a new live
strategy. No profit claim, leverage permission, billing resumption, cloud
restoration or scheduled support prompt was inferred from this investigation.

### Verification

The backend passed **235 tests**: 230 in the full ordinary suite and all five
PostgreSQL cases against a dedicated, migrated PostgreSQL 17 database.
Alembic's model check found no missing migrations. Ruff formatting/lint and
strict application mypy passed. The unchanged frontend passed 15 tests, types,
formatting, lint and its production build. The three real-data samples above
were read-only; the test database was removed afterward.
