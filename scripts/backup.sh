#!/usr/bin/env bash
# Migawka wyglądu Plasmy: konfiguracja, motywy, panele, widżety.
# Użycie: bash backup.sh [nazwa]   (domyślnie data i godzina)
set -euo pipefail

name="${1:-$(date +%Y-%m-%d_%H%M%S)}"
backup_dir="${XDG_DATA_HOME:-$HOME/.local/share}/plasma-tide/backups"
archive="$backup_dir/$name.tar.zst"
mkdir -p "$backup_dir"

if [[ -e "$archive" ]]; then
    echo "Kopia „$name” już istnieje: $archive" >&2
    exit 1
fi

# Wspólna lista ścieżek dla kopii i przywracania.
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

cd "$HOME"
existing=()
for path in "${CONFIG_FILES[@]}" "${MIRRORED_DIRS[@]}" "${ADD_ONLY_DIRS[@]}"; do
    [[ -e "$path" ]] && existing+=("$path")
done

# Plasma zapisuje układ paneli z opóźnieniem — niech zrzuci go teraz na dysk.
qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript 'panels()' >/dev/null 2>&1 || true

tar --zstd -cf "$archive" "${existing[@]}"
chmod 600 "$archive"
echo "Zapisano kopię „$name”: $archive ($(du -h "$archive" | cut -f1))"
