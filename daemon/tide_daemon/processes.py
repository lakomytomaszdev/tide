"""Migawka procesów z /proc: kto jest agentem, jego katalog, przodkowie i potomkowie.

Do wyprowadzania stanu potrzebujemy wiedzieć, czy proces sesji jeszcze żyje i czy uruchomił
coś (np. polecenie powłoki) po wywołaniu narzędzia. Do przechodzenia do okna — łańcucha
przodków aż do terminala i zmiennych KONSOLE_* ze środowiska procesu.
"""
import os
import time
from dataclasses import dataclass, field, replace

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
    """Pełny skan /proc (każdy proces). log_roots: katalogi dzienników, których otwarte pliki zbieramy u agentów.
    Monitor sesji używa przyrostowej sondy `Prober`; ta funkcja zostaje dla jednorazowych odczytów (CLI)."""
    boot = _boot_time(proc_root)
    ticks = os.sysconf("SC_CLK_TCK")
    try:
        pids = _pids(proc_root)
    except OSError:
        return ProcessTable([])
    processes = [p for p in (_read_process(proc_root, pid, boot, ticks, log_roots) for pid in pids) if p]
    return ProcessTable(processes)


# Pełny skan co tyle sekund także na postoju: zmiany, których nie widać po liście PID-ów (exec w tym samym procesie,
# ponowne użycie numeru PID), trafiają do migawki najpóźniej po tym czasie.
FULL_EVERY_S = 300.0
# Świeży proces (młodszy niż tyle sekund od startu) jest czytany ponownie w każdej sondzie: wrapper, który robi
# `exec claude` kilka sekund po `fork` (albo łańcuch dwóch `exec`), trafia do migawki w najbliższej sondzie, a nie
# dopiero przy pełnym skanie (PT-385). 6 ticków monitora (TICK_MS 5 s); koszt — ponowny odczyt kilku świeżych PID-ów.
SETTLE_S = 30.0


