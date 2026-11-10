"""Prognoza „ruszy od razu / poczeka” przy zgrupowanej zgodzie (K-97, PT-340): decision.js i okna Tablicy.

Migawka kart w testach pochodzi z prawdziwego kodu usługi (`fold` → `derived`, tylko odczyt): trwające polecenie
poza piaskownicą to zdarzenie `outside.started`, zgoda w kolejce — decyzja „uruchom” bez startu polecenia. Napisy,
które rozpoznaje biblioteka (`kindLabel` wpisu `flow.running[]`, `statusLabel` ticketu), nie są tu wpisane na sztywno:
test czyta je z wyniku usługi i porównuje ze stałymi biblioteki, więc zmiana napisu w usłudze łamie test, a nie po
cichu licznik. Limity lustrzane w bibliotece są porównane ze stałymi silnika (`OUTSIDE_PER_PROJECT`, `OUTSIDE_TOTAL`).

- biblioteka: `outsideLoad` liczy trwające i czekające w projekcie i łącznie, `outsideForecast` — ile ruszy od razu
  (przykłady z karty: 4 trwające i 7 zgód → 2 i 5; 6 łącznie w innych projektach → 1; zgody w kolejce zmniejszają
  liczbę), `null` dla migawki nierozpoznanej,
- okno decyzji i pełna karta: napis prognozy w pozycji grupy, bez `outsideLoad` sama liczba ticketów, te same
  odpowiedzi co bez prognozy.
"""
import json
import os
import shutil
import unittest

from . import REPO
from .decisionfix import (CARD, OUTSIDE_COMMAND, decisions, derived, fold, full_card, journal_outside, outside_step,
                          scenario_f)
from tide_daemon.cardflow.engine import OUTSIDE_PER_PROJECT, OUTSIDE_TOTAL  # noqa: E402 (ścieżkę dodaje decisionfix)
from .test_consent_groups import DECISION_JS, STEPS
from .test_decision_dialog import STATE_PY
from .test_view import ProbeCase

OTHER_PROJECT = "inny-projekt"


def service_journal(card, tickets, running=0, queued=0):
    """Dziennik karty `card` liczony przez usługę: plan zatwierdzony, każdy ticket w `tickets` dostał odmowę polecenia
    poza piaskownicą; pierwszych `running` ma decyzję „uruchom” i trwające polecenie, kolejnych `queued` — decyzję
    „uruchom” bez startu (czeka w kolejce)."""
    events = journal_outside([(t, "make test-changed BASE=1") for t in tickets])
    rows = [{"id": t, "workId": t, "title": f"Ticket {t}", "executor": "Sonnet", "reviewer": "Opus", "blockers": []}
            for t in tickets]
    plan = [{"type": "plan.proposed", "planner": "Opus", "rows": [{k: v for k, v in r.items() if k != "id"} for r in rows]},
            {"type": "plan.reviewed", "reviewer": "Sonnet", "verdict": "APPROVE", "findings": [], "corrected": False},
            {"type": "plan.approved", "map": {r["id"]: r["id"] for r in rows}, "rows": rows}]
    tail = []
    for index, ticket in enumerate(tickets):
        if index < running + queued:
            tail.append({"type": "step.decision", "step": outside_step(ticket), "ticket": ticket, "choice": "run"})
        if index < running:
            tail.append({"type": "outside.started", "step": outside_step(ticket), "ticket": ticket,
                         "command": "make test-changed BASE=1"})
    out = []
    for event in events[:1] + plan + events[1:] + tail:
        event = dict(event, v=1, seq=len(out) + 1, card=card)
        event.setdefault("ts", "2026-11-10T12:00:00+01:00")
        out.append(event)
    return out


def service_card(card_id, project, tickets, running=0, queued=0):
    """Karta migawki: `derived` policzone przez usługę z dziennika `service_journal`."""
    computed = derived(fold(service_journal(card_id, tickets, running, queued)), {"column": "todo"})
    return {"id": card_id, "project": project, "boardColumn": "progress", "derived": json.loads(json.dumps(computed))}


