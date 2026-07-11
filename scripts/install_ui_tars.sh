#!/bin/bash
set -euo pipefail
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew no está instalado. UI-TARS es opcional; instalalo manualmente desde su página de releases."
  exit 1
fi
brew install --cask ui-tars
cat <<'EOF'
UI-TARS instalado. Ahora habilitá manualmente:
  Ajustes del Sistema → Privacidad y seguridad → Accesibilidad
  Ajustes del Sistema → Privacidad y seguridad → Grabación de pantalla
Usá un solo monitor durante las primeras pruebas.
EOF
