"""Proxy wyjściowe pudełka (PT-168): box/proxy.py, box/settings.py, models/boxconfig.py.

Serwer proxy działa w procesie testu na 127.0.0.1 (konstruktor `ProxyService` nie sprawdza adresu — robi to
`start_service`, testowany osobno z atrapą `incus`). Połączenia CONNECT trafiają przez podstawiony `connector`
na lokalny serwer echa, Ollama to lokalna atrapa strumieniująca odpowiedź. Bez sieci, bez Incusa.
"""
import asyncio
import contextlib
import inspect
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from functools import partial

from tide_daemon import app as tide_app
from tide_daemon.box import profile as bp
from tide_daemon.box import proxy as bx
from tide_daemon.box import settings as bs
from tide_daemon.box import templates
from tide_daemon.models import boxconfig
from tide_daemon.models import config as models_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------------------ pomocnicze serwery

class EchoServer:
    """Serwer TCP odsyłający wszystko z przedrostkiem `echo:` — udaje drugą stronę tunelu CONNECT."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _serve(conn):
        with conn:
            while data := conn.recv(4096):
                conn.sendall(b"echo:" + data)

    def close(self):
        self.sock.close()


class FakeOllama:
    """Atrapa Ollamy: zapisuje żądanie i odpowiada strumieniem w dwóch fragmentach.

    Drugi fragment idzie dopiero po `release` — test sprawdza, że pierwszy dotarł do klienta wcześniej
    (proxy nie buforuje odpowiedzi do końca)."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self.release = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n")[1:]:
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value)
            while len(body) < length:
                body += conn.recv(4096)
            self.requests.append((head.decode("latin-1"), body))
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n"
                         b"Connection: close\r\n\r\n")
            first = b'{"response":"Ala","done":false}\n'
            conn.sendall(b"%x\r\n%s\r\n" % (len(first), first))
            self.release.wait(10)
            second = b'{"response":" ma kota","done":true}\n'
            conn.sendall(b"%x\r\n%s\r\n0\r\n\r\n" % (len(second), second))

    def close(self):
        self.release.set()
        self.sock.close()


def recv_until(sock, marker: bytes, limit: float = 5.0) -> bytes:
    sock.settimeout(limit)
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def recv_all(sock, limit: float = 5.0) -> bytes:
    sock.settimeout(limit)
    data = b""
    while chunk := sock.recv(4096):
        data += chunk
    return data


class ProxyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-proxy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.echo = EchoServer()
        self.addCleanup(self.echo.close)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.close)
        self.connected = []
        self.logs = []
        # Atrapa DNS: nazwa → adres. Goły adres IP „rozwiązuje się” na siebie. Adres sprawdzany jak w prawdziwym
        # łączniku (`address_allowed`), połączenie zawsze idzie do lokalnego serwera echa.
        self.resolved = {"refused.example.org": "127.0.0.1"}

        async def connector(host, port, internal):
            self.connected.append((host, port, internal))
            address = self.resolved.get(host, host if bx._ip(host) is not None else None)
            if address is not None and not bx.address_allowed(address, internal):
                raise bx.Refused(host)
            return await asyncio.open_connection("127.0.0.1", self.echo.port)

        self.proxy = bx.ProxyService("127.0.0.1", 0, ollama=("127.0.0.1", self.ollama.port),
                                     connector=connector, log=self.logs.append).start()
        self.addCleanup(self.proxy.stop)
        self.denied = os.path.join(self.tmp, "step", "proxy-denied.log")

    def policy(self, domains=("registry.npmjs.org", "*.npmjs.org"), ollama=False, **kw):
        return self.proxy.set_box_policy("127.0.0.1", list(domains), ollama, self.denied, box="tide-proto", **kw)

    def request(self, raw: bytes, read=recv_all) -> bytes:
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5) as sock:
            sock.sendall(raw)
            return read(sock)

    def connect(self, target: str) -> bytes:
        return self.request(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode(),
                            read=partial(recv_until, marker=b"\r\n\r\n"))

    def denied_lines(self):
        try:
            with open(self.denied, encoding="ascii") as fh:
                return [line.rstrip("\n").split("\t") for line in fh]
        except FileNotFoundError:
            return []


# ------------------------------------------------------------ CONNECT i zwykłe HTTP

