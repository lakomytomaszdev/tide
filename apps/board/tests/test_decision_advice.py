"""Jedno zalecenie w prośbie o zgodę i zwinięty podgląd skryptu (K-128, PT-506).

- reguła jednego zalecenia (`advice`, `optionAdvice` w apps/common/qml/card/decision.js) jako tabela liczona w qml6:
  zgodny opiekun, inny wybór opiekuna (kontrakt tymczasowy do etapu B), brak wyboru z `recommendation`, `failed`
  z `tried`, brak opiekuna; tytuł jako pytanie (`questionTitle`) i pole skryptu (`script`),
- sonda offscreen na prośbie o zgodę z dziennika karty (decisionfix.journal_outside → `build_decisions` usługi,
  opiekun dopisany funkcją usługi `caretaker.annotate_escalation`): pytanie w tytule, „polecane” z dopiskiem opiekuna,
  najwyżej jeden przycisk główny w oknie decyzji i w „Twój ruch”, podpowiedź klawiszy, ↑↓ wybierają możliwość,
  kontrast nowych kolorów (plakietki, dopiski) na ich tłach,
- podgląd skryptu z pola `decision.script` (dane testowe — usługa poda je w etapie B): blok zwinięty, po rozwinięciu
  linie z numerami i kolorowaniem, `<` i `&` widoczne jako tekst; bez pola — brak bloku, polecenie w polu z „Kopiuj”.

Z `TIDE_ADVICE_SHOTS=<katalog>` sondy zapisują też zrzuty PNG (treść 1180×1000 i najwęższe okno 1180×620); zawsze
QT_QPA_PLATFORM=offscreen (ProbeCase) — bez okien na pulpicie.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import REPO
from .decisionfix import full_card, journal_outside, scenario_f
from .test_view import QML_PROBLEMS, ProbeCase

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.cardflow.caretaker import annotate_escalation  # noqa: E402
from tide_daemon.cardflow.decisions import build_decisions  # noqa: E402
from tide_daemon.cardflow.state import fold  # noqa: E402

CARD_DIR = os.path.join(REPO, "apps", "common", "qml", "card")

COMMAND = "bash scripts/tmp/reci38-run.sh"
REASON = ("Testy RECI-38 z karty K-126 potrzebują sieci i Dockera: `docker compose build docs` pobiera silnik "
          "z cdn.jsdelivr.net do docs/site/templates/assets/vendor/.")
CARE_RECOMMENDATION = "Polecam zgodzić się na uruchamianie w projekcie."
CARE_SUMMARY = ("Skrypt jest zgodny z kartką RECI-38: lokalny Mermaid, build portalu i kontrola _site; "
                "nie wykonuje działań destrukcyjnych. Z sieci pobiera tylko silnik z cdn.jsdelivr.net.")
SCRIPT_TEXT = ("#!/usr/bin/env bash\n"
               "# RECI-38: build portalu i zrzuty przed/po\n"
               "set -euo pipefail\n"
               "OUT=\"$(mktemp -d)\"\n"
               "if [ ! -f docs/site/templates/assets/vendor/mermaid.min.js ]; then\n"
               "    curl -fsSL \"https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js\" \\\n"
               "        -o docs/site/templates/assets/vendor/mermaid.min.js\n"
               "fi\n"
               "export DOCKER_CONFIG=\"$(mktemp -d)\"\n"
               "echo '{}' > \"$DOCKER_CONFIG/config.json\"\n"
               "docker compose build docs\n"
               "cid=$(docker create cookapp-docs)\n"
               "docker cp \"$cid:/srv/_site\" \"$OUT/_site\" && test -d \"${OUT}/_site\"\n"
               "bash docs/site/tests/check-built-site.sh \"$OUT/_site\" 2>&1 | tee \"$OUT/check.log\"\n"
               "grep -c '<html' \"$OUT/_site/index.html\" > /dev/null\n"
               "echo \"wynik: $OUT\"\n")
SCRIPT = {"path": "scripts/tmp/reci38-run.sh", "language": "bash", "text": SCRIPT_TEXT,
          "lineCount": SCRIPT_TEXT.count("\n"), "truncated": False}


def consent_items(caretaker: str | None = "run", script: dict | None = None) -> list[dict]:
    """Prośba o zgodę z dziennika karty, policzona przez usługę; `caretaker` — opcja wskazana przez opiekuna
    (dopisana funkcją usługi `annotate_escalation`, jak przy decyzji wracającej od opiekuna), None — bez opiekuna."""
    events = journal_outside([("PT-3", COMMAND, {"reason": REASON, "pattern": ""})])
    st = fold(events)
    items = build_decisions(st)
    if caretaker is not None:
        items[0] = annotate_escalation(st, items[0], {
            "problem": "outside:K-8-ticket-3-0", "step": "", "optionId": caretaker,
            "recommendation": CARE_RECOMMENDATION, "summary": CARE_SUMMARY, "failed": False})
    if script is not None:
        items[0]["script"] = script
    return items


def full_data(data: dict, items: list[dict]) -> dict:
    """Pełna karta K-8 („Twój ruch”) z decyzjami `items` zamiast policzonych z dziennika."""
    data = full_card(data, journal_outside([("PT-3", COMMAND, {"reason": REASON, "pattern": ""})]))
    data["cardOpen"] = "full"
    for card in data["cards"]:
        if card["id"] == "K-8":
            card["derived"]["flow"]["decisions"] = items
    return data


# --- reguła i tytuł w qml6 (ten sam decision.js co w oknie) ---------------------------------------------------

LOGIC = """import QtQuick
import "decision.js" as Decision

