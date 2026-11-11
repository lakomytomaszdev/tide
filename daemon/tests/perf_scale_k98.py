#!/usr/bin/python3
"""Reproduktor skali produkcji K-98 (PT-362; wspólny z ewentualnym #7): czasy odpowiedzi i koszt wątku głównego usługi
przy Tablicy wielkości produkcyjnej.

Wyłącznie dane syntetyczne, bez kopii danych właściciela. Wszystko powstaje w katalogu tymczasowym:

- Tablica: `--cards` kart (domyślnie 98) w trzech projektach z `ai-docs: tide`, każda z dziennikiem przepływu
  (`flow/K-n/journal.jsonl` i kopia usługi w izolowanym `XDG_STATE_HOME`) oraz `--steps` katalogami kroków
  `flow/K-n/steps/<krok>/{in,out}` z plikami jak wyjście kroków; w `docs/<projekt>/` plany, specyfikacje i kartki.
  Domyślnie ≥ 4000 katalogów i ≥ 13 000 plików (liczone na dysku po zbudowaniu, bez `.git`, pole `scale`),
- `--sessions` sesji Claude Code (domyślnie 200) w izolowanym `HOME/.claude/projects`, każda w osobnym worktree
  (`--worktrees`, domyślnie 200, `git worktree add` z trzech repozytoriów projektów) — `scanner.sessions`
  i `gitinfo.describe` mają realny koszt,
- prawdziwa usługa (`<src>/daemon/tide-daemon.py`) jako osobny proces na prywatnej magistrali (własny dbus-daemon bez
  katalogów usług), z izolowanymi HOME i XDG_*, `TIDE_BOARD_DIR` w katalogu tymczasowym, atrapą `incus`
  (`fake_incus.py`, `TIDE_INCUS_BIN`) i `box.enabled` z `--box` w `$XDG_CONFIG_HOME/tide/cardflow.json`. Wyłączone są
  tylko dodatki dotykające pulpitu, sieci albo portów hosta (powiadomienia, karty Konsole, YouTrack, limity, modele,
  Docker, proxy pudełka, raport, bezczynność, wygląd aktywności, monitory, skróty). Sonda procesów, monitor sesji,
  koordynacja, KRunner (indeks wyszukiwania) i przepływ karty (tick co 5 s) działają jak w produkcji.

Zmiany w tle (wątki reproduktora, od rozgrzewki do końca pomiaru), tempo w polu `params.background`:
- dziennik karty: co `--journal-every` s (domyślnie 3 s — dłużej niż cisza DEBOUNCE_MS 2 s, więc każda seria kończy
  się synchronizacją, jak zapisy ticku co 5 s w produkcji) kolejna karta dostaje atomową podmianę
  `flow/K-n/journal.jsonl` tą samą drogą co odtworzenie z kopii (`.tide-tmp-` + rename, treść równa kopii usługi —
  bez nowych zdarzeń, żeby nie ścigać się z tickiem o numer `seq`) i dopisek do pliku kroku tej karty
  (`steps/<krok>/out/progress.log`, zmiana treści do commita „sync external changes”),
- sesje agentów: co `--session-every` s (domyślnie 0,25 s) dopisek tury w jednej z `--active` sesji (domyślnie 20),
- plan w `docs/<projekt>/plans/`: co `--docs-every` s (domyślnie 10 s) nowa treść jednego planu (agent pisze plan).

Pomiar (po rozgrywce `--warmup` s od pierwszej gotowej migawki):
- faza `load`: `/Board GetCardFlowSettings` i `/Board Snapshot` co `--period` s (domyślnie 0,5 s) przez `--duration` s
  (domyślnie 60 s), czasy ścienne każdego wywołania (surowe próbki, p95 nearest-rank: k = ⌈0,95·n⌉, max),
- faza `idle`: te same zmiany w tle, bez wywołań, `--idle` s (domyślnie 60 s; 0 — bez fazy),
- w każdej fazie: próbnik usługi (`sampler.py`, plik żądania w izolowanym `XDG_RUNTIME_DIR`, raport w izolowanym
  `XDG_STATE_HOME`) przez cały czas fazy — udział funkcji w próbkach (`shares`: liczba próbek z funkcją na stosie /
  liczba przebiegów próbnika; każdy przebieg ma dokładnie jedną próbkę wątku głównego, więc dla funkcji wątku
  głównego — `_rewatch`, `sync`, `publish`, `recompute` — to udział w próbkach wątku głównego) oraz CPU procesu usługi
  z `/proc/<pid>/stat` (utime+stime; osobno zakończone procesy potomne).

Wynik JSON: parametry i skala zmierzona na dysku, SHA, `/proc/loadavg` przed i po, liczba rdzeni, surowe próbki,
p95/max, CPU i udziały funkcji obu faz oraz pełne raporty próbnika.

Użycie (z katalogu repozytorium):
    /usr/bin/python3 daemon/tests/perf_scale_k98.py --out wynik.json [--box on|off] [--src <katalog z daemon/>]
        [--sha <SHA>] [--duration 60] [--period 0.5] [--idle 60] [--warmup 20] [--keep]

Skrypt niczego nie zapisuje poza katalogiem tymczasowym (usuwany na końcu, `--keep` go zostawia) i plikiem `--out`.
Testy jednostkowe nie mierzą czasu na sztywno; ten skrypt jest wyłącznie narzędziem pomiaru.
"""
import argparse
import contextlib
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.dirname(os.path.dirname(HERE))
FAKE_INCUS = os.path.join(HERE, "fake_incus.py")
SERVICE = "org.tide.Daemon"
BOARD_PATH, BOARD_IFACE = "/Board", "org.tide.Daemon.Board"
METHODS = ("GetCardFlowSettings", "Snapshot")
PROJECTS = ("plasma-tide", "alpha", "beta")
# Minimalna skala produkcji z opisu karty K-98 (#6) — domyślne parametry muszą ją dawać (sprawdzane po zbudowaniu).
PRODUCTION = {"cards": 98, "dirs": 4000, "files": 13000, "sessions": 200, "worktrees": 200}
STEP_FILES = (("in", "prompt.md"), ("in", "handoff.md"), ("in", "context.json"), ("out", "note.md"),
              ("out", "result.json"), ("out", "review.md"), ("out", "progress.log"), ("", "step.log"),
              ("", "process.json"))
