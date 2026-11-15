"""Prognoza „ruszy od razu / poczeka” przy zgrupowanej zgodzie (K-97, PT-340; K-39 #4, PT-374): decision.js i okna Tablicy.

Stan kolejki to pole `queue` migawki, liczone prawdziwym kodem usługi: dzienniki kart przechodzą przez `fold`, a
`queueview.build` (z klasyfikacją `heavyqueue.classify` na kontrakcie testów projektu) daje `queue`; klasę decyzji
(`technical.queue`) ustawia `queueview.mark_decisions`. Test nie wpisuje napisów usługi ani limitów na sztywno do
porównania z lustrem — biblioteka nie ma lustra (okno liczy tylko arytmetykę na liczbach z `queue`).

- biblioteka: `outsideLoad` daje kontekst z `queue`, `outsideForecast` — ile ruszy od razu. Przykład z karty: limit
  ciężkich 2, jedno trwające ciężkie polecenie i jedna próba testów po scaleniu, grupa 3 zgód → 0 i 3. Lekkie polecenia
  liczą limity projektu i łączny (trwające razem z ciężkimi), zgody w kolejce zmniejszają liczbę,
- bez pola `queue` (stara usługa) albo bez `technical.queue` decyzji prognozy nie ma, okno pokazuje samą liczbę
  ticketów jak przed PT-340,
- okno decyzji i pełna karta: napis prognozy w pozycji grupy, te same odpowiedzi co bez prognozy.
"""
import json
from datetime import datetime
import os
import shutil
import unittest

from . import REPO
from .decisionfix import (CARD, OUTSIDE_COMMAND, OUTSIDE_TICKETS, build_decisions, decisions, fold, full_card,
                          journal_outside, outside_step, scenario_f)
from tide_daemon.cardflow import heavyqueue, mergecheck, queueview  # noqa: E402 (ścieżkę dodaje decisionfix)
from .test_consent_groups import DECISION_JS, STEPS
from .test_decision_dialog import STATE_PY
from .test_view import ProbeCase

OTHER_PROJECT = "inny-projekt"
LIGHT_COMMAND = "make build-docs"
CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
SHA = "c" * 40
NOW = datetime.fromisoformat("2026-11-10T13:00:00+01:00").timestamp()


def heavy_of(card, name, step, command):
    """Klasyfikacja usługi na kontrakcie testów projektu (jak `FlowEngine._outside_heavy_of`)."""
    return heavyqueue.classify(command, (), CONTRACT, ()).heavy


def service_journal(project, tickets, command=OUTSIDE_COMMAND, running=0, queued=0):
    """Dziennik karty projektu `project` z dziennika usługi: każdy ticket z `tickets` dostał odmowę polecenia poza
    piaskownicą; pierwszych `running` ma decyzję „uruchom” i trwające polecenie, kolejnych `queued` — decyzję
    „uruchom” bez startu (czeka w kolejce)."""
    events = journal_outside([(t, command) for t in tickets])
    events[0] = {**events[0], "project": project}
    for index, ticket in enumerate(tickets):
        if index < running + queued:
            events.append({"type": "step.decision", "step": outside_step(ticket), "ticket": ticket, "choice": "run"})
        if index < running:
            events.append({"type": "outside.started", "step": outside_step(ticket), "ticket": ticket,
                           "command": command, "ts": "2026-11-10T12:30:00+01:00"})
    return number(events)


def merge_check_running(events, ticket):
    """Dopisuje trwającą próbę testów po scaleniu `ticket` (zdarzenia usługi: próba, pseudokrok, zgoda, start)."""
    check = f"{ticket}:1"
    step = mergecheck.step_name(check)
    command = "make test-changed BASE=" + SHA
    return number([*events,
                   {"type": mergecheck.ARMED},
                   {"type": mergecheck.REQUESTED, "ticket": ticket, "check": check, "command": command,
                    "after": SHA, "start": SHA, "before": SHA},
                   {"type": mergecheck.STEP, "step": step, "ticket": ticket, "kind": mergecheck.KIND, "check": check,
                    "command": command, "after": SHA, "worktree": "/wt/card", "stepDir": f"/wt/card.{step}.step"},
                   {"type": "step.decision", "step": step, "ticket": ticket, "choice": "run"},
                   {"type": "outside.started", "step": step, "ticket": ticket, "command": command,
                    "ts": "2026-11-10T12:31:00+01:00"}])


def number(events):
    out = []
    for event in events:
        event = {"v": 1, **event, "seq": len(out) + 1}
        event.setdefault("ts", "2026-11-10T12:00:00+01:00")
        event.setdefault("card", CARD)
        out.append(event)
    return out


def tickets_of(first, count):
    return [f"PT-{first + n}" for n in range(count)]