QtObject {
    function rule(d) {
        const a = Decision.advice(d)
        return { mode: a.mode, recommendedId: a.recommendedId, caretakerId: a.caretakerId, sentence: a.sentence,
                 marks: (d.options || []).map(o => Decision.optionAdvice(a, o.id)),
                 detail: Decision.adviceDetail(a, d.caretaker || null),
                 tried: Decision.triedRows(d.caretaker || null).length }
    }
    Component.onCompleted: {
        const out = [%(calls)s]
        console.warn("RESULT " + JSON.stringify(out))
        Qt.quit()
    }
}
"""


def logic(calls: list[str]) -> list:
    with tempfile.TemporaryDirectory(prefix="tide-advice-") as tmp:
        shutil.copy(os.path.join(CARD_DIR, "decision.js"), tmp)
        path = os.path.join(tmp, "Logic.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(LOGIC % {"calls": ",\n".join(calls)})
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
        result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60)
    log = result.stderr
    match = re.search(r"RESULT (\[.*\])", log)
    if re.search(QML_PROBLEMS, log) or not match:
        raise AssertionError("qml6 nie policzył decision.js:\n" + log)
    return json.loads(match.group(1))


OPTIONS = [{"id": "run-always", "title": "Zawsze w projekcie", "recommended": True},
           {"id": "run", "title": "Uruchom raz", "recommended": False},
           {"id": "deny", "title": "Nie uruchamiaj", "recommended": False}]
TRIED = [{"step": "K-8-care-1", "action": "merge", "ok": False, "reason": "", "text": "nie udało się „merge”"}]
NONE = {"badge": "", "note": "", "tone": "", "link": ""}
AGREE = {"badge": "", "note": "✓ Polecane — opiekun karty też to poleca", "tone": "agree",
         "link": "uzasadnienie opiekuna"}
DIFFER = {"badge": "opiekun", "note": "◆ Opiekun poleca tę opcję", "tone": "differ", "link": "dlaczego?"}


def decision(care=None):
    return {"options": OPTIONS, **({"caretaker": care} if care is not None else {})}


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class AdviceRuleTableTest(unittest.TestCase):
    """Reguła jednego zalecenia: dane wejściowe decyzji → tryb, oznaczenia opcji i zdanie opiekuna."""

    CASES = [
        ("zgodny opiekun", decision({"optionId": "run-always", "recommendation": "R", "summary": "S", "tried": [],
                                     "failed": False}),
         {"mode": "agree", "caretakerId": "run-always", "sentence": "", "marks": [AGREE, NONE, NONE],
          "detail": {"recommendation": "R", "summary": "S"}, "tried": 0}),
        # KONTRAKT TYMCZASOWY (TODO(K-128 etap B)): dziś usługa nadpisuje `recommended` wyborem opiekuna, więc ten
        # wiersz działa tylko na danych testowych; etap B może go zmienić albo usunąć.
        ("inny wybór opiekuna (kontrakt tymczasowy do etapu B)",
         decision({"optionId": "run", "recommendation": "R", "summary": "S", "tried": [], "failed": False}),
         {"mode": "differ", "caretakerId": "run", "sentence": "", "marks": [NONE, DIFFER, NONE],
          "detail": {"recommendation": "R", "summary": "S"}, "tried": 0}),
        ("brak wyboru z recommendation",
         decision({"optionId": "", "recommendation": "Zmień wykonawcę.", "summary": "S", "tried": [],
                   "failed": False}),
         {"mode": "note", "caretakerId": "", "sentence": "Opiekun poleca: Zmień wykonawcę.",
          "marks": [NONE, NONE, NONE], "detail": {"recommendation": "", "summary": "S"}, "tried": 0}),
        ("failed z tried",
         decision({"optionId": "", "recommendation": "", "summary": "Opiekun nie poradził sobie: X",
                   "tried": TRIED, "failed": True}),
         {"mode": "note", "caretakerId": "",
          "sentence": "Opiekun nie poradził sobie z problemem i oddaje decyzję Tobie.", "marks": [NONE, NONE, NONE],
          "detail": {"recommendation": "", "summary": "Opiekun nie poradził sobie: X"}, "tried": 1}),
        ("brak opiekuna", decision(),
         {"mode": "none", "caretakerId": "", "sentence": "", "marks": [NONE, NONE, NONE],
          "detail": {"recommendation": "", "summary": ""}, "tried": 0}),
        ("wybór spoza możliwości", decision({"optionId": "zniknęła", "recommendation": "R", "summary": "",
                                             "tried": [], "failed": False}),
         {"mode": "note", "caretakerId": "", "sentence": "Opiekun poleca: R", "marks": [NONE, NONE, NONE],
          "detail": {"recommendation": "", "summary": ""}, "tried": 0}),
        ("puste pole opiekuna", decision({"optionId": "", "recommendation": "", "summary": "", "tried": [],
                                          "failed": False}),
         {"mode": "none", "caretakerId": "", "sentence": "", "marks": [NONE, NONE, NONE],
          "detail": {"recommendation": "", "summary": ""}, "tried": 0}),
    ]

    def test_table(self):
        found = logic([f"rule({json.dumps(d, ensure_ascii=False)})" for _name, d, _expected in self.CASES])
        for (name, _decision, expected), got in zip(self.CASES, found):
            with self.subTest(name):
                self.assertEqual(got["recommendedId"], "run-always", "„polecane” zawsze z `recommended` usługi")
                self.assertEqual({k: got[k] for k in expected}, expected)

    def test_without_recommended_option_nothing_is_recommended(self):
        (got,) = logic([f"rule({json.dumps({'options': [dict(o, recommended=False) for o in OPTIONS]})})"])
        self.assertEqual((got["mode"], got["recommendedId"]), ("none", ""))

    def test_question_title_and_script_field(self):
        perm = {"kind": "permission", "technical": {"command": "make test-changed BASE=abc"},
                "happened": "Ticket PT-3 (Sonnet) prosi o uruchomienie poza piaskownicą: make test-changed BASE=abc"}
        long_cmd = "bash -c " + "x" * 90
        calls = [
            f"Decision.questionTitle({json.dumps(perm)})",
            f"Decision.questionTitle({json.dumps(dict(perm, technical={'command': long_cmd}, happened='… prosi o uruchomienie polecenia poza piaskownicą'))})",
            "Decision.questionTitle({kind: 'permission', technical: {command: 'git push'}, "
            "happened: 'Ticket chce uruchomić polecenie spoza listy dozwolonych: git push', commands: []})",
            "Decision.questionTitle({kind: 'permission', technical: {command: 'a; b'}, happened: 'x', "
            "commands: [{text: 'a'}, {text: 'b'}]})",
            "Decision.questionTitle({kind: 'permission', technical: {command: 'network: cdn.example.com'}, happened: 'x'})",
            "Decision.questionTitle({kind: 'question', technical: {command: ''}, happened: 'Krok pyta: czy?'})",
            f"Decision.questionTitle({json.dumps(dict(perm, group=True))})",
            f"Decision.script({json.dumps({'script': SCRIPT})})",
            "Decision.script({})",
            "Decision.script({script: {path: 'a.sh'}})",
        ]
        (short, long_, allow, chain, network, question, group, script, none, no_text) = logic(calls)
        self.assertEqual(short, "Uruchomić make test-changed BASE=abc poza piaskownicą?")
        self.assertEqual(long_, "Uruchomić polecenie poza piaskownicą?")
        self.assertEqual(allow, "Zezwolić na git push?")
        self.assertEqual(chain, "Zezwolić na polecenia z łańcucha?")
        self.assertEqual((network, question, group), ("", "", ""), "bez pytania — dzisiejszy tytuł")
        self.assertEqual(script, SCRIPT)
        self.assertIsNone(none)
        self.assertIsNone(no_text)


class TemporaryBranchMarkerTest(unittest.TestCase):
    def test_differ_branch_has_the_stage_b_todo(self):
        with open(os.path.join(CARD_DIR, "decision.js"), encoding="utf-8") as fh:
            source = fh.read()
        branch = source[source.index("out.mode = \"agree\""):source.index("out.mode = \"differ\"")]
        self.assertIn("TODO(K-128 etap B)", branch)
        self.assertIn("annotate_escalation nadpisuje `recommended`", branch)


# --- sondy okna decyzji i „Twój ruch” -----------------------------------------------------------------------

PROBE_PY = r'''
import os
from PyQt6 import sip
from PyQt6.QtGui import QTextDocument
from PyQt6.QtQuick import QQuickWindow
shots = os.environ.get("TIDE_ADVICE_SHOTS", "")

ALL = ("(function all(it, name, out) { out = out || []; if (!it) return out; "
       "if (it.objectName === name && it.visible) out.push(it); "
       "for (const c of it.children) all(c, name, out); return out })")
PRIMARY = ("(function prim(it, out) { out = out || []; if (!it || !it.visible) return out; "
           "if (it.variant === 'primary' && it.clicked !== undefined) out.push(it.text); "
           "for (const c of it.children) prim(c, out); return out })")

def shown(root, name):
    return at("(" + ALL + ")(" + root + ", '" + name + "')[0]")

def rgba(expr):
    return json.loads(js("(function () { const c = " + expr + "; return JSON.stringify([c.r, c.g, c.b, c.a]) })()"))

def look(root):
    return json.loads(js("(function () { const r = " + root + "; const all = " + ALL + "; "
        "const one = n => all(r, n)[0] || null; const y = it => it.mapToItem(r, 0, 0).y; "
        "const opts = all(r, 'decisionOption').sort((a, b) => y(a) - y(b)); "
        "return JSON.stringify({ "
        "  title: one('decisionTitle') ? one('decisionTitle').text : null, "
        "  happened: one('decisionHappened') ? one('decisionHappened').text : null, "
        "  titleAbove: one('decisionTitle') && one('decisionHappened') ? y(one('decisionTitle')) < y(one('decisionHappened')) : null, "
        "  options: opts.map(o => [o.title, o.checked, all(o, 'decisionRecommended').length, "
        "    all(o, 'decisionCaretakerBadge').length, (all(o, 'decisionAdviceNote')[0] || {}).text || '']), "
        "  oldBlock: !!one('decisionCaretaker'), "
        "  detail: !!one('decisionCaretakerDetail'), "
        "  summary: one('decisionCaretakerSummary') ? one('decisionCaretakerSummary').text : null, "
        "  primary: (" + PRIMARY + ")(r), "
        "  hint: (one('decisionKeysHint') || one('fullKeysHint') || {}).text || null, "
        "  command: one('decisionCommand') ? [all(one('decisionCommand'), 'codeBlockText')[0].text, "
        "    all(one('decisionCommand'), 'codeBlockCopy').map(b => b.text)] : null, "
        "  script: one('decisionScript') ? { "
        "    head: [all(one('decisionScript'), 'codeBlockPath').map(t => t.text)[0], "
        "           all(one('decisionScript'), 'codeBlockSummary').map(t => t.text)[0], "
        "           all(one('decisionScript'), 'codeBlockToggle').map(t => t.text)[0]], "
        "    lines: all(one('decisionScript'), 'codeBlockLine').map(l => [all(l, 'codeBlockNumber')[0].text, "
        "      all(l, 'codeBlockCode')[0].text]) } : null }) })()"))

def plain(markup):
    doc = QTextDocument()
    doc.setHtml(markup)
    return doc.toPlainText()

def grab(name):
    if not shots:
        return
    os.makedirs(shots, exist_ok=True)
    for size, (w, h) in (("wide", (1180, 1000)), ("narrow", (1180, 620))):
        window.setWidth(w)
        window.setHeight(h)
        wait(500)
        quick = sip.cast(window, QQuickWindow)
        quick.grabWindow().save(os.path.join(shots, name + "-" + size + ".png"))
    window.setWidth(1440)
    window.setHeight(900)
    wait(200)

click(card("K-8"))
until(lambda: js("page.decisionOpen || page.fullOpen"))
wait(200)
'''


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class ConsentDialogTest(ProbeCase):
    def test_consent_has_question_title_one_recommendation_and_one_primary_button(self):
        out = self.probe(PROBE_PY + """
