#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "Falta instalar. Ejecutando setup_mac.command…"
  "$(dirname "$0")/setup_mac.command"
fi

PORT="${TARS_PORT:-8765}"
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -c 'import certifi; print(certifi.where())' 2>/dev/null || echo '')}"

open -a "Brave Browser" "http://127.0.0.1:${PORT}" || {
  echo "No se pudo abrir Brave. Verificá que esté instalado; TARS no abrirá otro navegador automáticamente."
}

echo "TARS dev server — se reinicia automáticamente ante cambios en app/ o static/"
exec .venv/bin/watchmedo auto-restart \
  --directory=./app \
  --directory=./static \
  --pattern="*.py;*.js;*.html;*.css;*.json" \
  --recursive \
  -- \
  .venv/bin/python -m app.server
