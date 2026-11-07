#!/usr/bin/env bash
# Instaluje tide-daemon jako usługę użytkownika systemd (kopią, nie dowiązaniem).
# Ścieżki: $XDG_DATA_HOME/tide/daemon, $XDG_CONFIG_HOME/systemd/user/tide-daemon.service,
#          $XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service (start na żądanie widżetu)
#          i org.tide.Daemon.KRunner.service (start na żądanie KRunnera).
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
dest="$data/tide/daemon"
unit="$config/systemd/user/tide-daemon.service"
activation="$data/dbus-1/services/org.tide.Daemon.service"
krunner_activation="$data/dbus-1/services/org.tide.Daemon.KRunner.service"

rm -rf "$dest"
mkdir -p "$dest" "$(dirname "$unit")" "$(dirname "$activation")"
tar -C "$src" --exclude=__pycache__ -cf - tide-daemon.py tide.py tide_daemon | tar -C "$dest" -xf -
# Kolory stanów (np. karta Konsole z czekającym agentem) z palety, bo kopia działa bez repozytorium.
cp "$src/../theme/palette/mist.json" "$dest/palette.json"

cat > "$unit" <<EOF
[Unit]
Description=Tide: sesje agentów AI przez D-Bus
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=dbus
BusName=org.tide.Daemon
ExecStart=/usr/bin/python3 -s $dest/tide-daemon.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

cat > "$activation" <<EOF
[D-BUS Service]
Name=org.tide.Daemon
Exec=/usr/bin/python3 -s $dest/tide-daemon.py
SystemdService=tide-daemon.service
EOF

# Wtyczka KRunnera ma własną nazwę (osobne połączenie D-Bus w tej samej usłudze) — KRunner
# zapytany przed startem usługi uruchomi ją przez tę samą jednostkę systemd.
cat > "$krunner_activation" <<EOF
[D-BUS Service]
Name=org.tide.Daemon.KRunner
Exec=/usr/bin/python3 -s $dest/tide-daemon.py
SystemdService=tide-daemon.service
EOF

systemctl --user daemon-reload
systemctl --user enable tide-daemon.service
# restart, żeby po aktualizacji działał nowy kod (uruchamia też, gdy usługa stała).
systemctl --user restart tide-daemon.service
echo "Zainstalowano tide-daemon: $dest"
