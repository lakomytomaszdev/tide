"""Ustawienia opiekuna karty (K-37, PT-151): wyłącznik, wykonawca, limit działań, minuty czekania i „Tylko lokalnie”.

Najpierw czyste funkcje `caretaker.py` (`settings_of`, `setting_problem`, `executor_name`, `setup`) i widoki na
złożonych dziennikach, potem `FlowEngine` (`get/set_flow_settings`, `_caretake` na złożonym dzienniku), przebiegi
z atrapą `claude`/`codex` (tests/flowfakes.py) i na końcu `SetCardFlowSettings` na prywatnej magistrali.
"""
import json
import os
import unittest

from tide_daemon.cardflow import caretaker, decisions
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import derived, fold
from tide_daemon.models import executors

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal
from .test_cardflow_caretaker_retry import EngineOnJournal
from .test_cardflow_dbus import FlowCase

DEFAULT = executors.default_catalog()
NO_LOCAL = executors.Catalog(tuple(e for e in executors.DEFAULT_EXECUTORS if e.provider != "local"), {})


def failing(provider="anthropic"):
    j = Journal(provider=provider)
    j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
    return j


class SettingsTest(unittest.TestCase):
    def test_defaults_and_values_from_the_file(self):
        self.assertEqual(caretaker.settings_of({}), {"caretakerEnabled": True, "caretakerExecutor": "",
                                                     "caretakerActionLimit": 3, "caretakerDecisionMinutes": 10})
        stored = {"caretakerEnabled": False, "caretakerExecutor": " Sonnet ", "caretakerActionLimit": 10,
                  "caretakerDecisionMinutes": 1440}
        self.assertEqual(caretaker.settings_of(stored), {"caretakerEnabled": False, "caretakerExecutor": "Sonnet",
                                                         "caretakerActionLimit": 10, "caretakerDecisionMinutes": 1440})
        # Ręcznie zepsuty plik: zły typ albo zakres daje wartość domyślną.
        broken = {"caretakerEnabled": "nie", "caretakerExecutor": 5, "caretakerActionLimit": True,
                  "caretakerDecisionMinutes": 0}
        self.assertEqual(caretaker.settings_of(broken), caretaker.settings_of({}))

    def test_validation_codes(self):
        bad = [("caretakerEnabled", "false"), ("caretakerEnabled", 1), ("caretakerExecutor", 7),
               ("caretakerExecutor", "x" * 61), ("caretakerActionLimit", 0), ("caretakerActionLimit", 11),
               ("caretakerActionLimit", "3"), ("caretakerActionLimit", 2.5), ("caretakerActionLimit", True),
               ("caretakerDecisionMinutes", 0), ("caretakerDecisionMinutes", 1441), ("caretakerDecisionMinutes", None)]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                self.assertEqual(caretaker.setting_problem(key, value, DEFAULT)[0], "bad-request")
        self.assertEqual(caretaker.setting_problem("caretakerExecutor", "GPT-99", DEFAULT)[0], "bad-executor")
        good = [("caretakerEnabled", False), ("caretakerExecutor", ""), ("caretakerExecutor", "Opus"),
                ("caretakerExecutor", "Lokalny"), ("caretakerActionLimit", 1), ("caretakerActionLimit", 10),
                ("caretakerDecisionMinutes", 1), ("caretakerDecisionMinutes", 1440)]
        for key, value in good:
            with self.subTest(key=key, value=value):
                self.assertIsNone(caretaker.setting_problem(key, value, DEFAULT))


