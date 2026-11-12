"""K-99, PT-389: linia „Start od: …” i „Zmień gałąź startową…” w panelu i pełnej karcie (CardStartLine.qml).

Dane to `derived` policzone przez usługę z dziennika (decisionfix.fold), nie ręcznie wpisane pola:
- panel i pełna karta pokazują identyczny tekst dla tej samej karty; baza `unknown` to „Start od: nieznany”,
- po skutecznej korekcie bazy: nowa baza bez daty, pierwotna w podpowiedzi (`timeline` nie niesie czasu korekty),
- przycisk tylko na karcie bez pracy (ukryty przy ticketach poza `todo`, scalonych ticketach i domknięciu),
- pełna karta otwiera istniejący podgląd korekty bazy bez `RequestCardBaseChange`; panel emituje jeden sygnał,
  a strona Tablicy pokazuje pełną kartę z otwartym podglądem.
"""
from .decisionfix import CARD, full_card, full_d, journal_move
from .test_view import ProbeCase

OLD = "a" * 40
NEW = "b" * 40


def started(**extra) -> dict:
    return {"v": 1, "seq": 1, "ts": "2026-11-03T12:00:00+01:00", "card": CARD, "type": "card.started", "entry": "now",
            "executor": "Sonnet", "program": "claude", "provider": "anthropic",
            "baseRef": "refs/heads/master", "baseBranch": "master", "baseCommit": OLD, **extra}


def event(events: list[dict], event_type: str, **data) -> list[dict]:
    return events + [{"v": 1, "seq": len(events) + 1, "ts": "2026-11-04T12:00:00+01:00", "card": CARD,
                      "type": event_type, **data}]


def corrected() -> list[dict]:
    return event([started()], "card.base-changed", ok=True, previousBaseVersion=0, baseVersion=1,
                 base={"baseRef": "refs/heads/topic", "baseBranch": "topic", "baseCommit": NEW})


FIRST = r'''
js('page.openCard("K-8")')
wait()
out["panel"] = obj("cardPanelStartLineText").property("text")
out["panelButton"] = obj("cardPanelStartLineChange").property("visible")
js('page.closePanel(); page.openFull("K-8")')
wait()
out["full"] = obj("fullStartLineText").property("text")
out["fullButton"] = obj("fullStartLineChange").property("visible")
out["fullHint"] = obj("fullStartLine").property("hint")
out["oldBase"] = js("page.cardFullView.visible && page.cardFullView.flow.base.baseRef")
'''


class StartLineTest(ProbeCase):
    def probe_card(self, events, scenario=FIRST):
        return self.probe(scenario, full_card(self.data, events))

    def test_panel_and_full_card_show_the_same_start_line(self):
        out = self.probe_card([started()])
        self.assertEqual(out["panel"], "Start od: master (3.11, " + OLD[:7] + ")")
        self.assertEqual(out["full"], out["panel"])
        self.assertEqual(out["fullHint"], "")
        self.assertTrue(out["panelButton"])
        self.assertTrue(out["fullButton"])

    def test_unknown_base_says_unknown(self):
        events = [started()]
        for key in ("baseRef", "baseBranch", "baseCommit"):
            del events[0][key]
        out = self.probe_card(events)
        self.assertEqual(out["panel"], "Start od: nieznany")
        self.assertEqual(out["full"], "Start od: nieznany")

    def test_remote_base(self):
        out = self.probe_card([started(baseRef="refs/remotes/origin/develop", baseBranch="origin/develop")])
        self.assertEqual(out["panel"], "Start od: origin/develop (3.11, " + OLD[:7] + ")")

    def test_corrected_base_has_new_base_without_date_and_original_in_hint(self):
        out = self.probe_card(corrected())
        self.assertEqual(out["panel"], "Start od: topic (" + NEW[:7] + ")")
        self.assertEqual(out["full"], out["panel"])
        self.assertEqual(out["fullHint"], "Pierwotna baza: master (3.11, " + OLD[:7] + ")")

    def test_full_card_has_no_raw_base_line(self):
        out = self.probe_card([started()], FIRST + r'''
out["raw"] = 0 if obj("fullBaseChange") is None else 1
''')
        self.assertEqual(out["raw"], 0)

    def test_button_only_on_card_without_work(self):
        cases = {
            "work": full_d(self.data),
            "merged": full_card(self.data, journal_move([], merged=("#1",))),
            "closed": full_card(self.data, event([started()], "card.closed", summary="gotowe")),
            "step": full_card(self.data, event([started()], "step.started", step="K-8-ticket-1-0", ticket="#1",
                                                 kind="ticket", executor="Sonnet", provider="anthropic", model="sonnet",
                                                 attempt=0, parentStep="", stepDir="/wt/x.step", worktree="/wt/x",
                                                 branch="ticket/1")),
            "plan-todo": full_card(self.data, journal_move([], merged=())),
        }
        shown = {}
        for name, data in cases.items():
            out = self.probe(FIRST, data)
            self.assertTrue(out["full"].startswith("Start od: "), (name, out))
            shown[name] = (out["panelButton"], out["fullButton"])
        self.assertEqual(shown, {"work": (False, False), "merged": (False, False), "closed": (False, False),
                                 "step": (False, False), "plan-todo": (True, True)})

    def test_full_card_click_opens_existing_preview_without_service_write(self):
        out = self.probe_card([started()], r'''
js('page.openFull("K-8")')
wait()
before = len(calls())
click(named("fullStartLineChange"))
until(lambda: obj("baseChangePreview").property("visible"))
out["visible"] = obj("baseChangePreview").property("visible")
out["full"] = js("page.fullOpen")
out["calls"] = [c[0] for c in calls()[before:]]
''')
        self.assertTrue(out["visible"])
        self.assertTrue(out["full"])
        self.assertNotIn("RequestCardBaseChange", out["calls"])
        self.assertNotIn("PreviewCardBaseChange", out["calls"])
        self.assertNotIn("ResolveCardStep", out["calls"])

    def test_panel_click_emits_one_signal_and_page_shows_full_card_with_preview(self):
        out = self.probe_card([started()], r'''
js('page.openCard("K-8")')
wait()
signals = []
obj("cardPanel").baseChangeRequested.connect(lambda card_id: signals.append(card_id))
before = len(calls())
out["previewBefore"] = obj("baseChangePreview").property("visible")
click(named("cardPanelStartLineChange"))
until(lambda: obj("baseChangePreview").property("visible"))
out["signals"] = signals
out["full"] = js("page.fullOpen && page.fullCardId")
out["panelOpen"] = js("page.panelOpen")
out["visible"] = obj("baseChangePreview").property("visible")
out["calls"] = [c[0] for c in calls()[before:]]
''')
        self.assertFalse(out["previewBefore"])
        self.assertEqual(out["signals"], ["K-8"])
        self.assertEqual(out["full"], "K-8")
        self.assertFalse(out["panelOpen"])
        self.assertTrue(out["visible"])
        for member in ("RequestCardBaseChange", "PreviewCardBaseChange", "ResolveCardStep"):
            self.assertNotIn(member, out["calls"])
