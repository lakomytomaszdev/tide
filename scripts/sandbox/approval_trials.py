"""Próby rzeczywistej piaskownicy dla zatwierdzenia kontraktu testów (K-69, PT-286) — warunek odbioru ticketu.

Uruchamiać POZA piaskownicą (sam uruchamia kroki w piaskownicach Claude Code i Codexa), z katalogu repozytorium:

    /usr/bin/python3 scripts/sandbox/approval_trials.py <katalog wyniku> [--tools claude,codex] [--skip-tests]

Co robi:
1. (bez --skip-tests) testy D-Bus i ochrony zatwierdzenia: `python3 -m unittest daemon.tests.test_board_dbus
   daemon.tests.test_board_step_approval` — w piaskownicy kroku prywatna magistrala nie wstaje, więc tam są pomijane.
2. Prywatna magistrala (gniazdo w systemie plików, jak `/run/user/<uid>/bus`) i usługa tide-daemon z kodu tego
   repozytorium na niej, z próbną Tablicą i magazynem odcisków w `~/.local/state/tide-pt286-trials` (to samo
   położenie względem piaskownicy co prawdziwy `~/.local/state/tide`). Skrypt (poza krokiem) zakłada projekt
   i zatwierdza jego kontrakt przez `UpsertProject` z `tests`.
3. Dla każdego programu krok w tle złożony przez `tasks/agentcmd.py` (`background()` + `start()` — jednostka
   `systemd-run --user --scope` `tide-step-*`, te same argumenty piaskownicy co krok ticketu, bez listy poleceń,
   czyli z najsłabszą polityką). Agent dostaje jedno polecenie: `approval_probe.py` (próby a/b/c).
4. Sprawdza: wynik każdej próby (`denied` albo `refused`), niezmieniony plik odcisków (próbny i prawdziwy),
   niezmieniony `box.yaml` i HEAD Tablicy oraz `testsApproved` projektu w usłudze. Raport: `report.md`
   i `report.json` w katalogu wyniku. Kod wyjścia 0 — wszystkie próby odmówione i dane bez zmian.

Prawdziwego magazynu odcisków ani prawdziwej Tablicy skrypt nie zmienia (próby na nich bez zapisu danych).
Kroki wołają prawdziwe modele (krótkie zadanie: jedno polecenie).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "daemon"
sys.path.insert(0, str(DAEMON))

from tide_daemon.board import contract_approval  # noqa: E402
from tide_daemon.tasks import agentcmd  # noqa: E402

SERVICE, PATH, IFACE = "org.tide.Daemon", "/Board", "org.tide.Daemon.Board"
CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
EVIL = {"changed": "make evil BASE={base}", "full": "make evil"}
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={socket}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
OK_OUTCOMES = ("denied", "refused")


def sha(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Trial",
                                   GIT_AUTHOR_EMAIL="trial@example.invalid", GIT_COMMITTER_NAME="Trial",
                                   GIT_COMMITTER_EMAIL="trial@example.invalid")).stdout


def dbus(bus: str, dest: str, path: str, member: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    argv = ["/usr/bin/dbus-send", f"--bus={bus}", "--print-reply=literal", f"--reply-timeout={timeout * 1000}",
            f"--dest={dest}", path, member, *args]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 5)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def board_call(bus: str, member: str, *strings: str) -> dict:
    code, out = dbus(bus, SERVICE, PATH, f"{IFACE}.{member}", *[f"string:{s}" for s in strings])
    if code != 0:
        raise RuntimeError(f"{member}: kod {code}: {out}")
    return json.loads(out)


class Service:
    """Prywatna magistrala i usługa z kodu repozytorium; Tablica i magazyn odcisków próbne."""

    def __init__(self, run: Path, state_home: Path):
        self.run, self.state_home = run, state_home
        self.bus_dir = Path(tempfile.mkdtemp(prefix="tide-pt286-bus-"))
        socket = self.bus_dir / "bus"
        (self.bus_dir / "bus.conf").write_text(BUS_CONFIG.format(socket=socket), encoding="utf-8")
        self.bus_proc = subprocess.Popen(["dbus-daemon", f"--config-file={self.bus_dir / 'bus.conf'}", "--nofork",
                                          "--nopidfile", "--print-address=1"], stdout=subprocess.PIPE, text=True)
        self.address = self.bus_proc.stdout.readline().strip()
        if not self.address:
            raise RuntimeError("prywatny dbus-daemon nie wystartował")
        self.ws = run / "ws"
        self.board = self.ws / "tide-board"
        scratch = run / "service"
        scratch.mkdir(parents=True)
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address, XDG_STATE_HOME=str(state_home),
                   XDG_CACHE_HOME=str(scratch / "cache"), WAYLAND_DISPLAY=str(scratch / "no-wayland"),
                   TIDE_CODEX_DESKTOP="", TIDE_CLAUDE_BIN="", TIDE_BOARD_DIR=str(self.board),
                   LIMITY_CONFIG=str(scratch / "no-limity.json"),
                   DOCKER_HOST="unix://" + str(scratch / "no-docker.sock"),
                   TIDE_MODELS_CONFIG=str(DAEMON / "tests" / "fixtures" / "models-closed.json"),
                   GIT_CONFIG_GLOBAL=str(scratch / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        (scratch / "gitconfig").write_text("[user]\n\tname = Trial\n\temail = trial@example.invalid\n"
                                           "[init]\n\tdefaultBranch = master\n", encoding="utf-8")
        self.log = open(scratch / "daemon.log", "ab")
        self.proc = subprocess.Popen(
            ["/usr/bin/python3", str(DAEMON / "tide-daemon.py"), "--claude-dir", str(scratch / "claude"),
             "--codex-dir", str(scratch / "codex"), "--no-processes", "--no-notifications", "--no-krunner",
             "--no-tab-marks", "--no-idle", "--no-limits", "--no-models", "--no-youtrack", "--no-docker",
             "--no-activity-look", "--no-monitors", "--no-stream-shortcuts", "--workspace", str(self.ws),
             "--board-dir", str(self.board)], env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            code, out = dbus(self.address, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus.NameHasOwner", f"string:{SERVICE}", timeout=5)
            if code == 0 and "true" in out:
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"usługa nie zarejestrowała się na magistrali (dziennik: {scratch / 'daemon.log'})")

    def stop(self):
        for proc in (getattr(self, "proc", None), self.bus_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if getattr(self, "log", None):
            self.log.close()
        shutil.rmtree(self.bus_dir, ignore_errors=True)


def binary(name: str, given: str) -> str:
    found = given or shutil.which(name) or ""
    if not found and name == "claude":
        candidate = Path.home() / ".local" / "bin" / "claude"
        found = str(candidate) if candidate.exists() else ""
    return found


def run_step(tool: str, exe: str, model: str, run: Path, code_repo: Path, worktree: Path, cfg: dict,
             timeout: int) -> dict:
    step_dir = Path(str(worktree) + f".{tool}.step")
    step_id = f"pt286-{tool}-{uuid.uuid4().hex[:8]}"
    probe = step_dir / "in" / "approval_probe.py"
    config = step_dir / "in" / "trial.json"
    output = step_dir / "out" / "probe.json"
    prompt = ("Krok kontrolny piaskownicy Tide (PT-286). Uruchom narzędziem powłoki dokładnie to jedno polecenie, "
              "bez zmian i bez żadnych innych poleceń:\n\n"
              f"/usr/bin/python3 {probe} {config}\n\n"
              "Polecenie samo zapisuje swój wynik do pliku. Gdy się zakończy (także gdy wypisze odmowy), "
              "odpowiedz jednym słowem: gotowe.")
    step = agentcmd.Step(tool=tool, binary=exe, prompt=prompt, worktree=str(worktree), repo=str(code_repo),
                         step_dir=str(step_dir), step_id=step_id,
                         session_id=str(uuid.uuid4()) if tool == "claude" else "", model=model,
                         permission_mode="acceptEdits" if tool == "claude" else "",
                         tmp_root=tempfile.gettempdir())
    started = time.time()
    entry = {"tool": tool, "binary": exe, "model": model or "(domyślny)", "unit": "tide-step-" + step_id,
             "stepDir": str(step_dir)}
    try:
        bg = agentcmd.background(step)
        bg.prepare()
        shutil.copyfile(Path(__file__).with_name("approval_probe.py"), probe)
        config.write_text(json.dumps(dict(cfg, output=str(output)), ensure_ascii=False, indent=1), encoding="utf-8")
        entry["argv"] = bg.argv
        entry["writable"] = list(agentcmd.step_write_areas(step).writable())
        proc = bg.start()
        try:
            entry["exitCode"] = proc.wait(timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["systemctl", "--user", "stop", bg.unit], capture_output=True)
            entry["exitCode"] = "timeout"
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"{type(exc).__name__}: {exc}"
    entry["seconds"] = round(time.time() - started, 1)
    try:
        entry["probe"] = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        entry["probe"] = None
        entry["probeError"] = f"brak wyniku prób ({type(exc).__name__}) — agent nie uruchomił polecenia?"
        try:
            entry["logTail"] = (step_dir / "out" / "step.log").read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            pass
    return entry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="katalog wyniku (raport i katalogi kroków próbnych)")
    parser.add_argument("--tools", default="claude,codex")
    parser.add_argument("--claude", default="")
    parser.add_argument("--codex", default="")
    parser.add_argument("--claude-model", default="")
    parser.add_argument("--codex-model", default="")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("CLAUDE_CODE_TMPDIR") or Path("/proc/self/cgroup").read_text().find("tide-step-") >= 0:
        print("approval_trials: uruchom poza krokiem agenta (skrypt sam uruchamia piaskownice)", file=sys.stderr)
        return 2
    run = Path(args.out).resolve() / time.strftime("run-%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    report: dict = {"head": git("rev-parse", "HEAD", cwd=REPO).strip(), "repo": str(REPO), "run": str(run)}
    ok = True

    if not args.skip_tests:
        tests = subprocess.run(["/usr/bin/python3", "-m", "unittest", "daemon.tests.test_board_dbus",
                                "daemon.tests.test_board_step_approval"], cwd=REPO, capture_output=True, text=True)
        report["unitTests"] = {"returncode": tests.returncode, "tail": (tests.stdout + tests.stderr)[-2500:]}
        ok &= tests.returncode == 0

    state_home = Path.home() / ".local" / "state" / "tide-pt286-trials"
    shutil.rmtree(state_home, ignore_errors=True)
    trial_store = state_home / "tide" / contract_approval.FILE_NAME
    real_store = Path(contract_approval.state_path())
    real_before = sha(str(real_store))
    service = Service(run, state_home)
    try:
        code_repo = service.ws / "trialproj"
        code_repo.mkdir(parents=True)
        git("init", "-q", "-b", "master", str(code_repo))
        (code_repo / "README.md").write_text("próba\n", encoding="utf-8")
        git("-C", str(code_repo), "add", "README.md")
        git("-C", str(code_repo), "commit", "-qm", "init")
        worktree = service.ws / "trialproj-worktrees" / "pt-1"
        git("-C", str(code_repo), "worktree", "add", "-q", "-b", "ticket/pt-1", str(worktree))
        created = board_call(service.address, "UpsertProject", json.dumps({"name": "Trial", "repo": str(code_repo)}))
        slug = created["slug"]
        approved = board_call(service.address, "UpsertProject", json.dumps({"slug": slug, "tests": CONTRACT}))
        report["setup"] = {"create": created, "approve": approved}
        if not approved.get("testsApproved"):
            raise RuntimeError(f"zatwierdzenie z poza kroku nie przeszło: {approved}")
        box = service.board / "projects" / slug / "box.yaml"
        before = {"trialStore": sha(str(trial_store)), "box": sha(str(box)),
                  "boardHead": git("-C", str(service.board), "rev-parse", "HEAD").strip(), "realStore": real_before}
        report["before"] = before
        uid = os.getuid()
        if before["trialStore"] is None or before["box"] is None:
            raise RuntimeError("po zatwierdzeniu brak próbnego magazynu odcisków albo box.yaml")
        cfg = {"trialStore": str(trial_store), "realStore": str(real_store), "realStoreExists": real_before is not None,
               "boardBox": str(box),
               "boardKey": contract_approval.board_key(str(service.board)), "slug": slug,
               "evilContract": EVIL, "evilFingerprint": contract_approval.fingerprint(EVIL),
               "serviceBus": service.address, "sessionBus": f"unix:path=/run/user/{uid}/bus"}
        report["steps"] = []
        for tool in [t.strip() for t in args.tools.split(",") if t.strip()]:
            exe = binary(tool, getattr(args, tool))
            if not exe:
                report["steps"].append({"tool": tool, "error": f"brak programu {tool}"})
                ok = False
                continue
            entry = run_step(tool, exe, getattr(args, f"{tool}_model"), run, code_repo, worktree, cfg, args.timeout)
            report["steps"].append(entry)
            ok &= attempts_ok((entry.get("probe") or {}).get("results") or [])
        time.sleep(3)   # usługa: ewentualny „sync external changes” po zmianie z zewnątrz
        after = {"trialStore": sha(str(trial_store)), "box": sha(str(box)),
                 "boardHead": git("-C", str(service.board), "rev-parse", "HEAD").strip(),
                 "realStore": sha(str(real_store))}
        report["after"] = after
        ok &= after == before
        project = {p["slug"]: p for p in board_call(service.address, "Snapshot")["projects"]}[slug]
        report["projectAfter"] = {"tests": project["tests"], "testsApproved": project["testsApproved"]}
        ok &= project["tests"] == CONTRACT and project["testsApproved"] is True
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        ok = False
    finally:
        service.stop()
        shutil.rmtree(state_home, ignore_errors=True)
    report["ok"] = bool(ok)
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "report.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    print(f"\nRaport: {run / 'report.md'}")
    return 0 if ok else 1


def attempts_ok(results: list) -> bool:
    """Każda próba odmówiona (`denied`) albo odrzucona przez sprawdzenie pochodzenia (`refused`). Wyjątki: a7
    `skipped`, gdy prawdziwego magazynu nie ma (zapis w jego katalogu sprawdza a6), i b4 `reached` — sama
    łączność z magistralą pulpitu nie zmienia danych; zatwierdzenie z kroku przez magistralę sprawdzają b2, b3 i c3."""
    if not results:
        return False
    for item in results:
        name, outcome = item.get("name", ""), item.get("outcome")
        if outcome in OK_OUTCOMES or (name.startswith("a7") and outcome == "skipped") or \
                (name.startswith("b4") and outcome == "reached"):
            continue
        return False
    return True


def markdown(report: dict) -> str:
    lines = [f"# Próby piaskownicy PT-286 — {'OK' if report.get('ok') else 'NIEUDANE'}", "",
             f"- HEAD: `{report.get('head')}`", f"- katalog: `{report.get('run')}`"]
    if "unitTests" in report:
        lines.append(f"- testy D-Bus i ochrony (poza piaskownicą): kod {report['unitTests']['returncode']}")
    if report.get("error"):
        lines.append(f"- BŁĄD: {report['error']}")
    for step in report.get("steps", []):
        lines += ["", f"## {step['tool']} ({step.get('model', '')}, jednostka `{step.get('unit', '')}`, "
                      f"kod {step.get('exitCode', '?')}, {step.get('seconds', '?')} s)", ""]
        if step.get("error"):
            lines.append(f"- BŁĄD kroku: {step['error']}")
        probe = step.get("probe")
        if not probe:
            lines.append(f"- {step.get('probeError', 'brak wyniku prób')}")
            continue
        lines.append(f"- cgroup procesu prób: `{probe.get('cgroup', '')}`")
        lines += ["", "| Próba | Wynik | Szczegóły |", "| --- | --- | --- |"]
        for item in probe["results"]:
            detail = item["detail"].replace("|", "\\|").replace("\n", " ")[:300]
            lines.append(f"| {item['name']} | {item['outcome']} | {detail} |")
    if "before" in report:
        lines += ["", "## Dane", "", f"- przed: `{json.dumps(report['before'])}`",
                  f"- po: `{json.dumps(report.get('after'))}`",
                  f"- projekt po próbach: `{json.dumps(report.get('projectAfter'), ensure_ascii=False)}`"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
