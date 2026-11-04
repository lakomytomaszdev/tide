"""Prototypowe proxy pudełka (PT-178): scripts/box/prototype/proxy.py i skrypty prób p2/p3/p6/p7.

Proxy biegnie jako osobny proces na 127.0.0.1 z portem 0 (port czytamy z pierwszego wiersza wyjścia); „internet”
to lokalny serwer HTTP w wątku. Lista domen zawiera `localhost`, a porty CONNECT/HTTP wskazują serwer testowy —
żadne żądanie nie wychodzi poza maszynę.
"""
import datetime as dt
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "scripts" / "box" / "prototype"
PROXY = PROTO / "proxy.py"
TOKEN = "sk-ant-oat01-PRAWDZIWY-TOKEN-HOSTA"
CHATGPT_TOKEN = "eyJ-PRAWDZIWY-CHATGPT"


class Upstream(http.server.ThreadingHTTPServer):
    """Serwer testowy: zapisuje nagłówki każdego żądania i odpowiada „ok <ścieżka>”."""

    daemon_threads = True

    def __init__(self):
        self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(handler):
                length = int(handler.headers.get("Content-Length") or 0)
                body = handler.rfile.read(length) if length else b""
                self.seen.append({"path": handler.path, "headers": {k.lower(): v for k, v in handler.headers.items()},
                                  "body": body})
                data = f"ok {handler.path}".encode()
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)

            do_GET = do_POST = _answer

        super().__init__(("127.0.0.1", 0), Handler)
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()


class ProxyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tide-proxy-"))
        self.domains = self.tmp / "domains.txt"
        self.deny = self.tmp / "deny.log"
        self.access = self.tmp / "access.log"
        self.upstream = Upstream()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.upstream.shutdown)
        self.proc = None

    def start(self, *extra, domains="localhost\n", listen="127.0.0.1"):
        self.domains.write_text(domains)
        port = str(self.upstream.port)
        self.proc = subprocess.Popen(
            [sys.executable, str(PROXY), "--listen", listen, "--port", "0", "--domains", str(self.domains),
             "--deny-log", str(self.deny), "--access-log", str(self.access),
             "--connect-ports", port, "--http-ports", port, *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        line = self.proc.stdout.readline()
        match = re.search(r"słucha na ([0-9.]+):(\d+)", line)
        self.assertIsNotNone(match, line + self.proc.stderr.read() if self.proc.poll() is not None else line)
        self.port = int(match.group(2))
        return match.group(1)

    def stop(self):
        if self.proc and not hasattr(self, "output"):
            if self.proc.poll() is None:
                self.proc.terminate()
            out, err = self.proc.communicate(timeout=10)
            self.output = out + err

    def request(self, data: bytes, host="127.0.0.1") -> bytes:
        with socket.create_connection((host, self.port), timeout=10) as s:
            s.sendall(data)
            chunks = []
            while chunk := s.recv(65536):
                chunks.append(chunk)
            return b"".join(chunks)

    def connect(self, target: str):
        """CONNECT, potem GET przez tunel; zwraca (odpowiedź na CONNECT, odpowiedź z tunelu)."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        self.addCleanup(s.close)
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(4096)
            if not chunk:
                break
            head += chunk
        if not head.startswith(b"HTTP/1.1 200"):
            return head, b""
        s.sendall(b"GET /przez-tunel HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        body = b""
        while chunk := s.recv(65536):
            body += chunk
        return head, body

    def rows(self, path: Path):
        return [line.split("\t") for line in path.read_text().splitlines()] if path.exists() else []


class ConnectTest(ProxyCase):
    def test_connect_to_listed_domain_tunnels_to_the_local_server(self):
        self.start()
        head, body = self.connect(f"localhost:{self.upstream.port}")
        self.assertTrue(head.startswith(b"HTTP/1.1 200"), head)
        self.assertIn(b"ok /przez-tunel", body)
        self.assertEqual(self.rows(self.deny), [])
        self.assertEqual([r[2:] for r in self.rows(self.access)],
                         [["CONNECT", f"localhost:{self.upstream.port}", "200"]])

    def test_connect_outside_the_list_is_403_with_time_and_domain_in_the_deny_log(self):
        self.start()
        head, _ = self.connect(f"registry.npmjs.org:{self.upstream.port}")
        self.assertTrue(head.startswith(b"HTTP/1.1 403"), head)
        rows = self.rows(self.deny)
        self.assertEqual(len(rows), 1)
        when, domain, port, client, method = rows[0]
        self.assertEqual((domain, port, client, method),
                         ("registry.npmjs.org", str(self.upstream.port), "127.0.0.1", "CONNECT"))
        self.assertLess(abs(dt.datetime.fromisoformat(when) - dt.datetime.now().astimezone()),
                        dt.timedelta(minutes=1))
        self.assertEqual(self.upstream.seen, [])

    def test_listed_domain_on_a_port_outside_connect_ports_is_denied(self):
        self.start()
        head, _ = self.connect("localhost:22")
        self.assertTrue(head.startswith(b"HTTP/1.1 403"), head)
        self.assertEqual([r[1:3] for r in self.rows(self.deny)], [["localhost", "22"]])

    def test_list_file_is_reread_after_change_and_wildcards_match_subdomains(self):
        self.start(domains="")
        self.assertTrue(self.connect(f"localhost:{self.upstream.port}")[0].startswith(b"HTTP/1.1 403"))
        time.sleep(0.05)
        self.domains.write_text("# komentarz\n*.example.org\nlocalhost  # pudełko\n")
        self.assertTrue(self.connect(f"localhost:{self.upstream.port}")[0].startswith(b"HTTP/1.1 200"))
        self.assertTrue(self.connect(f"example.org:{self.upstream.port}")[0].startswith(b"HTTP/1.1 403"))


class ListenTest(ProxyCase):
    def test_listens_only_on_the_given_address(self):
        self.start()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.2", self.port), timeout=2).close()
        out = subprocess.run(["ss", "-Hltn", f"sport = :{self.port}"], capture_output=True, text=True)
        if out.returncode == 0:
            self.assertEqual(re.findall(r"\s(\S+):%d\s" % self.port, out.stdout), ["127.0.0.1"])

    def test_wildcard_listen_addresses_are_refused(self):
        for addr in ("0.0.0.0", "::", "localhost"):
            run = subprocess.run([sys.executable, str(PROXY), "--listen", addr, "--domains", "x", "--deny-log", "y"],
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 2, addr)
            self.assertIn("--listen", run.stderr)


class PlainHttpTest(ProxyCase):
    def test_plain_http_to_listed_domain_is_forwarded_and_others_denied(self):
        self.start()
        port = self.upstream.port
        ok = self.request(f"GET http://localhost:{port}/ubuntu/dists HTTP/1.1\r\nHost: localhost:{port}\r\n"
                          f"Proxy-Authorization: Basic eA==\r\n\r\n".encode())
        self.assertTrue(ok.startswith(b"HTTP/1.0 200") or ok.startswith(b"HTTP/1.1 200"), ok)
        self.assertIn(b"Connection: close", ok)
        self.assertTrue(ok.endswith(b"ok /ubuntu/dists"))
        self.assertNotIn("proxy-authorization", self.upstream.seen[0]["headers"])
        bad = self.request(f"GET http://archive.ubuntu.com:{port}/ HTTP/1.1\r\nHost: archive.ubuntu.com\r\n\r\n".encode())
        self.assertTrue(bad.startswith(b"HTTP/1.1 403"), bad)
        self.assertEqual([r[1:] for r in self.rows(self.deny)], [["archive.ubuntu.com", str(port), "127.0.0.1", "GET"]])


class InjectTest(ProxyCase):
    def creds(self, name, content):
        path = self.tmp / name
        path.write_text(content)
        return path

    def test_anthropic_route_replaces_box_credentials_with_host_token_and_never_logs_it(self):
        creds = self.creds("credentials.json", json.dumps(
            {"claudeAiOauth": {"accessToken": TOKEN, "refreshToken": "sk-ant-ort01-NIE-WOLNO", "expiresAt": 1}}))
        self.start("--inject", f"anthropic={creds}", "--upstream", f"anthropic=http://127.0.0.1:{self.upstream.port}")
        body = b'{"model":"x"}'
        answer = self.request(b"POST /anthropic/v1/messages?beta=true HTTP/1.1\r\nHost: 10.0.0.1:18080\r\n"
                              b"Authorization: Bearer tide-box-dummy\r\nx-api-key: tide-box-dummy\r\n"
                              b"anthropic-beta: claude-code-20250219\r\nContent-Type: application/json\r\n"
                              + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.assertIn(b"200", answer.split(b"\r\n", 1)[0])
        seen = self.upstream.seen[0]
        self.assertEqual(seen["path"], "/v1/messages?beta=true")
        self.assertEqual(seen["body"], body)
        self.assertEqual(seen["headers"]["authorization"], f"Bearer {TOKEN}")
        self.assertNotIn("x-api-key", seen["headers"])
        self.assertEqual(seen["headers"]["anthropic-beta"], "claude-code-20250219,oauth-2025-04-20")
        self.assertEqual(seen["headers"]["host"], f"127.0.0.1:{self.upstream.port}")
        self.assertEqual([r[2:] for r in self.rows(self.access)], [["INJECT POST", "/anthropic/v1/messages", "200"]])
        self.stop()
        for text in (self.access.read_text(), self.output, answer.decode("latin-1")):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("NIE-WOLNO", text)
            self.assertNotIn("tide-box-dummy", text)

    def test_chatgpt_route_injects_access_token_and_account_id(self):
        auth = self.creds("auth.json", json.dumps(
            {"tokens": {"access_token": CHATGPT_TOKEN, "account_id": "acct-hosta", "refresh_token": "rt"}}))
        self.start("--inject", f"chatgpt={auth}", "--upstream", f"chatgpt=http://127.0.0.1:{self.upstream.port}/")
        self.request(b"POST /chatgpt/backend-api/codex/responses HTTP/1.1\r\nHost: b\r\n"
                     b"Authorization: Bearer atrapa\r\nchatgpt-account-id: atrapa\r\nContent-Length: 0\r\n\r\n")
        headers = self.upstream.seen[0]["headers"]
        self.assertEqual(self.upstream.seen[0]["path"], "/backend-api/codex/responses")
        self.assertEqual(headers["authorization"], f"Bearer {CHATGPT_TOKEN}")
        self.assertEqual(headers["chatgpt-account-id"], "acct-hosta")
        self.assertNotIn("anthropic-beta", headers)

    def test_unknown_route_is_404_and_route_without_inject_is_not_forwarded(self):
        self.start()
        answer = self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nHost: b\r\nContent-Length: 0\r\n\r\n")
        self.assertTrue(answer.startswith(b"HTTP/1.1 404"), answer)
        self.assertEqual(self.upstream.seen, [])

    def test_token_file_inside_the_repository_is_refused(self):
        run = subprocess.run([sys.executable, str(PROXY), "--listen", "127.0.0.1", "--domains", "x",
                              "--deny-log", str(self.deny), "--inject", f"anthropic={ROOT / 'README.md'}"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 2)
        self.assertIn("poza repozytorium", run.stderr)


class ScriptsTest(unittest.TestCase):
    """Skrypty prób: składnia, wspólny dziennik i sprzątanie."""

    SCRIPTS = ("p2.sh", "p3.sh", "p6.sh", "p7.sh", "all.sh", "cleanup.sh", "common.sh")

    def test_scripts_parse(self):
        for name in self.SCRIPTS:
            run = subprocess.run(["bash", "-n", str(PROTO / name)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, f"{name}: {run.stderr}")

    def test_trial_scripts_log_to_docs_box_prototype_through_common(self):
        common = (PROTO / "common.sh").read_text()
        self.assertIn('docs/box-prototype', common)
        self.assertIn("tee", common)
        for name in ("p2.sh", "p3.sh", "p6.sh", "p7.sh"):
            text = (PROTO / name).read_text()
            trial = name[:-3]
            self.assertIn('source "$(dirname "${BASH_SOURCE[0]}")/common.sh"', text, name)
            self.assertRegex(text, rf"proto_log {trial}\b", name)

    def test_help_does_not_touch_incus(self):
        env = {**os.environ, "PATH": "/nonexistent"}
        for name in ("p2.sh", "p3.sh", "p6.sh", "p7.sh", "all.sh", "cleanup.sh"):
            run =subprocess.run(["/bin/bash", str(PROTO / name), "--help"], capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, f"{name}: {run.stderr}")
            self.assertIn("Użycie", run.stdout, name)

    def test_p6_finds_apt_sources_in_both_formats(self):
        # images:ubuntu/24.04 ma klasyczny /etc/apt/sources.list, nie deb822 ubuntu.sources (przebieg 2026-11-04).
        text = (PROTO / "p6.sh").read_text()
        self.assertIn("/etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list", text)
        self.assertNotIn("SOURCES=/etc/apt/sources.list.d/ubuntu.sources\n", text)

    def test_cleanup_removes_proxy_and_containers(self):
        text = (PROTO / "cleanup.sh").read_text()
        self.assertIn("incus delete --force", text)
        self.assertIn("proxy.pid", text)


if __name__ == "__main__":
    unittest.main()
