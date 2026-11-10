"""Zgrupowane zgody na to samo polecenie (K-97, PT-338): wspólny decision.js i okno decyzji Tablicy.

- biblioteka `consentGroups` w QJSEngine na decyzjach policzonych przez usługę z dziennika (decisionfix.journal_outside):
  siedem próśb poza piaskownicą o to samo polecenie → jedna grupa „· 7 ticketów”; inny tekst polecenia (spacje,
  cudzysłowy, koniec długiego polecenia), prośba ryzykowna (inne możliwości) i łańcuch `commands[]` zostają osobno;
  możliwości grupy w kolejności usługi z przekazaną polecaną; żądania grupy z `step` każdej decyzji; znak przyjęcia
  i odmowa usługi przywracająca tylko jedną decyzję,
- sonda okna decyzji: jedna pozycja z licznikiem liczącym grupę raz, nic nie zaznaczone, Enter bez wyboru nic
  nie wysyła, rozwijana lista ticketów i kroków, „Zawsze w projekcie” wysyła 7 × ResolveCardStep z `run-always`
  i od razu pokazuje „Przyjęto · wysyłam 7 odpowiedzi”, odmowa jednego kroku wraca osobno z powodem,
- blok „Twój ruch” pełnej karty: ta sama grupa i te same odpowiedzi.
"""
import json
import os
import shutil
import unittest

from . import REPO
from .decisionfix import (CARD, OUTSIDE_COMMAND, OUTSIDE_TICKETS, a1_journal, decisions, full_card,
                          journal_outside, outside_step, scenario_f)
from .test_decision_dialog import STATE_PY
from .test_view import ProbeCase

DECISION_JS = os.path.join(REPO, "apps", "common", "qml", "card", "decision.js")
STEPS = [outside_step(t) for t in OUTSIDE_TICKETS]


