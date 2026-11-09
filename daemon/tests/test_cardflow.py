"""Przepływ karty, wejście „Od razu” (PT-58), w procesie testu: BoardMonitor z CardFlowMonitor — te same metody,
które wywołuje /Board (start_card = StartCard, move_card = MoveCard, snapshot_json = Snapshot, work_json = Work),
oraz AgentMonitor z prawdziwym skanerem dzienników (lista agentów).

Ten sam przebieg co test_cardflow_dbus.py, ale bez magistrali: tam, gdzie prywatny dbus-daemon nie wystartuje
(piaskownica bez gniazd uniksowych), te testy nadal sprawdzają zachowanie usługi. Procesy kroków są prawdziwe
(atrapy `claude`, `systemd-run`, `systemctl` z tests/flowfakes.py), git i repozytoria — prawdziwe, tymczasowe.
"""
import json
import os
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from tide_daemon.cardflow_monitor import CardFlowMonitor

from . import flowfakes
from .privatebus import app, wait_until
from .test_cardflow_dbus import CLAUDE_MD, git, test_root


class _FlowMonitor(CardFlowMonitor):
    """Monitor testu w tym samym procesie co ręczne operacje na silniku."""

    def start(self):
        # Produkcyjny stop kończy usługę. Fixture dopuszcza ponowny start tego
        # samego monitora po barierze w stop(), także po anulowaniu ticka.
        self._stopped = False
        super().start()

    def stop(self):
        # Produkcja kończy cały proces. Test zachowuje proces i zaraz dotyka silnika
        # albo dziennika, więc musi najpierw dokończyć biegnący tick starego monitora.
        pools = (self._pool, self._hosting_pool)
        super().stop()
        for pool in pools:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        app().processEvents()
        # Anulowany tick nie dostaje callbacka `after`; ten sam monitor testu może ruszyć ponownie.
        self._ticking = False