def service_queue(journals, heavy_limit=2):
    """Pole `queue` migawki z dzienników kart (`{id karty: zdarzenia}`) — kod usługi, bez katalogu dzienników."""
    states = {card: fold(events) for card, events in journals.items()}
    # Stały zegar tuż po znacznikach dziennika: zgody starsze niż doba nie czekają w kolejce (`approval_live`).
    return json.loads(json.dumps(queueview.build(states, {"heavyLimit": heavy_limit}, heavy_of=heavy_of,
                                                 outside_limits=(6, 7), now=NOW)))


def service_decisions(events):
    """Decyzje karty K-8 z `technical.queue` ustawionym przez usługę."""
    st = fold(events)
    items = build_decisions(st)
    queueview.mark_decisions(CARD, st, items, heavy_of)
    return items


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class OutsideForecastLogicTest(unittest.TestCase):
    """decision.js w QJSEngine na `queue` i decyzjach z prawdziwego kodu usługi."""

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

    def forecast(self, queue, kind, n, project="tide"):
        return self.js("outsideForecast(outsideLoad(Q, P), K, N)", Q=queue, P=project, K=kind, N=n)

    def test_card_example_heavy_limit_with_running_command_and_merge_check(self):
        events = merge_check_running(service_journal("tide", tickets_of(10, 1), running=1), "PT-90")
        queue = service_queue({"K-1": events}, heavy_limit=2)
        self.assertEqual([e["kind"] for e in queue["heavy"]["running"]], ["outside", "merge-check"])
        self.assertEqual(self.forecast(queue, "heavy", 3), {"now": 0, "wait": 3})
        items = service_decisions(journal_outside([(t, OUTSIDE_COMMAND) for t in OUTSIDE_TICKETS[:3]]))
        self.assertEqual({d["technical"]["queue"] for d in items}, {"heavy"})
        [group] = self.js("consentItems(D, [], consentStart(), L)", D=items, L=self.js("outsideLoad(Q, 'tide')", Q=queue))
        self.assertEqual(group["forecast"],
                         "Ruszy od razu: 0, poczeka w kolejce: 3 (ciężkich poleceń biegnie teraz 2 z 2) — według "
                         "stanu sprzed chwili. To szacunek, nie obietnica kolejności startu.")

    def test_heavy_forecast_uses_the_service_limit_and_counts_waiting(self):
        running = merge_check_running(service_journal("tide", tickets_of(10, 1), running=1), "PT-90")
        self.assertEqual(self.forecast(service_queue({"K-1": running}, 4), "heavy", 3), {"now": 2, "wait": 1})
        waiting = service_journal(OTHER_PROJECT, tickets_of(20, 2), queued=2)
        queue = service_queue({"K-1": running, "K-2": waiting}, 4)
        self.assertEqual([e["step"] for e in queue["heavy"]["waiting"]], [outside_step(t) for t in tickets_of(20, 2)])
        self.assertEqual(self.forecast(queue, "heavy", 3), {"now": 0, "wait": 3}, "zgody w kolejce zajmują miejsca")
        self.assertEqual(self.forecast(service_queue({}, 6), "heavy", 9), {"now": 6, "wait": 3})

    def test_light_forecast_counts_project_and_total_limits_with_heavy_running(self):
        four = {"K-1": service_journal("tide", tickets_of(10, 4), LIGHT_COMMAND, running=4)}
        self.assertEqual(self.forecast(service_queue(four), "outside", 7), {"now": 2, "wait": 5})
        elsewhere = {"K-2": service_journal(OTHER_PROJECT, tickets_of(20, 6), LIGHT_COMMAND, running=6)}
        self.assertEqual(self.forecast(service_queue(elsewhere), "outside", 7), {"now": 1, "wait": 6},
                         "limit łączny wygrywa z limitem projektu")
        self.assertEqual(self.forecast(service_queue({}), "outside", 7), {"now": 6, "wait": 1}, "wolno: limit projektu")
        self.assertEqual(self.forecast(service_queue({}), "outside", 3), {"now": 3, "wait": 0})
        queued = {"K-1": service_journal("tide", tickets_of(10, 4), LIGHT_COMMAND, running=2, queued=2)}
        self.assertEqual(self.forecast(service_queue(queued), "outside", 7), {"now": 2, "wait": 5},
                         "zgody w kolejce zmniejszają liczbę")
        heavy = {"K-1": service_journal("tide", tickets_of(10, 1), OUTSIDE_COMMAND, running=1)}
        self.assertEqual(self.forecast(service_queue(heavy), "outside", 7), {"now": 5, "wait": 2},
                         "trwające ciężkie liczą się do limitów lekkich")
        full = {"K-1": service_journal("tide", tickets_of(10, 8), LIGHT_COMMAND, running=6, queued=2)}
        self.assertEqual(self.forecast(service_queue(full), "outside", 7), {"now": 0, "wait": 7},
                         "nie schodzi poniżej zera")

    def test_decision_class_comes_from_the_service(self):
        light = service_decisions(journal_outside([(t, LIGHT_COMMAND) for t in OUTSIDE_TICKETS[:2]]))
        self.assertEqual({d["technical"]["queue"] for d in light}, {"outside"})
        # Brak klasy (decyzja bez oznaczenia usługi) — grupa bez prognozy, także przy poprawnym `queue`.
        queue = service_queue({})
        items = decisions(journal_outside())
        [group] = self.js("consentItems(D, [], consentStart(), L)", D=items, L=self.js("outsideLoad(Q, 'tide')", Q=queue))
        self.assertEqual(group["forecast"], "")
        mixed = [dict(d, technical=dict(d["technical"], queue="heavy" if i else "outside"))
                 for i, d in enumerate(decisions(journal_outside()))]
        [mixed_group] = self.js("consentItems(D, [], consentStart(), L)", D=mixed,
                                L=self.js("outsideLoad(Q, 'tide')", Q=queue))
        self.assertEqual(mixed_group["forecast"], "", "różne klasy w jednej grupie — bez prognozy")

    def test_without_queue_field_there_is_no_forecast(self):
        self.assertIsNone(self.js("outsideLoad(undefined, 'tide')"))
        self.assertIsNone(self.js("outsideLoad(null, 'tide')"))
        self.assertIsNone(self.js("outsideLoad({}, 'tide')"))
        self.assertIsNone(self.js("outsideLoad({heavy: {}}, 'tide')"))
        self.assertIsNone(self.js("outsideForecast(null, 'heavy', 7)"))
        self.assertEqual(self.js("forecastText(null, 'heavy', 7)"), "")
        broken = {"heavy": {"limit": 2, "running": "nic"}, "outside": {}}
        self.assertIsNone(self.js("outsideForecast(outsideLoad(Q, 'tide'), 'heavy', 3)", Q=broken))
        items = service_decisions(journal_outside())
        [bare] = self.js("consentItems(D, [], consentStart())", D=items)
        self.assertEqual(bare["forecast"], "")
        [absent] = self.js("consentItems(D, [], consentStart(), null)", D=items)
        self.assertEqual(absent["forecast"], "")

    def test_single_decision_and_receipt_have_no_forecast(self):
        queue = service_queue({})
        load = self.js("outsideLoad(Q, 'tide')", Q=queue)
        one = service_decisions(journal_outside([("PT-1", OUTSIDE_COMMAND)]))
        self.assertEqual(self.js("consentItems(D, [], consentStart(), L)", D=one, L=load), one)
        items = service_decisions(journal_outside())
        sent = self.js("consentSent(consentStart(), I)", I=[d["id"] for d in items])
        [receipt] = self.js("consentItems(D, [], S, L)", D=items, S=sent, L=load)
        self.assertTrue(receipt["receipt"])
        self.assertEqual(receipt["forecast"], "")

    def test_merge_check_decision_has_its_own_kind_label(self):
        from .decisionfix import journal_merge_check
        problem = decisions(journal_merge_check())[0]
        self.assertEqual(problem["kind"], "merge-check")
        self.assertEqual(self.js("kindLabel(D)", D=problem), "Testy po scaleniu")
        self.assertEqual(self.js("KIND_LABELS['merge-check']"), "Testy po scaleniu")

    def test_merge_check_consent_is_always_heavy(self):
        st = fold(number([{"type": "card.started", "entry": "plan", "executor": "Sonnet", "program": "claude",
                           "provider": "anthropic"},
                          {"type": mergecheck.ARMED},
                          {"type": mergecheck.REQUESTED, "ticket": "PT-90", "check": "PT-90:1", "after": SHA,
                           "start": SHA, "before": SHA, "command": "make lint"},
                          {"type": mergecheck.STEP, "step": mergecheck.step_name("PT-90:1"), "ticket": "PT-90",
                           "kind": mergecheck.KIND, "check": "PT-90:1", "command": "make lint", "after": SHA,
                           "worktree": "/wt/card", "stepDir": "/wt/card.step"}]))
        items = [d for d in build_decisions(st) if d["kind"] == "permission"]
        self.assertTrue(items, "pytanie o zgodę na próbę po scaleniu")
        queueview.mark_decisions(CARD, st, items, lambda *args: False)
        self.assertEqual({d["technical"]["queue"] for d in items}, {"heavy"}, "próba jest ciężka bez klasyfikacji")


