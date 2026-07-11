#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "Falta instalar. Ejecutando setup_mac.command…"
  "$(dirname "$0")/setup_mac.command"
fi
PORT="${TARS_PORT:-8765}"
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -c 'import certifi; print(certifi.where())' 2>/dev/null || echo '')}"
.venv/bin/python -m app.server &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT INT TERM
sleep 1
open "http://127.0.0.1:${PORT}" || true
wait "$PID"