R = "page.decisionDialog"
out["closed"] = look(R)
click(shown(R, "decisionAdviceToggle"))
wait(150)
out["open"] = look(R)
grab("zgoda-polecane-opiekun")
key(Qt.Key.Key_Down)
out["down"] = look(R)["options"]
key(Qt.Key.Key_Down)
out["down2"] = look(R)["options"]
key(Qt.Key.Key_Up)
out["up"] = look(R)["options"]
out["sent"] = calls()
""", scenario_f(self.data, consent_items()))
        closed = out["closed"]
        self.assertEqual(closed["title"], f"Uruchomić {COMMAND} poza piaskownicą?")
        self.assertIn("prosi o uruchomienie poza piaskownicą", closed["happened"])
        self.assertTrue(closed["titleAbove"], "pytanie nad zdaniem usługi")
        titles = [o[0] for o in closed["options"]]
        self.assertEqual(titles[-1], "Porozmawiaj z opiekunem")
        self.assertEqual([o[2] for o in closed["options"]], [1] + [0] * (len(titles) - 1), "jedna plakietka „polecane”")
        self.assertEqual([o[3] for o in closed["options"]], [0] * len(titles), "bez plakietki „opiekun”")
        self.assertEqual(closed["options"][0][4], "✓ Polecane — opiekun karty też to poleca")
        self.assertEqual([o[4] for o in closed["options"][1:]], [""] * (len(titles) - 1))
        self.assertFalse(closed["oldBlock"], "bez bloku „Opiekun poleca”")
        self.assertFalse(closed["detail"], "uzasadnienie zwinięte")
        self.assertEqual([o[1] for o in closed["options"]], [False] * len(titles), "nic nie jest wybrane (D10)")
        self.assertEqual(closed["primary"], ["Wybierz"], "jeden przycisk główny w oknie decyzji")
        self.assertEqual(closed["hint"], "↑↓ wybór · Enter zatwierdza · Esc później")
        self.assertEqual(closed["command"], [COMMAND, ["Kopiuj"]], "polecenie w polu z „Kopiuj”")
        self.assertIsNone(closed["script"], "bez `decision.script` brak bloku skryptu")

        shown = out["open"]
        self.assertTrue(shown["detail"])
        self.assertEqual(shown["summary"], CARE_SUMMARY)
        self.assertEqual([o[1] for o in shown["options"]], [False] * len(titles))

        self.assertEqual([o[1] for o in out["down"]], [True] + [False] * (len(titles) - 1), "↓ wybiera pierwszą")
        self.assertEqual([o[1] for o in out["down2"]], [False, True] + [False] * (len(titles) - 2))
        self.assertEqual([o[1] for o in out["up"]], [True] + [False] * (len(titles) - 1))
        self.assertEqual(out["sent"], [], "strzałki niczego nie wysyłają")

    def test_new_colors_have_a_contrast_of_4_5_on_their_backgrounds(self):
        items = consent_items()
        items[0]["caretaker"]["optionId"] = "run-ticket"   # dane testowe: obie plakietki naraz (kontrakt tymczasowy)
        out = self.probe(PROBE_PY + """
