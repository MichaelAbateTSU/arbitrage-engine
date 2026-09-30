.PHONY: install dev backend frontend workers test test-unit test-integration test-e2e lint format typecheck migrate seed smoke replay
install:
	python -m pip install -r backend/requirements-dev.lock
	python -m pip install --no-deps -e backend
	cd frontend && npm ci
dev:
	docker compose up --build
backend:
	cd backend && uvicorn app.main:app --reload
frontend:
	cd frontend && npm run dev
workers:
	cd backend && python -m app.workers all
test:
	pytest backend/tests -q
	cd frontend && npm test
test-unit:
	pytest backend/tests/test_domain.py backend/tests/test_paper.py -q
test-integration:
	pytest backend/tests/test_integration.py backend/tests/test_adapters.py -q
test-e2e:
	cd frontend && npm run test:e2e
lint:
	ruff check backend scripts
	cd frontend && npm run lint && npm run format:check
format:
	ruff check backend scripts --fix
	ruff format backend scripts
	cd frontend && npm run format
typecheck:
	mypy backend/app
	cd frontend && npm run typecheck
migrate:
	cd backend && alembic upgrade head
seed:
	python scripts/seed_demo_data.py
smoke:
	python scripts/smoke_test.py
replay:
	python scripts/replay_market_data.py --help
