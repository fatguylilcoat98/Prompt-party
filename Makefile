.PHONY: dev test install

install:
	python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -e ".[dev]"

dev:
	scripts/dev.sh

test:
	.venv/bin/python -m pytest
