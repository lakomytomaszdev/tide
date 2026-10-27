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

    def test_group_hold_expiring_between_evaluations_does_not_strand_waiters(self):
        """Okno grupowania mija między ocenami dwóch czekających (zegar podmieniony): A (najstarsze, inny model)
        i B (ten sam model co ostatnio) nie mogą oba uznać, że następny jest ten drugi, i spać przy wolnym
        miejscu (review PT-16, MAJOR 1)."""
        real_sleep = time.sleep
        for attempt in range(6):
            now = {"t": 0.0}
            with mock.patch.object(queue_mod.time, "monotonic", lambda: now["t"]):
                queue = queue_mod.Queue(group_hold=10)
                original = queue._next
                jump = {"on": False}

                def jumping(*args, original=original, jump=jump, now=now):
                    result = original(*args)
                    if jump["on"]:
                        now["t"] = 20.0      # okno grupowania mija zaraz po tej ocenie
                    return result

                queue._next = jumping
                blocker = Blocker(queue, model="Y", wait=None)
                done = []
                a = threading.Thread(target=lambda: queue.run(lambda: done.append("A"), endpoint="local", model="X",
                                                              wait=None), daemon=True)
                a.start()
                real_sleep(0.05)
                now["t"] = 1.0
                b = threading.Thread(target=lambda: queue.run(lambda: done.append("B"), endpoint="local", model="Y",
                                                              wait=None), daemon=True)
                b.start()
                real_sleep(0.05)
                now["t"] = 5.0
                jump["on"] = True
                blocker.gate.set()
                for thread in (blocker.thread, a, b):
                    thread.join(2)
                self.assertEqual(sorted(done), ["A", "B"], f"próba {attempt}: czekający utknęli przy wolnym miejscu")

    def test_moved_call_keeps_its_original_waiting_since(self):
        """Wywołanie przeniesione na inny tor (ustawienia zmienione w czasie czekania) pokazuje w `waiting`
        pierwotną chwilę dołączenia, nie chwilę próby startu."""
        ticks = iter(range(1_800_000_000, 1_900_000_000, 100))   # każdy odczyt zegara ściennego +100 s
        queue = queue_mod.Queue(wall=lambda: float(next(ticks)))
        blocker = Blocker(queue, endpoint="b", lane="lane-b")
        moved = threading.Event()

        def resolve():
            moved.set()      # kolej na torze A, ale cel jest już na torze B
            return "lane-b", "b", "qwen3:8b", "b"

        thread = threading.Thread(target=lambda: queue.run(lambda: None, endpoint="a", lane="lane-a", role="describe",
                                                           model="qwen3:8b", resolve=resolve), daemon=True)
        thread.start()
        self.assertTrue(moved.wait(5))
        self.assertTrue(wait_for(lambda: (queue.snapshot().get("lane-b") or {}).get("waiting")))
        [entry] = queue.snapshot()["lane-b"]["waiting"]
        self.assertEqual(entry["since"], queue_mod._iso(1_800_000_200.0), "chwila dołączenia, nie próby startu (+300)")
        self.assertEqual(entry["endpoint"], "b")
        blocker.release()
        thread.join(5)

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
        self.assertRegex(str(ctx.exception), r"^Model lokalny: kolejka: czekał \d+(\.\d)? s — serwer zajęty: "
                                             r"raport poranny \(127\.0\.0\.1:11434\)$")
        self.assertRegex(summarize.local_note(ctx.exception),
                         r"^Model lokalny niedostępny — opis bez modelu \(kolejka: czekał \d+(\.\d)? s — serwer "
                         r"zajęty: raport poranny, 127\.0\.0\.1:11434\)$")
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
        self.assertLess(time.monotonic() - started, 1.0, "zamykanie usługi nie czeka na kolej")
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
        self.assertEqual(waiting["local"]["waiting"][0]["endpoint"], "local")
        self.assertEqual(set(waiting["local"]["waiting"][0]), {"endpoint", "role", "model", "since"})
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
        threads = [self.chat(results, timeout=0.75) for _ in range(3)]
        for thread in threads:
            thread.join(5)
        self.assertTrue(all(not isinstance(r, Exception) for r in results), results)
        self.assertEqual(len(self.server.requests), 3)
        waits = sorted(r.metrics["queueS"] for r in results)
        self.assertLess(waits[0], 0.35)
        self.assertGreater(waits[2], 0.75, "trzecie czekało dłużej niż własny limit — i zdążyło")
        self.assertTrue(all(r.metrics["wallS"] < 0.75 for r in results))

    def test_deadline_limits_waiting_and_the_call_together(self):
        results = []
        first = self.chat(results)
        self.assertTrue(wait_for(lambda: self.server.requests))
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", role="summary", config=self.cfg, queue=self.queue, timeout=180,
                        deadline=time.monotonic() + 0.2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertRegex(str(ctx.exception), r"kolejka: czekał \d+(\.\d)? s — serwer zajęty: opisy kart")
        first.join(5)
        # Termin bliżej niż `timeout` — limit wywołania przycięty do końca terminu (i tak w komunikacie).
        self.server.delay = 1.0
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", role="summary", config=self.cfg, queue=self.queue, timeout=180,
                        deadline=time.monotonic() + 0.4)
        self.assertRegex(str(ctx.exception), r"^Model lokalny nie odpowiedział w 0\.\d s ")

    def test_two_endpoint_names_on_one_server_share_one_slot(self):
        cfg = models_config.from_dict({"endpoints": {"a": {"url": self.server.url},
                                                     "b": {"url": self.server.url + "/"}},
                                       "roles": {"summary": "a:qwen3:8b", "describe": "b:qwen3:8b"}})
        results = []

        def work(role, **kw):
            try:
                results.append(client.chat("x", role=role, config=cfg, queue=self.queue, **kw))
            except ModelError as exc:
                results.append(exc)

        first = threading.Thread(target=work, args=("summary",))
        first.start()
        self.assertTrue(wait_for(lambda: self.server.requests))
        work("describe", wait=0.1)
        second = results[-1]
        first.join(5)
        self.assertIsInstance(second, ModelError, "drugi punkt na tym samym serwerze czeka na wolne miejsce")
        self.assertIn("kolejka: czekał", str(second))
        self.assertEqual(len(self.server.requests), 1)

    def test_target_is_resolved_when_the_call_starts(self):
        """Ustawienia zmienione w czasie czekania (np. SetDefault("mac") z okna Tide) — czekające wywołanie
        idzie już na nowy serwer."""
        import os
        import tempfile
        from .test_models_core import write_models
        other = start_server(self, {("POST", "/api/chat"): (200, OLLAMA_CHAT)})
        folder = tempfile.mkdtemp(prefix="tide-queue-")
        self.addCleanup(__import__("shutil").rmtree, folder)
        path = write_models(folder, {"endpoints": {"local": {"url": self.server.url}}})
        with mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": path}):
            results = []

            def work():
                results.append(client.chat("x", role="describe", queue=self.queue))

            first = threading.Thread(target=work)
            first.start()
            self.assertTrue(wait_for(lambda: self.server.requests))
            second = threading.Thread(target=work)
            second.start()
            self.assertTrue(wait_for(lambda: any(s.get("waiting") for s in self.queue.snapshot().values())))
            write_models(folder, {"endpoints": {"local": {"url": other.url}}})
            first.join(5)
            second.join(5)
        self.assertEqual((len(self.server.requests), len(other.requests)), (1, 1),
                         "czekające wywołanie poszło na serwer z aktualnych ustawień")

    def test_cancel_interrupts_running_and_clears_waiting(self):
        self.server.delay = 5.0
        cancel = Cancel()
        results = []
        threads = [self.chat(results, cancel=cancel) for _ in range(2)]
        lane = queue_mod.lane_of(self.server.url)
        self.assertEqual(lane, f"http://127.0.0.1:{self.server.server_address[1]}")
        self.assertTrue(wait_for(lambda: len(self.server.requests) == 1 and self.queue.snapshot()
                                 .get(lane, {}).get("waiting")))
        started = time.monotonic()
        cancel.set()
        for thread in threads:
            thread.join(3)
        self.assertLess(time.monotonic() - started, 2.0, "zamykanie usługi: trwające i czekające kończą się od razu")
        self.assertEqual(len(results), 2)
        self.assertTrue(all("przerwane" in str(r) for r in results), results)
        self.assertEqual(len(self.server.requests), 1, "czekające nie dotarło do serwera")
        self.assertEqual(self.queue.snapshot(), {})


