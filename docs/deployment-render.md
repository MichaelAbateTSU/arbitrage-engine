# Render deployment

`render.yaml` defines:

- `arb-api`, FastAPI with explicit predeploy Alembic migration and readiness.
- `arb-market-data-worker`, `arb-analysis-worker`, `arb-maintenance-worker`.
- `arb-frontend`, a non-root nginx SPA and same-origin API/SSE proxy.
- Private `arb-postgres`; Redis is unnecessary in this durable-DB design.

The API image also serves the compiled SPA, permitting a single public origin
without the optional extra frontend hop. Docker port binding uses `$PORT`.
All services use one region, no previews, paper-only flags, and checks-pass deploys.

## Owner setup

1. Review changes and **explicitly** authorize/perform commit and push. Render
   Git-based builds cannot deploy files that exist only in this local checkout.
2. Generate an operator password hash with `scripts/generate_admin.py`; keep the
   password private. Import the Blueprint through the Render dashboard connected
   to this repository.
3. Enter `ADMIN_PASSWORD_HASH` and exact HTTPS `ALLOWED_ORIGINS` for the public UI,
   e.g. `https://arb-frontend-<suffix>.onrender.com`. A generated session secret is
   supplied by Render and referenced consistently.
4. The API predeploy runs `alembic upgrade head`. First-start workers may wait for
   migrations; verify readiness before enabling paper simulation.
5. Verify the frontend's `/health/ready`, login, three worker heartbeats and SSE:
   `python scripts\verify_deployment.py --url https://<frontend> --workers --expected-version <full-tested-commit-sha>`,
   with `ARB_SMOKE_PASSWORD` set in the operator shell. The expected version must
   identify the tested release, not merely whichever older deployment is healthy.
6. Demo is the safe Blueprint default. To observe real public markets, create a
   separate public-mode database/deployment or explicitly isolate data; set
   `DATA_MODE=public` consistently. Do not mix synthetic research with observations.
7. Optionally inject the venue data-stream credentials only into the market-data
   worker. No wallet/execution secrets are needed. International streams are public.
8. Log into Settings and enable **paper only** after reviewing risk limits.

Readiness depends on schema/PostgreSQL and optional configured Redis, not optional
venue availability. Worker/venue outages are separate operational statuses.

## Troubleshooting and rollback

Use Render service logs for structured error codes, build status and worker role
heartbeats. Authentication-required WebSocket messages call for valid data keys,
not enabling trading. Missing start/rule fields belong in operator review.

If all roles fail together with database hostname resolution errors, check the
shared PostgreSQL instance's suspension status before changing credentials or
code. A manually suspended database needs owner approval to resume its normal
paid-plan billing. Do not replace it, open its firewall, upgrade its plan or
reset stored evidence as a workaround. Resume the existing instance only with
approval, then wait for database availability, rerun the API migration/deploy,
and verify all three role leases plus `/health/ready`. A Render worker deployment
marked `live` is not proof of a healthy application heartbeat.

Check individual service suspension too: restoring PostgreSQL cannot start a
separately suspended maintenance worker. Resuming either existing paid resource
requires explicit approval for its normal billing; do not infer approval from a
request to repair code when the operator cannot answer the billing question.

Workers retry transient DNS/connection failures during initialization, log only
the error class and fail explicitly after the bounded startup window. They do
not acquire healthy role leases until database/schema initialization succeeds.
Failed startup disposes the connection pool.

API dependency checks turn raw DNS, refused connections and timeouts into an
explicit unavailable/HTTP 503 readiness result, not an uncaught network exception.
Startup still fails closed without the migrated database. The API disposes its
pool on failed startup as well as normal shutdown, including Redis-close errors.

`RENDER_GIT_COMMIT` takes precedence over a manually set `BUILD_VERSION`; source
provenance and worker heartbeat checks therefore follow the actual deployed
commit. `BUILD_VERSION` remains a fallback for non-Render deployments. All
services must reach the same new source version before rollout is accepted.
The deployment smoke requires the exact three roles, their running/healthy
leases, matching API build and demo/public source, and advancing aware timestamps.
Duplicate/missing roles, mixed releases/sources, stopped roles and cached
heartbeats fail verification. Checks-pass CI does not resume suspended resources
or prove that Render has deployed the new commit.

Before a rollback activate the paper kill switch. Restore a known image/commit
through Render, assess migration compatibility, and inspect persisted submitted
intents rather than deleting or resubmitting them. Do not run destructive downgrade
on live evidence without a backup and explicit approval.

Rotate secrets in Render environment settings, restart affected workers and
observe resynchronization. PostgreSQL backups and restoration need owner-managed
retention/restore drills. Detailed book cleanup is automatic; deleting test data
is an explicit isolated-database operation, not a dashboard reset.

Cloud costs, creating subscriptions and modifying existing unrelated services
require owner decisions. No TradeAgent services are part of this Blueprint.

## Reproducible dependency builds

Python requirements contain official PyPI SHA256 hashes for the installed/tested
graph. Standard builds use PyPI over verified HTTPS. If a local network cannot
complete TLS to its download host, a trusted HTTPS mirror can be explicitly
selected with Docker's `PIP_INDEX_URL` build argument or Compose's
`ARB_PIP_INDEX_URL`; hashes remain enforced. Never disable TLS verification or
remove hashes. A mirror may lag the newest release; the supported framework
range is pinned to Starlette 1.6 and verified against tests/audit.

## Status discipline

A valid Blueprint, image build, local smoke or accessible Render API key is **not**
a successful deployment. Record the actual new service URLs, deployment IDs,
health responses, worker heartbeats and browser smoke before claiming deployment.
