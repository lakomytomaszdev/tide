#!/usr/bin/env bash
# Zatrzymuje i usuwa tide-daemon wraz z plikiem usługi i aktywacją D-Bus.
set -euo pipefail

data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
state="${XDG_STATE_HOME:-$HOME/.local/state}"
unit="$config/systemd/user/tide-daemon.service"

if [[ -e "$unit" ]]; then
    systemctl --user disable --now tide-daemon.service || true
fi
rm -f "$unit" "$data/dbus-1/services/org.tide.Daemon.service" "$data/dbus-1/services/org.tide.Daemon.KRunner.service"
rm -rf "$data/tide/daemon"
rm -rf "$data/tide/resources"
# Zatrzymana usługa sama przywraca karty Konsole; plik zostaje tylko po twardym zabiciu procesu.
rm -f "$state/tide/konsole-tabs.json"
# Historia odczytów Limitów AI i pamięć wysłanych powiadomień (limits/history.py, limits/alerts.py).
rm -f "$state/tide/limits-history.jsonl" "$state/tide/limits-history.jsonl.tmp" \
      "$state/tide/limits-alerts.json" "$state/tide/limits-alerts.json.tmp"
rmdir "$state/tide" 2>/dev/null || true
rmdir "$data/tide" 2>/dev/null || true
systemctl --user daemon-reload || true
echo "Odinstalowano tide-daemon."
