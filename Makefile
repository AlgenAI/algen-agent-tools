.PHONY: install format lint typecheck test check

install:
	python -m pip install -e '.[dev]'
format:
	ruff format src tests scripts
lint:
	ruff format --check src tests scripts
	ruff check src tests scripts
typecheck:
	mypy src/algen_agent_tools
test:
	pytest -q -p no:cacheprovider
check: lint typecheck test
