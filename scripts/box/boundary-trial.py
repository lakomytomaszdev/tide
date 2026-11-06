#!/usr/bin/python3
"""PT-191: dowód CLI/Incusa i pełne testy, do jawnego uruchomienia poza piaskownicą kroku.

Domyślnie nie buduje ani nie zatwierdza profili. --prepare-missing odtwarza brakujące środowisko próby PT-192
z profilem testowym tego ticketu; istniejące pudełko lub profil zachowuje. Nie tworzy karty testowej.
--enable-snapshots ustawia jedną właściwość istniejącego projektu zgodnie z instalatorem PT-182 (sudo -n).
Brak uwierzytelnienia sudo zapisuje jako błąd i podaje polecenie administratora, bez oczekiwania na hasło.
--retry-failed-build ponawia tylko nieudaną budowę zgodną z bieżącym profilem, bez kroków.
Przy działającym proxy innego procesu CLI odmawia zgodnie z kontraktem ensure_proxy.
Logi pozostają w docs/box-prototype/pt191-<czas>/; nie zbieramy argv obcych procesów do pliku.
Próba współdzielonego repozytorium wymaga osłony zapisów planu także w zainstalowanym daemonie.
"""
import argparse
import ast
import datetime
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))
from tide_daemon.box import netenv, profile as bp, settings as bsettings, state as bs

OUT = None
failures = []


def run(name, argv, *, ok_codes=(0,), **kwargs):
    print(f"PRÓBA {name}: {argv}", flush=True)
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, errors="replace", **kwargs)
    (OUT / (name + ".stdout.log")).write_text(result.stdout)
    (OUT / (name + ".stderr.log")).write_text(result.stderr)
    print(f"WYNIK {name}: {result.returncode}", flush=True)
    if result.returncode not in ok_codes:
        failures.append(name)
    return result


def check(name, ok, detail):
    print(f"WYNIK {name}: {'ok' if ok else 'fail'} — {detail}", flush=True)
    if not ok:
        failures.append(name)


def deployed_plan_guard_ready(path=None):
    """Odmowa dla potwierdzonej kolizji wersji PT-170; nie dowodzi wersji już załadowanego procesu.

    Zainstalowany kod bez osłony commit_plan cofał legalne commity innych kart podczas czerwonej listy.
    Nie wystarcza sama obecność nazwy host_operation gdzie indziej w pliku.
    """
    path = path or Path.home() / ".local/share/tide/daemon/tide_daemon/cardflow/gitops.py"
    ready = False
    try:
        tree = ast.parse(Path(path).read_text())
        for method in ast.walk(tree):
            if not isinstance(method, ast.FunctionDef) or method.name != "commit_plan":
                continue
            for block in ast.walk(method):
                if not isinstance(block, ast.With):
                    continue
                guarded = any(isinstance(item.context_expr, ast.Call)
                              and isinstance(item.context_expr.func, ast.Attribute)
                              and item.context_expr.func.attr == "host_operation" for item in block.items)
                commits = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                              and node.func.attr == "run" and node.args
                              and isinstance(node.args[0], ast.Constant) and node.args[0].value == "commit"
                              for statement in block.body for node in ast.walk(statement))
                ready |= guarded and commits
    except (OSError, SyntaxError, UnicodeError):
        pass
    check("host-plan-guard", ready,
          f"{path}: wymagany commit_plan pod host_operation; przed próbą także działający daemon musi używać tej wersji")
    return ready


def enable_snapshots():
    project = bsettings.load().project
    before = run("snapshots-before", ["incus", "project", "get", project, "restricted.snapshots"])
    if before.returncode:
        return False  # Nie zmieniamy nieznanego stanu.
    if before.stdout.strip() == "allow":
        return True
    command = ["incus", "project", "set", project, "restricted.snapshots=allow"]
    # Próba w tle nie ma odbiorcy okna hasła. Brak zgody kończy tę operację od razu.
    changed = run("snapshots-enable", ["sudo", "-n", *command])
    if changed.returncode:
        print("Nie potwierdzono zmiany polityki migawek. Polecenie administratora w terminalu: "
              + shlex.join(["sudo", *command]), flush=True)
        return False
    after = run("snapshots-after", ["incus", "project", "get", project, "restricted.snapshots"])
    ok = after.returncode == 0 and after.stdout.strip() == "allow"
    check("snapshots-policy", ok, f"{project}: restricted.snapshots=allow (zgodnie z instalatorem PT-182)")
    return ok