class ExecutorChoiceTest(unittest.TestCase):
    def test_empty_setting_takes_the_strongest_class_of_the_preferred_provider(self):
        self.assertEqual(caretaker.executor_name(DEFAULT, failing().st), "Opus")
        self.assertEqual(caretaker.executor_name(DEFAULT, failing("openai").st), "Codex mocny")

    def test_executor_from_the_catalog_is_used(self):
        self.assertEqual(caretaker.executor_name(DEFAULT, failing().st, "Sonnet"), "Sonnet")
        self.assertEqual(caretaker.executor_name(DEFAULT, failing().st, "Codex mocny"), "Codex mocny")
        # Wykonawca usunięty z katalogu po zapisaniu ustawienia — domyślny.
        self.assertEqual(caretaker.executor_name(DEFAULT, failing().st, "Usunięty"), "Opus")

    def test_local_only_card_takes_a_local_executor_even_with_a_cloud_setting(self):
        local = failing("local").st
        self.assertEqual(caretaker.executor_name(DEFAULT, local, "Opus"), "Lokalny")
        self.assertEqual(caretaker.executor_name(DEFAULT, local, ""), "Lokalny")
        two = executors.Catalog((*executors.DEFAULT_EXECUTORS,
                                 executors.Executor("Lokalny 2", "codex", "qwen", "local", classes=("lokalny",))), {})
        self.assertEqual(caretaker.executor_name(two, local, "Lokalny 2"), "Lokalny 2")
        self.assertEqual(caretaker.executor_name(NO_LOCAL, local, "Opus"), "")

    def test_setup_reasons_and_notes(self):
        conf = caretaker.setup(failing().st, {"caretakerExecutor": "Sonnet", "caretakerActionLimit": 1}, DEFAULT)
        self.assertEqual(conf, {"enabled": True, "executor": "Sonnet", "reason": "", "note": "", "limit": 1,
                                "decisionMinutes": 10})
        off = caretaker.setup(failing().st, {"caretakerEnabled": False}, DEFAULT)
        self.assertEqual((off["reason"], off["note"]), (caretaker.DISABLED_NOTE, ""))
        local = caretaker.setup(failing("local").st, {"caretakerExecutor": "Opus"}, DEFAULT)
        self.assertEqual((local["executor"], local["reason"]), ("Lokalny", ""))
        alone = caretaker.setup(failing("local").st, {"caretakerExecutor": "Opus"}, NO_LOCAL)
        self.assertEqual((alone["executor"], alone["reason"], alone["note"]),
                         ("", caretaker.LOCAL_ONLY_NOTE, caretaker.LOCAL_ONLY_NOTE))


class ViewTest(unittest.TestCase):
    def view(self, j, conf):
        st = j.st
        st.caretaker_setup = conf
        return st, derived(st, {"column": "todo"})

    def test_disabled_caretaker_gives_todays_decision_without_a_note(self):
        j = failing()
        st, out = self.view(j, caretaker.setup(j.st, {"caretakerEnabled": False}, DEFAULT))
        self.assertFalse(out["flow"]["caretaker"]["enabled"])
        self.assertEqual(caretaker.unavailable(st, "todo"), caretaker.DISABLED_NOTE)
        decision = out["flow"]["decisions"][0]
        self.assertEqual(decision["kind"], "error")
        self.assertNotIn("caretaker", decision)
        self.assertNotIn("opiekun", decision["why"])

    def test_local_only_card_with_a_local_executor_has_the_caretaker(self):
        j = failing("local")
        st, out = self.view(j, caretaker.setup(j.st, {"caretakerExecutor": "Opus"}, DEFAULT))
        self.assertTrue(out["flow"]["caretaker"]["enabled"])
        self.assertEqual(caretaker.unavailable(st, "todo"), "")
        self.assertNotIn("pracy tylko lokalnej", decisions.build_decisions(st)[0]["why"])

    def test_local_only_card_without_a_local_executor_has_the_note(self):
        j = failing("local")
        st, out = self.view(j, caretaker.setup(j.st, {}, NO_LOCAL))
        self.assertFalse(out["flow"]["caretaker"]["enabled"])
        self.assertIn("pracy tylko lokalnej", out["flow"]["decisions"][0]["why"])


