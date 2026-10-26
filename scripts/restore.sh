#!/usr/bin/env bash
# Przywraca wygląd Plasmy z migawki zrobionej przez backup.sh.
# Użycie: bash restore.sh [nazwa]   (domyślnie „przed-tide”)
#         bash restore.sh --list
set -euo pipefail

backup_dir="${XDG_DATA_HOME:-$HOME/.local/share}/plasma-tide/backups"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/paths.sh"

if [[ "${1:-}" == "--list" ]]; then
    ls -1t "$backup_dir" 2>/dev/null | sed 's/\.tar\.zst$//'
    exit 0
fi

name="${1:-przed-tide}"
archive="$backup_dir/$name.tar.zst"
if [[ ! -f "$archive" ]]; then
    echo "Nie ma kopii „$name”. Dostępne:" >&2
    ls -1t "$backup_dir" 2>/dev/null | sed 's/\.tar\.zst$//' >&2
    exit 1
fi

# Samo przywracanie też da się cofnąć.
bash "$script_dir/backup.sh" "przed-przywroceniem-$(date +%Y-%m-%d_%H%M%S)"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
tar --zstd -xf "$archive" -C "$staging"

# Plasma przy wyjściu zapisuje układ paneli — musi być zatrzymana, zanim go podmienimy.
systemctl --user stop plasma-plasmashell.service

restore_path() {
    local path="$1" delete="$2"
    if [[ -d "$staging/$path" ]]; then
        mkdir -p "$HOME/$path"
        rsync -a $delete "$staging/$path/" "$HOME/$path/"
    elif [[ -e "$staging/$path" ]]; then
        mkdir -p "$(dirname "$HOME/$path")"
        rsync -a "$staging/$path" "$HOME/$path"
    elif [[ -n "$delete" ]]; then
        # Nie było tego w kopii, więc dodały to późniejsze zmiany.
        rm -rf "${HOME:?}/$path"
    fi
}

for path in "${CONFIG_FILES[@]}" "${MIRRORED_DIRS[@]}"; do
    restore_path "$path" --delete
done
for path in "${ADD_ONLY_DIRS[@]}"; do
    restore_path "$path" ""
done

# Powiadom działające aplikacje i KWin o zmianie wyglądu.
qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
for change in 0 1 2; do  # paleta, czcionki, styl
    dbus-send --session --type=signal /KGlobalSettings org.kde.KGlobalSettings.notifyChange "int32:$change" int32:0 || true
done
dbus-send --session --type=signal /KIconLoader org.kde.KIconLoader.iconChanged int32:0 || true

systemctl --user start plasma-plasmashell.service

echo "Przywrócono wygląd z kopii „$name”."
echo "Dla pełnego efektu (dekoracje okien, kursor, aplikacje GTK) wyloguj się i zaloguj ponownie."
