"""Skrypty prób pudełka (PT-164): scripts/box/prototype/*.sh z atrapami incus, docker, nft i sudo.

Skrypty kopiujemy do katalogu tymczasowego (logi idą do <kopia>/docs/box-prototype, nie do repozytorium),
a host udaje fake_box_proto.py. Sprawdzamy kształt logu (nagłówek, kod wyjścia i czas każdego polecenia),
to, że skrypty nie czytają stdin, i chwilowe reguły nftables P-1: wyłącznie trzy reguły z komentarzem
tide-proto w tabeli inet tide-box, usuwane także po błędzie w środku próby.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "scripts" / "box" / "prototype"
FAKE = Path(__file__).resolve().parent / "fake_box_proto.py"
FAKE_NAMES = ("incus", "nft", "docker", "sudo", "ip", "systemctl", "systemd-run", "npm", "node")
SCRIPTS = ("p1", "p4", "p5", "p9", "measure")
BOX_IP = "10.99.0.5"

EXPECTED_RULES = [
    ("forward", f'iifname "incusbr0" ip saddr {BOX_IP} accept comment "tide-proto"'),
    ("forward", f'oifname "incusbr0" ip daddr {BOX_IP} ct state established,related accept comment "tide-proto"'),
    ("input", f'iifname "incusbr0" ip saddr {BOX_IP} meta l4proto {{ tcp, udp }} th dport 53 accept '
              'comment "tide-proto"'),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class PrototypeScriptsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tide-proto-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.copy = self.tmp / "repo"
        shutil.copytree(PROTO, self.copy / "scripts" / "box" / "prototype")
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in FAKE_NAMES:
            wrapper = self.bin / name
            wrapper.write_text(f'#!/bin/sh\nexec python3 "{FAKE}" {name} "$@"\n')
            wrapper.chmod(0o755)
        self.state = {"box": False, "status": "STOPPED", "nft_rules": [], "next_handle": 20,
                      "port": free_port(), "bridge": "127.0.0.1/24", "box_ip": BOX_IP}
        (self.fake / "state.json").write_text(json.dumps(self.state))
        # LegalApp (P-1) i projekt node (P-4) jako małe repozytoria git.
        self.la = self.tmp / "legalapp"
        self.la.mkdir()
        (self.la / "global.json").write_text('{"sdk": {"version": "8.0.100"}}\n')
        git("init", "-q", cwd=self.la)
        git("add", ".", cwd=self.la)
        git("commit", "-q", "-m", "init", cwd=self.la)
        self.proj = self.tmp / "nodeproj"
        (self.proj / "web").mkdir(parents=True)
        (self.proj / "web" / "package-lock.json").write_text("{}\n")
        git("init", "-q", cwd=self.proj)
        git("add", ".", cwd=self.proj)
        git("commit", "-q", "-m", "init", cwd=self.proj)
        (self.tmp / "nodeproj-worktrees").mkdir()

    def run_script(self, name, **env):
        environ = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "TIDE_PROTO_FAKE_DIR": str(self.fake),
            "PROTO_SETTLE": "0",
            "PROTO_WAIT": "5",
            "PROTO_LEGALAPP": str(self.la),
            "PROTO_NODE_PROJECT": str(self.proj),
            "PROTO_PORT": str(self.state["port"]),
            **env,
        }
        environ.pop("SUDO_ASKPASS", None)
        # stdin zostaje otwartym potokiem: skrypt, który by z niego czytał, zawiśnie i test padnie na limicie.
        output = self.tmp / f"{name}.out"
        with open(output, "wb") as fh:
            proc = subprocess.Popen(["bash", str(self.copy / "scripts" / "box" / "prototype" / f"{name}.sh")],
                                    stdin=subprocess.PIPE, stdout=fh, stderr=subprocess.STDOUT,
                                    env=environ, start_new_session=True)
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 9)
                proc.wait()
                self.fail(f"{name}.sh nie skończył się w 120 s (czyta stdin?):\n{output.read_text()}")
            finally:
                proc.stdin.close()
        out = output.read_text()
        log = self.copy / "docs" / "box-prototype" / f"{name}.log"
        self.assertTrue(log.exists(), out)
        return proc.returncode, log.read_text(), out

    def state_now(self):
        return json.loads((self.fake / "state.json").read_text())

    def calls(self):
        lines = (self.fake / "calls.jsonl").read_text().splitlines()
        return [json.loads(line) for line in lines]

    def assert_log_shape(self, log, title):
        lines = log.splitlines()
        self.assertTrue(lines[0].startswith(f"# {title}"), lines[0])
        commands = [i for i, line in enumerate(lines) if line.startswith("$ ")]
        self.assertGreater(len(commands), 3, log)
        for i in commands:
            self.assertRegex(lines[i + 1], r"^  \[.+\] kod wyjścia: \d+, czas: \d+\.\d{3} s$", lines[i])
        self.assertIn("## Podsumowanie", log)
        self.assertRegex(log, r"(?m)^WYNIK \S+: (ok|fail|skip|info) — ")

    def test_every_script_logs_header_exit_code_and_time_without_reading_stdin(self):
        titles = {"p1": "P-1", "p4": "P-4", "p5": "P-5", "p9": "P-9", "measure": "Pomiary", "cleanup": "Sprzątanie"}
        for name in (*SCRIPTS, "cleanup"):
            with self.subTest(script=name):
                _, log, _ = self.run_script(name)
                self.assert_log_shape(log, titles[name])
                self.assertNotIn("WYNIK przerwanie", log)

    def test_p1_by_default_goes_through_the_temporary_proxy_without_touching_nftables(self):
        code, log, _ = self.run_script("p1")
        self.assertEqual(code, 0, log)
        self.assertEqual([c for c in self.calls() if c[0] in ("nft", "sudo")], [])
        self.assertIn("chwilowe proxy scripts/box/prototype/proxy.py", log)
        self.assertIn("WYNIK siec-proxy: info", log)
        self.assertIn("https_proxy=http://127.0.0.1:", log)
        self.assertIn("dockerd w kontenerze przez proxy", log)
        # Proxy zamknięte: port znów wolny.
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.state["port"]))

    def test_p1_tries_crun_and_the_oldest_runc_when_docker_fails_without_touching_the_project(self):
        code, log, _ = self.run_script("p1", FAKE_DOCKER_FAIL="1")
        self.assertNotEqual(code, 0)
        for runtime in ("runc", "crun", "runc-stary"):
            self.assertIn(f"WYNIK docker-{runtime}: fail", log)
        self.assertIn("apt-get install -y --allow-downgrades runc=1.1.12-0ubuntu3", log)
        self.assertIn("[dockerd: domyślny runtime crun] kod wyjścia: 0", log)
        self.assertIn("default-runtime", log)
        self.assertIn("WYNIK dotnet-test: skip", log)
        self.assertEqual([c for c in self.calls() if c[:2] == ["incus", "project"] or c[0] == "sudo"], [])

    def test_p1_stops_at_the_first_working_runtime(self):
        code, log, _ = self.run_script("p1")
        self.assertEqual(code, 0, log)
        self.assertIn("WYNIK docker-runc: ok", log)
        self.assertNotIn("docker-crun", log)
        self.assertIn("runtime runc, bez intercept", log)

    def test_p1_inserts_exactly_the_three_tide_proto_rules_and_removes_them(self):
        code, log, out = self.run_script("p1", PROTO_NET="rules")
        self.assertEqual(code, 0, log)
        state = self.state_now()
        self.assertEqual([(r["chain"], r["rule"]) for r in state["inserted"]], EXPECTED_RULES)
        self.assertEqual(state.get("foreign", []), [])
        self.assertEqual(state["nft_rules"], [])
        deletes = [c for c in self.calls() if c[:3] == ["nft", "delete", "rule"]]
        self.assertEqual(len(deletes), 3)
        for c in deletes:
            self.assertEqual(c[3:5], ["inet", "tide-box"])
            self.assertEqual(c[6], "handle")
        self.assertIn("WYNIK nft-przed-po: ok", log)
        self.assertIn("(przed regułami chwilowymi)", log)
        self.assertIn("(po usunięciu reguł chwilowych)", log)
        self.assertIn("WYNIK hello-world: ok", log)
        self.assertIn("WYNIK dotnet-test: ok", log)
        # Zmiany nftables (insert, delete) wyłącznie w inet tide-box; poza nią tylko odczyt (diagnoza FORWARD).
        for c in self.calls():
            if c[0] == "nft" and "list" not in c:
                self.assertEqual(c[3:5], ["inet", "tide-box"], c)

    def test_p1_removes_rules_when_docker_run_fails(self):
        code, log, _ = self.run_script("p1", PROTO_NET="rules", FAKE_DOCKER_FAIL="1")
        self.assertNotEqual(code, 0)
        self.assertRegex(log, r"docker run --rm hello-world \(runc\)\] kod wyjścia: 125")
        self.assertIn("WYNIK hello-world: fail", log)
        state = self.state_now()
        self.assertEqual(len(state["inserted"]), 3)
        self.assertEqual(state["nft_rules"], [])
        self.assertIn("WYNIK nft-przed-po: ok", log)

    def test_p1_removes_rules_when_it_dies_in_the_middle(self):
        code, log, _ = self.run_script("p1", PROTO_NET="rules", FAKE_FAIL="dotnet-install")
        self.assertEqual(code, 2, log)
        self.assertIn("WYNIK warunki: fail", log)
        state = self.state_now()
        self.assertEqual(len(state["inserted"]), 3)
        self.assertEqual(state["nft_rules"], [])
        self.assertIn("WYNIK nft-przed-po: ok", log)

    def test_p1_removes_rules_when_killed(self):
        # Sygnał w trakcie próby: pułapka EXIT też sprząta. Atrapa incus przy docker run hello-world wysyła
        # SIGTERM do skryptu (PID z kopii lib.sh).
        (self.bin / "incus").write_text(
            '#!/bin/sh\ncase "$*" in *hello-world*) kill -TERM "$TIDE_PROTO_SCRIPT_PID" ;; esac\n'
            f'exec python3 "{FAKE}" incus "$@"\n')
        lib = self.copy / "scripts" / "box" / "prototype" / "lib.sh"
        lib.write_text(lib.read_text().replace("set -euo pipefail\n",
                                               "set -euo pipefail\nexport TIDE_PROTO_SCRIPT_PID=$$\n", 1))
        code, log, _ = self.run_script("p1", PROTO_NET="rules")
        self.assertEqual(code, 130, log)
        self.assertEqual(self.state_now()["nft_rules"], [])
        self.assertIn("WYNIK nft-przed-po: ok", log)
        self.assertIn("WYNIK przerwanie: fail", log)

    def test_p4_cleans_up_its_worktree_and_branch(self):
        self.run_script("p4")
        self.assertFalse((self.tmp / "nodeproj-worktrees" / "proto-k-38").exists())
        branches = subprocess.run(["git", "branch", "--list", "proto/k-38"], cwd=self.proj,
                                  capture_output=True, text=True).stdout
        self.assertEqual(branches.strip(), "")
        shifted = [c for c in self.calls() if c[:4] == ["incus", "config", "device", "add"]]
        self.assertTrue(shifted)
        for c in shifted:
            self.assertIn("shift=true", c)
            self.assertEqual(c[c.index("disk") + 1].removeprefix("source="),
                             c[c.index("disk") + 2].removeprefix("path="))
        ro = {c[5] for c in shifted if "readonly=true" in c}
        self.assertEqual(ro, {"proto-hooks", "proto-config", "proto-info"})
        rw = {c[5] for c in shifted if "readonly=true" not in c}
        self.assertEqual(rw, {"proto-git", "proto-wt"})
        removed = {c[5] for c in self.calls() if c[:4] == ["incus", "config", "device", "remove"]}
        self.assertEqual(removed, ro | rw)
        self.assertEqual(self.state_now()["nft_rules"], [])

    def test_p9_checks_every_variant_against_the_death_of_the_incus_exec_client(self):
        self.run_script("p9")
        log = (self.copy / "docs" / "box-prototype" / "p9.log").read_text()
        for variant in ("scope", "usługa-user", "usługa-root"):
            self.assertIn(f"WYNIK przezywa-klienta-{variant}:", log)
        stops = [c for c in self.calls() if c[:3] == ["systemctl", "--user", "stop"]]
        self.assertGreaterEqual(len(stops), 3)

    def test_note_has_a_row_per_trial_and_room_for_pt_178(self):
        note = (ROOT / "docs" / "box-prototype.md").read_text()
        for trial in ("P-1", "P-2", "P-3", "P-4", "P-5", "P-6", "P-7", "P-9"):
            self.assertRegex(note, rf"(?m)^\| {trial} \|", trial)
        for name in ("p1", "p4", "p5", "p9", "measure"):
            self.assertIn(f"bash scripts/box/prototype/{name}.sh", note)
        decisions = (ROOT / "docs" / "box-decisions.md").read_text()
        self.assertIn("| Etykieta | Data | Ticket | Treść | Uzasadnienie |", decisions)

    def test_scripts_have_strict_mode_and_never_use_privileged_containers(self):
        for path in sorted(PROTO.glob("*.sh")):
            with self.subTest(script=path.name):
                text = path.read_text()
                self.assertIn("set -euo pipefail", text)
                self.assertNotRegex(text, r"security\.privileged=true")
                self.assertNotRegex(text, r"\.local/share|\.config/")
                # Brak pytań: read tylko z napisu (<<<), nigdy z terminala ani stdin.
                for line in text.splitlines():
                    if re.search(r"\bread\b", line) and not line.strip().startswith("#"):
                        self.assertTrue("<<<" in line or "< <(" in line or line.rstrip().endswith("do"), line)
        for name in (*SCRIPTS, "cleanup"):
            self.assertTrue((PROTO / f"{name}.sh").exists(), name)


class TemporaryProxyTest(unittest.TestCase):
    """proxy.py: zwykłe HTTP z pełnym adresem (apt) i tunel CONNECT (HTTPS), dziennik celów."""

    def setUp(self):
        import http.server
        import threading
        tmp = Path(tempfile.mkdtemp(prefix="tide-proto-proxy-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "www").mkdir()
        (tmp / "www" / "hello.txt").write_text("cześć z serwera\n")
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp / "www"), **k)  # noqa: E731
        http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
        self.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.origin.serve_forever, daemon=True).start()
        self.addCleanup(self.origin.shutdown)
        self.journal = tmp / "journal"
        self.port = free_port()
        self.proxy = subprocess.Popen(["python3", str(PROTO / "proxy.py"), "127.0.0.1", str(self.port),
                                       str(self.journal)])
        self.addCleanup(self.proxy.wait)
        self.addCleanup(self.proxy.terminate)
        for _ in range(100):
            if self.journal.exists() and "ready" in self.journal.read_text():
                break
            import time
            time.sleep(0.05)

    def test_plain_http_with_absolute_url(self):
        # Surowe gniazdo zamiast urllib: urllib omija proxy dla adresów z NO_PROXY środowiska testu.
        origin_port = self.origin.server_address[1]
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(f"GET http://127.0.0.1:{origin_port}/hello.txt HTTP/1.1\r\nHost: x\r\n"
                      "Proxy-Connection: keep-alive\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(4096):
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 200"), data[:100])
        self.assertIn("cześć z serwera".encode(), data)
        self.assertIn(f"GET 127.0.0.1:{origin_port}", self.journal.read_text())

    def test_connect_tunnel(self):
        origin_port = self.origin.server_address[1]
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            reply = s.recv(4096)
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
            s.sendall(b"GET /hello.txt HTTP/1.0\r\nHost: x\r\n\r\n")
            data = b""
            while chunk := s.recv(4096):
                data += chunk
        self.assertIn("cześć z serwera".encode(), data)
        self.assertIn(f"CONNECT 127.0.0.1:{origin_port}", self.journal.read_text())


if __name__ == "__main__":
    unittest.main()