R = "page.decisionDialog"
o0 = "(" + ALL + ")(" + R + ", 'decisionOption')[0]"
o1 = "(" + ALL + ")(" + R + ", 'decisionOption')[1]"
pairs = {}
for name, opt, badge in (("polecane", o0, "decisionRecommended"), ("opiekun", o1, "decisionCaretakerBadge")):
    b = "(" + ALL + ")(" + opt + ", '" + badge + "')[0]"
    pairs[name] = [rgba(b + ".ink"), rgba(b + ".color"), rgba(opt + ".color")]
for name, opt in (("dopisek opiekun", o1),):
    n = "(" + ALL + ")(" + opt + ", 'decisionAdviceText')[0]"
    t = "(" + ALL + ")(" + opt + ", 'decisionAdviceToggle')[0]"
    pairs[name] = [rgba(n + ".color"), [0, 0, 0, 0], rgba(opt + ".color")]
    pairs[name + " przycisk"] = [rgba(t + ".ink"), rgba(t + ".fill"), rgba(opt + ".color")]
out["pairs"] = pairs
""", scenario_f(self.data, items))
        from widgets.stage import DEFAULT_PALETTE, load_palette
        palette = load_palette(DEFAULT_PALETTE)

        def color(value):
            digits = value.lstrip("#")
            return [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1]

        window, card = color(palette["background"]["window"]), color(palette["background"]["card"])
        out["window"], out["card"] = window, card
        clear = [0, 0, 0, 0]
        # Dopisek przy opcji polecanej (teal) i zdanie opiekuna pod możliwościami (teal, przy `failed` brzoskwinia).
        out["pairs"]["dopisek polecane"] = [color(palette["pastel"]["teal"]), clear, card]
        out["pairs"]["zdanie opiekuna"] = [color(palette["pastel"]["teal"]), clear, window]
        out["pairs"]["zdanie: nie poradził sobie"] = [color(palette["pastel"]["peach"]), clear, window]

        def over(top, bottom):
            a = top[3]
            return [top[i] * a + bottom[i] * (1 - a) for i in range(3)] + [1]

        def lum(c):
            ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c[:3]]
            return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

        def ratio(a, b):
            la, lb = lum(a), lum(b)
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

        self.assertEqual(len(out["pairs"]), 7)
        for name, (ink, fill, under) in out["pairs"].items():
            for base in (out["window"], out["card"]):
                with self.subTest(name=name, base=base):
                    background = over(fill, over(under, base))
                    self.assertGreaterEqual(ratio(over(ink, background), background), 4.5, (name, ink, background))

    def test_script_block_is_collapsed_then_shows_numbered_colored_lines_as_text(self):
        out = self.probe(PROBE_PY + """
