"""Odbiór rdzenia K-99 (PT-472) w oknie Worktrees na ZAPISANYCH danych z #21 (apps/board/tests/data/k99_acceptance/).

Nie powtarza testów PT-388 (tryb, trwałość), PT-395 (zgoda, Escape, zmiana celu, historia 37) i PT-403 (komponent okna);
sprawdza tylko to, czego atrapy tamtych testów nie dają:

- mapa z `branchmap.json` (≥ 40 gałęzi) w trybie Gałęzie: każdy węzeł ma tytuł i stan zgodny z danymi, karta z `capture.json`
  wisi pod swoją bazą, a „Scal…” mają dokładnie karty domknięte i niescalone,
- „Scal…” na mapie otwiera okno z odpowiedzią `preview_card_merge.json` (podaną przez atrapę backendu): liczby commitów
  i plików w sekcjach PT-403 równe odpowiedzi i `capture.json`, stan zdaniem zgodny z `canMerge: false`, powody blokad
  zdaniami, „Scal do” nieaktywne,
- kontrole 13.11 w gospodarzu: przy podglądzie z konfliktem okno nie ma przycisku zlecenia konfliktów ani jego
  właściwości, a backend nie ma `CreateCard` na liście wywołań `/Board`.
Okno sondowane wyłącznie przez publiczne `objectName` komponentu (prefiks `mergeWindow`) i sondy mapy z testów PT-388/395.

Dane są prawdziwe, ale karta w oknie jest karta domknięta z mapy (K-99 jest czynna, więc nie ma „Scal…”), a odpowiedź
podglądu to zapisana odpowiedź dla K-99 (karta mogła zostać wybrana krokiem 3 reguły #21 — to nie jest usterka).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from . import REPO
from .test_diff_actions import PROBE, rows
from .test_merge import HARNESS, HELPERS, QML_HELPERS
from .test_window import QML_PROBLEMS, load_launcher, stage

DATA = os.path.join(REPO, "apps", "board", "tests", "data", "k99_acceptance")
WINDOW_QML = os.path.join(REPO, "apps", "common", "qml", "git", "MergeWindow.qml")
COLUMNS = {"backlog": "Backlog", "todo": "Do zrobienia", "progress": "W toku", "waiting": "Czeka na mnie",
           "review": "Do sprawdzenia", "done": "Gotowe", "archived": "Archiwum"}
PROJECTS = {"ok": True, "projects": [{"slug": "plasma-tide", "name": "plasma-tide", "repo": "x", "cards": 37}]}


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def short_ref(ref):
    for prefix in ("refs/heads/", "refs/remotes/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def short_date(text):
    """d.MM jak formatter K-36 (czas lokalny; sondy działają z TZ=UTC)."""
    if not text:
        return ""
    moment = datetime.fromisoformat(text).astimezone(timezone.utc)
    return f"{moment.day}.{moment.month:02d}"


def expected_title(node):
    if node["kind"] == "base":
        return short_ref(node["ref"]) or node["branch"] or node["id"]
    if node["kind"] == "unknown-base":
        return node.get("title") or "Nieznana baza"
    ident = node["card"] if node["kind"] == "card" else node["ticket"]
    return f"{ident} · {node['title']}" if node.get("title") else ident


def expected_state(node):
    merged = node.get("merged")
    parts = []
    if merged:
        if merged.get("targetRef"):
            parts.append("do " + short_ref(merged["targetRef"]))
        if short_date(merged.get("date")):
            parts.append("(" + short_date(merged["date"]) + ")")
    suffix = " ".join(parts)
    if node["kind"] == "card":
        if node["state"] == "merged":
            return "scalona" + (" " + suffix if suffix else "")
        return "domknięta" if node["state"] == "closed" else "w toku"
    return {"merged": "scalony", "rejected": "odrzucony", "closed": "zamknięty"}.get(node["state"], "w toku")


def expected_detail(node):
    if node["kind"] in ("base", "unknown-base"):
        return ""
    parts = [COLUMNS.get(node["column"], node["column"])] if node["kind"] == "card" and node.get("column") else []
    return " · ".join(parts + [expected_state(node)])


def reason_sentences():
    """Zdania blokad z komponentu okna (`reasonSentences`) — kod → zdanie, do porównania z tym, co okno pokazuje."""
    with open(WINDOW_QML, encoding="utf-8") as fh:
        source = fh.read()
    block = source[source.index("readonly property var reasonSentences"):]
    block = block[:block.index("})")]
    return dict(re.findall(r'^\s*"([a-z-]+)":\s*"(.*)",?\s*$', block, re.M))


PREAMBLE = r'''
PROJECTS = json.loads(%(projects)r)
TREE = json.loads(%(tree)r)
PREVIEW = json.loads(%(preview)r)
LISTING = json.loads(%(listing)r)

def section(name):
    """Tytuł, wiersze i widoczność sekcji podglądu po publicznym objectName (także niewidocznej)."""
    text = js("(function () { const all = " + ALL.replace("it.visible", "true") + "; "
              "const it = all(loader.item, " + json.dumps(name) + ")[0]; return it ? JSON.stringify("
              "{title: it.title, rows: it.rows, visible: it.visible}) : '' })()")
    return json.loads(text) if text else None

def pick(prefix):
    """Zaznacza kartę, której tytuł zaczyna się od `prefix` (np. "K-78 "): przewija drzewo (300 wierszy nie mieści
    się w oknie) i klika węzeł; zwraca ID zaznaczonej karty ze strony (`selectedCard`) albo "" (klik nie trafił)."""
    names = titles()
    index = next(i for i, t in enumerate(names) if t.startswith(prefix))
    js("(function () { const all = " + ALL + "; const list = all(loader.item, 'branchNode').sort((x, y) => "
       "x.mapToItem(loader.item, 0, 0).y - y.mapToItem(loader.item, 0, 0).y); const it = list[" + str(index) + "]; "
       "let flick = it.parent; while (flick && flick.contentY === undefined) flick = flick.parent; "
       "flick.contentY = Math.max(0, it.mapToItem(flick.contentItem, 0, 0).y - 120) })()")
    wait(80)
    click("branchNode", index)
    return page("selectedCard")

def window_names():
    """Wszystkie objectName okna scalania (także niewidoczne i w treści kontrolek) i nazwy właściwości okna."""
    return json.loads(js("(function () { const names = []; const win = " + ALL.replace("it.visible", "true")
        + "(loader.item, 'mergeWindow')[0]; (function walk(it) { if (!it) return; if (it.objectName) "
        "names.push(it.objectName); for (const c of it.children) walk(c); if (it.contentItem) walk(it.contentItem) })(win); "
        "const props = []; for (const k in win) props.push(k); "
        "const host = " + ALL.replace("it.visible", "true") + "(loader.item, 'branchesPage')[0]; const hostProps = []; "
        "for (const k in host) hostProps.push(k); "
        "return JSON.stringify({names: names, props: props, hostProps: hostProps}) })()"))
'''


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class AcceptanceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree, cls.listing = load("branchmap.json"), load("list_card_branches.json")
        cls.preview, cls.capture = load("preview_card_merge.json"), load("capture.json")
        cls.nodes = {n["id"]: n for n in cls.tree["nodes"]}

    def probe(self, scenario: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="tide-wt-k99-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            qml = os.path.join(pkg, "views", "worktrees", "qml")
            with open(os.path.join(qml, "Harness.qml"), "w", encoding="utf-8") as fh:
                fh.write(HARNESS % {"list": json.dumps(json.dumps(rows(), ensure_ascii=False))})
            script = os.path.join(tmp, "scenario.py")
            data = PREAMBLE % {"projects": json.dumps(PROJECTS), "tree": json.dumps(self.tree),
                               "preview": json.dumps(self.preview), "listing": json.dumps(self.listing)}
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(data + HELPERS + QML_HELPERS + scenario)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TZ="UTC")
            result = subprocess.run(["/usr/bin/python3", PROBE, qml, script], env=env, capture_output=True,
                                    text=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out


class MapTest(AcceptanceCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cards = [n["card"] for n in cls.tree["nodes"] if n["kind"] == "card"]
        cls._out = None

    def run_map(self):
        """Jedna sonda na całą klasę (okno z mapą ≥ 40 gałęzi startuje kilka sekund)."""
        if type(self)._out is None:
            type(self)._out = self.probe_map()
        return type(self)._out

    def probe_map(self):
        return self.probe('''
show(TREE)
out["titles"] = titles()
out["details"] = field("branchNode", "it.detailText")
out["indent"] = field("branchNode", "it.x")
out["truncated"] = field("branchTruncated", "it.text")
merge, selected = {}, {}
for number in CARDS:
    selected[number] = pick(number + " ")
    merge[number] = field("branchMerge", "it.text")
out["merge"] = merge
out["selected"] = selected
'''.replace("CARDS", repr(self.cards)))

    def test_saved_map_shows_every_node_with_title_and_state_from_the_data(self):
        out = self.run_map()
        nodes = self.tree["nodes"]
        self.assertGreaterEqual(self.tree["total"], 40)
        self.assertEqual(out["truncated"], [], "mapa nieobcięta: bez „pokazano N z M”")
        self.assertEqual(len(out["titles"]), len(nodes), "jeden wiersz drzewa na węzeł odpowiedzi")
        by_title = dict(zip(out["titles"], out["details"]))
        self.assertEqual(len(by_title), len(nodes), "tytuły wierszy są unikalne")
        for node in nodes:
            self.assertEqual(by_title.get(expected_title(node)), expected_detail(node), node["id"])

    def test_card_from_capture_hangs_under_its_base_with_its_tickets_and_has_no_merge_button(self):
        out = self.run_map()
        card = self.nodes["card:" + self.capture["card"]]
        base = "base:" + card["started"]["ref"]
        self.assertIn({"kind": "start", "from": base, "to": card["id"]}, self.tree["edges"], "dane: karta pod bazą")
        titles, indent = out["titles"], out["indent"]
        at = titles.index(expected_title(card))
        self.assertEqual(indent[at], 24, "karta jest o jeden poziom pod bazą")
        parent = max(i for i in range(at) if indent[i] == 0)
        self.assertEqual(titles[parent], short_ref(card["started"]["ref"]), "najbliższy wiersz bazy to baza karty")
        children = [self.nodes[e["to"]] for e in self.tree["edges"] if e["kind"] == "start" and e["from"] == card["id"]]
        below = []
        for i in range(at + 1, len(titles)):
            if indent[i] <= 24:
                break
            if indent[i] == 48:
                below.append(titles[i])
        self.assertEqual(sorted(below), sorted(expected_title(c) for c in children))
        self.assertEqual(card["state"], "active", "karta K-99 w danych jest czynna, nie domknięta")
        self.assertEqual(out["selected"][card["card"]], card["card"], "karta K-99 była zaznaczona")
        self.assertEqual(out["merge"][card["card"]], [], "czynna karta nie ma „Scal…”")

    def test_merge_button_only_for_closed_unmerged_cards(self):
        out = self.run_map()
        closed = [n["card"] for n in self.tree["nodes"] if n["kind"] == "card" and n["state"] == "closed"]
        self.assertGreaterEqual(len(closed), 1, "dane mają domknięte karty")
        expected = {n["card"]: ["Scal…"] if n["state"] == "closed" else []
                    for n in self.tree["nodes"] if n["kind"] == "card"}
        self.assertEqual(out["merge"], expected)
        self.assertEqual(out["selected"], {c: c for c in self.cards}, "każdy klik zaznaczył swoją kartę")


class WindowTest(AcceptanceCase):
    """„Scal…” z mapy → okno z zapisaną odpowiedzią `preview_card_merge.json` (`canMerge: false`, konflikty)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.card = next(n["card"] for n in cls.tree["nodes"] if n["kind"] == "card" and n["state"] == "closed")
        cls._out = None

    def run_window(self):
        """Jedna sonda na całą klasę."""
        if type(self)._out is None:
            type(self)._out = self.probe_window()
        return type(self)._out

    def probe_window(self):
        return self.probe('''
show(TREE)
out["selected"] = pick(CARD + " ")
click("branchMerge")
wait(80)
out["opened"] = opened()
out["list"] = of("ListCardBranches")
list_branches(LISTING)
out["preview"] = of("PreviewCardMerge")
preview(PREVIEW)
out["state"] = item("mergeWindowState")["text"]
out["commits"] = section("mergeWindowCommits")
out["files"] = section("mergeWindowFiles")
out["conflicts"] = section("mergeWindowConflicts")
out["blocked"] = section("mergeWindowBlocked")
out["merge"] = item("mergeWindowMerge")
out["target"] = item("mergeWindowTarget")["text"]
out["texts"] = window_texts()
out["internals"] = window_names()
out["merges"] = of("MergeCard")
out["creates"] = of("CreateCard")
'''.replace("CARD", repr(self.card)))

    def test_sections_show_the_saved_response_numbers_state_reasons_and_merge_is_inactive(self):
        out = self.run_window()
        preview, capture = self.preview, self.capture
        self.assertEqual(out["selected"], self.card)
        self.assertTrue(out["opened"])
        self.assertEqual([c[2] for c in out["list"]], [self.card])
        self.assertEqual([json.loads(c[3]) for c in out["preview"]], [{"targetRef": self.listing["defaultTargetRef"]}])
        # Liczby: odpowiedź, `capture.json.preview` i `capture.json.selection` mówią to samo.
        self.assertEqual((preview["fileCount"], preview["commitCount"]),
                         (capture["selection"]["fileCount"], capture["preview"]["commitCount"]))
        self.assertEqual(out["commits"]["title"], f"Wnoszone commity ({preview['commitCount']})")
        self.assertEqual(len(out["commits"]["rows"]), preview["commitCount"])
        self.assertEqual(out["files"]["title"], f"Pliki ({preview['fileCount']})")
        self.assertEqual(len(out["files"]["rows"]), preview["fileCount"])
        self.assertTrue(out["commits"]["visible"] and out["files"]["visible"])
        self.assertTrue(out["commits"]["rows"][0].startswith(preview["commits"][0]["sha"][:7]))
        # Stan zdaniem zgodny z `canMerge: false` i konfliktami z odpowiedzi.
        self.assertFalse(preview["canMerge"])
        conflicts = len(preview["conflicts"]) or preview["conflictCount"]
        self.assertGreater(conflicts, 0, "zapisana odpowiedź ma konflikty")
        self.assertEqual(out["state"], "Konflikt w 1 pliku" if conflicts == 1 else f"Konflikty w {conflicts} plikach")
        self.assertEqual(out["conflicts"]["title"], f"Konflikty ({preview['conflictCount']})")
        self.assertEqual(out["conflicts"]["rows"], preview["conflicts"])
        # Powody blokad zdaniami PT-403, każdy raz, w kolejności odpowiedzi.
        sentences = reason_sentences()
        codes = []
        for blocked in preview["blockedReasons"]:
            if blocked["reason"] not in codes:
                codes.append(blocked["reason"])
        self.assertEqual(out["blocked"]["rows"], [sentences[c] for c in codes])
        self.assertEqual(out["blocked"]["title"], "Dlaczego nie można scalić")
        # „Scal do” nieaktywne, a okno nie wysłało zgody.
        self.assertEqual(out["target"], f"{self.card} → {short_ref(preview['targetRef'])}")
        self.assertEqual((out["merge"]["text"], out["merge"]["enabled"]), ("Scal do master", False))
        self.assertEqual(out["merges"], [])

    def test_conflict_preview_has_no_conflict_task_button_and_the_backend_has_no_create_card(self):
        out = self.run_window()
        self.assertGreater(self.preview["conflictCount"], 0)
        joined = "\n".join(out["texts"])
        self.assertIsNone(re.search(r"Zleć|zleć|Zlec", joined), "okno nie ma przycisku zlecenia konfliktów")
        internals = out["internals"]
        self.assertIn("cardId", internals["props"], "wyliczenie właściwości okna działa")
        self.assertIn("treeRows", internals["hostProps"], "wyliczenie właściwości strony działa")
        self.assertIn("mergeWindowConflicts", internals["names"])
        for name in internals["names"] + internals["props"] + internals["hostProps"]:
            self.assertIsNone(re.search(r"conflictTask|ConflictTask|createCard|CreateCard|taskRequested", name), name)
        self.assertEqual(out["creates"], [], "okno nie wywołało CreateCard")
        module = load_launcher()
        self.assertNotIn("CreateCard", module.BOARD_CALLS)
        backend = module.Backend(None)
        replies = []
        backend.boardReplied.connect(lambda tag, text: replies.append((tag, json.loads(text)["error"])))
        backend._board("k99:1", "CreateCard", ['{"title":"konflikty"}'])
        self.assertEqual(replies, [("k99:1", "not-allowed")])


if __name__ == "__main__":
    unittest.main()
