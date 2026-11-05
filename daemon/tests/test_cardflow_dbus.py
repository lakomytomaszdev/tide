"""Przepływ karty, wejście „Od razu” (PT-58), przez D-Bus na prywatnej magistrali.

Usługa dostaje tymczasową tablicę, tymczasowe repozytorium projektu, własny HOME, XDG_* i atrapy `claude -p`,
`systemd-run` i `systemctl` (tests/flowfakes.py). Test zleca kartę przez /Board StartCard i patrzy tylko na to, co
widać z zewnątrz: zdarzenia w dzienniku karty, kolumnę i znacznik karty w Snapshot(), commity na gałęziach,
procesy kroku, listę agentów i kafelek Praca.

Katalog testu nie może leżeć pod /tmp/claude-<uid> (strażnik katalogów kroku z PT-76 słusznie go odrzuca) —
w piaskownicy Claude Code wskaż inny przez TIDE_TEST_TMPDIR.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from . import DAEMON_DIR, flowfakes
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until

try:
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    QDBusMessage = None

BOARD_PATH, BOARD_IFACE = "/Board", "org.tide.Daemon.Board"
AGENTS_PATH, AGENTS_IFACE = "/Agents", "org.tide.Daemon.Agents"
CLAUDE_MD = "# Projekt\n\n## Plan pracy agentów (Tide)\n\n- ticket-prefix: `PT`, tracker: local\n"


def test_root() -> str:
    """Katalog na pliki testu poza /tmp/claude-<uid>: TIDE_TEST_TMPDIR, katalog tymczasowy systemu, a w piaskownicy
    Claude Code (tam $TMPDIR to /tmp/claude-<uid>, a /tmp jest tylko do odczytu) — ignorowany przez gita build/
    w katalogu repozytorium Tide."""
    sandbox = f"/tmp/claude-{os.getuid()}"
    build = os.path.join(os.path.dirname(DAEMON_DIR), "build", "test-tmp")
    for base in (os.environ.get("TIDE_TEST_TMPDIR"), tempfile.gettempdir(), "/tmp", build):
        if not base or os.path.realpath(base).startswith(sandbox):
            continue
        try:
            os.makedirs(base, exist_ok=True)
            return tempfile.mkdtemp(prefix="tide-flow-", dir=base)
        except OSError:
            continue
    raise unittest.SkipTest(f"nie ma zapisywalnego katalogu tymczasowego poza {sandbox} — ustaw TIDE_TEST_TMPDIR")


def git(cwd, *args, env=None) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True, env=env).stdout


@requires_dbus
class FlowCase(unittest.TestCase):
    guard_minutes = 15.0

    def setUp(self):
        self.base = test_root()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        for name in ("ws", "home", "run", "tmp", "config/tide", "state"):
            os.makedirs(os.path.join(self.base, name), exist_ok=True)
        os.chmod(os.path.join(self.base, "run"), 0o700)
        self.runtime = os.path.join(self.base, "run")
        gitconfig = os.path.join(self.base, "gitconfig")
        with open(gitconfig, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        self.git_env = dict(os.environ, GIT_CONFIG_GLOBAL=gitconfig, GIT_CONFIG_NOSYSTEM="1")
        self.repo = os.path.join(self.base, "ws", "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", env=self.git_env)
        with open(os.path.join(self.repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(CLAUDE_MD)
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "init", env=self.git_env)
        self.master = git(self.repo, "rev-parse", "master").strip()
        with open(os.path.join(self.base, "config", "tide", "cardflow.json"), "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": self.guard_minutes, "autoConsent": False, "escalateErrors": False,
                       "reviewRoundsBeforeQuestion": 3}, fh)
        fakebin = os.path.join(self.base, "fakebin")
        flowfakes.install(fakebin)
        self.board = os.path.join(self.base, "ws", "tide-board")
        self.calls = os.path.join(self.base, "calls.log")
        self.env = {"HOME": os.path.join(self.base, "home"), "XDG_RUNTIME_DIR": self.runtime,
                    "XDG_CONFIG_HOME": os.path.join(self.base, "config"),
                    "TMPDIR": os.path.join(self.base, "tmp"), "PATH": fakebin + os.pathsep + "/usr/bin:/bin",
                    "GIT_CONFIG_GLOBAL": gitconfig, "GIT_CONFIG_NOSYSTEM": "1",
                    "TIDE_SYSTEMD_RUN": os.path.join(fakebin, "systemd-run"), "TIDE_FLOW_POLL_MS": "200",
                    "TIDE_FAKE_LOG": self.calls}
        self.service = PrivateBus("tide-flow-test")
        self.addCleanup(self.service.stop)
        self.daemon = self.spawn()
        self.call("UpsertProject", json.dumps({"repo": self.repo}))

    def spawn(self):
        return self.service.spawn_daemon(
            "--claude-dir", os.path.join(self.base, "home", ".claude"),
            "--codex-dir", os.path.join(self.base, "codex"), "--no-processes", "--no-notifications",
            "--workspace", os.path.join(self.base, "ws"), "--board-dir", self.board,
            extra_env={**self.env, "XDG_STATE_HOME": os.path.join(self.base, "state")})

    def restart(self):
        self.daemon.terminate()
        self.daemon.wait(10)
        wait_until(lambda: not self.service.bus.interface().isServiceRegistered(SERVICE).value(), timeout=5)
        self.daemon = self.spawn()

    # --- pomocnicze -------------------------------------------------------------------------------

    def call(self, member, *args, path=BOARD_PATH, iface=BOARD_IFACE):
        reply = self.service.call(SERVICE, path, iface, member, *args, timeout=60000)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return json.loads(reply.arguments()[0])

    def card(self, title="Eksport CSV", description="Dodaj eksport.\n\nATRAPA: gotowe", column="todo"):
        return self.call("CreateCard", json.dumps({"title": title, "project": "proj", "description": description,
                                                   "column": column}))["id"]

    def start(self, card_id, **extra):
        return self.call("StartCard", card_id, json.dumps({"entry": "now", "executor": "Sonnet", **extra}))

    def shown(self, card_id) -> dict:
        return next(c for c in self.call("Snapshot")["cards"] if c["id"] == card_id)

    def wait_column(self, card_id, column, timeout=30.0) -> dict:
        found = wait_until(lambda: self.shown(card_id)["boardColumn"] == column, timeout=timeout)
        card = self.shown(card_id)
        self.assertTrue(found, f"{card_id} w {card['boardColumn']}, a nie {column}: {card.get('derived')}\n"
                        f"{self.events(card_id)}")
        # Kolumna liczy się z kopii dziennika w usłudze; plik w tablicy dostaje to samo zdarzenie chwilę później.
        from tide_daemon.cardflow.journal import mirror_root
        mirror = os.path.join(mirror_root(self.board, {"XDG_STATE_HOME": os.path.join(self.base, "state")}),
                              card_id + ".jsonl")
        self.assertTrue(wait_until(lambda: self.events(card_id) == self.events(card_id, mirror), timeout=5))
        return card

    def events(self, card_id, path="") -> list:
        path = path or os.path.join(self.board, "flow", card_id, "journal.jsonl")
        try:
            with open(path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []

    def of(self, card_id, kind) -> list:
        return [e for e in self.events(card_id) if e["type"] == kind]

    def ticket_finished(self, card_id) -> list:
        """Wyniki kroków ticketu bez osobnych kroków opiekuna (PT-143)."""
        steps = {s["step"] for s in self.of(card_id, "step.started") if s.get("kind") != "caretaker"}
        return [e for e in self.of(card_id, "step.finished") if e["step"] in steps]

    def results(self, card_id, action) -> list:
        return [e for e in self.of(card_id, "result") if e.get("action") == action]

    def fake_call(self, started) -> dict:
        with open(os.path.join(started["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            return json.load(fh)


class CaretakerChatDbusTest(FlowCase):
    def spawn(self):
        service = "org.kde.konsole.tide_caretaker_test"
        fake = os.path.join(os.path.dirname(__file__), "fake_konsole.py")
        if not getattr(self, "fake_console", None):
            self.fake_console = self.service.spawn(["/usr/bin/python3", fake, service, "1"], service)
        self.env.update(TIDE_FAKE_KONSOLE_SERVICE=service, TIDE_FAKE_KONSOLE=fake)
        return super().spawn()

    def console_tasks(self):
        reply = self.service.call("org.kde.konsole.tide_caretaker_test", "/Windows/1",
                                  "org.kde.konsole.Window", "Tasks")
        return json.loads(reply.arguments()[0])

    def test_talk_opens_console_with_chat_context_and_only_one_session(self):
        card = self.card(description="Eksport.\n\nATRAPA: blad-opiekun\nATRAPA-POWOD: test pada ATRAPA-OPIEKUN: escalate")
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "caretaker.escalated"), timeout=30))
        problem = self.of(card, "caretaker.started")[0]["problem"]
        reply = self.call("TalkToCaretaker", card, json.dumps({"problem": problem}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: self.console_tasks(), timeout=5))
        [task] = self.console_tasks()
        self.assertIn("rozmowa z opiekunem", task["title"])
        started = next(s for s in self.of(card, "step.started") if s["step"] == reply["step"])
        self.assertEqual(started["kind"], "caretaker-chat")
        self.assertTrue(os.path.isdir(os.path.join(started["stepDir"], "in", "context")))
        self.assertEqual(task["workdir"], os.path.join(started["stepDir"], "out"))
        self.assertIn("Tryb rozmowy", task["argv"][-1])
        self.assertEqual(self.call("TalkToCaretaker", card, "{}")["error"], "console-open")
        self.assertEqual(self.call("TalkToCaretaker", card, "[]")["error"], "bad-request")
        path = os.path.join(started["stepDir"], "out", "action-1.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "action": "retry", "step": self.of(card, "caretaker.started")[0]["target"],
                       "instruction": "Sprawdź format.", "reason": "Format daty."}, fh)
        def actions():
            return [e for e in self.of(card, "caretaker.action") if e.get("mode") == "chat"]
        self.assertTrue(wait_until(actions, timeout=5))
        self.assertTrue(actions()[0]["ok"], actions())
        self.restart()
        self.call("GetCardFlowSettings")
        self.assertEqual(len(actions()), 1)


class PlanDbusTest(FlowCase):
    def test_full_planning_finds_claude_in_local_bin_without_service_path(self):
        local_bin = os.path.join(self.env["HOME"], ".local", "bin")
        os.makedirs(local_bin)
        shutil.move(os.path.join(self.base, "fakebin", "claude"), os.path.join(local_bin, "claude"))
        self.restart()

        card = self.card(column="backlog")
        reply = self.call("StartCard", card, json.dumps({"entry": "planning", "mode": "full", "executor": "Opus"}))
        self.assertTrue(reply["ok"], reply)

    def test_plan_is_proposed_and_approved_through_private_board_bus(self):
        card = self.card(description="Plan z pliku")
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-10-29-card.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Pierwszy | todo | | Sonnet | | standard | Opus | |\n")
        with open(os.path.join(folder, "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Ticket\n\nATRAPA: gotowe\n\n## Kryteria akceptacji\n\n- [ ] Działa.\n")
        proposed = self.call("StartCard", card, json.dumps({"entry": "plan", "planPath": path}))
        self.assertTrue(proposed["ok"], proposed)
        self.assertEqual(self.results(card, "reserve"), [])
        self.assertEqual(self.wait_column(card, "waiting")["derived"]["flow"]["plan"]["waves"], [["#1"]])
        edited = self.call("EditCardPlan", card, json.dumps({"action": "edit", "ticket": "#1", "executor": "Opus", "reviewer": "Sonnet"}))
        self.assertTrue(edited["ok"], edited)
        self.assertEqual(self.of(card, "plan.ticket-edited")[-1]["changes"]["executor"], "Opus")
        approved = self.call("ApproveCardPlan", card)
        self.assertTrue(approved["ok"], approved)
        self.assertIn("#1", approved["map"])
        self.assertEqual(self.call("ApproveCardPlan", card)["map"], approved["map"])
        self.wait_column(card, "review", timeout=40)
        self.assertEqual([r["ticket"] for r in self.results(card, "reserve")], [approved["map"]["#1"]])


class NowTest(FlowCase):
    def test_step_question_is_answered_through_board_dbus(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        shown = self.wait_column(card_id, "waiting")
        self.assertEqual(shown["derived"]["flow"]["decisions"][0]["kind"], "question")
        answer = self.call("ResolveCardStep", card_id, json.dumps({"choice": "answer", "answer": "ISO 8601"}))
        self.assertEqual((answer["ok"], answer["choice"]), (True, "answer"))
        self.wait_column(card_id, "review")
        self.assertEqual([e["choice"] for e in self.of(card_id, "step.decision")], ["answer"])

    def test_review_question_is_answered_through_board_dbus(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 3")
        self.assertTrue(self.start(card_id)["ok"])
        shown = self.wait_column(card_id, "waiting", timeout=50)
        self.assertTrue(shown["derived"]["flow"]["reviewQuestion"])
        answer = self.call("ResolveCardReview", card_id, "reject")
        self.assertEqual((answer["ok"], answer["choice"]), (True, "reject"))
        self.assertTrue(wait_until(lambda: self.of(card_id, "ticket.rejected"), timeout=5))
        self.assertEqual(self.call("ResolveCardReview", card_id, "retry")["error"], "no-review-question")
        self.assertEqual(self.results(card_id, "merge"), [])

    def test_now_runs_one_background_ticket_from_launch_to_review(self):
        legacy = self.card("Dawna karta", "Bez dziennika.")
        card_id = self.card()
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["ticket"], "PT-1")
        self.assertTrue(reply["cardBranch"].startswith("card/k-2-"), reply)
        self.assertTrue(reply["branch"].startswith("ticket/pt-1-"), reply)

        card = self.wait_column(card_id, "review")
        self.assertIn("PT-1", card["derived"]["note"])
        self.assertTrue(card["derived"]["flow"])
        types = [e["type"] for e in self.events(card_id)]
        self.assertEqual(types[0], "card.started")
        for kind in ("step.started", "step.finished"):
            self.assertEqual(types.count(kind), 3, types)
        self.assertEqual([s["kind"] for s in self.of(card_id, "step.started")],
                         ["ticket", "review", "close"])
        self.assertEqual(types.count("review.verdict"), 1, types)
        self.assertTrue(all(e["v"] == 1 and e["card"] == card_id for e in self.events(card_id)))
        self.assertEqual(self.of(card_id, "card.started")[0]["entry"], "now")
        self.assertEqual(self.of(card_id, "card.started")[0]["executor"], "Sonnet")
        finished = self.of(card_id, "step.finished")[0]
        self.assertEqual((finished["result"], finished["ticket"]), ("done", "PT-1"))
        # Każda akcja poza dziennikiem to para „zamiar → wynik” z tym samym kluczem.
        intents = {(e["action"], e["key"]) for e in self.of(card_id, "intent")}
        done = {(e["action"], e["key"]) for e in self.of(card_id, "result")}
        self.assertEqual(intents, done)
        self.assertTrue({"reserve", "card-branch", "ticket-branch", "step-start", "merge"}
                        <= {a for a, _k in intents}, intents)

        # Gałąź karty ma pracę kroku, gałąź główna i drugi worktree bez zmian.
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        files = git(self.repo, "ls-tree", "-r", "--name-only", reply["cardBranch"]).split()
        self.assertIn("ATRAPA.md", files)
        self.assertIn("feat: atrapa robi ticket", git(self.repo, "log", "--format=%s", reply["cardBranch"]))
        card_wt = self.of(card_id, "result")
        card_wt = next(e["worktree"] for e in card_wt if e["action"] == "card-branch")
        self.assertTrue(os.path.isfile(os.path.join(card_wt, "ATRAPA.md")), "worktree karty po scaleniu")

        # Krok dostał kartkę z karty, w tle (claude -p), z piaskownicą i bez Konsoli.
        started = self.of(card_id, "step.started")[0]
        call = self.fake_call(started)
        self.assertIn("-p", call["argv"])
        self.assertIn("--session-id", call["argv"])
        self.assertIn("--model=sonnet", call["argv"])
        self.assertIn("Eksport CSV", call["prompt"])
        self.assertIn("Dodaj eksport.", call["prompt"])
        self.assertEqual(call["cwd"], started["worktree"])
        self.assertNotIn("SSH_AUTH_SOCK", call["env"])
        self.assertFalse(os.path.exists(self.calls), "żadna karta Konsoli")
        # Wyjście kroku przejęte do katalogu karty w repozytorium tablicy, tablica bez niezacommitowanych zmian.
        kept = os.path.join(self.board, "flow", card_id, "steps", started["step"])
        self.assertTrue(os.path.isfile(os.path.join(kept, "result.json")))
        self.assertTrue(os.path.isfile(os.path.join(kept, "note.md")))
        self.assertTrue(wait_until(lambda: git(self.board, "status", "--porcelain") == "", timeout=5),
                        git(self.board, "status", "--porcelain"))
        self.assertNotIn("sync external changes", git(self.board, "log", "--format=%s"),
                         "dziennik commituje usługa, nie synchronizacja zmian z zewnątrz")

        # Karta bez dziennika wygląda jak dotąd.
        old = self.shown(legacy)
        self.assertEqual(old["boardColumn"], "todo")
        self.assertFalse((old.get("derived") or {}).get("flow"))

        # Sesja kroku nie jest wierszem agenta ani pozycją kafelka Praca.
        sessions = self.call("Sessions", path=AGENTS_PATH, iface=AGENTS_IFACE)
        self.assertFalse([s for s in sessions if s.get("sessionId") == started["session"]], sessions)
        work = self.call("Work")
        self.assertFalse([i for i in work["items"] if started["session"] in json.dumps(i)], work)
        self.assertTrue([i for i in work["items"] if i.get("id") == card_id or i.get("card") == card_id], work)

    def test_second_start_is_refused_while_the_card_works(self):
        card_id = self.card(description="ATRAPA: czekaj")
        self.assertTrue(self.start(card_id)["ok"])
        again = self.start(card_id)
        self.assertEqual((again["ok"], again["error"]), (False, "flow-active"), again)
        self.assertEqual(len(self.of(card_id, "card.started")), 1)

    def test_unknown_or_unconfigured_local_executor_is_refused(self):
        card_id = self.card()
        for name, code in (("Nieznany", "unknown-executor"), ("Lokalny", "local-unavailable")):
            reply = self.start(card_id, executor=name)
            self.assertEqual((reply["ok"], reply["error"]), (False, code), reply)
        self.assertEqual(self.events(card_id), [])
        self.assertEqual(git(self.repo, "branch", "--format=%(refname:short)").split(), ["master"])

    def test_codex_executor_works_through_start_card(self):
        card_id = self.card()
        reply = self.start(card_id, executor="Codex standard", preferredProvider="openai")
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "review")
        step = self.of(card_id, "step.started")[0]
        self.assertEqual((step["executor"], step["program"], step["model"]),
                         ("Codex standard", "codex", "gpt-6.1-sol"))
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn('model_reasoning_effort="medium"', call["argv"])

    def test_missing_result_file_is_a_step_error(self):
        card_id = self.card(description="ATRAPA: bez-wyniku")
        reply = self.start(card_id)
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=30), self.events(card_id))
        card = self.wait_column(card_id, "waiting")
        finished = self.ticket_finished(card_id)
        self.assertEqual(len(finished), 2)
        self.assertEqual((finished[0]["result"], finished[0]["reason"]), ("error", "no-result"))
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertIn("błąd", card["derived"]["note"])
        self.assertEqual(self.results(card_id, "merge"), [], "praca bez pliku wyniku nie idzie na gałąź karty")
        self.assertNotIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["cardBranch"]).split())

    def test_question_waits_for_the_user(self):
        card_id = self.card(description="ATRAPA: pytanie")
        self.start(card_id)
        card = self.wait_column(card_id, "waiting")
        self.assertIn("Który format daty?", card["derived"]["note"])
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "question")


class GuardTest(FlowCase):
    guard_minutes = 0.05   # 3 s

    def test_guard_stops_a_step_without_progress_and_keeps_its_changes(self):
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        started = wait_until(lambda: self.of(card_id, "step.started"), timeout=10)[0]
        pid = wait_until(lambda: flowfakes.unit_pid(self.runtime, started["unit"]), timeout=5)
        self.assertTrue(flowfakes.alive(pid))
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=30), self.events(card_id))
        card = self.wait_column(card_id, "waiting", timeout=30)
        finished = self.ticket_finished(card_id)
        self.assertEqual(len(finished), 2)
        self.assertEqual((finished[0]["result"], finished[0]["reason"]), ("error", "no-progress"))
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertIn("bez postępu", card["derived"]["note"])
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5), "krok zatrzymany")
        # Bez utraty zmian: commit WIP (piaskownica gita usługi działa) albo plik dalej w worktree (nie działa).
        wip = finished[0].get("wip") or {}
        if wip.get("committed"):
            self.assertIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["branch"]).split())
        else:
            self.assertEqual(wip.get("error"), "no-sandbox", wip)
            self.assertTrue(os.path.isfile(os.path.join(started["worktree"], "ATRAPA.md")))


class RestartTest(FlowCase):
    def test_service_restart_during_a_step_neither_loses_it_nor_duplicates_events(self):
        card_id = self.card(description="ATRAPA: powoli 3")
        self.start(card_id)
        started = wait_until(lambda: self.of(card_id, "step.started"), timeout=10)[0]
        pid = wait_until(lambda: flowfakes.unit_pid(self.runtime, started["unit"]), timeout=5)
        self.restart()
        self.assertTrue(flowfakes.alive(pid), "krok przeżył restart usługi")
        self.wait_column(card_id, "review", timeout=40)
        types = [e["type"] for e in self.events(card_id)]
        for kind in ("card.started", "step.started", "step.finished"):
            self.assertEqual(types.count(kind), 1 if kind == "card.started" else 3, types)
        self.assertEqual([s["kind"] for s in self.of(card_id, "step.started")],
                         ["ticket", "review", "close"])
        self.assertEqual(len(self.results(card_id, "merge")), 1, types)
        seqs = [e["seq"] for e in self.events(card_id)]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))


class ManualColumnsTest(FlowCase):
    def test_backlog_pauses_and_done_finishes_with_changes_kept(self):
        card_id = self.card(description="ATRAPA: czekaj")
        reply = self.start(card_id)
        started = wait_until(lambda: self.of(card_id, "step.started"), timeout=10)[0]
        pid = wait_until(lambda: flowfakes.unit_pid(self.runtime, started["unit"]), timeout=5)
        self.assertEqual(self.wait_column(card_id, "progress")["derived"]["flow"]["ticket"], "PT-1")
        # Okno Worktrees: worktree trwającego kroku zajęty jak katalog agenta, choć sesja kroku nie jest wierszem agenta.
        busy = lambda: [w for w in self.call("List", path="/Worktrees", iface="org.tide.Daemon.Worktrees")
                        if os.path.realpath(w.get("path", "")) == os.path.realpath(started["worktree"])
                        and w.get("hasAgent")]
        self.service.call(SERVICE, "/Worktrees", "org.tide.Daemon.Worktrees", "Refresh")
        self.assertTrue(wait_until(busy, timeout=15), "worktree kroku zajęty w oknie Worktrees")
        self.assertTrue(self.call("MoveCard", card_id, "backlog", 0)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.paused"), timeout=5), self.events(card_id))
        card = self.wait_column(card_id, "backlog")
        self.assertIn("wstrzymana", card["derived"]["note"])
        self.assertEqual(len(self.of(card_id, "card.paused")), 1)
        self.assertTrue(flowfakes.alive(pid), "wstrzymanie nie zabija trwającego kroku")

        self.assertTrue(self.call("MoveCard", card_id, "done", 0)["ok"])
        self.wait_column(card_id, "done")
        self.assertTrue(wait_until(lambda: self.of(card_id, "card.finished"), timeout=20), self.events(card_id))
        self.assertTrue(wait_until(lambda: not flowfakes.alive(pid), timeout=5))
        finished = self.of(card_id, "step.finished")
        self.assertEqual([f["result"] for f in finished], ["stopped"])
        self.assertEqual([e["ticket"] for e in self.of(card_id, "ticket.rejected")], ["PT-1"])
        wip = finished[0].get("wip") or {}
        if wip.get("committed"):
            self.assertIn("ATRAPA.md", git(self.repo, "ls-tree", "-r", "--name-only", reply["branch"]).split())
        else:
            self.assertEqual(wip.get("error"), "no-sandbox", wip)
            self.assertTrue(os.path.isfile(os.path.join(started["worktree"], "ATRAPA.md")))
        # Ponowne przeliczenia nie dopisują kolejnych zdarzeń.
        count = len(self.events(card_id))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.events(card_id)), count)


class JournalTest(FlowCase):
    def test_journal_removed_from_outside_comes_back_with_a_warning(self):
        card_id = self.card()
        self.start(card_id)
        self.wait_column(card_id, "review")
        self.assertTrue(wait_until(lambda: git(self.board, "status", "--porcelain") == "", timeout=5))
        before = self.events(card_id)
        git(self.board, "rm", "-q", "-r", os.path.join("flow", card_id), env=self.git_env)
        git(self.board, "commit", "-q", "-m", "ręczne cofnięcie", env=self.git_env)
        self.assertTrue(wait_until(lambda: self.events(card_id) == before, timeout=15), "dziennik odtworzony")

        def warning():
            errors = self.call("Snapshot")["errors"]
            return [e for e in errors if "dziennik" in e["message"] and card_id in e["message"]]

        # Odtworzenie pliku w wątku przepływu wyprzedza publikację migawki w głównym wątku usługi.
        self.assertTrue(wait_until(warning, timeout=5), self.call("Snapshot")["errors"])
        self.assertEqual(self.shown(card_id)["boardColumn"], "review")


if __name__ == "__main__":
    unittest.main()


class PauseDbusTest(FlowCase):
    def test_pause_and_resume_are_available_on_private_board_bus(self):
        card = self.card(description="ATRAPA: powoli 2")
        self.assertTrue(self.call("StartCard", card, json.dumps({"entry": "now", "executor": "Sonnet"}))["ok"])
        self.assertTrue(self.call("SetCardPaused", card, True)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "step.finished"), timeout=10))
        wait_until(lambda: False, timeout=0.4)
        self.assertEqual(len(self.of(card, "step.started")), 1)
        shown = self.call("Snapshot")
        row = next(c for c in shown["cards"] if c["id"] == card)
        self.assertTrue(row["derived"]["flow"]["paused"])
        self.assertEqual(row["derived"]["note"], "wstrzymana")
        self.assertTrue(self.call("SetCardPaused", card, False)["ok"])
        self.wait_column(card, "review", timeout=15)
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")],
                         ["ticket", "review", "close"])
