#!/usr/bin/env bash
# Wyłącznie pliki projektu Tide; bez kopiowania metodologii i konfiguracji użytkownika.
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/tide/resources"
mkdir -p "$dest/docs" "$dest/tools"
install -m 644 "$src/docs/agent-plan-standard.md" "$dest/docs/agent-plan-standard.md"
install -m 644 "$src/tools/next_id.py" "$src/tools/check_plans.py" "$dest/tools/"