class Prober:
    """Sonda przyrostowa /proc (K-98 #5, PT-346) — jedna instancja na wątek sondy monitora sesji.

    Pełny skan (odczyt `/proc/<pid>/stat` każdego procesu) jest tylko w pierwszym przebiegu i co `full_every` s.
    Pomiędzy nimi przebieg:

    - czyta samą listę `/proc` (jedno `listdir`) i porównuje zbiór PID-ów z poprzednim — tak rozpoznaje „brak
      zmian” bez pełnego skanu: nowy potomek to nowy PID, zakończony potomek to brakujący PID,
    - czyta `stat` tylko nowych PID-ów, potomków zakończonych procesów (zmiana rodzica) i świeżych PID-ów, które
      nie są agentami (proces czytany tuż po `fork` mógł jeszcze nie zrobić `exec`): nowy PID jest czytany jeszcze
      raz w następnym przebiegu, a dalej — dopóki od jego startu minęło mniej niż `SETTLE_S` s (zegar `wall`),
    - u agentów (claude/codex, zwykle kilka procesów) zawsze czyta `stat` (ten sam start i nazwa — ten sam proces),
      katalog i otwarte pliki dzienników, bo przełączenie dziennika nie zmienia listy PID-ów,
    - bez żadnej zmiany zwraca TEN SAM obiekt `ProcessTable` (ponowne użycie wyniku między odczytami).

    `counters`: `probes`, `fullScans`, `statReads` (odczyty `/proc/<pid>/stat`), `reused` (ta sama tabela)."""

    def __init__(self, proc_root: str = "/proc", full_every: float = FULL_EVERY_S, monotonic=time.monotonic,
                 wall=time.time, settle_s: float = SETTLE_S):
        self.proc_root = proc_root
        self.full_every = full_every
        self.monotonic = monotonic
        self.wall = wall              # zegar epoki — porównywany z czasem startu procesu (`ProcessInfo.start_time`)
        self.settle_s = settle_s
        self.counters = {"probes": 0, "fullScans": 0, "statReads": 0, "reused": 0}
        self._infos: dict[int, ProcessInfo] = {}
        self._table: ProcessTable | None = None
        self._full_at = 0.0
        self._settle: set[int] = set()
        self._ticks = os.sysconf("SC_CLK_TCK")
        self._boot = 0.0

    def probe(self, log_roots: tuple = ()) -> ProcessTable:
        self.counters["probes"] += 1
        try:
            pids = set(_pids(self.proc_root))
        except OSError:
            return ProcessTable([])
        now = self.monotonic()
        if self._table is None or now - self._full_at >= self.full_every:
            return self._full(pids, log_roots, now)
        old = self._table
        gone = set(self._infos) - pids
        new = pids - set(self._infos)
        reread = new | (self._settle & pids)
        for pid in gone:   # osierocone dzieci dostają nowego rodzica
            reread.update(child.pid for child in old.children.get(pid, ()) if child.pid in pids)
        changed = bool(gone)
        infos = {pid: info for pid, info in self._infos.items() if pid in pids} if gone else dict(self._infos)
        for pid in reread:
            info = self._read(pid, log_roots)
            if info is None:
                infos.pop(pid, None)
            else:
                infos[pid] = info
            changed = changed or info != self._infos.get(pid)
        for pid, info in list(infos.items()):
            if info.tool and pid not in reread:
                fresh = self._refresh_agent(info, log_roots)
                if fresh is not info:
                    changed = True
                    if fresh is None:
                        infos.pop(pid)
                    else:
                        infos[pid] = fresh
        # Nowy PID — jeszcze jeden odczyt w następnej sondzie (jak dotąd); dalej tylko świeży proces bez rozpoznanego
        # agenta (agenta pilnuje `_refresh_agent`). Na postoju bez nowych PID-ów zbiór jest pusty — zero ponownych odczytów.
        young = self.wall() - self.settle_s
        self._settle = {pid for pid in new | (self._settle & pids) if pid in infos
                        and (pid in new or (not infos[pid].tool and infos[pid].start_time > young))}
        if not changed:
            self.counters["reused"] += 1
            return old
        self._infos = infos
        self._table = ProcessTable(list(infos.values()))
        return self._table

    def _full(self, pids, log_roots, now) -> ProcessTable:
        self.counters["fullScans"] += 1
        self._boot = _boot_time(self.proc_root)
        infos = {}
        for pid in pids:
            info = self._read(pid, log_roots)
            if info is not None:
                infos[pid] = info
        young = self.wall() - self.settle_s   # świeże procesy z pełnego skanu też mogą jeszcze zrobić `exec`
        self._settle = {pid for pid, info in infos.items() if not info.tool and info.start_time > young}
        self._infos, self._full_at = infos, now
        self._table = ProcessTable(list(infos.values()))
        return self._table

    def _read(self, pid: int, log_roots) -> ProcessInfo | None:
        self.counters["statReads"] += 1
        return _read_process(self.proc_root, pid, self._boot, self._ticks, log_roots)

    def _refresh_agent(self, info: ProcessInfo, log_roots) -> ProcessInfo | None:
        """Agent bez zmiany numeru PID: ten sam proces (start, nazwa) — nowy katalog i otwarte dzienniki;
        inny proces pod tym numerem — pełny odczyt; zakończony — None."""
        base = f"{self.proc_root}/{info.pid}"
        self.counters["statReads"] += 1
        stat = _read_stat(base, self._boot, self._ticks)
        if stat is None:
            return None
        name, ppid, start = stat
        if name != info.name or start != info.start_time:
            return self._read(info.pid, log_roots)
        cwd, files = _readlink(f"{base}/cwd"), _open_logs(base, log_roots)
        if (ppid, cwd, files) == (info.ppid, info.cwd, info.open_files):
            return info
        return replace(info, ppid=ppid, cwd=cwd, open_files=files)


def _pids(proc_root: str) -> list[int]:
    return [int(d) for d in os.listdir(proc_root) if d.isdigit()]


def _read_stat(base: str, boot: float, ticks: int):
    """(nazwa, ppid, start) z `<base>/stat` albo None (proces zniknął, zły format)."""
    try:
        with open(f"{base}/stat", encoding="utf-8", errors="replace") as fh:
            stat = fh.read()
    except OSError:
        return None
    # comm jest w nawiasach i może zawierać spacje — dzielimy po ostatnim „)”.
    name = stat[stat.find("(") + 1: stat.rfind(")")]
    fields = stat[stat.rfind(")") + 2:].split()
    try:
        return name, int(fields[1]), boot + int(fields[19]) / ticks
    except (IndexError, ValueError):
        return None


def _read_process(proc_root: str, pid: int, boot: float, ticks: int, log_roots) -> ProcessInfo | None:
    base = f"{proc_root}/{pid}"
    stat = _read_stat(base, boot, ticks)
    if stat is None:
        return None
    name, ppid, start = stat
    proc = ProcessInfo(pid, ppid, name, start)
    cmdline = _read_cmdline(base) if name in AGENT_NAMES or name in ("node", "bun") else ()
    proc.tool = _agent_tool(name, cmdline)
    if proc.tool:
        proc.cmdline = cmdline
        proc.cwd = _readlink(f"{base}/cwd")
        proc.env = _read_env(base)
        proc.open_files = _open_logs(base, log_roots)
    return proc


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
