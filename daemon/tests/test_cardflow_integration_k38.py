"""PT-294 (K-38 F6): bramka wykonawcza partii 1 — wyłączone pudełko w pełnym przepływie prawdziwej usługi.

Każdy test uruchamia własny proces usługi (`tide-daemon.py`) na własnym, prywatnym `dbus-daemon` (bez katalogów
usług, więc bez aktywacji D-Bus), z własnymi HOME, XDG_CONFIG_HOME, XDG_STATE_HOME, XDG_DATA_HOME, XDG_CACHE_HOME,
XDG_RUNTIME_DIR, TMPDIR, TIDE_BOARD_DIR i katalogiem Tablicy, syntetycznymi repozytoriami z własną konfiguracją
Gita (GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM) oraz atrapami `claude`/`codex`/`systemd-run`/`systemctl`
(tests/flowfakes.py) i `incus` (zapisuje każde wywołanie). Środowisko usługi jest budowane od zera — nie dziedziczy
zmiennych użytkownika. `box.enabled: false` ustawia plik `cardflow.json` zapisany przed startem usługi, więc ten
sam scenariusz działa na wersji sprzed F4 (bez klucza `box` w SetCardFlowSettings).

Źródło usługi wybiera jawnie `TIDE_F6_SERVICE_SRC` (katalog z `tide-daemon.py` i pakietem `tide_daemon`); domyślnie
katalog `daemon/` tego checkoutu. Usługa startuje przez mały program startowy (`LAUNCHER`) w trybie `python3 -I`:
na `sys.path` jest tylko wskazane źródło, a program co pół sekundy zapisuje pochodzenie wczytanych modułów
`tide_daemon.*`. Test odrzuca moduł spoza źródła. Proces testu nie importuje `tide_daemon`.

Przebieg patrzy wyłącznie na granicę usługi: odpowiedzi metod /Board, dziennik karty (kopia usługi), Snapshot(),
pliki wyniku atrap (`fake-call.json`, `result.json`), wyjście kroków w Tablicy, dziennik `incus` i katalogi stanu
pudełka/ekspozycji. Problemy środowiska (brak `dbus-daemon`/QtDBus/gita, brak źródła, zły import, usługa się nie
rejestruje, brak odpowiedzi albo błąd D-Bus, w tym nieznana metoda) są błędem przebiegu (`RuntimeError`, wynik
ERROR), nigdy porażką asercji ani pominięciem — nie mogą udawać czerwonego dowodu.

Dowody: `F6-EVIDENCE {json}` na stderr — gotowość usługi z pochodzeniem importów, skuteczne żądania D-Bus,
start kroku hosta (HostRunner = brak pola `runner`) albo konkretna blokada (`step.waiting`, `box.wait`, notka karty).

Orkiestrator czerwony/zielony (`python3 -m daemon.tests.test_cardflow_integration_k38 evidence --out <katalog>`):
eksport drzew `RED_SHA` i kandydata przez `git archive` do katalogu tymczasowego (bez checkoutu, resetu ani zmiany
refów), osobny pakiet harnessu z obiektów kandydata (te same bajty dla obu przebiegów, sha256 w podsumowaniu),
przebiegi `RED_TESTS` na obu drzewach i pełny moduł na kandydacie; klasyfikacja czerwonego wymaga FAIL każdego
scenariusza z gotowością usługi, skutecznym żądaniem i śladem blokady. Opis: docs/box-cli-v1.md nie dotyczy;
notka ticketu PT-294 zawiera polecenia i wyniki.
"""
import hashlib
import io
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid

from . import flowfakes

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
except ImportError:  # pragma: no cover - preflight zgłasza brak jako błąd przebiegu
    QDBusConnection = None

HARNESS_VERSION = "1"
RED_SHA = "b6e18b6549ea3b30e798b3dcb6e8266cfdc45e4e"   # K-38 sprzed F1/F2 (boxstep.place: environment i PROJECT przed enabled)
F6_BASE = "72ecaf8ed878f8058f5bdb6aafc225fbea4192c1"   # początek ticketu F6 po scaleniu #1–#5 (baza test-changed)
SOURCE_ENV = "TIDE_F6_SERVICE_SRC"
SERVICE = "org.tide.Daemon"
BOARD_PATH, BOARD_IFACE = "/Board", "org.tide.Daemon.Board"
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DEFAULT_SOURCE)
HARNESS_FILES = ("daemon/__init__.py", "daemon/tests/__init__.py", "daemon/tests/flowfakes.py",
                 "daemon/tests/test_cardflow_integration_k38.py", "tools/test_isolation.py", "tools/test_guard.py")
MODULE = "daemon.tests.test_cardflow_integration_k38"
RED_TESTS = (
    f"{MODULE}.NowLifecycleTest.test_new_ticket_review_fix_review_close_all_on_host",
    f"{MODULE}.PlanApprovalTest.test_plasma_tide",
    f"{MODULE}.PlanApprovalTest.test_project_with_profile",
    f"{MODULE}.PlanApprovalTest.test_project_without_profile",
)

PLASMA, PROFILED, BARE = "plasma-tide", "inny", "goly"   # ekspozycja v1 / projekt z profilem / bez profilu
CLAUDE_MD = "# Projekt\n\n## Plan pracy agentów (Tide)\n\n- ticket-prefix: `PT`, tracker: local\n"
PROFILE_YAML = "tide-box: 1\nproject: {slug}\nnetwork:\n  domains:\n    - api.example.org\n"
BOX_YAML = "tide-box: 1\nnetwork:\n  domains:\n    - api.example.org\n"
# Kartka hosta (tekst master, PT-291) i dodatki wyłącznie pudełka (owijka, testy w pudełku, brak outside).
HOST_MARKERS = ("Przed poleceniem spoza listy dozwolonych zapisz wynik `denied` z pełnym poleceniem i zakończ krok.",
                "Jeśli potrzebujesz polecenia, którego piaskownica kroku nie wykona",
                "zgłoś polecenie (`denied` albo `outside`)")
BOX_MARKERS = ("tide-test", "W pudełku wolno", "Testy w pudełku", "Ograniczenie pudełka",
               "dla opakowanych testów")
BOX_FIELDS = ("runner", "box", "boxSlug", "boxIp", "boxAccess", "boxPortOwner")
# Pierwszy format `step.started` (PT-58, c0b2cd16) i pola tożsamości `intent` wymagane przez model PT-288.
PT58_STEP_FIELDS = ("v", "seq", "ts", "card", "type", "step", "kind", "ticket", "executor", "program", "model",
                    "unit", "session", "worktree", "branch", "stepDir", "gitDir", "repo")
# Najstarszy format, który umiał wznowić krok po decyzji (80ae0057, 29.10.2026): bez deliveredNoteIds,
# readDirsProfile, baseVersion/baseRef/baseCommit i pól pudełka. Format PT-58 (c0b2cd16) poprzedza wznowienia
# (brak `provider`), więc takiego kroku z pytaniem nie dało się wznowić także na master.
DECISION_STEP_FIELDS = ("v", "seq", "ts", "card", "type", "step", "kind", "ticket", "round", "executor", "program",
                        "model", "provider", "local", "effort", "routeUrl", "routeKind", "routeTokenFile",
                        "reviewTip", "parentStep", "attempt", "unit", "session", "worktree", "branch", "stepDir",
                        "gitDir", "repo")
