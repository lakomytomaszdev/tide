"""Kolejka wywołań modeli lokalnych (tide_daemon/models/queue.py) bez Qt i razem z raportem i opisami kart.

Rdzeń: jedno wywołanie naraz na punkt, dwa punkty równolegle, priorytety i kolejność przyjścia,
grupowanie modeli, limit czekania osobno od limitu wywołania, przerwanie (Cancel) czekających i trwających,
brak wiszących wątków. Integracja: raport i opisy kart naraz na wolnej atrapie Ollamy, która obsługuje
jedno zapytanie naraz (jak OLLAMA_NUM_PARALLEL=1) — karta nie dostaje mylącego „nie odpowiedział w N s”.

Prawdziwa Ollama nie jest pytana: każdy test podaje własną konfigurację z adresem atrapy.
"""
import json
import re
import threading
import time
import unittest
from unittest import mock

from tide_daemon.coordination import describe
from tide_daemon.models import Cancel, ModelError, client
from tide_daemon.models import config as models_config
from tide_daemon.models import queue as queue_mod
from tide_daemon.reports import summarize

from .test_coordination_describe import board, ollama_tickets, ticket
from .test_models_core import OLLAMA_CHAT, start_server
from .test_report_standup import StandupWorld, ollama_answer, project_answer


