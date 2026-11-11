#!/usr/bin/python3
"""Pomiar pierwszego wywołania serii żądań D-Bus (K-98 #4, PT-345): prawdziwa usługa z sondą procesów.

Usługa (`<src>/daemon/tide-daemon.py`) startuje jako osobny proces na prywatnej magistrali (własny dbus-daemon bez
katalogów usług), z izolowanymi HOME, XDG_STATE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME,
XDG_RUNTIME_DIR i Tablicą w katalogu tymczasowym, z kopią dzienników z `daemon/tests/fixtures`. Sonda procesów jest
WŁĄCZONA (bez `--no-processes`): usługa czyta prawdziwe `/proc` maszyny (tylko odczyt), tak jak w produkcji co tick
monitora sesji (5 s). Wyłączone są tylko dodatki dotykające pulpitu lub sieci (KRunner, powiadomienia, karty
Konsole, YouTrack, limity, modele, Docker, raport, bezczynność).

Seria = `/Agents Sessions`, `/Board Snapshot`, `/Board Work`, `/Coordination Coordinators` wołane jedno po drugim.
Między seriami przerwa `--gap` s (domyślnie 1,3 s — nie jest dzielnikiem 5 s, więc początki serii przesuwają się
względem ticku monitora i trafiają też w trwającą sondę). Pierwsza seria startuje po `--warmup` s od rejestracji
usługi (pierwsza migawka i pierwszy tick). Wynik: czasy pierwszego wywołania każdej serii (surowe), p95
nearest-rank (k = ⌈0,95·n⌉), max, czasy wszystkich wywołań, loadavg przed i po, SHA i liczba procesów w /proc.

Użycie (z katalogu repozytorium):
    /usr/bin/python3 daemon/tests/perf_dbus_first_call.py --out wynik.json [--src <katalog z daemon/>] [--sha <SHA>]
        [--series 20] [--gap 1.3] [--warmup 6]

Skrypt niczego nie zapisuje poza katalogiem tymczasowym (usuwany na końcu) i plikiem `--out`.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.dirname(os.path.dirname(HERE))
SERVICE = "org.tide.Daemon"
SERIES = (("/Agents", "org.tide.Daemon.Agents", "Sessions"),
          ("/Board", "org.tide.Daemon.Board", "Snapshot"),
          ("/Board", "org.tide.Daemon.Board", "Work"),
          ("/Coordination", "org.tide.Daemon.Coordination", "Coordinators"))
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
OFF = ("--no-krunner", "--no-notifications", "--no-tab-marks", "--no-youtrack", "--no-idle", "--no-limits",
       "--no-models", "--no-docker", "--no-report", "--no-activity-look", "--no-monitors", "--no-stream-shortcuts")


def nearest_rank(samples, q=0.95):
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


def proc_count():
    try:
        return sum(1 for name in os.listdir("/proc") if name.isdigit())
    except OSError:
        return 0


def isolated_env(base, address):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("XDG_", "TIDE_", "DBUS_"))}
    paths = {"HOME": "home", "XDG_STATE_HOME": "state", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
             "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run"}
    for key, rel in paths.items():
        env[key] = os.path.join(base, rel)
        os.makedirs(env[key], mode=0o700, exist_ok=True)
    env.update(DBUS_SESSION_BUS_ADDRESS=address, QT_QPA_PLATFORM="offscreen",
               TIDE_BOARD_DIR=os.path.join(base, "board"), TIDE_CODEX_DESKTOP="", TIDE_CLAUDE_BIN="",
               WAYLAND_DISPLAY=os.path.join(base, "no-wayland"),
               LIMITY_CONFIG=os.path.join(base, "no-limity-config.json"),
               DOCKER_HOST="unix://" + os.path.join(base, "no-docker.sock"),
               TIDE_MODELS_CONFIG=os.path.join(HERE, "fixtures", "models-closed.json"),
               GIT_CONFIG_NOSYSTEM="1")
    return env


def measure(src, sha, series, gap, warmup):
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
    QCoreApplication.instance() or QCoreApplication([])
    base = os.path.realpath(tempfile.mkdtemp(prefix="tide-perf-dbus-"))
    result = {"tool": "daemon/tests/perf_dbus_first_call.py", "sha": sha or git_sha(src), "src": src,
              "params": {"series": series, "gapS": gap, "warmupS": warmup,
                         "calls": [f"{path} {member}" for path, _, member in SERIES], "processes": True},
              "cpuCount": os.cpu_count(), "loadavgBefore": loadavg(), "procCount": proc_count()}
    bus_proc = daemon = None
    try:
        with open(os.path.join(base, "bus.conf"), "w", encoding="utf-8") as fh:
            fh.write(BUS_CONFIG.format(tmp=base))
        bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={os.path.join(base, 'bus.conf')}", "--nofork",
                                     "--nopidfile", "--print-address=1"], stdout=subprocess.PIPE, text=True)
        address = bus_proc.stdout.readline().strip()
        if not address:
            raise RuntimeError("prywatny dbus-daemon nie wystartował")
        bus = QDBusConnection.connectToBus(address, "tide-perf-first-call")
        claude, codex = os.path.join(base, "claude"), os.path.join(base, "codex")
        shutil.copytree(os.path.join(HERE, "fixtures", "claude"), claude)
        shutil.copytree(os.path.join(HERE, "fixtures", "codex"), codex)
        os.makedirs(os.path.join(base, "work"))
        env = isolated_env(base, address)
        started = time.perf_counter()
        with open(os.path.join(base, "daemon.log"), "w", encoding="utf-8") as log:
            daemon = subprocess.Popen([sys.executable, os.path.join(src, "daemon", "tide-daemon.py"),
                                       "--claude-dir", claude, "--codex-dir", codex, "--workspace",
                                       os.path.join(base, "work"), *OFF], env=env, stdout=log, stderr=log)
        deadline = time.time() + 60
        while time.time() < deadline and daemon.poll() is None:
            if bus.interface().isServiceRegistered(SERVICE).value():
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("usługa nie zarejestrowała się na magistrali")
        result["registerMs"] = (time.perf_counter() - started) * 1000.0
        time.sleep(warmup)

        def call(path, iface, member):
            msg = QDBusMessage.createMethodCall(SERVICE, path, iface, member)
            begin = time.perf_counter()
            reply = bus.call(msg, QDBus.CallMode.Block, 30000)
            ms = (time.perf_counter() - begin) * 1000.0
            ok = reply.type() == QDBusMessage.MessageType.ReplyMessage
            return ms, ok, "" if ok else reply.errorMessage()

        first, rows, errors = [], [], []
        for index in range(series):
            row = []
            for path, iface, member in SERIES:
                ms, ok, error = call(path, iface, member)
                row.append(ms)
                if not ok:
                    errors.append({"series": index, "call": member, "error": error})
            first.append(row[0])
            rows.append(row)
            time.sleep(gap)
        result["firstCallMs"] = first
        result["p95FirstMs"] = nearest_rank(first)
        result["maxFirstMs"] = max(first) if first else None
        result["seriesMs"] = rows
        result["errors"] = errors
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
        result["loadavgAfter"] = loadavg()
        try:
            with open(os.path.join(base, "daemon.log"), encoding="utf-8", errors="replace") as fh:
                result["daemonLogTail"] = fh.read().splitlines()[-30:]
        except OSError:
            result["daemonLogTail"] = []
        shutil.rmtree(base, ignore_errors=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--src", default=DEFAULT_SRC, help="katalog z daemon/ (np. wypakowana baza)")
    parser.add_argument("--sha", default="")
    parser.add_argument("--series", type=int, default=20)
    parser.add_argument("--gap", type=float, default=1.3)
    parser.add_argument("--warmup", type=float, default=6.0)
    args = parser.parse_args(argv)
    result = measure(os.path.realpath(args.src), args.sha, args.series, args.gap, args.warmup)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"SHA {result['sha']}: pierwsze wywołanie serii p95 {result['p95FirstMs']:.1f} ms, "
          f"max {result['maxFirstMs']:.1f} ms; błędy {len(result['errors'])}; procesy w /proc {result['procCount']}; "
          f"loadavg {result['loadavgBefore']} → {result['loadavgAfter']}", flush=True)
    return 0 if not result["errors"] and result.get("daemonAlive") else 1


if __name__ == "__main__":
    sys.exit(main())
