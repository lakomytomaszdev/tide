#!/usr/bin/env bash
# Samodzielny klient; kopia nie wymaga repozytorium ani modułów usługi w PYTHONPATH.
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$HOME/.local/bin"
mkdir -p "$dest"
install -m 755 "$src/tide.py" "$dest/tide"