class EngineSettingsTest(EngineOnJournal):
    def test_get_returns_defaults_and_set_keeps_other_fields(self):
        engine = self.engine
        got = engine.get_flow_settings()
        self.assertEqual({k: got[k] for k in caretaker.SETTINGS_DEFAULTS}, caretaker.SETTINGS_DEFAULTS)
        self.assertEqual(got["providerSlots"], {"anthropic": 1})
        reply = engine.set_flow_settings({"caretakerEnabled": False, "caretakerExecutor": "Sonnet",
                                          "caretakerActionLimit": 5, "caretakerDecisionMinutes": 30})
        self.assertEqual({k: reply[k] for k in caretaker.SETTINGS_DEFAULTS},
                         {"caretakerEnabled": False, "caretakerExecutor": "Sonnet", "caretakerActionLimit": 5,
                          "caretakerDecisionMinutes": 30})
        self.assertEqual(reply["providerSlots"], {"anthropic": 1})
        reply = engine.set_flow_settings({"limitThresholdPercent": 20})
        self.assertEqual((reply["caretakerActionLimit"], reply["limitThresholdPercent"]), (5, 20))
        for request, code in (({"caretakerExecutor": "GPT-99"}, "bad-executor"),
                              ({"caretakerActionLimit": 0}, "bad-request"),
                              ({"caretakerActionLimit": "3"}, "bad-request"),
                              ({"caretakerDecisionMinutes": 1441}, "bad-request"),
                              # Złe ustawienie opiekuna nie zapisuje też poprawnych pól z tego samego żądania.
                              ({"limitThresholdPercent": 30, "caretakerActionLimit": 11}, "bad-request")):
            with self.subTest(request=request), self.assertRaises(FlowError) as raised:
                engine.set_flow_settings(request)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(engine.get_flow_settings(), reply)

    def failing_local(self):
        card = "K-1"
        self.add(card, "card.started", entry="now", executor="Lokalny", program="codex", model="", provider="local",
                 preferredProvider="local", repo="/r", column="todo")
        for name, attempt, parent in (("K-1-PT-7-1", 0, ""), ("K-1-PT-7-2", 1, "K-1-PT-7-1")):
            self.add(card, "step.started", step=name, kind="ticket", ticket="PT-7", executor="Lokalny",
                     provider="local", attempt=attempt, parentStep=parent, worktree="/w", branch="ticket/pt-7")
            self.add(card, "step.finished", step=name, ticket="PT-7", result="error", reason="awaria testowa")
            if not parent:
                self.add(card, "step.retry", step=name, ticket="PT-7")
        return card

    def test_local_only_card_without_a_local_executor_does_not_start_the_caretaker(self):
        card = self.failing_local()
        self.engine.set_flow_settings({"caretakerExecutor": "Opus"})
        self.engine.catalog = lambda: NO_LOCAL
        self.engine._caretake(card, fold(self.journal.read(card)))
        self.assertEqual([e for e in self.journal.read(card) if e["type"].startswith("caretaker.")], [])
        out = self.engine.derived({"id": card, "column": "todo"})
        self.assertFalse(out["flow"]["caretaker"]["enabled"])
        decision = out["flow"]["decisions"][0]
        self.assertIn("Opiekun niedostępny dla pracy tylko lokalnej", decision["why"])
        self.assertNotIn("caretaker", decision)
        # Ten sam dziennik z wykonawcą lokalnym w katalogu: opiekun ma wykonawcę lokalnego, nie chmurowy Opus.
        self.engine.catalog = executors.default_catalog
        self.assertEqual(self.engine._caretaker_setup(fold(self.journal.read(card)))["executor"], "Lokalny")
        self.assertTrue(self.engine.derived({"id": card, "column": "todo"})["flow"]["caretaker"]["enabled"])

    def test_disabled_caretaker_waiting_to_start_records_a_stale_action_and_the_decision_returns(self):
        """Opiekun wyłączony, gdy jego krok czekał na start (np. na slot): bez kroku, bez eskalacji i bez liczenia."""
        j = failing()
        for event in j.events:
            fields = {k: v for k, v in event.items() if k not in ("seq", "ts", "type")}
            self.add("K-1", event["type"], **fields)
        st = fold(self.journal.read("K-1"))
        problem = caretaker.problems(st)[0]
        self.add("K-1", caretaker.STARTED, problem=problem["id"], key=f"caretaker:{problem['id']}:1", n=1,
                 step="K-1-care-1", target=problem["step"], root=problem["root"], ticket="PT-7", trigger="error",
                 triggers=["error"], decision=problem["decision"], watermark=st.last_seq)
        self.assertEqual(decisions.build_decisions(fold(self.journal.read("K-1"))), [], "decyzja ukryta")
        self.engine.set_flow_settings({"caretakerEnabled": False})
        self.engine._caretake("K-1", fold(self.journal.read("K-1")))
        events = self.journal.read("K-1")
        self.assertFalse([e for e in events if e["type"] == "step.started" and e.get("kind") == caretaker.KIND])
        self.assertEqual([e["type"] for e in events if e["type"].startswith("caretaker.")],
                         [caretaker.STARTED, caretaker.ACTION])
        action = [e for e in events if e["type"] == caretaker.ACTION][0]
        self.assertEqual((action["stale"], action["detail"]), (True, caretaker.DISABLED_NOTE))
        st = fold(events)
        self.assertEqual(caretaker.owned(st, problem["id"])["count"], 0)
        out = self.engine.derived({"id": "K-1", "column": "todo"})
        self.assertNotIn("caretaker", out["flow"]["decisions"][0])
        self.assertFalse(out["flow"]["caretaker"]["enabled"])