class ConsentGroupsLogicTest(unittest.TestCase):
    """decision.js w QJSEngine na decyzjach z `build_decisions` usługi."""

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

    def groups(self, items):
        return self.js("consentGroups(D)", D=items)

    def test_seven_requests_for_the_same_command_are_one_group(self):
        items = decisions(journal_outside())
        self.assertEqual(len(items), 7)
        self.assertEqual({d["technical"]["command"] for d in items}, {OUTSIDE_COMMAND})
        [group] = self.groups(items)
        self.assertTrue(group["group"])
        self.assertEqual(group["ticket"], "7 ticketów")
        self.assertEqual(group["happened"], OUTSIDE_COMMAND + " · 7 ticketów")
        self.assertEqual(group["kind"], "permission")
        self.assertEqual(self.js("who('K-8', G)", G=group), "K-8 · 7 ticketów")
        self.assertEqual([(m["ticket"], m["step"]) for m in group["members"]], list(zip(OUTSIDE_TICKETS, STEPS)))
        self.assertTrue(all(m["reason"].startswith("Testy ticketu") for m in group["members"]))
        self.assertEqual(group["forecast"], "", "bez obciążenia (Sesje, migawka nierozpoznana) nie ma prognozy (PT-340)")
        self.assertFalse(group["freeText"])
        # Możliwości z usługi w tej samej kolejności, polecona tylko przekazana (bez zmian).
        service = items[0]["options"]
        self.assertEqual([(o["id"], o["title"], o["recommended"]) for o in group["options"]],
                         [(o["id"], o["title"], o["recommended"]) for o in service])
        self.assertEqual([o["id"] for o in group["options"]], ["run-always", "run", "deny"])
        effects = {o["id"]: o["effect"] for o in group["options"]}
        self.assertEqual(effects["run"], "Każdy z 7 kroków dostanie jedno uruchomienie; następne takie polecenie znowu zapyta.")
        self.assertEqual(effects["run-always"], "Polecenie ruszy teraz w 7 krokach, a ta rodzina poleceń w tym projekcie "
                                                "będzie uruchamiana bez pytania.")
        self.assertEqual(effects["deny"], "7 kroków wznowi się bez polecenia.")

    def test_group_requests_are_the_service_requests_of_each_step(self):
        items = decisions(journal_outside())
        requests = self.js("groupRequests(consentGroups(D)[0], 'run-always')", D=items)
        self.assertEqual(requests, [{"id": d["id"], "method": "ResolveCardStep",
                                     "params": {"choice": "run-always", "step": d["step"]}} for d in items])
        self.assertEqual([r["params"]["step"] for r in requests], STEPS)

    def test_single_request_and_two_tickets(self):
        one = decisions(journal_outside([("PT-1", OUTSIDE_COMMAND)]))
        self.assertEqual(self.groups(one), one, "pojedyncza decyzja zostaje bez zmian")
        two = self.groups(decisions(journal_outside([("PT-1", OUTSIDE_COMMAND), ("PT-2", OUTSIDE_COMMAND)])))
        self.assertEqual([g["ticket"] for g in two], ["2 tickety"])
        self.assertEqual({o["id"]: o["effect"] for o in two[0]["options"]}["deny"], "2 kroki wznowią się bez polecenia.")

    def test_key_edge_cases_keep_different_command_text_apart(self):
        cases = {
            "spacje w środku": ("make test-changed BASE=1", "make  test-changed BASE=1"),
            "spacja na końcu": ("make test-changed BASE=1", "make test-changed BASE=1 "),
            "cudzysłowy": ('pytest -k "a b"', "pytest -k 'a b'"),
            "koniec długiego polecenia": ("pytest " + "tests/test_długiej_ścieżki.py " * 4 + "-k alpha",
                                          "pytest " + "tests/test_długiej_ścieżki.py " * 4 + "-k alphb"),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                items = decisions(journal_outside([("PT-1", first), ("PT-2", second)]))
                self.assertEqual([d["technical"]["command"] for d in items], [first, second], "usługa nie scala tekstu")
                out = self.groups(items)
                self.assertEqual([d["id"] for d in out], [d["id"] for d in items])
                self.assertFalse(any(d.get("group") for d in out))

    def test_identical_quoted_and_long_commands_group_with_full_text(self):
        long = "pytest " + "tests/test_długiej_ścieżki.py " * 4 + "-k 'a b' --maxfail=1"
        for command in ('pytest -k "a b"', long):
            with self.subTest(command=command):
                [group] = self.groups(decisions(journal_outside([("PT-1", command), ("PT-2", command), ("PT-3", command)])))
                self.assertEqual(group["technical"]["command"], command, "polecenie w polu pod spodem — w całości")
                head = command if len(command) <= 70 else "To samo polecenie poza piaskownicą"
                self.assertEqual(group["happened"], head + " · 3 tickety")

    def test_risky_request_does_not_join_ordinary_ones(self):
        # Ten sam tekst: dla PT-1 ścieżka jest w jego worktree (zwykła prośba), dla PT-2 i PT-3 — spoza worktree.
        command = "python3 /wt/PT-1/tools/run.py"
        items = decisions(journal_outside([("PT-1", command), ("PT-2", command), ("PT-3", command)]))
        self.assertEqual([[o["id"] for o in d["options"]] for d in items],
                         [["run-always", "run", "deny"], ["run", "run-ticket", "deny"], ["run", "run-ticket", "deny"]])
        out = self.groups(items)
        self.assertEqual(out[0]["id"], items[0]["id"], "zwykła prośba zostaje sama")
        self.assertFalse(any(d.get("group") and items[0]["id"] in [m["id"] for m in d["members"]] for d in out))
        # Ryzykowne prośby różnych ticketów też się nie łączą: „do końca ticketu” nazywa swój ticket.
        self.assertEqual([d["id"] for d in out], [d["id"] for d in items])

    def test_risky_requests_with_the_same_effects_group_and_keep_the_warning(self):
        decisions_list = decisions(journal_outside([("PT-1", "cat /etc/hostname"), ("PT-2", "cat /etc/hostname")]))
        # Ten sam ticket nie występuje dwa razy — zgodę „do końca ticketu” usługa opisuje per ticket.
        self.assertNotEqual(decisions_list[0]["options"][1]["effect"], decisions_list[1]["options"][1]["effect"])
        self.assertEqual([d["id"] for d in self.groups(decisions_list)], [d["id"] for d in decisions_list])

    def test_chain_with_commands_list_is_not_grouped(self):
        chain = decisions(a1_journal())
        twin = json.loads(json.dumps(chain))
        twin[0]["id"] += "-bis"
        twin[0]["step"] += "-bis"
        for option in twin[0]["options"]:
            option["params"]["step"] = twin[0]["step"]
        both = chain + twin
        self.assertTrue(all(d["commands"] for d in both))
        self.assertEqual([d["id"] for d in self.groups(both)], [d["id"] for d in both])

    def test_other_decisions_keep_their_place(self):
        items = decisions(journal_outside([("PT-1", OUTSIDE_COMMAND), ("PT-2", "make lint"), ("PT-3", OUTSIDE_COMMAND)]))
        out = self.groups(items)
        self.assertEqual([d["id"] for d in out], ["consent-group:" + items[0]["id"], items[1]["id"]])

    def test_receipt_and_refusal_of_one_step(self):
        items = decisions(journal_outside())
        ids = [d["id"] for d in items]
        sent = self.js("consentSent(consentStart(), I)", I=ids)
        [receipt] = self.js("consentItems(D, [], S)", D=items, S=sent)
        self.assertTrue(receipt["receipt"])
        self.assertEqual(receipt["receiptText"], "Przyjęto · wysyłam 7 odpowiedzi")
        self.assertEqual(receipt["options"], [])
        failed = self.js("consentFailed(S, I[2], 'krok już wznowiony')", S=sent, I=ids)
        out = self.js("consentItems(D, [], F)", D=items, F=failed)
        self.assertEqual([d["id"] for d in out], ["consent-receipt:" + ids[0], ids[2]])
        self.assertEqual(out[0]["count"], 6)
        self.assertEqual(out[0]["receiptText"], "Przyjęto · wysyłam 7 odpowiedzi · usługa odrzuciła 1 odpowiedź — "
                                                "wraca osobno z powodem")
        self.assertEqual(failed["reasons"], {ids[2]: "krok już wznowiony"})
        # Odrzucona pierwsza decyzja stoi przed znakiem przyjęcia (kolejność usługi).
        first = self.js("consentItems(D, [], consentFailed(S, I[0], 'x'))", D=items, S=sent, I=ids)
        self.assertEqual([d["id"] for d in first], [ids[0], "consent-receipt:" + ids[0]])
        # Usługa przyjęła — decyzje znikają z migawki, znak przyjęcia też.
        self.assertEqual(self.js("consentItems([], [], S)", S=sent), [])
        # Nowa prośba o to samo polecenie po wysyłce nie dołącza do wysłanej grupy.
        later = decisions(journal_outside(list((t, OUTSIDE_COMMAND) for t in OUTSIDE_TICKETS) + [("PT-8", OUTSIDE_COMMAND)]))
        out = self.js("consentItems(D, [], S)", D=later, S=sent)
        self.assertEqual([d["id"] for d in out], ["consent-receipt:" + ids[0], later[-1]["id"]])


GROUP_PY = r'''
def group():
    """Pozycja grupy w oknie: znak przyjęcia, przełącznik i wiersze listy ticketów, miejsce na prognozę."""
    return json.loads(js("(function () { const d = page.decisionDialog; const all = " + ALL + "; "
        "const one = n => all(d, n)[0] || null; const y = it => it.mapToItem(d, 0, 0).y; "
        "return JSON.stringify({ receipt: one('decisionReceipt') ? one('decisionReceipt').text : '', "
        "  toggle: one('decisionGroupToggle') ? one('decisionGroupToggle').text : '', "
        "  members: all(d, 'decisionGroupMember').sort((a, b) => y(a) - y(b)).map(m => [m.label, m.reason]), "
        "  forecast: !!one('decisionGroupForecast'), command: one('decisionCommand') ? true : false }) })()"))

def option_titled(title):
    return at("(" + ALL + ")(page.decisionDialog, 'decisionOption').find(o => o.title.indexOf(" + json.dumps(title) + ") === 0)")
'''


def with_question(events):
    """Dziennik z dodatkową decyzją innego rodzaju (pytanie PT-9) po prośbach o zgodę."""
    events = list(events)
    seq = len(events)
    events += [
        {"v": 1, "seq": seq + 1, "ts": "2026-11-10T11:30:00+01:00", "card": CARD, "type": "step.started",
         "step": "K-8-ticket-9-0", "ticket": "PT-9", "kind": "ticket", "executor": "Opus", "provider": "anthropic",
         "model": "opus", "attempt": 0, "parentStep": "", "stepDir": "/wt/PT-9.step", "worktree": "/wt/PT-9",
         "branch": "ticket/pt-9"},
        {"v": 1, "seq": seq + 2, "ts": "2026-11-10T11:40:00+01:00", "card": CARD, "type": "step.finished",
         "step": "K-8-ticket-9-0", "ticket": "PT-9", "result": "question", "question": "Czy zostawić stary test?"}]
    return events


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class ConsentGroupDialogTest(ProbeCase):
    def test_seven_requests_are_one_position_and_counter_counts_the_group_once(self):
        items = decisions(with_question(journal_outside()))
        self.assertEqual(len(items), 8)
        out = self.probe(STATE_PY + GROUP_PY + """
click(card("K-8"))
out["opened"] = state()
out["group"] = group()
key(Qt.Key.Key_Return)
out["enter"] = [len(calls()), state()["choose"]]
click(named("decisionGroupToggle"))
out["expanded"] = group()
""", scenario_f(self.data, items))
        opened = out["opened"]
        self.assertTrue(opened["open"])
        self.assertEqual(opened["counter"], "1 z 2", "siedem decyzji grupy liczy się jako jedna pozycja")
        self.assertEqual(opened["kind"], "Prośba o zgodę")
        self.assertEqual(opened["who"], "K-8 · 7 ticketów")
        self.assertEqual(opened["happened"], OUTSIDE_COMMAND + " · 7 ticketów")
        self.assertEqual([(o[0][:18], o[2], o[3]) for o in opened["options"]],
                         [("Zawsze w projekcie", True, False), ("Uruchom raz", False, False),
                          ("Nie uruchamiaj", False, False)], "kolejność i polecona z usługi, nic nie zaznaczone (D10)")
        self.assertIn("Każdy z 7 kroków", opened["options"][1][1])
        self.assertIsNone(opened["other"], "grupa nie ma wspólnej własnej odpowiedzi")
        self.assertFalse(opened["choose"])
        self.assertEqual(out["enter"], [0, False], "Enter bez wyboru nic nie wysyła")
        self.assertEqual(out["group"]["toggle"], "▸ Tickety i kroki (7)")
        self.assertEqual(out["group"]["members"], [], "lista zwinięta po otwarciu")
        # Strona Tablicy zawsze liczy obciążenie z migawki kart, więc prognoza jest widoczna; jej brak bez `outsideLoad`
        # (Sesje, migawka nierozpoznana) sprawdza test_outside_forecast.py.
        self.assertTrue(out["group"]["forecast"], "strona Tablicy przekazuje obciążenie (PT-340)")
        self.assertEqual([m[0] for m in out["expanded"]["members"]],
                         [f"{t} · {s} · Sonnet" for t, s in zip(OUTSIDE_TICKETS, STEPS)])
        self.assertTrue(all(m[1].startswith("Testy ticketu") for m in out["expanded"]["members"]))

    def test_always_in_project_sends_seven_answers_and_shows_receipt_at_once(self):
        out = self.probe(STATE_PY + GROUP_PY + """
click(card("K-8"))
click(option_titled("Zawsze w projekcie"))
out["picked"] = state()["choose"]
key(Qt.Key.Key_Return)
out["now"] = [state(), group()]
out["sent"] = calls()
wait(200)
out["after"] = [state(), group()]
""", scenario_f(self.data, decisions(journal_outside())))
        self.assertTrue(out["picked"])
        sent = out["sent"]
        self.assertEqual(len(sent), 7)
        self.assertEqual([c[:2] for c in sent], [["ResolveCardStep", CARD]] * 7)
        self.assertEqual([json.loads(c[2]) for c in sent], [{"choice": "run-always", "step": s} for s in STEPS])
        state, group = out["now"]
        self.assertTrue(state["open"], "pozycja zostaje ze znakiem przyjęcia")
        self.assertEqual(group["receipt"], "Przyjęto · wysyłam 7 odpowiedzi")
        self.assertEqual(state["options"], [])
        self.assertFalse(state["choose"])
        self.assertEqual(out["after"][1]["receipt"], "Przyjęto · wysyłam 7 odpowiedzi", "usługa przyjęła — bez zmian")

    def test_refusal_for_one_step_brings_back_only_its_decision_with_reason(self):
        out = self.probe(STATE_PY + GROUP_PY + """
replies.extend([{"ok": True}, {"ok": True}, {"ok": False, "error": "busy", "message": "krok PT-3 jeszcze się kończy"}])
click(card("K-8"))
click(option_titled("Uruchom raz"))
key(Qt.Key.Key_Return)
until(lambda: state()["who"] == "K-8 · PT-3 · Sonnet", 3000)
out["back"] = state()
out["sent"] = calls()
click(named("decisionPrev"))
click(named("decisionNext"))
out["again"] = state()["error"]
""", scenario_f(self.data, decisions(journal_outside())))
        sent = out["sent"]
        self.assertEqual([json.loads(c[2]) for c in sent], [{"choice": "run", "step": s} for s in STEPS])
        back = out["back"]
        self.assertEqual(back["who"], "K-8 · PT-3 · Sonnet", "wraca tylko odrzucona decyzja")
        self.assertIn("krok PT-3 jeszcze się kończy", back["error"])
        self.assertEqual(back["counter"], "2 z 2", "znak przyjęcia pozostałych i jedna decyzja osobno")
        self.assertEqual([o[3] for o in back["options"]], [False, False, False])
        self.assertIn("krok PT-3 jeszcze się kończy", out["again"], "powód zostaje przy decyzji")

    def test_full_card_your_move_uses_the_same_group(self):
        out = self.probe(STATE_PY + """
FULL = ("(function () { const v = page.cardFullView; const all = " + ALL + "; const one = n => all(v, n)[0] || null; "
        "return JSON.stringify({ happened: one('decisionHappened') ? one('decisionHappened').text : '', "
        "  receipt: one('decisionReceipt') ? one('decisionReceipt').text : '', "
        "  all: one('fullAllDecisions') ? one('fullAllDecisions').text : '' }) })()")
js("page.openFull('K-8')")
wait(200)
out["opened"] = json.loads(js(FULL))
js("page.cardFullView.form.pick('deny')")
js("page.cardFullView.form.choose()")
wait(50)
out["sent"] = calls()
out["after"] = json.loads(js(FULL))
""", full_card(self.data, journal_outside()))
        self.assertEqual(out["opened"]["happened"], OUTSIDE_COMMAND + " · 7 ticketów")
        self.assertEqual(out["opened"]["all"], "", "jedna pozycja — bez „1 z N”")
        self.assertEqual([json.loads(c[2]) for c in out["sent"]], [{"choice": "deny", "step": s} for s in STEPS])
        self.assertEqual(out["after"]["receipt"], "Przyjęto · wysyłam 7 odpowiedzi")


if __name__ == "__main__":
    unittest.main()
