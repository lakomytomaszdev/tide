"""Odbiór rdzenia K-99 (PT-472) w Tablicy na ZAPISANYCH danych z #21 (apps/board/tests/data/k99_acceptance/) i kontrole 13.11.

Nie powtarza testów zgody, zmiany celu, Escape i przewijania (PT-390, PT-391, PT-403) ani PT-401; sprawdza to,
czego one nie dają:

- „Scal…” w panelu karty otwiera okno z odpowiedzią `preview_card_merge.json` (podaną przez atrapę backendu Tablicy):
  liczby commitów i plików w sekcjach PT-403 równe odpowiedzi i `capture.json`, stan zdaniem zgodny z `canMerge: false`,
  powody blokad zdaniami, „Scal do” nieaktywne; przy konflikcie brak przycisku zlecenia konfliktów i jego właściwości
  w oknie i na stronie Tablicy (sondy wyłącznie przez publiczne `objectName` komponentu, prefiks `mergeWindow`),
- wyszukiwanie `Zleć rozwiązanie|conflictTask|CreateCard` w `apps/worktrees/` i `BoardPage.qml`: tylko komentarze,
- „Scalono do <cel> (<data czas>)” w panelu i pełnej karcie powstaje z `shortRef`/`shortDate` (git/start.js): cel lokalny
  i zdalny, bez własnego skracania w CardPanel.qml i CardFullView.qml,
- sondy testów okna scalania z PT-390/391/395/401 odwołują się do okna wyłącznie przez `objectName` z listy komponentu
  (jedyne odstępstwo — nazwy wyboru celu `mergeTarget…` z podkomponentu BranchPicker — jest jawnie wyliczone).
Karta w oknie to karta przykładowa Tablicy (K-6): `capture.json` dotyczy K-99, która jest czynna i nie ma „Scal…”.
"""
import json
import os
import re
import unittest
from datetime import datetime

from . import REPO
from .decisionfix import full_d
from .test_view import FIND_PY, ProbeCase

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "k99_acceptance")
WINDOW_QML = os.path.join(REPO, "apps", "common", "qml", "git", "MergeWindow.qml")
SECTION_QML = os.path.join(REPO, "apps", "common", "qml", "git", "MergeSection.qml")
PICKER_QML = os.path.join(REPO, "apps", "common", "qml", "git", "BranchPicker.qml")


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def reason_sentences():
    """Kod blokady → zdanie z komponentu okna (`reasonSentences`)."""
    block = read("apps", "common", "qml", "git", "MergeWindow.qml")
    block = block[block.index("readonly property var reasonSentences"):]
    return dict(re.findall(r'^\s*"([a-z-]+)":\s*"(.*)",?\s*$', block[:block.index("})")], re.M))


def local_stamp(ts):
    """„d.MM HH:MM” w czasie lokalnym, jak formatter K-36 (shortDate + shortTime)."""
    moment = datetime.fromisoformat(ts).astimezone()
    return f"{moment.day}.{moment.month:02d} {moment.hour:02d}:{moment.minute:02d}"


WALK = ("(function () { const out = {texts: [], names: [], props: [], host: []}; "
        "(function walk(it) { if (!it) return; if (it.objectName) out.names.push(it.objectName); "
        "if (it.visible && typeof it.text === 'string') out.texts.push(it.text); "
        "for (const c of it.children) walk(c); if (it.contentItem) walk(it.contentItem) })(mwin); "
        "for (const k in mwin) out.props.push(k); for (const k in page) out.host.push(k); "
        "return JSON.stringify(out) })()")