PT58_INTENT_FIELDS = ("v", "seq", "ts", "card", "type", "action", "key", "ticket", "kind", "unit", "session",
                      "stepDir", "worktree")
PT58_RESULT_FIELDS = ("v", "seq", "ts", "card", "type", "action", "key", "ok", "unit", "gitDir")

LAUNCHER = r'''import json, os, runpy, sys, threading, time
src = os.path.realpath(os.environ.pop("TIDE_F6_SERVICE_SRC"))
report = os.environ.pop("TIDE_F6_PROVENANCE")
sys.path.insert(0, src)   # tryb -I: bez katalogu skryptu, PYTHONPATH i site użytkownika
def dump():
    modules = {n: getattr(m, "__file__", None) for n, m in list(sys.modules.items())
               if n == "tide_daemon" or n.startswith("tide_daemon.")}
    data = {"src": src, "executable": sys.executable, "path": sys.path[:3], "modules": modules, "pid": os.getpid()}
    tmp = report + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, report)
def loop():
    while True:
        time.sleep(0.5)
        dump()
import tide_daemon
dump()
threading.Thread(target=loop, daemon=True).start()
script = os.path.join(src, "tide-daemon.py")
sys.argv = [script] + sys.argv[1:]
runpy.run_path(script, run_name="__main__")
'''

INCUS = """#!/bin/sh
echo "incus $*" >> "{log}"
exit 1
"""

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

_APP = None


class Bootstrap(RuntimeError):
    """Niewykonany dowód: środowisko, narzędzie, import, start usługi albo D-Bus — nigdy porażka scenariusza."""


EVIDENCE_ENV = "TIDE_F6_EVIDENCE_FILE"


