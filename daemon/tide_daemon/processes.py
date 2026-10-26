"""Migawka procesów z /proc: kto jest agentem, jego katalog, przodkowie i potomkowie.

Do wyprowadzania stanu potrzebujemy wiedzieć, czy proces sesji jeszcze żyje i czy uruchomił
coś (np. polecenie powłoki) po wywołaniu narzędzia. Do przechodzenia do okna — łańcucha
przodków aż do terminala i zmiennych KONSOLE_* ze środowiska procesu.
"""
import os
from dataclasses import dataclass, field

AGENT_NAMES = {"claude": "claude", "codex": "codex"}
KONSOLE_ENV = ("KONSOLE_DBUS_SERVICE", "KONSOLE_DBUS_WINDOW", "KONSOLE_DBUS_SESSION")


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    start_time: float = 0.0          # sekundy epoki
    cwd: str = ""
    cmdline: tuple = ()
    open_files: tuple = ()           # tylko dla agentów: otwarte pliki dzienników
    env: dict = field(default_factory=dict)  # tylko KONSOLE_*
    tool: str = ""                   # "claude" | "codex" | ""


class ProcessTable:
    def __init__(self, processes):
        self.by_pid = {p.pid: p for p in processes}
        self.children: dict[int, list[ProcessInfo]] = {}
        for p in processes:
            self.children.setdefault(p.ppid, []).append(p)

    def get(self, pid: int) -> ProcessInfo | None:
        return self.by_pid.get(pid)

    def agents(self, tool: str) -> list[ProcessInfo]:
        return [p for p in self.by_pid.values() if p.tool == tool]

    def ancestors(self, pid: int) -> list[ProcessInfo]:
        """Przodkowie od najbliższego, bez init i menedżera usług użytkownika."""
        out, seen = [], set()
        proc = self.by_pid.get(pid)
        while proc and proc.ppid > 1 and proc.ppid not in seen:
            seen.add(proc.ppid)
            parent = self.by_pid.get(proc.ppid)
            if not parent or parent.name in ("systemd", "init"):
                break
            out.append(parent)
            proc = parent
        return out

    def started_after(self, pid: int, ts: float) -> bool:
        """Czy któryś potomek procesu wystartował po chwili ts (np. polecenie z narzędzia)."""
        stack = list(self.children.get(pid, ()))
        while stack:
            proc = stack.pop()
            if proc.start_time >= ts - 1:
                return True
            stack.extend(self.children.get(proc.pid, ()))
        return False


def probe(proc_root: str = "/proc", log_roots: tuple = ()) -> ProcessTable:
    """Czyta /proc. log_roots: katalogi dzienników, których otwarte pliki zbieramy u agentów."""
    boot = _boot_time(proc_root)
    ticks = os.sysconf("SC_CLK_TCK")
    processes = []
    try:
        pids = [int(d) for d in os.listdir(proc_root) if d.isdigit()]
    except OSError:
        return ProcessTable([])
    for pid in pids:
        base = f"{proc_root}/{pid}"
        try:
            with open(f"{base}/stat", encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
        except OSError:
            continue
        # comm jest w nawiasach i może zawierać spacje — dzielimy po ostatnim „)”.
        name = stat[stat.find("(") + 1: stat.rfind(")")]
        fields = stat[stat.rfind(")") + 2:].split()
        try:
            ppid = int(fields[1])
            start = boot + int(fields[19]) / ticks
        except (IndexError, ValueError):
            continue
        proc = ProcessInfo(pid, ppid, name, start)
        cmdline = _read_cmdline(base) if name in AGENT_NAMES or name in ("node", "bun") else ()
        proc.tool = _agent_tool(name, cmdline)
        if proc.tool:
            proc.cmdline = cmdline
            proc.cwd = _readlink(f"{base}/cwd")
            proc.env = _read_env(base)
            proc.open_files = _open_logs(base, log_roots)
        processes.append(proc)
    return ProcessTable(processes)


def _agent_tool(name: str, cmdline: tuple) -> str:
    if name in AGENT_NAMES:
        return AGENT_NAMES[name]
    # Claude Code zainstalowany przez npm działa jako node …/claude.
    if name in ("node", "bun") and len(cmdline) > 1 and os.path.basename(cmdline[1]) in ("claude", "cli.js") \
            and "claude" in cmdline[1]:
        return "claude"
    return ""


def _boot_time(proc_root: str) -> float:
    try:
        with open(f"{proc_root}/stat", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except OSError:
        pass
    return 0.0


def _read_cmdline(base: str) -> tuple:
    try:
        with open(f"{base}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return ()
    return tuple(a.decode("utf-8", "replace") for a in raw.split(b"\0") if a)


def _readlink(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _read_env(base: str) -> dict:
    try:
        with open(f"{base}/environ", "rb") as fh:
            raw = fh.read()
    except OSError:
        return {}
    env = {}
    for item in raw.split(b"\0"):
        key, _, value = item.partition(b"=")
        key = key.decode("utf-8", "replace")
        if key in KONSOLE_ENV:
            env[key] = value.decode("utf-8", "replace")
    return env


def _open_logs(base: str, roots: tuple) -> tuple:
    if not roots:
        return ()
    found = []
    try:
        fds = os.listdir(f"{base}/fd")
    except OSError:
        return ()
    for fd in fds:
        target = _readlink(f"{base}/fd/{fd}")
        if target.endswith(".jsonl") and target.startswith(roots):
            found.append(target)
    return tuple(found)
