"""Sekcja „Środowisko karty” w planie do zatwierdzenia (PT-172): wspólny komponent apps/common/qml/card/CardEnvironment.qml
w panelu karty widoku Tablica.

- pakiet: komponent trafia do views/board/qml/card/; w QML nie ma nazw klas ryzyka (zielone/żółte/czerwone liczy
  usługa i podaje w modelu), kolorów na sztywno ani listy kluczy profilu,
- sonda offscreen na modelu liczonym przez usługę (environmentfix.py): kolor i opis każdej pozycji z modelu, pozycje
  wymagające zgody są wyłączone i dopiero jawne zaznaczenie dopisuje je do wysyłanego YAML-a, „Tylko plan”, „Zmień”,
  odmowa usługi pokazana w sekcji, stan „do przebudowy” z różnicą, znacznik boxWait i pytanie dla projektu bez profilu.
"""
import json
import os
import re
import shutil
import unittest

from . import REPO
from .environmentfix import SLUG, clean_snapshot_then_change, environment, plan_card
from .test_view import FIND_PY, ProbeCase

COMPONENT = os.path.join(REPO, "apps", "common", "qml", "card", "CardEnvironment.qml")

ALL_PY = r'''
ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")

def rows():
    return json.loads(js("JSON.stringify(" + ALL + "(page.cardPanel, 'envItem').map(r => ({ "
        "id: r.itemId, checked: r.checked, "
        "label: " + ALL + "(r, 'envItemLabel')[0].text, risk: " + ALL + "(r, 'envItemRiskLabel')[0].text, "
        "note: " + ALL + "(r, 'envItemNote').map(n => n.text).join(''), "
        "dot: String(" + ALL + "(r, 'envItemRisk')[0].color), "
        "switch: " + ALL + "(r, 'envItemSwitch').length === 1 })))"))

def text(name):
    items = js("JSON.stringify(" + ALL + "(page.cardPanel, '" + name + "').map(t => t.text))")
    return json.loads(items)

def press(name):
    js(ALL + "(page.cardPanel, '" + name + "')[0].clicked()")
    wait(120)

def flip(item_id):
    js(ALL + "(page.cardPanel, 'envItem').find(r => r.itemId === " + json.dumps(item_id) + ").flip()")
    wait(60)

def open_card():
    click(card("K-6"))
    wait(150)
'''


def calls_of(out, member):
    return [c for c in out["calls"] if c[0] == member]


