"""PT-172: kontrakt D-Bus `ApproveCardEnvironment(card, yaml, onlyPlan)` i `BoxStatus(slug)` na prywatnej magistrali.

Test woła metody usługi bez UI, jak inne testy kontraktu /Board: zły YAML wysłany spoza panelu jest odrzucany bez
zapisu (profil projektu i karty w Tablicy, zdarzenie `box.approved`) i bez budowy. Incusa w teście nie ma, więc
budowa w tle kończy się błędem po stronie buildera — kontrakt sprawdza tylko zapisy i odpowiedzi.
"""
import json
import os

from .privatebus import wait_until
from .test_cardflow_dbus import FlowCase


class EnvironmentDbusTest(FlowCase):
    def spawn(self):
        self.env["TIDE_BOARD_DIR"] = os.path.join(self.base, "boxboard")
        return super().spawn()

    def box_files(self):
        root = os.path.join(self.base, "boxboard")
        return [os.path.join(d, f) for d, _s, files in os.walk(root) for f in files if f in ("box.yaml", "journal.jsonl")]

    def started_card(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")   # karta czeka na odpowiedź, nie kończy się
        self.assertTrue(self.start(card_id)["ok"])
        return card_id

    def test_bad_yaml_from_outside_the_ui_is_rejected_without_save_or_build(self):
        card_id = self.started_card()
        for text, expected in (("tide-box: 1\nmagic: true\n", "magic: nieznany klucz"),
                               ("tide-box: 1\napt: 5\n", "apt: oczekiwano listy"),
                               ("tide-box: 1\nnetwork:\n  domains:\n    - github.com\n", "wymaga jawnego zaznaczenia"),
                               ("tide-box: 1\nnetwork:\n  full: true\n", "network.full")):
            for only_plan in (False, True):
                reply = self.call("ApproveCardEnvironment", card_id, text, only_plan)
                self.assertFalse(reply["ok"], reply)
                self.assertEqual(reply["error"], "invalid-environment")
                self.assertIn(expected, reply["message"])
        self.assertEqual(self.box_files(), [])
        self.assertEqual(self.of(card_id, "box.wait"), [])

    def test_valid_yaml_is_saved_by_the_service_and_recorded_as_approved_from_the_ui(self):
        card_id = self.started_card()
        reply = self.call("ApproveCardEnvironment", card_id,
                          "tide-box: 1\nnetwork:\n  domains:\n    - api.example.org\n"
                          "    - github.com\naccepted_red:\n  - github.com\n", False)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["acceptedRed"], reply["onlyPlan"]), (["github.com"], False))
        saved = [os.path.relpath(p, os.path.join(self.base, "boxboard")) for p in self.box_files()]
        self.assertIn(os.path.join("projects", "proj", "box.yaml"), saved)
        self.assertIn(os.path.join("projects", "proj", "cards", card_id.lower(), "box.yaml"), saved)
        journal = os.path.join(self.base, "boxboard", "projects", "proj", "box", "journal.jsonl")
        self.assertTrue(wait_until(lambda: os.path.isfile(journal), timeout=10))
        with open(journal, encoding="utf-8") as fh:
            approved = [e for e in map(json.loads, fh) if e["type"] == "box.approved"]
        self.assertEqual([(e["source"], e["accepted_red"]) for e in approved], [("ui", ["github.com"])])

    def test_only_plan_with_disabled_boxes_defers_consent_without_wait_in_snapshot_and_box_status(self):
        # PT-290 (D1): usługa testu ma domyślne `box.enabled: false` — „Tylko plan” spoza UI nie tworzy oczekiwania
        # `environment`; zapis odroczenia jest w dzienniku. Przy włączonym pudełku: test_cardflow_environment.py.
        card_id = self.started_card()
        reply = self.call("ApproveCardEnvironment", card_id, "tide-box: 1\n", True)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["boxWait"], reply["boxEnabled"], reply["build"]), ("", False, ""))
        self.assertIn("box.enabled=false", reply["message"])
        self.assertEqual([(e["value"], e.get("deferred")) for e in self.of(card_id, "box.wait")],
                         [("", "environment")])
        self.assertEqual(self.box_files(), [])
        flow = self.shown(card_id)["derived"]["flow"]
        self.assertEqual((flow["boxWait"], flow["boxWaitLabel"]), ("", ""))
        status = self.call("BoxStatus", "proj")
        self.assertTrue(status["ok"], status)
        self.assertEqual([(c["card"], c["boxWait"]) for c in status["cards"]], [(card_id, "")])

    def test_disabled_full_approval_over_dbus_records_the_decision_but_builds_nothing(self):
        card_id = self.started_card()
        reply = self.call("ApproveCardEnvironment", card_id, "tide-box: 1\n", False)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["build"], reply["boxEnabled"], reply["decision"]), ("", False, "recorded"))
        self.assertIn("budowy nie zlecono", reply["message"])
        journal = os.path.join(self.base, "boxboard", "projects", "proj", "box", "journal.jsonl")
        with open(journal, encoding="utf-8") as fh:
            types = [json.loads(line)["type"] for line in fh]
        self.assertEqual(types, ["box.approved"], "bez box.build i innych zdarzeń budowy")

    def test_box_status_contract(self):
        status = self.call("BoxStatus", "proj")
        for key in ("slug", "box", "state", "exists", "nicFiltering", "nicLabel", "recipeHash", "profileHash", "diff",
                    "usable", "cards"):
            self.assertIn(key, status)
        self.assertEqual((status["slug"], status["box"], status["exists"]), ("proj", "proj", False))
        self.assertEqual(status["nicLabel"], "filtrowanie NIC: nie")
        self.assertEqual(self.call("BoxStatus", "../zly")["error"], "bad-project")
        self.assertEqual(self.call("BoxStatus", "nie-ma")["error"], "no-project")
