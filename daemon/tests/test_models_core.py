"""Modele lokalne w tide-daemon bez Qt: models.json (wartości domyślne, dwukropki w nazwach modeli, zepsute
części, token z pliku), klient obu rodzajów serwera i stan serwerów — na atrapie serwera HTTP w wątku testu.

Prawdziwa Ollama (127.0.0.1:11434 na tym komputerze) nie jest pytana: każdy test podaje własną
konfigurację z adresem atrapy albo zamkniętego portu. Pliki tylko w katalogu tymczasowym.
"""
import http.server
import json
import os
import secrets
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tide_daemon.models import client, status
from tide_daemon.models import config as models_config
from tide_daemon.models.transport import ModelError

SCHEMA = {"type": "object", "properties": {"zrobione": {"type": "array", "items": {"type": "string"}}},
          "required": ["zrobione"]}
BIELIK = "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M"

OLLAMA_TAGS = {"models": [
    {"name": "qwen3:8b", "model": "qwen3:8b", "size": 5225388164,
     "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"}},
    {"name": "bge-m3:latest", "model": "bge-m3:latest", "size": 1157672605,
     "details": {"family": "bert", "parameter_size": "566.70M", "quantization_level": "F16"}},
    {"name": BIELIK, "model": BIELIK, "size": 6724052276, "details": {"family": "llama"}},
]}
OLLAMA_PS = {"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "size": 6000000000, "size_vram": 4500000000,
                         "context_length": 8192, "expires_at": "2026-10-27T17:05:00+01:00"}]}
OLLAMA_CHAT = {"model": "qwen3:8b", "message": {"role": "assistant", "content": '{"zrobione": ["model w daemonie"]}'},
               "done": True, "total_duration": 8_100_000_000, "load_duration": 1_500_000_000,
               "prompt_eval_count": 4512, "prompt_eval_duration": 2_000_000_000,
               "eval_count": 96, "eval_duration": 2_040_000_000}