def tickets_of(prefix, count):
    return [f"{prefix}-{n}" for n in range(1, count + 1)]


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class OutsideForecastLogicTest(unittest.TestCase):
    """decision.js w QJSEngine na migawce z prawdziwego kodu usługi."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        with open(DECISION_JS, encoding="utf-8") as fh:
            result = cls.engine.evaluate(fh.read().replace(".pragma library", ""))
        assert not result.isError(), result.toString()

    def js(self, expression, **values):
        for name, value in values.items():
            self.engine.globalObject().setProperty(name, self.engine.toScriptValue(json.dumps(value, ensure_ascii=False)))
            self.engine.evaluate(f"{name} = JSON.parse({name})")
        value = self.engine.evaluate(f"JSON.stringify({expression})")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def load(self, cards, project="tide"):
        return self.js("outsideLoad(C, P)", C=cards, P=project)

    def test_limits_mirror_the_service_constants(self):
        self.assertEqual(self.js("OUTSIDE_PER_PROJECT"), OUTSIDE_PER_PROJECT)
        self.assertEqual(self.js("OUTSIDE_TOTAL"), OUTSIDE_TOTAL)

    def test_library_recognizes_service_labels_of_running_and_queued(self):
        card = service_card("K-1", "tide", tickets_of("PT", 3), running=1, queued=1)
        flow, tickets = card["derived"]["flow"], card["derived"]["tickets"]
        self.assertEqual([r["kindLabel"] for r in flow["running"]], [self.js("OUTSIDE_RUNNING_LABEL")],
                         "napis trwającego polecenia z usługi = stała biblioteki")
        queued = [t["statusLabel"] for t in tickets if t["statusLabel"].startswith(self.js("OUTSIDE_QUEUED_PREFIX"))]
        self.assertEqual(len(queued), 1, [t["statusLabel"] for t in tickets])
        self.assertEqual(self.load([card]), {"projectRunning": 1, "totalRunning": 1, "projectQueued": 1,
                                             "totalQueued": 1})

    def test_ticket_that_has_not_started_is_not_a_queued_consent(self):
        # Ticket bez kroku też ma „w kolejce” w usłudze, ale bez polecenia: nie liczy się jako zgoda w kolejce.
        events = service_journal("K-1", ["PT-1"])
        events = [e for e in events if e["type"] not in ("step.started", "step.finished")]
        card = {"id": "K-1", "project": "tide", "derived": json.loads(json.dumps(derived(fold(events), {"column": "todo"})))}
        self.assertIn("w kolejce", [t["statusLabel"] for t in card["derived"]["tickets"]])
        self.assertEqual(self.load([card])["totalQueued"], 0)

    def test_counts_by_project_and_in_total(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 3), running=2, queued=1),
                 service_card("K-2", OTHER_PROJECT, tickets_of("B", 4), running=3, queued=1),
                 {"id": "K-3", "project": "tide", "derived": None},
                 {"id": "K-4", "project": "tide"}]
        self.assertEqual(self.load(cards), {"projectRunning": 2, "totalRunning": 5, "projectQueued": 1, "totalQueued": 2})
        self.assertEqual(self.load(cards, OTHER_PROJECT),
                         {"projectRunning": 3, "totalRunning": 5, "projectQueued": 1, "totalQueued": 2})
        self.assertEqual(self.load(cards, "")["projectRunning"], 0, "karta bez projektu nie liczy projektu")

    def test_forecast_examples_from_the_card(self):
        four = [service_card("K-1", "tide", tickets_of("A", 4), running=4)]
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load(four)), {"now": 2, "wait": 5})
        elsewhere = [service_card("K-2", OTHER_PROJECT, tickets_of("B", 6), running=6)]
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load(elsewhere)), {"now": 1, "wait": 6},
                         "limit łączny wygrywa z limitem projektu")
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load([])), {"now": 6, "wait": 1}, "wolno: limit projektu")
        self.assertEqual(self.js("outsideForecast(L, 3)", L=self.load([])), {"now": 3, "wait": 0})

    def test_queued_consents_reduce_the_number_that_starts_at_once(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 4), running=2, queued=2)]
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load(cards)), {"now": 2, "wait": 5})
        no_queue = [service_card("K-1", "tide", tickets_of("A", 4), running=2)]
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load(no_queue)), {"now": 4, "wait": 3})
        full = [service_card("K-1", "tide", tickets_of("A", 8), running=6, queued=2)]
        self.assertEqual(self.js("outsideForecast(L, 7)", L=self.load(full)), {"now": 0, "wait": 7}, "nie schodzi poniżej zera")

    def test_unrecognized_snapshot_gives_null_and_no_forecast(self):
        self.assertIsNone(self.js("outsideLoad(undefined, 'tide')"))
        self.assertIsNone(self.js("outsideLoad(null, 'tide')"))
        self.assertIsNone(self.js("outsideLoad({}, 'tide')"))
        for broken in ({"running": [{"id": "x"}]}, {"running": [{"kindLabel": ""}]}, {"running": "nic"}):
            with self.subTest(broken=broken):
                card = {"id": "K-1", "project": "tide", "derived": {"flow": broken}}
                self.assertIsNone(self.load([card]))
        self.assertIsNone(self.load([{"id": "K-1", "project": "tide", "derived": {"flow": {}, "tickets": [{"id": "PT-1"}]}}]))
        self.assertIsNone(self.js("outsideForecast(null, 7)"))
        self.assertEqual(self.js("forecastText(null, 7)"), "")

    def test_group_forecast_text_is_an_estimate_without_order(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 4), running=4)]
        items = decisions(journal_outside())
        [group] = self.js("consentItems(D, [], consentStart(), L)", D=items, L=self.load(cards))
        self.assertEqual(group["forecast"],
                         "Ruszy od razu: 2, poczeka w kolejce: 5 (w projekcie biegnie teraz 4 z 6, łącznie 4 z 7) — "
                         "według stanu sprzed chwili. To szacunek, nie obietnica kolejności startu.")
        [bare] = self.js("consentItems(D, [], consentStart())", D=items)
        self.assertEqual(bare["forecast"], "")
        [absent] = self.js("consentItems(D, [], consentStart(), null)", D=items)
        self.assertEqual(absent["forecast"], "")
        # Pojedyncza decyzja i znak przyjęcia nie dostają prognozy.
        one = decisions(journal_outside([("PT-1", OUTSIDE_COMMAND)]))
        self.assertEqual(self.js("consentItems(D, [], consentStart(), L)", D=one, L=self.load(cards)), one)
        ids = [d["id"] for d in items]
        sent = self.js("consentSent(consentStart(), I)", I=ids)
        [receipt] = self.js("consentItems(D, [], S, L)", D=items, S=sent, L=self.load(cards))
        self.assertTrue(receipt["receipt"])
        self.assertEqual(receipt["forecast"], "")


FORECAST_PY = r'''
def forecast(owner="page.decisionDialog"):
    """Napis prognozy w pozycji grupy (widoczność i tekst) oraz liczba widocznych napisów o liczbie ticketów."""
    return json.loads(js("(function () { const d = " + owner + "; const all = " + ALL + "; "
        "const f = all(d, 'decisionGroupForecast')[0] || null; "
        "return JSON.stringify({ found: !!f, visible: f ? f.visible : false, text: f ? f.text : '' }) })()"))
'''


def board_with(data, *cards):
    """Kopia danych podglądu z dołożonymi kartami (szablon: K-8), z projektem K-8 jako „tide”."""
    data = json.loads(json.dumps(data))
    template = next(c for c in data["cards"] if c["id"] == CARD)
    template["project"] = "tide"
    for card in cards:
        extra = json.loads(json.dumps(template))
        extra.update(id=card["id"], project=card["project"], boardColumn="progress", column="todo",
                     derived=card["derived"], title=card["id"])
        data["cards"].append(extra)
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class OutsideForecastDialogTest(ProbeCase):
    def test_dialog_shows_forecast_and_sends_the_same_answers(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 4), running=4)]
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
click(named("decisionGroupToggle"))
out["members"] = js("(" + ALL + ")(page.decisionDialog, 'decisionGroupMember').length")
js("page.decisionDialog.form.pick('run')")
js("page.decisionDialog.form.choose()")
wait(50)
out["sent"] = calls()
""", board_with(scenario_f(self.data, decisions(journal_outside())), *cards))
        self.assertTrue(out["forecast"]["visible"])
        self.assertEqual(out["forecast"]["text"],
                         "Ruszy od razu: 2, poczeka w kolejce: 5 (w projekcie biegnie teraz 4 z 6, łącznie 4 z 7) — "
                         "według stanu sprzed chwili. To szacunek, nie obietnica kolejności startu.")
        self.assertEqual(out["members"], 7, "lista ticketów bez zmian")
        sent = out["sent"]
        self.assertEqual([c[:2] for c in sent], [["ResolveCardStep", CARD]] * 7)
        self.assertEqual([json.loads(c[2]) for c in sent], [{"choice": "run", "step": s} for s in STEPS],
                         "te same odpowiedzi co bez prognozy (#7)")

    def test_total_limit_wins_in_the_dialog(self):
        cards = [service_card("K-2", OTHER_PROJECT, tickets_of("B", 6), running=6)]
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
""", board_with(scenario_f(self.data, decisions(journal_outside())), *cards))
        self.assertTrue(out["forecast"]["text"].startswith("Ruszy od razu: 1, poczeka w kolejce: 6 "), out["forecast"])
        self.assertIn("łącznie 6 z 7", out["forecast"]["text"])

    def test_queued_consents_in_the_dialog(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 4), running=2, queued=2)]
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
""", board_with(scenario_f(self.data, decisions(journal_outside())), *cards))
        self.assertTrue(out["forecast"]["text"].startswith("Ruszy od razu: 2, poczeka w kolejce: 5 "), out["forecast"])

    def test_without_load_group_shows_only_the_ticket_count(self):
        # Migawka nierozpoznana: wpis `running` bez `kindLabel` — prognoza się chowa, bez błędów QML (ProbeCase je łapie).
        broken = {"id": "K-1", "project": "tide", "derived": {"flow": {"running": [{"id": "x"}]}, "phases": []}}
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
out["state"] = state()
""", board_with(scenario_f(self.data, decisions(journal_outside())), broken))
        self.assertFalse(out["forecast"]["visible"], out["forecast"])
        self.assertEqual(out["forecast"]["text"], "")
        self.assertEqual(out["state"]["who"], "K-8 · 7 ticketów")
        self.assertEqual(out["state"]["happened"], OUTSIDE_COMMAND + " · 7 ticketów")

    def test_full_card_shows_the_same_forecast(self):
        cards = [service_card("K-1", "tide", tickets_of("A", 4), running=4)]
        out = self.probe(STATE_PY + FORECAST_PY + """
