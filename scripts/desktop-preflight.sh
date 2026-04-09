#!/usr/bin/env bash
set -euo pipefail

echo "Desktop preflight for el-salvador-laws"
echo

check() {
  local label="$1"
  local cmd="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "[ok] $label: $(command -v "$cmd")"
  else
    echo "[missing] $label"
  fi
}

check "node" node
check "npm" npm
check "python3" python3
check "git" git
check "tesseract" tesseract
check "ocrmypdf" ocrmypdf
check "psql" psql

echo
echo "If Playwright is installed in the project, run:"
echo "  npx playwright install"