# Funkcje raportowane osobno (udział w próbkach); klucze jak w raporcie próbnika: „plik.py:funkcja”.
WATCHED_FUNCTIONS = ("board_monitor.py:_rewatch", "board_monitor.py:sync", "board_monitor.py:_mutate",
                     "board_monitor.py:publish", "board_monitor.py:_publish", "board_monitor.py:_derived",
                     "os.py:walk", "monitor.py:recompute", "scanner.py:sessions", "gitinfo.py:describe",
                     "store.py:sync", "store.py:snapshot", "cardflow_monitor.py:commit")
OFF = ("--no-notifications", "--no-tab-marks", "--no-youtrack", "--no-idle", "--no-limits", "--no-models",
       "--no-docker", "--no-box", "--no-report", "--no-activity-look", "--no-monitors", "--no-stream-shortcuts")
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={tmp}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def nearest_rank(samples, q=0.95):
    """Percentyl nearest-rank: k = ⌈q·n⌉ (1-indeksowane) na posortowanych próbkach."""
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def loadavg():
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def git_sha(src):
    try:
        return subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# --- środowisko -----------------------------------------------------------------------------------------------------

def isolated_env(base):
    """Zmienne usługi i budowania danych: wyłącznie katalogi w `base` (bez XDG_*, TIDE_* i DBUS_* z zewnątrz)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("XDG_", "TIDE_", "DBUS_", "GIT_"))}
    paths = {"HOME": "home", "XDG_STATE_HOME": "state", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
             "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run"}
    for key, rel in paths.items():
        env[key] = os.path.join(base, rel)
        os.makedirs(env[key], mode=0o700, exist_ok=True)
    gitconfig = os.path.join(base, "gitconfig")
    with open(gitconfig, "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Perf\n\temail = perf@example.invalid\n[init]\n\tdefaultBranch = master\n")
    incus = os.path.join(base, "bin", "incus")
    os.makedirs(os.path.dirname(incus), exist_ok=True)
    with open(incus, "w", encoding="utf-8") as fh:
        fh.write(f'#!/bin/sh\nexec /usr/bin/python3 -s "{FAKE_INCUS}" "$@"\n')
    os.chmod(incus, 0o755)
    env.update(QT_QPA_PLATFORM="offscreen", GIT_CONFIG_GLOBAL=gitconfig, GIT_CONFIG_NOSYSTEM="1",
               TIDE_BOARD_DIR=os.path.join(base, "board"), TIDE_INCUS_BIN=incus,
               TIDE_FAKE_INCUS_STATE=os.path.join(base, "incus-state"), TIDE_FAKE_LOG=os.path.join(base, "incus.log"),
               TIDE_CODEX_DESKTOP="", TIDE_CLAUDE_BIN="", TIDE_ACTIVITY_LOOK_ENGINE="", TIDE_KAMP="",
               TIDE_ACTIVITY_LOOKS=os.path.join(base, "no-activity-looks"),
               TIDE_MONITORS_BIN=os.path.join(base, "no-monitors-bin"),
               CODEX_HOME=os.path.join(base, "codex"),
               WAYLAND_DISPLAY=os.path.join(base, "no-wayland"),
               LIMITY_CONFIG=os.path.join(base, "no-limity-config.json"),
               DOCKER_HOST="unix://" + os.path.join(base, "no-docker.sock"),
               TIDE_MODELS_CONFIG=os.path.join(HERE, "fixtures", "models-closed.json"))
    os.makedirs(env["TIDE_FAKE_INCUS_STATE"], exist_ok=True)
    return env


def write_settings(env, box):
    """`$XDG_CONFIG_HOME/tide/cardflow.json` usługi. Zapis dopiero po zbudowaniu danych: `box.enabled` w pliku włącza
    granicę Gita (bwrap) także w tym procesie, a dane budujemy zwykłym gitem (te same pliki w obu wariantach)."""
    settings = os.path.join(env["XDG_CONFIG_HOME"], "tide", "cardflow.json")
    os.makedirs(os.path.dirname(settings), exist_ok=True)
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"autoConsent": False, "box": {"enabled": bool(box)}}, fh)
    return settings


@contextlib.contextmanager
def applied(env):
    """Budowanie danych w tym procesie z tymi samymi zmiennymi co usługa (np. kopia dziennika w XDG_STATE_HOME)."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --- dane syntetyczne -----------------------------------------------------------------------------------------------

