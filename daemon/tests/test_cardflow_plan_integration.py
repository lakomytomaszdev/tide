"""PT-78: propozycja planu, zatwierdzenie i wykonanie według blokerów."""
import json
import os
import shutil
from unittest import mock

from tide_daemon.cardflow import boxwait
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class PlanFlowTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        # 09.11: kopia planu na gałęzi karty dotyczy projektów z planami w repozytorium (`ai-docs: repo`);
        # projekt z `ai-docs: tide` trzyma plany na Tablicy (test_plan_of_a_project_with_board_docs_…).
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps(
            {"slug": "proj", "repo": self.repo, "aiDocs": "repo"})))["ok"])

    def proposal(self, card_id, invalid=False, count=2):
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        shutil.rmtree(os.path.join(folder, "tickets"), ignore_errors=True)  # kartki poprzedniej propozycji
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-10-29-plan.md")
        extra = "".join(f"| #{n} | Plasterek {n} | todo | | Sonnet | | standard | Opus | |\n" for n in range(3, count + 1))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card_id +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\nsession: planning-123\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Pierwszy plasterek | todo | | Sonnet | konieczny | standard | Opus | |\n" +
                     ("| #2 | Drugi plasterek | todo | | Sonnet | po pierwszym | standard | Opus | #1 |\n"
                      if count >= 2 else "") + extra)
        for number in range(1, count + 1):
            with open(os.path.join(folder, "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: gotowe\n\n## Kryteria akceptacji\n\n- [ ] Wynik {number}.\n"
                         if not invalid or number == 1 else "# Brak kryteriów\n")
        return path

    def submit(self, card_id, path):
        box = []
        self.flow.start_card(card_id, json.dumps({"entry": "plan", "planPath": path}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        reply = json.loads(box[0])
        if reply.get("ok"):
            self.assertTrue(wait_until(lambda: self.of(card_id, "plan.reviewed"), timeout=20), self.events(card_id))
        return reply

    def approve(self, card_id, *, environment=True):
        """Testy wykonania zatwierdzają plan i środowisko; sam plan wymaga `environment=False`."""
        box = []
        self.flow.approve_plan(card_id, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        reply = json.loads(box[0])
        if environment and reply.get("ok") and not reply.get("queued"):
            state = fold(self.flow.engine.journal.read(card_id))
            if boxwait.card_box_wait(state) == boxwait.ENVIRONMENT:
                self.approve_environment(card_id)
        return reply

    def test_plain_plan_approval_leaves_the_card_waiting_for_the_environment(self):
        # PT-172: zatwierdzenie z okna decyzji (ApproveCardPlan) nie omija pakietu zgód.
        from tide_daemon.box.settings import BoxSettings
        from tide_daemon.cardflow import boxstep
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        # PT-290 (D1): oczekiwanie powstaje tylko przy włączonych pudełkach (wyłączone: test_cardflow_env_consent_switch).
        with mock.patch.object(boxstep, "settings_of", return_value=BoxSettings(enabled=True)):
            self.assertTrue(self.approve(card, environment=False)["ok"])
        st = fold(self.flow.engine.journal.read(card))
        self.assertEqual(boxwait.card_box_wait(st), boxwait.ENVIRONMENT)
        # Bariera zgody obowiązuje przy włączonych pudełkach; przy `box.enabled: false` wyłącznik (PT-289)
        # wybiera hosta przed nią (test_cardflow_box_switch). Projekt bez profilu nie dochodzi tu do Incusa.
        with mock.patch.object(boxstep, "settings_of", return_value=BoxSettings(enabled=True)):
            self.restart()
            self.assertTrue(wait_until(lambda: self.of(card, "step.waiting"), timeout=5), self.events(card))
            self.assertFalse(any(s.get("kind") == "ticket" for s in self.of(card, "step.started")))
            self.assertEqual(boxwait.card_box_wait(fold(self.flow.engine.journal.read(card))), boxwait.ENVIRONMENT)
            self.assertEqual(self.of(card, "step.waiting")[-1]["reason"], boxstep.WAIT_ENVIRONMENT)
            self.stop_flow_worker()
        self.flow.start()
        self.approve_environment(card)
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "ticket" for s in self.of(card, "step.started")),
                                   timeout=10), self.events(card))

    def test_partial_reservation_results_resume_only_missing_rows(self):
        # K-102 #1: dziennik z wynikiem dla części wierszy (np. sprzed rezerwacji zbiorczej) — zatwierdzenie
        # dokańcza tylko brakujące, a istniejące ID zostają.
        from tide_daemon.coordination import reservations
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card, count=3))["ok"])
        self.stop_flow()
        engine = self.flow.engine
        seq = fold(engine.journal.read(card)).plan_proposed["seq"]
        key = f"plan:{seq}:#2"
        first = engine._act(card, "reserve", key,
                            lambda: engine._reserve(self.repo, "Drugi plasterek", f"{card}:{key}", "PT", 0),
                            retry_failed=True, repo=self.repo, title="Drugi plasterek", prefix="PT", minimumNumber=0)
        original = reservations.reserve_many
        asked = []
        def spy(repo, requests, **kwargs):
            asked.append([r["request_key"] for r in requests])
            return original(repo, requests, **kwargs)
        with mock.patch.object(reservations, "reserve_many", side_effect=spy):
            reply = engine.approve_plan(card)
        self.assertEqual(asked, [[f"{card}:plan:{seq}:#1", f"{card}:plan:{seq}:#3"]])
        self.assertEqual(reply["map"]["#2"], first["ticket"])
        number = int(first["ticket"].split("-")[1])
        self.assertEqual([reply["map"]["#1"], reply["map"]["#3"]], [f"PT-{number + 1}", f"PT-{number + 2}"])
        self.assertEqual(len(reservations.load(self.repo)), 3)
        self.assertEqual(len(self.results(card, "reserve")), 3)

    def test_batch_failure_marks_covered_rows_and_retry_takes_the_same_path(self):
        from tide_daemon.cardflow.engine import FlowError
        from tide_daemon.coordination import reservations
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.stop_flow()
        original = reservations.reserve_many
        calls = []
        def fail_first(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise reservations.ReservationError("przejściowy błąd zapisu")
            return original(*args, **kwargs)
        with mock.patch.object(reservations, "reserve_many", side_effect=fail_first):
            with self.assertRaises(FlowError):
                self.flow.engine.approve_plan(card)
            failed = self.results(card, "reserve")
            self.assertEqual([(r["key"].rsplit(":", 1)[1], r["ok"]) for r in failed], [("#1", False), ("#2", False)])
            self.assertEqual(reservations.load(self.repo), [])
            self.assertEqual(self.of(card, "plan.approved"), [])
            reply = self.flow.engine.approve_plan(card)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(reservations.load(self.repo)), 2)
        self.assertEqual(sorted(reply["map"]), ["#1", "#2"])

    def test_approval_reads_repository_plans_once_for_1_and_18_tickets(self):
        from tide_daemon.coordination import reservations
        from tide_daemon.coordination.repoplans import RepoPlans
        reads = {}
        for count in (1, 18):
            card = self.card()
            self.assertTrue(self.submit(card, self.proposal(card, count=count))["ok"])
            self.stop_flow()
            original = RepoPlans.read
            calls = []
            def counting(reader, roots):
                calls.append(roots)
                return original(reader, roots)
            with mock.patch.object(RepoPlans, "read", autospec=True, side_effect=counting):
                reply = self.flow.engine.approve_plan(card)
            reads[count] = len(calls)
            self.assertEqual(len(reply["map"]), count)
            numbers = [int(reply["map"][f"#{n}"].split("-")[1]) for n in range(1, count + 1)]
            self.assertEqual(numbers, list(range(numbers[0], numbers[0] + count)), "#1 → najmniejszy z nowych")
            self.assertEqual(len(self.results(card, "reserve")), count)
            self.assertEqual(len([i for i in self.of(card, "intent") if i["action"] == "reserve"]), count)
            self.flow = self.make_flow()
        self.assertLessEqual(reads[18], 1, reads)
        self.assertEqual(reads[1], reads[18])
        self.assertEqual(len(reservations.load(self.repo)), 19)

    def test_restart_after_registry_write_before_results_keeps_ids(self):
        from tide_daemon.coordination import reservations
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card, count=18))["ok"])
        self.stop_flow()
        journal = self.flow.engine.journal
        original = journal.append

        def stop_before_results(ident, kind, **data):
            if kind == "result" and data.get("action") == "reserve":
                raise OSError("przerwanie po zapisie rejestru")
            return original(ident, kind, **data)

        with mock.patch.object(journal, "append", side_effect=stop_before_results):
            with self.assertRaises(OSError):
                self.flow.engine.approve_plan(card)
        self.assertEqual(self.results(card, "reserve"), [])
        self.assertEqual(len([i for i in self.of(card, "intent") if i["action"] == "reserve"]), 18)
        registry = {r["request_key"]: f"PT-{r['number']}" for r in reservations.load(self.repo)}
        self.assertEqual(len(registry), 18)
        self.restart()
        reply = self.approve(card, environment=False)
        seq = fold(self.flow.engine.journal.read(card)).plan_proposed["seq"]
        self.assertEqual(reply["map"], {f"#{n}": registry[f"{card}:plan:{seq}:#{n}"] for n in range(1, 19)})
        self.assertEqual(len(reservations.load(self.repo)), 18)
        self.assertEqual(len([r for r in self.results(card, "reserve") if r["ok"]]), 18)

    def test_proposal_rejects_different_repository_prefix_without_reservation(self):
        card = self.card()
        path = self.proposal(card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source.replace("ticket-prefix: PT", "ticket-prefix: OTHER"))
        reply = self.submit(card, path)
        self.assertFalse(reply["ok"])
        self.assertTrue(any("prefiks" in error for error in reply["errors"]))
        self.assertEqual(self.of(card, "plan.proposed"), [])
        self.assertEqual(self.results(card, "reserve"), [])

    def test_invalid_proposal_records_errors_without_reserving(self):
        card = self.card()
        reply = self.submit(card, self.proposal(card, invalid=True))
        self.assertEqual((reply["ok"], reply["error"]), (False, "invalid-plan"))
        self.assertTrue(any("kryteriów" in e for e in reply["errors"]))
        self.assertEqual(self.results(card, "reserve"), [])
        self.assertEqual(self.of(card, "plan.checked")[-1]["valid"], False)
        self.assertEqual(self.shown(card)["boardColumn"], "waiting")

    def test_proposal_rejects_preassigned_ticket_id(self):
        card = self.card()
        path = self.proposal(card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source.replace("| #1 |", "| PT-1 |"))
        reply = self.submit(card, path)
        self.assertFalse(reply["ok"])
        self.assertTrue(any("numeru roboczego" in error for error in reply["errors"]))
        self.assertEqual(self.results(card, "reserve"), [])

    def test_approval_assigns_ids_then_runs_blocked_ticket_after_merge(self):
        self.add_make_targets()
        card = self.card(description="Plan z pliku")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertEqual(self.results(card, "reserve"), [], "propozycja nie bierze numerów")
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["plan"]["waves"], [["#1"], ["#2"]])
        reply = self.approve(card)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(set(reply["map"]), {"#1", "#2"})
        self.assertEqual(len(set(reply["map"].values())), 2)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=45), self.events(card))
        self.wait_column(card, "review")
        steps = [s for s in self.of(card, "step.started") if s.get("kind") == "ticket"]
        self.assertEqual([s["ticket"] for s in steps], [reply["map"]["#1"], reply["map"]["#2"]])
        merge = self.results(card, "merge")
        self.assertEqual([r["key"] for r in merge], [reply["map"]["#1"], reply["map"]["#2"]])
        bases = {r["ticket"]: r["base"] for r in self.results(card, "ticket-branch")}
        self.assertNotEqual(bases[reply["map"]["#2"]], self.master)
        for step in self.of(card, "step.started"):
            if step.get("kind") not in ("ticket", "review"):
                continue
            with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
                self.assertIn(f"make test-changed BASE={bases[step['ticket']]}", json.load(fh)["prompt"])
        second_start = next(e["seq"] for e in self.events(card) if e["type"] == "step.started" and
                            e.get("ticket") == reply["map"]["#2"])
        first_merge = next(e["seq"] for e in self.events(card) if e["type"] == "result" and
                           e.get("action") == "merge" and e.get("key") == reply["map"]["#1"])
        self.assertGreater(second_start, first_merge)
        self.assertEqual(self.shown(card)["boardColumn"], "review")
        from tide_daemon.coordination.repoplans import RepoPlans
        plans, _ = RepoPlans(ttl=0).read([self.repo])
        approved_plan = next(p for p in plans if p.card == card and
                             os.path.basename(p.path).endswith(f"{card.lower()}-flow.md"))
        self.assertEqual([r.ticket for r in approved_plan.rows], list(reply["map"].values()))
        self.assertEqual([r.state for r in approved_plan.rows], ["gotowe", "gotowe"])
        self.assertGreaterEqual(approved_plan.updated, merge[-1]["ts"])
        with open(approved_plan.path, encoding="utf-8") as fh:
            frontmatter = fh.read().split("---")[1]
        for field in ("tracker-url:", "tracker-project:", "coordinator: claude", "session: planning-123"):
            self.assertIn(field, frontmatter)
        self.assertGreaterEqual(len(self.results(card, "plan-commit")), 3)
        self.assertTrue(all(os.path.isfile(os.path.join(os.path.dirname(approved_plan.path).replace("plans", "tickets"),
                                                        f"{card.lower()}-flow", r.ticket.lower() + ".md"))
                            for r in approved_plan.rows))

    def test_restart_between_reservation_and_approval_reuses_ids(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        # Kontrolowane przerwanie zapisu nie może ścigać się z automatycznym odzyskiwaniem monitora.
        self.stop_flow()
        journal = self.flow.engine.journal
        original = journal.append
        interrupted = False

        def stop_before_map(ident, kind, **data):
            nonlocal interrupted
            if kind == "plan.approved" and not interrupted:
                interrupted = True
                raise OSError("przerwanie przed mapą")
            return original(ident, kind, **data)

        with mock.patch.object(journal, "append", side_effect=stop_before_map):
            with self.assertRaises(OSError):
                self.flow.engine.approve_plan(card)
        reserved = [r["ticket"] for r in self.results(card, "reserve")]
        self.assertEqual(len(reserved), 2)
        self.restart()
        reply = self.approve(card)
        self.assertEqual(list(reply["map"].values()), reserved)
        self.assertEqual(len(self.results(card, "reserve")), 2)
        self.assertEqual(self.approve(card)["map"], reply["map"])

    def test_restart_after_plan_commit_before_journal_result_keeps_numbering_and_plan(self):
        from tide_daemon.cardflow.state import fold
        from tide_daemon.coordination.repoplans import RepoPlans
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.stop_flow()
        engine = self.flow.engine
        mapping = engine.approve_plan(card)["map"]
        journal = engine.journal
        original = journal.append
        interrupted = False

        def stop_after_commit(ident, event_type, **data):
            nonlocal interrupted
            if event_type == "result" and data.get("action") == "plan-commit" and not interrupted:
                interrupted = True
                raise OSError("przerwanie po commicie planu")
            return original(ident, event_type, **data)

        with mock.patch.object(journal, "append", side_effect=stop_after_commit):
            with self.assertRaises(OSError):
                engine._plan_next(card, fold(journal.read(card)))
        self.restart()
        self.assertTrue(wait_until(lambda: self.results(card, "plan-commit"), timeout=15))
        self.assertEqual(len(self.results(card, "reserve")), 2)
        plans, _ = RepoPlans(ttl=0).read([self.repo])
        approved_plan = next(p for p in plans if p.card == card and
                             os.path.basename(p.path).endswith(f"{card.lower()}-flow.md"))
        self.assertEqual([r.ticket for r in approved_plan.rows], list(mapping.values()))

    def test_failed_plan_commit_can_be_retried_without_new_ticket_numbers(self):
        from tide_daemon.cardflow.engine import FlowError
        from tide_daemon.cardflow.gitops import FlowGitError
        from tide_daemon.cardflow.state import fold, ticket_of

        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5),
                        "koniec tyknięcia przed ręcznym zatwierdzeniem i ponowieniem commita")
        engine = self.flow.engine
        engine.approve_plan(card)
        reserved = [r["ticket"] for r in self.results(card, "reserve")]
        with mock.patch.object(engine.git, "commit_plan", side_effect=FlowGitError("przejściowy błąd")):
            with self.assertRaises(FlowError):
                engine._plan_next(card, fold(engine.journal.read(card)))
        self.assertFalse(self.results(card, "plan-commit")[-1]["ok"])
        state = fold(engine.journal.read(card))
        info = ticket_of(state)
        engine._plan_commit(card, state, self.repo, info["cardBranch"], info["cardWorktree"])
        self.assertTrue(self.results(card, "plan-commit")[-1]["ok"])
        self.assertEqual([r["ticket"] for r in self.results(card, "reserve")], reserved)

    def test_plan_of_a_project_with_board_docs_is_not_committed_to_the_repository(self):
        """09.11, K-89 (LogViewer, `ai-docs: tide`): plany żyją na Tablicy, a repozytorium wyklucza
        docs/agents/plans — zapis planu do repo blokował kartę i wyniósłby plan na gałąź do wypchnięcia."""
        import dataclasses
        from tide_daemon.cardflow.state import fold, ticket_of

        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
            fh.write("/docs/agents/plans/\n")
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5))
        engine = self.flow.engine
        real = engine._project

        def board_docs(card_id):
            item, project = real(card_id)
            return item, dataclasses.replace(project, ai_docs="tide")
        with mock.patch.object(engine, "_project", side_effect=board_docs):
            engine.approve_plan(card)
            state = fold(engine.journal.read(card))
            info = ticket_of(state)
            before = engine.git.sha(self.repo, info["cardBranch"])
            engine._plan_commit(card, state, self.repo, info["cardBranch"], info["cardWorktree"])
        result = self.results(card, "plan-commit")[-1]
        self.assertTrue(result["ok"], result)
        self.assertEqual(engine.git.sha(self.repo, info["cardBranch"]), before, "plan trafił na gałąź karty w repo")
        self.assertNotIn("plan-commit", fold(engine.journal.read(card)).flow_errors)


if __name__ == "__main__":
    import unittest
    unittest.main()
