#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
chmod +x scripts/*.command scripts/*.sh 2>/dev/null || true
exec ./scripts/start.command
