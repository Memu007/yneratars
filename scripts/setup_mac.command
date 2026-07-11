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

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e '.[browser]'
.venv/bin/pip install uv

if [ -x .venv/bin/browser-use ]; then
  echo "Preparando Chromium para Browser Use…"
  PATH="$PWD/.venv/bin:$PATH" .venv/bin/browser-use install || {
    echo "Aviso: no se pudo instalar Chromium automáticamente."
    echo "Reintentá luego con: PATH=\"$PWD/.venv/bin:$PATH\" .venv/bin/browser-use install"
  }
else
  echo "Aviso: Browser Use no quedó instalado dentro del entorno virtual."
fi

chmod +x scripts/*.command scripts/*.sh
echo
echo "Instalación terminada. Abrí scripts/start.command"