def evidence(test, kind, /, **data):
    """Rekord dowodu; `kind` jest wyłącznie pozycyjny i wygrywa z polami danych (rodzaj kroku: `stepKind`)."""
    line = json.dumps({**data, "test": test.id().rsplit(".", 2)[-2] + "." + test._testMethodName, "kind": kind,
                       "time": round(time.time(), 3)}, ensure_ascii=False, sort_keys=True)
    print("F6-EVIDENCE " + line, file=sys.stderr, flush=True)
    if os.environ.get(EVIDENCE_ENV):
        with open(os.environ[EVIDENCE_ENV], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def app():
    global _APP
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP


def wait_until(predicate, timeout=10.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def test_root() -> str:
    """Katalog testu poza /tmp/claude-<uid> (strażnik katalogów kroku odrzuca go słusznie)."""
    sandbox = f"/tmp/claude-{os.getuid()}"
    for base in (os.environ.get("TIDE_TEST_TMPDIR"), tempfile.gettempdir(), "/tmp",
                 os.path.join(REPO, "build", "test-tmp")):
        if not base or os.path.realpath(base).startswith(sandbox):
            continue
        try:
            os.makedirs(base, exist_ok=True)
            return tempfile.mkdtemp(prefix="tide-f6-", dir=base)
        except OSError:
            continue
    raise Bootstrap(f"brak zapisywalnego katalogu tymczasowego poza {sandbox} (TIDE_TEST_TMPDIR)")


def kill_fakes(base: str) -> None:
    """Procesy atrap kroku przeżywają usługę (jak jednostka scope) — sprzątamy je po ścieżce katalogu testu."""
    me = os.getpid()
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                line = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if base in line:
            try:
                os.kill(int(name), signal.SIGKILL)
            except OSError:
                pass


class ServiceCase(unittest.TestCase):
    """Usługa na prywatnej magistrali w izolowanych katalogach; sprzątanie przez addCleanup także po porażce."""

    projects = (PLASMA, PROFILED, BARE)
    slots = 12

    def setUp(self):
        self.started_at = time.time()
        self.token = uuid.uuid4().hex[:12]
        self.source = os.path.realpath(os.environ.get(SOURCE_ENV) or DEFAULT_SOURCE)
        self.preflight_tools()
        self.base = test_root()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.addCleanup(kill_fakes, self.base)
        for name in ("ws", "home", "run", "tmp", "config/tide", "state", "data", "cache", "boxboard", "bus"):
            os.makedirs(os.path.join(self.base, name), exist_ok=True)
        os.chmod(os.path.join(self.base, "run"), 0o700)
        self.board = os.path.join(self.base, "ws", "tide-board")
        self.boxboard = os.path.join(self.base, "boxboard")
        self.state = os.path.join(self.base, "state")
        self.fakebin = os.path.join(self.base, "fakebin")
        flowfakes.install(self.fakebin)
        self.incus_log = os.path.join(self.base, "incus.log")
        with open(os.path.join(self.fakebin, "incus"), "w", encoding="utf-8") as fh:
            fh.write(INCUS.format(log=self.incus_log))
        os.chmod(os.path.join(self.fakebin, "incus"), 0o755)
        self.gitconfig = os.path.join(self.base, "gitconfig")
        with open(self.gitconfig, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        self.git_env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(self.base, "home"),
                        "GIT_CONFIG_GLOBAL": self.gitconfig, "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8"}
        self.settings_file = os.path.join(self.base, "config", "tide", "cardflow.json")
        # Wyłączone pudełko z izolowanej konfiguracji przed startem usługi — publiczny interfejs obu wersji.
        with open(self.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15, "autoConsent": False, "escalateErrors": False,
                       "reviewRoundsBeforeQuestion": 3, "providerSlots": {"anthropic": self.slots},
                       "box": {"enabled": False}}, fh)
        with open(os.path.join(self.base, "models-closed.json"), "w", encoding="utf-8") as fh:
            json.dump({"endpoints": {"local": {"url": "http://127.0.0.1:9", "kind": "ollama"}}}, fh)
        with open(os.path.join(self.base, "youtrack-closed.json"), "w", encoding="utf-8") as fh:
            json.dump({"url": "http://127.0.0.1:9", "tokenFile": "", "tokenVar": "", "importCheckMinutes": 0}, fh)
        with open(os.path.join(self.base, "launcher.py"), "w", encoding="utf-8") as fh:
            fh.write(LAUNCHER)
        self.repos = {slug: self.make_repo(slug) for slug in self.projects}
        if PROFILED in self.projects:
            path = os.path.join(self.boxboard, "projects", PROFILED, "box.yaml")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(PROFILE_YAML.format(slug=PROFILED))
        self.start_bus()
        self.daemon = self.spawn()
        for slug, repo in self.repos.items():
            self.must("UpsertProject", json.dumps({"repo": repo, "slug": slug, "name": slug}))

    # --- preflight i usługa ----------------------------------------------------------------------------

    def preflight_tools(self):
        missing = [name for name in ("dbus-daemon", "git") if not shutil.which(name)]
        if QDBusConnection is None:
            missing.append("PyQt6.QtDBus")
        if not os.path.isfile("/usr/bin/python3"):
            missing.append("/usr/bin/python3")
        if missing:
            raise Bootstrap("PREFLIGHT: brak narzędzi " + ", ".join(missing))
        for rel in ("tide-daemon.py", os.path.join("tide_daemon", "__init__.py"),
                    os.path.join("tide_daemon", "cardflow", "boxstep.py")):
            if not os.path.isfile(os.path.join(self.source, rel)):
                raise Bootstrap(f"PREFLIGHT: źródło usługi {self.source} nie ma {rel}")
        if "tide_daemon" in sys.modules:
            raise Bootstrap("PREFLIGHT: proces testu zaimportował tide_daemon — granica usługi nieczytelna")

    def start_bus(self):
        app()
        config = os.path.join(self.base, "bus", "bus.conf")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(BUS_CONFIG.format(tmp=os.path.join(self.base, "bus")))
        self.bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={config}", "--nofork", "--nopidfile",
                                          "--print-address=1"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_proc, self.bus_proc)
        self.address = self.bus_proc.stdout.readline().strip()
        self.bus_name = f"tide-f6-{self.token}"
        if not self.address:
            raise Bootstrap("PREFLIGHT: prywatny dbus-daemon nie wystartował")
        self.bus = QDBusConnection.connectToBus(self.address, self.bus_name)
        self.addCleanup(QDBusConnection.disconnectFromBus, self.bus_name)
        if not self.bus.isConnected():
            raise Bootstrap("PREFLIGHT: brak połączenia z prywatną magistralą")

    def service_env(self) -> dict:
        user = pwd.getpwuid(os.getuid()).pw_name
        base = self.base
        return {
            "PATH": self.fakebin + os.pathsep + "/usr/bin:/bin", "USER": user, "LOGNAME": user, "LANG": "C.UTF-8",
            "HOME": os.path.join(base, "home"), "XDG_CONFIG_HOME": os.path.join(base, "config"),
            "XDG_STATE_HOME": self.state, "XDG_DATA_HOME": os.path.join(base, "data"),
            "XDG_CACHE_HOME": os.path.join(base, "cache"), "XDG_RUNTIME_DIR": os.path.join(base, "run"),
            "TMPDIR": os.path.join(base, "tmp"), "TIDE_BOARD_DIR": self.boxboard,
            "DBUS_SESSION_BUS_ADDRESS": self.address,
            "GIT_CONFIG_GLOBAL": self.gitconfig, "GIT_CONFIG_NOSYSTEM": "1",
            "TIDE_SYSTEMD_RUN": os.path.join(self.fakebin, "systemd-run"), "TIDE_FLOW_POLL_MS": "200",
            "TIDE_FAKE_LOG": os.path.join(base, "calls.log"), "TIDE_INCUS_BIN": os.path.join(self.fakebin, "incus"),
            "WAYLAND_DISPLAY": os.path.join(base, "no-wayland"), "TIDE_CODEX_DESKTOP": "",
            "LIMITY_CONFIG": os.path.join(base, "no-limity.json"), "TIDE_CLAUDE_BIN": "",
            "DOCKER_HOST": "unix://" + os.path.join(base, "no-docker.sock"),
            "TIDE_MODELS_CONFIG": os.path.join(base, "models-closed.json"),
            "TIDE_YOUTRACK_CONFIG": os.path.join(base, "youtrack-closed.json"),
            "CODEX_HOME": os.path.join(base, "codex"), "TIDE_ACTIVITY_LOOK_ENGINE": "",
            "TIDE_ACTIVITY_LOOKS": os.path.join(base, "no-activities"),
            "TIDE_MONITORS_BIN": os.path.join(base, "no-monitors"), "TIDE_KAMP": "",
            SOURCE_ENV: self.source, "TIDE_F6_PROVENANCE": os.path.join(base, "provenance.json"),
        }

    def spawn(self):
        provenance = os.path.join(self.base, "provenance.json")
        if os.path.exists(provenance):
            os.unlink(provenance)
        self.service_log = os.path.join(self.base, f"service-{int(time.time() * 1000)}.log")
        log = open(self.service_log, "w", encoding="utf-8")
        self.addCleanup(log.close)
        argv = ["/usr/bin/python3", "-I", os.path.join(self.base, "launcher.py"),
                "--claude-dir", os.path.join(self.base, "home", ".claude"),
                "--codex-dir", os.path.join(self.base, "codex"), "--no-processes", "--no-notifications",
                "--workspace", os.path.join(self.base, "ws"), "--board-dir", self.board]
        proc = subprocess.Popen(argv, env=self.service_env(), stdout=log, stderr=subprocess.STDOUT,
                                cwd=os.path.join(self.base, "tmp"))
        self.addCleanup(self.stop_proc, proc)
        registered = wait_until(lambda: proc.poll() is not None or
                                self.bus.interface().isServiceRegistered(SERVICE).value(), timeout=30)
        if not registered or proc.poll() is not None:
            raise Bootstrap(f"PREFLIGHT: usługa nie zarejestrowała {SERVICE} na prywatnej magistrali; "
                            f"koniec dziennika usługi:\n{self.log_tail()}")
        data = wait_until(lambda: self.provenance(), timeout=10)
        if not data:
            raise Bootstrap("PREFLIGHT: brak raportu pochodzenia importów usługi")
        self.check_provenance(data)
        evidence(self, "service-ready", pid=proc.pid, bus=self.address, source=self.source,
                 module=data["modules"].get("tide_daemon"), executable=data["executable"],
                 isolated={k: v for k, v in self.service_env().items()
                           if k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                                    "XDG_RUNTIME_DIR", "TIDE_BOARD_DIR")})
        return proc

    def provenance(self):
        try:
            with open(os.path.join(self.base, "provenance.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def check_provenance(self, data=None):
        data = data or self.provenance()
        if not data or os.path.realpath(data["src"]) != self.source:
            raise Bootstrap(f"PREFLIGHT: usługa wczytała inne źródło: {data and data.get('src')}")
        foreign = {name: path for name, path in data["modules"].items()
                   if not path or not os.path.realpath(path).startswith(self.source + os.sep)}
        if foreign or "tide_daemon" not in data["modules"]:
            raise Bootstrap(f"PREFLIGHT: moduły usługi spoza {self.source}: {foreign}")
        return data

    def log_tail(self, lines=40):
        try:
            with open(self.service_log, encoding="utf-8", errors="replace") as fh:
                return "".join(fh.readlines()[-lines:])
        except OSError:
            return "(brak dziennika)"

    @staticmethod
    def stop_proc(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        if proc.stdout:
            proc.stdout.close()

    def restart(self):
        self.check_provenance()
        self.stop_proc(self.daemon)
        if not wait_until(lambda: not self.bus.interface().isServiceRegistered(SERVICE).value(), timeout=10):
            raise Bootstrap("usługa nie zwolniła nazwy na magistrali")
        self.daemon = self.spawn()

    # --- D-Bus -----------------------------------------------------------------------------------------

    def call(self, member, *args, record=True):
        msg = QDBusMessage.createMethodCall(SERVICE, BOARD_PATH, BOARD_IFACE, member)
        if args:
            msg.setArguments(list(args))
        reply = self.bus.call(msg, QDBus.CallMode.Block, 60000)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            raise Bootstrap(f"D-Bus {member}: {reply.errorName()} {reply.errorMessage()}")
        try:
            data = json.loads(reply.arguments()[0])
        except (IndexError, TypeError, ValueError) as exc:
            raise Bootstrap(f"D-Bus {member}: odpowiedź nie jest JSON ({exc})") from None
        if record and isinstance(data, dict):
            evidence(self, "dbus", method=member, ok=data.get("ok", True), error=data.get("error", ""))
        return data

    def must(self, member, *args):
        """Żądanie, które musi zostać przyjęte; odmowa to niewykonany krok scenariusza, nie dowód blokady."""
        data = self.call(member, *args)
        if isinstance(data, dict) and data.get("ok") is False:
            raise Bootstrap(f"D-Bus {member} odrzucone: {data}")
        return data

    def card(self, project, description, title="Eksport"):
        return self.must("CreateCard", json.dumps({"title": f"{title} {self.token}", "project": project,
                                                   "description": description, "column": "todo"}))["id"]

    def shown(self, card_id) -> dict:
        return next(c for c in self.call("Snapshot", record=False)["cards"] if c["id"] == card_id)

    # --- dziennik i artefakty ----------------------------------------------------------------------------

    def mirror(self, card_id) -> str:
        key = hashlib.sha1(os.path.realpath(self.board).encode("utf-8")).hexdigest()[:12]
        return os.path.join(self.state, "tide", "cardflow", key, card_id + ".jsonl")

    def board_journal(self, card_id) -> str:
        return os.path.join(self.board, "flow", card_id, "journal.jsonl")

    def events(self, card_id) -> list:
        for path in (self.mirror(card_id), self.board_journal(card_id)):
            try:
                with open(path, encoding="utf-8") as fh:
                    return [json.loads(line) for line in fh if line.strip()]
            except (OSError, ValueError):
                continue
        return []

    def of(self, card_id, kind) -> list:
        return [e for e in self.events(card_id) if e.get("type") == kind]

    def results(self, card_id, action) -> list:
        return [e for e in self.of(card_id, "result") if e.get("action") == action]

    def steps(self, card_id, kind=None) -> list:
        return [e for e in self.of(card_id, "step.started") if kind is None or e.get("kind") == kind]

    def finished(self, card_id, step) -> list:
        return [e for e in self.of(card_id, "step.finished") if e.get("step") == step]

    def fake_call(self, step) -> dict:
        path = os.path.join(step["stepDir"], "out", "fake-call.json")
        found = wait_until(lambda: os.path.isfile(path), timeout=15)
        self.assertTrue(found, f"atrapa agenta nie dostała zlecenia kroku {step['step']}")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def kept(self, card_id, step) -> set:
        folder = os.path.join(self.board, "flow", card_id, "steps", step)
        return set(os.listdir(folder)) if os.path.isdir(folder) else set()

    def blockade(self, card_id) -> dict:
        try:
            card = self.shown(card_id)
        except (Bootstrap, StopIteration):
            card = {}
        flow = ((card.get("derived") or {}).get("flow") or {})
        return {"stepWaiting": [{"ticket": e.get("ticket"), "kind": e.get("kind"), "reason": e.get("reason")}
                                for e in self.of(card_id, "step.waiting")],
                "boxWait": [{"value": e.get("value"), "source": e.get("source"), "deferred": e.get("deferred")}
                            for e in self.of(card_id, "box.wait")],
                "flowErrors": [e.get("error") for e in self.of(card_id, "flow.error")],
                "note": (card.get("derived") or {}).get("note"), "cardBoxWait": flow.get("boxWait"),
                "column": card.get("boardColumn"), "started": [s.get("kind") for s in self.steps(card_id)]}

    def wait_host_start(self, card_id, kind="ticket", ticket=None, timeout=45.0, parent=None) -> dict:
        """Start kroku na hoście (HostRunner: bez pól pudełka) z dostarczonym zleceniem atrapy agenta.
        Brak startu: dowód blokady (step.waiting, box.wait, notka) w evidence i w komunikacie porażki."""
        def found():
            return next((s for s in self.steps(card_id, kind) if (ticket is None or s.get("ticket") == ticket)
                         and (parent is None or s.get("parentStep") == parent)), None)
        step = wait_until(found, timeout=timeout)
        if step is None:
            detail = self.blockade(card_id)
            evidence(self, "blocked", card=card_id, stepKind=kind, ticket=ticket, hostRunnerStart=False, **detail)
            self.fail(f"BLOCKED: brak startu HostRunner ({kind} {ticket or ''}) w {card_id}: "
                      f"{json.dumps(detail, ensure_ascii=False)}")
        box = {k: step[k] for k in BOX_FIELDS if k in step}
        evidence(self, "host-start", card=card_id, stepKind=kind, ticket=step.get("ticket"), step=step["step"],
                 runner=step.get("runner", "host"), box=box)
        self.assertEqual(box, {}, f"krok {step['step']} nie biegnie na hoście")
        self.fake_call(step)
        return step

    def wait_column(self, card_id, column, timeout=60.0) -> dict:
        found = wait_until(lambda: self.shown(card_id)["boardColumn"] == column, timeout=timeout)
        self.assertTrue(found, f"{card_id} nie dotarła do {column}: {json.dumps(self.blockade(card_id), ensure_ascii=False)}")
        return self.shown(card_id)

    def settle(self, seconds=2.0):
        wait_until(lambda: False, timeout=seconds)

    # --- repozytoria i plany ---------------------------------------------------------------------------

    def git(self, cwd, *args) -> str:
        return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True,
                              env=self.git_env).stdout

    def make_repo(self, slug) -> str:
        repo = os.path.join(self.base, "ws", slug)
        os.makedirs(repo)
        self.git(repo, "init", "-q")
        with open(os.path.join(repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(CLAUDE_MD)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "init")
        return repo

    def write_plan(self, slug, card_id, first="ATRAPA: na-sygnal"):
        folder = os.path.join(self.repos[slug], "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, f"2026-11-09-{card_id.lower()}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card_id +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Pierwszy plasterek | todo | | Sonnet | konieczny | standard | Opus | |\n"
                     "| #2 | Drugi plasterek | todo | | Sonnet | po pierwszym | standard | Opus | #1 |\n")
        for number, marker in ((1, first), (2, "ATRAPA: gotowe")):
            with open(os.path.join(folder, "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\n{marker}\n\n## Kryteria akceptacji\n\n- [ ] Wynik {number}.\n")
        return path

    def propose(self, slug, card_id, **plan):
        self.must("StartCard", card_id, json.dumps({"entry": "plan", "planPath": self.write_plan(slug, card_id, **plan)}))
        if not wait_until(lambda: self.of(card_id, "plan.reviewed"), timeout=60):
            raise Bootstrap(f"review planu nie zakończyło się: {json.dumps(self.blockade(card_id), ensure_ascii=False)}")

    def release(self, card_id):
        for step in self.steps(card_id):
            out = os.path.join(step.get("stepDir") or "", "out")
            if os.path.isdir(out):
                open(os.path.join(out, "release-agent"), "w").close()

    # --- izolacja ----------------------------------------------------------------------------------------

    def assert_no_box_effects(self, allowed_box_events=()):
        calls = open(self.incus_log, encoding="utf-8").read() if os.path.exists(self.incus_log) else ""
        self.assertEqual(calls, "", "wywołanie Incusa przy box.enabled=false")
        exposures = os.path.join(self.state, "tide", "project-exposures")
        self.assertFalse(os.path.exists(exposures), "zapis ekspozycji projektu przy box.enabled=false")
        for slug in self.projects:
            path = os.path.join(self.boxboard, "projects", slug, "box", "journal.jsonl")
            types = []
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    types = [json.loads(line).get("type") for line in fh if line.strip()]
            self.assertEqual([t for t in types if t not in allowed_box_events], [],
                             f"zdarzenia pudełka {slug} przy box.enabled=false")

    def assert_isolated(self):
        """Usługa i jej kroki w katalogach testu; brak zapisu tego przebiegu w produkcyjnej Tablicy i kopii."""
        self.check_provenance()
        with open(f"/proc/{self.daemon.pid}/environ", "rb") as fh:
            env = dict(item.split("=", 1) for item in fh.read().decode("utf-8", "replace").split("\0") if "=" in item)
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR", "TMPDIR", "TIDE_BOARD_DIR"):
            self.assertTrue(env.get(key, "").startswith(self.base + os.sep), f"{key} usługi poza testem: {env.get(key)}")
        self.assertEqual(env.get("DBUS_SESSION_BUS_ADDRESS"), self.address, "usługa na innej magistrali")
        self.assertEqual(shutil.which("claude", path=env["PATH"]), os.path.join(self.fakebin, "claude"))
        self.assertEqual(shutil.which("codex", path=env["PATH"]), os.path.join(self.fakebin, "codex"))
        self.assertFalse(os.path.exists(os.path.join(self.base, "calls.log")), "uruchomiona Konsola")
        home = pwd.getpwuid(os.getuid()).pw_dir
        production = os.path.join(home, "DevWorkspace", "tide-board")
        key = hashlib.sha1(os.path.realpath(production).encode("utf-8")).hexdigest()[:12]
        written = []
        for folder in (os.path.join(production, "flow"), os.path.join(home, ".local", "state", "tide", "cardflow", key)):
            try:
                for root, _dirs, files in os.walk(folder, onerror=self._walk_error):
                    for name in files:
                        path = os.path.join(root, name)
                        try:
                            if os.lstat(path).st_mtime < self.started_at - 1 or not name.endswith((".jsonl", ".json", ".md")):
                                continue
                            with open(path, "rb") as fh:
                                if self.token.encode() in fh.read(4 * 1024 * 1024):
                                    written.append(path)
                        except FileNotFoundError:
                            continue
            except PermissionError as exc:
                raise Bootstrap(f"kontrola izolacji niewykonalna: {exc}") from None
        evidence(self, "isolation", productionBoard=production, productionWrites=written,
                 service={k: env.get(k) for k in ("HOME", "XDG_STATE_HOME", "TIDE_BOARD_DIR")})
        self.assertEqual(written, [], "zapis przebiegu testu w produkcyjnej Tablicy albo kopii dziennika")

    @staticmethod
    def _walk_error(exc):
        if isinstance(exc, PermissionError):
            raise exc

    # --- stary format zapisu ---------------------------------------------------------------------------

    def rewrite(self, card_id, change):
        """Przepisuje zdarzenia w kopii usługi i w Tablicy (usługa zatrzymana); seq i kolejność bez zmian."""
        events = self.events(card_id)
        new = [change(dict(e)) for e in events]
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in new)
        for path in (self.mirror(card_id), self.board_journal(card_id)):
            tmp = path + ".f6"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        return new


# === Scenariusze wspólne z czerwonym przebiegiem =========================================================

class NowLifecycleTest(ServiceCase):
    projects = (PLASMA,)

    def test_new_ticket_review_fix_review_close_all_on_host(self):
        card_id = self.card(PLASMA, "Dodaj eksport.\n\nATRAPA: review-requests 1")
        reply = self.must("StartCard", card_id, json.dumps({"entry": "now", "executor": "Sonnet"}))
        ticket = self.wait_host_start(card_id, "ticket")
        self.assertEqual(ticket["ticket"], reply["ticket"])
        self.wait_column(card_id, "review", timeout=120)
        self.assertTrue(wait_until(lambda: all(self.finished(card_id, s["step"]) for s in self.steps(card_id)),
                                   timeout=20))
        steps = self.steps(card_id)
        self.assertEqual([s["kind"] for s in steps], ["ticket", "review", "fix", "review", "close"])
        self.assertEqual([e.get("verdict") for e in self.of(card_id, "review.verdict")],
                         ["REQUEST_CHANGES", "APPROVE"])
        expected = {"ticket": {"result.json", "note.md"}, "fix": {"result.json", "note.md"},
                    "review": {"result.json", "review.md"}, "close": {"result.json", "summary.md"}}
        for step in steps:
            with self.subTest(step=step["step"]):
                self.assertEqual({k: step[k] for k in BOX_FIELDS if k in step}, {}, "runner host")
                done = self.finished(card_id, step["step"])
                self.assertEqual([f["result"] for f in done], ["done"], "wynik result.json odebrany raz")
                self.assertLessEqual(expected[step["kind"]], self.kept(card_id, step["step"]), "komplet artefaktów")
                prompt = self.fake_call(step)["prompt"]
                for marker in HOST_MARKERS:
                    self.assertIn(marker, prompt, f"kartka hosta {step['kind']} bez tekstu master")
                for marker in BOX_MARKERS:
                    self.assertNotIn(marker, prompt, f"kartka hosta {step['kind']} z dodatkiem pudełka")
        self.assertEqual(len(self.results(card_id, "merge")), 1)
        self.assertTrue(all(r.get("ok") for r in self.results(card_id, "merge")))
        self.assertEqual(self.of(card_id, "step.waiting"), [])
        self.assertEqual([e for e in self.of(card_id, "box.wait") if e.get("value")], [])
        files = self.git(self.repos[PLASMA], "ls-tree", "-r", "--name-only", reply["cardBranch"]).split()
        self.assertIn("ATRAPA.md", files)
        self.assert_no_box_effects()
        self.assert_isolated()


class PlanApprovalTest(ServiceCase):
    """Nowy plan (ApproveCardPlan) i zmiana zatwierdzonego planu (ApproveCardEnvironment „Tylko plan”)."""

    def scenario(self, slug):
        card_id = self.card(slug, "Plan z pliku")
        self.addCleanup(self.release, card_id)
        self.propose(slug, card_id)
        approved = self.must("ApproveCardPlan", card_id)
        first = approved["map"]["#1"]
        self.wait_host_start(card_id, "ticket", ticket=first)
        self.assertEqual([e for e in self.of(card_id, "box.wait") if e.get("value")], [],
                         "zatwierdzenie planu utworzyło oczekiwanie przy box.enabled=false")
        # Zmiana już zatwierdzonego planu: nowy ticket bez blokerów rusza na hoście bez czekania na środowisko.
        self.must("EditCardPlan", card_id, json.dumps({"action": "return", "feedback": "Dodaj ticket"}))
        if not wait_until(lambda: len(self.of(card_id, "plan.reviewed")) == 2, timeout=60):
            raise Bootstrap(f"poprawka planu bez review: {json.dumps(self.blockade(card_id), ensure_ascii=False)}")
        revised = self.must("ApproveCardEnvironment", card_id, BOX_YAML, True)
        self.assertEqual(len(self.of(card_id, "plan.revision-approved")), 1)
        added = [r["id"] for r in self.of(card_id, "plan.revision-approved")[0]["plan"]["rows"]
                 if r["workId"] == "#3"]
        self.assertEqual(len(added), 1, "poprawka nie dodała ticketu")
        self.wait_host_start(card_id, "ticket", ticket=added[0])
        self.assertFalse(revised.get("boxWait"), revised)
        self.assertEqual(revised.get("build", ""), "", "„Tylko plan” zleciło budowę")
        self.assertEqual([e for e in self.of(card_id, "box.wait") if e.get("value")], [])
        self.assertEqual([e for e in self.of(card_id, "step.waiting")], [])
        self.assertNotIn(approved["map"]["#2"], [s.get("ticket") for s in self.steps(card_id, "ticket")],
                         "#2 czeka na #1 (bloker), nie na środowisko")
        self.assert_no_box_effects()
        self.assert_isolated()

    def test_plasma_tide(self):
        self.scenario(PLASMA)

    def test_project_with_profile(self):
        self.scenario(PROFILED)

    def test_project_without_profile(self):
        self.scenario(BARE)


# === Scenariusze tylko kandydata (nowe Set/Get i pozostałe AC) =============================================

class RestartHistoricalHostStepTest(ServiceCase):
    projects = (PLASMA,)

    @staticmethod
    def old_format(steps, fields=PT58_STEP_FIELDS):
        """Zdarzenia kroku w dawnym formacie (`fields`); pola runner/box ani późniejsze pola nie istnieją."""
        def change(event):
            if event.get("type") == "step.started" and event.get("step") in steps:
                return {k: event[k] for k in fields if k in event}
            if event.get("action") == "step-start" and event.get("key") in steps:
                fields = PT58_INTENT_FIELDS if event["type"] == "intent" else PT58_RESULT_FIELDS
                return {k: event[k] for k in fields if k in event}
            return event
        return change

    def test_restart_keeps_historical_host_steps_and_takes_each_result_once(self):
        running = self.card(PLASMA, "Dodaj eksport.\n\nATRAPA: powoli 6", title="Trwa")
        asked = self.card(PLASMA, "Dodaj eksport.\n\nATRAPA: pytanie", title="Pyta")
        self.must("StartCard", running, json.dumps({"entry": "now", "executor": "Sonnet"}))
        self.must("StartCard", asked, json.dumps({"entry": "now", "executor": "Sonnet"}))
        alive = self.wait_host_start(running)
        question = self.wait_host_start(asked)
        pid = wait_until(lambda: flowfakes.unit_pid(os.path.join(self.base, "run"), alive["unit"]), timeout=10)
        self.assertTrue(flowfakes.alive(pid))
        self.assertTrue(wait_until(lambda: self.finished(asked, question["step"]), timeout=30))
        self.assertEqual(self.finished(asked, question["step"])[0]["result"], "question")
        self.stop_proc(self.daemon)
        historic = {}
        for card_id, step, fields in ((running, alive, PT58_STEP_FIELDS), (asked, question, DECISION_STEP_FIELDS)):
            new = self.rewrite(card_id, self.old_format({step["step"]}, fields))
            historic[card_id] = next(e for e in new if e.get("type") == "step.started" and e["step"] == step["step"])
            self.assertEqual(set(historic[card_id]) - set(fields), set())
            self.assertFalse({"deliveredNoteIds", "readDirsProfile", "baseVersion", *BOX_FIELDS} & set(historic[card_id]))
        self.assertTrue(flowfakes.alive(pid) or os.path.isfile(os.path.join(alive["stepDir"], "out", "result.json")))
        self.restart()

        # Krok trwający w czasie restartu: ta sama jednostka i sesja, wynik odebrany dokładnie raz.
        self.wait_column(running, "review", timeout=120)
        self.assertEqual([f["result"] for f in self.finished(running, alive["step"])], ["done"])
        kept = next(e for e in self.of(running, "step.started") if e["step"] == alive["step"])
        self.assertEqual(kept, historic[running], "historia kroku zmieniona po restarcie")
        self.assertEqual((kept["unit"], kept["session"]), (alive["unit"], alive["session"]))
        self.assertEqual(len(self.results(running, "merge")), 1)
        self.assertEqual([s["kind"] for s in self.steps(running)], ["ticket", "review", "close"])
        seqs = [e["seq"] for e in self.events(running)]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=10))

        # Wznowienie historycznego kroku z pytaniem: runner host, ta sama sesja agenta, wynik raz.
        self.must("ResolveCardStep", asked, json.dumps({"choice": "answer", "answer": "ISO 8601"}))
        resumed = self.wait_host_start(asked, "ticket", parent=question["step"])
        call = self.fake_call(resumed)
        self.assertEqual(resumed["session"], question["session"])
        self.assertIn("--resume", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--resume") + 1], question["session"])
        self.wait_column(asked, "review", timeout=120)
        self.assertEqual([f["result"] for f in self.finished(asked, resumed["step"])], ["done"])
        self.assertEqual([f["result"] for f in self.finished(asked, question["step"])], ["question"])
        self.assertEqual(next(e for e in self.of(asked, "step.started") if e["step"] == question["step"]),
                         historic[asked])
        self.assertEqual(len(self.results(asked, "merge")), 1)
        self.assert_no_box_effects()
        self.assert_isolated()


class ForcedBoxRefusalTest(ServiceCase):
    projects = (PLASMA,)

    def test_resuming_a_box_step_with_boxes_disabled_is_refused_without_host_fallback(self):
        card_id = self.card(PLASMA, "Dodaj eksport.\n\nATRAPA: pytanie")
        self.must("StartCard", card_id, json.dumps({"entry": "now", "executor": "Sonnet"}))
        step = self.wait_host_start(card_id)
        self.assertTrue(wait_until(lambda: self.finished(card_id, step["step"]), timeout=30))
        self.stop_proc(self.daemon)

        def boxed(event):
            if event.get("type") == "step.started" and event.get("step") == step["step"]:
                return {**event, "runner": "box", "box": PLASMA, "boxSlug": PLASMA, "boxIp": "10.0.0.5",
                        "boxAccess": 1}
            return event
        self.rewrite(card_id, boxed)
        self.restart()
        before = len(self.steps(card_id))
        reply = self.call("ResolveCardStep", card_id, json.dumps({"choice": "answer", "answer": "ISO 8601"}))
        refused = wait_until(lambda: [e for e in self.of(card_id, "flow.error") if "box.enabled" in (e.get("error") or "")]
                             or (reply.get("ok") is False and "box" in json.dumps(reply)), timeout=20)
        self.settle(3)
        evidence(self, "forced-box", reply=reply, refused=bool(refused), **self.blockade(card_id))
        self.assertTrue(refused, f"brak odmowy wznowienia kroku z pudełka: {reply} {self.blockade(card_id)}")
        self.assertEqual(len(self.steps(card_id)), before, "wznowienie kroku z pudełka przeszło na hosta")
        self.assertEqual([s for s in self.steps(card_id) if s.get("parentStep") == step["step"]], [])
        self.assert_no_box_effects()
        self.assert_isolated()


class SettingsAndConsentTest(ServiceCase):
    def test_box_settings_set_get_at_the_service_boundary(self):
        got = self.must("GetCardFlowSettings")
        self.assertIs(got["box"]["enabled"], False)
        self.assertEqual(got["boxWarnings"], [])
        changed = self.must("SetCardFlowSettings", json.dumps({"box": {"idle-hours": 6}}))
        self.assertEqual((changed["box"]["enabled"], changed["box"]["idle-hours"]), (False, 6.0))
        self.assertEqual(self.must("GetCardFlowSettings")["box"]["idle-hours"], 6.0)
        with open(self.settings_file, "rb") as fh:
            stored = fh.read()
        self.assertEqual(json.loads(stored)["box"], {"enabled": False, "idle-hours": 6})
        for bad in ({"box": {"enabled": "tak"}}, {"box": {"nieznany": 1}}, {"box": []}):
            with self.subTest(request=bad):
                reply = self.call("SetCardFlowSettings", json.dumps(bad))
                self.assertEqual((reply.get("ok"), reply.get("error")), (False, "bad-settings"), reply)
                with open(self.settings_file, "rb") as fh:
                    self.assertEqual(fh.read(), stored, "odrzucony zapis zmienił plik")
        # Skutek po zapisie przez usługę: nadal host, bez oczekiwania i bez Incusa.
        card_id = self.card(PLASMA, "Dodaj eksport.\n\nATRAPA: gotowe")
        self.must("StartCard", card_id, json.dumps({"entry": "now", "executor": "Sonnet"}))
        self.wait_host_start(card_id)
        self.wait_column(card_id, "review", timeout=90)
        self.assert_no_box_effects()
        self.assert_isolated()

    def test_environment_consent_with_boxes_disabled_records_the_decision_and_builds_nothing(self):
        for slug in (PROFILED, BARE):
            with self.subTest(project=slug):
                card_id = self.card(slug, "Plan z pliku", title=f"Zgoda {slug}")
                self.addCleanup(self.release, card_id)
                self.propose(slug, card_id)
                reply = self.must("ApproveCardEnvironment", card_id, BOX_YAML, False)
                self.assertEqual((reply["boxEnabled"], reply["build"], reply["decision"], reply["boxWait"]),
                                 (False, "", "recorded", ""))
                self.assertIn("budowy nie zlecono", reply["message"])
                self.assertTrue(reply["plan"]["map"])
                self.assertTrue(os.path.isfile(os.path.join(self.boxboard, "projects", slug, "cards",
                                                            card_id.lower(), "box.yaml")))
                self.wait_host_start(card_id, "ticket", ticket=reply["plan"]["map"]["#1"])
                self.assertEqual([e for e in self.of(card_id, "box.wait") if e.get("value")], [])
        self.assert_no_box_effects(allowed_box_events=("box.approved",))
        self.assert_isolated()


class EvidenceClassificationTest(unittest.TestCase):
    """Reguły orkiestratora bez usługi: tylko FAIL ze śladem blokady jest czerwonym dowodem."""

    SOURCE = "/tmp/red/daemon"

    def run_of(self, outcome, kinds=("service-ready", "dbus", "blocked"), reason="czeka na manifest projektu"):
        records = []
        for test in RED_TESTS:
            name = test.rsplit(".", 2)[-2] + "." + test.rsplit(".", 1)[1]
            for kind in kinds:
                record = {"test": name, "kind": kind}
                if kind == "service-ready":
                    record["module"] = self.SOURCE + "/tide_daemon/__init__.py"
                if kind == "dbus":
                    record["ok"] = True
                if kind == "blocked":
                    record.update(hostRunnerStart=False, stepWaiting=[{"reason": reason}])
                records.append(record)
        outcomes = {test.rsplit(".", 2)[-2] + "." + test.rsplit(".", 1)[1]: outcome for test in RED_TESTS}
        return {"outcomes": outcomes, "evidence": records}

    def test_evidence_record_keeps_its_kind_next_to_step_fields(self):
        path = os.path.join(tempfile.mkdtemp(prefix="tide-f6-ev-"), "e.jsonl")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), ignore_errors=True)
        old = os.environ.get(EVIDENCE_ENV)
        os.environ[EVIDENCE_ENV] = path
        try:
            evidence(self, "blocked", kind="ticket", stepKind="ticket", hostRunnerStart=False)
        finally:
            if old is None:
                os.environ.pop(EVIDENCE_ENV)
            else:
                os.environ[EVIDENCE_ENV] = old
        with open(path, encoding="utf-8") as fh:
            record = json.loads(fh.read())
        self.assertEqual((record["kind"], record["stepKind"]), ("blocked", "ticket"))

    def test_unittest_summary_lines(self):
        self.assertEqual(_outcome("...\nFAILED (failures=1)\n"), "FAIL")
        self.assertEqual(_outcome("...\nFAILED (failures=1, errors=1)\n"), "ERROR")
        self.assertEqual(_outcome("...\nFAILED (errors=1)\n"), "ERROR")
        self.assertEqual(_outcome("...\nOK (skipped=1)\n"), "SKIP")
        self.assertEqual(_outcome("...\nOK\n"), "ok")
        self.assertEqual(_outcome("Traceback: ImportError\n"), "NONE")

    def test_only_failures_with_readiness_request_and_blockade_are_red(self):
        self.assertTrue(_red_verdict(self.run_of("FAIL"), self.SOURCE)["valid"])
        for outcome in ("ERROR", "SKIP", "NONE", "ok"):
            with self.subTest(outcome=outcome):
                self.assertFalse(_red_verdict(self.run_of(outcome), self.SOURCE)["valid"])
        for kinds in (("dbus", "blocked"), ("service-ready", "blocked"), ("service-ready", "dbus")):
            with self.subTest(kinds=kinds):
                self.assertFalse(_red_verdict(self.run_of("FAIL", kinds), self.SOURCE)["valid"])
        timeout_only = self.run_of("FAIL", reason="")
        self.assertFalse(_red_verdict(timeout_only, self.SOURCE)["valid"], "sam timeout bez powodu blokady")
        self.assertFalse(_red_verdict(self.run_of("FAIL"), "/tmp/inne/daemon")["valid"], "import z innego źródła")