FORECAST_PY = r'''
def forecast(owner="page.decisionDialog"):
    """Napis prognozy w pozycji grupy (widoczność i tekst) oraz liczba widocznych napisów o liczbie ticketów."""
    return json.loads(js("(function () { const d = " + owner + "; const all = " + ALL + "; "
        "const f = all(d, 'decisionGroupForecast')[0] || null; "
        "return JSON.stringify({ found: !!f, visible: f ? f.visible : false, text: f ? f.text : '' }) })()"))
'''


def board_with(data, queue=None):
    """Kopia danych podglądu z projektem K-8 „tide” i (opcjonalnie) polem `queue` — migawka nowej usługi;
    bez `queue` taka jak ze starej usługi."""
    data = json.loads(json.dumps(data))
    next(c for c in data["cards"] if c["id"] == CARD)["project"] = "tide"
    if queue is not None:
        data["queue"] = queue
    return data


def marked_full_card(data, events):
    """`full_card` z decyzjami oznaczonymi klasą kolejki (`technical.queue`) przez usługę."""
    data = full_card(data, events)
    st = fold(events)
    for card in data["cards"]:
        if card["id"] == CARD:
            queueview.mark_decisions(CARD, st, card["derived"]["flow"]["decisions"], heavy_of)
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class OutsideForecastDialogTest(ProbeCase):
    def heavy_queue(self):
        events = merge_check_running(service_journal("tide", tickets_of(10, 1), running=1), "PT-90")
        return service_queue({"K-1": events}, heavy_limit=2)

    def test_dialog_shows_the_card_example_and_sends_the_same_answers(self):
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
click(named("decisionGroupToggle"))
out["members"] = js("(" + ALL + ")(page.decisionDialog, 'decisionGroupMember').length")
js("page.decisionDialog.form.pick('run')")
js("page.decisionDialog.form.choose()")
wait(50)
out["sent"] = calls()
""", board_with(scenario_f(self.data, service_decisions(journal_outside())), self.heavy_queue()))
        self.assertTrue(out["forecast"]["visible"])
        self.assertEqual(out["forecast"]["text"],
                         "Ruszy od razu: 0, poczeka w kolejce: 7 (ciężkich poleceń biegnie teraz 2 z 2) — według "
                         "stanu sprzed chwili. To szacunek, nie obietnica kolejności startu.")
        self.assertEqual(out["members"], 7, "lista ticketów bez zmian")
        sent = out["sent"]
        self.assertEqual([c[:2] for c in sent], [["ResolveCardStep", CARD]] * 7)
        self.assertEqual([json.loads(c[2]) for c in sent], [{"choice": "run", "step": s} for s in STEPS],
                         "te same odpowiedzi co bez prognozy (#7)")

    def test_light_group_uses_project_and_total_limits(self):
        elsewhere = service_queue({"K-2": service_journal(OTHER_PROJECT, tickets_of(20, 6), LIGHT_COMMAND, running=6)})
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
""", board_with(scenario_f(self.data, service_decisions(journal_outside([(t, LIGHT_COMMAND) for t in OUTSIDE_TICKETS]))),
                elsewhere))
        self.assertEqual(out["forecast"]["text"],
                         "Ruszy od razu: 1, poczeka w kolejce: 6 (w projekcie biegnie teraz 0 z 6, łącznie 6 z 7) — "
                         "według stanu sprzed chwili. To szacunek, nie obietnica kolejności startu.")

    def test_without_queue_field_group_shows_only_the_ticket_count(self):
        # Stara usługa: migawka bez pola `queue` — okno działa jak przed PT-340 (bez prognozy, bez błędów QML).
        out = self.probe(STATE_PY + FORECAST_PY + """
click(card("K-8"))
out["forecast"] = forecast()
out["state"] = state()
""", board_with(scenario_f(self.data, service_decisions(journal_outside()))))
        self.assertFalse(out["forecast"]["visible"], out["forecast"])
        self.assertEqual(out["forecast"]["text"], "")
        self.assertEqual(out["state"]["who"], "K-8 · 7 ticketów")
        self.assertEqual(out["state"]["happened"], OUTSIDE_COMMAND + " · 7 ticketów")

    def test_full_card_shows_the_same_forecast(self):
        out = self.probe(STATE_PY + FORECAST_PY + """
js("page.openFull('K-8')")
wait(200)
out["forecast"] = forecast("page.cardFullView")
js("page.cardFullView.form.pick('deny')")
js("page.cardFullView.form.choose()")
wait(50)
out["sent"] = calls()
""", board_with(marked_full_card(self.data, journal_outside()), self.heavy_queue()))
        self.assertTrue(out["forecast"]["visible"])
        self.assertTrue(out["forecast"]["text"].startswith("Ruszy od razu: 0, poczeka w kolejce: 7 "), out["forecast"])
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

    def test_library_has_no_mirror_of_service_limits_or_labels(self):
        with open(DECISION_JS, encoding="utf-8") as fh:
            text = fh.read()
        for name in ("OUTSIDE_PER_PROJECT", "OUTSIDE_TOTAL", "OUTSIDE_RUNNING_LABEL", "OUTSIDE_QUEUED_PREFIX"):
            self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
