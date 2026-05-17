.PHONY: help install test lint fmt clean dev docker docker-test

help:
	@echo "Targets:"
	@echo "  install      editable install with dev extras"
	@echo "  test         run pytest"
	@echo "  lint         run ruff check + format check"
	@echo "  fmt          run ruff format"
	@echo "  dev          run the FastAPI app with hot reload on :8080"
	@echo "  clean        remove build artefacts and caches"
	@echo "  docker       build the production docker image"
	@echo "  docker-test  run the test suite inside docker"

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test:
	pytest tests/

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

docker:
	docker compose build

docker-test:
	docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