class CaretakerSettingsFlowTest(FlowInProcess):
    """Przebiegi przez FlowEngine z atrapą `claude`/`codex`."""

    def failing_card(self, care="escalate"):
        return self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                                     f"ATRAPA-POWOD: test pada ATRAPA-OPIEKUN: {care}")

    def caretaker_steps(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker"]

    def test_disabled_caretaker_gives_todays_decision(self):
        self.flow.engine.set_flow_settings({"caretakerEnabled": False})
        card_id = self.failing_card()
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting", timeout=40)
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(self.of(card_id, "caretaker.started"), [])
        self.assertEqual(self.caretaker_steps(card_id), [])
        flow = card["derived"]["flow"]
        self.assertFalse(flow["caretaker"]["enabled"])
        decision = flow["decisions"][0]
        self.assertEqual(decision["kind"], "error")
        self.assertNotIn("caretaker", decision)
        self.assertNotIn("opiekun", decision["why"].lower())
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.notice")) == 1, timeout=5))

    def test_executor_from_the_settings_runs_the_caretaker(self):
        self.flow.engine.set_flow_settings({"caretakerExecutor": "Sonnet"})
        card_id = self.failing_card()
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=40), self.events(card_id))
        self.assertEqual([(s["executor"], s["model"]) for s in self.caretaker_steps(card_id)], [("Sonnet", "sonnet")])

    def test_action_limit_one_escalates_after_the_first_failed_retry(self):
        self.flow.engine.set_flow_settings({"caretakerActionLimit": 1})
        card_id = self.failing_card("retry")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                   timeout=90), self.events(card_id))
        card = self.wait_column(card_id, "waiting", timeout=30)
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.caretaker_steps(card_id)), 1, "drugi krok opiekuna nie startuje")
        self.assertEqual([(a["action"], a["ok"]) for a in self.of(card_id, "caretaker.action")], [("retry", True)])
        escalated = self.of(card_id, "caretaker.escalated")
        self.assertEqual([(e["code"], e["count"]) for e in escalated], [("caretaker-limit", 1)])
        self.assertIn("z 1 dozwolonych", escalated[0]["summary"])
        tried = card["derived"]["flow"]["decisions"][0]["caretaker"]["tried"]
        self.assertEqual([(t["action"], t["ok"]) for t in tried], [("retry", True)])
        with open(os.path.join(self.caretaker_steps(card_id)[0]["stepDir"], "in", "prompt.txt"),
                  encoding="utf-8") as fh:
            self.assertIn("najwyżej 1 razy", fh.read())

    def test_local_only_card_runs_the_caretaker_locally_despite_a_cloud_setting(self):
        from tide_daemon.models import config as models_config
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "codex",
                       "executors": [{"name": "Lokalny", "provider": "local", "classes": ["lokalny"]},
                                     {"name": "Lokalny recenzent", "provider": "local", "program": "codex",
                                      "model": "qwen2.5-coder:7b", "endpoint": "local", "classes": ["lokalny"]},
                                     {"name": "Opus", "program": "claude", "model": "opus", "provider": "anthropic",
                                      "classes": ["mocny"]}]}, fh)
        engine = self.flow.engine
        engine.models_config = lambda: models_config.load(path)
        engine.model_probe = lambda endpoint: {"online": True, "kind": "ollama",
                                                "models": [{"name": "qwen3-coder:30b"}, {"name": "qwen2.5-coder:7b"}]}
        engine.set_flow_settings({"caretakerExecutor": "Opus"})
        card_id = self.failing_card()
        reply = self.start(card_id, executor="Lokalny", preferredProvider="local")
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=60), self.events(card_id))
        steps = self.caretaker_steps(card_id)
        self.assertEqual([(s["executor"], s["provider"], s["local"]) for s in steps], [("Lokalny", "local", True)])
        with open(os.path.join(steps[0]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn("--oss", call["argv"])
        self.assertNotIn("OPENAI_API_KEY", call["env"])
        self.assertEqual([e["failed"] for e in self.of(card_id, "caretaker.escalated")], [False])


class FlowSettingsCaretakerDbusTest(FlowCase):
    def test_caretaker_settings_on_the_bus(self):
        got = self.call("GetCardFlowSettings")
        self.assertEqual({k: got[k] for k in caretaker.SETTINGS_DEFAULTS}, caretaker.SETTINGS_DEFAULTS)
        for request, code in (({"caretakerExecutor": "GPT-99"}, "bad-executor"),
                              ({"caretakerActionLimit": 0}, "bad-request"),
                              ({"caretakerActionLimit": 11}, "bad-request"),
                              ({"caretakerActionLimit": "3"}, "bad-request"),
                              ({"caretakerDecisionMinutes": 0}, "bad-request"),
                              ({"caretakerDecisionMinutes": 1441}, "bad-request")):
            with self.subTest(request=request):
                rejected = self.call("SetCardFlowSettings", json.dumps(request))
                self.assertEqual((rejected["ok"], rejected["error"]), (False, code), rejected)
        changed = self.call("SetCardFlowSettings", json.dumps({
            "caretakerEnabled": False, "caretakerExecutor": "Sonnet", "caretakerActionLimit": 1,
            "caretakerDecisionMinutes": 1440, "limitTransfer": {"mode": "auto", "executor": "Codex mocny"},
            "limitThresholdPercent": 15, "providerSlots": {"anthropic": 6}}))
        self.assertTrue(changed["ok"], changed)
        self.restart()
        got = self.call("GetCardFlowSettings")
        self.assertEqual({k: got[k] for k in caretaker.SETTINGS_DEFAULTS},
                         {"caretakerEnabled": False, "caretakerExecutor": "Sonnet", "caretakerActionLimit": 1,
                          "caretakerDecisionMinutes": 1440})
        self.assertEqual((got["limitTransfer"], got["limitThresholdPercent"], got["providerSlots"]),
                         ({"mode": "auto", "executor": "Codex mocny"}, 15, {"anthropic": 6}))
        rejected = self.call("SetCardFlowSettings", json.dumps({"limitTransfer": {"mode": "auto", "executor": ""}}))
        self.assertEqual((rejected["ok"], rejected["error"]), (False, "bad-settings"))


if __name__ == "__main__":
    unittest.main()