R = "page.decisionDialog"
out["closed"] = look(R)
click(shown(R, "codeBlockToggle"))
wait(200)
out["open"] = look(R)
out["plain"] = [plain(line[1]) for line in out["open"]["script"]["lines"]]
grab("zgoda-skrypt")
out["sent"] = calls()
""", scenario_f(self.data, consent_items(None, SCRIPT)))
        closed = out["closed"]["script"]
        self.assertEqual(closed["head"], [SCRIPT["path"], "· 16 linii · bash", "pokaż treść skryptu"])
        self.assertEqual(closed["lines"], [], "blok skryptu domyślnie zwinięty")
        self.assertEqual(out["closed"]["command"], [COMMAND, ["Kopiuj"]], "polecenie nadal w polu z „Kopiuj”")
        lines = out["open"]["script"]["lines"]
        self.assertEqual([n for n, _html in lines], [str(i) for i in range(1, 17)], "numery linii")
        self.assertEqual(out["plain"], SCRIPT_TEXT.rstrip("\n").split("\n"), "„<” i „&” widoczne jako tekst")
        grep = lines[14][1]
        self.assertIn("&lt;html", grep)
        self.assertIn("&amp;&amp;", lines[12][1])
        self.assertNotIn("<html", re.sub(r"</?(div|span)[^>]*>", "", grep), "znacznik ze skryptu nie trafia do HTML")
        from widgets.stage import DEFAULT_PALETTE, load_palette
        pastel = load_palette(DEFAULT_PALETTE)["pastel"]
        self.assertIn(f"color:{pastel['teal']}", lines[10][1], "polecenie `docker` kolorowane")
        self.assertIn(f"color:{pastel['lavender']}", lines[2][1], "słowo kluczowe `set`")
        self.assertIn(f"color:{pastel['sky']}", lines[12][1], "zmienna $cid")
        self.assertEqual(out["sent"], [], "rozwinięcie skryptu niczego nie wysyła")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class YourMoveTest(ProbeCase):
    def test_full_card_your_move_has_one_primary_button_and_the_same_recommendation(self):
        out = self.probe(PROBE_PY + """
