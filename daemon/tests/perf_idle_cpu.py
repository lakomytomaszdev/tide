#!/usr/bin/python3
"""Reproduktor CPU usługi na postoju (K-98 #5, PT-346): prawdziwa usługa, izolowane dane, `box.enabled` true/false.

Usługa (`<src>/daemon/tide-daemon.py`) startuje jako osobny proces na prywatnej magistrali (własny dbus-daemon bez
katalogów usług), z izolowanymi HOME, XDG_STATE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME,
XDG_RUNTIME_DIR i Tablicą w katalogu tymczasowym. Dane:

- Tablica z 30 kartami, trzema projektami z repozytoriami git i dziennikami przepływu — złożona tym samym kodem co
  reproduktor PT-342 (`perf_board_refresh.Service`), z `box.enabled` w `cardflow.json` albo bez (`--box on|off`);
  `incus` to atrapa (`fake_incus.py`, `TIDE_INCUS_BIN`, stan w katalogu tymczasowym) — żadne pudełko nie powstaje,
- `--sessions` (domyślnie 200) sesji Claude Code z ostatnich 30 dni (kilka z ostatniej doby — widoczne w monitorze
  sesji), dla indeksu KRunnera i wyszukiwania po znaczeniu,
- atrapa serwera Ollamy (`/api/embed`, wektory 1024 wymiarów liczone ze skrótu tekstu) w tym procesie — indeks
  wyszukiwania po znaczeniu działa naprawdę, ale nic nie idzie do modeli tego komputera.

Sonda procesów jest WŁĄCZONA (usługa czyta prawdziwe `/proc` maszyny, tylko odczyt — jak w produkcji). Wyłączone są
dodatki dotykające pulpitu lub sieci (powiadomienia, karty Konsole, YouTrack, limity, monitor modeli, Docker, raport,
bezczynność, proxy pudełka `--no-box` — nie zajmuje portów produkcyjnej usługi). KRunner jest włączony (indeks
wyszukiwania), rejestruje się na prywatnej magistrali.

Po rozgrzewce (`--warmup` s; pierwszy indeks wyszukiwania i pierwszy tick) dwa okna:

- okno CPU (`--window` s, bez próbnika): czas CPU procesu (utime+stime z `/proc/<pid>/stat`) co sekundę — surowe
  próbki — oraz czas CPU każdego wątku (`/proc/<pid>/task/*/stat`, nazwa z `comm`) na początku i końcu,
- okno próbnika (`--window` s): żądanie `$XDG_RUNTIME_DIR/tide-daemon-profile` → raport próbnika usługi; z sekcji
  „czas łączny” podział próbek pracy na: indeks (`semantic.py:sync`), sonda procesów (`processes.py:probe`), tick
  (`engine.py:tick`) i resztę. Próbnik sam zużywa CPU, więc wynik progu pochodzi z okna CPU, a nie z tego okna.

Na końcu pomiar w procesie reproduktora (ten sam kod `--src`): koszt CPU jednego przebiegu sondy procesów na
prawdziwym `/proc` (20 kolejnych przebiegów) i jednego przebiegu indeksu wyszukiwania bez zmian (5 przebiegów po
pierwszym pełnym) — z liczbą zapisanych bajtów pliku indeksu. To uzupełnia podział: przebieg indeksu jest co 300 s,
więc w 60-sekundowym oknie może go nie być.

CPU w procentach jednego rdzenia. Wynik JSON zawiera surowe próbki, SHA, loadavg przed i po, liczbę rdzeni i procesów.

Użycie (z katalogu repozytorium):
    /usr/bin/python3 daemon/tests/perf_idle_cpu.py --out wynik.json --box on|off [--src <katalog z daemon/>]
        [--sha <SHA>] [--sessions 200] [--warmup 45] [--window 60]

Skrypt niczego nie zapisuje poza katalogiem tymczasowym (usuwany na końcu) i plikiem `--out`.
Testy jednostkowe nie mierzą czasu; ten skrypt jest wyłącznie narzędziem pomiaru.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.dirname(os.path.dirname(HERE))
SERVICE = "org.tide.Daemon"
KRUNNER = "org.tide.Daemon.KRunner"
DIMS = 1024
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
OFF = ("--no-notifications", "--no-tab-marks", "--no-youtrack", "--no-idle", "--no-limits", "--no-models",
       "--no-docker", "--no-report", "--no-activity-look", "--no-monitors", "--no-stream-shortcuts", "--no-box")
# Kategorie podziału kosztu: ramka w stosie (sekcja „czas łączny” raportu próbnika).
CATEGORIES = (("index", "semantic.py:sync"), ("probe", "processes.py:probe"), ("tick", "engine.py:tick"))
PROJECTS = ("plasma-tide", "alpha", "beta")
TOPICS = ("Kontenery i mapowanie portów", "Formularz logowania", "Raport poranny i notki", "Ponawianie wysyłania",
          "Motyw SDDM i tapeta", "Indeks wyszukiwania sesji", "Sonda procesów", "Tablica kart i panel",
          "Pudełko i sieć", "Plan karty i tickety")


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


def proc_count():
    try:
        return sum(1 for name in os.listdir("/proc") if name.isdigit())
    except OSError:
        return 0


# --- izolowane dane ---------------------------------------------------------------------------------

def isolated_env(base, address=""):
    """Te same katalogi co `perf_board_refresh.isolated_env` (Tablica złożona tamtym kodem jest tu widoczna)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("XDG_", "TIDE_", "DBUS_"))}
    names = {"HOME": "home", "XDG_STATE_HOME": "state", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
             "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run"}
    for key, rel in names.items():
        env[key] = os.path.join(base, rel)
        os.makedirs(env[key], mode=0o700, exist_ok=True)
    binary = os.path.join(base, "bin", "incus")
    env.update(TIDE_BOARD_DIR=os.path.join(base, "board"), TIDE_FAKE_INCUS_STATE=os.path.join(base, "incus-state"),
               TIDE_FAKE_LOG=os.path.join(base, "incus.log"), TIDE_INCUS_BIN=binary,
               PATH=os.path.dirname(binary) + os.pathsep + env.get("PATH", "/usr/bin:/bin"),
               GIT_CONFIG_GLOBAL=os.path.join(base, "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
               QT_QPA_PLATFORM="offscreen", TIDE_CODEX_DESKTOP="", TIDE_CLAUDE_BIN="",
               WAYLAND_DISPLAY=os.path.join(base, "no-wayland"),
               LIMITY_CONFIG=os.path.join(base, "no-limity-config.json"),
               DOCKER_HOST="unix://" + os.path.join(base, "no-docker.sock"),
               TIDE_MODELS_CONFIG=os.path.join(base, "models.json"))
    if address:
        env["DBUS_SESSION_BUS_ADDRESS"] = address
    return env


def prepare_board(base, src, box, cards):
    """Tablica reproduktora PT-342 w osobnym procesie (kod usługi z `src`)."""
    code = ("import sys, os\n"
            f"sys.path.insert(0, {os.path.join(src, 'daemon')!r})\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import perf_board_refresh as pbr\n"
            f"base = {base!r}\n"
            "with pbr.isolated_env(base):\n"
            f"    service = pbr.Service(base, {cards}, box_file={bool(box)!r})\n"
            "    service.close()\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "TIDE_", "DBUS_"))}
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run([sys.executable, "-c", code], check=True, env=env, cwd=base)


def write_sessions(base, count, now):
    """`count` sesji Claude Code: projekty z repozytoriami Tablicy, ostatnia aktywność rozłożona na 30 dni."""
    claude = os.path.join(base, "claude")
    os.makedirs(os.path.join(claude, "projects"), exist_ok=True)
    for index in range(count):
        project = PROJECTS[index % len(PROJECTS)]
        cwd = os.path.join(base, "ws", project)
        sid = str(uuid.UUID(int=index + 1))
        folder = os.path.join(claude, "projects", cwd.replace("/", "-"))
        os.makedirs(folder, exist_ok=True)
        # Pięć sesji z ostatniej doby (monitor sesji), reszta 1–29 dni temu (tylko indeks KRunnera).
        age = (index + 1) * 600.0 if index < 5 else 86400.0 * (1 + (index * 7) % 28) + index
        stamp = now - age
        iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(stamp))
        topic = TOPICS[index % len(TOPICS)]
        rows = [
            {"type": "user", "isSidechain": False, "timestamp": iso, "cwd": cwd, "gitBranch": "master",
             "sessionId": sid, "message": {"role": "user", "content": f"{topic}: zadanie {index}"}},
            {"type": "assistant", "isSidechain": False, "timestamp": iso, "cwd": cwd, "gitBranch": "master",
             "sessionId": sid, "message": {"role": "assistant", "stop_reason": "end_turn",
                                           "content": [{"type": "text", "text": "Zrobione."}]}},
            {"type": "ai-title", "aiTitle": f"{topic} {index}", "sessionId": sid},
        ]
        path = os.path.join(folder, f"{sid}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        os.utime(path, (stamp, stamp))
    return claude


def write_incus(base):
    binary = os.path.join(base, "bin", "incus")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "w", encoding="utf-8") as fh:
        fh.write(f'#!/bin/sh\nexec /usr/bin/python3 -s "{os.path.join(HERE, "fake_incus.py")}" "$@"\n')
    os.chmod(binary, 0o755)
    os.makedirs(os.path.join(base, "incus-state"), exist_ok=True)


# --- atrapa serwera embeddingów ---------------------------------------------------------------------

def fake_vector(text):
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    raw = (seed * (DIMS * 2 // len(seed) + 1))[:DIMS * 2]
    return [((v % 2000) - 1000) / 1000.0 for v in struct.unpack(f"<{DIMS}H", raw)]


class EmbedServer:
    def __init__(self):
        self.calls = 0
        self.texts = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/api/embed":
                    self.send_error(404)
                    return
                texts = body.get("input") or []
                texts = [texts] if isinstance(texts, str) else texts
                owner.calls += 1
                owner.texts += len(texts)
                data = json.dumps({"model": body.get("model"), "embeddings": [fake_vector(t) for t in texts]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data.encode("utf-8"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def write_models(path, port):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"default": "local", "endpoints": {"local": {"url": f"http://127.0.0.1:{port}", "kind": "ollama"}},
                   "roles": {"embed": "local:bge-m3"}, "semanticSearch": True}, fh)


# --- CPU procesu i wątków ---------------------------------------------------------------------------

TICKS = os.sysconf("SC_CLK_TCK")


def _stat_fields(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        stat = fh.read()
    return stat[stat.find("(") + 1: stat.rfind(")")], stat[stat.rfind(")") + 2:].split()


def process_cpu(pid):
    """(utime+stime, cutime+cstime) procesu w sekundach."""
    _name, fields = _stat_fields(f"/proc/{pid}/stat")
    return (int(fields[11]) + int(fields[12])) / TICKS, (int(fields[13]) + int(fields[14])) / TICKS


def thread_cpu(pid):
    out = {}
    for task in glob.glob(f"/proc/{pid}/task/*"):
        try:
            name, fields = _stat_fields(os.path.join(task, "stat"))
        except OSError:
            continue
        out[os.path.basename(task)] = {"name": name, "cpuS": (int(fields[11]) + int(fields[12])) / TICKS}
    return out


def cpu_window(pid, seconds):
    """Surowe próbki CPU procesu co sekundę (procent jednego rdzenia) i czas CPU wątków."""
    threads_before = thread_cpu(pid)
    own0, child0 = process_cpu(pid)
    wall0 = time.monotonic()
    samples, last_own, last_wall = [], own0, wall0
    while time.monotonic() - wall0 < seconds:
        time.sleep(1.0)
        own, _child = process_cpu(pid)
        now = time.monotonic()
        samples.append(round(100.0 * (own - last_own) / (now - last_wall), 2))
        last_own, last_wall = own, now
    own1, child1 = process_cpu(pid)
    wall = time.monotonic() - wall0
    threads_after = thread_cpu(pid)
    threads = {}
    for tid, after in threads_after.items():
        before = threads_before.get(tid, {"cpuS": 0.0})
        threads[tid] = {"name": after["name"], "cpuS": round(after["cpuS"] - before["cpuS"], 3)}
    return {"wallS": round(wall, 3), "cpuS": round(own1 - own0, 3), "childCpuS": round(child1 - child0, 3),
            "cpuPercent": round(100.0 * (own1 - own0) / wall, 2), "samplesPercent": samples, "threads": threads}


# --- raport próbnika --------------------------------------------------------------------------------

def parse_profile(text):
    busy, total, section = 0, {}, ""
    for line in text.splitlines():
        if line.startswith("Próbek poza ramkami"):
            busy = int(line.rsplit(":", 1)[1].strip())
        elif line.startswith("## "):
            section = line
        elif section.startswith("## Funkcje — czas łączny") and line.strip():
            count, _, name = line.strip().partition("  ")
            try:
                total[name.strip()] = int(count)
            except ValueError:
                pass
    split = {key: total.get(frame, 0) for key, frame in CATEGORIES}
    split["rest"] = max(0, busy - sum(split.values()))
    return {"busySamples": busy, "samples": split,
            "share": {k: (round(v / busy, 4) if busy else None) for k, v in split.items()},
            "frames": {frame: total.get(frame, 0) for _key, frame in CATEGORIES}}


def profile_window(base, pid, seconds):
    state = os.path.join(base, "state", "tide")
    before = set(glob.glob(os.path.join(state, "profile-*.txt")))
    request = os.path.join(base, "run", "tide-daemon-profile")
    with open(request, "w", encoding="utf-8") as fh:
        fh.write(f"{int(seconds)}\n")
    cpu = cpu_window(pid, seconds + 4)      # próbnik sprawdza żądanie co 3 s
    deadline = time.monotonic() + seconds + 30
    report = ""
    while time.monotonic() < deadline:
        fresh = sorted(set(glob.glob(os.path.join(state, "profile-*.txt"))) - before)
        if fresh:
            time.sleep(0.5)
            with open(fresh[-1], encoding="utf-8") as fh:
                report = fh.read()
            break
        time.sleep(0.5)
    return {"cpu": cpu, "split": parse_profile(report) if report else None, "report": report}


# --- pomiar w procesie: sonda i indeks ----------------------------------------------------------------

def micro(base, src, claude, port):
    """Koszt CPU jednego przebiegu sondy procesów i indeksu wyszukiwania bez zmian (kod z `src`)."""
    code = f"""
import json, os, sys, time
sys.path.insert(0, {os.path.join(src, 'daemon')!r})
from tide_daemon import processes
from tide_daemon.krunner.index import SessionIndex
from tide_daemon.krunner import semantic
from tide_daemon.models import config as models_config
out = {{}}
prober = processes.Prober() if hasattr(processes, "Prober") else None
probe = prober.probe if prober is not None else processes.probe
cpu = []
for _ in range(20):
    t = time.thread_time(); probe(); cpu.append(round((time.thread_time() - t) * 1000.0, 3))
out["probe"] = {{"incremental": prober is not None, "cpuMs": cpu,
                 "counters": dict(getattr(prober, "counters", {{}}) or {{}})}}
index = SessionIndex({claude!r}, [{os.path.join(base, 'codex')!r}])
path = os.path.join({base!r}, "micro-index", "sessions.json")
cfg = models_config.load
search = semantic.SemanticSearch(index, path, config=cfg, embed=None, home={os.path.join(base, 'home')!r})
search.queue = None
t = time.thread_time(); search.sync(); first = round((time.thread_time() - t) * 1000.0, 3)
idle, written = [], []
for _ in range(5):
    size = os.path.getsize(path) if os.path.exists(path) else 0
    mtime = os.stat(path).st_mtime_ns if os.path.exists(path) else 0
    t = time.thread_time(); search.sync(); idle.append(round((time.thread_time() - t) * 1000.0, 3))
    changed = os.path.exists(path) and os.stat(path).st_mtime_ns != mtime
    written.append(os.path.getsize(path) if changed else 0)
out["index"] = {{"sessions": len(index.entries()), "firstPassCpuMs": first, "idlePassCpuMs": idle,
                 "idlePassBytesWritten": written, "state": search.state(),
                 "counters": dict(getattr(search, "counters", {{}}) or {{}}),
                 "storeCounters": dict(getattr(search.store, "counters", {{}}) or {{}}),
                 "everyS": semantic.INDEX_EVERY_S}}
print(json.dumps(out))
"""
    env = isolated_env(base)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=base,
                            timeout=600)
    if result.returncode != 0:
        return {"error": result.stderr[-2000:]}
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- przebieg ---------------------------------------------------------------------------------------

def measure(src, sha, box, sessions, warmup, window, cards=30):
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBusConnection
    QCoreApplication.instance() or QCoreApplication([])
    base = os.path.realpath(tempfile.mkdtemp(prefix="tide-perf-idle-"))
    result = {"tool": "daemon/tests/perf_idle_cpu.py", "sha": sha or git_sha(src), "src": src,
              "params": {"box": box, "sessions": sessions, "cards": cards, "warmupS": warmup, "windowS": window,
                         "processes": True, "krunner": True},
              "cpuCount": os.cpu_count(), "loadavgBefore": loadavg(), "procCount": proc_count()}
    bus_proc = daemon = embed = None
    try:
        with open(os.path.join(base, "gitconfig"), "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Perf\n\temail = perf@example.invalid\n[init]\n\tdefaultBranch = master\n")
        prepare_board(base, src, box, cards)
        write_incus(base)
        now = time.time()
        claude = write_sessions(base, sessions, now)
        codex = os.path.join(base, "codex")
        os.makedirs(os.path.join(codex, "sessions"), exist_ok=True)
        embed = EmbedServer()
        write_models(os.path.join(base, "models.json"), embed.port)
        with open(os.path.join(base, "bus.conf"), "w", encoding="utf-8") as fh:
            fh.write(BUS_CONFIG.format(tmp=base))
        bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={os.path.join(base, 'bus.conf')}", "--nofork",
                                     "--nopidfile", "--print-address=1"], stdout=subprocess.PIPE, text=True)
        address = bus_proc.stdout.readline().strip()
        if not address:
            raise RuntimeError("prywatny dbus-daemon nie wystartował")
        bus = QDBusConnection.connectToBus(address, "tide-perf-idle")
        env = isolated_env(base, address)
        with open(os.path.join(base, "daemon.log"), "w", encoding="utf-8") as log:
            daemon = subprocess.Popen([sys.executable, os.path.join(src, "daemon", "tide-daemon.py"),
                                       "--claude-dir", claude, "--codex-dir", codex, "--workspace",
                                       os.path.join(base, "ws"), "--search-delay", "1", *OFF],
                                      env=env, stdout=log, stderr=log, cwd=base)
        deadline = time.time() + 60
        while time.time() < deadline and daemon.poll() is None:
            if bus.interface().isServiceRegistered(SERVICE).value():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("usługa nie zarejestrowała się na magistrali")
        result["krunnerRegistered"] = bool(bus.interface().isServiceRegistered(KRUNNER).value())
        time.sleep(warmup)
        index_file = os.path.join(base, "data", "tide", "search", "sessions.json")
        result["indexFileAfterWarmup"] = os.path.getsize(index_file) if os.path.exists(index_file) else None
        result["embedAfterWarmup"] = {"calls": embed.calls, "texts": embed.texts}
        result["loadavgWindowStart"] = loadavg()
        result["cpuWindow"] = cpu_window(daemon.pid, window)
        result["loadavgWindowEnd"] = loadavg()
        result["embedAfterCpuWindow"] = {"calls": embed.calls, "texts": embed.texts}
        result["profileWindow"] = profile_window(base, daemon.pid, window)
        result["daemonAlive"] = daemon.poll() is None
    finally:
        if daemon is not None and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(10)
            except subprocess.TimeoutExpired:
                daemon.kill()
        if bus_proc is not None:
            bus_proc.terminate()
            try:
                bus_proc.wait(5)
            except subprocess.TimeoutExpired:
                bus_proc.kill()
            if bus_proc.stdout:
                bus_proc.stdout.close()
        try:
            with open(os.path.join(base, "daemon.log"), encoding="utf-8", errors="replace") as fh:
                result["daemonLogTail"] = fh.read().splitlines()[-40:]
        except OSError:
            result["daemonLogTail"] = []
    try:
        if embed is not None:
            result["micro"] = micro(base, src, os.path.join(base, "claude"), embed.port)
    finally:
        if embed is not None:
            embed.close()
        result["loadavgAfter"] = loadavg()
        shutil.rmtree(base, ignore_errors=True)
    return result


def summary(result):
    cpu = result.get("cpuWindow") or {}
    split = ((result.get("profileWindow") or {}).get("split") or {})
    share = split.get("share") or {}
    micro_ = result.get("micro") or {}
    probe = (micro_.get("probe") or {}).get("cpuMs") or []
    index = micro_.get("index") or {}
    parts = ", ".join(f"{k} {100 * v:.1f} %" for k, v in share.items() if v is not None)
    return (f"SHA {result['sha']} box={result['params']['box']}: CPU usługi {cpu.get('cpuPercent')} % "
            f"(okno {cpu.get('wallS')} s); podział próbek: {parts or '—'}; sonda (w procesie) "
            f"{(sum(probe) / len(probe)) if probe else 0:.1f} ms CPU/przebieg; indeks bez zmian "
            f"{index.get('idlePassCpuMs')} ms CPU, zapis {index.get('idlePassBytesWritten')} B; "
            f"loadavg {result['loadavgBefore']} → {result['loadavgAfter']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--box", choices=("on", "off"), required=True)
    parser.add_argument("--src", default=DEFAULT_SRC, help="katalog z daemon/ (np. wypakowana baza)")
    parser.add_argument("--sha", default="")
    parser.add_argument("--sessions", type=int, default=200)
    parser.add_argument("--warmup", type=float, default=45.0)
    parser.add_argument("--window", type=float, default=60.0)
    args = parser.parse_args(argv)
    result = measure(os.path.realpath(args.src), args.sha, args.box == "on", args.sessions, args.warmup, args.window)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(summary(result), flush=True)
    return 0 if result.get("daemonAlive") else 1


if __name__ == "__main__":
    sys.exit(main())
