"""Rozpoznawanie pytań, drugi stopień (PT-42): `doubtful` wskazuje wątpliwe końcówki, model roli `classify` je ocenia.

Bez prawdziwej Ollamy: atrapa serwera HTTP w wątku testu (test_models_core.py) i własny models.json w katalogu
tymczasowym. Obejmuje: reguły `doubtful`, ocenę w tle i pamięć werdyktów (także po restarcie), błędy modelu
(sam rachunek reguł), role (bez modelu, nieznany punkt, punkt w chmurze — decyzja 70), kolejkę (ocena przed
raportem, bez zagłodzenia), stan sesji i usługę na prywatnej magistrali D-Bus.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from tide_daemon.agents import AgentScanner
from tide_daemon.agents import classify
from tide_daemon.agents.claude import escape_cwd
from tide_daemon.agents.question import QUESTION_MAX, closing, doubtful, ending_question
from tide_daemon.models import client
from tide_daemon.models import config as models_config
from tide_daemon.models import queue as queue_mod
from tide_daemon.models.transport import ModelError

from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_models_core import OLLAMA_CHAT, start_server, write_models
from .test_models_queue import Blocker, wait_for

SELECT = ("Jasne. Do tej pory baza była tylko czytana.\n\n**Krok 1:** uruchom ten select i wklej mi wynik:\n\n"
          "```sql\nSELECT * FROM messages WHERE id = 5;\n```")
RULES_YES = "Zapisałem odpowiedź.\n\nPytanie 10: skąd biorą się czat i alerty? Czy donacje są w planach na start?"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def verdict_answer(waits: bool, seen=None):
    """Trasa atrapy: odpowiedź modelu {"waits": …}; `seen` dostaje treść zapytań (kolejność wywołań)."""
    def reply(handler):
        body = handler.body or {}
        if seen is not None:
            seen.append(body["messages"][-1]["content"])
        data = json.dumps({"model": body.get("model"), "done": True,
                           "message": {"role": "assistant", "content": json.dumps({"waits": waits})}}).encode()
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:  # klient zrezygnował (limit czasu)
            pass
    return (200, reply)


def chat_server(test, waits=True, seen=None):
    return start_server(test, {("POST", "/api/chat"): verdict_answer(waits, seen)})


class DoubtfulTest(unittest.TestCase):
    def test_doubtful_endings(self):
        for text in (SELECT, "Daj znać.", "Zrobiłem migrację. Dwie rzeczy: A i B. Daj znać.",
                     "Co się zmieniło? Dodałem pole daty. Testy przechodzą, review APPROVE.",
                     "Zrobione.\n\n1. Uruchom `make test`.\n2. Wklej wynik.",
                     "Teraz uruchom ten skrypt, proszę.",
                     "Wdrożone na stagingu. Jeśli wolisz inny termin, powiedz.",
                     "Dwie drogi, polecam pierwszą. To Twoja decyzja.",
                     "Zmiana jest gotowa i czeka na Ciebie w zakładce zmian.",
                     "Zrobione. Kliknij „Zatwierdź” w panelu."):
            self.assertTrue(doubtful(text), text)

    def test_not_doubtful(self):
        for text in ("Gotowe.", "Jeśli chcesz, mogę też dodać testy.", "Gotowe. Jeśli chcesz, mogę też dodać testy.",
                     "Agent PT-33 pracuje w tle. Dam znać, gdy skończę.", "dam znać, gdy skończę", "",
                     "Scalone do mastera i zainstalowane. Testy przechodzą: 1071 OK.",
                     "Stan kart: wszystko pracuje. Teraz nic nie czeka na Ciebie.",
                     "Dwa warianty, wybrałem pierwszy. Przegląd zostaje Twoją decyzją, ale to już zrobione."):
            self.assertFalse(doubtful(text), text)

    def test_rule_yes_is_never_doubtful(self):
        # Reguła „tak” rozstrzyga sama — model nie jest pytany.
        for text in (RULES_YES, "Gotowe.\n\nPushować?", "Spec gotowa. Czekam na Twoją odpowiedź."):
            self.assertTrue(ending_question(text), text)
            self.assertFalse(doubtful(text), text)

    def test_closing_is_the_last_sentences_and_clipped(self):
        self.assertEqual(closing(SELECT), "Krok 1: uruchom ten select i wklej mi wynik:")
        self.assertIn("uruchom ten select i wklej mi wynik", closing(SELECT))
        self.assertLessEqual(len(closing("Daj znać " + "x" * 600 + ".")), QUESTION_MAX)
        self.assertEqual(closing(""), "")


class ClassifierBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-classify-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state = os.path.join(self.tmp, "state", "classify.json")
        self.logs = []
        patch = mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": os.path.join(self.tmp, "models.json")})
        patch.start()
        self.addCleanup(patch.stop)

    def models(self, **data) -> str:
        return write_models(self.tmp, data)

    def classifier(self, **kw) -> classify.Classifier:
        kw.setdefault("path", self.state)
        kw.setdefault("log", self.logs.append)
        made = classify.Classifier(**kw)
        self.addCleanup(made.close)
        return made

    def settle(self, made):
        self.assertTrue(wait_for(made.idle, 10), "ocena nie skończyła się")

    def single(self, server, role="local:qwen3:8b"):
        return self.models(endpoints={"local": {"url": server.url, "kind": "ollama"}}, roles={"classify": role})


class ClassifierTest(ClassifierBase):
    def test_judge_never_waits_for_the_model_and_remembers_the_verdict(self):
        server = chat_server(self, waits=True)
        server.delay = 0.6
        self.single(server)
        results = []
        made = self.classifier(on_result=lambda: results.append(1))
        started = time.monotonic()
        self.assertIsNone(made.judge("claude:u1", SELECT), "jeszcze nie wiadomo")
        self.assertLess(time.monotonic() - started, 0.3, "judge nie czeka na model")
        self.assertIsNone(made.judge("claude:u1", SELECT), "ocena w toku — bez drugiego wywołania")
        self.settle(made)
        self.assertEqual((made.judge("claude:u1", SELECT), results, len(server.requests)), (True, [1], 1))
        body = server.requests[0]["body"]
        self.assertEqual((body["think"], body["options"]["temperature"], body["format"], body["model"]),
                         (False, 0, classify.SCHEMA, "qwen3:8b"))
        self.assertIn("uruchom ten select", body["messages"][-1]["content"])

    def test_same_message_is_judged_once_also_after_restart(self):
        server = chat_server(self, waits=True)
        self.single(server)
        first = self.classifier()
        first.judge("claude:u1", SELECT)
        self.settle(first)
        self.assertEqual(len(server.requests), 1)
        saved = json.load(open(self.state, encoding="utf-8"))
        self.assertEqual(saved["results"]["claude:u1"]["waits"], True)
        second = self.classifier()      # restart usługi: nowy obiekt, ten sam plik stanu
        self.assertIs(second.judge("claude:u1", SELECT), True, "werdykt z pliku od ręki")
        self.assertEqual((second.calls, len(server.requests)), (0, 1))

    def test_state_file_is_pruned_to_the_newest(self):
        server = chat_server(self, waits=False)
        self.single(server)
        clock = iter(range(1, 100))
        with mock.patch.object(classify, "MAX_RESULTS", 3):
            made = self.classifier(clock=lambda: next(clock))
            for i in range(5):
                made.judge(f"claude:u{i}", f"Daj znać {i}.")
                self.settle(made)
        saved = json.load(open(self.state, encoding="utf-8"))["results"]
        self.assertEqual(sorted(saved), ["claude:u2", "claude:u3", "claude:u4"])

    def test_broken_state_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.state))
        with open(self.state, "w", encoding="utf-8") as fh:
            fh.write("{nie json")
        server = chat_server(self, waits=True)
        self.single(server)
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertIs(made.verdict("claude:u1"), True)

    def test_model_down_bad_json_and_timeout_leave_the_rules(self):
        cases = {}
        down = self.models(endpoints={"local": {"url": "http://127.0.0.1:9", "kind": "ollama"}})
        cases["bez Ollamy"] = down
        bad = start_server(self, {("POST", "/api/chat"): (200, {"message": {"role": "assistant", "content": "to nie json"}})})
        cases["zły JSON"] = self.single(bad)
        wrong = start_server(self, {("POST", "/api/chat"): (200, {"message": {"role": "assistant",
                                                                               "content": '{"waits": "tak"}'}})})
        cases["zły kształt"] = self.single(wrong)
        slow = chat_server(self, waits=True)
        slow.delay = 1.5
        cases["limit czasu"] = self.single(slow)
        for name, path in cases.items():
            with self.subTest(name), mock.patch.object(classify, "TIMEOUT_S", 0.4), \
                    mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": path}):
                self.logs.clear()
                made = self.classifier(path=os.path.join(self.tmp, f"{name}.json"))
                self.assertIsNone(made.judge("claude:u1", SELECT))
                self.settle(made)
                self.assertIsNone(made.judge("claude:u1", SELECT), "bez werdyktu")
                self.assertIsNone(made.verdict("claude:u1"))
                self.assertEqual(len(self.logs), 1, self.logs)
                self.assertIn("liczą same reguły", self.logs[0])
                self.assertEqual(made.calls, 1, "po błędzie ta sama wiadomość nie wraca od razu")
                self.assertFalse(os.path.exists(made.path), "błąd nie zapisuje werdyktu")

    def test_failed_message_comes_back_after_the_retry_time(self):
        server = chat_server(self, waits=True)
        self.single(server)
        now = [1000.0]
        flaky = [ModelError("Model lokalny niedostępny: serwer nie odpowiada")]

        def chat(*args, **kw):
            if flaky:
                raise flaky.pop()
            return client.chat(*args, **kw)

        made = self.classifier(chat=chat, monotonic=lambda: now[0])
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertIsNone(made.judge("claude:u1", SELECT))
        self.assertTrue(made.idle(), "w czasie przerwy nic nie jest zaplanowane")
        now[0] += classify.RETRY_S + 1
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertIs(made.verdict("claude:u1"), True)


class RolesTest(ClassifierBase):
    def test_default_role_is_qwen_on_the_default_point(self):
        role = models_config.defaults().roles["classify"]
        self.assertEqual((role.endpoint, role.model, role.pinned, role.error), ("local", "qwen3:8b", False, ""))

    def test_role_without_model_or_with_skipped_point_is_off(self):
        server = chat_server(self)
        # Punkt pominięty (zły adres) — rola niedostępna z powodem, serwera nikt nie pyta.
        path = self.models(endpoints={"local": {"url": server.url, "kind": "ollama"}, "bad": {"url": "nie-adres"}},
                           roles={"classify": "bad:qwen3:8b"})
        self.assertTrue(models_config.load(path).roles["classify"].error)
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertEqual((made.verdict("claude:u1"), len(server.requests), made.calls), (None, 0, 0))
        self.assertEqual(len(self.logs), 1)
        self.assertIn("ocena modelem wyłączona", self.logs[0])
        self.assertIn("liczą same reguły", self.logs[0])
        # Rola bez modelu i rola z punktem spoza ustawień (ręcznie zbudowane ustawienia).
        cfg = models_config.defaults()
        role = cfg.roles["classify"]
        for broken, word in ((models_config.Role("classify", "local", " ", None), "bez modelu"),
                             (models_config.Role("classify", "ghost", "qwen3:8b", None), "nieznany serwer")):
            roles = dict(cfg.roles, classify=broken)
            problem = classify.Classifier.unavailable(models_config.ModelsConfig(cfg.default, cfg.endpoints, roles))
            self.assertIn(word, problem)
        self.assertEqual(classify.Classifier.unavailable(cfg), "")
        self.assertEqual(role.model, "qwen3:8b")

    def test_cloud_model_without_explicit_point_is_off(self):
        server = chat_server(self)
        self.single(server, role="gpt-oss:120b-cloud")      # bez „punkt:” — przez `default`
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertEqual((made.verdict("claude:u1"), len(server.requests)), (None, 0))
        self.assertIn("ocena modelem wyłączona", self.logs[0])
        with self.assertRaises(ModelError):   # także bezpośrednio: bez jawnego punktu chmura nie przechodzi
            client.chat("x", role="classify", allow_cloud=True)

    def two_points(self, role):
        local = chat_server(self, waits=False)
        cloud = chat_server(self, waits=True)
        self.models(endpoints={"local": {"url": local.url, "kind": "ollama"},
                               "cloud": {"url": cloud.url, "kind": "ollama"}}, roles={"classify": role})
        return local, cloud

    def test_explicit_cloud_point_is_used_and_only_it(self):
        local, cloud = self.two_points("cloud:gpt-oss:120b-cloud")
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertIs(made.verdict("claude:u1"), True)
        self.assertEqual((len(local.requests), len(cloud.requests)), (0, 1), "lokalny w tym trybie nie jest wołany")
        self.assertEqual(cloud.requests[0]["body"]["model"], "gpt-oss:120b-cloud")

    def test_changed_role_model_is_used_by_the_next_judgement(self):
        """PT-112: plik zmieniony w trakcie pracy (SetRole) — bez restartu następna ocena idzie nowym modelem."""
        server = chat_server(self, waits=True)
        self.single(server, role="local:qwen3:8b")
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.single(server, role="local:gemma3:4b")
        made.judge("claude:u2", SELECT)
        self.settle(made)
        self.assertEqual([r["body"]["model"] for r in server.requests], ["qwen3:8b", "gemma3:4b"])

    def test_cloud_point_error_means_rules_only_without_a_substitute(self):
        local, cloud = self.two_points("cloud:gpt-oss:120b-cloud")
        cloud.routes[("POST", "/api/chat")] = (500, {"error": "boom"})
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertEqual((made.verdict("claude:u1"), len(local.requests), len(cloud.requests)), (None, 0, 1))
        self.assertIn("liczą same reguły", self.logs[0])

    def test_local_role_never_calls_the_cloud_point(self):
        local, cloud = self.two_points("local:qwen3:8b")
        made = self.classifier()
        made.judge("claude:u1", SELECT)
        self.settle(made)
        self.assertIs(made.verdict("claude:u1"), False)
        self.assertEqual((len(local.requests), len(cloud.requests)), (1, 0))
        # Także nieudane wywołanie lokalne nie przechodzi do punktu w chmurze.
        local.routes[("POST", "/api/chat")] = (500, {"error": "boom"})
        made.judge("claude:u2", "Daj znać.")
        self.settle(made)
        self.assertEqual((made.verdict("claude:u2"), len(cloud.requests)), (None, 0))


class QueueOrderTest(ClassifierBase):
    def test_classification_goes_before_the_report_and_does_not_starve_it(self):
        seen = []
        server = chat_server(self, waits=True, seen=seen)
        cfg = self.single(server)
        queue = queue_mod.Queue()
        made = self.classifier(queue=queue)
        lane = queue_mod.lane_of(server.url)
        blocker = Blocker(queue, endpoint="local", lane=lane, role="summary")      # serwer zajęty (np. raport)
        report = threading.Thread(
            target=lambda: client.chat("RAPORT", role="summary", config=models_config.load(cfg), queue=queue),
            daemon=True)
        report.start()
        self.assertTrue(wait_for(lambda: len(queue.snapshot().get(lane, {"waiting": []})["waiting"]) == 1),
                        "raport czeka w kolejce")
        made.judge("claude:u1", "Pierwsza. Daj znać.")      # przychodzi PO raporcie…
        made.judge("claude:u2", "Druga. Daj znać.")
        self.assertTrue(wait_for(lambda: len(queue.snapshot().get(lane, {"waiting": []})["waiting"]) == 2))
        blocker.release()
        report.join(10)
        self.settle(made)
        order = ["raport" if m == "RAPORT" else "ocena" for m in seen]
        # …a idzie przed nim; druga ocena ustępuje raportowi, który już czeka (bez zagłodzenia).
        self.assertEqual(order, ["ocena", "raport", "ocena"], seen)
        self.assertEqual((made.verdict("claude:u1"), made.verdict("claude:u2")), (True, True))


class SessionStateTest(ClassifierBase):
    def setUp(self):
        super().setUp()
        self.cwd = os.path.join(self.tmp, "proj")
        os.makedirs(self.cwd)
        folder = os.path.join(self.tmp, "claude", "projects", escape_cwd(self.cwd))
        os.makedirs(folder)
        self.sid = "5ad7a2f1-0000-4000-8000-000000000042"
        self.path = os.path.join(folder, f"{self.sid}.jsonl")
        self.now = time.time()

    def write(self, *records):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(dict(record, cwd=self.cwd, gitBranch="main"), ensure_ascii=False) + "\n")

    def turn(self, text, uid="uuid-1", ago=300):
        ts = self.now - ago
        self.write({"type": "user", "timestamp": iso(ts - 60), "message": {"role": "user", "content": "Start"}},
                   {"type": "assistant", "uuid": uid, "timestamp": iso(ts),
                    "message": {"role": "assistant", "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": text}]}},
                   {"type": "system", "subtype": "turn_duration", "timestamp": iso(ts + 1)})
        return ts

    def scanner(self, made):
        scanner = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        scanner.classifier = made
        scanner.discover(self.now)
        scanner.update()
        return scanner

    def test_model_turns_a_done_session_into_waiting_with_the_rule_shape(self):
        server = chat_server(self, waits=True)
        self.single(server)
        asked = self.turn(SELECT)
        made = self.classifier()
        scanner = self.scanner(made)
        s = scanner.sessions(self.now)[0]
        self.assertEqual((s["state"], s["reason"], s["waitingText"]), ("done", "", ""), "do wyniku liczą reguły")
        self.settle(made)
        s = scanner.sessions(self.now)[0]
        self.assertEqual((s["state"], s["reason"]), ("waiting", "reply"))
        self.assertIn("uruchom ten select i wklej mi wynik", s["waitingText"])
        self.assertAlmostEqual(s["sinceTs"], asked, places=0)
        # Ten sam kształt co ze stanu z reguł (pytanie z „?”): te same klucze, ten sam powód.
        self.turn(RULES_YES, uid="uuid-2", ago=100)
        scanner.update()
        rules = scanner.sessions(self.now)[0]
        self.assertEqual((rules["state"], rules["reason"]), ("waiting", "reply"))
        self.assertEqual(sorted(rules), sorted(s))
        self.assertEqual(made.calls, 1, "reguła „tak” nie pyta modelu")

    def test_model_no_keeps_the_session_done(self):
        server = chat_server(self, waits=False)
        self.single(server)
        self.turn("Gotowe. Daj znać, gdy mam zaczynać następny.")
        made = self.classifier()
        scanner = self.scanner(made)
        self.assertEqual(scanner.sessions(self.now)[0]["state"], "done")
        self.settle(made)
        self.assertEqual(scanner.sessions(self.now)[0]["state"], "done")
        self.assertEqual(made.calls, 1)

    def test_state_read_does_not_wait_for_the_model(self):
        server = chat_server(self, waits=True)
        server.delay = 1.0
        self.single(server)
        self.turn(SELECT)
        made = self.classifier()
        scanner = self.scanner(made)
        started = time.monotonic()
        state = scanner.sessions(self.now)[0]["state"]
        self.assertLess(time.monotonic() - started, 0.5, "odczyt stanu nie czeka na model")
        self.assertEqual(state, "done")
        self.assertFalse(made.idle(), "ocena w toku")
        self.settle(made)
        self.assertEqual(scanner.sessions(self.now)[0]["state"], "waiting")

    def test_new_user_message_clears_the_verdict_and_no_model_means_rules(self):
        server = chat_server(self, waits=True)
        self.single(server)
        self.turn(SELECT)
        made = self.classifier()
        scanner = self.scanner(made)
        scanner.sessions(self.now)
        self.settle(made)
        self.assertEqual(scanner.sessions(self.now)[0]["state"], "waiting")
        self.write({"type": "user", "timestamp": iso(self.now - 10), "message": {"role": "user", "content": "Wynik: 5"}})
        scanner.update()
        self.assertEqual(scanner.sessions(self.now)[0]["state"], "working")
        bare = AgentScanner(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))   # bez oceny modelem
        bare.discover(self.now)
        bare.update()
        self.assertEqual(bare.sessions(self.now)[0]["state"], "working")


@requires_dbus
class ClassifyDBusTest(unittest.TestCase):
    NOW_TEXT = "2026-10-26T12:00:00Z"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-classify-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bus = PrivateBus("tide-classify-test")
        self.addCleanup(self.bus.stop)
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.empty = empty
        cwd = os.path.join(self.tmp, "proj")
        os.makedirs(cwd)
        folder = os.path.join(self.tmp, "claude", "projects", escape_cwd(cwd))
        os.makedirs(folder)
        with open(os.path.join(folder, "5ad7a2f1-0000-4000-8000-000000000042.jsonl"), "w", encoding="utf-8") as fh:
            for record in (
                    {"type": "user", "timestamp": "2026-10-26T11:30:00Z", "message": {"role": "user", "content": "Start"}},
                    {"type": "assistant", "uuid": "uuid-dbus-1", "timestamp": "2026-10-26T11:40:00Z",
                     "message": {"role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "**Krok 1:** uruchom ten select i wklej mi wynik"}]}},
                    {"type": "system", "subtype": "turn_duration", "timestamp": "2026-10-26T11:40:01Z"}):
                fh.write(json.dumps(dict(record, cwd=cwd, gitBranch="main")) + "\n")

    def sessions(self):
        reply = self.bus.call(SERVICE, "/Agents", "org.tide.Daemon.Agents", "Sessions")
        return json.loads(reply.arguments()[0])

    def test_session_turns_waiting_after_the_model_and_the_read_does_not_wait(self):
        server = chat_server(self, waits=True)
        server.delay = 1.5
        models = write_models(self.tmp, {"endpoints": {"local": {"url": server.url, "kind": "ollama"}}})
        self.bus.spawn_daemon("--claude-dir", os.path.join(self.tmp, "claude"), "--codex-dir", self.empty,
                              "--workspace", self.empty, "--no-processes", "--no-krunner", "--no-idle", "--no-limits",
                              "--now", self.NOW_TEXT, extra_env={"TIDE_MODELS_CONFIG": models})
        def judged():
            return [r for r in server.requests if r["path"] == "/api/chat" and r["body"]
                    and r["body"]["messages"][-1]["content"].startswith(classify.PROMPT)]

        self.assertTrue(wait_until(lambda: len(judged()) >= 1, timeout=10), "ocena ruszyła")
        started = time.monotonic()
        mid = self.sessions()      # model jeszcze odpowiada (opóźnienie atrapy) — odpowiedź D-Bus nie czeka
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual([(s["state"], s["waitingText"]) for s in mid], [("done", "")])
        final = wait_until(lambda: (self.sessions() or [{}])[0].get("state") == "waiting" and self.sessions(), timeout=10)
        self.assertTrue(final, "po wyniku modelu stan waiting")
        self.assertEqual((final[0]["reason"], final[0]["waitingText"]),
                         ("reply", "Krok 1: uruchom ten select i wklej mi wynik"))
        # Atrapa widzi też zapytania innych obszarów usługi (np. opisy kart) — liczą się tylko oceny.
        self.assertEqual(len(judged()), 1, f"jedna ocena na wiadomość; zapytania: "
                                           f"{[(r['method'], r['path'], str(r['body'])[:80]) for r in server.requests]}")
        saved = os.path.join(self.bus.tmp, "state", "tide", "classify.json")
        self.assertTrue(wait_until(lambda: os.path.exists(saved), timeout=5))
        self.assertTrue(json.load(open(saved, encoding="utf-8"))["results"]["claude:uuid-dbus-1"]["waits"])

    def test_role_model_change_applies_to_the_next_judgement_without_restart(self):
        """PT-112: SetRole classify w działającej usłudze — następna ocena idzie nowym modelem."""
        server = chat_server(self, waits=True)
        models = write_models(self.tmp, {"endpoints": {"local": {"url": server.url, "kind": "ollama"}}})
        self.bus.spawn_daemon("--claude-dir", os.path.join(self.tmp, "claude"), "--codex-dir", self.empty,
                              "--workspace", self.empty, "--no-processes", "--no-krunner", "--no-idle", "--no-limits",
                              "--now", self.NOW_TEXT, extra_env={"TIDE_MODELS_CONFIG": models})

        def judged():
            return [r["body"]["model"] for r in server.requests if r["path"] == "/api/chat" and r["body"]
                    and r["body"]["messages"][-1]["content"].startswith(classify.PROMPT)]

        self.assertTrue(wait_until(lambda: len(judged()) >= 1, timeout=10), "pierwsza ocena")
        self.assertEqual(judged(), ["qwen3:8b"], "domyślny model roli")
        reply = self.bus.call(SERVICE, "/Models", "org.tide.Daemon.Models", "SetRole",
                              json.dumps({"role": "classify", "model": "gemma3:4b"}))
        self.assertEqual(json.loads(reply.arguments()[0]), {"ok": True, "error": ""})
        folder = os.path.join(self.tmp, "claude", "projects", escape_cwd(os.path.join(self.tmp, "proj")))
        path = os.path.join(folder, "5ad7a2f1-0000-4000-8000-000000000042.jsonl")
        with open(path, "a", encoding="utf-8") as fh:   # nowa tura agenta z wątpliwą końcówką
            for record in (
                    {"type": "user", "timestamp": "2026-10-26T11:50:00Z", "message": {"role": "user", "content": "Wynik: 5"}},
                    {"type": "assistant", "uuid": "uuid-dbus-2", "timestamp": "2026-10-26T11:55:00Z",
                     "message": {"role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "**Krok 2:** uruchom ten skrypt i wklej mi wynik"}]}},
                    {"type": "system", "subtype": "turn_duration", "timestamp": "2026-10-26T11:55:01Z"}):
                fh.write(json.dumps(dict(record, cwd=os.path.join(self.tmp, "proj"), gitBranch="main")) + "\n")
        self.assertTrue(wait_until(lambda: len(judged()) >= 2, timeout=15), "druga ocena")
        self.assertEqual(judged(), ["qwen3:8b", "gemma3:4b"], "następna ocena idzie nowym modelem")


if __name__ == "__main__":
    unittest.main()