js("page.openFull('K-8')")
wait(200)
out["forecast"] = forecast("page.cardFullView")
js("page.cardFullView.form.pick('deny')")
js("page.cardFullView.form.choose()")
wait(50)
out["sent"] = calls()
""", board_with(full_card(self.data, journal_outside()), *cards))
        self.assertTrue(out["forecast"]["visible"])
        self.assertTrue(out["forecast"]["text"].startswith("Ruszy od razu: 2, poczeka w kolejce: 5 "), out["forecast"])
        self.assertEqual([json.loads(c[2]) for c in out["sent"]], [{"choice": "deny", "step": s} for s in STEPS])


class SessionsUntouchedTest(unittest.TestCase):
    def test_new_property_defaults_to_null(self):
        for name in ("DecisionDialog.qml", "CardFullView.qml"):
            with open(os.path.join(REPO, "apps", "common", "qml", "card", name), encoding="utf-8") as fh:
                self.assertIn("property var outsideLoad: null", fh.read(), name)

    def test_sessions_app_has_no_outside_load(self):
        sessions = os.path.join(REPO, "apps", "sessions")
        for root, _dirs, files in os.walk(os.path.join(sessions, "qml")):
            for name in files:
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn("outsideLoad", fh.read(), os.path.join(root, name))


if __name__ == "__main__":
    unittest.main()
