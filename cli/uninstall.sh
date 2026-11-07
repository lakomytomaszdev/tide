#!/usr/bin/env bash
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$src/agents/install.py" --uninstall
rm -f "$HOME/.local/bin/tide"
