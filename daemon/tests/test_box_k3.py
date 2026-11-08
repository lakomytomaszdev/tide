"""K3 (PT-175): box/k3.py i trasy wstrzykujące w box/proxy.py.

Proxy działa w procesie testu na 127.0.0.1; „api.anthropic.com” i „chatgpt.com” to lokalna atrapa HTTP
(podstawiony `k3_connector`), która zapisuje surowe żądanie i odpowiada strumieniem. Tokeny hosta to pliki
w katalogu tymczasowym. Bez sieci, bez Incusa, bez prawdziwych poświadczeń.
"""
import asyncio
import base64
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from dataclasses import replace

from tide_daemon.box import k3
from tide_daemon.box import proxy as bx

REAL_CLAUDE = "sk-ant-oat01-PRAWDZIWY-TOKEN-HOSTA"
REAL_CODEX = "eyJ.PRAWDZIWY-TOKEN-CODEXA.sig"


class FakeUpstream:
    """Atrapa hosta trasy: zapisuje surowe bajty żądania do zamknięcia połączenia przez klienta (proxy) albo do
    końca treści i odpowiada SSE w dwóch fragmentach."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self.connections = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            names = {line.partition(":")[0].lower(): line.partition(":")[2].strip() for line in lines[1:]}
            if "content-length" in names:
                while len(body) < int(names["content-length"]):
                    body += conn.recv(4096)
            elif names.get("transfer-encoding") == "chunked":
                while not body.endswith(b"0\r\n\r\n"):
                    body += conn.recv(4096)
            # Krótko czekamy, czy proxy nie dośle czegoś poza treścią (potokowane drugie żądanie).
            conn.settimeout(0.2)
            try:
                extra = conn.recv(4096)
            except OSError:
                extra = b""
            self.requests.append({"head": head.decode("latin-1"), "lines": lines, "body": body, "extra": extra})
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                         b"event: message_start\ndata: {}\n\nevent: message_stop\ndata: {}\n\n")

    def close(self):
        self.sock.close()


def recv_all(sock, limit=5.0):
    sock.settimeout(limit)
    data = b""
    while chunk := sock.recv(4096):
        data += chunk
    return data


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


class K3Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-k3-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.upstream = FakeUpstream()
        self.addCleanup(self.upstream.close)
        self.claude_file = os.path.join(self.tmp, "host", ".claude", ".credentials.json")
        self.codex_file = os.path.join(self.tmp, "host", ".codex", "auth.json")
        self.write_claude(REAL_CLAUDE)
        id_token = f"{b64({'alg': 'RS256'})}.{b64({'https://api.openai.com/auth': {'chatgpt_plan_type': 'pro', 'chatgpt_account_id': 'acct-123'}})}.podpis"
        self.write_json(self.codex_file, {"tokens": {"access_token": REAL_CODEX, "id_token": id_token,
                                                     "refresh_token": "PRAWDZIWY-REFRESH", "account_id": "acct-123"}})
        base = k3.default_routes({"HOME": os.path.join(self.tmp, "host")})
        self.assertEqual(base["anthropic"].token_file, self.claude_file)
        self.routes = base
        self.now = [1000.0]
        self.wall = [1_800_000_000.0]
        self.grants = k3.GrantRegistry(clock=lambda: self.now[0], routes=self.routes, wall_clock=lambda: self.wall[0])
        self.dialed = []

        async def k3_connector(route):
            self.dialed.append(route.host)
            return await asyncio.open_connection("127.0.0.1", self.upstream.port)

        self.logs = []
        self.proxy = bx.ProxyService("127.0.0.1", 0, connector=self._no_connect, log=self.logs.append,
                                     grants=self.grants, k3_connector=k3_connector).start()
        self.addCleanup(self.proxy.stop)
        self.denied = os.path.join(self.tmp, "step", "proxy-denied.log")
        self.proxy.set_box_policy("127.0.0.1", ["registry.npmjs.org"], False, self.denied, box="tide-k3")

    async def _no_connect(self, host, port, internal):
        self.dialed.append(host)
        raise OSError("test: brak sieci")

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def write_claude(self, token, expires=None):
        oauth = {"accessToken": token, "refreshToken": "PRAWDZIWY-REFRESH-CLAUDE"}
        if expires is not None:
            oauth["expiresAt"] = expires
        self.write_json(self.claude_file, {"claudeAiOauth": oauth})

    def request(self, raw: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5) as sock:
            sock.sendall(raw)
            return recv_all(sock)

    def messages(self, key, path="/anthropic/v1/messages?beta=true", body=b'{"model":"x","messages":[]}',
                 extra=b""):
        return self.request(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: 10.173.184.1:18080\r\n"
                            b"x-api-key: " + key.encode() + b"\r\nanthropic-version: 2023-06-01\r\n"
                            b"anthropic-beta: claude-code-20250219\r\nUser-Agent: claude-cli/2.0\r\n"
                            b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() +
                            b"\r\n\r\n" + body + extra)

    def denied_lines(self):
        try:
            with open(self.denied, encoding="ascii") as fh:
                return [line.rstrip("\n").split("\t") for line in fh]
        except FileNotFoundError:
            return []

    def assert_no_secret(self, *texts):
        for text in texts:
            for secret in (REAL_CLAUDE, REAL_CODEX, "PRAWDZIWY"):
                self.assertNotIn(secret, text)


class K3ActiveTests(unittest.TestCase):
    def test_both_programs_have_k3_after_p3(self):
        self.assertTrue(k3.k3_active("claude"))
        self.assertTrue(k3.k3_active("codex"))
        self.assertFalse(k3.k3_active("aider"))
        self.assertEqual(k3.login_programs(), ())

    def test_issue_refuses_program_without_k3(self):
        with self.assertRaises(ValueError):
            k3.GrantRegistry().issue("PT-1", "10.0.0.2", "aider")


class ClaudeRouteTests(K3Case):
    def test_injects_host_token_and_changes_only_auth_headers(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        body = '{"model":"claude","messages":[{"role":"user","content":"zażółć"}]}'.encode()
        reply = self.messages(grant.key, body=body)
        self.assertTrue(reply.startswith(b"HTTP/1.1 200 OK"), reply)
        self.assertTrue(reply.endswith(b"event: message_stop\ndata: {}\n\n"))
        [req] = self.upstream.requests
        self.assertEqual(self.dialed, ["api.anthropic.com"])
        self.assertEqual(req["lines"][0], "POST /v1/messages?beta=true HTTP/1.1")
        self.assertEqual(req["body"], body)                       # treść bez zmian
        self.assertEqual(req["lines"][1:], [
            "anthropic-version: 2023-06-01",
            "anthropic-beta: claude-code-20250219,oauth-2025-04-20",
            "User-Agent: claude-cli/2.0",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            f"Authorization: Bearer {REAL_CLAUDE}",
            "Host: api.anthropic.com",
            "Connection: close",
        ])
        self.assertNotIn(grant.key, req["head"])                  # atrapa nie wychodzi z hosta
        self.assertEqual(self.denied_lines(), [])
        self.assert_no_secret(reply.decode("latin-1"), "\n".join(self.logs))

    def test_bearer_dummy_works_and_beta_is_added_when_missing(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        reply = self.request(b"POST /anthropic/v1/messages/count_tokens HTTP/1.1\r\nHost: x\r\n"
                             b"Authorization: Bearer " + grant.key.encode() + b"\r\nContent-Length: 2\r\n\r\n{}")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        [req] = self.upstream.requests
        self.assertIn("anthropic-beta: oauth-2025-04-20", req["lines"])
        self.assertEqual([line for line in req["lines"] if line.lower().startswith("authorization")],
                         [f"Authorization: Bearer {REAL_CLAUDE}"])

    def test_absolute_form_to_proxy_address_is_the_same_route(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        path = f"http://127.0.0.1:{self.proxy.port}/anthropic/v1/messages"
        self.assertTrue(self.messages(grant.key, path=path).startswith(b"HTTP/1.1 200"))
        self.assertEqual(self.upstream.requests[0]["lines"][0], "POST /v1/messages HTTP/1.1")

    def test_refreshed_host_token_is_used_without_new_dummy(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        self.assertTrue(self.messages(grant.key).startswith(b"HTTP/1.1 200"))
        self.write_claude("sk-ant-oat01-ODSWIEZONY-NA-HOSCIE")      # host odświeżył token w trakcie kroku
        self.assertTrue(self.messages(grant.key).startswith(b"HTTP/1.1 200"))
        auth = [[line for line in r["lines"] if line.startswith("Authorization")] for r in self.upstream.requests]
        self.assertEqual(auth, [[f"Authorization: Bearer {REAL_CLAUDE}"],
                                ["Authorization: Bearer sk-ant-oat01-ODSWIEZONY-NA-HOSCIE"]])

    def test_chunked_body_is_relayed_unchanged_and_nothing_after_it(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        body = b"5;ext=1\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        smuggled = b"POST /v1/organizations HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n"
        reply = self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nx-api-key: " + grant.key.encode() +
                             b"\r\nTransfer-Encoding: chunked\r\n\r\n" + body + smuggled)
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        [req] = self.upstream.requests
        self.assertEqual(req["body"], body)
        self.assertEqual(req["extra"], b"")       # potokowane drugie żądanie nie idzie z tokenem hosta

    def test_ambiguous_length_is_400(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        reply = self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nx-api-key: " + grant.key.encode() +
                             b"\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply)
        self.assertEqual(self.upstream.requests, [])

    def test_control_characters_in_request_line_are_400_without_upstream(self):
        # Review M1: goły LF w zapytaniu dopisywał nagłówki (np. `chatgpt-account-id`, `Host`) przed tokenem hosta.
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        key = grant.key.encode()
        for line in (b"POST /anthropic/v1/messages?x=1\nchatgpt-account-id:evil\nHost:evil.example HTTP/1.1",
                     b"POST /anthropic/v1/messages?x=1\rHost:evil.example HTTP/1.1",
                     b"POST /anthropic/v1/messages?x=1\tHost:evil.example HTTP/1.1",
                     b"POST /anthropic/v1/messages?x=\x7f HTTP/1.1",
                     b"POST /anthropic/v1/messages?x=\xc4\x85 HTTP/1.1",
                     b"PO\nST /anthropic/v1/messages HTTP/1.1",
                     b"PO(ST /anthropic/v1/messages HTTP/1.1",
                     b"POST /anthropic/v1/messages HTTP/1.1x",
                     b"POST /anthropic/v1/messages\n HTTP/1.1"):
            with self.subTest(line=line):
                reply = self.request(line + b"\r\nx-api-key: " + key + b"\r\nContent-Length: 0\r\n\r\n")
                self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply)
        self.assertEqual(self.dialed, [])
        self.assertEqual(self.upstream.connections, 0)

    def test_query_outside_safe_set_is_400_and_safe_query_passes(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        for path in ('/anthropic/v1/messages?a="b"', "/anthropic/v1/messages?a=<b>", "/anthropic/v1/messages?a#b",
                     "/anthropic/v1/messages?a\\b", "/anthropic/v1/messages?a={b}"):
            with self.subTest(path=path):
                self.assertTrue(self.messages(grant.key, path=path).startswith(b"HTTP/1.1 400"))
        self.assertEqual(self.upstream.connections, 0)
        ok = "/anthropic/v1/messages?beta=true&x=a%20b,c:d@e/f?g"
        self.assertTrue(self.messages(grant.key, path=ok).startswith(b"HTTP/1.1 200"))
        self.assertEqual(self.upstream.requests[0]["lines"][0], "POST /v1/messages?beta=true&x=a%20b,c:d@e/f?g HTTP/1.1")

    def test_chunk_size_must_be_plain_hex(self):
        # Review N1: `int(..., 16)` przyjmował `0x5`, `+0`, `0_5`, spacje — ścisły upstream policzyłby to inaczej.
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        for body in (b"0x5\r\nhello\r\n0\r\n\r\n", b"+5\r\nhello\r\n0\r\n\r\n", b"0_5\r\nhello\r\n0\r\n\r\n",
                     b" 5\r\nhello\r\n0\r\n\r\n", b"\r\n"):
            with self.subTest(body=body):
                self.upstream.requests.clear()
                self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nx-api-key: " + grant.key.encode() +
                             b"\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
                for req in self.upstream.requests:
                    self.assertNotIn(b"hello", req["body"])        # nic z niepoprawnej ramki nie przeszło


class DummyLimitsTests(K3Case):
    def assert_k3_denied(self, reply, kind="K3", host="api.anthropic.com"):
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.upstream.requests, [])
        line = self.denied_lines()[-1]
        self.assertEqual(line[1:], ["tide-k3", "127.0.0.1", host, "443", kind])

    def test_dummy_revoked_after_step_is_403(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        other = self.grants.issue("PT-2", "127.0.0.1", "claude")
        self.assertEqual(self.grants.revoke_step("PT-1"), 1)
        self.assert_k3_denied(self.messages(grant.key))
        with open(self.denied, encoding="ascii") as fh:
            self.assertNotIn(grant.key, fh.read())             # atrapa nie trafia do dziennika
        self.assertTrue(self.messages(other.key).startswith(b"HTTP/1.1 200"))

    def test_dummy_expires_without_revoke(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude", ttl=60)
        self.now[0] += 61
        self.assert_k3_denied(self.messages(grant.key))
        self.assertEqual(self.grants.active(), 0)

    def test_unknown_dummy_and_missing_dummy_are_403(self):
        self.grants.issue("PT-1", "127.0.0.1", "claude")
        self.assert_k3_denied(self.messages("sk-ant-oat01-zgadywana"))
        self.assert_k3_denied(self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nContent-Length: 0\r\n\r\n"))

    def test_dummy_bound_to_box_address(self):
        grant = self.grants.issue("PT-1", "10.173.184.23", "claude")     # inne pudełko
        self.assert_k3_denied(self.messages(grant.key))

    def test_grant_address_is_normalized_like_client_address(self):
        # Review N3: `::ffff:127.0.0.1` z `issue` musi pasować do klienta po `_ip` (127.0.0.1).
        grant = self.grants.issue("PT-1", "::ffff:127.0.0.1", "claude")
        self.assertEqual(grant.ip, "127.0.0.1")
        self.assertTrue(self.messages(grant.key).startswith(b"HTTP/1.1 200"))
        with self.assertRaises(ValueError):
            self.grants.issue("PT-1", "tide-k3", "claude")

    def test_dummy_bound_to_one_route(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        reply = self.request(b"GET /chatgpt/backend-api/codex/models HTTP/1.1\r\n"
                             b"Authorization: Bearer " + grant.key.encode() + b"\r\n\r\n")
        self.assert_k3_denied(reply, host="chatgpt.com")

    def test_two_different_dummies_in_one_request_are_403(self):
        a = self.grants.issue("PT-1", "127.0.0.1", "claude")
        b = self.grants.issue("PT-2", "127.0.0.1", "claude")
        reply = self.request(b"POST /anthropic/v1/messages HTTP/1.1\r\nx-api-key: " + a.key.encode() +
                             b"\r\nAuthorization: Bearer " + b.key.encode() + b"\r\nContent-Length: 0\r\n\r\n")
        self.assert_k3_denied(reply)

    def test_path_outside_route_list_is_403(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        for path in ("/anthropic/v1/organizations", "/anthropic/v1/messages/../organizations",
                     "/anthropic/v1/messages/%2e%2e/files", "/anthropic//v1/messages", "/anthropic/api/oauth/token"):
            self.assert_k3_denied(self.messages(grant.key, path=path), kind="K3PATH")

    def test_dummy_to_other_domain_is_403(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        auth = b"Authorization: Bearer " + grant.key.encode() + b"\r\nx-api-key: " + grant.key.encode()
        connect = self.request(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n" + auth + b"\r\n\r\n")
        self.assertTrue(connect.startswith(b"HTTP/1.1 403"), connect)
        plain = self.request(b"POST http://evil.example/anthropic/v1/messages HTTP/1.1\r\nHost: evil.example\r\n" +
                             auth + b"\r\nContent-Length: 0\r\n\r\n")
        self.assertTrue(plain.startswith(b"HTTP/1.1 403"), plain)
        self.assertEqual([line[3:] for line in self.denied_lines()],
                         [["evil.example", "443", "CONNECT"], ["evil.example", "80", "HTTP"]])
        self.assertEqual(self.dialed, [])
        self.assertEqual(self.upstream.connections, 0)

    def test_box_without_policy_gets_403(self):
        self.proxy.clear_box_policy("127.0.0.1")
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        self.assertTrue(self.messages(grant.key).startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.upstream.requests, [])


class DefaultConnectorTests(K3Case):
    def test_route_host_resolving_to_loopback_is_refused(self):
        routes = dict(self.routes, anthropic=replace(self.routes["anthropic"], host="localhost"))
        grants = k3.GrantRegistry(routes=routes)
        proxy = bx.ProxyService("127.0.0.1", 0, log=self.logs.append, grants=grants).start()
        self.addCleanup(proxy.stop)
        proxy.set_box_policy("127.0.0.1", [], False, self.denied, box="tide-k3")
        grant = grants.issue("PT-1", "127.0.0.1", "claude")
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
            sock.sendall(b"POST /anthropic/v1/messages HTTP/1.1\r\nx-api-key: " + grant.key.encode() +
                         b"\r\nContent-Length: 0\r\n\r\n")
            reply = recv_all(sock)
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.denied_lines()[-1][3:], ["localhost", "443", "ADDRESS"])


class HostTokenErrorsTests(K3Case):
    def test_missing_host_token_is_502_without_fallback(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        os.unlink(self.claude_file)
        reply = self.messages(grant.key)
        self.assertTrue(reply.startswith(b"HTTP/1.1 502"), reply)
        self.assertIn(b"brak pliku tokenu", reply)
        self.assertEqual(self.upstream.requests, [])
        self.assertTrue(any("K3 anthropic" in line for line in self.logs), self.logs)

    def test_expired_host_token_is_502_and_never_shown(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        self.write_claude(REAL_CLAUDE, expires=(self.wall[0] + 60) * 1000)
        self.assertTrue(self.messages(grant.key).startswith(b"HTTP/1.1 200"))   # jeszcze ważny
        self.upstream.requests.clear()
        self.wall[0] += 61                                                       # zegar epoki, nie zegar atrap
        reply = self.messages(grant.key)
        self.assertTrue(reply.startswith(b"HTTP/1.1 502"), reply)
        self.assertIn("wygasł".encode("ascii", "replace"), reply)
        self.assert_no_secret(reply.decode("latin-1"), "\n".join(self.logs))
        self.assertEqual(self.upstream.requests, [])

    def test_broken_host_file_message_has_no_content(self):
        grant = self.grants.issue("PT-1", "127.0.0.1", "claude")
        with open(self.claude_file, "w", encoding="utf-8") as fh:
            fh.write('{"claudeAiOauth": PRAWDZIWY')
        reply = self.messages(grant.key)
        self.assertTrue(reply.startswith(b"HTTP/1.1 502"), reply)
        self.assert_no_secret(reply.decode("latin-1"), "\n".join(self.logs))


class CodexRouteTests(K3Case):
    def test_step_credentials_carry_only_dummy_and_account_identifiers(self):
        grant = self.grants.issue("PT-3", "127.0.0.1", "codex")
        creds = k3.step_credentials(grant, "http://10.173.184.1:18080/")
        self.assertEqual(creds.env, {})
        self.assertEqual(list(creds.files), [".codex/auth.json"])
        auth = json.loads(creds.files[".codex/auth.json"])
        self.assertEqual(auth["tokens"]["access_token"], grant.key)
        self.assertEqual(auth["tokens"]["account_id"], "acct-123")
        claims = json.loads(base64.urlsafe_b64decode(grant.key.split(".")[1] + "=="))
        self.assertEqual(claims["https://api.openai.com/auth"],
                         {"chatgpt_plan_type": "pro", "chatgpt_account_id": "acct-123"})
        self.assert_no_secret(creds.files[".codex/auth.json"], " ".join(creds.args))
        self.assertIn('model_provider="tide"', creds.args)
        self.assertIn('chatgpt_base_url="http://10.173.184.1:18080/chatgpt/backend-api/"', creds.args)
        self.assertTrue(any('base_url = "http://10.173.184.1:18080/chatgpt/backend-api/codex"' in a
                            and "requires_openai_auth = true" in a for a in creds.args))

    def test_responses_get_host_token_and_account_header(self):
        grant = self.grants.issue("PT-3", "127.0.0.1", "codex")
        body = b'{"model":"gpt-5","input":"hi","stream":true}'
        reply = self.request(b"POST /chatgpt/backend-api/codex/responses HTTP/1.1\r\nHost: 10.173.184.1:18080\r\n"
                             b"Authorization: Bearer " + grant.key.encode() + b"\r\nchatgpt-account-id: acct-123\r\n"
                             b"originator: codex_exec\r\nContent-Length: " + str(len(body)).encode() +
                             b"\r\n\r\n" + body)
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        [req] = self.upstream.requests
        self.assertEqual(self.dialed, ["chatgpt.com"])
        self.assertEqual(req["lines"], [
            "POST /backend-api/codex/responses HTTP/1.1",
            "originator: codex_exec",
            f"Content-Length: {len(body)}",
            f"Authorization: Bearer {REAL_CODEX}",
            "chatgpt-account-id: acct-123",
            "Host: chatgpt.com",
            "Connection: close",
        ])
        self.assertEqual(req["body"], body)

    def test_codex_paths_outside_p3_list_are_403(self):
        grant = self.grants.issue("PT-3", "127.0.0.1", "codex")
        for path in (b"/chatgpt/backend-api/accounts/transfer", b"/chatgpt/backend-api/conversation",
                     b"/chatgpt/api/auth/session"):
            reply = self.request(b"GET " + path + b" HTTP/1.1\r\nAuthorization: Bearer " + grant.key.encode() +
                                 b"\r\n\r\n")
            self.assertTrue(reply.startswith(b"HTTP/1.1 403"), (path, reply))
        self.assertEqual(self.upstream.requests, [])
        for path in (b"/chatgpt/backend-api/wham/accounts/check", b"/chatgpt/backend-api/ps/plugins/list"):
            reply = self.request(b"GET " + path + b" HTTP/1.1\r\nAuthorization: Bearer " + grant.key.encode() +
                                 b"\r\n\r\n")
            self.assertTrue(reply.startswith(b"HTTP/1.1 200"), (path, reply))


class StepEnvTests(unittest.TestCase):
    def test_claude_env_points_at_proxy_with_dummy(self):
        grants = k3.GrantRegistry()
        grant = grants.issue("PT-1", "10.173.184.23", "claude")
        creds = k3.step_credentials(grant, "http://10.173.184.1:18080")
        self.assertEqual(creds.env, {"ANTHROPIC_BASE_URL": "http://10.173.184.1:18080/anthropic",
                                     "CLAUDE_CODE_OAUTH_TOKEN": grant.key})
        self.assertEqual((creds.files, creds.args), ({}, ()))
        self.assertTrue(grant.key.startswith("sk-ant-oat01-tide-k3-"))
        self.assertNotIn(grant.key, repr(grant))
        self.assertNotEqual(grants.issue("PT-1", "10.173.184.23", "claude").key, grant.key)

    def test_path_allowed_prefix_and_exact(self):
        route = k3.default_routes({"HOME": "/x"})["chatgpt"]
        self.assertTrue(k3.path_allowed(route, "/backend-api/codex/responses"))
        self.assertTrue(k3.path_allowed(route, "/backend-api/ps/mcp"))
        self.assertFalse(k3.path_allowed(route, "/backend-api/ps/mcpx"))
        self.assertFalse(k3.path_allowed(route, "/backend-api/codex/./responses"))
        self.assertFalse(k3.path_allowed(route, "/backend-api/codex/responses\n"))     # review N2: `$` przed `\n`
        self.assertTrue(k3.query_allowed("beta=true&a=%2F"))
        self.assertFalse(k3.query_allowed("a=1\nHost:x"))
        self.assertFalse(k3.query_allowed("a=1\n"))
        self.assertFalse(k3.path_allowed(replace(route, paths=()), "/backend-api/codex/responses"))


class NoSecretsInBoxTests(unittest.TestCase):
    def test_service_code_never_copies_host_credentials(self):
        """Kod pudełka nie kopiuje plików poświadczeń hosta — czyta je tylko k3.auth_headers/codex_account."""
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tide_daemon", "box")
        for name in os.listdir(root):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                text = fh.read()
            if name != "k3.py":
                self.assertNotIn(".credentials.json", text, name)
                self.assertNotIn("auth.json", text, name)
            # copyfileobj strumieniuje już otwarte archiwum migracji; nie otwiera pliku poświadczeń hosta.
            # Widok Git kopiuje wyłącznie HEAD/index z administracji projektu.
            # Nadal obowiązuje powyższy zakaz ścieżek poświadczeń we wszystkich modułach.
            if name != "gitview.py":
                self.assertNotRegex(text, r"\bcopyfile\b", name)


if __name__ == "__main__":
    unittest.main()