class MonitorFixtureTest(unittest.TestCase):
    def test_worker_barrier_keeps_start_and_environment_actions_available(self):
        app()
        engine = mock.Mock()
        engine.journal.lock = threading.RLock()
        engine.journal.take_dirty.return_value = []
        engine.take_notices.return_value = []
        engine.start.return_value = {"ok": True, "ticket": "PT-1"}
        engine.approve_environment.return_value = {"ok": True, "boxWait": ""}
        monitor = _FlowMonitor(engine)
        self.addCleanup(monitor.stop)
        fixture = FlowInProcess()
        fixture.flow = monitor
        entered, release = threading.Event(), threading.Event()

        def tick():
            entered.set()
            release.wait(2)

        engine.tick.side_effect = tick
        monitor.start()
        self.assertTrue(entered.wait(2))
        timer = threading.Timer(0.05, release.set)
        timer.start()
        self.addCleanup(timer.join)
        self.addCleanup(release.set)
        fixture.stop_flow_worker()
        self.assertTrue(release.is_set(), "bariera czeka na bieżący tick")
        self.assertFalse(monitor._timer.isActive())
        self.assertFalse(monitor._ticking)

        replies = []
        monitor.start_card("K-1", '{"entry":"now"}', replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=2))
        self.assertTrue(json.loads(replies.pop())["ok"], "bariera nie zamyka API StartCard")
        monitor.approve_environment("K-1", "tide-box: 1\n", False, replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=2))
        self.assertTrue(json.loads(replies.pop())["ok"], "bariera nie zamyka API ApproveCardEnvironment")
        self.assertEqual(engine.tick.call_count, 1, "akcje nie wznawiają odpytywania")
        monitor.start()
        self.assertTrue(wait_until(lambda: engine.tick.call_count == 2, timeout=2))
        monitor.stop()
        monitor.start_card("K-1", '{"entry":"now"}', replies.append)
        monitor.approve_environment("K-1", "tide-box: 1\n", False, replies.append)
        self.assertEqual([json.loads(reply)["error"] for reply in replies], ["unavailable", "unavailable"])
        self.assertEqual(engine.start.call_count, 1, "zamknięty monitor nadal odrzuca akcje")
        self.assertEqual(engine.approve_environment.call_count, 1)

    def test_restart_after_cancelling_a_queued_tick_polls_again(self):
        app()
        engine = mock.Mock()
        engine.journal.lock = threading.RLock()
        engine.journal.take_dirty.return_value = []
        engine.take_notices.return_value = []
        monitor = _FlowMonitor(engine)
        entered, release = threading.Event(), threading.Event()

        def busy():
            entered.set()
            release.wait(2)

        monitor._submit(busy, lambda future: None)
        self.addCleanup(monitor.stop)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        monitor.tick()  # Tick czeka w kolejce i zostanie anulowany przez stop().
        timer = threading.Timer(0.05, release.set)
        timer.start()
        self.addCleanup(timer.join)
        monitor.stop()
        monitor.start()
        self.assertTrue(wait_until(lambda: engine.tick.call_count == 1, timeout=2),
                        "anulowany tick nie może zatrzymać odpytywania po ponownym starcie monitora testu")

    def test_stop_settles_inflight_tick_before_manual_engine_operations(self):
        app()
        engine = mock.Mock()
        engine.journal.lock = threading.RLock()
        engine.journal.take_dirty.return_value = []
        engine.take_notices.return_value = []
        monitor = _FlowMonitor(engine)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def tick():
            entered.set()
            release.wait(2)
            finished.set()

        monitor._submit(tick, lambda future: None)
        pool = monitor._pool
        self.addCleanup(lambda: app().processEvents())
        self.addCleanup(lambda: pool.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        timer = threading.Timer(0.05, release.set)
        timer.start()
        self.addCleanup(timer.join)
        monitor.stop()
        self.assertTrue(finished.is_set(), "stary tick nie może modyfikować dziennika po zatrzymaniu monitora testu")


class FlowInProcess(unittest.TestCase):
    guard_minutes = 15.0
    auto_consent = False
    escalate_errors = False

    def setUp(self):
        app()
        self.base = test_root()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        for name in ("ws", "home", "run", "tmp", "config/tide", "state"):
            os.makedirs(os.path.join(self.base, name), exist_ok=True)
        self.runtime = os.path.join(self.base, "run")
        os.chmod(self.runtime, 0o700)
        gitconfig = os.path.join(self.base, "gitconfig")
        with open(gitconfig, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        self.fakes = flowfakes.install(os.path.join(self.base, "fakebin"))
        self.settings = os.path.join(self.base, "config", "tide", "cardflow.json")
        with open(self.settings, "w", encoding="utf-8") as fh:
            # Testy decyzji sprawdzają ręczne zgody; automatyczne mają własny test (auto_consent = True).
            json.dump({"stepGuardMinutes": self.guard_minutes, "autoConsent": self.auto_consent,
                       "reviewRoundsBeforeQuestion": 3, "escalateErrors": self.escalate_errors}, fh)
        patcher = mock.patch.dict(os.environ, {
            "HOME": os.path.join(self.base, "home"), "XDG_RUNTIME_DIR": self.runtime,
            "XDG_STATE_HOME": os.path.join(self.base, "state"), "XDG_CONFIG_HOME": os.path.join(self.base, "config"),
            "TIDE_BOARD_DIR": os.path.join(self.base, "boxboard"),
            "PATH": os.path.dirname(self.fakes["claude"]) + os.pathsep + "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": gitconfig, "GIT_CONFIG_NOSYSTEM": "1", "TIDE_FAKE_LOG": os.path.join(self.base, "calls.log")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = os.environ["TIDE_FAKE_LOG"]
        self.repo = os.path.join(self.base, "ws", "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(CLAUDE_MD)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.master = git(self.repo, "rev-parse", "master").strip()
        self.root = os.path.join(self.base, "ws", "tide-board")

        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        self.store = BoardStore(self.root)
        self.board = BoardMonitor(self.store, debounce_ms=200, poll_ms=60_000)
        self.addCleanup(self.stop_board)
        self.flow = self.make_flow()
        self.board.start()
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps({"repo": self.repo})))["ok"])

    def make_flow(self):
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.models import executors

        engine = FlowEngine(self.store, claude_projects=os.path.join(self.base, "home", ".claude", "projects"),
                            systemd_run=self.fakes["systemd-run"], catalog=executors.default_catalog,
                            tmp_root=os.path.join(self.base, "tmp"), settings=self.settings,
                            which=lambda name: self.fakes.get(name), grace=5.0)
        flow = _FlowMonitor(engine, self.board, poll_ms=150)
        self.board.flow = flow
        stop = flow.stop

        def stop_and_drain():
            # Monitor w produkcji zamyka pulę bez czekania. Test musi zakończyć jej pracę przed
            # odtworzeniem os.environ i usunięciem repozytoriów, bo wątek korzysta z obu tych rzeczy.
            flow._timer.stop()
            if flow._pool is not None:
                flow._pool.shutdown(wait=True, cancel_futures=False)
            stop()
            app().processEvents()

        flow.stop = stop_and_drain
        flow.start()
        self.addCleanup(self.stop_flow, flow)
        return flow

    def stop_flow(self, flow=None):
        """Kończymy także bieżącą pracę przed zmianą środowiska testu lub ręcznym użyciem silnika."""
        flow = flow or self.flow
        flow.stop()

    def stop_board(self):
        self.board.stop()
        # QObject monitora może pozostać w cyklu Python/Qt. Nie zostawiamy instancji inotify
        # do końca wielotysięcznego zestawu testów w jednym procesie.
        from PyQt6.QtCore import QCoreApplication, QEvent
        watcher = self.board._watcher
        watcher.deleteLater()
        QCoreApplication.sendPostedEvents(watcher, QEvent.Type.DeferredDelete)

    def restart(self):
        """Nowy silnik i monitor na tej samej tablicy (bez procesów kroków w pamięci) — jak usługa po restarcie."""
        self.stop_flow()
        self.flow = self.make_flow()

    def stop_flow_worker(self):
        """Pauza odpytywania i bariera wątku; API monitora nadal przyjmuje akcje testu."""
        self.flow._timer.stop()
        if self.flow._pool is not None:
            self.flow._pool.shutdown(wait=True, cancel_futures=False)
            self.flow._pool = None
        # Dostarczamy zakończenia także ticka: kolejne start() ma wznowić odpytywanie.
        app().processEvents()
        self.flow._ticking = False

    # --- pomocnicze -------------------------------------------------------------------------------

    def card(self, title="Eksport CSV", description="Dodaj eksport.\n\nATRAPA: gotowe", column="todo") -> str:
        reply = json.loads(self.board.create_card(json.dumps({"title": title, "project": "proj",
                                                              "description": description, "column": column})))
        self.assertTrue(reply["ok"], reply)
        return reply["id"]

    def start(self, card_id, **extra) -> dict:
        box = []
        self.board.start_card(card_id, json.dumps({"entry": "now", "executor": "Sonnet", **extra}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=60), "StartCard bez odpowiedzi")
        return json.loads(box[0])

    def approve_environment(self, card_id) -> dict:
        """Jawna zgoda na minimalne środowisko; budowa jest atrapą, runner hostowy zostaje bez Incusa."""
        box = []
        with mock.patch.object(self.flow.engine, "box_launcher", lambda *_args: None):
            self.flow.approve_environment(card_id, "tide-box: 1\n", False, box.append)
            self.assertTrue(wait_until(lambda: box, timeout=10), "ApproveCardEnvironment bez odpowiedzi")
        reply = json.loads(box[0])
        self.assertTrue(reply["ok"], reply)
        return reply

    def shown(self, card_id) -> dict:
        return next(c for c in json.loads(self.board.snapshot_json())["cards"] if c["id"] == card_id)

    def wait_column(self, card_id, column, timeout=30.0) -> dict:
        found = wait_until(lambda: self.shown(card_id)["boardColumn"] == column, timeout=timeout)
        card = self.shown(card_id)
        self.assertTrue(found, f"{card_id} w {card['boardColumn']}, a nie {column}: {card.get('derived')}\n"
                        f"{json.dumps(self.events(card_id), ensure_ascii=False, indent=1)}")
        # Kolumna liczy się z kopii dziennika w usłudze; plik w tablicy dostaje to samo zdarzenie chwilę później.
        journal = self.flow.engine.journal
        self.assertTrue(wait_until(lambda: self.events(card_id) == journal.read(card_id), timeout=5))
        return card

    def events(self, card_id) -> list:
        try:
            with open(os.path.join(self.root, "flow", card_id, "journal.jsonl"), encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []

    def of(self, card_id, kind) -> list:
        return [e for e in self.events(card_id) if e["type"] == kind]

    def ticket_finished(self, card_id) -> list:
        """`step.finished` kroków ticketu, bez kroku opiekuna karty (PT-143), który rusza po drugim błędzie."""
        tickets = {s["step"] for s in self.of(card_id, "step.started") if s.get("kind") != "caretaker"}
        return [e for e in self.of(card_id, "step.finished") if e["step"] in tickets]

    def results(self, card_id, action) -> list:
        return [e for e in self.of(card_id, "result") if e.get("action") == action]

    def started(self, card_id) -> dict:
        found = wait_until(lambda: self.of(card_id, "step.started"), timeout=15)
        self.assertTrue(found, self.events(card_id))
        return found[0]

    def unit_pid(self, step) -> int:
        return wait_until(lambda: flowfakes.unit_pid(self.runtime, step["unit"]), timeout=5)

    def assert_changes_kept(self, finished, step, branch):
        wip = finished.get("wip") or {}
        if wip.get("committed"):
            self.assertIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", branch).split())
        else:   # piaskownica gita usługi (bwrap) niedostępna — zmiany zostają w worktree, nigdy git bez piaskownicy
            self.assertEqual(wip.get("error"), "no-sandbox", wip)
            self.assertTrue(os.path.isfile(os.path.join(step["worktree"], "ATRAPA.md")))


class NowInProcessTest(FlowInProcess):
    def wait_now_closed(self, card_id, ticket="PT-1", timeout=30.0):
        """Koniec całego scenariusza Od razu, a nie przejściowe review po scaleniu ticketu."""
        journal = self.flow.engine.journal
        last = {}

        def completed():
            # Nie czytaj połowy zapisu ani nie czekaj na blokadzie dłużej niż timeout.
            if not journal.lock.acquire(blocking=False):
                return False
            try:
                events = self.events(card_id)
                mirror = journal.read(card_id)
                card = self.shown(card_id)
                last.update(journal=events, mirror=mirror, snapshot=card)
                starts = [e for e in events if e["type"] == "step.started"]
                finishes = [e for e in events if e["type"] == "step.finished"]
                verdicts = [e for e in events if e["type"] == "review.verdict"]
                closed = [e for e in events if e["type"] == "card.closed"]
                if ([s["kind"] for s in starts] != ["ticket", "review", "close"] or
                        [s.get("ticket") for s in starts[:2]] != [ticket, ticket] or
                        [f["step"] for f in finishes] != [s["step"] for s in starts] or
                        any(f["result"] != "done" for f in finishes)):
                    return False
                if (len(verdicts) != 1 or verdicts[0].get("ticket") != ticket or
                        verdicts[0].get("step") != starts[1]["step"] or
                        verdicts[0].get("verdict") != "APPROVE" or len(closed) != 1 or
                        closed[0].get("step") != starts[2]["step"] or
                        closed[0]["seq"] <= finishes[2]["seq"]):
                    return False
                flow = (card.get("derived") or {}).get("flow") or {}
                return (events == mirror and card["boardColumn"] == "review" and flow.get("closed") and
                        card["derived"] == self.flow.engine.derived(card) and card)
            finally:
                journal.lock.release()

        found = wait_until(completed, timeout=timeout)
        self.assertTrue(found, f"{card_id}: brak spójnego zakończenia ticket → review → close\n"
                        + json.dumps(last, ensure_ascii=False, indent=1))
        return found

    def test_codex_executor_completes_from_start_to_review(self):
        card_id = self.card()
        reply = self.start(card_id, executor="Codex mocny", preferredProvider="openai")
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "review")
        started = self.of(card_id, "step.started")[0]
        self.assertEqual((started["program"], started["model"], started["provider"]),
                         ("codex", "gpt-6.1-sol", "openai"))
        with open(os.path.join(started["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertEqual(call["argv"][0], "exec")
        self.assertIn("--model=gpt-6.1-sol", call["argv"])
        self.assertIn('model_reasoning_effort="high"', call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--sandbox") + 1], "workspace-write")
        self.assertIn('approval_policy="never"', call["argv"])
        self.assertIn("Dozwolone polecenia i wzorce", call["prompt"])
        self.assertNotIn("--allowedTools", call["argv"])
        self.assertEqual(self.of(card_id, "card.started")[0]["preferredProvider"], "openai")
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "done")

    def test_local_executor_refuses_offline_then_runs_only_on_local_server(self):
        from tide_daemon.models import config as models_config
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "codex",
                       "executors": [{"name": "Lokalny", "provider": "local", "classes": ["lokalny"]},
                                     {"name": "Lokalny recenzent", "provider": "local", "program": "codex",
                                      "model": "qwen2.5-coder:7b", "endpoint": "local",
                                      "classes": ["lokalny"]}]}, fh)
        engine = self.flow.engine
        engine.models_config = lambda: models_config.load(path)
        card_id = self.card()
        engine.model_probe = lambda endpoint: {"online": False, "error": "", "models": []}
        refused_unchecked = self.start(card_id, executor="Lokalny", preferredProvider="local")
        self.assertEqual((refused_unchecked["ok"], refused_unchecked["error"]),
                         (False, "local-unavailable"), refused_unchecked)
        engine.model_probe = lambda endpoint: {"online": False, "error": "serwer nie odpowiada", "models": []}
        refused = self.start(card_id, executor="Lokalny", preferredProvider="local")
        self.assertEqual((refused["ok"], refused["error"]), (False, "local-unavailable"), refused)
        self.assertEqual(self.events(card_id), [])
        engine.model_probe = lambda endpoint: {"online": True, "kind": "ollama",
                                                "models": [{"name": "qwen3-coder:30b"},
                                                           {"name": "qwen2.5-coder:7b"}]}
        reply = self.start(card_id, executor="Lokalny", preferredProvider="local")
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "review")
        started = self.of(card_id, "step.started")[0]
        self.assertEqual((started["program"], started["provider"], started["local"]),
                         ("codex", "local", True))
        reviewer = next(s for s in self.of(card_id, "step.started") if s["kind"] == "review")
        self.assertEqual((reviewer["executor"], reviewer["provider"], reviewer["local"]),
                         ("Lokalny recenzent", "local", True))
        with open(os.path.join(started["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn("--oss", call["argv"])
        self.assertEqual(call["env"]["CODEX_OSS_BASE_URL"], "http://127.0.0.1:11434/v1")
        self.assertNotIn("OPENAI_API_KEY", call["env"])

    def test_other_provider_uses_its_endpoint_without_putting_token_in_argv_or_journal(self):
        from tide_daemon.models import config as models_config
        token = os.path.join(self.base, "config", "tide", "other.token")
        with open(token, "w", encoding="utf-8") as fh:
            fh.write("sekret-testowy\n")
        os.chmod(token, 0o600)
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"providers": {"other": {"label": "Inny", "url": "https://example.test/v1",
                                                 "api": "openai", "tokenFile": token}},
                       "executors": [{"name": "Inny", "program": "codex", "model": "model-inny",
                                      "provider": "other", "classes": ["standard"]},
                                     {"name": "Inny recenzent", "program": "codex", "model": "model-drugi",
                                      "provider": "other", "classes": ["mocny"]}]}, fh)
        self.flow.engine.models_config = lambda: models_config.load(path)
        card_id = self.card()
        reply = self.start(card_id, executor="Inny", preferredProvider="other")
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "review")
        step = self.of(card_id, "step.started")[0]
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn('model_provider="tide-other"', call["argv"])
        self.assertIn('model_providers.tide-other.base_url="https://example.test/v1"', call["argv"])
        self.assertNotIn("sekret-testowy", json.dumps(call["argv"]))
        self.assertNotIn("sekret-testowy", json.dumps(self.events(card_id)))

    def test_two_cards_in_one_repo_reserve_different_ticket_numbers(self):
        first = self.card(title="Ten sam tytuł")
        second = self.card(title="Ten sam tytuł")
        one = self.start(first)
        two = self.start(second)
        self.assertTrue(one["ok"], one)
        self.assertTrue(two["ok"], two)
        self.assertEqual((one["ticket"], two["ticket"]), ("PT-1", "PT-2"))
        self.wait_column(first, "review")
        self.wait_column(second, "review")

    def test_finished_card_cannot_start_an_invisible_step(self):
        for column in ("done", "archived"):
            card_id = self.card(column=column)
            reply = self.start(card_id)
            self.assertEqual((reply["ok"], reply["error"]), (False, "card-finished"), reply)
            self.assertEqual(self.events(card_id), [])

    def test_manual_check_choice_is_kept_in_the_card_journal_and_marker(self):
        card_id = self.card()
        self.assertTrue(self.start(card_id, check="manual")["ok"])
        shown = self.wait_column(card_id, "review")
        self.assertEqual(self.of(card_id, "card.started")[0]["check"], "manual")
        self.assertEqual(shown["derived"]["check"], "manual")

    def test_nonzero_exit_with_done_file_is_a_step_error(self):
        """Nie wolno scalać pracy, gdy program zgłosił błąd mimo zapisanego wcześniej pliku wyniku."""
        fake = self.fakes["claude"]
        with open(fake, encoding="utf-8") as fh:
            script = fh.read()
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script + "\nsys.exit(7)\n")
        card_id = self.card()
        reply = self.start(card_id)
        self.wait_column(card_id, "waiting", timeout=15)
        finished = self.of(card_id, "step.finished")
        self.assertEqual((finished[0]["result"], finished[0]["reason"], finished[0]["exit"]),
                         ("error", "exit", 7))
        self.assertEqual(self.results(card_id, "merge"), [])
        self.assertNotIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["cardBranch"]).split())

    def test_now_runs_one_background_ticket_from_launch_to_review(self):
        from tide_daemon.agents import AgentScanner
        from tide_daemon.monitor import AgentMonitor
        legacy = self.card("Dawna karta", "Bez dziennika.")
        card_id = self.card()
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["ticket"], "PT-1")
        self.assertTrue(reply["cardBranch"].startswith("card/k-2-"), reply)
        self.assertTrue(reply["branch"].startswith("ticket/pt-1-"), reply)

        card = self.wait_now_closed(card_id)
        self.assertIn("PT-1", card["derived"]["note"])
        types = [e["type"] for e in self.events(card_id)]
        self.assertEqual(types[0], "card.started")
        for kind in ("step.started", "step.finished"):
            self.assertEqual(types.count(kind), 3, types)  # ticket, niezależny review i domknięcie
        self.assertEqual([s["kind"] for s in self.of(card_id, "step.started")],
                         ["ticket", "review", "close"])
        self.assertEqual(types.count("review.verdict"), 1, types)
        starts = self.of(card_id, "step.started")
        self.assertEqual([f["step"] for f in self.of(card_id, "step.finished")],
                         [s["step"] for s in starts])
        self.assertEqual([f["result"] for f in self.of(card_id, "step.finished")], ["done"] * 3)
        verdict = self.of(card_id, "review.verdict")[0]
        self.assertEqual((verdict["ticket"], verdict["step"], verdict["verdict"], verdict["reviewer"]),
                         ("PT-1", starts[1]["step"], "APPROVE", starts[1]["executor"]))
        self.assertNotEqual(starts[0]["model"], starts[1]["model"], "niezależne review")
        self.assertEqual(self.of(card_id, "card.closed")[0]["step"], starts[2]["step"])
        self.assertTrue(card["derived"]["flow"]["closed"])
        self.assertTrue(all(e["v"] == 1 and e["card"] == card_id for e in self.events(card_id)))
        self.assertEqual([e["seq"] for e in self.events(card_id)], list(range(1, len(types) + 1)))
        launched = self.of(card_id, "card.started")[0]
        self.assertEqual((launched["entry"], launched["executor"]), ("now", "Sonnet"))
        finished = self.of(card_id, "step.finished")[0]
        self.assertEqual((finished["result"], finished["ticket"]), ("done", "PT-1"))
        intents = {(e["action"], e["key"]) for e in self.of(card_id, "intent")}
        self.assertEqual(intents, {(e["action"], e["key"]) for e in self.of(card_id, "result")})
        self.assertEqual({a for a, _k in intents},
                         {"reserve", "card-branch", "ticket-branch", "step-start", "merge", "cleanup"})
        # Numer ticketu jest w rejestrze rezerwacji (kontrola numerów go widzi).
        from tide_daemon.coordination import reservations
        self.assertEqual([(r["prefix"], r["number"]) for r in reservations.load(self.repo)], [("PT", 1)])

        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["cardBranch"]).split())
        self.assertIn(f"Merge ticket PT-1 into card {card_id}", git(self.repo, "log", "--format=%s", reply["cardBranch"]))
        self.assertTrue(os.path.isfile(os.path.join(reply["cardWorktree"], "ATRAPA.md")), "worktree karty po scaleniu")
        self.assertEqual(git(reply["cardWorktree"], "status", "--porcelain"), "")

        step = self.of(card_id, "step.started")[0]
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn("-p", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--session-id") + 1], step["session"])
        self.assertIn("--model=sonnet", call["argv"])
        self.assertIn("acceptEdits", call["argv"])
        self.assertIn(f"Karta {card_id}: Eksport CSV", call["prompt"])
        self.assertIn("Dodaj eksport.", call["prompt"])
        self.assertIn("Ticket: PT-1", call["prompt"])
        self.assertEqual(call["cwd"], step["worktree"])
        self.assertNotIn("SSH_AUTH_SOCK", call["env"])
        self.assertEqual(call["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/nonexistent/tide-step/bus")
        self.assertFalse(os.path.exists(self.calls), "żadna karta Konsoli")
        kept = os.path.join(self.root, "flow", card_id, "steps", step["step"])
        self.assertEqual(sorted(os.listdir(kept)), ["note.md", "result.json"])
        self.assertFalse(os.path.exists(os.path.join(self.base, "tmp", f"tide-step-{step['step']}")),
                         "katalog tymczasowy kroku sprzątnięty")
        self.assertTrue(wait_until(lambda: git(self.root, "status", "--porcelain") == "", timeout=5),
                        git(self.root, "status", "--porcelain"))
        subjects = git(self.root, "log", "--format=%s").splitlines()
        self.assertIn(f"{card_id}: card flow", subjects)
        self.assertNotIn("sync external changes", subjects)

        old = self.shown(legacy)
        self.assertEqual(old["boardColumn"], "todo")
        self.assertIsNone(old.get("derived"))

        # Lista agentów: stała data w atrapowym dzienniku nie może ukryć sesji przed sprawdzeniem filtra.
        monitor = AgentMonitor(AgentScanner(os.path.join(self.base, "home", ".claude"),
                                            [os.path.join(self.base, "x")], max_age=float("inf")),
                               use_processes=False)
        monitor.rescan()
        self.assertTrue([s for s in monitor.sessions if s.get("sessionId") == step["session"]],
                        "bez filtra skaner widzi sesję kroku (inaczej test niczego nie sprawdza)")
        monitor.hidden = self.flow.engine.is_hidden
        monitor.recompute()
        self.assertFalse([s for s in monitor.sessions if s.get("sessionId") == step["session"]], monitor.sessions)
        work = json.loads(self.board.work_json())
        self.assertEqual([i["kind"] for i in work["items"] if step["session"] in json.dumps(i)], [])

    def test_now_waits_for_close_and_its_published_snapshot(self):
        engine = self.flow.engine
        release = threading.Event()
        advance = engine._advance_close

        def delayed_close(card, state):
            if release.is_set():
                return advance(card, state)

        with mock.patch.object(engine, "_advance_close", side_effect=delayed_close):
            card_id = self.card()
            self.assertTrue(self.start(card_id)["ok"])
            # Poprzedni warunek kończy oczekiwanie, choć close jeszcze nie wystartował.
            card = self.wait_column(card_id, "review")
            self.assertFalse(card["derived"]["flow"]["closed"])
            self.assertEqual(len(self.of(card_id, "step.finished")), 2)
            with self.assertRaises(AssertionError) as missing:
                self.wait_now_closed(card_id, timeout=0.15)
            for key in ("journal", "mirror", "snapshot"):
                self.assertIn(key, str(missing.exception))
            # Teraz dziennik może się domknąć, lecz migawka nadal pokazuje dawny stan.
            with mock.patch.object(self.board, "_apply", return_value=None):
                release.set()
                self.assertTrue(wait_until(lambda: self.of(card_id, "card.closed"), timeout=30),
                                self.events(card_id))
                with self.assertRaises(AssertionError) as unpublished:
                    self.wait_now_closed(card_id, timeout=0.15)
                self.assertIn('"card.closed"', str(unpublished.exception))
                self.assertIn('"closed": false', str(unpublished.exception))
            self.board.publish()
            self.wait_now_closed(card_id)

    def test_now_wait_rejects_failed_close_with_diagnostics(self):
        engine = self.flow.engine
        finish = engine._finish

        def failed_close(card, step, **kwargs):
            if step.get("kind") == "close":
                kwargs["forced"] = {"result": "error", "reason": "kontrolowana awaria domknięcia"}
            return finish(card, step, **kwargs)

        with mock.patch.object(engine, "_finish", side_effect=failed_close):
            card_id = self.card()
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: any(
                e.get("reason") == "kontrolowana awaria domknięcia"
                for e in self.of(card_id, "step.finished")), timeout=30), self.events(card_id))
            with self.assertRaises(AssertionError) as failed:
                self.wait_now_closed(card_id, timeout=0.15)
            for text in ("journal", "snapshot", "kontrolowana awaria domknięcia"):
                self.assertIn(text, str(failed.exception))
            self.assertEqual(self.of(card_id, "card.closed"), [])

    def test_second_start_is_refused_while_the_card_works(self):
        card_id = self.card(description="ATRAPA: czekaj")
        self.assertTrue(self.start(card_id)["ok"])
        again = self.start(card_id)
        self.assertEqual((again["ok"], again["error"]), (False, "flow-active"), again)
        self.assertEqual(len(self.of(card_id, "card.started")), 1)
        self.call_done(card_id)

    def call_done(self, card_id):
        self.board.move_card(card_id, "done", 0)
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.finished"), timeout=20))

    def test_unknown_and_unavailable_executors_leave_no_artifacts(self):
        card_id = self.card()
        for name, code in (("Nieznany", "unknown-executor"), ("Lokalny", "local-unavailable")):
            reply = self.start(card_id, executor=name)
            self.assertEqual((reply["ok"], reply["error"]), (False, code), reply)
        mismatch = self.start(card_id, executor="Sonnet", preferredProvider="openai")
        self.assertEqual((mismatch["ok"], mismatch["error"]), (False, "provider-mismatch"), mismatch)
        reply = self.start(card_id, entry="planning")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-request"), reply)
        self.assertEqual(self.events(card_id), [])
        self.assertEqual(git(self.repo, "branch", "--format=%(refname:short)").split(), ["master"])
        self.assertFalse(os.path.exists(self.repo + "-worktrees"))

    def test_project_without_ticket_prefix_gets_one_from_the_repository_name(self):
        # 04.11, K-41: nowy projekt bez ticket-prefix w CLAUDE.md nie może zaczynać się od walki ze środowiskiem.
        from tide_daemon.coordination.reservations import repo_prefix
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# Projekt bez planu\n")
        card_id = self.card()
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        reserved = [e for e in self.events(card_id) if e["type"] == "result" and e.get("action") == "reserve"]
        self.assertTrue(reserved[0]["ticket"].startswith(repo_prefix(self.repo) + "-"), reserved)

    def test_missing_result_file_is_a_step_error(self):
        card_id = self.card(description="ATRAPA: bez-wyniku")
        reply = self.start(card_id)
        card = self.wait_column(card_id, "waiting")
        finished = self.ticket_finished(card_id)
        self.assertEqual([(f["result"], f["reason"]) for f in finished],
                         [("error", "no-result"), ("error", "no-result")])
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertIn("brak pliku wyniku", card["derived"]["note"])
        self.assertEqual(self.results(card_id, "merge"), [], "praca bez pliku wyniku nie idzie na gałąź karty")
        self.assertNotIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["cardBranch"]).split())

    def test_question_waits_for_the_user(self):
        card_id = self.card(description="ATRAPA: pytanie")
        self.start(card_id)
        card = self.wait_column(card_id, "waiting")
        self.assertIn("Który format daty?", card["derived"]["note"])
        self.assertEqual(card["derived"]["question"], "Który format daty?")
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "question")