class ConnectTests(ProxyCase):
    def test_connect_to_listed_domain_opens_tunnel(self):
        self.policy()
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5) as sock:
            sock.sendall(b"CONNECT registry.npmjs.org:443 HTTP/1.1\r\nHost: registry.npmjs.org:443\r\n\r\n")
            head = recv_until(sock, b"\r\n\r\n")
            self.assertTrue(head.startswith(b"HTTP/1.1 200"), head)
            sock.sendall(b"\x16\x03\x01tls-bajty")    # proxy nie zagląda do środka tunelu
            self.assertEqual(recv_until(sock, b"bajty"), b"echo:\x16\x03\x01tls-bajty")
        self.assertEqual(self.connected, [("registry.npmjs.org", 443, False)])
        self.assertEqual(self.denied_lines(), [])

    def test_connect_outside_list_is_403_with_log_line(self):
        self.policy()
        reply = self.connect("example.com:443")
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.connected, [])
        [line] = self.denied_lines()
        self.assertEqual(line[1:], ["tide-proto", "127.0.0.1", "example.com", "443", "CONNECT"])
        self.assertRegex(line[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(stat.S_IMODE(os.stat(self.denied).st_mode), 0o600)

    def test_connect_only_to_port_443(self):
        self.policy()
        self.assertTrue(self.connect("registry.npmjs.org:22").startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.denied_lines()[0][3:], ["registry.npmjs.org", "22", "CONNECT"])
        self.assertEqual(self.connected, [])

    def test_plain_http_to_foreign_domain_is_403_without_path_in_log(self):
        self.policy()
        reply = self.request(b"GET http://registry.npmjs.org/pkg?token=SEKRET123 HTTP/1.1\r\n"
                             b"Host: registry.npmjs.org\r\nAuthorization: Bearer SEKRET456\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        [line] = self.denied_lines()
        self.assertEqual(line[3:], ["registry.npmjs.org", "80", "HTTP"])
        with open(self.denied, encoding="ascii") as fh:
            text = fh.read()
        self.assertNotIn("SEKRET", text)
        self.assertNotIn("/pkg", text)
        self.assertNotIn("SEKRET", reply.decode("latin-1"))

    def test_resolved_address_refused_is_403_with_own_kind(self):
        self.policy(domains=("refused.example.org",))
        reply = self.connect("refused.example.org:443")
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertNotIn(b"spoza listy", reply)
        self.assertIn(b"adres niedozwolony", reply)
        self.assertEqual(self.denied_lines()[0][3:], ["refused.example.org", "443", "ADDRESS"])

    def test_public_name_on_list_pointing_to_lan_is_refused(self):
        self.resolved["cdn.example.net"] = "10.20.3.10"
        self.policy(domains=("cdn.example.net",))
        self.assertTrue(self.connect("cdn.example.net:443").startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.connected, [("cdn.example.net", 443, False)])

    def test_internal_name_accepted_in_card_may_reach_private_address(self):
        self.resolved["git.firma.example"] = "10.20.3.20"
        self.policy(domains=("git.firma.example",))
        self.assertTrue(self.connect("git.firma.example:443").startswith(b"HTTP/1.1 200"))
        self.assertEqual(self.connected, [("git.firma.example", 443, True)])

    def test_bad_requests(self):
        self.policy()
        self.assertTrue(self.request(b"garbage\r\n\r\n").startswith(b"HTTP/1.1 400"))
        self.assertTrue(self.request(b"CONNECT registry.npmjs.org HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 400"))
        self.assertTrue(self.request(b"GET / HTTP/1.1\r\nbez dwukropka\r\n\r\n").startswith(b"HTTP/1.1 400"))
        self.assertTrue(self.request(b"GET / HTTP/2\r\n\r\n").startswith(b"HTTP/1.1 400"))

    def test_log_line_is_sanitized(self):
        self.policy()
        self.connect("ev\til\x01.com:443")
        [line] = self.denied_lines()
        self.assertEqual(len(line), 6)
        self.assertNotIn("\x01", "".join(line))


class MatchingTests(ProxyCase):
    def test_wildcard_has_no_gaps(self):
        self.policy(domains=("*.npmjs.org",))
        for target in ("npmjs.org.attacker.com", "evil-npmjs.org", "npmjs.org", "xnpmjs.org"):
            with self.subTest(target=target):
                self.assertTrue(self.connect(f"{target}:443").startswith(b"HTTP/1.1 403"))
        self.assertTrue(self.connect("registry.npmjs.org:443").startswith(b"HTTP/1.1 200"))
        self.assertTrue(self.connect("a.b.npmjs.org:443").startswith(b"HTTP/1.1 200"))

    def test_case_and_trailing_dot_do_not_bypass(self):
        self.policy(domains=("registry.npmjs.org",))
        for target in ("REGISTRY.NPMJS.ORG", "registry.npmjs.org."):
            with self.subTest(target=target):
                self.assertTrue(self.connect(f"{target}:443").startswith(b"HTTP/1.1 200"))
        self.assertEqual({c[0] for c in self.connected}, {"registry.npmjs.org"})
        for target in ("registry.npmjs.org..", "registry.npmjs.org.evil.com.", "EVIL.COM."):
            with self.subTest(target=target):
                self.assertTrue(self.connect(f"{target}:443").startswith(b"HTTP/1.1 403"))

    def test_bare_ip_needs_literal_entry(self):
        self.policy(domains=("registry.npmjs.org", "*.npmjs.org"))
        for target in ("104.16.0.35:443", "[2606:4700::6810:23]:443", "3232235786:443", "0x68100023:443",
                       "104.16.35:443"):
            with self.subTest(target=target):
                self.assertTrue(self.connect(target).startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.connected, [])
        self.policy(domains=("104.16.0.35", "2606:4700::6810:23"))
        self.assertTrue(self.connect("104.16.0.35:443").startswith(b"HTTP/1.1 200"))
        self.assertTrue(self.connect("[2606:4700::6810:23]:443").startswith(b"HTTP/1.1 200"))

    def test_matcher_unit(self):
        m = bx.DomainMatcher(["*.npmjs.org", "Api.NuGet.org.", "10.0.0.5", "*.10.0.0.5", "zła nazwa"])
        self.assertTrue(m.allows("x.npmjs.org"))
        self.assertTrue(m.allows("api.nuget.org"))
        self.assertTrue(m.allows("10.0.0.5"))
        self.assertTrue(m.allows("::ffff:10.0.0.5"))
        self.assertFalse(m.allows("npmjs.org"))
        self.assertFalse(m.allows("x.10.0.0.5"))
        self.assertEqual(m.skipped, ("*.10.0.0.5", "zła nazwa"))
        self.assertIsNone(bx.normalize_target("10.1"))
        self.assertIsNone(bx.normalize_target("a..b"))
        self.assertIsNone(bx.normalize_target("żółw.pl"))
        self.assertIsNone(bx.normalize_target(""))

    def test_address_allowed(self):
        for address in ("127.0.0.1", "::1", "0.0.0.0", "169.254.169.254", "fe80::1", "224.0.0.1", "::ffff:127.0.0.1"):
            with self.subTest(address=address):
                self.assertFalse(bx.address_allowed(address, True))
        for address in ("10.20.3.10", "10.0.0.1", "172.16.0.1", "100.64.0.1", "fd00::1"):
            with self.subTest(address=address):
                self.assertFalse(bx.address_allowed(address, False))
                self.assertTrue(bx.address_allowed(address, True))
        self.assertTrue(bx.address_allowed("104.16.0.35", False))

    def test_full_network_still_validates_names(self):
        self.policy(domains=(), full=True)
        self.assertTrue(self.connect("anything.example.com:443").startswith(b"HTTP/1.1 200"))
        self.assertTrue(self.connect("3232235786:443").startswith(b"HTTP/1.1 403"))

    def test_full_network_does_not_open_lan_or_host_bridge_address(self):
        # M-1: `network.full` to osobna czerwona pozycja od adresów wewnętrznych.
        self.resolved.update({"x.lan": "10.20.3.10", "nas.example.org": "10.0.0.9"})
        self.policy(domains=("registry.npmjs.org",), full=True)
        for target in ("10.0.0.5:443", "10.173.184.1:443", "10.20.3.10:443", "100.64.0.1:443", "[fd00::1]:443",
                       "x.lan:443", "nas.example.org:443", "127.0.0.1:443"):
            with self.subTest(target=target):
                self.assertTrue(self.connect(target).startswith(b"HTTP/1.1 403"))
        self.assertTrue(all(internal is False for _h, _p, internal in self.connected), self.connected)
        self.assertTrue(all(line[5] == "ADDRESS" for line in self.denied_lines()))
        self.assertTrue(self.connect("104.16.0.35:443").startswith(b"HTTP/1.1 200"))   # publiczny goły IP

    def test_full_network_with_listed_internal_address(self):
        self.policy(domains=("10.20.3.10",), full=True)
        self.assertTrue(self.connect("10.20.3.10:443").startswith(b"HTTP/1.1 200"))
        self.assertEqual(self.connected, [("10.20.3.10", 443, True)])
        self.assertTrue(self.connect("192.168.3.11:443").startswith(b"HTTP/1.1 403"))

    def test_policy_internal_needs_listed_target(self):
        policy = self.policy(domains=("git.firma.example", "10.20.3.10"), full=True)
        self.assertTrue(policy.internal("git.firma.example"))
        self.assertTrue(policy.internal("10.20.3.10"))
        for host in ("firma.example", "10.0.0.5", "nas.lan", "registry.npmjs.org"):
            with self.subTest(host=host):
                self.assertFalse(policy.internal(host))


class PolicyRefreshTests(ProxyCase):
    def test_unknown_ip_gets_403_everywhere_and_no_log(self):
        for raw in (b"CONNECT registry.npmjs.org:443 HTTP/1.1\r\n\r\n", b"GET /api/tags HTTP/1.1\r\n\r\n",
                    b"GET http://example.com/ HTTP/1.1\r\n\r\n"):
            with self.subTest(raw=raw):
                self.assertTrue(self.request(raw, read=partial(recv_until, marker=b"\r\n\r\n"))
                                .startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.connected, [])
        self.assertEqual(self.ollama.requests, [])
        self.assertFalse(os.path.exists(self.denied))
        self.assertTrue(any("brak polityki" in line for line in self.logs))

    def test_policy_refresh_without_restart(self):
        self.policy(domains=("registry.npmjs.org",))
        port = self.proxy.port
        self.assertTrue(self.connect("api.nuget.org:443").startswith(b"HTTP/1.1 403"))
        self.policy(domains=("registry.npmjs.org", "api.nuget.org"))
        self.assertTrue(self.connect("api.nuget.org:443").startswith(b"HTTP/1.1 200"))
        self.policy(domains=())
        self.assertTrue(self.connect("registry.npmjs.org:443").startswith(b"HTTP/1.1 403"))
        self.proxy.clear_box_policy("127.0.0.1")
        self.assertIsNone(self.proxy.policy("127.0.0.1"))
        self.assertEqual(self.proxy.port, port)

    def test_set_box_policy_signature_for_builder(self):
        params = list(inspect.signature(bx.ProxyService.set_box_policy).parameters.values())
        self.assertEqual([p.name for p in params[1:5]], ["ip", "domains", "ollama", "denied_log_path"])
        self.assertTrue(all(p.kind is p.POSITIONAL_OR_KEYWORD for p in params[1:5]))
        self.assertTrue(all(p.kind is p.KEYWORD_ONLY for p in params[5:]))
        # Tak woła go builder (#4) i krok w pudełku (#18): pozycyjnie, bez pełnej sieci.
        policy = self.proxy.set_box_policy("10.0.0.7", ("registry.npmjs.org",), False, self.denied)
        self.assertFalse(policy.full)
        self.assertEqual(policy.box, "")

    def test_set_box_policy_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.proxy.set_box_policy("pudełko", [], False, self.denied)
        with self.assertRaises(ValueError):
            self.proxy.set_box_policy("10.0.0.7", [], False, "")
        self.proxy.set_box_policy("10.0.0.7", ["dobra.example.org", "bad name"], False, self.denied)
        self.assertTrue(any("bad name" in line for line in self.logs))


# ------------------------------------------------------------ Ollama hosta

class OllamaTests(ProxyCase):
    def test_ollama_request_is_forwarded_and_streamed(self):
        self.policy(ollama=True)
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5) as sock:
            body = b'{"model":"qwen3:8b","prompt":"Ala","stream":true}'
            sock.sendall(b"POST /api/generate HTTP/1.1\r\nHost: 10.0.0.1:18080\r\nConnection: keep-alive\r\n"
                         b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
            first = recv_until(sock, b'"done":false}')
            self.assertIn(b"HTTP/1.1 200", first)
            self.assertNotIn(b"ma kota", first)        # drugi fragment jeszcze nie wysłany
            self.ollama.release.set()
            rest = recv_all(sock)
        self.assertIn(b'" ma kota","done":true}', rest)
        [(head, got)] = self.ollama.requests
        self.assertEqual(got, body)
        self.assertTrue(head.startswith("POST /api/generate HTTP/1.1"))
        self.assertIn(f"Host: 127.0.0.1:{self.ollama.port}", head)
        self.assertNotIn("10.0.0.1:18080", head)
        self.assertNotIn("keep-alive", head)

    def test_absolute_form_to_proxy_address_reaches_ollama(self):
        self.policy(ollama=True)
        self.ollama.release.set()
        reply = self.request(f"GET http://127.0.0.1:{self.proxy.port}/api/tags HTTP/1.1\r\n\r\n".encode())
        self.assertIn(b"HTTP/1.1 200", reply)
        self.assertTrue(self.ollama.requests[0][0].startswith("GET /api/tags HTTP/1.1"))

    def test_ollama_false_is_403(self):
        self.policy(ollama=False)
        reply = self.request(b"GET /api/tags HTTP/1.1\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.ollama.requests, [])
        self.assertEqual(self.denied_lines()[0][3:], ["host-ollama", str(self.ollama.port), "OLLAMA"])

    def test_only_ollama_paths_and_never_another_host(self):
        self.policy(ollama=True)
        self.assertTrue(self.request(b"GET /internal/v1/messages HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 404"))
        # Trasa K3 (PT-175) bez atrapy kroku: 403, nigdy do Ollamy (szczegóły w test_box_k3.py).
        self.assertTrue(self.request(b"GET /anthropic/v1/messages HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 403"))
        reply = self.request(f"GET http://127.0.0.1:{self.ollama.port}/api/tags HTTP/1.1\r\n\r\n".encode())
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.ollama.requests, [])


# ------------------------------------------------------------ polityka z profili

class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.notes = []

    def compose(self, project=None, cards=None):
        return bx.compose_policy(project, cards, log=self.notes.append)

    def test_policy_is_models_base_stacks_project_and_cards(self):
        project = {"tide-box": 1, "stacks": ["node"], "network": {"domains": ["api.example.org"]}}
        cards = {"K-1": {"tide-box": 1, "stacks": ["dotnet"], "network": {"domains": ["cdn.example.net"]}}}
        got = self.compose(project, cards)
        for domain in bx.MODEL_DOMAINS + templates.work_domains(project) + ("cdn.example.net",):
            self.assertIn(domain, got.domains)
        for domain in templates.work_domains({"stacks": ["dotnet"]}):
            self.assertIn(domain, got.domains)
        # domeny tylko budowy szablonów (PT-192) nie trafiają do polityki kroków
        self.assertNotIn("aka.ms", got.domains)
        self.assertEqual(len(got.domains), len(set(got.domains)))
        self.assertFalse(got.ollama)
        self.assertFalse(got.full)
        self.assertEqual(self.notes, [])

    def test_service_never_adds_remote_repos_or_internal_addresses(self):
        got = self.compose({"tide-box": 1, "stacks": templates.stack_names()})
        for domain in got.domains:
            with self.subTest(domain=domain):
                self.assertNotEqual(bp.classify(domain), "red")
                self.assertNotEqual(domain, "github.com")
                self.assertNotIn("gitlab", domain)
                self.assertFalse(domain.startswith("192.168."))
                self.assertFalse(domain.endswith("firma.example"))
        for domain in bx.MODEL_DOMAINS:
            self.assertEqual(bp.internal_hosts([domain]), [])

    def test_card_full_without_consent_is_ignored_with_note(self):
        cards = {"K-2": {"tide-box": 1, "network": {"full": True, "domains": ["cdn.example.net"]}}}
        got = self.compose(None, cards)
        self.assertFalse(got.full)
        self.assertIn("cdn.example.net", got.domains)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("K-2", self.notes[0])
        self.assertIn("network.full", self.notes[0])

    def test_card_full_with_consent_and_accepted_red(self):
        card = {"tide-box": 1, "network": {"full": True, "full-consent": True}, "accepted_red": ["network.full"]}
        self.assertTrue(self.compose(None, {"K-3": card}).full)
        card = {"tide-box": 1, "network": {"full": True, "full-consent": True}}
        got = self.compose(None, {"K-4": card})
        self.assertFalse(got.full)
        self.assertTrue(any("network.full" in n and "K-4" in n for n in self.notes))

    def test_card_red_domain_needs_accepted_red(self):
        card = {"tide-box": 1, "network": {"domains": ["github.com", "10.20.3.10"]}}
        got = self.compose(None, {"K-5": card})
        self.assertNotIn("github.com", got.domains)
        self.assertNotIn("10.20.3.10", got.domains)
        card["accepted_red"] = ["10.20.3.10"]
        self.assertIn("10.20.3.10", self.compose(None, {"K-5": card}).domains)

    def test_host_ollama_from_profiles(self):
        self.assertTrue(self.compose({"tide-box": 1, "network": {"host-ollama": True}}).ollama)
        self.assertTrue(self.compose(None, {"K-6": {"tide-box": 1, "network": {"host-ollama": True}}}).ollama)

    def test_invalid_card_is_skipped(self):
        got = self.compose(None, {"K-7": {"tide-box": 2}})
        self.assertEqual(got.domains, bx.MODEL_DOMAINS + templates.work_domains({}))
        self.assertNotIn("motd.ubuntu.com", got.domains)
        self.assertTrue(any("K-7" in n for n in self.notes))


# ------------------------------------------------------------ start z usługą (atrapa incus)

FAKE_INCUS = """#!/bin/bash
echo "incus $*" >> "$TIDE_FAKE_LOG"
if [[ "$1 $2 $4" == "network get ipv4.address" && "$3" == "${TIDE_FAKE_BRIDGE_NAME:-incusbr0}" \
      && -n "$TIDE_FAKE_BRIDGE" ]]; then
    echo "$TIDE_FAKE_BRIDGE"; exit 0
fi
echo "Error: Network not found" >&2; exit 1
"""

# Atrapa `ip` (zapas, gdy incus-user nie widzi sieci): adres z $TIDE_FAKE_IP_JSON, rodzaj interfejsu
# z $TIDE_FAKE_LINK_JSON; bez nich „Device does not exist”.
FAKE_IP = """#!/bin/bash
echo "ip $*" >> "$TIDE_FAKE_LOG"
if [[ "$*" == "-j -d link show dev incusbr0" && -n "$TIDE_FAKE_LINK_JSON" ]]; then
    echo "$TIDE_FAKE_LINK_JSON"; exit 0
fi
if [[ "$*" == "-j -4 addr show dev incusbr0" && -n "$TIDE_FAKE_IP_JSON" ]]; then
    echo "$TIDE_FAKE_IP_JSON"; exit 0
fi
echo 'Device "incusbr0" does not exist.' >&2; exit 1
"""


def ip_json(address):
    return json.dumps([{"ifname": "incusbr0", "addr_info": [
        {"family": "inet", "local": address, "prefixlen": 24, "scope": "global"}]}])


def link_json(kind="bridge"):
    link = {"ifname": "incusbr0", "link_type": "ether"}
    if kind:
        link["linkinfo"] = {"info_kind": kind}
    return json.dumps([link])


class FakeService:
    started = []

    def __init__(self, listen, port, log=None):
        self.listen, self.port = listen, port

    def start(self):
        FakeService.started.append((self.listen, self.port))
        return self

    def stop(self):
        pass


class StartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-proxy-start-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name, body in (("incus", FAKE_INCUS), ("ip", FAKE_IP)):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, 0o755)
        self.calls = os.path.join(self.tmp, "calls.log")
        self.logs = []
        FakeService.started = []

    def start(self, bridge, settings=None, ip=None, kind="bridge", **kw):
        env = {"PATH": self.bin + os.pathsep + "/usr/bin:/bin", "TIDE_FAKE_LOG": self.calls,
               "TIDE_FAKE_BRIDGE": bridge or "", "TIDE_FAKE_IP_JSON": ip_json(ip) if ip else "",
               "TIDE_FAKE_LINK_JSON": link_json(kind) if ip else ""}
        settings = settings or bs.BoxSettings(enabled=True)
        return bx.start_service(settings, run=partial(subprocess.run, env=env),
                                which=lambda name: shutil.which(name, path=self.bin), log=self.logs.append,
                                factory=FakeService, **kw)

    def incus_calls(self):
        try:
            with open(self.calls, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return []

    def test_starts_on_bridge_address_and_port_from_settings(self):
        service = self.start("10.173.184.1/24", bs.BoxSettings(enabled=True, proxy_port=18555))
        self.assertIsInstance(service, FakeService)
        self.assertEqual(FakeService.started, [("10.173.184.1", 18555)])
        self.assertEqual(self.incus_calls(), ["incus network get incusbr0 ipv4.address"])
        self.assertIs(bx.running(), service)

    def test_falls_back_to_interface_address_when_incus_user_cannot_read_network(self):
        service = self.start(None, ip="10.173.184.1")
        self.assertEqual(FakeService.started, [("10.173.184.1", bs.DEFAULT_PORT)])
        self.assertIsInstance(service, FakeService)
        self.assertEqual(self.incus_calls(), ["incus network get incusbr0 ipv4.address",
                                              "ip -j -d link show dev incusbr0",
                                              "ip -j -4 addr show dev incusbr0"])
        self.assertTrue(any("Network not found" in line for line in self.logs))

    def test_fallback_refuses_interface_that_is_not_a_bridge(self):
        # M-2: `box.bridge: wlan0` nie może przenieść nasłuchu na adres sieci lokalnej.
        for kind in (None, "veth", "vlan"):
            with self.subTest(kind=kind), self.assertRaises(bx.ProxyError) as ctx:
                self.start(None, ip="10.20.3.5", kind=kind)
            self.assertIn("nie jest mostkiem", str(ctx.exception))
        self.assertEqual(FakeService.started, [])
        self.assertNotIn("ip -j -4 addr show dev incusbr0", self.incus_calls())

    def test_fallback_address_is_checked_too(self):
        with self.assertRaises(bx.ProxyError):
            self.start(None, ip="127.0.0.1")
        self.assertEqual(FakeService.started, [])

    def test_refuses_loopback_unspecified_and_other_address(self):
        for bridge in ("127.0.0.1/8", "0.0.0.0/0"):
            with self.subTest(bridge=bridge), self.assertRaises(bx.ProxyError):
                self.start(bridge)
        with self.assertRaises(bx.ProxyError):
            self.start("10.173.184.1/24", listen="10.173.184.99")
        with self.assertRaises(bx.ProxyError):
            self.start("10.173.184.1/24", listen="0.0.0.0")
        self.assertEqual(FakeService.started, [])

    def test_check_listen(self):
        self.assertEqual(bx.check_listen("10.0.0.1", "10.0.0.1"), "10.0.0.1")
        for listen in ("0.0.0.0", "::", "127.0.0.1", "127.0.0.2", "::1", "incusbr0"):
            with self.subTest(listen=listen), self.assertRaises(bx.ProxyError):
                bx.check_listen(listen, listen)
        with self.assertRaises(bx.ProxyError):
            bx.check_listen("10.0.0.1", None)

    def test_disabled_does_not_touch_incus(self):
        self.assertIsNone(self.start("10.0.0.1/24", bs.BoxSettings(enabled=False)))
        self.assertEqual(self.incus_calls(), [])
        self.assertEqual(FakeService.started, [])

    def test_no_bridge_or_no_incus(self):
        self.assertIsNone(self.start(None))
        self.assertTrue(any("mostek" in line for line in self.logs))
        os.unlink(os.path.join(self.bin, "incus"))
        self.assertIsNone(self.start("10.0.0.1/24"))
        self.assertTrue(any("incus" in line for line in self.logs))
        self.assertEqual(FakeService.started, [])

    def test_daemon_start_without_box_settings_works_as_before(self):
        settings_file = os.path.join(self.tmp, "cardflow.json")
        self.assertIsNone(tide_app.start_box_proxy(None, settings_file))   # brak pliku
        with open(settings_file, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 5, "box": {"enabled": True}}, fh)
        # box.enabled, ale bez Incusa: usługa dalej startuje, tylko bez proxy.
        self.assertIsNone(tide_app.start_box_proxy(None, settings_file, which=lambda name: None,
                                                   log=self.logs.append))
        # Zły adres mostka: wpis w dzienniku zamiast wyjątku w usłudze.
        env = {"PATH": self.bin + os.pathsep + "/usr/bin:/bin", "TIDE_FAKE_LOG": self.calls,
               "TIDE_FAKE_BRIDGE": "127.0.0.1/8"}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(tide_app.start_box_proxy(None, settings_file, run=partial(subprocess.run, env=env),
                                                       which=lambda name: shutil.which(name, path=self.bin)))
        self.assertIn("nie startuje", err.getvalue())

    def test_real_service_binds_and_stops(self):
        service = bx.ProxyService("127.0.0.1", 0, log=self.logs.append).start()
        self.assertGreater(service.port, 0)
        bx._running = service
        self.addCleanup(setattr, bx, "_running", None)
        service.stop()
        self.assertIsNone(bx.running())   # zatrzymane proxy nie jest już „uruchomionym”
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", service.port), timeout=1).close()


class SettingsTests(unittest.TestCase):
    def test_defaults_and_values(self):
        self.assertEqual(bs.from_dict({}), bs.BoxSettings())
        self.assertFalse(bs.BoxSettings().enabled)
        got = bs.from_dict({"box": {"enabled": True, "proxy-port": 18081, "bridge": "tidebr0"}})
        self.assertEqual((got.enabled, got.proxy_port, got.bridge, got.warnings), (True, 18081, "tidebr0", ()))

    def test_bad_values_fall_back_safely(self):
        got = bs.from_dict({"box": {"enabled": "yes", "proxy-port": 80, "bridge": "a b"}})
        self.assertEqual((got.enabled, got.proxy_port, got.bridge), (False, bs.DEFAULT_PORT, bs.DEFAULT_BRIDGE))
        self.assertEqual(len(got.warnings), 3)
        self.assertFalse(bs.from_dict({"box": []}).enabled)

    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cardflow.json")
            self.assertEqual(bs.load(path), bs.BoxSettings())
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{zepsuty")
            self.assertFalse(bs.load(path).enabled)
            self.assertTrue(bs.load(path).warnings)
            self.assertEqual(bs.settings_path({"XDG_CONFIG_HOME": tmp}), os.path.join(tmp, "tide", "cardflow.json"))


class BoxModelsTests(unittest.TestCase):
    def test_host_ollama_points_to_bridge_proxy(self):
        data = {"default": "local",
                "endpoints": {"local": {"url": "http://127.0.0.1:11434", "kind": "ollama"},
                              "mac": {"url": "http://mac-studio:11434", "kind": "ollama", "tokenFile": "~/.t"},
                              "lo": {"url": "http://localhost:11434/"}},
                "roles": {"agent": "local:qwen3-coder:30b", "summary": "mac:qwen3:8b"},
                "providers": [{"label": "x", "url": "https://api.example.com", "tokenFile": "~/.x"}],
                "agentProgram": "claude"}
        got = boxconfig.for_box(data, "10.173.184.1", 18080)
        self.assertEqual(got["endpoints"], {"local": {"url": "http://10.173.184.1:18080", "kind": "ollama"},
                                            "lo": {"url": "http://10.173.184.1:18080", "kind": "ollama"}})
        self.assertNotIn("providers", got)
        self.assertNotIn("tokenFile", json.dumps(got))
        self.assertEqual(got["agentProgram"], "claude")
        cfg = models_config.from_dict(got)
        self.assertEqual(cfg.role("agent").model, "qwen3-coder:30b")
        # Rola przypięta do punktu, którego pudełko nie widzi, znika (nie ląduje po cichu z nazwą „mac:…”).
        self.assertNotIn("summary", got["roles"])
        self.assertEqual(cfg.role("summary").model, models_config.DEFAULT_MODEL)
        self.assertEqual(data["endpoints"]["local"]["url"], "http://127.0.0.1:11434")   # oryginał bez zmian

    def test_without_host_file_uses_default_endpoint(self):
        got = boxconfig.for_box(None, "10.0.0.1", 18080)
        self.assertEqual(got, {"endpoints": {"local": {"url": "http://10.0.0.1:18080", "kind": "ollama"}}})
        self.assertFalse(boxconfig.is_host_ollama("http://127.0.0.1:8080"))
        self.assertFalse(boxconfig.is_host_ollama("https://127.0.0.1:11434"))


class BoundaryTests(unittest.TestCase):
    def test_daemon_never_calls_nft_or_sudo(self):
        pattern = re.compile(r"""\[\s*["'](?:nft|sudo)["']|["'](?:nft|sudo)\s[^"']*["']""")
        hits = []
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "daemon", "tide_daemon")):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as fh:
                        hits += [f"{path}:{n}" for n, line in enumerate(fh, 1) if pattern.search(line)]
        self.assertEqual(hits, [])

    def test_proxy_module_has_no_tls_interception(self):
        with open(bx.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("import ssl", source)
        self.assertNotIn("start_tls", source)


if __name__ == "__main__":
    unittest.main()
