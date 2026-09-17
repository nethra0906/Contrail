.PHONY: up down logs seed migrate revision train test test-unit test-integration lint fmt typecheck bench frontend-dev api-dev ingest-dev

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	.venv/Scripts/alembic upgrade head

revision:
	.venv/Scripts/alembic revision --autogenerate -m "$(m)"

seed:
	.venv/Scripts/python -m scripts.seed_reference_data

train:
	.venv/Scripts/python -m ml.train.train_trajectory
	.venv/Scripts/python -m ml.train.train_eta

test: test-unit

test-unit:
	.venv/Scripts/python -m pytest tests/unit -v

test-integration:
	.venv/Scripts/python -m pytest tests/integration -v

lint:
	.venv/Scripts/ruff check services ml tests scripts

fmt:
	.venv/Scripts/ruff format services ml tests scripts

typecheck:
	.venv/Scripts/mypy services --ignore-missing-imports

api-dev:
	.venv/Scripts/uvicorn services.api.main:app --reload --port 8000

ingest-dev:
	.venv/Scripts/python -m services.ingest.main

frontend-dev:
	cd frontend && npm run dev