class PackageTest(unittest.TestCase):
    def test_component_is_staged_with_the_other_card_components(self):
        from .test_view import stage
        import tempfile
        from daemon.tests.fakebin import files_under
        with tempfile.TemporaryDirectory(prefix="tide-env-pkg-") as tmp:
            files = files_under(os.path.join(stage(os.path.join(tmp, "pkg")), "views", "board"))
        self.assertIn("qml/card/CardEnvironment.qml", files)

    def test_qml_has_no_risk_class_names_colors_or_profile_keys(self):
        with open(COMPONENT, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(re.findall(r"\b(?:green|yellow|red)\b", source, re.I), [], "klasy ryzyka tylko z modelu usługi")
        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [])
        self.assertNotIn("pixelSize", source)
        for key in ("network", "docker", "domains", "accepted_red", "packs", "per-card", "ports", "apt", "setup"):
            self.assertNotIn(key, source, f"klucz profilu {key!r} jest tylko w usłudze")
        for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S):
            self.assertIn("textFormat: Text.PlainText", body)


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class EnvironmentSectionTest(ProbeCase):
    def probe_card(self, scenario, model, **kwargs):
        return self.probe(FIND_PY + ALL_PY + scenario + '\nout["calls"] = calls()\n',
                          data=plan_card(self.data, model, **kwargs))

    def test_project_scope_manifest_preview_and_explicit_version_consent(self):
        # Model liczy rzeczywista usługa na sztucznym projekcie, jak w testach IPC.
        from daemon.tests import test_box_exposure as fixture
        case = fixture.ServiceTests("test_consent_model_explains_project_scope_and_k1_and_does_not_scan_packs")
        case.setUp()
        try:
            from tide_daemon.cardflow import envconsent
            model = envconsent.model(case.engine, "K-38", fixture.fold(case.journal.read("K-38")))
        finally:
            case.doCleanups()
        out = self.probe_card("""
open_card()
out['scope'] = text('envProjectScope')
out['manifest'] = text('envProjectManifest')
out['initiallyAccepted'] = js(ALL + "(page.cardPanel, 'envProjectConsent')[0].checked")
out['initiallyEnabled'] = js(ALL + "(page.cardPanel, 'envApprove')[0].enabled")
press('envProjectPreview')
wait(150)
js(ALL + "(page.cardPanel, 'cardEnvironment')[0].exposureAccepted = true")
press('envApprove')
""", model)
        self.assertIn("root przez wspólny Docker", out["scope"][0])
        self.assertIn("per-card", out["scope"][0])
        self.assertIn(" → ", out["manifest"][0])
        self.assertFalse(out["initiallyAccepted"])
        self.assertFalse(out["initiallyEnabled"])
        first, second = calls_of(out, "ApproveCardEnvironment")
        self.assertIn("exposure-action: preview", first[2])
        self.assertIn("exposure-accepted: false", first[2])
        self.assertIn("exposure-action: approve", second[2])
        self.assertIn("exposure-accepted: true", second[2])
        self.assertIn(model["exposure"]["expected"], second[2])

    def test_items_show_risk_from_the_service_and_red_ones_start_unchecked(self):
        model = environment()
        out = self.probe_card("""
open_card()
out["rows"] = rows()
out["state"] = text("envState")
out["buttons"] = [text(n) and 1 for n in ("envApprove", "envOnlyPlan", "envEdit")]
out["labels"] = [js(ALL + "(page.cardPanel, '" + n + "')[0].text") for n in ("envApprove", "envOnlyPlan", "envEdit")]
out["plainApprove"] = obj("cardPlanApprove").property("visible")
""", model)
        shown = {r["id"]: r for r in out["rows"]}
        by_id = {i["id"]: i for i in model["items"]}
        self.assertEqual(list(shown), [i["id"] for i in model["items"]], "kolejność z usługi")
        for ident, row in shown.items():
            self.assertIn(by_id[ident]["riskLabel"], row["risk"], ident)
            self.assertEqual(row["switch"], by_id[ident]["needsConsent"] or "toggle" in by_id[ident], ident)
        self.assertEqual({r["checked"] for r in out["rows"] if by_id[r["id"]]["needsConsent"]}, {False})
        self.assertEqual(shown["github.com"]["checked"], False)
        self.assertEqual(shown["api.example.org"]["checked"], True)
        dots = {r["id"]: r["dot"] for r in out["rows"]}
        self.assertEqual(len({dots["github.com"], dots["api.example.org"], dots["ports"]}), 3, "trzy różne kolory ryzyka")
        self.assertEqual(out["state"], ["Pudełko: do zbudowania"])
        self.assertEqual(out["labels"], ["Zatwierdź plan i środowisko", "Tylko plan", "Zmień"])
        self.assertFalse(out["plainApprove"], "zwykłe zatwierdzenie planu nie omija pakietu zgód")

    def test_docker_item_shows_the_k1_risk_description_from_the_proposal(self):
        out = self.probe_card("open_card()\nout['rows'] = rows()", environment())
        docker = next(r for r in out["rows"] if r["id"] == "docker")
        self.assertIn("Ryzyko K1", docker["note"])
        self.assertIn("K3 aktywne", docker["note"])

    def test_approve_sends_the_yaml_and_consent_only_after_explicit_check(self):
        model = environment()
        out = self.probe_card("""
open_card()
press("envApprove")
flip("github.com")
out["after"] = rows()
press("envApprove")
""", model)
        first, second = [c for c in out["calls"] if c[0] == "ApproveCardEnvironment"]
        self.assertEqual(first[:2], ["ApproveCardEnvironment", "K-6"])
        self.assertNotIn(model["acceptKey"], first[2], "bez zaznaczenia nie ma zgody")
        self.assertEqual(first[3], False)
        self.assertEqual(second[2], first[2] + 'accepted_red:\n  - "github.com"\n')
        self.assertTrue(next(r for r in out["after"] if r["id"] == "github.com")["checked"])

    def test_only_plan_sends_the_flag_and_per_card_switch_adds_the_service_line(self):
        model = environment()
        toggle = next(i for i in model["items"] if i["id"] == "box:per-card")["toggle"]
        out = self.probe_card("""
open_card()
flip("box:per-card")
press("envOnlyPlan")
""", model)
        [call] = [c for c in out["calls"] if c[0] == "ApproveCardEnvironment"]
        self.assertEqual(call[3], True)
        self.assertTrue(call[2].endswith(toggle["on"] + "\n"), call[2])

    def test_edit_sends_the_corrected_yaml_and_shows_the_service_refusal(self):
        model = environment()
        out = self.probe_card("""
open_card()
replies.append({"ok": False, "id": "K-6", "error": "invalid-environment", "message": "magic: nieznany klucz",
                "errors": ["magic: nieznany klucz"], "items": []})
press("envEdit")
out["editor"] = js(ALL + "(page.cardPanel, 'envEditor').length")
js(ALL + "(page.cardPanel, 'envEditor')[0].text = 'tide-box: 1\\\\nmagic: true\\\\n'")
press("envApprove")
wait(150)
out["refusal"] = text("envError")
out["busy"] = js("page.cardPanel.busy")
""", model)
        [call] = [c for c in out["calls"] if c[0] == "ApproveCardEnvironment"]
        self.assertEqual(out["editor"], 1)
        self.assertEqual(call[2], "tide-box: 1\nmagic: true\n")
        self.assertIn("magic: nieznany klucz", " ".join(out["refusal"]))
        self.assertFalse(out["busy"])

    def test_rebuild_state_shows_the_profile_difference(self):
        model = environment(prepare=clean_snapshot_then_change)
        self.assertEqual(model["state"], "rebuild")
        out = self.probe_card("open_card()\nout['state'] = text('envState')\nout['diff'] = text('envDiff')", model)
        self.assertEqual(out["state"], ["Pudełko: do przebudowy"])
        self.assertIn("dodane: image:postgres:17", out["diff"][0])
        self.assertIn("usunięte: image:testcontainers/ryuk:0.11.0", out["diff"][0])

    def test_only_plan_state_shows_the_wait_marker_and_only_the_environment_button(self):
        model = environment(wait="environment")
        out = self.probe_card("""
open_card()
out["wait"] = text("envWait")
out["approve"] = text("envApprove") and js(ALL + "(page.cardPanel, 'envApprove')[0].text")
out["onlyPlan"] = js(ALL + "(page.cardPanel, 'envOnlyPlan').length")
""", model, status="approved", wait="environment")
        self.assertEqual(out["wait"], ["Karta: czeka na środowisko"])
        self.assertEqual(out["approve"], "Zatwierdź środowisko")
        self.assertEqual(out["onlyPlan"], 0)

    def test_project_without_profile_and_pattern_shows_one_question_as_a_plan_item(self):
        model = environment(project=None, box={"tide-box": 1})
        out = self.probe_card("open_card()\nout['question'] = text('envQuestion')\nout['rows'] = rows()", model)
        self.assertEqual(len(out["question"]), 1)
        self.assertIn("Czego potrzebuje środowisko?", out["question"][0])
        self.assertEqual(model["items"][0]["group"], "per-card")


if __name__ == "__main__":
    unittest.main()