def wait_for(predicate, timeout=5.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


class Blocker:
    """Wywołanie zajmujące punkt, dopóki test go nie puści (`release`)."""

    def __init__(self, queue, endpoint="local", role="summary", model="qwen3:8b", **kw):
        self.started = threading.Event()
        self.gate = threading.Event()
        self.thread = threading.Thread(target=lambda: queue.run(self._work, endpoint=endpoint, role=role,
                                                                model=model, **kw), daemon=True)
        self.thread.start()
        assert self.started.wait(5), "blokujące wywołanie nie ruszyło"

    def _work(self):
        self.started.set()
        self.gate.wait(10)
        return "blocker"

    def release(self):
        self.gate.set()
        self.thread.join(5)


class QueueCoreTest(unittest.TestCase):
    def setUp(self):
        self.queue = queue_mod.Queue()
        self.threads_before = threading.active_count()

    def spawn(self, results, name, **kw):
        def work():
            try:
                results.append(self.queue.run(lambda: name, **kw))
            except ModelError as exc:
                results.append(exc)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread

    def waiting(self, endpoint="local"):
        return len((self.queue.snapshot().get(endpoint) or {}).get("waiting") or ())

    def test_one_at_a_time_per_endpoint_and_two_endpoints_in_parallel(self):
        lock = threading.Lock()
        live = {"local": 0, "mac": 0, "all": 0}
        peak = {"local": 0, "mac": 0, "all": 0}

        def work(endpoint):
            with lock:
                for key in (endpoint, "all"):
                    live[key] += 1
                    peak[key] = max(peak[key], live[key])
            time.sleep(0.05)
            with lock:
                for key in (endpoint, "all"):
                    live[key] -= 1
            return endpoint

        threads = [threading.Thread(target=lambda e=e: self.queue.run(lambda: work(e), endpoint=e, role="summary"))
                   for e in ["local"] * 4 + ["mac"] * 4]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual((peak["local"], peak["mac"]), (1, 1), "na jednym punkcie jedno wywołanie naraz")
        self.assertEqual(peak["all"], 2, "dwa punkty pracują równolegle")
        self.assertEqual(self.queue.snapshot(), {})
        self.assertFalse(self.queue.busy())

    def test_more_slots_per_endpoint(self):
        queue = queue_mod.Queue(slots={"mac": 2})
        self.assertEqual((queue.slots("mac"), queue.slots("local")), (2, 1))
        first = Blocker(queue, endpoint="mac")
        second = Blocker(queue, endpoint="mac")          # drugie miejsce na Macu — rusza od razu
        self.assertEqual(len(queue.snapshot()["mac"]["waiting"]), 0)
        first.release()
        second.release()

    def test_priority_first_then_arrival_order_and_no_preemption(self):
        blocker = Blocker(self.queue, role="summary")
        order = []
        threads = []
        for name, role, priority in (("raport-2", "summary", queue_mod.LOW), ("inne", "agent", None),
                                     ("karta-1", "describe", queue_mod.HIGH), ("karta-2", "describe", None)):
            threads.append(self.spawn(order, name, endpoint="local", role=role, model="qwen3:8b", priority=priority))
            self.assertTrue(wait_for(lambda: self.waiting() == len(threads)))
        time.sleep(0.1)
        self.assertEqual(order, [], "trwającego wywołania nic nie przerywa — czekający czekają")
        snap = self.queue.snapshot()["local"]
        self.assertEqual(snap["running"]["role"], "summary")
        self.assertEqual([w["role"] for w in snap["waiting"]], ["describe", "describe", "agent", "summary"],
                         "w stanie w kolejności, w jakiej ruszą")
        blocker.release()
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ["karta-1", "karta-2", "inne", "raport-2"])

    def test_same_model_first_within_a_priority_unless_the_oldest_waited_long(self):
        for hold, expected in ((60.0, ["a-2", "b-1"]), (0.0, ["b-1", "a-2"])):
            queue = queue_mod.Queue(group_hold=hold)
            self.queue = queue
            blocker = Blocker(queue, role="summary", model="model-a")
            order = []
            threads = [self.spawn(order, "b-1", endpoint="local", role="summary", model="model-b")]
            self.assertTrue(wait_for(lambda: self.waiting() == 1))
            threads.append(self.spawn(order, "a-2", endpoint="local", role="summary", model="model-a"))
            self.assertTrue(wait_for(lambda: self.waiting() == 2))
            blocker.release()
            for thread in threads:
                thread.join(5)
            self.assertEqual(order, expected, f"group_hold={hold}")

    def test_higher_priority_beats_model_grouping(self):
        blocker = Blocker(self.queue, role="summary", model="model-a")
        order = []
        threads = [self.spawn(order, "raport", endpoint="local", role="summary", model="model-a")]
        self.assertTrue(wait_for(lambda: self.waiting() == 1))
        threads.append(self.spawn(order, "karta", endpoint="local", role="describe", model="model-b"))
        self.assertTrue(wait_for(lambda: self.waiting() == 2))
        blocker.release()
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ["karta", "raport"])

    def test_wait_limit_is_separate_and_names_what_is_running(self):
        blocker = Blocker(self.queue, role="summary")
        called = []
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            self.queue.run(lambda: called.append(1), endpoint="local", role="describe", model="qwen3:8b", wait=0.3,
                           host="127.0.0.1:11434")
        self.assertGreaterEqual(time.monotonic() - started, 0.29)
        self.assertEqual(called, [], "po limicie czekania wywołanie nie rusza")
        self.assertEqual(str(ctx.exception),
                         "Model lokalny: kolejka: czekał 0,3 s — serwer zajęty: raport poranny (127.0.0.1:11434)")
        self.assertEqual(summarize.local_note(ctx.exception),
                         "Model lokalny niedostępny — opis bez modelu (kolejka: czekał 0,3 s — serwer zajęty: "
                         "raport poranny, 127.0.0.1:11434)")
        self.assertEqual(self.waiting(), 0, "po limicie znika z kolejki")
        blocker.release()

    def test_error_in_call_frees_the_endpoint(self):
        def boom():
            raise ModelError("Model lokalny: atrapa")
        with self.assertRaises(ModelError):
            self.queue.run(boom, endpoint="local", role="summary")
        self.assertEqual(self.queue.run(lambda: "ok", endpoint="local", role="summary", wait=0.1), "ok")

    def test_cancel_removes_waiting_at_once_and_set_cancel_never_starts(self):
        blocker = Blocker(self.queue, role="summary")
        cancel = Cancel()
        results = []
        thread = self.spawn(results, "karta", endpoint="local", role="describe", cancel=cancel, wait=None,
                            host="127.0.0.1:11434")
        self.assertTrue(wait_for(lambda: self.waiting() == 1))
        started = time.monotonic()
        cancel.set()
        thread.join(2)
        self.assertLess(time.monotonic() - started, 0.5, "zamykanie usługi nie czeka na kolej")
        self.assertIsInstance(results[0], ModelError)
        self.assertEqual(str(results[0]), "Model lokalny: wywołanie przerwane — usługa się zamyka (127.0.0.1:11434)")
        self.assertEqual(self.waiting(), 0)
        self.assertEqual(cancel._hooks, set(), "kolejka nie zostawia haków w Cancel")
        with self.assertRaises(ModelError):
            self.queue.run(lambda: "nie", endpoint="mac", cancel=cancel)  # wolny punkt, ale Cancel już ustawiony
        blocker.release()

    def test_listener_and_snapshot_without_prompt_contents(self):
        events = []
        self.queue.add_listener(lambda: events.append(self.queue.snapshot()))
        self.queue.add_listener(lambda: 1 / 0)  # zepsuty słuchacz nie psuje wywołania
        with mock.patch("builtins.print"):
            self.assertEqual(self.queue.run(lambda: "ok", endpoint="local", role="describe", model="qwen3:8b"), "ok")
        self.assertEqual(len(events), 3, "dołączenie, start, koniec")
        waiting, running, done = events
        self.assertEqual(waiting["local"]["running"], None)
        self.assertEqual(set(waiting["local"]["waiting"][0]), {"role", "model", "since"})
        self.assertEqual((running["local"]["running"]["role"], running["local"]["running"]["model"]),
                         ("describe", "qwen3:8b"))
        self.assertRegex(running["local"]["running"]["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(done, {})

    def test_no_thread_leaks(self):
        cancel = Cancel()
        for i in range(20):
            self.queue.run(lambda: i, endpoint="local", role="summary", cancel=cancel)
        blocker = Blocker(self.queue)
        for _ in range(5):
            with self.assertRaises(ModelError):
                self.queue.run(lambda: None, endpoint="local", wait=0.01)
        blocker.release()
        self.assertTrue(wait_for(lambda: threading.active_count() <= self.threads_before),
                        "kolejka nie tworzy własnych wątków")


class ChatQueueTest(unittest.TestCase):
    """models.chat przez kolejkę na atrapie, która obsługuje jedno zapytanie naraz."""

    def setUp(self):
        self.queue = queue_mod.Queue()
        self.server = start_server(self, {("POST", "/api/chat"): (200, OLLAMA_CHAT)})
        self.server.serial = True
        self.server.delay = 0.4
        self.cfg = models_config.from_dict({"endpoints": {"local": {"url": self.server.url}}})

    def chat(self, results, **kw):
        def work():
            try:
                results.append(client.chat("x", role="describe", config=self.cfg, queue=self.queue, **kw))
            except ModelError as exc:
                results.append(exc)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread

    def test_timeout_counts_from_the_start_of_the_call(self):
        results = []
        threads = [self.chat(results, timeout=0.7) for _ in range(3)]
        for thread in threads:
            thread.join(5)
        self.assertTrue(all(not isinstance(r, Exception) for r in results), results)
        self.assertEqual(len(self.server.requests), 3)
        waits = sorted(r.metrics["queueS"] for r in results)
        self.assertLess(waits[0], 0.2)
        self.assertGreater(waits[2], 0.7, "trzecie czekało dłużej niż własny limit — i zdążyło")
        self.assertTrue(all(r.metrics["wallS"] < 0.7 for r in results))

    def test_deadline_limits_waiting_and_the_call_together(self):
        results = []
        first = self.chat(results)
        self.assertTrue(wait_for(lambda: self.server.requests))
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", role="summary", config=self.cfg, queue=self.queue, timeout=180,
                        deadline=time.monotonic() + 0.2)
        self.assertLess(time.monotonic() - started, 0.35)
        self.assertIn("kolejka: czekał 0,2 s — serwer zajęty: opisy kart", str(ctx.exception))
        first.join(5)
        # Termin bliżej niż `timeout` — limit wywołania przycięty do końca terminu (i tak w komunikacie).
        self.server.delay = 1.0
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", role="summary", config=self.cfg, queue=self.queue, timeout=180,
                        deadline=time.monotonic() + 0.4)
        self.assertRegex(str(ctx.exception), r"^Model lokalny nie odpowiedział w 0\.[34] s ")

    def test_cancel_interrupts_running_and_clears_waiting(self):
        self.server.delay = 5.0
        cancel = Cancel()
        results = []
        threads = [self.chat(results, cancel=cancel) for _ in range(2)]
        self.assertTrue(wait_for(lambda: len(self.server.requests) == 1 and self.queue.snapshot()
                                 .get("local", {}).get("waiting")))
        started = time.monotonic()
        cancel.set()
        for thread in threads:
            thread.join(3)
        self.assertLess(time.monotonic() - started, 1.0, "zamykanie usługi: trwające i czekające kończą się od razu")
        self.assertEqual(len(results), 2)
        self.assertTrue(all("przerwane" in str(r) for r in results), results)
        self.assertEqual(len(self.server.requests), 1, "czekające nie dotarło do serwera")
        self.assertEqual(self.queue.snapshot(), {})


