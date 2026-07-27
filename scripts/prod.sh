#!/usr/bin/env bash
# Production start for bare-metal runs (systemd calls uvicorn directly;
# this is for manual production starts). ONE worker, always.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn app.main:create_app --factory \
    --host "${PROMPT_PARTY_HOST:-127.0.0.1}" \
    --port "${PROMPT_PARTY_PORT:-8710}" \
    --workers 1 --no-access-log --log-level info