def build(base, params):
    """Repozytoria i worktree, Tablica z dziennikami i krokami, sesje agentów. Zwraca opis danych (`scale`)."""
    ws = os.path.join(base, "ws")
    repos = {}
    for name in PROJECTS:
        repo = os.path.join(ws, name)
        os.makedirs(os.path.join(repo, "src"))
        _write(os.path.join(repo, "README.md"), f"# {name}\n")
        for index in range(20):
            _write(os.path.join(repo, "src", f"module_{index}.py"), f"VALUE = {index}\n")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        repos[name] = repo
    worktrees = []
    for index in range(params["worktrees"]):
        name = PROJECTS[index % len(PROJECTS)]
        path = os.path.join(ws, f"{name}-worktrees", f"wt-{index:03d}")
        _git(repos[name], "worktree", "add", "-q", "-b", f"ticket/pt-{1000 + index}-perf", path)
        worktrees.append(path)

    from tide_daemon.board.store import BoardStore
    from tide_daemon.cardflow.journal import Journal
    root = os.environ["TIDE_BOARD_DIR"]
    store = BoardStore(root)
    for name in PROJECTS:
        reply = store.upsert_project({"repo": repos[name], "aiDocs": "tide"})
        if not reply.get("ok", True) and reply.get("error"):
            raise RuntimeError(f"projekt {name}: {reply}")
    journal = Journal(root)
    rows = [{"workId": f"#{i}", "title": f"Ticket {i}", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
             "class": "standard", "note": ""} for i in range(1, 6)]
    cards = []
    for index in range(params["cards"]):
        slug = PROJECTS[index % len(PROJECTS)]
        reply = store.create_card({"title": f"Karta {index + 1}", "project": slug, "column": "todo",
                                   "description": "Syntetyczna karta pomiaru skali (PT-362)."})
        card = reply["id"]
        journal.append(card, "card.started", entry="plan", executor="Sonnet", program="claude", provider="anthropic")
        journal.append(card, "plan.proposed", title=f"Plan {card}", prefix="PT", rows=rows, planner="Opus", box=None)
        journal.append(card, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
        for step in range(params["steps"]):
            folder = os.path.join(root, "flow", card, "steps", f"s{step + 1:02d}-ticket-pt-{index * 100 + step}")
            for sub, name in STEP_FILES:
                _write(os.path.join(folder, sub, name), f"{card} krok {step + 1}: {name}\n" * 4)
        num = card.split("-")[1]
        docs = os.path.join(root, "docs", slug)
        _write(os.path.join(docs, "plans", f"2026-11-01-k-{num}-flow.md"), _plan(card, index))
        _write(os.path.join(docs, "specs", f"k-{num}-perf.md"), f"---\ncard: {card}\nstatus: gotowa\n---\n# Spec\n")
        for ticket in range(5):
            _write(os.path.join(docs, "tickets", f"k-{num}-flow", f"PT-{index * 10 + ticket + 1}.md"),
                   f"# PT-{index * 10 + ticket + 1}\n\n- [ ] kryterium\n")
        cards.append(card)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "perf: synthetic board")

    claude = os.path.join(base, "claude")
    os.makedirs(os.path.join(claude, "sessions"))
    os.makedirs(os.path.join(base, "codex"), exist_ok=True)
    from tide_daemon.agents.claude import escape_cwd
    now = time.time()
    sessions = []
    for index in range(params["sessions"]):
        cwd = worktrees[index % len(worktrees)] if worktrees else ws
        sid = f"perf-{index:04d}-0000-4000-8000-000000000000"
        path = os.path.join(claude, "projects", escape_cwd(cwd), f"{sid}.jsonl")
        lines = []
        for turn in range(6):
            ts = now - 3600 + turn * 60 + index
            lines.append(_turn(sid, cwd, ts, f"Krok {turn}", "end_turn" if turn == 5 else "tool_use"))
        _write(path, "".join(lines))
        sessions.append({"path": path, "id": sid, "cwd": cwd})
    return {"root": root, "cards": cards, "sessions": sessions, "worktrees": worktrees, "repos": repos,
            "scale": scale(root, len(cards), sessions, worktrees)}


