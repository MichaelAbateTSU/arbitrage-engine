# Engineering constraints

- Financial values are Decimal/string inputs. Floats are only for timing/display.
- No live order code. Unsafe live settings must fail startup; unavailable methods
  must raise, never report fake success.
- Match approvals bind rule/identity hashes. Preserve fail-closed unknown policies,
  dynamic price grids, timestamps, complementary quantity and fee validity.
- Production schema changes require explicit Alembic revisions. Do not call
  `metadata.create_all` in production.
- Persist intent and reserve risk transactionally before simulating fills.
- Keep demo and public modes isolated. Synthetic data must remain prominently labeled.
- Never log passwords, API headers, signatures, keys, session tokens or raw exceptions.
- Administrative mutations require session, CSRF, input validation and audit records.
- Run Ruff, mypy, pytest and frontend lint/types/tests/build for related changes.
- Deployment cannot substitute a local build for verified cloud health.
