"""Otwieranie worktree w terminalu (Konsole) albo w Riderze.

Rider z JetBrains Toolbox ma skrypt uruchomieniowy w ~/.local/share/JetBrains/Toolbox/scripts,
którego zwykle nie ma w PATH usługi systemd — dlatego szukamy go też tam (i w kilku innych
typowych miejscach). Gdy Ridera nie ma, widżet ukrywa przycisk.

Programy startują przez `systemd-run --user --scope`, żeby trafiły do własnej jednostki,
a nie do grupy procesów tide-daemon — restart usługi nie zamyka wtedy otwartych okien.
"""
import glob
import os
import shutil
import subprocess
import threading

TERMINAL = "terminal"
RIDER = "rider"


def rider_candidates(home: str) -> list[str]:
    return [
        os.path.join(home, ".local/share/JetBrains/Toolbox/scripts/rider"),
        os.path.join(home, ".local/bin/rider"),
        "/snap/bin/rider",
        *sorted(glob.glob(os.path.join(home, ".local/share/JetBrains/Toolbox/apps/*ider*/bin/rider.sh"))),
        *sorted(glob.glob(os.path.join(home, ".local/share/JetBrains/Toolbox/apps/*/ch-0/*/bin/rider.sh"))),
        *sorted(glob.glob("/opt/*ider*/bin/rider.sh")),
    ]


class Launchers:
    def __init__(self, env: dict | None = None, path: str | None = None):
        env = os.environ if env is None else env
        home = env.get("HOME") or os.path.expanduser("~")
        search = path if path is not None else env.get("PATH", "")
        self.terminal = shutil.which("konsole", path=search) or ""
        self.rider = env.get("TIDE_RIDER") or shutil.which("rider", path=search) or next(
            (p for p in rider_candidates(home) if os.path.isfile(p) and os.access(p, os.X_OK)), "")
        self.scope = shutil.which("systemd-run", path=search) or ""

    def available(self) -> dict:
        return {TERMINAL: bool(self.terminal), RIDER: bool(self.rider)}

    def command(self, app: str, path: str) -> list[str]:
        """Polecenie otwierające katalog albo pusta lista, gdy program jest niedostępny."""
        if app == TERMINAL and self.terminal:
            argv = [self.terminal, "--workdir", path]
        elif app == RIDER and self.rider:
            argv = [self.rider, path]
        else:
            return []
        if self.scope:
            argv = [self.scope, "--user", "--scope", "--collect", "--quiet", "--"] + argv
        return argv

    def open(self, app: str, path: str) -> bool:
        argv = self.command(app, path)
        if not argv or not os.path.isdir(path):
            return False
        try:
            proc = subprocess.Popen(argv, cwd=path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        # Zbieramy proces po zakończeniu, żeby nie zostawał zombie.
        threading.Thread(target=proc.wait, daemon=True).start()
        return True