OPENAI_CHAT = {"choices": [{"index": 0, "message": {"role": "assistant", "content": '{"zrobione": []}'}}],
               "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}}


class FakeModelServer(http.server.ThreadingHTTPServer):
    """Atrapa serwera modeli: odpowiedzi według (metoda, ścieżka); zapisuje zapytania z nagłówkiem i treścią.

    Odpowiedź to (kod, obiekt JSON albo surowe bajty[, nagłówki]) albo lista takich krotek — kolejne
    zapytania biorą kolejne pozycje (ostatnia zostaje). Zamiast treści może być funkcja, która sama
    pisze odpowiedź przez handler (serwer sączący bajty; treść zapytania w `handler.body`). `delay` opóźnia
    każdą odpowiedź (limit czasu); `serial` — zapytania obsługiwane po jednym, jak Ollama z
    OLLAMA_NUM_PARALLEL=1 (kolejne czeka na serwerze, aż poprzednie skończy)."""

    daemon_threads = True

    def __init__(self, routes):
        super().__init__(("127.0.0.1", 0), FakeModelHandler)
        self.routes = dict(routes)
        self.requests = []
        self.delay = 0.0
        self.serial = False
        self.lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def reply(self, method, path):
        route = self.routes.get((method, path))
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route or (404, {"error": "not found"})


class FakeModelHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.body = body
        if self.server.serial:
            with self.server.lock:
                return self._answer(body)
        return self._answer(body)

    def _answer(self, body):
        self.server.requests.append({"method": self.command, "path": self.path, "body": body,
                                     "auth": self.headers.get("Authorization")})
        if self.server.delay:
            time.sleep(self.server.delay)
        code, value, *extra = self.server.reply(self.command, self.path)
        if callable(value):
            return value(self)
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        try:
            self.send_response(code)
            for key, header in (extra[0] if extra else {}).items():
                self.send_header(key, header)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:  # klient zrezygnował (limit czasu)
            pass

    do_GET = _serve
    do_POST = _serve


def start_server(test, routes):
    server = FakeModelServer(routes)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def ollama_routes():
    return {("GET", "/api/version"): (200, {"version": "0.40.11"}), ("GET", "/api/tags"): (200, OLLAMA_TAGS),
            ("GET", "/api/ps"): (200, OLLAMA_PS), ("POST", "/api/chat"): (200, OLLAMA_CHAT)}


def trickle(handler):
    """Nagłówki od razu, potem po bajcie co 0,1 s przez 5 s — każdy odczyt mieści się w limicie gniazda."""
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        for _ in range(50):
            handler.wfile.write(b" ")
            handler.wfile.flush()
            time.sleep(0.1)
    except OSError:
        pass


def closed_port() -> int:
    """Port, na którym nikt nie słucha (zajęty i od razu zwolniony)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_models(folder, data) -> str:
    path = os.path.join(folder, "models.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))
    return path


def cfg_for(url, kind="ollama", token_file=None):
    """Jeden punkt `local` pod wskazanym adresem i role domyślne."""
    endpoint = {"url": url, "kind": kind, **({"tokenFile": token_file} if token_file else {})}
    return models_config.from_dict({"endpoints": {"local": endpoint}})


class TestEnvironmentTest(unittest.TestCase):
    def test_tests_never_reach_the_real_ollama(self):
        # daemon/tests/__init__.py: bez jawnej ścieżki ustawienia wskazują zamknięty port, nie 127.0.0.1:11434.
        cfg = models_config.load()
        self.assertTrue(cfg.exists, cfg.path)
        self.assertEqual({e.url for e in cfg.endpoints.values()}, {"http://127.0.0.1:9"})


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-models-config-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_without_file_one_local_ollama_and_default_roles(self):
        cfg = models_config.load(os.path.join(self.tmp, "brak.json"))
        self.assertEqual((cfg.exists, cfg.error, cfg.warnings, cfg.default), (False, "", (), "local"))
        local = cfg.endpoint()
        self.assertEqual((local.name, local.url, local.kind, local.token_file),
                         ("local", "http://127.0.0.1:11434", "ollama", ""))
        self.assertEqual(sorted(cfg.roles), ["describe", "summary"])
        for name in ("summary", "describe"):
            role = cfg.role(name)
            self.assertEqual((role.endpoint, role.model, role.ctx), ("local", "qwen3:8b", 8192))

    def test_agent_program_defaults_to_codex_and_bad_value_warns(self):
        """PT-39: program agenta na modelu lokalnym — `agentProgram`, domyślnie Codex; zła wartość nie psuje reszty."""
        self.assertEqual(models_config.load(os.path.join(self.tmp, "brak.json")).agent_program, "codex")
        self.assertEqual(models_config.from_dict({}).agent_program, "codex")
        self.assertEqual(models_config.from_dict({"agentProgram": None}).warnings, ())
        cfg = models_config.from_dict({"agentProgram": "claude", "roles": {"agent": "qwen3-coder:30b"}})
        self.assertEqual((cfg.agent_program, cfg.warnings), ("claude", ()))
        for value, shown in (("Claude", "Claude"), ("opencode", "opencode"), (1, "1"), (["codex"], '["codex"]')):
            with self.subTest(value=value):
                cfg = models_config.from_dict({"agentProgram": value, "roles": {"agent": "qwen3-coder:30b"}})
                self.assertEqual(cfg.agent_program, "codex")
                self.assertEqual(cfg.warnings, (f"agentProgram: nieznany program „{shown}” (codex albo claude) — "
                                                f"agent lokalny działa w Codexie",))
                self.assertEqual(cfg.roles["agent"].model, "qwen3-coder:30b", "rola agent działa dalej")
        state = status.pending(models_config.from_dict({"agentProgram": "claude"}))
        self.assertEqual(state["agentProgram"], "claude")
        self.assertEqual(status.pending(models_config.defaults())["agentProgram"], "codex")

    def test_config_path_from_environment(self):
        self.assertEqual(models_config.config_path({"XDG_CONFIG_HOME": "/x/conf", "HOME": "/h"}),
                         "/x/conf/tide/models.json")
        self.assertEqual(models_config.config_path({"HOME": "/h"}), "/h/.config/tide/models.json")
        self.assertEqual(models_config.config_path({"TIDE_MODELS_CONFIG": "/t/m.json", "XDG_CONFIG_HOME": "/x"}),
                         "/t/m.json")

    def test_plan_example_and_colons_in_model_names(self):
        cfg = models_config.load(write_models(self.tmp, {
            "default": "local",
            "endpoints": {"local": {"url": "http://127.0.0.1:11434/", "kind": "ollama"},
                          "mac": {"url": "http://mac-studio:11434", "kind": "ollama", "tokenFile": "~/mac.token"},
                          "lm": {"url": "http://mac-studio:1234/v1", "kind": "openai"}},
            "roles": {"summary": "local:qwen3:8b", "describe": {"model": "mac:qwen3:8b", "ctx": 4096},
                      "embed": "local:bge-m3", "agent": "local:qwen3-coder:30b", "polish": f"local:{BIELIK}",
                      "bare": "qwen3:8b", "bielik": BIELIK, "server": {"model": "local:qwen3:8b", "ctx": 0},
                      "mlx": "lm:mlx-community/Qwen3-8B-4bit"}}))
        self.assertEqual((cfg.error, cfg.warnings), ("", ()))
        roles = {name: (r.endpoint, r.model, r.ctx) for name, r in cfg.roles.items()}
        self.assertEqual(roles, {
            "summary": ("local", "qwen3:8b", 8192),            # rola raportu bez ctx — 8k (PT-13)
            "describe": ("mac", "qwen3:8b", 4096),
            "embed": ("local", "bge-m3", None),
            "agent": ("local", "qwen3-coder:30b", None),        # inne role — kontekst serwera
            "polish": ("local", BIELIK, None),                  # dzielone tylko na pierwszym dwukropku
            "bare": ("local", "qwen3:8b", None),                # „qwen3” to nie punkt — cały napis to model
            "bielik": ("local", BIELIK, None),
            "server": ("local", "qwen3:8b", None),              # ctx 0 — kontekst serwera
            "mlx": ("lm", "mlx-community/Qwen3-8B-4bit", None),
        })
        self.assertEqual(cfg.endpoint("local").url, "http://127.0.0.1:11434")
        self.assertEqual(cfg.endpoint("lm").url, "http://mac-studio:1234")  # /v1 dokłada klient
        self.assertEqual(cfg.endpoint("mac").host, "mac-studio:11434")

    def test_default_endpoint_carries_missing_default_roles(self):
        cfg = models_config.from_dict({"default": "mac", "endpoints": {
            "local": {"url": "http://127.0.0.1:11434"}, "mac": {"url": "http://mac-studio:11434"}},
            "roles": {"agent": "qwen3-coder:30b"}})
        self.assertEqual(cfg.endpoint().name, "mac")
        self.assertEqual({n: (r.endpoint, r.model) for n, r in cfg.roles.items()},
                         {"agent": ("mac", "qwen3-coder:30b"), "summary": ("mac", "qwen3:8b"),
                          "describe": ("mac", "qwen3:8b")})
        self.assertEqual(cfg.endpoint("local").kind, "ollama")  # rodzaj domyślny

    def test_broken_file_falls_back_to_defaults_with_error(self):
        for text, expected in (("{\"endpoints\": {", "niepoprawny JSON (linia 1)"),
                               ("[1, 2]", "oczekiwano obiektu JSON")):
            cfg = models_config.load(write_models(self.tmp, text))
            self.assertTrue(cfg.exists)
            self.assertIn(expected, cfg.error)
            self.assertEqual(cfg.endpoint().url, "http://127.0.0.1:11434")
            self.assertEqual(cfg.role("summary").model, "qwen3:8b")

    def test_bad_parts_are_skipped_with_warnings(self):
        cfg = models_config.from_dict({
            "default": "nie-ma",
            "endpoints": {"local": {"url": "http://127.0.0.1:11434"}, "ftp": {"url": "ftp://x"},
                          "bez": {"kind": "ollama"}, "zly": {"url": "http://x", "kind": "vllm"},
                          "token": {"url": "http://x", "tokenFile": 5}, "lista": [], "z spacją": {"url": "http://x"},
                          # Adresy, których nie da się rozebrać na host i port, i dane logowania w adresie.
                          "nawias": {"url": "http://[fd7a:115c::1:11434"}, "port-abc": {"url": "http://mac:abc"},
                          "port-duzy": {"url": "http://127.0.0.1:99999"}, "port-zero": {"url": "http://mac:0"},
                          "ipv6": {"url": "http://fd7a:115c:a1e0::1:11434"},
                          "haslo": {"url": "http://user:sekret@mac:11434"}},
            "roles": {"summary": 7, "describe": {"model": "local:qwen3:8b", "ctx": -1}, "pusta": "",
                      "flaga": {"model": "qwen3:8b", "ctx": True}, "ok": "local:qwen3:8b",
                      "agent": "nawias:qwen3-coder:30b"}})
        self.assertEqual(sorted(cfg.endpoints), ["local"])
        self.assertEqual(cfg.default, "local")
        self.assertEqual(len(cfg.warnings), 18, cfg.warnings)
        self.assertTrue(any("default: nieznany serwer „nie-ma”" in w for w in cfg.warnings))
        self.assertTrue(any("nieznany rodzaj „vllm”" in w for w in cfg.warnings))
        self.assertTrue(any("„haslo”: dane logowania w adresie" in w for w in cfg.warnings), cfg.warnings)
        self.assertNotIn("sekret", json.dumps(cfg.warnings))
        # Poprawna rola z punktem zostaje. Role bez punktu (także zepsute role domyślne, które wracają
        # z wartościami domyślnymi) nie idą cicho na „local”, bo `default` wskazuje nieznany punkt.
        self.assertEqual({n: (r.model, r.ctx) for n, r in cfg.roles.items() if not r.error}, {"ok": ("qwen3:8b", None)})
        self.assertEqual((cfg.roles["summary"].endpoint, cfg.roles["summary"].model, cfg.roles["summary"].ctx),
                         ("nie-ma", "qwen3:8b", 8192))
        self.assertIn("nieznany serwer „nie-ma”", cfg.roles["summary"].error)
        # Rola na pominiętym punkcie nie idzie cicho na `default` — jest niedostępna z powodem.
        agent = cfg.roles["agent"]
        self.assertEqual((agent.endpoint, agent.model), ("nawias", "qwen3-coder:30b"))
        self.assertTrue(any("rola „agent”: serwer „nawias” jest pominięty" in w for w in cfg.warnings), cfg.warnings)
        with self.assertRaises(ModelError) as caught:
            cfg.role("agent")
        self.assertIn("serwer „nawias” jest pominięty", str(caught.exception))
        with self.assertRaises(ModelError):
            client.chat("x", role="agent", config=cfg)

    def test_default_must_be_a_name(self):
        for value in (["mac"], {}, 5, None):
            cfg = models_config.from_dict({"default": value, "endpoints": {"local": {"url": "http://127.0.0.1:11434"}},
                                           "roles": {"ok": "local:qwen3:8b"}})
            self.assertEqual(cfg.default, "local")
            self.assertEqual(len(cfg.warnings), 0 if value is None else 1, cfg.warnings)
            # Zły `default`: role bez punktu niedostępne, role z punktem działają.
            self.assertEqual(bool(cfg.roles["summary"].error), value is not None)
            self.assertFalse(cfg.roles["ok"].error)

    def test_default_on_skipped_endpoint_does_not_move_roles_to_local(self):
        cfg = models_config.from_dict({"default": "mac",
                                       "endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                                     "mac": {"url": "http://mac:abc"}},
                                       "roles": {"agent": "qwen3-coder:30b", "ok": "local:qwen3:8b"}})
        self.assertEqual(cfg.default, "local")  # tylko do odczytu stanu
        for name in ("summary", "describe", "agent"):
            role = cfg.roles[name]
            self.assertEqual(role.endpoint, "mac", name)
            self.assertIn("serwer „mac” jest pominięty", role.error)
        self.assertEqual(cfg.roles["agent"].model, "qwen3-coder:30b")
        self.assertFalse(cfg.roles["ok"].error)
        self.assertTrue(any("role bez serwera niedostępne" in w for w in cfg.warnings), cfg.warnings)
        with self.assertRaises(ModelError):
            client.chat("x", role="summary", config=cfg)

    def test_default_role_on_skipped_endpoint_stays_there(self):
        cfg = models_config.from_dict({"endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                                     "mac": {"url": "http://mac:abc"}},
                                       "roles": {"summary": "mac:qwen3:8b"}})
        self.assertEqual(cfg.roles["summary"].endpoint, "mac")  # nie „local” — rola niedostępna
        self.assertTrue(cfg.roles["summary"].error)
        self.assertEqual(cfg.roles["describe"].endpoint, "local")

    def test_unexpected_failure_while_reading_gives_defaults(self):
        path = write_models(self.tmp, {"endpoints": {}})
        with mock.patch.object(models_config, "from_dict", side_effect=RuntimeError("boom")):
            cfg = models_config.load(path)
        self.assertIn("RuntimeError", cfg.error)
        self.assertEqual(cfg.endpoint().name, "local")

    def test_no_valid_endpoint_means_default_local(self):
        cfg = models_config.from_dict({"endpoints": {"mac": {"url": "mac-studio"}}, "roles": "nie"})
        self.assertEqual(list(cfg.endpoints), ["local"])
        self.assertEqual(len(cfg.warnings), 3, cfg.warnings)
        self.assertEqual(sorted(cfg.roles), ["describe", "summary"])

    def test_unknown_role_and_endpoint_raise_model_error(self):
        cfg = models_config.from_dict({})
        with self.assertRaises(ModelError) as caught:
            cfg.role("embed")
        self.assertIn("nieznana rola „embed”", str(caught.exception))
        with self.assertRaises(ModelError):
            cfg.endpoint("mac")

    def test_token_file_with_home_expansion_becomes_bearer_header(self):
        token = "tok-" + secrets.token_hex(12)
        with open(os.path.join(self.tmp, "mac.token"), "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            cfg = models_config.from_dict({"endpoints": {"mac": {"url": "http://mac:11434",
                                                                 "tokenFile": "~/mac.token"}}})
        endpoint = cfg.endpoint("mac")
        self.assertEqual(endpoint.token_file, os.path.join(self.tmp, "mac.token"))
        self.assertEqual(endpoint.headers()["Authorization"], f"Bearer {token}")
        self.assertNotIn(token, json.dumps(cfg.public()))
        self.assertNotIn(token, repr(endpoint))

    def test_missing_or_empty_token_file_is_a_model_error(self):
        empty = os.path.join(self.tmp, "empty.token")
        open(empty, "w").close()
        for path in (os.path.join(self.tmp, "brak.token"), empty):
            endpoint = models_config.Endpoint("mac", "http://mac:11434", "ollama", path)
            with self.assertRaises(ModelError) as caught:
                endpoint.headers()
            self.assertIn("tokenu serwera „mac”", str(caught.exception))

    def test_ipv6_address_in_brackets(self):
        endpoint = models_config.from_dict({"endpoints": {"mac": {"url": "http://[fd7a:115c::1]:11434"}}}) \
            .endpoint("mac")
        self.assertEqual((endpoint.host, endpoint.public_url()), ("[fd7a:115c::1]:11434", "http://[fd7a:115c::1]:11434"))

    def test_token_must_be_printable_ascii(self):
        path = os.path.join(self.tmp, "pl.token")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("zażółć-gęślą")
        endpoint = models_config.Endpoint("mac", "http://127.0.0.1:9", "ollama", path)
        with self.assertRaises(ModelError) as caught:
            endpoint.headers()
        self.assertIn("niepoprawny plik tokenu serwera „mac”", str(caught.exception))
        self.assertNotIn("zażółć", str(caught.exception))


class OllamaClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-models-client-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def server(self):
        return start_server(self, ollama_routes())

    def test_role_answer_with_schema_num_ctx_and_metrics(self):
        server = self.server()
        answer = client.chat("Streść dzień.", role="summary", system="Piszesz po polsku.", schema=SCHEMA,
                             config=cfg_for(server.url))
        self.assertEqual(answer.text, OLLAMA_CHAT["message"]["content"])
        self.assertEqual(answer.json(), {"zrobione": ["model w daemonie"]})
        self.assertEqual((answer.endpoint, answer.model), ("local", "qwen3:8b"))
        metrics = dict(answer.metrics)
        self.assertGreaterEqual(metrics.pop("wallS"), 0)
        self.assertGreaterEqual(metrics.pop("queueS"), 0)   # czekanie w kolejce modeli — osobno od wywołania
        self.assertEqual(metrics, {"promptTokens": 4512, "outputTokens": 96, "loadS": 1.5, "promptS": 2.0,
                                   "outputS": 2.04})
        [sent] = server.requests
        self.assertEqual((sent["method"], sent["path"], sent["auth"]), ("POST", "/api/chat", None))
        self.assertEqual(sent["body"], {
            "model": "qwen3:8b", "stream": False, "think": False, "options": {"num_ctx": 8192}, "format": SCHEMA,
            "messages": [{"role": "system", "content": "Piszesz po polsku."},
                         {"role": "user", "content": "Streść dzień."}]})

    def test_explicit_model_without_ctx_uses_server_context(self):
        server = self.server()
        messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                    {"role": "user", "content": "c"}]
        client.chat(messages, endpoint="local", model=BIELIK, config=cfg_for(server.url))
        body = server.requests[0]["body"]
        self.assertEqual(body["model"], BIELIK)
        self.assertEqual(body["messages"], messages)
        self.assertNotIn("options", body)
        self.assertNotIn("format", body)
        client.chat("x", model="qwen3:8b", ctx=2048, config=cfg_for(server.url))  # punkt domyślny, ctx jawnie
        self.assertEqual(server.requests[1]["body"]["options"], {"num_ctx": 2048})

    def test_model_without_thinking_switch_is_retried_without_think(self):
        server = self.server()
        server.routes[("POST", "/api/chat")] = [(400, {"error": '"gpt-oss:20b" does not support thinking'}),
                                               (200, OLLAMA_CHAT)]
        answer = client.chat("x", model="gpt-oss:20b", config=cfg_for(server.url))
        self.assertEqual(answer.model, "gpt-oss:20b")
        self.assertEqual(len(server.requests), 2)
        self.assertIs(server.requests[0]["body"]["think"], False)
        self.assertNotIn("think", server.requests[1]["body"])

    def test_other_bad_request_is_not_retried(self):
        server = self.server()
        server.routes[("POST", "/api/chat")] = (400, {"error": "invalid format schema"})
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg_for(server.url))
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(len(server.requests), 1)
        self.assertIn("invalid format schema", str(caught.exception))

    def test_http_errors_give_short_polish_message(self):
        server = self.server()
        host = server.url.removeprefix("http://")
        cases = [((500, {"error": "llama runner process has terminated"}),
                  f"Model lokalny: serwer odpowiedział 500: llama runner process has terminated ({host})"),
                 ((404, {"error": "model 'qwen3:8b' not found"}),
                  f"Model lokalny: serwer odpowiedział 404: model 'qwen3:8b' not found ({host})"),
                 ((502, b"<html>Bad Gateway</html>"), f"Model lokalny: serwer odpowiedział 502: "
                                                       f"<html>Bad Gateway</html> ({host})")]
        for reply, message in cases:
            server.routes[("POST", "/api/chat")] = reply
            with self.assertRaises(ModelError) as caught:
                client.chat("x", role="summary", config=cfg_for(server.url))
            self.assertEqual(str(caught.exception), message)

    def test_unreadable_or_empty_answer(self):
        server = self.server()
        host = server.url.removeprefix("http://")
        for reply, expected in (((200, b"to nie json"), f"nieczytelna odpowiedź serwera ({host})"),
                                ((200, b'"napis"'), "nieczytelna odpowiedź serwera"),
                                ((200, {"message": {"content": "  "}}), "pusta odpowiedź modelu qwen3:8b"),
                                ((200, {"done": True}), "pusta odpowiedź modelu")):
            server.routes[("POST", "/api/chat")] = reply
            with self.assertRaises(ModelError) as caught:
                client.chat("x", role="summary", config=cfg_for(server.url))
            self.assertIn(expected, str(caught.exception))
        server.routes[("POST", "/api/chat")] = (200, {"message": {"content": "zwykły tekst"}})
        answer = client.chat("x", role="summary", config=cfg_for(server.url))
        with self.assertRaises(ModelError) as caught:
            answer.json()
        self.assertIn("nie jest poprawnym JSON-em", str(caught.exception))

    def test_server_down(self):
        port = closed_port()
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg_for(f"http://127.0.0.1:{port}"))
        self.assertEqual(str(caught.exception), f"Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:{port})")
        self.assertIsNone(caught.exception.status)

    def test_timeout(self):
        server = self.server()
        server.delay = 1.0
        started = time.monotonic()
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", timeout=0.2, config=cfg_for(server.url))
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(str(caught.exception),
                         f"Model lokalny nie odpowiedział w 0.2 s ({server.url.removeprefix('http://')})")

    def test_cancel_from_another_thread_ends_the_call_at_once(self):
        from tide_daemon.models import Cancel
        server = self.server()
        server.delay = 5.0
        cancel = Cancel()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", timeout=30, config=cfg_for(server.url), cancel=cancel)
        self.assertLess(time.monotonic() - started, 1.5)
        host = server.url.removeprefix("http://")
        self.assertEqual(str(caught.exception), f"Model lokalny: wywołanie przerwane — usługa się zamyka ({host})")
        count = len(server.requests)
        with self.assertRaises(ModelError):
            client.chat("x", role="summary", config=cfg_for(server.url), cancel=cancel)
        self.assertEqual(len(server.requests), count, "po przerwaniu nowe zapytania nie startują")

    def test_token_goes_only_to_the_header(self):
        server = self.server()
        token = "tok-" + secrets.token_hex(12)
        path = os.path.join(self.tmp, "token")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token)
        cfg = cfg_for(server.url, token_file=path)
        client.chat("x", role="summary", config=cfg)
        self.assertEqual(server.requests[0]["auth"], f"Bearer {token}")
        server.routes[("POST", "/api/chat")] = (401, {"error": "unauthorized"})
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg)
        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(token, json.dumps(status.snapshot(cfg, timeout=1)))

    def test_redirect_is_not_followed_and_token_stays(self):
        other = self.server()
        token = "tok-" + secrets.token_hex(12)
        path = os.path.join(self.tmp, "token")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token)
        first = start_server(self, {
            ("POST", "/api/chat"): (302, b"", {"Location": other.url + "/api/chat"}),
            ("GET", "/api/version"): (307, b"", {"Location": other.url + "/api/version"})})
        cfg = cfg_for(first.url, token_file=path)
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg)
        self.assertEqual(str(caught.exception),
                         f"Model lokalny: serwer odpowiedział 302 — przekierowanie nie jest obsługiwane "
                         f"({first.url.removeprefix('http://')})")
        local = status.snapshot(cfg, timeout=2)["endpoints"]["local"]
        self.assertFalse(local["online"])
        self.assertIn("307", local["error"])
        self.assertEqual(other.requests, [], "drugi serwer nie dostał zapytania ani tokenu")
        self.assertEqual({r["auth"] for r in first.requests}, {f"Bearer {token}"})

    def test_one_deadline_for_the_whole_request(self):
        server = self.server()
        server.routes[("POST", "/api/chat")] = (200, trickle)
        started = time.monotonic()
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", timeout=0.5, config=cfg_for(server.url))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("nie odpowiedział w 0.5 s", str(caught.exception))

    def test_slow_name_lookup_counts_into_the_deadline(self):
        real = socket.getaddrinfo

        def slow(host, *args, **kwargs):
            if host == "wolny-dns.test":
                time.sleep(3)
            return real(host, *args, **kwargs)

        cfg = models_config.from_dict({"endpoints": {"local": {"url": "http://wolny-dns.test:11434"}}})
        started = time.monotonic()
        with mock.patch("socket.getaddrinfo", slow):
            with self.assertRaises(ModelError) as caught:
                client.chat("x", role="summary", timeout=0.3, config=cfg)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("nie odpowiedział w 0.3 s (wolny-dns.test:11434)", str(caught.exception))

    def test_proxy_from_environment_is_not_used(self):
        server = self.server()
        bogus = f"http://127.0.0.1:{closed_port()}"
        with mock.patch.dict(os.environ, {"http_proxy": bogus, "HTTP_PROXY": bogus, "no_proxy": "", "NO_PROXY": ""}):
            self.assertTrue(client.chat("x", role="summary", config=cfg_for(server.url)).text)

    def test_role_or_model_required_and_unknown_role(self):
        with self.assertRaises(ValueError):
            client.chat("x", config=models_config.from_dict({}))
        with self.assertRaises(ModelError):
            client.chat("x", role="nie-ma", config=models_config.from_dict({}))

    def test_config_is_read_at_call_time(self):
        server = self.server()
        path = write_models(self.tmp, {"endpoints": {"local": {"url": server.url}},
                                       "roles": {"summary": "local:qwen3:4b"}})
        with mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": path}):
            client.chat("x", role="summary")
            write_models(self.tmp, {"endpoints": {"local": {"url": server.url}},
                                    "roles": {"summary": {"model": f"local:{BIELIK}", "ctx": 4096}}})
            client.chat("x", role="summary")
        self.assertEqual([(r["body"]["model"], r["body"]["options"]["num_ctx"]) for r in server.requests],
                         [("qwen3:4b", 8192), (BIELIK, 4096)])


class OpenAIClientTest(unittest.TestCase):
    def test_chat_completions_with_json_schema(self):
        server = start_server(self, {("POST", "/v1/chat/completions"): (200, OPENAI_CHAT)})
        cfg = models_config.from_dict({"endpoints": {"lm": {"url": server.url + "/v1/", "kind": "openai"}},
                                       "default": "lm", "roles": {"summary": "lm:qwen3-8b-mlx"}})
        answer = client.chat("x", role="summary", system="s", schema=SCHEMA, config=cfg)
        self.assertEqual(answer.json(), {"zrobione": []})
        self.assertEqual({k: v for k, v in answer.metrics.items() if k not in ("wallS", "queueS")},
                         {"promptTokens": 120, "outputTokens": 7})
        [sent] = server.requests
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["body"], {
            "model": "qwen3-8b-mlx", "stream": False,
            "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "answer", "strict": True, "schema": SCHEMA}}})
        self.assertNotIn("num_ctx", json.dumps(sent["body"]))  # kontekst ustawia serwer

    def test_errors(self):
        server = start_server(self, {("POST", "/v1/chat/completions"): (500, {"error": {"message": "OOM"}})})
        cfg = cfg_for(server.url, kind="openai")
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg)
        self.assertIn("serwer odpowiedział 500: OOM", str(caught.exception))
        server.routes[("POST", "/v1/chat/completions")] = (200, {"choices": []})
        with self.assertRaises(ModelError) as caught:
            client.chat("x", role="summary", config=cfg)
        self.assertIn("pusta odpowiedź", str(caught.exception))


class StatusTest(unittest.TestCase):
    def test_ollama_online_with_models_loaded_share_and_roles(self):
        server = start_server(self, ollama_routes())
        cfg = models_config.from_dict({"endpoints": {"local": {"url": server.url}},
                                       "roles": {"embed": "local:bge-m3", "polish": f"local:{BIELIK}",
                                                 "agent": "local:qwen3-coder:30b"}})
        state = status.snapshot(cfg, timeout=2)
        local = state["endpoints"]["local"]
        self.assertEqual((local["online"], local["error"], local["version"], local["kind"], local["auth"]),
                         (True, "", "0.40.11", "ollama", False))
        self.assertEqual([m["name"] for m in local["models"]], ["bge-m3:latest", BIELIK, "qwen3:8b"])
        self.assertEqual(local["models"][2], {"name": "qwen3:8b", "size": 5225388164, "family": "qwen3",
                                              "parameterSize": "8.2B", "quantization": "Q4_K_M"})
        self.assertEqual(local["loaded"], [{"name": "qwen3:8b", "size": 6000000000, "sizeVram": 4500000000,
                                            "gpuPercent": 75, "contextLength": 8192,
                                            "expiresAt": "2026-10-27T17:05:00+01:00"}])
        roles = {n: (r["installed"], r["available"], r["ctx"]) for n, r in state["roles"].items()}
        self.assertEqual(roles, {"embed": (True, True, None), "polish": (True, True, None),
                                 "agent": (False, False, None), "summary": (True, True, 8192),
                                 "describe": (True, True, 8192)})
        self.assertTrue(state["checked"])
        self.assertEqual(state["config"], {"path": "", "exists": True, "error": "", "warnings": []})
        # Tylko odczyt: żadne zapytanie nie ładuje modelu.
        self.assertEqual([(r["method"], r["path"]) for r in server.requests],
                         [("GET", "/api/version"), ("GET", "/api/tags"), ("GET", "/api/ps")])

    def test_offline_endpoint_reports_error_and_stops_asking(self):
        port = closed_port()
        cfg = cfg_for(f"http://127.0.0.1:{port}")
        started = time.monotonic()
        state = status.snapshot(cfg, timeout=2)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(state["endpoints"]["local"], {
            "name": "local", "url": f"http://127.0.0.1:{port}", "kind": "ollama", "auth": False, "online": False,
            "error": f"Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:{port})", "version": None,
            "models": [], "loaded": []})
        self.assertEqual(state["roles"]["summary"], {"endpoint": "local", "model": "qwen3:8b", "ctx": 8192,
                                                     "installed": None, "available": False, "error": "",
                                                     "pinned": False})

    def test_server_failing_after_version_is_offline(self):
        server = start_server(self, ollama_routes())
        server.routes[("GET", "/api/tags")] = (500, {"error": "boom"})
        local = status.snapshot(cfg_for(server.url), timeout=2)["endpoints"]["local"]
        self.assertFalse(local["online"])
        self.assertIn("500: boom", local["error"])
        self.assertEqual((local["models"], local["loaded"], local["version"]), ([], [], None))

    def test_unexpected_error_in_one_endpoint_does_not_break_snapshot(self):
        server = start_server(self, ollama_routes())
        cfg = models_config.from_dict({"endpoints": {"local": {"url": server.url}, "mac": {"url": server.url}}})
        real = status.request

        def broken(endpoint, *args, **kwargs):
            if endpoint.name == "mac":
                raise ValueError("coś nieprzewidzianego")
            return real(endpoint, *args, **kwargs)

        with mock.patch.object(status, "request", broken):
            state = status.snapshot(cfg, timeout=2)
        self.assertTrue(state["endpoints"]["local"]["online"])
        self.assertFalse(state["endpoints"]["mac"]["online"])
        self.assertIn("ValueError", state["endpoints"]["mac"]["error"])

    def test_hanging_server_does_not_hang_status(self):
        server = start_server(self, ollama_routes())
        server.delay = 2.0
        started = time.monotonic()
        local = status.snapshot(cfg_for(server.url), timeout=0.2)["endpoints"]["local"]
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("nie odpowiedział w 0.2 s", local["error"])

    def test_openai_server_lists_models_without_loaded_info(self):
        server = start_server(self, {("GET", "/v1/models"): (200, {"object": "list", "data": [
            {"id": "qwen3-8b-mlx", "object": "model"}, {"id": "bielik", "object": "model"}, {"bad": 1}]})})
        cfg = models_config.from_dict({"default": "lm", "endpoints": {"lm": {"url": server.url, "kind": "openai"}},
                                       "roles": {"summary": "qwen3-8b-mlx", "describe": "inny"}})
        state = status.snapshot(cfg, timeout=2)
        lm = state["endpoints"]["lm"]
        self.assertEqual((lm["online"], lm["version"], lm["loaded"]), (True, None, None))
        self.assertEqual(lm["models"], [{"name": "bielik"}, {"name": "qwen3-8b-mlx"}])
        self.assertEqual((state["roles"]["summary"]["available"], state["roles"]["describe"]["installed"]),
                         (True, False))
        self.assertEqual([(r["method"], r["path"]) for r in server.requests], [("GET", "/v1/models")])

    def test_openai_offline(self):
        lm = status.snapshot(cfg_for(f"http://127.0.0.1:{closed_port()}", kind="openai"), timeout=1)["endpoints"]["local"]
        self.assertEqual((lm["online"], lm["loaded"], lm["models"]), (False, None, []))
        self.assertIn("serwer nie odpowiada", lm["error"])

    def test_read_takes_config_now_and_reports_file_problems(self):
        tmp = tempfile.mkdtemp(prefix="tide-models-status-")
        self.addCleanup(shutil.rmtree, tmp)
        server = start_server(self, ollama_routes())
        path = write_models(tmp, {"endpoints": {"local": {"url": server.url}, "zly": {"url": "x"}}})
        state = status.read(path, timeout=2)
        self.assertTrue(state["endpoints"]["local"]["online"])
        self.assertEqual(len(state["config"]["warnings"]), 1)
        write_models(tmp, "{zepsuty")
        closed = f"http://127.0.0.1:{closed_port()}"
        with mock.patch.object(models_config, "DEFAULT_URL", closed):  # nie prawdziwa Ollama z tego komputera
            state = status.read(path, timeout=0.5)
        self.assertIn("niepoprawny JSON", state["config"]["error"])
        self.assertEqual(state["endpoints"]["local"]["url"], closed)
        self.assertFalse(state["endpoints"]["local"]["online"])

    def test_pending_state_before_first_read(self):
        state = status.pending(models_config.from_dict({}))
        self.assertFalse(state["checked"])
        self.assertEqual(state["endpoints"]["local"]["online"], False)
        self.assertEqual(state["roles"]["summary"]["installed"], None)

    def test_model_key(self):
        self.assertEqual(status.model_key("bge-m3"), "bge-m3:latest")
        self.assertEqual(status.model_key("qwen3:8b"), "qwen3:8b")
        self.assertEqual(status.model_key("hf.co/x/Bielik-GGUF"), "hf.co/x/Bielik-GGUF:latest")
        self.assertEqual(status.model_key(BIELIK), BIELIK)


if __name__ == "__main__":
    unittest.main()
