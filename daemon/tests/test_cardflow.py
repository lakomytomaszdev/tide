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
import unittest
from unittest import mock

from . import flowfakes
from .privatebus import app, wait_until
from .test_cardflow_dbus import CLAUDE_MD, git, test_root


class FlowInProcess(unittest.TestCase):
    guard_minutes = 15.0

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
            json.dump({"stepGuardMinutes": self.guard_minutes}, fh)
        patcher = mock.patch.dict(os.environ, {
            "HOME": os.path.join(self.base, "home"), "XDG_RUNTIME_DIR": self.runtime,
            "XDG_STATE_HOME": os.path.join(self.base, "state"), "XDG_CONFIG_HOME": os.path.join(self.base, "config"),
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
        self.addCleanup(self.board.stop)
        self.flow = self.make_flow()
        self.board.start()
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps({"repo": self.repo})))["ok"])

    def make_flow(self):
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        from tide_daemon.models import executors
        engine = FlowEngine(self.store, claude_projects=os.path.join(self.base, "home", ".claude", "projects"),
                            systemd_run=self.fakes["systemd-run"], catalog=executors.default_catalog,
                            tmp_root=os.path.join(self.base, "tmp"), settings=self.settings,
                            which=lambda name: self.fakes.get(name), grace=5.0)
        flow = CardFlowMonitor(engine, self.board, poll_ms=150)
        self.board.flow = flow
        flow.start()
        self.addCleanup(flow.stop)
        return flow

    def restart(self):
        """Nowy silnik i monitor na tej samej tablicy (bez procesów kroków w pamięci) — jak usługa po restarcie."""
        self.flow.stop()
        wait_until(lambda: False, timeout=0.3)
        self.flow = self.make_flow()

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

        card = self.wait_column(card_id, "review")
        self.assertIn("PT-1", card["derived"]["note"])
        types = [e["type"] for e in self.events(card_id)]
        self.assertEqual(types[0], "card.started")
        for kind in ("step.started", "step.finished"):
            self.assertEqual(types.count(kind), 1, types)
        self.assertTrue(all(e["v"] == 1 and e["card"] == card_id for e in self.events(card_id)))
        self.assertEqual([e["seq"] for e in self.events(card_id)], list(range(1, len(types) + 1)))
        launched = self.of(card_id, "card.started")[0]
        self.assertEqual((launched["entry"], launched["executor"]), ("now", "Sonnet"))
        finished = self.of(card_id, "step.finished")[0]
        self.assertEqual((finished["result"], finished["ticket"]), ("done", "PT-1"))
        intents = {(e["action"], e["key"]) for e in self.of(card_id, "intent")}
        self.assertEqual(intents, {(e["action"], e["key"]) for e in self.of(card_id, "result")})
        self.assertEqual({a for a, _k in intents}, {"reserve", "card-branch", "ticket-branch", "step-start", "merge"})
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

        # Lista agentów: dziennik sesji kroku jest na dysku, skaner go widzi, ale usługa go nie pokazuje.
        monitor = AgentMonitor(AgentScanner(os.path.join(self.base, "home", ".claude"), [os.path.join(self.base, "x")]),
                               use_processes=False)
        monitor.rescan()
        self.assertTrue([s for s in monitor.sessions if s.get("sessionId") == step["session"]],
                        "bez filtra skaner widzi sesję kroku (inaczej test niczego nie sprawdza)")
        monitor.hidden = self.flow.engine.is_hidden
        monitor.recompute()
        self.assertFalse([s for s in monitor.sessions if s.get("sessionId") == step["session"]], monitor.sessions)
        work = json.loads(self.board.work_json())
        self.assertEqual([i["kind"] for i in work["items"] if step["session"] in json.dumps(i)], [])

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

    def test_only_claude_executors_from_the_catalog_and_nothing_left_behind(self):
        card_id = self.card()
        for name, code in (("Nieznany", "unknown-executor"), ("Codex standard", "unsupported-executor"),
                           ("Lokalny", "unsupported-executor")):
            reply = self.start(card_id, executor=name)
            self.assertEqual((reply["ok"], reply["error"]), (False, code), reply)
        reply = self.start(card_id, entry="planning")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-request"), reply)
        self.assertEqual(self.events(card_id), [])
        self.assertEqual(git(self.repo, "branch", "--format=%(refname:short)").split(), ["master"])
        self.assertFalse(os.path.exists(self.repo + "-worktrees"))

    def test_project_without_ticket_prefix_is_refused_before_anything(self):
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# Projekt bez planu\n")
        card_id = self.card()
        reply = self.start(card_id)
        self.assertEqual((reply["ok"], reply["error"]), (False, "no-ticket-number"), reply)
        self.assertEqual(self.events(card_id), [])
        self.assertEqual(self.shown(card_id)["boardColumn"], "todo")

    def test_missing_result_file_is_a_step_error(self):
        card_id = self.card(description="ATRAPA: bez-wyniku")
        reply = self.start(card_id)
        card = self.wait_column(card_id, "waiting")
        finished = self.of(card_id, "step.finished")
        self.assertEqual([(f["result"], f["reason"]) for f in finished], [("error", "no-result")])
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

    def test_guard_stops_a_step_without_progress_and_keeps_its_changes(self):
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.assertTrue(flowfakes.alive(pid))
        card = self.wait_column(card_id, "waiting", timeout=30)
        finished = self.of(card_id, "step.finished")
        self.assertEqual([(f["result"], f["reason"]) for f in finished], [("error", "no-progress")])
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
    def test_restart_during_a_step_neither_loses_it_nor_duplicates_events(self):
        card_id = self.card(description="ATRAPA: powoli 3")
        self.start(card_id)
        step = self.started(card_id)
        pid = self.unit_pid(step)
        self.restart()
        self.assertTrue(flowfakes.alive(pid), "krok przeżył restart usługi")
        self.wait_column(card_id, "review", timeout=40)
        types = [e["type"] for e in self.events(card_id)]
        for kind in ("card.started", "step.started", "step.finished"):
            self.assertEqual(types.count(kind), 1, types)
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
        errors = json.loads(self.board.snapshot_json())["errors"]
        self.assertTrue([e for e in errors if "dziennik" in e["message"] and card_id in e["message"]], errors)
        self.assertEqual(self.shown(card_id)["boardColumn"], "review")
        self.assertTrue(wait_until(lambda: git(self.root, "status", "--porcelain") == "", timeout=5))
        self.assertIn("journal.jsonl", git(self.root, "show", "--name-only", "--format=", "HEAD"))


if __name__ == "__main__":
    unittest.main()