class BoardOnSavedDataTest(ProbeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.preview, cls.capture = load("preview_card_merge.json"), load("capture.json")
        cls._window = None

    def closed_card(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "review", "flow": {
                    "closed": True, "summary": "Gotowe", "merged": False, "mainMerged": False,
                    "mainBranch": "master", "cardBranch": "card/k-6-eksport",
                    "base": {"status": "known", "baseRef": "refs/heads/master"},
                    "plan": {"status": "done", "rows": [], "waves": [], "revision": {}}}}
                card["boardColumn"] = "review"
        return data

    def window(self):
        """Jedna sonda na klasę: „Scal…” z panelu, odpowiedź z pliku, odczyt sekcji przez publiczne objectName."""
        if type(self)._window is None:
            type(self)._window = self.probe(FIND_PY + "\nPREVIEW = json.loads(" + repr(json.dumps(self.preview)) + ")\n"
                                            "WALK = " + repr(WALK) + r'''
click(card("K-6"))
replies.append(PREVIEW)
click(at(FIND + "(page.cardPanel, 'cardMerge')"))
until(lambda: not obj("mergeWindow").property("busy"))
engine.rootContext().setContextProperty("mwin", obj("mergeWindow"))
def section(name):
    engine.rootContext().setContextProperty("probeSection", obj(name))
    return json.loads(js("JSON.stringify({title: probeSection.title, rows: probeSection.rows, "
                         "visible: probeSection.visible})"))
out["opened"] = obj("mergeWindow").property("opened")
out["calls"] = calls()
out["state"] = obj("mergeWindowState").property("text")
out["commits"] = section("mergeWindowCommits")
out["files"] = section("mergeWindowFiles")
out["conflicts"] = section("mergeWindowConflicts")
out["blocked"] = section("mergeWindowBlocked")
out["merge"] = [obj("mergeWindowMerge").property("text"), obj("mergeWindowMerge").property("enabled")]
out["target"] = obj("mergeWindowTarget").property("text")
out["walk"] = json.loads(js(WALK))
''', self.closed_card())
        return type(self)._window

    def test_panel_merge_opens_the_window_with_the_saved_response(self):
        out, preview, capture = self.window(), self.preview, self.capture
        self.assertTrue(out["opened"])
        self.assertEqual([(c[0], c[1], json.loads(c[2])) for c in out["calls"]],
                         [("PreviewCardMerge", "K-6", {"targetRef": preview["targetRef"]})])
        self.assertEqual((preview["fileCount"], preview["commitCount"]),
                         (capture["selection"]["fileCount"], capture["preview"]["commitCount"]))
        self.assertEqual(out["commits"]["title"], f"Wnoszone commity ({preview['commitCount']})")
        self.assertEqual(len(out["commits"]["rows"]), preview["commitCount"])
        self.assertEqual(out["files"]["title"], f"Pliki ({preview['fileCount']})")
        self.assertEqual(len(out["files"]["rows"]), preview["fileCount"])
        self.assertTrue(out["commits"]["visible"] and out["files"]["visible"])
        self.assertTrue(out["commits"]["rows"][0].startswith(preview["commits"][0]["sha"][:7]))
        self.assertFalse(preview["canMerge"])
        conflicts = len(preview["conflicts"]) or preview["conflictCount"]
        self.assertGreater(conflicts, 0, "zapisana odpowiedź ma konflikty")
        self.assertEqual(out["state"], "Konflikt w 1 pliku" if conflicts == 1 else f"Konflikty w {conflicts} plikach")
        self.assertEqual(out["conflicts"]["title"], f"Konflikty ({preview['conflictCount']})")
        self.assertEqual(out["conflicts"]["rows"], preview["conflicts"])
        sentences = reason_sentences()
        codes = []
        for blocked in preview["blockedReasons"]:
            if blocked["reason"] not in codes:
                codes.append(blocked["reason"])
        self.assertEqual(out["blocked"]["rows"], [sentences[c] for c in codes])
        self.assertEqual(out["target"], "K-6 → master")
        self.assertEqual(out["merge"], ["Scal do master", False], "„Scal do” nieaktywne przy canMerge: false")
        self.assertNotIn("MergeCard", [c[0] for c in out["calls"]])

    def test_conflict_preview_has_no_conflict_task_button_or_properties_in_the_board_host(self):
        walk = self.window()["walk"]
        self.assertIsNone(re.search(r"Zleć|zleć|Zlec", "\n".join(walk["texts"])), "brak przycisku zlecenia konfliktów")
        self.assertIn("cardId", walk["props"], "wyliczenie właściwości okna działa")
        self.assertIn("fullOpen", walk["host"], "wyliczenie właściwości strony Tablicy działa")
        self.assertIn("mergeWindowConflicts", walk["names"])
        for name in walk["names"] + walk["props"] + walk["host"]:
            self.assertIsNone(re.search(r"conflictTask|ConflictTask|taskRequested", name), name)

    def merged_data(self, merge):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                flow = item["derived"]["flow"]
                flow.update(mainBranch="master", base={"status": "known", "baseRef": "refs/heads/master"},
                            closed=True, summary="Gotowe", merged=True, mainMerged=True, merge=merge)
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "done"
        return data

    def test_merged_line_in_panel_and_full_card_comes_from_the_k36_formatter(self):
        ts = "2026-11-11T18:02:00+00:00"
        cases = [{"targetRef": "refs/heads/master", "targetBranch": "master", "ts": ts},
                 {"targetRef": "refs/remotes/origin/develop", "targetBranch": "origin/develop", "ts": ts},
                 {"targetBranch": "release/1.0", "ts": ts},
                 {"targetRef": "refs/heads/master", "ts": ""}]
        out = self.probe("CASES = " + repr(cases) + r'''
def line(name):
    item = obj(name)
    return [item.property("visible"), item.property("text")]
def with_merge(case):
    snap = json.loads(backend.snapshotJson)
    next(c for c in snap["cards"] if c["id"] == "K-8")["derived"]["flow"]["merge"] = case
    set_snapshot(snap)
out["panel"], out["full"] = [], []
js('page.openCard("K-8")')
wait()
for case in CASES:
    with_merge(case)
    out["panel"].append(line("cardMerged"))
js('page.closePanel(); page.openFull("K-8")')
wait()
for case in CASES:
    with_merge(case)
    out["full"].append(line("fullMerged"))
''', self.merged_data(None))
        stamp = local_stamp(ts)
        expected = [f"Scalono do master ({stamp})", f"Scalono do origin/develop ({stamp})",
                    f"Scalono do release/1.0 ({stamp})", "Scalono do master"]
        self.assertEqual(out["panel"], [[True, text] for text in expected])
        self.assertEqual(out["full"], [[True, text] for text in expected])


class SearchTest(unittest.TestCase):
    """Wyszukiwania z notki: brak własnego skracania w „Scalono do…” i brak zlecenia konfliktów w gospodarzach."""

    def source_lines(self, *paths, pattern):
        found = []
        for path in paths:
            for number, line in enumerate(read(*path).splitlines(), 1):
                if re.search(pattern, line):
                    found.append((os.path.join(*path), number, line.strip()))
        return found

    def test_merged_line_uses_the_shared_formatter_without_own_shortening(self):
        panel, full = ("apps", "board", "qml", "CardPanel.qml"), ("apps", "common", "qml", "card", "CardFullView.qml")
        for path in (panel, full):
            code = [h for h in self.source_lines(path, pattern=r"refs/heads/|refs/remotes/|toLocale")
                    if not h[2].startswith("//")]
            self.assertEqual(code, [], path)
            self.assertIn("Start.mergedLine(", read(*path), path)
            self.assertRegex(read(*path), r'import "[./a-z]*git/start\.js" as Start')
            text = read(*path).splitlines()
            near = [i for i, line in enumerate(text) if re.search(r'"cardMerged"|"fullMerged"|Start\.mergedLine', line)]
            for hit in self.source_lines(path, pattern=r"replace\("):
                self.assertTrue(all(abs(hit[1] - 1 - i) > 15 for i in near),
                                f"własne replace( przy napisie „Scalono do…”: {hit}")
        formatter = read("apps", "common", "qml", "git", "start.js")
        body = formatter[formatter.index("function mergedLine"):]
        self.assertIn("shortRef(", body[:body.index("\n}\n")])
        self.assertIn("shortDate(", body[:body.index("\n}\n")])

    def test_no_conflict_task_in_worktrees_and_board_page_except_comments(self):
        pattern = r"Zleć rozwiązanie|conflictTask|CreateCard"
        hits = []
        base = os.path.join(REPO, "apps", "worktrees")
        for folder, _dirs, files in os.walk(base):
            for name in sorted(files):
                if name.endswith((".py", ".qml", ".js")) and os.sep + "tests" not in folder:
                    path = os.path.relpath(os.path.join(folder, name), REPO)
                    hits += self.source_lines(tuple(path.split(os.sep)), pattern=pattern)
        hits += self.source_lines(("apps", "board", "qml", "BoardPage.qml"), pattern=pattern)
        for path, number, line in hits:
            self.assertTrue(line.startswith(("//", "#")), f"{path}:{number}: kod, nie komentarz: {line}")
            self.assertIn("Bez", line, f"{path}:{number}: komentarz musi mówić, że zlecenia nie ma: {line}")


class ProbeNamesTest(unittest.TestCase):
    """Sondy testów okna scalania (PT-390, PT-391, PT-395, PT-401) przez publiczne `objectName` komponentu."""

    AUDITED = (("apps", "board", "tests", "test_merge_window.py"), ("apps", "board", "tests", "test_merge_targets.py"),
               ("apps", "board", "tests", "test_card_full_view.py"), ("apps", "board", "tests", "test_view.py"),
               ("apps", "worktrees", "tests", "test_merge.py"), ("apps", "worktrees", "tests", "mergeprobe.py"))
    # Wybór celu to podkomponent BranchPicker (apps/common/qml/git/BranchPicker.qml, wspólny z wyborem bazy): jego
    # nazwy nie mają prefiksu `mergeWindow`, a komponent okna nie ma publicznego odpowiednika dla wiersza skrótu,
    # wyniku wyszukiwania, pola szukania i odświeżenia — to odstępstwo wymaga decyzji planu (nowe nazwy w komponencie).
    PICKER_EXEMPT = {"mergeTargetShortcut", "mergeTargetResult", "mergeTargetSearch", "mergeTargetRefresh",
                     "mergeNewTargetName"}

    def public_names(self):
        window = read("apps", "common", "qml", "git", "MergeWindow.qml")
        names = set(re.findall(r'objectName:\s*"(mergeWindow\w*)"', window))
        sections = {n for n in names if re.search(r'MergeSection \{\s*objectName: "%s"' % n, window)}
        self.assertTrue(sections, "komponent ma sekcje")
        # Sekcja MergeSection wystawia `<nazwa>Title` i `<nazwa>List` (MergeSection.qml).
        self.assertIn('section.objectName + "Title"', read("apps", "common", "qml", "git", "MergeSection.qml"))
        self.assertIn('section.objectName + "List"', read("apps", "common", "qml", "git", "MergeSection.qml"))
        return names | {n + "Title" for n in sections} | {n + "List" for n in sections}

    def literal_names(self, path):
        text = read(*path)
        return sorted(set(re.findall(r"[\"'](mergeWindow\w*)[\"']", text)))

    def test_every_probed_merge_window_name_is_a_public_object_name(self):
        public = self.public_names()
        self.assertIn("mergeWindowMerge", public)
        for path in self.AUDITED:
            self.assertEqual([n for n in self.literal_names(path) if n not in public], [], os.path.join(*path))

    def test_only_branch_picker_names_are_probed_outside_the_window_prefix(self):
        picker = set(re.findall(r'"(mergeTarget\w*|mergeNewTarget\w*)"', read("apps", "common", "qml", "git",
                                                                              "BranchPicker.qml")))
        self.assertTrue({"mergeTargetSearch", "mergeTargetRefresh", "mergeNewTargetName"} <= picker)
        used = set()
        for path in self.AUDITED:
            for name in re.findall(r"[\"'](merge(?:Target|NewTarget)[A-Za-z]*)\d*[\"']", read(*path)):
                used.add(name)
        self.assertEqual(sorted(used - self.PICKER_EXEMPT), [], "nowa sonda spoza listy komponentu")
        self.assertTrue(used, "audyt widzi sondy wyboru celu")


if __name__ == "__main__":
    unittest.main()