class ReportAndCardsTest(StandupWorld):
    """Raport (jedno wywołanie na projekt, niski priorytet) i opisy kart (wysoki) naraz na wspólnej kolejce
    procesu i atrapie, która jak Ollama obsługuje jedno zapytanie naraz."""

    def setUp(self):
        self.queue = queue_mod.Queue()   # świeża kolejka procesu na czas testu (raport i karty biorą shared())
        patch = mock.patch.object(queue_mod, "_SHARED", self.queue)
        patch.start()
        self.addCleanup(patch.stop)
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
        self.lane = queue_mod.lane_of(server.url)
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
        # Atrapa jak Ollama: jedno zapytanie naraz, 1 s na odpowiedź; karta ma 1,6 s na wywołanie. Bez kolejki
        # karta czekałaby w serwerze za raportem (1 + 1 s > 1,6 s) i dostała mylące „nie odpowiedział”.
        server = self.server(delay=1.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.6)
        self.assertIsNotNone(job)
        out = {}
        report = threading.Thread(target=lambda: out.setdefault("report", summarize.summarize(
            self.report, mode="local", home=self.p["home"], models_config=config, timeout=1.6)))
        report.start()
        self.assertTrue(wait_for(lambda: len(server.requests) == 1), "raport pyta o pierwszy projekt")
        cards = threading.Thread(target=lambda: out.setdefault("cards", d.run(job)))
        cards.start()
        self.assertTrue(wait_for(lambda: (self.queue.snapshot().get(self.lane) or {}).get("waiting")))
        snap = self.queue.snapshot()[self.lane]
        self.assertEqual(snap["running"]["role"], "summary")
        self.assertEqual([w["role"] for w in snap["waiting"]], ["describe"], "karta czeka w kolejce, nie w serwerze")
        report.join(15)
        cards.join(15)
        self.assertTrue(out["cards"], "karta dostała opis modelu — bez mylącego „nie odpowiedział”")
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
        blocker = Blocker(self.queue, lane=self.lane, role="describe", model="qwen3:8b")
        try:
            started = time.monotonic()
            result = summarize.summarize(self.report, mode="local", home=self.p["home"], models_config=config,
                                         budget=0.4)
            self.assertLess(time.monotonic() - started, 2.0)
        finally:
            blocker.release()
        self.assertEqual(result["source"], "rules")
        self.assertTrue(re.fullmatch(r"Model lokalny niedostępny — opis bez modelu \(kolejka: czekał \d+(\.\d)? s — serwer "
                                     r"zajęty: opisy kart, 127\.0\.0\.1:\d+\)", result["note"]), result["note"])
        self.assertEqual(server.requests, [], "budżet raportu minął w kolejce — serwer nie był pytany")

    def test_card_wait_limit_gives_an_honest_note(self):
        server = self.server(delay=0.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.0)
        d.wait = 0.2
        blocker = Blocker(self.queue, lane=self.lane, role="summary", model="qwen3:8b")
        try:
            self.assertFalse(d.run(job))
        finally:
            blocker.release()
        d.decorate(boards)
        note = boards[0]["tickets"][0]["describeNote"]
        self.assertRegex(note, r"^Model lokalny niedostępny — opis bez modelu \(kolejka: czekał \d+(\.\d)? s — "
                               r"serwer zajęty: raport poranny, 127\.0\.0\.1:\d+\)$")
        self.assertEqual(server.requests, [])

    def settings(self, folder, url, **extra):
        from .test_models_core import write_models
        return write_models(folder, {"endpoints": {"local": {"url": url}, **extra.pop("more", {})}, **extra})

    def test_report_keeps_the_settings_it_started_with(self):
        """Raport: jeden przebieg = jedne ustawienia. Zmiana `default` w trakcie nie przenosi reszty projektów
        na inny serwer, a host, remote i model w wyniku (stopka) mówią, dokąd naprawdę poszły fakty."""
        import os
        import shutil
        import tempfile
        first = self.server(delay=0.5)
        other = start_server(self, {("POST", "/api/chat"): (200, self.respond)})
        folder = tempfile.mkdtemp(prefix="tide-queue-report-")
        self.addCleanup(shutil.rmtree, folder)
        path = self.settings(folder, first.url, more={"mac": {"url": other.url}})
        with mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": path}):
            out = {}
            report = threading.Thread(target=lambda: out.setdefault("report", summarize.summarize(
                self.report, mode="local", home=self.p["home"])))
            report.start()
            self.assertTrue(wait_for(lambda: first.requests), "raport pyta o pierwszy projekt")
            self.settings(folder, first.url, more={"mac": {"url": other.url}}, default="mac")
            report.join(10)
        result = out["report"]
        self.assertEqual((result["source"], result["engine"]), ("model", "local"))
        self.assertEqual(len(first.requests), 2, "wszystkie projekty na serwerze z początku przebiegu")
        self.assertEqual(other.requests, [])
        self.assertEqual((result["host"], result["remote"]), (f"127.0.0.1:{first.server_address[1]}", False))

    def test_card_settings_changed_while_waiting_gives_rules_with_note(self):
        """Opisy kart nie przypinają ustawień: wywołanie idzie tam, gdzie wskazuje models.json w chwili startu.
        Gdy to nie ten model, który partia założyła (tożsamość w pamięci, kontekst), opis nie trafia do pamięci
        pod złą tożsamością — karta ma reguły i informację, a następna próba bierze już nowe ustawienia."""
        import os
        import shutil
        import tempfile
        server = self.server(delay=0.0)
        other = start_server(self, {("POST", "/api/chat"): (200, self.respond)})
        folder = tempfile.mkdtemp(prefix="tide-queue-cards-")
        self.addCleanup(shutil.rmtree, folder)
        path = self.settings(folder, server.url)
        with mock.patch.dict(os.environ, {"TIDE_MODELS_CONFIG": path}):
            d, boards, job = self.describer(None, timeout=5.0)
            blocker = Blocker(self.queue, lane=self.lane, role="summary", model="qwen3:8b")
            out = {}
            worker = threading.Thread(target=lambda: out.setdefault("ok", d.run(job)))
            worker.start()
            try:
                self.assertTrue(wait_for(lambda: (self.queue.snapshot().get(self.lane) or {}).get("waiting")))
                self.settings(folder, other.url)       # ten sam punkt `local`, inny serwer
            finally:
                blocker.release()
            worker.join(10)
            self.assertFalse(out["ok"])
            self.assertEqual(len(other.requests), 1, "wywołanie poszło według aktualnych ustawień")
            self.assertEqual(d._entries, {}, "nic w pamięci pod tożsamością z początku partii")
            d.decorate(boards)
        note = boards[0]["tickets"][0]["describeNote"]
        self.assertEqual(note, "Model lokalny niedostępny — opis bez modelu (ustawienia modeli zmieniły się "
                               "w trakcie wywołania)")
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "rules")

    def test_describer_stop_removes_its_waiting_call(self):
        server = self.server(delay=0.0)
        config = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        d, boards, job = self.describer(config, timeout=1.0)
        blocker = Blocker(self.queue, lane=self.lane, role="summary", model="qwen3:8b")
        try:
            out = {}
            worker = threading.Thread(target=lambda: out.setdefault("ok", d.run(job)))
            worker.start()
            self.assertTrue(wait_for(lambda: (self.queue.snapshot().get(self.lane) or {}).get("waiting")))
            started = time.monotonic()
            d.stop()
            worker.join(2)
            self.assertLess(time.monotonic() - started, 1.0, "zamykanie usługi nie czeka, aż raport zwolni serwer")
            self.assertFalse(out["ok"])
            self.assertEqual(self.queue.snapshot()[self.lane]["waiting"], [])
        finally:
            blocker.release()


if __name__ == "__main__":
    unittest.main()
