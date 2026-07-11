#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== TARS Local · instalación macOS =="
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Se necesita Python 3.11 o superior. Instalalo con: brew install python@3.12")
print("Python:", sys.version.split()[0])
PY

BRAVE_APP="/Applications/Brave Browser.app"
if [ ! -d "$BRAVE_APP" ] && [ ! -d "$HOME/Applications/Brave Browser.app" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Brave Browser no está instalado. Instalándolo con Homebrew…"
    brew install --cask brave-browser || {
      echo "Aviso: no se pudo instalar Brave automáticamente."
      echo "Instalalo manualmente desde brave.com o ejecutá: brew install --cask brave-browser"
    }
  else
    echo "Aviso: Brave Browser no está instalado y Homebrew no está disponible."
    echo "Instalá Brave manualmente antes de usar CASE. TARS no usará Chrome ni Chromium como fallback."
  fi
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e '.[browser]'

echo "Browser Use configurado para usar el Brave instalado en macOS."
echo "No se descargará Chromium mediante browser-use install."

chmod +x scripts/*.command scripts/*.sh
echo
echo "Instalación terminada. Abrí scripts/start.command"
