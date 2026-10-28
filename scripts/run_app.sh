#!/usr/bin/env bash
# Start the app (same as scripts/run_app.py, which loads .env.local, builds the UI when it changed, and serves on HOST:PORT;
# models start on demand). Kept for launchers that run shell scripts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/run_app.py" "$@"