def prepare_missing(*, retry_failed=False):
    codes = (0, 3) if retry_failed else (0,)
    result = run("prepare-status", [sys.executable, "daemon/tide.py", "box", "status", "plasma-tide", "--json"],
                 ok_codes=codes)
    if result.returncode not in codes:
        return
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or not isinstance(state.get("exists"), bool) or state.get("error"):
        raise ValueError("nie potwierdzono stanu pudełka — bez zmiany profilu i budowy")
    if state.get("exists"):
        if retry_failed and state.get("state") == bs.FAILED:
            if (state.get("steps") or not state.get("recipeHash") or state.get("diff")
                    or state.get("recipeHash") != state.get("profileHash")):
                raise ValueError("odmowa ponowienia: kroki lub niezgodny profil nieudanej budowy")
            run("retry-failed-build", [sys.executable, "daemon/tide.py", "box", "build", "plasma-tide"])
            return
        print("Przygotowanie: istniejące pudełko zachowane, bez przebudowy.", flush=True)
        return
    if not Path(bp.project_path("plasma-tide")).exists():
        profile = ROOT / "docs/agents/pt-191/plasma-tide-proof.box.yaml"
        approved = run("approve-proof-profile", [sys.executable, "daemon/tide.py", "box", "approve", "plasma-tide",
                                                 "--file", str(profile)])
        if approved.returncode:
            return
    else:
        print("Przygotowanie: istniejący profil zachowany, budowa z tego profilu.", flush=True)
    run("build-missing-box", [sys.executable, "daemon/tide.py", "box", "build", "plasma-tide"])


def free_address(settings):
    # Brak kompletnego odczytu dzierżaw = brak zgadywania adresu. Skrypt check.sh zapisze pominięcie.
    try:
        cidr = subprocess.run(["incus", "network", "get", settings.bridge, "ipv4.address"],
                              capture_output=True, text=True, check=True).stdout.strip()
        leases = json.loads(subprocess.run(["incus", "network", "list-leases", settings.bridge, "--format=json"],
                                           capture_output=True, text=True, check=True).stdout)
        network = ipaddress.ip_interface(cidr).network
        if network.num_addresses > 65536:
            return ""
        taken = {str(ipaddress.ip_interface(cidr).ip)} | {str(lease["address"]) for lease in leases}
        return next((str(addr) for addr in reversed(list(network.hosts())) if str(addr) not in taken), "")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return ""