class GuardInProcessTest(FlowInProcess):
    guard_minutes = 0.05   # 3 s

    def test_codex_guard_keeps_last_session_message(self):
        root = os.path.join(self.base, "codex-data")
        self.flow.engine.codex_roots = [root]
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id, executor="Codex mocny", preferredProvider="openai")
        step = self.started(card_id)
        sessions = os.path.join(root, "sessions", "2026", "10", "29")
        os.makedirs(sessions)
        log = os.path.join(sessions, "rollout-2026-10-29T12-00-00-codex-test.jsonl")
        records = [
            {"type": "session_meta", "payload": {"cwd": step["worktree"]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                   "content": [{"type": "output_text", "text": "Sprawdzam testy."}]}}
        ]
        with open(log, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.wait_column(card_id, "waiting", timeout=30)
        finished = self.of(card_id, "step.finished")[0]
        self.assertEqual((finished["result"], finished["reason"]), ("error", "no-progress"))
        self.assertEqual(finished["messages"], [{"role": "assistant", "text": "Sprawdzam testy."}])

    def test_guard_counts_busy_processor_as_progress(self):
        # K-35/PT-123: pełne `make test` w tle przez 25 min bez wyjścia — strażnik zatrzymał pracujący krok.
        ticks = iter(range(10_000))
        self.flow.engine.unit_cpu = lambda unit: next(ticks)
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        self.assertFalse(wait_until(lambda: self.of(card_id, "step.finished"), timeout=8))
        self.assertTrue(flowfakes.alive(self.unit_pid(step)))
        self.flow.engine.unit_cpu = lambda unit: 7       # procesor stoi — strażnik działa jak dotąd
        self.wait_column(card_id, "waiting", timeout=30)
        self.assertEqual(self.of(card_id, "step.finished")[0]["reason"], "no-progress")

    def test_guard_stops_a_step_without_progress_and_keeps_its_changes(self):
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.assertTrue(flowfakes.alive(pid))
        card = self.wait_column(card_id, "waiting", timeout=30)
        finished = self.ticket_finished(card_id)
        self.assertEqual([(f["result"], f["reason"]) for f in finished],
                         [("error", "no-progress"), ("error", "no-progress")])
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertIn("bez postępu", card["derived"]["note"])
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5), "krok zatrzymany")
        self.assertTrue(finished[0]["wip"]["stopped"])
        self.assert_changes_kept(finished[0], step, reply["branch"])

    def test_a_step_that_keeps_working_is_not_stopped(self):
        card_id = self.card(description="ATRAPA: powoli 5")   # 5 s dopisywania przy strażniku 3 s
        self.start(card_id)
        self.wait_column(card_id, "review", timeout=40)
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "done")


