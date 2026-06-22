.PHONY: setup lint test run
PYTHON ?= python3

setup:
\t$(PYTHON) -m venv .venv
\t. .venv/bin/activate && $(PYTHON) -m pip install -U pip
\t. .venv/bin/activate && $(PYTHON) -m pip install -e ".[dev]"

lint:
\t. .venv/bin/activate && ruff check .
\t. .venv/bin/activate && ruff format --check .

test:
\t. .venv/bin/activate && pytest -q

run:
\t. .venv/bin/activate && $(PYTHON) -m preparation_bdd --help
