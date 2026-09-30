# Contributing

Use Python 3.12+, Node 24, PostgreSQL 17 and the checked-in dependency locks.
Native Windows setup is in README. Install with `scripts\bootstrap.ps1`.

Add examples/properties for financial changes; documented wire fixtures for adapter
changes; restart/idempotency tests for lifecycle changes. Do not loosen gates or
delete tests to make signals appear. Keep capture data sanitized and marked with
source/date. Never use real private keys in fixtures.

Schema changes: run Alembic autogeneration against an isolated database, inspect
the generated revision, verify upgrade/downgrade and `alembic check`. API startup
only verifies schema; migrations are a deliberate deployment step.

The UI builds on the same-origin API; monetary JSON fields are decimal strings.
New pages need loading, empty, error and keyboard-accessible states.

Do not commit or push changes without explicit owner authorization.