def _plan(card, index):
    rows = "\n".join(f"| PT-{index * 10 + t + 1} | Ticket {t + 1} | todo | | | |" for t in range(5))
    return (f"---\ntide-plan: 1\ntitle: Plan {card}\ntracker: local\ntracker-url: \ntracker-project: \n"
            f"ticket-prefix: PT\ncoordinator: claude\nsession: \nstatus: active\ncard: {card}\n"
            f"updated: 2026-11-01T10:00:00+01:00\n---\n\n| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n"
            f"| --- | --- | --- | --- | --- | --- |\n{rows}\n")


def _turn(sid, cwd, ts, text, stop):
    user = {"type": "user", "isSidechain": False, "timestamp": _iso(ts), "cwd": cwd, "gitBranch": "",
            "sessionId": sid, "message": {"role": "user", "content": text}}
    reply = {"type": "assistant", "isSidechain": False, "timestamp": _iso(ts + 1), "cwd": cwd, "gitBranch": "",
             "sessionId": sid, "message": {"role": "assistant", "stop_reason": stop,
                                           "content": [{"type": "text", "text": f"{text}: gotowe."}]}}
    return json.dumps(user, ensure_ascii=False) + "\n" + json.dumps(reply, ensure_ascii=False) + "\n"


def scale(root, cards, sessions, worktrees):
    """Skala zmierzona na dysku (bez `.git`): katalogi i pliki całej Tablicy oraz samego `flow/*/steps/**`."""
    dirs = files = step_dirs = step_files = 0
    steps_root = os.path.join(root, "flow")
    for folder, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d != ".git"]
        dirs += 1
        files += len(names)
        rel = os.path.relpath(folder, steps_root).split(os.sep)
        if not rel[0].startswith("..") and len(rel) >= 2 and rel[1] == "steps":
            step_dirs += 1
            step_files += len(names)
    return {"cards": cards, "dirs": dirs, "files": files, "stepDirs": step_dirs, "stepFiles": step_files,
            "sessions": len(sessions), "worktrees": len(worktrees)}


