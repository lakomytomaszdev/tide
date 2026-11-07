#!/usr/bin/env bash
# Zatrzymuje tide-tray (oddaje oryginały w zasobniku) i usuwa usługę.
set -euo pipefail

data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
state="${XDG_STATE_HOME:-$HOME/.local/state}"
dest="${data:?}/tide/tray"
unit="${config:?}/systemd/user/tide-tray.service"

if [[ -e "$unit" ]]; then
    systemctl --user disable --now tide-tray.service || true
fi
# Gdyby usługa padła bez sprzątania: zdejmij wyłączenia, które zapisała.
if [[ -f "$state/tide/tray-mirror.json" && -f "$dest/tide-tray.py" ]]; then
    /usr/bin/python3 -s "$dest/tide-tray.py" --release || true
fi
rm -f -- "$unit"
rm -rf -- "${dest:?}"
rmdir "$data/tide" 2>/dev/null || true
systemctl --user daemon-reload || true
echo "Odinstalowano tide-tray."