# === Orkiestrator dowodu czerwony/zielony ================================================================

def _git(*args, **kwargs):
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True, **kwargs).stdout


def _export(sha: str, dest: str) -> str:
    """Drzewo `daemon/` z obiektów gita (`git archive`), bez checkoutu, worktree ani zmiany refów."""
    data = _git("archive", "--format=tar", sha, "daemon")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        tar.extractall(dest, filter="data")
    return os.path.join(dest, "daemon")


def _harness(sha: str, dest: str) -> dict:
    """Osobny pakiet testowy z obiektów kandydata — te same bajty dla obu przebiegów."""
    hashes = {}
    for rel in HARNESS_FILES:
        blob = _git("show", f"{sha}:{rel}")
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(blob)
        hashes[rel] = hashlib.sha256(blob).hexdigest()
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(hashes.items())).encode()).hexdigest()
    return {"files": hashes, "sha256": combined}


def _outcome(text: str) -> str:
    """Wynik z końcowej linii unittest: ok, FAIL (same porażki), ERROR (jakikolwiek błąd), SKIP albo NONE."""
    found = re.findall(r"^(OK|FAILED)(?: \((.*)\))?\s*$", text, re.M)
    if not found:
        return "NONE"
    word, counts = found[-1]
    numbers = dict((k, int(v)) for k, v in re.findall(r"(\w+)=(\d+)", counts or ""))
    if numbers.get("errors") or numbers.get("unexpected successes"):
        return "ERROR"
    if numbers.get("skipped") or numbers.get("expected failures"):
        return "SKIP"
    return "FAIL" if word == "FAILED" else "ok"