class RestartInProcessTest(FlowInProcess):
    def test_nonzero_exit_with_done_file_after_restart_is_not_merged(self):
        fake = self.fakes["claude"]
        with open(fake, encoding="utf-8") as fh:
            script = fh.read()
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script + "\nsys.exit(7)\n")
        card_id = self.card()
        self.flow._timer.stop()  # wyłączamy tylko zegar; monitor nadal przyjmuje StartCard
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        step = self.started(card_id)
        from tide_daemon.tasks import stepstop
        self.assertEqual(wait_until(lambda: stepstop.read_exit_code(step["stepDir"]), timeout=10), 7)
        self.flow.stop()  # dopiero teraz zamykamy monitor; nowy odczyta wynik z dysku
        self.flow = self.make_flow()
        self.wait_column(card_id, "waiting")
        finished = self.of(card_id, "step.finished")[0]
        self.assertEqual((finished["result"], finished["reason"], finished["exit"]), ("error", "exit", 7))
        self.assertEqual(self.results(card_id, "merge"), [])

    def test_reservation_replay_uses_the_same_number_after_registry_write(self):
        """Restart po zapisie rejestru, a przed wynikiem dziennika, nie spala drugiego numeru."""
        from tide_daemon.coordination import reservations
        card_id = self.card()
        key = "launch-1"
        got = reservations.reserve(self.repo, count=1, title="Eksport CSV", request_key=f"{card_id}:{key}")
        journal = self.flow.engine.journal
        journal.append(card_id, "card.started", entry="now", repo=self.repo, column="todo")
        journal.append(card_id, "intent", action="reserve", key=key, repo=self.repo, title="Eksport CSV")
        self.flow.engine.tick()
        self.assertEqual(self.results(card_id, "reserve")[0]["ticket"], got["ids"][0])
        self.assertEqual(len(reservations.load(self.repo)), 1)

    def test_restart_after_review_to_todo_with_the_same_stored_column_keeps_rework(self):
        """Ruch z kolumny wyliczonej zapisuje identyfikator, choć w pliku kolumna była już `todo`."""
        card_id = self.card()
        self.start(card_id)
        self.wait_column(card_id, "review")
        self.flow.stop()
        self.store.move_card(card_id, "todo", 0, "review")
        move_id = self.store.load().cards[int(card_id.partition("-")[2])].move_id
        self.assertTrue(move_id)
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "card.moved")) == 1, timeout=10), self.events(card_id))
        self.assertEqual(self.of(card_id, "card.moved")[0]["moveId"], move_id)
        # Tick zapisuje dziennik w workerze, a dopiero callback publikuje migawkę w wątku Qt.
        self.wait_column(card_id, "todo")

    def test_restart_after_store_move_to_backlog_pauses_the_card(self):
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.wait_column(card_id, "progress")
        self.flow.stop()
        self.store.move_card(card_id, "backlog", 0, "progress")
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.paused"), timeout=10), self.events(card_id))
        self.assertTrue(flowfakes.alive(pid))
        self.assertEqual(len(self.of(card_id, "card.moved")), 1)

    def test_restart_after_store_move_before_flow_event_finishes_the_card(self):
        """Zapis kolumny przed asynchronicznym zdarzeniem też musi przeżyć restart usługi."""
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.wait_column(card_id, "progress")
        self.flow.stop()
        self.store.move_card(card_id, "done", 0, "progress")
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.finished"), timeout=10), self.events(card_id))
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5))
        self.assertEqual(len(self.of(card_id, "card.moved")), 1)
        self.assertEqual([e["result"] for e in self.of(card_id, "step.finished")], ["stopped"])

    def test_restart_after_launch_result_recovers_the_running_step(self):
        """Awaria między wynikiem startu jednostki i step.started nie może osierocić kroku."""
        card_id = self.card(description="ATRAPA: powoli 5")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        journal = self.flow.engine.journal
        self.flow.stop()
        wait_until(lambda: False, timeout=0.3)
        events = self.events(card_id)
        self.assertEqual(events[-1]["type"], "step.started")
        truncated = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events[:-1])
        for path in (journal.path(card_id), journal.mirror_path(card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(truncated)
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.of(card_id, "step.started"), timeout=5), self.events(card_id))
        self.assertTrue(flowfakes.alive(pid), "krok nie wystartował drugi raz ani nie zginął")
        self.wait_column(card_id, "review", timeout=40)
        recovered = [e for e in self.of(card_id, "step.started") if e["kind"] == "ticket"]
        self.assertEqual(len(recovered), 1)
        self.assertEqual({k: v for k, v in recovered[0].items() if k != "ts"},
                         {k: v for k, v in step.items() if k != "ts"})
        self.assertEqual(len(self.results(card_id, "merge")), 1)

    def test_restart_during_a_step_neither_loses_it_nor_duplicates_events(self):
        card_id = self.card(description="ATRAPA: powoli 3")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.restart()
        self.assertTrue(flowfakes.alive(pid), "krok przeżył restart usługi")
        self.wait_column(card_id, "review", timeout=40)
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.closed"), timeout=5), self.events(card_id))
        self.flow.stop()  # migawka obejmuje również końcowe powiadomienia biegnącego ticka
        types = [e["type"] for e in self.events(card_id)]
        for kind in ("card.started", "step.started", "step.finished"):
            self.assertEqual(types.count(kind), 1 if kind == "card.started" else 3, types)
        self.assertEqual([s["kind"] for s in self.of(card_id, "step.started")],
                         ["ticket", "review", "close"])
        self.assertEqual(len(self.results(card_id, "merge")), 1, types)
        self.assertEqual([e["seq"] for e in self.events(card_id)], list(range(1, len(types) + 1)))
        self.restart()
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual([e["type"] for e in self.events(card_id)], types, "kolejny restart nic nie dopisuje")

    def test_merge_started_before_a_restart_is_finished_once(self):
        card_id = self.card()
        reply = self.start(card_id)
        self.wait_column(card_id, "review")
        tip = git(self.repo, "rev-parse", reply["cardBranch"]).strip()
        # Usługa padła po zamiarze scalenia, przed wynikiem: w dzienniku (i w kopii usługi) zamiar bez wyniku.
        journal = self.flow.engine.journal
        events = journal.read(card_id)
        cut = next(i for i, e in enumerate(events) if e["type"] == "result" and e["action"] == "merge")
        self.flow.stop()
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events[:cut])
        for path in (journal.mirror_path(card_id), journal.path(card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.results(card_id, "merge"), timeout=15), self.events(card_id))
        self.wait_column(card_id, "review")
        merged = self.results(card_id, "merge")
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["already"], "ticket był już w gałęzi karty — bez drugiego scalenia")
        self.assertEqual(git(self.repo, "rev-parse", reply["cardBranch"]).strip(), tip)


