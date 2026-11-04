#!/usr/bin/env bash
# Instaluje tide-tray jako usługę użytkownika systemd (kopią, nie dowiązaniem).
# Ścieżki: $XDG_DATA_HOME/tide/tray (program i reguły z theme/icons/tray.json),
#          $XDG_CONFIG_HOME/systemd/user/tide-tray.service.
# Kafelki tide-tray-* leżą w nakładce ikon Tide (make install-icons), lustro tylko podaje ich nazwy.
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
dest="${data:?}/tide/tray"
unit="${config:?}/systemd/user/tide-tray.service"

/usr/bin/python3 -c "import gi; gi.require_version('Gio', '2.0'); from gi.repository import Gio" \
    || { echo "tide-tray potrzebuje python3-gi (Gio)."; exit 1; }

# Stara wersja oddaje oryginały, zanim podmienimy pliki.
if systemctl --user is-active --quiet tide-tray.service; then
    systemctl --user stop tide-tray.service
fi

rm -rf -- "${dest:?}"
mkdir -p "$dest" "$(dirname "$unit")"
tar -C "$src" --exclude=__pycache__ -cf - tide-tray.py tide_tray | tar -C "$dest" -xf -
cp "$src/../theme/icons/tray.json" "$dest/tray.json"

cat > "$unit" <<UNIT
[Unit]
Description=Tide: kafelki w zasobniku za aplikacje z gotowym obrazkiem (ChatGPT, Discord, OBS)
PartOf=graphical-session.target
After=graphical-session.target plasma-plasmashell.service

[Service]
ExecStart=/usr/bin/python3 -s $dest/tide-tray.py
# Po twardym zabiciu (bez SIGTERM) oryginały wracają tu, zanim usługa wystartuje ponownie.
ExecStopPost=/usr/bin/python3 -s $dest/tide-tray.py --release
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable tide-tray.service
systemctl --user restart tide-tray.service
echo "Zainstalowano tide-tray: $dest"