def _run(label, harness, source, tests, out_dir) -> dict:
    """Jeden proces unittest na pozycję `tests` (osobny wynik każdego scenariusza), wspólny dziennik przebiegu."""
    env = dict(os.environ, **{SOURCE_ENV: source})
    for key in ("TIDE_TEST_ISOLATED", "TIDE_TEST_STATE_DIR", "TIDE_TEST_ORIGINAL_STATE_ROOTS", "TIDE_TEST_GUARD"):
        env.pop(key, None)
    log = os.path.join(out_dir, f"{label}.log")
    records_file = os.path.join(out_dir, f"{label}.evidence.jsonl")
    open(records_file, "w").close()
    env[EVIDENCE_ENV] = records_file
    started, outcomes, commands, codes = time.time(), {}, [], {}
    with open(log, "w", encoding="utf-8") as fh:
        for test in tests:
            argv = ["/usr/bin/python3", "-m", "unittest", "-v", test]
            command = f"cd {harness} && {SOURCE_ENV}={source} {' '.join(argv)}"
            fh.write(f"\n# ==== {label}: {command}\n")
            fh.flush()
            chunk = os.path.join(out_dir, f".{label}.part")
            with open(chunk, "w", encoding="utf-8") as part:
                code = subprocess.run(argv, cwd=harness, env=env, stdout=part, stderr=subprocess.STDOUT).returncode
            with open(chunk, encoding="utf-8", errors="replace") as part:
                text = part.read()
            os.unlink(chunk)
            fh.write(text + f"\n# ==== exit {code}\n")
            fh.flush()
            outcomes[test.rsplit(".", 2)[-2] + "." + test.rsplit(".", 1)[1] if test.count(".") > 3 else test] = \
                _outcome(text)
            commands.append(command)
            codes[test] = code
    with open(records_file, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    return {"label": label, "commands": commands, "exits": codes, "exit": max(codes.values()), "log": log,
            "evidenceFile": records_file, "seconds": round(time.time() - started, 1), "outcomes": outcomes,
            "evidence": records}


BLOCK_WORDS = ("manifest projektu", "środowisk", "environment", "PT-264")


def _red_verdict(run, source) -> dict:
    """Czerwony dowód: każdy scenariusz FAIL (nie ERROR/skip), usługa gotowa z importami starego źródła,
    skuteczne żądanie D-Bus, konkretna blokada environment/ekspozycji i brak startu HostRunner."""
    problems = []
    for test in RED_TESTS:
        name = test.rsplit(".", 1)[1]
        cls = test.rsplit(".", 2)[1]
        mine = [r for r in run["evidence"] if r["test"] == f"{cls}.{name}"]
        if run["outcomes"].get(f"{cls}.{name}") != "FAIL":
            problems.append(f"{cls}.{name}: wynik {run['outcomes'].get(f'{cls}.{name}')!r}, a nie FAIL")
            continue
        ready = [r for r in mine if r["kind"] == "service-ready"]
        if not ready or not all(os.path.realpath(r["module"] or "").startswith(source + os.sep) for r in ready):
            problems.append(f"{cls}.{name}: brak gotowości usługi z importem {source}")
        if not [r for r in mine if r["kind"] == "dbus" and r["ok"] is not False]:
            problems.append(f"{cls}.{name}: brak skutecznego żądania D-Bus")
        blocked = [r for r in mine if r["kind"] == "blocked"]
        # Tylko wartości stanu karty: powody step.waiting, box.wait, notka, boxWait karty i błędy przepływu.
        trace = json.dumps([[w.get("reason") for w in r.get("stepWaiting") or []] +
                            [w.get("value") for w in r.get("boxWait") or []] +
                            [r.get("note"), r.get("cardBoxWait")] + list(r.get("flowErrors") or [])
                            for r in blocked], ensure_ascii=False)
        if not blocked or blocked[-1].get("hostRunnerStart") is not False or \
                not any(word in trace for word in BLOCK_WORDS):
            problems.append(f"{cls}.{name}: brak śladu blokady environment/ekspozycji")
    return {"valid": not problems, "problems": problems}


def evidence_main(argv) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="PT-294: czerwony/zielony dowód harnessu F6")
    parser.add_argument("command", choices=["evidence"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--red-sha", default=RED_SHA)
    parser.add_argument("--candidate", default="HEAD")
    args = parser.parse_args(argv)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    candidate = _git("rev-parse", args.candidate, text=True).strip()
    red = _git("rev-parse", args.red_sha, text=True).strip()
    dirty = _git("status", "--porcelain", "--", *HARNESS_FILES, text=True).strip()
    summary = {"harnessVersion": HARNESS_VERSION, "redSha": red, "f6Base": F6_BASE, "candidateSha": candidate,
               "trees": {"red": _git("rev-parse", f"{red}^{{tree}}", text=True).strip(),
                         "candidate": _git("rev-parse", f"{candidate}^{{tree}}", text=True).strip()},
               "harnessUncommittedChanges": dirty, "redTests": list(RED_TESTS)}
    tmp = tempfile.mkdtemp(prefix="tide-f6-evidence-", dir=os.environ.get("TIDE_TEST_TMPDIR") or None)
    try:
        harness = os.path.join(tmp, "harness")
        summary["harness"] = _harness(candidate, harness)
        sources = {"red": os.path.realpath(_export(red, os.path.join(tmp, "red"))),
                   "candidate": os.path.realpath(_export(candidate, os.path.join(tmp, "candidate")))}
        summary["sources"] = sources
        runs = [_run("red", harness, sources["red"], RED_TESTS, out),
                _run("green-scenario", harness, sources["candidate"], RED_TESTS, out),
                _run("green-full", harness, sources["candidate"], [MODULE], out)]
        summary["runs"] = [{k: v for k, v in r.items() if k != "evidence"} for r in runs]
        summary["red"] = _red_verdict(runs[0], sources["red"])
        summary["green"] = {"scenario": runs[1]["exit"] == 0 and set(runs[1]["outcomes"].values()) == {"ok"},
                            "full": runs[2]["exit"] == 0 and set(runs[2]["outcomes"].values()) == {"ok"}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"red": summary.get("red"), "green": summary.get("green"), "candidate": candidate,
                      "harness": (summary.get("harness") or {}).get("sha256"),
                      "runs": [(r["label"], r["exit"], r["outcomes"]) for r in summary.get("runs", [])]},
                     ensure_ascii=False, indent=1))
    ok = summary.get("red", {}).get("valid") and all(summary.get("green", {}).values()) and not dirty
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(evidence_main(sys.argv[1:]))
