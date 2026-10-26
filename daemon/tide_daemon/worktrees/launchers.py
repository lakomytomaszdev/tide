"""Otwieranie worktree w terminalu (Konsole) albo w Riderze, wznawianie sesji agenta w Konsoli
i otwieranie okna Worktrees.

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
WINDOW = "window"
PYTHON = "/usr/bin/python3"  # systemowy Python z PyQt6 (python3 z pyenv go nie ma)
AGENTS = ("claude", "codex")
# Po zakończeniu agenta karta nie znika: zostaje zwykła powłoka w tym samym katalogu.
# "$0" "$@" to polecenie agenta przekazane jako osobne argumenty — bez sklejania napisów.
KEEP_SHELL = '"$0" "$@"; exec "${SHELL:-/bin/sh}"'


def rider_candidates(home: str) -> list[str]:
    return [
        os.path.join(home, ".local/share/JetBrains/Toolbox/scripts/rider"),
        os.path.join(home, ".local/bin/rider"),
        "/snap/bin/rider",
        *sorted(glob.glob(os.path.join(home, ".local/share/JetBrains/Toolbox/apps/*ider*/bin/rider.sh"))),
        *sorted(glob.glob(os.path.join(home, ".local/share/JetBrains/Toolbox/apps/*/ch-0/*/bin/rider.sh"))),
        *sorted(glob.glob("/opt/*ider*/bin/rider.sh")),
    ]


def window_script(env) -> str:
    """Zainstalowany launcher okna Worktrees ($TIDE_WORKTREES_WINDOW albo XDG_DATA_HOME/tide)."""
    if env.get("TIDE_WORKTREES_WINDOW"):
        return env["TIDE_WORKTREES_WINDOW"]
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/share")
    return os.path.join(data, "tide", "worktrees-window", "tide-worktrees.py")


def task_dialog_script(env) -> str:
    """Zainstalowane okno „Nowe zadanie” ($TIDE_NEW_TASK_WINDOW albo XDG_DATA_HOME/tide/new-task)."""
    if env.get("TIDE_NEW_TASK_WINDOW"):
        return env["TIDE_NEW_TASK_WINDOW"]
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local/share")
    return os.path.join(data, "tide", "new-task", "tide-new-task.py")


class Launchers:
    def __init__(self, env: dict | None = None, path: str | None = None):
        env = os.environ if env is None else env
        home = env.get("HOME") or os.path.expanduser("~")
        search = path if path is not None else env.get("PATH", "")
        self.search = search
        self.home = home
        self.terminal = shutil.which("konsole", path=search) or ""
        self.rider = env.get("TIDE_RIDER") or shutil.which("rider", path=search) or next(
            (p for p in rider_candidates(home) if os.path.isfile(p) and os.access(p, os.X_OK)), "")
        self.scope = shutil.which("systemd-run", path=search) or ""
        # Programy agentów: z PATH usługi, zapasowo z ~/.local/bin (instalator Claude Code),
        # a w ostateczności sama nazwa — wtedy szuka jej powłoka w Konsoli.
        self.agents = {name: shutil.which(name, path=search)
                       or _executable(os.path.join(home, ".local/bin", name)) or name for name in AGENTS}
        self.window_script = window_script(env)
        self.task_dialog_script = task_dialog_script(env)

    @property
    def window(self) -> str:
        """Sprawdzane przy każdym użyciu — okno może zostać zainstalowane po starcie usługi."""
        return self.window_script if os.path.isfile(self.window_script) else ""

    @property
    def task_dialog(self) -> str:
        return self.task_dialog_script if os.path.isfile(self.task_dialog_script) else ""

    def show_task_dialog(self) -> bool:
        """Okno „Nowe zadanie”; drugie uruchomienie tylko pokazuje już otwarte."""
        if not self.task_dialog:
            return False
        return self.spawn(self._scoped([PYTHON, "-s", self.task_dialog]), self.home)

    def available(self) -> dict:
        return {TERMINAL: bool(self.terminal), RIDER: bool(self.rider), WINDOW: bool(self.window)}

    def command(self, app: str, path: str) -> list[str]:
        """Polecenie otwierające katalog albo pusta lista, gdy program jest niedostępny."""
        if app == TERMINAL and self.terminal:
            argv = [self.terminal, "--workdir", path]
        elif app == RIDER and self.rider:
            argv = [self.rider, path]
        elif app == WINDOW and self.window:
            argv = [PYTHON, "-s", self.window]
        else:
            return []
        return self._scoped(argv)

    def terminal_command(self, path: str, argv: list[str]) -> list[str]:
        """Konsole w katalogu path z poleceniem argv (np. wznowienie sesji agenta), po którym
        zostaje powłoka. Pusta lista, gdy Konsoli nie ma."""
        if not self.terminal or not argv:
            return []
        return self._scoped([self.terminal, "--workdir", path, "-e", "/bin/sh", "-c", KEEP_SHELL, *argv])

    def resume_command(self, tool: str, session_id: str, cwd: str) -> list[str]:
        """Wznowienie sesji: Claude Code `claude --resume <id>`, Codex `codex resume <id>`."""
        if tool == "claude":
            agent = [self.agents["claude"], "--resume", session_id]
        elif tool == "codex":
            agent = [self.agents["codex"], "resume", session_id]
        else:
            return []
        return self.terminal_command(cwd, agent)

    def _scoped(self, argv: list[str]) -> list[str]:
        if self.scope:
            argv = [self.scope, "--user", "--scope", "--collect", "--quiet", "--"] + argv
        return argv

    def open(self, app: str, path: str) -> bool:
        if app not in (TERMINAL, RIDER):
            return False  # okno Worktrees otwiera tylko show_window()
        return self.spawn(self.command(app, path), path)

    def open_report(self, path: str) -> bool:
        """Raport poranny w domyślnej przeglądarce (xdg-open). Tylko istniejący plik .html."""
        opener = shutil.which("xdg-open", path=self.search) or ""
        if not opener or not path.endswith(".html") or not os.path.isfile(path):
            return False
        return self.spawn(self._scoped([opener, path]), os.path.dirname(path))

    def show_window(self) -> bool:
        """Okno Worktrees; drugie uruchomienie samo przekazuje prośbę o pokazanie pierwszemu."""
        return self.spawn(self.command(WINDOW, ""), self.home)

    def spawn(self, argv: list[str], path: str) -> bool:
        """Uruchamia argv w katalogu path (w osobnej jednostce systemd, gdy się da)."""
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


def _executable(path: str) -> str:
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else ""
