.PHONY: install run up down test lint fmt migrate revision

install:
	uv sync

run:
	uv run python -m app.main

up:
	docker compose up --build

down:
	docker compose down

test:
	uv run pytest

lint:
	uv run pyright
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"
