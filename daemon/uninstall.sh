#!/usr/bin/env bash
# Zatrzymuje i usuwa tide-daemon wraz z plikiem usługi i aktywacją D-Bus.
set -euo pipefail

data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
unit="$config/systemd/user/tide-daemon.service"

if [[ -e "$unit" ]]; then
    systemctl --user disable --now tide-daemon.service || true
fi
rm -f "$unit" "$data/dbus-1/services/org.tide.Daemon.service"
rm -rf "$data/tide/daemon"
rmdir "$data/tide" 2>/dev/null || true
systemctl --user daemon-reload || true
echo "Odinstalowano tide-daemon."
