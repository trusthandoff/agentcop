.PHONY: lint test

lint:
	ruff check src/ tests/
	ruff format src/ tests/

test:
	.venv/bin/pytest tests/ -v
