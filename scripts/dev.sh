#!/usr/bin/env bash
# Development startup command for Prompt Party.
# Usage: scripts/dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e ".[dev]"
fi

if [ ! -f .env ]; then
    echo "No .env found; using defaults from .env.example semantics."
fi

HOST="${PROMPT_PARTY_HOST:-127.0.0.1}"
PORT="${PROMPT_PARTY_PORT:-8710}"

exec .venv/bin/uvicorn "app.main:create_app" --factory --reload --host "$HOST" --port "$PORT"