class ManualColumnsInProcessTest(FlowInProcess):
    def test_done_waits_for_a_step_that_failed_to_stop(self):
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.wait_column(card_id, "progress")
        with mock.patch.object(self.flow.engine, "_stop", return_value=False) as failed_stop:
            self.assertTrue(json.loads(self.board.move_card(card_id, "done", 0))["ok"])
            self.assertTrue(wait_until(lambda: failed_stop.called, timeout=5), self.events(card_id))
            self.assertEqual(self.of(card_id, "card.finished"), [])
            self.assertEqual(self.of(card_id, "ticket.rejected"), [])
            self.assertTrue(flowfakes.alive(pid))
        self.flow.tick()
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.finished"), timeout=15), self.events(card_id))
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5))

    def test_backlog_pauses_and_done_finishes_with_changes_kept(self):
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        working = self.wait_column(card_id, "progress")
        self.assertEqual((working["derived"]["flow"]["ticket"], working["derived"]["note"]),
                         ("PT-1", "PT-1 · krok w tle (Sonnet)"))
        # Okno Worktrees: worktree kroku i karty zajęte jak katalog agenta (sesja kroku nie jest wierszem agenta).
        engine = self.flow.engine
        self.assertTrue(wait_until(lambda: set(engine.busy_places()) == {step["worktree"], reply["cardWorktree"]},
                                   timeout=5), engine.busy_places())
        self.assertTrue(json.loads(self.board.move_card(card_id, "backlog", 0))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.paused"), timeout=10))
        card = self.wait_column(card_id, "backlog")
        self.assertIn("wstrzymana", card["derived"]["note"])
        self.assertTrue(flowfakes.alive(pid), "wstrzymanie nie zabija trwającego kroku")

        self.assertTrue(json.loads(self.board.move_card(card_id, "done", 0))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.finished"), timeout=20), self.events(card_id))
        self.wait_column(card_id, "done")
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5))
        finished = self.of(card_id, "step.finished")
        self.assertEqual([f["result"] for f in finished], ["stopped"])
        self.assertEqual([e["ticket"] for e in self.of(card_id, "ticket.rejected")], ["PT-1"])
        self.assert_changes_kept(finished[0], step, reply["branch"])
        self.assertTrue(wait_until(lambda: engine.busy_places() == [], timeout=5), "zakończona karta niczego nie trzyma")
        count = len(self.events(card_id))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.events(card_id)), count, "kolejne przeliczenia nic nie dopisują")

    def test_resume_after_backlog_pause_brings_the_card_back_to_work(self):
        # 06.11, K-55: karta wstrzymana przeniesieniem do Backlogu i wznowiona SetCardPaused pracowała dalej,
        # a Tablica pokazywała ją w Backlogu (zapisana kolumna została „backlog”).
        card_id = self.card(description="ATRAPA: czekaj")
        self.start(card_id)
        self.started(card_id)
        self.wait_column(card_id, "progress")
        self.assertTrue(json.loads(self.board.move_card(card_id, "backlog", 0))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.paused"), timeout=10))
        self.wait_column(card_id, "backlog")
        box = []
        self.flow.set_paused(card_id, False, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        card = self.wait_column(card_id, "progress")
        self.assertNotIn("wstrzymana", card["derived"]["note"])
        self.assertEqual(self.store.load().cards[int(card_id.split("-")[1])].column, "todo")
        self.assertEqual(len(self.of(card_id, "card.paused")), 1, "wznowienie nie wstrzymuje karty ponownie")

    def test_started_from_backlog_reaches_review_and_back_to_todo_is_rework(self):
        card_id = self.card(column="backlog")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(json.loads(self.board.move_card(card_id, "todo", -1))["ok"])   # jak okno po starcie z popupu
        self.wait_column(card_id, "review")
        self.assertTrue(json.loads(self.board.move_card(card_id, "todo", 0))["ok"])    # „do poprawy” z Do sprawdzenia
        card = self.wait_column(card_id, "todo")
        self.assertIsNone(card["derived"])
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "card.moved")) == 2, timeout=5))
        self.assertEqual([(m["from"], m["to"]) for m in self.of(card_id, "card.moved")],
                         [("backlog", "todo"), ("review", "todo")])

    def test_rework_after_title_change_uses_the_original_card_branch(self):
        card_id = self.card()
        first = self.start(card_id)
        self.wait_column(card_id, "review")
        # Review jest widoczne już po scaleniu ticketu. Ten scenariusz odsyła do poprawy
        # dopiero domkniętą kartę, więc nie może ścigać się z zapisem card.closed.
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.closed"), timeout=30))
        self.assertTrue(json.loads(self.board.move_card(card_id, "todo", 0))["ok"])
        self.wait_column(card_id, "todo")
        self.assertTrue(json.loads(self.board.update_card(card_id, json.dumps({"title": "Nowy tytuł"})))["ok"])
        second = self.start(card_id)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["ticket"], "PT-2")
        self.assertEqual(second["cardBranch"], first["cardBranch"])
        self.assertEqual(second["cardWorktree"], first["cardWorktree"])
        self.wait_column(card_id, "review")

    def test_question_moved_to_todo_can_be_started_again(self):
        # K-34: karta z pytaniem przeniesiona ręcznie z Czeka na mnie do Do zrobienia stoi bez stanu,
        # więc nowe zlecenie startuje; przed przeniesieniem odmowa mówi, na co karta czeka.
        card_id = self.card(description="ATRAPA: pytanie")
        first = self.start(card_id)
        self.wait_column(card_id, "waiting")
        refused = self.start(card_id)
        self.assertEqual((refused["ok"], refused["error"]), (False, "flow-active"), refused)
        self.assertIn("decyzję (pytanie)", refused["message"])
        self.assertTrue(json.loads(self.board.move_card(card_id, "todo", 0))["ok"])
        card = self.wait_column(card_id, "todo")
        self.assertIsNone(card["derived"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.moved"), timeout=5))
        self.assertTrue(json.loads(self.board.update_card(card_id, json.dumps({"description": "zwykła"})))["ok"])
        second = self.start(card_id)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["cardBranch"], first["cardBranch"])
        self.wait_column(card_id, "review")

    def test_moving_a_card_without_journal_writes_no_journal(self):
        card_id = self.card(description="zwykła", column="backlog")
        self.assertTrue(json.loads(self.board.move_card(card_id, "todo", 0))["ok"])
        self.assertTrue(json.loads(self.board.move_card(card_id, "done", 0))["ok"])
        wait_until(lambda: False, timeout=0.5)
        self.assertFalse(os.path.exists(os.path.join(self.root, "flow")))


class JournalInProcessTest(FlowInProcess):
    def test_journal_removed_from_outside_comes_back_with_a_warning(self):
        card_id = self.card()
        self.start(card_id)
        self.wait_column(card_id, "review")
        self.assertTrue(wait_until(lambda: git(self.root, "status", "--porcelain") == "", timeout=5))
        before = self.events(card_id)
        git(self.root, "rm", "-q", "-r", os.path.join("flow", card_id))
        git(self.root, "commit", "-q", "-m", "ręczne cofnięcie")
        self.assertTrue(wait_until(lambda: self.events(card_id) == before, timeout=15), "dziennik odtworzony")
        def warning():
            errors = json.loads(self.board.snapshot_json())["errors"]
            return [e for e in errors if "dziennik" in e["message"] and card_id in e["message"]]
        self.assertTrue(wait_until(warning, timeout=5), json.loads(self.board.snapshot_json())["errors"])
        self.assertEqual(self.shown(card_id)["boardColumn"], "review")
        self.assertTrue(wait_until(lambda: git(self.root, "status", "--porcelain") == "", timeout=5))
        self.assertIn("journal.jsonl", git(self.root, "show", "--name-only", "--format=", "HEAD"))


if __name__ == "__main__":
    unittest.main()