def below_production(found):
    """Pola skali poniżej minimum z opisu karty (pusta lista — skala produkcji)."""
    return [f"{key} {found.get(key)} < {minimum}" for key, minimum in PRODUCTION.items()
            if (found.get(key) or 0) < minimum]


# --- zmiany w tle ---------------------------------------------------------------------------------------------------

class Background:
    """Wątki zmian w tle: dziennik karty i plik kroku, sesje agentów, plan w docs/. Liczniki w `counts`."""

    def __init__(self, data, mirror, params):
        self.data, self.mirror, self.params = data, mirror, params
        self.stop_event = threading.Event()
        self.counts = {"journal": 0, "stepFile": 0, "session": 0, "docs": 0, "errors": 0}
        self.threads = []

    def start(self):
        for target, every in ((self._journal, self.params["journal_every"]),
                              (self._session, self.params["session_every"]),
                              (self._docs, self.params["docs_every"])):
            if every and every > 0:
                thread = threading.Thread(target=self._loop, args=(target, every), daemon=True)
                thread.start()
                self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(10)

    def _loop(self, target, every):
        index = 0
        while not self.stop_event.wait(every):
            try:
                target(index)
            except OSError:
                self.counts["errors"] += 1
            index += 1

    def _journal(self, index):
        cards = self.data["cards"]
        card = cards[index % len(cards)]
        root = self.data["root"]
        with open(os.path.join(self.mirror, card + ".jsonl"), encoding="utf-8") as fh:
            text = fh.read()
        target = os.path.join(root, "flow", card, "journal.jsonl")
        tmp = os.path.join(os.path.dirname(target), ".tide-tmp-journal.jsonl")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
        self.counts["journal"] += 1
        steps = os.path.join(root, "flow", card, "steps")
        step = sorted(os.listdir(steps))[index % self.params["steps"]]
        with open(os.path.join(steps, step, "out", "progress.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{_iso(time.time())} postęp {index}\n")
        self.counts["stepFile"] += 1

    def _session(self, index):
        active = self.data["sessions"][:max(1, self.params["active"])]
        session = active[index % len(active)]
        with open(session["path"], "a", encoding="utf-8") as fh:
            fh.write(_turn(session["id"], session["cwd"], time.time(), f"Tura {index}", "tool_use"))
        self.counts["session"] += 1

    def _docs(self, index):
        plans = sorted(glob.glob(os.path.join(self.data["root"], "docs", "*", "plans", "*.md")))
        path = plans[index % len(plans)]
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        lines = [f"updated: {stamp}" if line.startswith("updated:") else line for line in text.split("\n")]
        tmp = os.path.join(os.path.dirname(path), ".tide-tmp-plan.md")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        os.replace(tmp, path)
        self.counts["docs"] += 1


# --- usługa i pomiar ------------------------------------------------------------------------------------------------

def cpu_ticks(pid):
    """(utime+stime, cutime+cstime) procesu w taktach zegara albo None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return int(fields[11]) + int(fields[12]), int(fields[13]) + int(fields[14])


def parse_profile(text):
    """Raport próbnika (sampler.write_report) → {samples, total: {funkcja: próbki}, own: {…}, stacks: [[n, wątek, stos]]}."""
    out = {"samples": 0, "total": {}, "own": {}, "stacks": []}
    section = None
    for line in text.splitlines():
        if line.startswith("Próbki stosów:"):
            try:
                out["samples"] = int(line.split(":", 1)[1].split()[0])
            except (IndexError, ValueError):
                pass
        elif line.startswith("## "):
            section = ("own" if "własny" in line else "total" if "łączny" in line else
                       "stacks" if "stosy" in line else None)
        elif section and line.strip():
            count, _, rest = line.strip().partition("  ")
            try:
                count = int(count)
            except ValueError:
                continue
            if section == "stacks":
                thread, _, stack = rest.strip().partition("] ")
                out["stacks"].append([count, thread.lstrip("["), stack])
            else:
                out[section][rest.strip()] = count
    return out


def shares(profile):
    """Udział funkcji w próbkach (liczba próbek ze stosem zawierającym funkcję / liczba przebiegów próbnika)."""
    samples = profile.get("samples") or 0
    total = profile.get("total") or {}
    if not samples:
        return {}
    return {name: round(total.get(name, 0) / samples, 4) for name in WATCHED_FUNCTIONS}


class Daemon:
    def __init__(self, base, env, src, data):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtDBus import QDBusConnection
        QCoreApplication.instance() or QCoreApplication([])
        self.base, self.env = base, dict(env)
        with open(os.path.join(base, "bus.conf"), "w", encoding="utf-8") as fh:
            fh.write(BUS_CONFIG.format(tmp=base))
        self.bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={os.path.join(base, 'bus.conf')}",
                                          "--nofork", "--nopidfile", "--print-address=1"],
                                         stdout=subprocess.PIPE, text=True)
        address = self.bus_proc.stdout.readline().strip()
        if not address:
            raise RuntimeError("prywatny dbus-daemon nie wystartował")
        self.env["DBUS_SESSION_BUS_ADDRESS"] = address
        self.bus = QDBusConnection.connectToBus(address, "tide-perf-scale")
        self.log = open(os.path.join(base, "daemon.log"), "w", encoding="utf-8")
        argv = [sys.executable, os.path.join(src, "daemon", "tide-daemon.py"), "--claude-dir",
                os.path.join(base, "claude"), "--codex-dir", os.path.join(base, "codex"), "--workspace",
                os.path.join(base, "ws"), "--board-dir", data["root"], *OFF]
        started = time.perf_counter()
        self.proc = subprocess.Popen(argv, env=self.env, stdout=self.log, stderr=self.log)
        deadline = time.time() + 120
        while time.time() < deadline and self.proc.poll() is None:
            if self.bus.interface().isServiceRegistered(SERVICE).value():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("usługa nie zarejestrowała się na magistrali")
        self.register_ms = (time.perf_counter() - started) * 1000.0

    def call(self, member, timeout_ms=60_000):
        from PyQt6.QtDBus import QDBus, QDBusMessage
        msg = QDBusMessage.createMethodCall(SERVICE, BOARD_PATH, BOARD_IFACE, member)
        begin = time.perf_counter()
        reply = self.bus.call(msg, QDBus.CallMode.Block, timeout_ms)
        ms = (time.perf_counter() - begin) * 1000.0
        ok = reply.type() == QDBusMessage.MessageType.ReplyMessage
        value = reply.arguments()[0] if ok and reply.arguments() else ""
        return ms, ok, value if ok else reply.errorMessage()

    def wait_ready(self, timeout=300.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, ok, value = self.call("Snapshot")
            if ok:
                with contextlib.suppress(ValueError):
                    if json.loads(value).get("readState") == "ready":
                        return True
            time.sleep(0.5)
        return False

    def profile(self, seconds):
        """Żądanie próbnika: (czas startu próbkowania, funkcja() → ścieżka nowego raportu albo None)."""
        request = os.path.join(self.env["XDG_RUNTIME_DIR"], "tide-daemon-profile")
        folder = os.path.join(self.env["XDG_STATE_HOME"], "tide")
        before = set(glob.glob(os.path.join(folder, "profile-*.txt")))
        with open(request, "w", encoding="utf-8") as fh:
            fh.write(f"{seconds:g}\n")
        deadline = time.time() + 15
        while os.path.exists(request) and time.time() < deadline:
            time.sleep(0.05)
        consumed = not os.path.exists(request)

        def report(wait=seconds + 60.0):
            end = time.time() + wait
            while time.time() < end:
                fresh = sorted(set(glob.glob(os.path.join(folder, "profile-*.txt"))) - before)
                if fresh:
                    time.sleep(0.5)       # raport zapisany w całości (jeden write po zebraniu próbek)
                    return fresh[-1]
                time.sleep(0.5)
            return None
        return consumed, report

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        from PyQt6.QtDBus import QDBusConnection
        QDBusConnection.disconnectFromBus("tide-perf-scale")
        if self.bus_proc.poll() is None:
            self.bus_proc.terminate()
            try:
                self.bus_proc.wait(5)
            except subprocess.TimeoutExpired:
                self.bus_proc.kill()
        if self.bus_proc.stdout:
            self.bus_proc.stdout.close()
        self.log.close()


def phase(daemon, name, seconds, period=None):
    """Jedna faza: próbnik i CPU przez `seconds`; z `period` — wywołania METHODS co `period` s (stały rytm)."""
    out = {"phase": name, "seconds": seconds}
    clk = os.sysconf("SC_CLK_TCK")
    consumed, report = daemon.profile(seconds)
    out["samplerStarted"] = consumed
    cpu0, t0 = cpu_ticks(daemon.proc.pid), time.monotonic()
    calls = {m: [] for m in METHODS} if period else {}
    errors = []
    if period:
        tick = 0
        while True:
            due = t0 + tick * period
            if due >= t0 + seconds:
                break
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            for method in METHODS:
                ms, ok, error = daemon.call(method)
                calls[method].append(ms)
                if not ok:
                    errors.append({"tick": tick, "method": method, "error": error})
            tick += 1
    else:
        time.sleep(seconds)
    cpu1, t1 = cpu_ticks(daemon.proc.pid), time.monotonic()
    wall = t1 - t0
    if cpu0 and cpu1:
        out["cpu"] = {"wallS": round(wall, 3), "processS": round((cpu1[0] - cpu0[0]) / clk, 3),
                      "childrenS": round((cpu1[1] - cpu0[1]) / clk, 3),
                      "percent": round(100.0 * (cpu1[0] - cpu0[0]) / clk / wall, 1),
                      "percentWithChildren": round(100.0 * ((cpu1[0] - cpu0[0]) + (cpu1[1] - cpu0[1])) / clk / wall, 1)}
    if period:
        out["methods"] = {m: {"samplesMs": v, "n": len(v), "p95Ms": nearest_rank(v), "maxMs": max(v) if v else None}
                          for m, v in calls.items()}
        out["errors"] = errors
    path = report()
    if path:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        profile = parse_profile(text)
        out["profile"] = {"samples": profile["samples"], "shares": shares(profile),
                          "total": profile["total"], "own": profile["own"], "stacks": profile["stacks"], "text": text}
    else:
        out["profile"] = None
    return out


def measure(params, src=DEFAULT_SRC, sha="", keep=False, base=None):
    own = base is None
    base = os.path.realpath(base or tempfile.mkdtemp(prefix="tide-perf-scale-"))
    result = {"tool": "daemon/tests/perf_scale_k98.py", "sha": sha or git_sha(src), "src": src,
              "params": {**{k: v for k, v in params.items() if k not in ("journal_every", "session_every",
                                                                          "docs_every", "active")},
                         "background": {"journalEveryS": params["journal_every"],
                                        "sessionEveryS": params["session_every"],
                                        "activeSessions": params["active"], "docsEveryS": params["docs_every"]}},
              "cpuCount": os.cpu_count(), "loadavgBefore": loadavg(), "isolatedBase": base}
    daemon = background = None
    try:
        env = isolated_env(base)
        with applied(env):
            started = time.perf_counter()
            data = build(base, params)
            result["setupS"] = round(time.perf_counter() - started, 1)
            from tide_daemon.cardflow.journal import mirror_root
            mirror = mirror_root(data["root"])
        result["scale"] = data["scale"]
        result["belowProduction"] = below_production(data["scale"])
        if params.get("build_only"):
            return result
        write_settings(env, params["box"] == "on")
        daemon = Daemon(base, env, src, data)
        result["registerMs"] = daemon.register_ms
        result["ready"] = daemon.wait_ready()
        background = Background(data, mirror, params)
        background.start()
        time.sleep(params["warmup"])
        result["phases"] = [phase(daemon, "load", params["duration"], params["period"])]
        if params["idle"] > 0:
            result["phases"].append(phase(daemon, "idle", params["idle"]))
        result["background"] = dict(background.counts)
        result["daemonAlive"] = daemon.proc.poll() is None
    finally:
        if background is not None:
            background.stop()
        if daemon is not None:
            daemon.close()
            with contextlib.suppress(OSError):
                with open(os.path.join(base, "daemon.log"), encoding="utf-8", errors="replace") as fh:
                    result["daemonLogTail"] = fh.read().splitlines()[-40:]
        result["loadavgAfter"] = loadavg()
        if own and not keep:
            shutil.rmtree(base, ignore_errors=True)
    return result


def summary(result):
    lines = [f"SHA {result['sha'] or '?'}; box {result['params']['box']}; loadavg przed: {result['loadavgBefore']}; "
             f"po: {result['loadavgAfter']}; rdzenie: {result['cpuCount']}",
             f"skala: {result.get('scale')}; poniżej produkcji: {result.get('belowProduction') or 'nic'}"]
    for item in result.get("phases") or []:
        cpu = item.get("cpu") or {}
        lines.append(f"[{item['phase']}] CPU usługi {cpu.get('percent')} % (z potomnymi {cpu.get('percentWithChildren')} %)")
        for method, data in (item.get("methods") or {}).items():
            lines.append(f"[{item['phase']}] {method}: n={data['n']} p95 {data['p95Ms']:.1f} ms, max {data['maxMs']:.1f} ms")
        profile = item.get("profile") or {}
        lines.append(f"[{item['phase']}] udziały (próbki {profile.get('samples')}): "
                     + ", ".join(f"{k.split(':')[1]} {v:.1%}" for k, v in (profile.get("shares") or {}).items()))
        if item.get("errors"):
            lines.append(f"[{item['phase']}] błędy wywołań: {len(item['errors'])}")
    lines.append(f"tło: {result.get('background')}")
    return "\n".join(lines)


def defaults():
    return {"cards": 98, "steps": 14, "sessions": 200, "worktrees": 200, "duration": 60.0, "period": 0.5,
            "idle": 60.0, "warmup": 20.0, "box": "on", "journal_every": 3.0, "session_every": 0.25, "active": 20,
            "docs_every": 10.0}


def main(argv=None):
    d = defaults()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="plik JSON z wynikiem (surowe próbki)")
    parser.add_argument("--box", choices=("on", "off"), default=d["box"], help="box.enabled w cardflow.json usługi")
    parser.add_argument("--src", default=DEFAULT_SRC, help="katalog z daemon/ (np. wypakowana baza do porównania)")
    parser.add_argument("--sha", default="", help="SHA kodu z --src, gdy to nie jest repozytorium git")
    for key in ("cards", "steps", "sessions", "worktrees", "active"):
        parser.add_argument(f"--{key}", type=int, default=d[key])
    for key in ("duration", "period", "idle", "warmup"):
        parser.add_argument(f"--{key}", type=float, default=d[key])
    parser.add_argument("--journal-every", type=float, default=d["journal_every"])
    parser.add_argument("--session-every", type=float, default=d["session_every"])
    parser.add_argument("--docs-every", type=float, default=d["docs_every"])
    parser.add_argument("--keep", action="store_true", help="zostaw izolowany katalog danych")
    args = parser.parse_args(argv)
    src = os.path.realpath(args.src)
    sys.path.insert(0, os.path.join(src, "daemon"))
    params = {key: getattr(args, key) for key in d}
    result = measure(params, src=src, sha=args.sha, keep=args.keep)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(summary(result), flush=True)
    ok = result.get("daemonAlive") and result.get("ready") and not result.get("belowProduction")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