def cli_proof():
    # Tożsamość sieci kroku ma losowy klucz: wyszukujemy go w ps, nie samą nazwę zmiennej ani znane ścieżki.
    script = 'import json,os,time; print(json.dumps(dict(os.environ)), flush=True); time.sleep(4)'
    argv = [sys.executable, "daemon/tide.py", "box", "run", "plasma-tide", "--worktree", str(ROOT),
            "--", "python3", "-c", script]
    started = time.monotonic()
    with (OUT / "env.stderr.log").open("w+") as err:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=err, text=True)
        folder = ""
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and proc.poll() is None:
            err.flush()
            text = (OUT / "env.stderr.log").read_text()
            match = re.search(r"^katalog kroku: (.+)$", text, re.M)
            if match:
                folder = match[1]
                break
            time.sleep(0.05)
        snapshot = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, check=True).stdout
        owner = Path(folder).name.split(".")[0] if folder else ""
        selected = [line for line in snapshot.splitlines() if owner and owner in line and "incus" in line]
        (OUT / "ps-args.log").write_text("\n".join(selected) + "\n")
        stdout, _ = proc.communicate()
    (OUT / "env.stdout.log").write_text(stdout)
    check("cli-env", proc.returncode == 0 and bool(folder),
          f"kod {proc.returncode}; katalog kroku: {'tak' if folder else 'brak'}; {time.monotonic() - started:.3f} s")
    if proc.returncode or not folder:
        return
    values = json.loads(stdout.strip())
    expected = {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "TIDE_PORT_BASE", "PORT", "E2E_PORT"}
    forbidden = {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
                 "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "DOCKER_HOST"}
    check("env-keys", expected <= values.keys() and not forbidden & values.keys(),
          "proxy, NO_PROXY, porty i brak tokenów agentów")
    bridge = values["TIDE_BOX_BRIDGE"]
    check("no-proxy", values.get("NO_PROXY") == netenv.BOX_NO_PROXY + "," + bridge, "pełna lista i adres mostka")
    url = values.get("HTTPS_PROXY", "")
    key = url.partition("http://tide:")[2].partition("@")[0]
    check("ps-no-env-values", bool(selected) and bool(key) and key not in snapshot
          and values.get("TIDE_PORT_BASE", "") not in "\n".join(selected), "klucz proxy i port poza argv")
    mode = (Path(folder) / "in" / "env").stat().st_mode & 0o777
    check("env-mode", mode == 0o600, f"in/env {mode:o}")


def main(argv=None):
    global OUT, failures
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-missing", action="store_true",
                        help="zbuduj brakujące pudełko; nie zastępuj istniejącego profilu")
    parser.add_argument("--enable-snapshots", action="store_true",
                        help="ustaw restricted.snapshots=allow w projekcie Incusa (sudo -n, bez okna hasła)")
    parser.add_argument("--retry-failed-build", action="store_true",
                        help="ponów wyłącznie nieudaną, zgodną budowę bez kroków; istniejący profil pozostaje")
    options = parser.parse_args(argv)
    OUT = ROOT / "docs" / "box-prototype" / ("pt191-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    OUT.mkdir(parents=True)
    failures = []
    print(f"PT-191: {OUT}", flush=True)
    if options.prepare_missing or options.enable_snapshots or options.retry_failed_build:
        try:
            ready = enable_snapshots() if options.enable_snapshots else True
            if ready and (options.prepare_missing or options.retry_failed_build):
                prepare_missing(retry_failed=options.retry_failed_build)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            failures.append("prepare-missing")
            print(f"BŁĄD przygotowania: {exc}", flush=True)
    run("make-test", ["make", "test"])
    status = run("box-status", [sys.executable, "daemon/tide.py", "box", "status", "plasma-tide", "--json"])
    try:
        usable = status.returncode == 0 and json.loads(status.stdout).get("usable")
    except ValueError:
        usable = False
    check("preflight", bool(usable), "wymagane istniejące pudełko plasma-tide z zatwierdzonym profilem")
    if usable and deployed_plan_guard_ready():
        try:
            cli_proof()
            settings = bsettings.load()
            spoof = free_address(settings)
            args = ["--spoof-ip", spoof] if spoof else []
            if spoof:
                cidr = subprocess.run(["incus", "network", "get", settings.bridge, "ipv4.address"],
                                      capture_output=True, text=True, check=True).stdout.strip()
                args += ["--prefix", str(ipaddress.ip_interface(cidr).network.prefixlen)]
            host_route = subprocess.run(["ip", "-j", "route", "get", "1.1.1.1"], capture_output=True, text=True)
            if host_route.returncode == 0:
                src = json.loads(host_route.stdout)[0].get("prefsrc")
                if src:
                    args += ["--host-ip", str(ipaddress.ip_address(src))]
            red = run("box-check", ["make", "box-check", "BOX=plasma-tide", "CHECK_ARGS=" + " ".join(args)])
            # Źródło z nieprzydzielonego IP nie ma polityki, więc gdy pakiet dotarł, proxy zapisuje odmowę na stderr.
            if spoof:
                check("spoof-proxy-log", not re.search(re.escape(spoof) + r": brak polityki", red.stderr),
                      "brak wpisu proxy z próbnego adresu; osobno sprawdzić wiersz próby (może być nie dotyczy)")
            print(red.stdout, flush=True)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            failures.append("proof-exception")
            print(f"BŁĄD próby: {exc}", flush=True)
    (OUT / "summary.json").write_text(json.dumps({"failures": failures, "firstAgentStep": "pending-approved-test-card"},
                                                 ensure_ascii=False, indent=2) + "\n")
    print(f"WYNIK końcowy: {'fail' if failures else 'ok'}; logi: {OUT}; pierwszy krok agenta: osobna karta", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