R = "page.cardFullView"
click(shown(R, "decisionAdviceToggle"))
wait(150)
out["look"] = look(R)
out["state"] = [js("page.decisionOpen"), js("page.fullOpen")]
grab("twoj-ruch-polecane-opiekun")
key(Qt.Key.Key_Down)
out["down"] = look(R)["options"]
""", full_data(self.data, consent_items()))
        self.assertEqual(out["state"], [False, True])
        look = out["look"]
        self.assertEqual(look["title"], f"Uruchomić {COMMAND} poza piaskownicą?")
        self.assertEqual(look["primary"], ["Wybierz"], "jeden przycisk główny w widoku karty")
        self.assertEqual(look["hint"], "↑↓ wybór · Enter zatwierdza · Esc później")
        self.assertEqual(look["options"][0][2:], [1, 0, "✓ Polecane — opiekun karty też to poleca"])
        self.assertEqual(look["summary"], CARE_SUMMARY)
        self.assertEqual([o[1] for o in out["down"]][:2], [True, False], "↓ wybiera w „Twój ruch”")

    def test_full_card_script_block(self):
        out = self.probe(PROBE_PY + """
R = "page.cardFullView"
click(shown(R, "codeBlockToggle"))
wait(200)
out["look"] = look(R)
grab("twoj-ruch-skrypt")
""", full_data(self.data, consent_items(None, SCRIPT)))
        self.assertEqual(len(out["look"]["script"]["lines"]), 16)
        self.assertEqual(out["look"]["primary"], ["Wybierz"])


if __name__ == "__main__":
    unittest.main()
