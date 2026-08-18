.PHONY: up down db-reset test lint eval

up:        ; docker compose up -d
down:      ; docker compose down
db-reset:  ; docker compose down -v && docker compose up -d
test:      ; uv run pytest
lint:      ; uv run ruff check --fix . && uv run ruff format .
eval:      ; uv run python scripts/evaluate.py
