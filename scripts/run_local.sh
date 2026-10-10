#!/usr/bin/env bash
# Run the API (serving the built frontend) against a local PostgreSQL. Usage: scripts/run_local.sh
set -euo pipefail
cd "$(dirname "$0")/../backend"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://smedi:smedi_local@localhost:5432/smedi}"
export DATA_DIR="${DATA_DIR:-../var}"
export STATIC_DIR="${STATIC_DIR:-../frontend/dist}"
exec python3 -m uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