class ReportAndCardsTest(StandupWorld):
    """Raport (jedno wywołanie na projekt, niski priorytet) i opisy kart (wysoki) naraz na wspólnej kolejce
    procesu i atrapie, która jak Ollama obsługuje jedno zapytanie naraz."""

    def setUp(self):
        self.queue = queue_mod.shared()
        self.assertFalse(self.queue.busy(), "kolejka procesu pusta przed testem")
        never = mock.patch.object(summarize, "call_claude", side_effect=AssertionError("claude w trybie local"))
        never.start()
        self.addCleanup(never.stop)

    def respond(self, handler):
        body = handler.body
        system = body["messages"][0]["content"]
        if system == describe.BOARD_PROMPT:
            keys = [t["key"] for t in json.loads(body["messages"][1]["content"])["tickets"]]
            code, value = ollama_tickets(*keys)
        else:
            name = json.loads(body["messages"][1]["content"])["projects"][0]["name"]
            code, value = ollama_answer(project_answer(name, f"Opis {name}"))
        data = json.dumps(value).encode()
        try:
            handler.send_response(code)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:
            pass

    def server(self, delay):
        server = start_server(self, {("POST", "/api/chat"): (200, self.respond)})
        server.serial = True
        server.delay = delay
        return server

    def describer(self, config, timeout):
        clock = {"now": 1000.0}
        d = describe.Describer(mode="local", model="sonnet", cache_file=f"{self.tmp}/cards-{time.time_ns()}.json",
                               clock=lambda: clock["now"], log=lambda text: None, models_config=config,
                               timeout=timeout)
        boards = [board([ticket("~a"), ticket("~b", state="w-toku")])]
        d.decorate(boards)
        clock["now"] += describe.SETTLE
        d.decorate(boards)
        return d, boards, d.due(boards)

    @staticmethod
    def kind(request):
        return "karta" if request["body"]["messages"][0]["content"] == describe.BOARD_PROMPT else "raport"

    def test_cards_wait_in_the_queue_not_against_their_own_timeout(self):
        server = self.server(delay=0.6)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.0)
        self.assertIsNotNone(job)
        out = {}
        report = threading.Thread(target=lambda: out.setdefault("report", summarize.summarize(
            self.report, mode="local", home=self.p["home"], models_config=config, timeout=1.0)))
        report.start()
        self.assertTrue(wait_for(lambda: len(server.requests) == 1), "raport pyta o pierwszy projekt")
        cards = threading.Thread(target=lambda: out.setdefault("cards", d.run(job)))
        cards.start()
        self.assertTrue(wait_for(lambda: (self.queue.snapshot().get("local") or {}).get("waiting")))
        snap = self.queue.snapshot()["local"]
        self.assertEqual(snap["running"]["role"], "summary")
        self.assertEqual([w["role"] for w in snap["waiting"]], ["describe"], "karta czeka w kolejce, nie w serwerze")
        report.join(10)
        cards.join(10)
        self.assertTrue(out["cards"], "karta dostała opis modelu — bez mylącego „nie odpowiedział w 1 s”")
        d.decorate(boards)
        self.assertEqual([t["describedBy"] for t in boards[0]["tickets"]], ["model", "model"])
        self.assertNotIn("describeNote", boards[0]["tickets"][0])
        result = out["report"]
        self.assertEqual((result["source"], result["note"]), ("model", ""))
        self.assertEqual(set(result["projects"]), {"alpha", "beta"})
        self.assertEqual([self.kind(r) for r in server.requests], ["raport", "karta", "raport"],
                         "karta przed resztą raportu (wyższy priorytet), bez przerywania trwającego wywołania")
        self.assertEqual(self.queue.snapshot(), {})

    def test_report_budget_includes_waiting_in_the_queue(self):
        server = self.server(delay=0.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        blocker = Blocker(self.queue, role="describe", model="qwen3:8b")
        try:
            started = time.monotonic()
            result = summarize.summarize(self.report, mode="local", home=self.p["home"], models_config=config,
                                         budget=0.4)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            blocker.release()
        self.assertEqual(result["source"], "rules")
        self.assertTrue(re.fullmatch(r"Model lokalny niedostępny — opis bez modelu \(kolejka: czekał 0,4 s — serwer "
                                     r"zajęty: opisy kart, 127\.0\.0\.1:\d+\)", result["note"]), result["note"])
        self.assertEqual(server.requests, [], "budżet raportu minął w kolejce — serwer nie był pytany")

    def test_card_wait_limit_gives_an_honest_note(self):
        server = self.server(delay=0.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.0)
        d.wait = 0.2
        blocker = Blocker(self.queue, role="summary", model="qwen3:8b")
        try:
            self.assertFalse(d.run(job))
        finally:
            blocker.release()
        d.decorate(boards)
        note = boards[0]["tickets"][0]["describeNote"]
        self.assertTrue(note.startswith("Model lokalny niedostępny — opis bez modelu (kolejka: czekał 0,2 s — "
                                        "serwer zajęty: raport poranny, 127.0.0.1:"), note)
        self.assertEqual(server.requests, [])

    def test_describer_stop_removes_its_waiting_call(self):
        server = self.server(delay=0.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.0)
        blocker = Blocker(self.queue, role="summary", model="qwen3:8b")
        try:
            out = {}
            worker = threading.Thread(target=lambda: out.setdefault("ok", d.run(job)))
            worker.start()
            self.assertTrue(wait_for(lambda: (self.queue.snapshot().get("local") or {}).get("waiting")))
            started = time.monotonic()
            d.stop()
            worker.join(2)
            self.assertLess(time.monotonic() - started, 0.5, "zamykanie usługi nie czeka, aż raport zwolni serwer")
            self.assertFalse(out["ok"])
            self.assertEqual(self.queue.snapshot()["local"]["waiting"], [])
        finally:
            blocker.release()


if __name__ == "__main__":
    unittest.main()
