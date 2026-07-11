#!/bin/bash
set -u
cd "$(dirname "$0")/.."
FAIL=0
echo "== Diagnóstico TARS Local =="
python3 - <<'PY' || FAIL=1
import sys
print("Python", sys.version.split()[0], "OK" if sys.version_info >= (3,11) else "INSUFICIENTE")
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY
PYTEST=python3
if [ -x .venv/bin/python ]; then
  PYTEST=.venv/bin/python
  .venv/bin/python - <<'PY' || FAIL=1
import importlib.util
print("Browser Use:", "OK" if importlib.util.find_spec("browser_use") else "NO INSTALADO")
PY
else
  echo "Entorno virtual: NO INSTALADO"
  FAIL=1
fi
if command -v security >/dev/null 2>&1; then echo "Keychain macOS: OK"; else echo "Keychain macOS: no disponible (se usará archivo 0600)"; fi
if [ -d "/Applications/Google Chrome.app" ]; then echo "Google Chrome: OK"; else echo "Google Chrome: no detectado"; fi
"$PYTEST" -m unittest discover -s tests -v || FAIL=1
exit $FAIL
