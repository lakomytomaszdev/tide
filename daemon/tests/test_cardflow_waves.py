"""PT-62: fale, współbieżność i wstrzymanie przez publiczny interfejs przepływu."""
import json
import os
from pathlib import Path
import subprocess

from .privatebus import wait_until
from . import test_cardflow_plan_integration as plan_tests
from .test_cardflow import FlowInProcess

# Cały plan obejmuje kilka ticketów, review i kwalifikacje Git przed scaleniem.
PLAN_RESULT_TIMEOUT = 120


class WaveFlowTest(FlowInProcess):
    proposal = plan_tests.PlanFlowTest.proposal
    submit = plan_tests.PlanFlowTest.submit
    approve = plan_tests.PlanFlowTest.approve

    def ticket_starts(self, card):
        return [step for step in self.of(card, "step.started") if step.get("ticket")]

    def ticket_finishes(self, card):
        tickets = {step["step"] for step in self.ticket_starts(card)}
        return [step for step in self.of(card, "step.finished") if step["step"] in tickets]

    def wave_plan(self, card, count=2):
        path = self.proposal(card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read().split("| #1 |")[0]
        rows = [f"| #{n} | Plasterek {n} | todo | | Sonnet | | standard | Opus | |\n"
                for n in range(1, count + 1)]
        blockers = ", ".join(f"#{n}" for n in range(1, count + 1))
        rows.append(f"| #{count + 1} | Zablokowany | todo | | Sonnet | | standard | Opus | {blockers} |\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source + "".join(rows))
        for number in range(1, count + 2):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: powoli 2\nATRAPA-PLIK: {number}\n\n- [ ] Wynik.\n")
        return path

    def test_independent_tickets_overlap_and_blocked_ticket_waits_for_both_merges(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.wave_plan(card))["ok"])
        mapping = self.approve(card)["map"]
        self.assertNotIn("waves", self.of(card, "plan.proposed")[0])
        self.assertNotIn("waves", self.of(card, "plan.approved")[0])
        self.assertTrue(wait_until(lambda: len(self.ticket_starts(card)) >= 2, timeout=15))
        starts = self.ticket_starts(card)
        first_finish = self.ticket_finishes(card)
        self.assertEqual([s["ticket"] for s in starts[:2]], [mapping["#1"], mapping["#2"]])
        self.assertFalse(first_finish, "niezależne tickety muszą wystartować przed końcem pierwszego")
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT), self.events(card))
        blocked = next(e for e in self.of(card, "step.started") if e["ticket"] == mapping["#3"])
        merges = [e for e in self.results(card, "merge") if e["key"] in (mapping["#1"], mapping["#2"])]
        self.assertEqual(len(merges), 2)
        self.assertTrue(all(e["ok"] and e["seq"] < blocked["seq"] for e in merges))
        for started in self.ticket_starts(card):
            self.assertFalse(os.path.exists(started["worktree"]), "każdy scalony ticket sprząta własne worktree")


    def test_provider_limit_queues_excess_tickets_with_visible_reason(self):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1}}, fh)
        card = self.card()
        self.assertTrue(self.submit(card, self.wave_plan(card))["ok"])
        self.approve(card)
        self.assertTrue(wait_until(lambda: any(e.get("cause") == "slots" for e in self.of(card, "step.waiting")),
                                   timeout=5), self.events(card))
        self.assertEqual(len(self.ticket_starts(card)), 1)
        self.assertIn("limit", self.of(card, "step.waiting")[-1]["reason"])
        self.assertTrue(wait_until(lambda: any("limit" in t.get("waitReason", "")
                        for t in self.shown(card)["derived"]["tickets"]), timeout=5))
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT))
        events = self.events(card)
        active = set()
        for event in events:
            if event["type"] == "step.started":
                active.add(event["step"])
                self.assertLessEqual(len(active), 1)
            elif event["type"] == "step.finished":
                active.discard(event["step"])

    def test_pause_finishes_running_step_and_resume_starts_review(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.wave_plan(card))["ok"])
        self.approve(card)
        self.assertTrue(wait_until(lambda: len(self.ticket_starts(card)) == 2, timeout=5))
        box = []
        self.flow.set_paused(card, True, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=5))
        self.assertTrue(json.loads(box[0])["ok"])
        self.assertTrue(wait_until(lambda: len(self.ticket_finishes(card)) == 2, timeout=10))
        wait_until(lambda: False, timeout=0.4)
        self.assertEqual(len(self.ticket_starts(card)), 2)
        self.assertTrue(self.shown(card)["derived"]["flow"]["paused"])
        box = []
        self.flow.set_paused(card, False, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=5))
        self.assertTrue(json.loads(box[0])["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT))

    def local_config(self):
        from tide_daemon.models import config
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "codex",
                       "executors": [{"name": "Lokalny", "provider": "local", "classes": ["lokalny"]},
                                     {"name": "Lokalny recenzent", "provider": "local", "program": "codex",
                                      "model": "qwen2.5-coder:7b", "endpoint": "local", "classes": ["lokalny"]}]}, fh)
        self.flow.engine.models_config = lambda: config.load(path)
        self.flow.engine.model_probe = lambda endpoint: {"online": True, "kind": "ollama", "models": [
            {"name": "qwen3-coder:30b"}, {"name": "qwen2.5-coder:7b"}]}

    def test_local_steps_are_serial_and_pending_report_runs_before_next_step(self):
        import threading
        from unittest import mock
        from tide_daemon.models import queue
        isolated = queue.Queue()
        with mock.patch.object(queue, "shared", return_value=isolated):
            self.local_config()
            first = self.card(description="ATRAPA: na-sygnal")
            second = self.card(description="ATRAPA: powoli 2")
            self.assertTrue(self.start(first, executor="Lokalny", preferredProvider="local")["ok"])
            signal = Path(self.of(first, "step.started")[0]["stepDir"], "out", "release-agent")
            begun, release = threading.Event(), threading.Event()
            def report():
                begun.set()
                release.wait(120)
            lane = queue.lane_of("http://127.0.0.1:11434/v1")
            worker = None
            try:
                self.assertTrue(self.start(second, executor="Lokalny", preferredProvider="local")["ok"])
                self.assertEqual(len(self.of(second, "step.started")), 0)
                worker = threading.Thread(target=lambda: isolated.run(report, endpoint="local", role="summary",
                                          lane=lane), daemon=True)
                worker.start()
                # Raport musi naprawdę czekać, zanim pierwszy krok zwolni slot.
                self.assertTrue(wait_until(lambda: any(item["role"] == "summary" for item in
                    isolated.snapshot().get(lane, {}).get("waiting", [])), timeout=5))
                self.assertFalse(begun.is_set())
                self.assertFalse(self.of(first, "step.finished"))
                signal.touch()
                self.assertTrue(wait_until(begun.is_set, timeout=60))
                self.assertEqual(len(self.of(first, "step.started")), 1)
                self.assertEqual(self.of(second, "step.started"), [])
                self.assertTrue(self.of(first, "step.waiting") or self.of(second, "step.waiting"))
            finally:
                signal.touch()
                release.set()
                if worker is not None:
                    worker.join(5)
            self.wait_column(first, "review", timeout=20)
            self.wait_column(second, "review", timeout=20)
            # Review jest widoczne jeszcze przed końcem lokalnego kroku domknięcia.
            for card in (first, second):
                self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=20), self.events(card))
            # Zapis step.finished poprzedza zwolnienie slotu; kończymy tick przed sprawdzeniem kolejki
            # i wyjściem z podmiany queue.shared (także przy cleanupie tego testu).
            self.stop_flow()
            self.assertFalse(isolated.busy())

    def test_restart_restores_local_slot_and_releases_it_after_running_step(self):
        from unittest import mock
        from tide_daemon.models import queue
        isolated = queue.Queue()
        with mock.patch.object(queue, "shared", return_value=isolated):
            self.local_config()
            card = self.card(description="ATRAPA: powoli 2")
            self.assertTrue(self.start(card, executor="Lokalny", preferredProvider="local")["ok"])
            self.restart()
            self.local_config()
            self.wait_column(card, "review", timeout=15)
            self.assertEqual(len([s for s in self.of(card, "step.started") if s.get("kind") != "close"]), 2)
            self.assertFalse(isolated.busy())

    def test_question_on_one_ticket_can_be_answered_while_another_is_running(self):
        card = self.card()
        path = self.wave_plan(card)
        first = os.path.join(os.path.dirname(path), "tickets", "1.md")
        with open(first, encoding="utf-8") as fh:
            source = fh.read()
        with open(first, "w", encoding="utf-8") as fh:
            fh.write(source.replace("ATRAPA: powoli 2", "ATRAPA: pytanie"))
        self.assertTrue(self.submit(card, path)["ok"])
        mapping = self.approve(card)["map"]
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["ticket"], mapping["#1"])
        self.assertEqual(shown["derived"]["flow"]["decisions"][0]["kind"], "question")
        self.assertTrue(any(s["ticket"] == mapping["#2"] for s in self.of(card, "step.started")))
        self.assertFalse(any(s["ticket"] == mapping["#2"] for s in self.of(card, "step.finished")))
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "ISO"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=5))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT))

    def test_queued_direct_card_keeps_one_ticket_until_provider_slot_is_free(self):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1}}, fh)
        first = self.card(description="ATRAPA: powoli 2")
        second = self.card(description="ATRAPA: powoli 2")
        self.assertTrue(self.start(first)["ok"])
        reply = self.start(second)
        self.assertTrue(reply["ok"])
        self.assertEqual(self.of(second, "step.started"), [])
        duplicate = self.start(second)
        self.assertEqual(duplicate.get("error"), "flow-active", duplicate)
        self.assertTrue(wait_until(lambda: "limit" in self.shown(second)["derived"]["note"], timeout=5))
        self.wait_column(first, "review")
        self.wait_column(second, "review")
        self.assertEqual(len(self.results(second, "reserve")), 1)

    def test_default_limit_allows_three_tickets_and_queues_the_fourth(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.wave_plan(card, count=4))["ok"])
        mapping = self.approve(card)["map"]
        self.assertTrue(wait_until(lambda: any(e.get("cause") == "slots" for e in self.of(card, "step.waiting")),
                                   timeout=5), self.events(card))
        self.assertEqual([s["ticket"] for s in self.ticket_starts(card)],
                         [mapping["#1"], mapping["#2"], mapping["#3"]])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT))
        active = set()
        for event in self.events(card):
            if event["type"] == "step.started":
                active.add(event["step"])
                self.assertLessEqual(len(active), 3)
            elif event["type"] == "step.finished":
                active.discard(event["step"])

    def test_queued_direct_step_preserves_screenshot_after_dialog_removes_source(self):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1}}, fh)
        first = self.card(description="ATRAPA: na-sygnal")
        second = self.card(description="ATRAPA: na-sygnal")
        try:
            self.assertTrue(self.start(first)["ok"])
            signal = Path(self.started(first)["stepDir"], "out", "release-agent")
            image = os.path.join(self.base, "shot.png")
            with open(image, "wb") as fh:
                fh.write(b"screenshot-data")
            self.assertTrue(self.start(second, images=[image])["ok"])
            self.assertTrue(wait_until(lambda: any(e.get("cause") == "slots" for e in
                                                  self.of(second, "step.waiting")), timeout=5))
            self.assertEqual(self.of(second, "step.started"), [], "Zrzut ma czekać w kolejce")
            self.assertFalse(self.of(first, "step.finished"))
            os.unlink(image)
            signal.touch()

            def received_image():
                starts = self.of(second, "step.started")
                return starts and Path(starts[0]["stepDir"], "out", "fake-call.json").is_file()

            # Czytamy wejście rzeczywistego procesu; kolumna review jest przejściowa
            # i mogła już zniknąć, zanim callback startu zwrócił sterowanie testowi.
            self.assertTrue(wait_until(received_image, timeout=60), self.events(second))
            started = self.of(second, "step.started")[0]
            self.assertFalse(self.of(second, "step.finished"), "Druga atrapa nadal czeka na kontrolę wejść")
            self.assertFalse(os.path.exists(image))
            with open(os.path.join(started["stepDir"], "in", "images", "1-shot.png"), "rb") as fh:
                self.assertEqual(fh.read(), b"screenshot-data")
            # 04.11, K-49: Read musi móc odczytać zrzut z katalogu wejść kroku.
            with open(os.path.join(started["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
                argv = json.load(fh)["argv"]
            images = os.path.join(started["stepDir"], "in", "images")
            self.assertIn(images, [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--add-dir"])
        finally:
            # Start w bieżącym ticku musi się zakończyć przed odczytem listy,
            # także gdy asercja zawiedzie przed zapisem step.started.
            self.stop_flow()
            released = []
            for card in (first, second):
                for started in self.of(card, "step.started"):
                    if started["kind"] == "ticket":
                        Path(started["stepDir"], "out", "release-agent").touch()
                        released.append(started)
            for started in released:
                proc = self.flow.engine.procs.get(started["step"])
                if proc is not None:
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.flow.engine._runner_for(started).kill(started, proc=proc)
                        proc.wait(timeout=5)

    def test_retry_uses_own_worktree_while_another_ticket_is_running(self):
        card = self.card()
        path = self.wave_plan(card)
        first = os.path.join(os.path.dirname(path), "tickets", "1.md")
        with open(first, encoding="utf-8") as fh:
            source = fh.read()
        with open(first, "w", encoding="utf-8") as fh:
            fh.write(source.replace("ATRAPA: powoli 2", "ATRAPA: blad-raz"))
        self.assertTrue(self.submit(card, path)["ok"])
        mapping = self.approve(card)["map"]
        self.assertTrue(wait_until(lambda: len([s for s in self.of(card, "step.started")
                        if s["ticket"] == mapping["#1"]]) >= 2, timeout=10))
        attempts = [s for s in self.of(card, "step.started") if s["ticket"] == mapping["#1"]]
        self.assertEqual(attempts[0]["worktree"], attempts[1]["worktree"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=PLAN_RESULT_TIMEOUT))

    def test_manual_finish_rejects_all_running_and_not_started_tickets(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.wave_plan(card))["ok"])
        mapping = self.approve(card)["map"]
        self.assertTrue(wait_until(lambda: len(self.ticket_starts(card)) >= 2, timeout=5))
        self.assertTrue(json.loads(self.board.move_card(card, "done", 0))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.finished"), timeout=10))
        self.assertEqual({event["ticket"] for event in self.of(card, "ticket.rejected")}, set(mapping.values()))
        self.assertEqual(len(self.ticket_finishes(card)), 2)

    def test_manual_finish_keeps_merged_ticket_and_rejects_remaining_plan(self):
        card = self.card()
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 15")):
            ticket_path = os.path.join(os.path.dirname(path), "tickets", f"{number}.md")
            with open(ticket_path, encoding="utf-8") as fh:
                source = fh.read()
            with open(ticket_path, "w", encoding="utf-8") as fh:
                fh.write(source.replace("powoli 2", mode))
        self.assertTrue(self.submit(card, path)["ok"])
        mapping = self.approve(card)["map"]
        self.assertTrue(wait_until(lambda: any(event.get("key") == mapping["#1"] and event.get("ok")
                        for event in self.results(card, "merge")), timeout=10))
        self.assertTrue(json.loads(self.board.move_card(card, "archived", 0))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.finished"), timeout=10))
        self.assertEqual({event["ticket"] for event in self.of(card, "ticket.rejected")},
                         {mapping["#2"], mapping["#3"]})
        self.assertTrue(next(event for event in self.results(card, "merge") if event["key"] == mapping["#1"])["ok"])
