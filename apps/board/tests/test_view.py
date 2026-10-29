"""Widok Tablica okna Tide.

- pakiet okna Tide: moduł widoku w views/board (backend.py, QML, board.js i biblioteka z Tokens.qml);
  bez kolorów wpisanych na sztywno, teksty jako zwykły tekst,
- logika (board.js) w QJSEngine: kolumny, filtry, pozycja dla MoveCard, przeniesienia „w locie”,
  podsumowanie z liczbą mnogą, plakietka, notatki, ramka „Dane tablicy”, żądania z formularza,
- sonda w oknie offscreen (probe.py): przeciąganie myszą między kolumnami i w kolumnie (MoveCard),
  kolumny wyliczane nie przyjmują karty, nowa karta i edycja (CreateCard, UpdateCard tylko ze zmianami),
  archiwizacja, błąd usługi w panelu, commitError, filtry tagów, projektu i szukanie, pusty stan,
  pasek błędów plików, showItem przed migawką, klawiatura (Ctrl+N, Esc, Ctrl+→), nowy tag, projekty,
- zrzuty z --data bez ostrzeżeń QML,
- backend na prywatnej magistrali z prawdziwym tide-daemon i tymczasową tablicą (--board-dir):
  zapis, migawka po zapisie, błąd w kształcie {ok: false, error, message}, plakietka,
- plan i specyfikacja w panelu karty (PT-5): logika zakładek (cardDocs.js), zakładki tylko przy
  dokumentach, tabela ticketów, Markdown, „Co przetestować ręcznie”, szerszy panel i „Rozwiń”,
  odświeżenie po Changed, „Otwórz plik” / „Pokaż w katalogu” / linki tylko http(s), CardDocs na
  prywatnej magistrali,
- okno uruchomienia jak „Nowe zadanie” (PT-43): zrzuty ekranu (przeciągnięte, Ctrl+V z obrazem i z tekstem,
  „×”, atrapa Spectacle z chowaniem okna, Anuluj sprząta katalog) w `images` prośby Start, model chmura/lokalny
  z roli `agent` (wyłączony z powodem, linia agenta, gałąź za programem, blokada przy niedostępnym modelu
  i braku programu), Defaults i /Models z prawdziwej usługi.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import APP_DIR, REPO
from .boardfix import build_snapshot, empty_snapshot, git_env, preview_data
from daemon.tests.fakebin import files_under
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Cannot read|unavailable|Invalid property|Binding loop|failed to load|Only binding to one"
TIDE_DIR = os.path.join(REPO, "apps", "tide")
PROBE = os.path.join(APP_DIR, "tests", "probe.py")


def stage(out: str) -> str:
    """Pakiet okna Tide z widokiem Tablica w views/board."""
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", TIDE_DIR, out], check=True)
    return out


def offscreen(base=None) -> dict:
    return dict(os.environ if base is None else base, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")


class PackageTest(unittest.TestCase):
    def test_package_contains_view_module_and_library(self):
        with tempfile.TemporaryDirectory(prefix="tide-board-pkg-") as tmp:
            files = files_under(os.path.join(stage(os.path.join(tmp, "pkg")), "views", "board"))
        for name in ("backend.py", "qml/BoardView.qml", "qml/BoardPage.qml", "qml/BoardColumn.qml", "qml/BoardCard.qml",
                     "qml/CardPanel.qml", "qml/ManagePage.qml", "qml/board.js", "qml/tide/Tokens.qml",
                     "qml/tide/Theme.qml", "qml/CardDocView.qml", "qml/MarkdownBlock.qml", "qml/cardDocs.js",
                     "taskimages.py", "qml/tide/ScreenshotStrip.qml", "qml/tide/agentmodel.js"):
            self.assertIn(name, files)   # PT-43: zrzuty i model wspólne z oknem „Nowe zadanie”
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])

    def test_no_hardcoded_colors_and_plain_texts(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                source = fh.read()
            self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', source), [], name)
            self.assertNotIn("StyledText", source, name)
            if name != "MarkdownBlock.qml":  # jedyny wyjątek: HTML z backend.renderMarkdown (bez obrazów i HTML ze źródła)
                self.assertNotIn("RichText", source, name)
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", source, re.S):
                self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")

    def test_registered_as_default_view(self):
        with open(os.path.join(TIDE_DIR, "views.json"), encoding="utf-8") as fh:
            views = {v["id"]: v for v in json.load(fh)["views"]}
        self.assertEqual((views["board"]["module"], views["board"]["qml"], views["board"].get("default")),
                         ("apps/board", "BoardView.qml", True))
        self.assertEqual([v for v in views if views[v].get("default")], ["board"])


class LogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        with open(os.path.join(APP_DIR, "qml", "board.js"), encoding="utf-8") as fh:
            source = fh.read().replace(".pragma library", "")
        result = cls.engine.evaluate(source)
        assert not result.isError(), result.toString()
        cls.tmp = tempfile.mkdtemp(prefix="tide-board-logic-")
        cls.snap = build_snapshot(cls.tmp, derived=True)
        cls.engine.globalObject().setProperty("S", cls.engine.toScriptValue(json.dumps(cls.snap)))
        cls.engine.evaluate("S = JSON.parse(S)")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def js(self, expression):
        value = self.engine.evaluate(f"JSON.stringify({expression})")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def ids(self, expression):
        return self.js(f"({expression}).map(c => c.id)")

    def test_columns_and_filters(self):
        self.assertEqual([c["id"] for c in self.js("columns(S)")],
                         ["backlog", "todo", "progress", "waiting", "review", "done"], "archiwum ukryte")
        self.assertEqual([c["manual"] for c in self.js("columns(S)")], [True, True, False, False, False, True])
        self.assertEqual(self.ids("column(visible(S, null, null), 'backlog')"), ["K-15", "K-14", "K-13", "K-12", "K-10"])
        self.assertEqual(self.ids("column(visible(S, null, null), 'progress')"), ["K-6", "K-5"], "kolumna z koordynatora")
        self.assertEqual(self.ids("visible(S, {tag: 'PILNE'}, null)"), ["K-12", "K-7"], "tag bez wielkości liter")
        self.assertEqual(self.ids("visible(S, {tag: 'Firma'}, null)").count("K-13"), 1, "tag z projektu")
        self.assertEqual(self.ids("visible(S, {query: 'pdf mail'}, null)"), ["K-13"], "słowa w tytule i opisie")
        self.assertEqual(self.ids("visible(S, {query: 'tom-146'}, null)"), ["K-8"], "notatka koordynatora")
        self.assertEqual(self.ids("visible(S, {project: 'cookapp'}, null)"), ["K-10"])
        self.assertEqual(self.ids("visible(S, {project: NO_PROJECT}, null)"), [])
        self.assertTrue(self.js("draggable(S.cards.find(c => c.id === 'K-15'))"))
        self.assertFalse(self.js("draggable(S.cards.find(c => c.id === 'K-5'))"), "kartę prowadzi koordynator")

    def test_confirming_review_cards_and_agent_target(self):
        k7, k5, k15 = ("S.cards.find(c => c.id === '%s')" % i for i in ("K-7", "K-5", "K-15"))
        self.assertTrue(self.js(f"confirmable({k7})"), "Do sprawdzenia — potwierdzasz przeniesieniem")
        self.assertTrue(self.js(f"draggable({k7})"))
        self.assertEqual(self.js(f"dropTargets({k7})"), ["done", "todo"])
        self.assertFalse(self.js(f"draggable({k5})"), "W toku dalej nie")
        self.assertEqual(self.js(f"dropTargets({k5})"), [])
        self.assertEqual(self.js(f"dropTargets({k15})"), ["backlog", "todo", "done"])
        self.assertTrue(self.js("confirmable({column: 'todo', boardColumn: 'done', derived: {column: 'done'}})"))
        self.assertFalse(self.js("confirmable({column: 'done', boardColumn: 'done', derived: {column: 'done'}})"))
        self.assertEqual(self.js(f"agentTarget({k5})"), "claude:5f0c")
        self.assertEqual(self.js("agentTarget({derived: {coordinator: 'codex:019a', session: 'codex:x'}})"), "codex:019a")
        self.assertEqual(self.js("agentTarget({derived: null})"), "")
        self.assertFalse(self.js("isCoordinator(S, 'claude:5f0c')"))
        self.assertTrue(self.js("isCoordinator({cards: [{derived: {coordinator: 'codex:019a'}}]}, 'codex:019a')"))
        self.assertEqual(self.js("agentSession({derived: {session: 'claude:1', coordinator: 'claude:2'}})"), "claude:1")
        self.assertEqual(self.js("agentSession({derived: {session: '', coordinator: 'codex:9'}})"), "codex:9")
        self.assertEqual(self.js("sessionsTarget({derived: {session: 'claude:1', coordinator: ''}})"), "")
        self.assertEqual(self.js("sessionsTarget({derived: {coordinator: 'codex:9'}})"), "codex:9")

    def test_move_index_and_optimistic_moves(self):
        # Kolumna zapisana todo: K-11, K-9, K-8, K-7, K-6, K-5 (cztery ostatnie wyświetlane gdzie indziej).
        self.assertEqual(self.js("moveIndex(S, 'K-15', 'todo', '')"), -1, "na koniec")
        self.assertEqual(self.js("moveIndex(S, 'K-15', 'todo', 'K-9')"), 1)
        self.assertEqual(self.js("moveIndex(S, 'K-15', 'todo', 'K-11')"), 0)
        self.assertEqual(self.js("moveIndex(S, 'K-10', 'backlog', 'K-15')"), 0, "w tej samej kolumnie, bez siebie")
        self.assertEqual(self.js("moveIndex(S, 'K-15', 'backlog', 'K-10')"), 3)
        self.assertEqual(self.js("moveIndex(S, 'K-15', 'done', 'K-4')"), 0)
        self.assertEqual(self.js("storedIndex(S, 'K-9')"), 1)
        moved = "visible(S, null, [{id: 'K-15', column: 'todo', before: 'K-9'}, {id: 'K-10', column: 'backlog', before: 'K-14'}])"
        self.assertEqual(self.ids(f"column({moved}, 'todo')"), ["K-11", "K-15", "K-9"])
        self.assertEqual(self.ids(f"column({moved}, 'backlog')"), ["K-10", "K-14", "K-13", "K-12"])
        self.assertEqual(self.ids("column(visible(S, null, {id: 'K-15', column: 'done', before: ''}), 'done')")[-1], "K-15")
        self.assertNotIn("K-13", self.ids("visible(S, null, {id: 'K-13', column: 'archived'})"))
        self.assertEqual((self.js("neighbourColumn('backlog', 1)"), self.js("neighbourColumn('todo', 1)"),
                          self.js("neighbourColumn('done', 1)"), self.js("neighbourColumn('backlog', -1)")),
                         ("todo", "done", "", ""))

    def test_summary_badge_and_plurals(self):
        self.assertEqual(self.js("summary(visible(S, null, null))"),
                         "11 kart · 2 w toku · 1 czeka na ciebie · 1 do sprawdzenia")
        self.assertEqual(self.js("summary([{column: 'todo'}, {column: 'backlog'}, {column: 'done'}])"),
                         "2 karty · 1 do zrobienia", "bez stanu z koordynatora — ile do zrobienia")
        self.assertEqual(self.js("summary([{column: 'backlog'}])"), "1 karta")
        self.assertEqual(self.js("summary([])"), "0 kart")
        self.assertEqual(self.js("summary([{boardColumn: 'waiting'}, {boardColumn: 'waiting'}])"),
                         "2 karty · 2 czekają na ciebie")
        self.assertEqual([self.js(f"cards({n})") for n in (1, 2, 4, 5, 12, 22, 25)],
                         ["1 karta", "2 karty", "4 karty", "5 kart", "12 kart", "22 karty", "25 kart"])
        self.assertEqual(self.js("badge(S)"), {"text": "● 1", "tone": "waiting"})
        self.assertEqual(self.js("badge({cards: [{column: 'backlog'}, {column: 'done'}]})"), {"text": "1", "tone": "muted"})
        self.assertEqual(self.js("badge({cards: []})"), {"text": "", "tone": "muted"})
        self.assertEqual(self.js("counts(visible(S, null, null)).projects"), 7, "projekty kart w grze (bez Gotowych)")

    def test_card_texts(self):
        k8 = "S.cards.find(c => c.id === 'K-8')"
        self.assertEqual(self.js(f"progress({k8})"), {"done": 5, "total": 9, "ratio": 5 / 9, "text": "5 z 9",
                                                       "short": "5/9"})
        self.assertIsNone(self.js("progress({derived: null})"))
        self.assertEqual(self.js(f"who({k8})"), "Claude · koordynator")
        self.assertEqual(self.js(f"noteTint({k8}, cardNote({k8}))"), "peach")
        self.assertEqual(self.js("noteTint({column: 'backlog'}, '✓ specyfikacja gotowa')"), "mint")
        self.assertEqual(self.js("noteTint({column: 'todo'}, 'bez agenta')"), "textMuted")
        self.assertEqual(self.js("tagColor(S, 'firma')"), "sky")
        self.assertEqual(self.js("doneShown([1,2,3,4,5,6,7,8], false).length"), 6)
        self.assertEqual(self.js("doneShown([1,2,3,4,5,6,7,8], true).length"), 8)
        self.assertEqual(self.js("shortPath('/home/t/DevWorkspace/tide-board', '/home/t')"), "~/DevWorkspace/tide-board")
        self.assertEqual(self.js("shortPath('/home/tomasz/x', '/home/t')"), "/home/tomasz/x")

    def test_launch_entries_and_defaults(self):
        self.assertEqual(self.js("LAUNCH_ENTRIES.map(e => e.id)"), ["planning", "plan", "spec", "now"])
        self.assertEqual(self.js("LAUNCH_MODES.map(m => m.id)"), ["full", "light"], "Tylko specyfikacja nie jest już trybem")
        self.assertEqual(self.js("launchEntry('spec').title"), "Tylko specyfikacja")
        self.assertEqual(self.js("launchEntry('planning').title"), "Planowanie")
        self.assertEqual(self.js("launchEntry('plan').title"), "Mam plan")
        self.assertEqual(self.js("launchEntry('???').id"), "planning")
        self.assertEqual(self.js("launchSteps('spec', 'full')"), self.js("launchEntry('spec').steps"))
        self.assertEqual(self.js("launchSteps('planning', 'light')"), self.js("launchMode('light').steps"))
        for project, want in (({}, ["planning", "full"]), ({"entry": "spec", "mode": "light"}, ["spec", "light"]),
                              ({"mode": "spec"}, ["spec", "full"]), ({"mode": "light"}, ["planning", "light"]),
                              ({"entry": "planning", "mode": "spec"}, ["planning", "full"])):
            got = self.js(f"(function (d) {{ return [d.entry, d.mode] }})(launchDefaults({json.dumps(project)}))")
            self.assertEqual(got, want, project)
        self.assertEqual(self.js("launchDefaults(null).entry"), "planning")

    def test_retry_executor_filters_author_reviewer_and_provider(self):
        status = json.dumps({"executors": EXECUTORS})
        review = "{stepKind:'review', provider:'anthropic', executor:'Opus', authorModel:'sonnet', " \
                 "authorProvider:'anthropic', preferredProvider:'anthropic'}"
        self.assertEqual(self.js(f"retryExecutors({status}, {review}).map(o => o.value)"), ["Haiku"])
        author = "{stepKind:'ticket', provider:'anthropic', executor:'Sonnet', reviewerModel:'opus', " \
                 "reviewerProvider:'anthropic', preferredProvider:'anthropic'}"
        self.assertEqual(self.js(f"retryExecutors({status}, {author}).map(o => o.value)"), ["Haiku"])
        mixed = "{stepKind:'ticket', provider:'anthropic', executor:'Sonnet', reviewerModel:'gpt-6.1-sol', " \
                "reviewerProvider:'openai', preferredProvider:'mixed'}"
        self.assertEqual(self.js(f"retryExecutors({status}, {mixed}).map(o => o.value)"), ["Haiku", "Opus"])

    def test_now_executors_and_flow_card_drop_targets(self):
        status = json.dumps({"executors": EXECUTORS})
        self.assertEqual(self.js(f"launchExecutors({status}).map(o => o.value)"), ["Haiku", "Sonnet", "Opus"])
        self.assertEqual(self.js("launchExecutors(null).map(o => o.value)"), ["Haiku", "Sonnet", "Opus"],
                         "zanim przyjdzie Status() — domyślni wykonawcy Claude")
        self.assertEqual(self.js(f"defaultExecutor(launchExecutors({status}))"), "Sonnet")
        self.assertEqual(self.js("launchSteps('now', 'full')"),
                         ["Ticket w tle", "Review", "Gałąź karty", "Do sprawdzenia"])
        self.assertEqual(self.js("[isFlowEntry('now'), isFlowEntry('planning'), entryHasModes('now')]"),
                         [True, True, False])
        self.assertEqual(self.js("startCardRequest({entry: 'now', executor: 'Opus', check: 'manual'}, ['/t/1.png'])"),
                         {"entry": "now", "executor": "Opus", "check": "manual", "images": ["/t/1.png"]})
        actual = json.dumps({"executors": EXECUTORS["list"], "providers": {"other": {"label": "Inny"}}})
        self.assertEqual(self.js(f"launchExecutors({actual}, 'openai').map(o => o.value)"), ["Codex standard"])
        self.assertEqual(self.js(f"launchExecutors({actual}, 'local').map(o => o.value)"), ["Lokalny"])
        self.assertEqual(self.js(f"launchExecutors({actual}, 'mixed').length"), 5)
        self.assertEqual(self.js(f"reviewExecutors({actual}, 'Opus', 'anthropic').map(o => o.value)"),
                         ["Haiku", "Sonnet"])
        self.assertEqual(self.js(f"reviewExecutors({actual}, 'Opus', 'mixed').map(o => o.value)"),
                         ["Codex standard"])
        self.assertEqual(self.js(f"reviewExecutors({actual}, 'Lokalny', 'local').map(o => o.value)"), [])
        self.assertIn("other", self.js(f"preferredProviders({actual}).map(o => o.value)"))
        self.assertEqual(self.js("startCardRequest({entry:'now', executor:'Codex standard', preferredProvider:'openai', check:'auto'}, [])"),
                         {"entry": "now", "executor": "Codex standard", "preferredProvider": "openai",
                          "check": "auto", "images": []})
        flow = "{id: 'K-1', column: 'todo', boardColumn: '%s', derived: {column: '%s', flow: {ticket: 'PT-1'}}}"
        for shown in ("progress", "waiting"):
            card = flow % (shown, shown)
            self.assertTrue(self.js(f"draggable({card})"), shown)
            self.assertEqual(self.js(f"dropTargets({card})"), ["backlog", "done"], shown)
        self.assertEqual(self.js(f"dropTargets({flow % ('review', 'review')})"), ["done", "todo"])
        legacy = "{id: 'K-2', column: 'todo', boardColumn: 'progress', derived: {column: 'progress', coordinator: 'c'}}"
        self.assertFalse(self.js(f"draggable({legacy})"))
        self.assertEqual(self.js(f"dropTargets({legacy})"), [])

    def test_launch_texts_and_requests(self):
        k9 = "S.cards.find(c => c.id === 'K-9')"
        self.assertEqual(self.js(f"launchBranch({k9}, 'codex')"), "codex/k-9-wlasne-okno-kafelek-dockera")
        self.assertEqual(self.js("retoolBranch('claude/k-9-okno', 'codex')"), "codex/k-9-okno")
        self.assertEqual(self.js("retoolBranch('feat/k-9-okno', 'codex')"), "feat/k-9-okno", "własna gałąź bez prefiksu narzędzia")
        self.assertEqual(self.js(f"launchBlocker(S, {k9})"), "")
        self.assertIn("nie ma projektu", self.js(f"launchBlocker(S, Object.assign({{}}, {k9}, {{project: ''}}))"))
        self.assertEqual(self.js(f"docsLabel(launchProject(S, {k9}))"), "w repozytorium · docs/agents/")
        self.assertEqual(self.js("docsLabel({aiDocs: 'tide', slug: 'mobileapp', docsDir: '/h/DevWorkspace/tide-board/docs/mobileapp'})"),
                         "w Tide · tide-board/docs/mobileapp/")
        state = {"cardId": "K-16", "entry": "planning", "mode": "full", "tool": "claude", "branch": "claude/k-16-okno",
                 "newWorktree": True, "check": "auto", "aiDocs": "repo", "slug": "plasma-tide", "repoName": "plasma-tide"}
        js_state = json.dumps(state)
        self.assertEqual(self.js(f"launchFooter({js_state})"),
                         "Planowanie Pełne w jednej karcie Konsoli: planujący dopyta, zapisze specyfikację i pokaże "
                         "podział na tickety. Po review plan poczeka w „Czeka na mnie” na Twoje zatwierdzenie.")
        light = self.js(f"launchFooter(Object.assign({js_state}, {{mode: 'light', tool: 'codex', check: 'manual'}}))")
        self.assertIn("Planowanie w tle bez Konsoli", light)
        self.assertIn("Twoje zatwierdzenie", light)
        spec = self.js(f"launchFooter(Object.assign({js_state}, {{entry: 'spec', aiDocs: 'tide', slug: 'mobileapp'}}))")
        self.assertEqual(spec, "Claude Code w katalogu projektu, bez worktree: dopyta i zapisze specyfikację w "
                               "tide-board/docs/mobileapp/specs/. Potem karta wróci do Backlogu ze znacznikiem "
                               "„specyfikacja gotowa”.")
        flow = {"text": "Karta K-16…", "repo": "/r/plasma-tide", "tool": "claude", "branch": "claude/k-16-okno",
                "docsDir": "/b/docs/mobileapp", "aiDocs": "tide"}
        self.assertEqual(self.js(f"launchStartRequest({json.dumps(flow)}, {js_state})"),
                         {"repoPath": "/r/plasma-tide", "tool": "claude", "prompt": "Karta K-16…", "newWorktree": True,
                          "branch": "claude/k-16-okno", "images": [], "addDirs": ["/b/docs/mobileapp"], "model": "cloud"})
        # PT-43: zrzuty i model lokalny jak z okna „Nowe zadanie”.
        local = dict(state, tool="codex", model="local", localModel="qwen3-coder:30b",
                     images=["/d/tasks/x/zrzut-1.png", "/d/tasks/x/zrzut-2.png"])
        local_start = self.js(f"launchStartRequest({json.dumps(dict(flow, tool='codex'))}, {json.dumps(local)})")
        self.assertEqual((local_start["images"], local_start["model"], local_start["tool"]),
                         (local["images"], "local", "codex"))
        self.assertIn("Planowanie Pełne w jednej karcie Konsoli", self.js(f"launchFooter({json.dumps(local)})"))
        spec_start = self.js(f"launchStartRequest({json.dumps(dict(flow, aiDocs='repo'))}, Object.assign({js_state}, {{entry: 'spec'}}))")
        self.assertEqual((spec_start["newWorktree"], spec_start["branch"], spec_start["addDirs"]), (False, "", []))
        self.assertEqual(self.js(f"launchMarkRequest({{worktree: '/w/k-16', branch: 'claude/k-16-okno', tool: 'claude'}}, {js_state}, 'T')"),
                         {"branch": "claude/k-16-okno", "worktree": "/w/k-16", "entry": "planning", "mode": "full",
                          "tool": "claude", "check": "auto", "launched": "T", "session": ""})
        self.assertEqual(self.js(f"launchMarkRequest({{worktree: '', branch: 'master'}}, Object.assign({js_state}, {{entry: 'spec', mode: 'light'}}), 'T')")
                         ["entry"] + "/" + self.js(f"launchMarkRequest({{worktree: ''}}, Object.assign({js_state}, {{entry: 'spec', mode: 'light'}}), 'T')")["mode"],
                         "spec/", "przy Tylko specyfikacja karta nie dostaje trybu")
        self.assertEqual(self.js(f"launchMarkRequest({{worktree: '', branch: 'master'}}, Object.assign({js_state}, {{check: 'manual'}}), 'T')")["branch"],
                         "", "bez worktree gałąź główna nie trafia do karty")
        self.assertRegex(self.js("isoNow(1793099040000)"), r"^2026-10-27T\d\d:\d\d:00[+-]\d\d:\d\d$")
        self.assertEqual(self.js("cardNote({column: 'backlog', spec: '/s/k-1.md', note: 'moja'})"), "✓ specyfikacja gotowa")
        self.assertEqual(self.js("cardNote({column: 'todo', spec: '/s/k-1.md', note: 'moja'})"), "moja")

    def test_extra_projects_logic(self):
        """PT-28: dodatkowe projekty w oknie uruchomienia — opcje, dodawanie, usuwanie, zmiana, stopka, Start."""
        k9 = "S.cards.find(c => c.id === 'K-9')"
        options = self.js(f"extraOptions(S, {k9}, [], '')")
        keys = [o["key"] for o in options]
        primary = self.js(f"{k9}.project")
        self.assertNotIn(primary, keys, "główny projekt nie jest dodatkowym")
        self.assertIn("mobileapp", keys)
        repo = next(o for o in options if o["kind"] == "repo")
        self.assertEqual((repo["name"], repo["detail"]), ("legal-agents", "nowy projekt z repozytorium"))
        self.assertEqual([o["key"] for o in self.js(f"extraOptions(S, {k9}, [{{key: 'mobileapp'}}], 'app5')")],
                         ["webservice"], "wybrane znikają z listy, szukanie po nazwie")
        picked = self.js("addExtra(addExtra([], 'project', 'mobileapp', 'mobileapp'), 'repo', '/r/legal', 'legal')")
        self.assertEqual(picked, [{"kind": "project", "key": "mobileapp", "name": "mobileapp"},
                                  {"kind": "repo", "key": "/r/legal", "name": "legal"}])
        self.assertEqual(self.js(f"addExtra({json.dumps(picked)}, 'project', 'mobileapp', 'x')"), picked, "bez powtórzeń")
        self.assertEqual(self.js(f"removeExtra({json.dumps(picked)}, 'mobileapp')"), picked[1:])
        self.assertTrue(self.js(f"extrasChanged({{projects: []}}, {json.dumps(picked)})"))
        self.assertFalse(self.js("extrasChanged({projects: ['mobileapp']}, [{key: 'mobileapp'}])"))
        self.assertEqual(self.js("cardExtras(S, {projects: ['mobileapp', 'nie-ma']})"),
                         [{"kind": "project", "key": "mobileapp", "name": "mobileapp"},
                          {"kind": "project", "key": "nie-ma", "name": "nie-ma"}])
        state = {"cardId": "K-16", "mode": "light", "tool": "claude", "branch": "claude/k-16-okno", "newWorktree": True,
                 "check": "auto", "aiDocs": "tide", "slug": "dp", "repoName": "dp", "extras": picked}
        self.assertIn("Dodatkowe projekty do odczytu: mobileapp, legal.",
                      self.js(f"launchFooter({json.dumps(state)})"))
        flow = {"text": "x", "repo": "/r/dp", "tool": "claude", "branch": "claude/k-16-okno", "docsDir": "/b/docs/dp",
                "aiDocs": "tide", "addDirs": ["/b/docs/dp", "/r/mobileapp"]}
        self.assertEqual(self.js(f"launchStartRequest({json.dumps(flow)}, {json.dumps(state)})")["addDirs"],
                         ["/b/docs/dp", "/r/mobileapp"], "katalogi --add-dir z RenderLaunch")

    def test_rail_note_errors_and_replies(self):
        now = 1793099040  # 12:04 w Warszawie
        self.assertEqual(self.js("railNote({exists: false})"), "jeszcze nie założona — powstanie przy pierwszej karcie")
        self.assertEqual(self.js("railNote({exists: true, git: {busy: 'revert'}})"), "trwa git revert — zapis wstrzymany")
        self.assertEqual(self.js("railNote({exists: true, git: {pending: 2}})"), "lokalne repo · 2 zmiany czekają na commit")
        note = self.js(f"railNote({{exists: true, git: {{}}, lastCommit: {{ts: {now - 60}}}}}, {now})")
        self.assertRegex(note, r"^lokalne repo · zapisano \d\d:\d\d$")
        self.assertRegex(self.js(f"when({now - 86400 * 3}, {now})"), r"^\d+\.\d\d \d\d:\d\d$")
        errors = json.dumps({"boardDir": "/b", "errors": [{"path": "/b/cards/K-3.md", "message": "brak frontmattera"},
                                                          {"path": "/b", "message": "trwa git revert"},
                                                          {"path": "/b/tags/x.md", "message": "a"},
                                                          {"path": "/b/tags/y.md", "message": "b"}]})
        self.assertEqual(self.js(f"errorLines({errors}, 3)"),
                         ["cards/K-3.md: brak frontmattera", "trwa git revert",
                          "i 2 więcej — szczegóły: tide-daemon.py --print-board"])
        self.assertEqual(self.js("errorLines({errors: []})"), [])
        self.assertEqual(self.js("errorText({ok: false, error: 'bad-request', message: 'pusty tytuł'})"), "pusty tytuł")
        self.assertEqual(self.js("errorText({ok: false, error: 'git-busy'})"),
                         "W repozytorium tablicy trwa operacja gita — dokończ ją albo przerwij.")
        self.assertTrue(self.js("errorText(null)").startswith("Usługa tide-daemon nie odpowiedziała"))
        self.assertEqual(self.js("commitNote({ok: true, commitError: 'index.lock'})"),
                         "Zapisane w pliku, commit czeka: index.lock")
        self.assertEqual(self.js("commitNote({ok: true, commitError: ''})"), "")

    def test_requests_from_form(self):
        self.assertEqual(self.js("createRequest({title: '  Nowa  ', description: 'd', note: ' n ', tags: ['a'],"
                                 " project: 'cookapp', projectKind: 'project', column: 'todo'})"),
                         {"title": "Nowa", "description": "d", "note": "n", "tags": ["a"], "project": "cookapp",
                          "column": "todo"})
        self.assertEqual(self.js("createRequest({title: 'x', project: '/r/new', projectKind: 'repo'})"),
                         {"title": "x", "description": "", "note": "", "tags": [], "repo": "/r/new"})
        k12 = "S.cards.find(c => c.id === 'K-12')"
        self.assertEqual(self.js(f"updateRequest({k12}, {{title: {k12}.title, description: '', note: 'teraz',"
                                 f" tags: ['pilne'], project: 'timetracker', projectKind: 'project'}})"), {"note": "teraz"},
                         "tylko zmienione pola")
        self.assertEqual(self.js(f"updateRequest({k12}, {{title: 'Inny', description: 'opis', note: '', tags: [],"
                                 f" project: '', projectKind: ''}})"),
                         {"title": "Inny", "description": "opis", "tags": [], "project": ""})
        self.assertEqual(self.js(f"updateRequest({k12}, {{title: {k12}.title, tags: ['Pilne'], project: '/r/x',"
                                 f" projectKind: 'repo'}})"), {"repo": "/r/x"}, "wielkość liter tagu bez znaczenia")
        options = self.js("projectOptions(S, '')")
        self.assertEqual(options[0], {"kind": "none", "key": "", "name": "Bez projektu", "detail": ""})
        self.assertEqual([o["kind"] for o in options].count("project"), 7)
        self.assertEqual(options[-1]["kind"], "repo")
        self.assertEqual([o["name"] for o in self.js("projectOptions(S, 'legal')")], ["Bez projektu", "legal-agents"])


    def test_failed_planning_offers_other_planners_without_a_ticket_reviewer(self):
        status = dict(models_status(), executors=EXECUTORS)
        options = self.js(f"retryExecutors({json.dumps(status)}, {{stepKind: 'planning', provider: 'anthropic', executor: 'Sonnet', preferredProvider: 'anthropic'}}).map(o => o.value)")
        self.assertIn("Opus", options)
        self.assertNotIn("Sonnet", options)
        self.assertNotIn("Lokalny", options)


class DocsLogicTest(unittest.TestCase):
    """cardDocs.js w QJSEngine: zakładki, postęp, stany ticketów, statusy, linki."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        with open(os.path.join(APP_DIR, "qml", "cardDocs.js"), encoding="utf-8") as fh:
            result = cls.engine.evaluate(fh.read().replace(".pragma library", ""))
        assert not result.isError(), result.toString()

    def js(self, expression):
        value = self.engine.evaluate(f"JSON.stringify({expression})")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def test_tabs_and_progress(self):
        plan = "{tickets: [{id: 'A', state: 'gotowe'}, {id: 'B', state: 'w-toku'}, {id: 'C', state: 'odrzucone'}]}"
        self.assertEqual(self.js("tabs(null)"), [{"value": "opis", "label": "Opis"}])
        self.assertEqual([t["label"] for t in self.js(f"tabs({{plan: {plan}, spec: {{}}}})")],
                         ["Opis", "Plan · 1/2", "Specyfikacja"], "odrzucone się nie liczą")
        self.assertEqual([t["label"] for t in self.js("tabs({plan: {tickets: []}, spec: null})")], ["Opis", "Plan"])
        self.assertEqual(self.js("effectiveTab({plan: {}, spec: null}, 'spec')"), "opis")
        self.assertEqual(self.js("effectiveTab({plan: {}, spec: {}}, 'spec')"), "spec")
        self.assertEqual(self.js("effectiveTab(null, 'plan')"), "opis")

    def test_states_statuses_and_links(self):
        self.assertEqual(self.js("ticketState('czeka')"), {"word": "czeka", "tint": "peach"})
        self.assertEqual(self.js("ticketState('w-toku').tint"), "teal")
        self.assertEqual(self.js("ticketState('')"), {"word": "bez stanu", "tint": "textMuted"})
        self.assertEqual(self.js("planStatus('done').word"), "zakończony")
        self.assertEqual(self.js("specStatus({ready: true, status: 'gotowa'})"), {"word": "gotowa", "tint": "mint"})
        self.assertEqual(self.js("specStatus({ready: false, status: 'szkic'}).tint"), "butter")
        self.assertEqual(self.js("specStatus({ready: false, status: ''}).word"), "bez statusu")
        self.assertEqual(self.js("safeLink(' https://example.com/a?b=1 ')"), "https://example.com/a?b=1")
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "../specs/k-1.md", "https://a b", ""):
            self.assertEqual(self.js(f"safeLink({json.dumps(bad)})"), "", bad)
        self.assertEqual(self.js("docTime({updatedTs: 5, mtime: 9})"), 5)
        self.assertEqual(self.js("docTime({updated: '2026-10-27T12:00:00Z', mtime: 9})"), 1793102400)
        self.assertEqual(self.js("docTime({updated: 'zle', mtime: 9})"), 9)
        self.assertTrue(self.js("outsideCard({cardTickets: ['A']}, {id: 'B'})"))
        self.assertFalse(self.js("outsideCard({cardTickets: []}, {id: 'B'})"))
        self.assertEqual(self.js("source({link: 'coordinator', where: 'worktree'}, 'plan')"),
                         "plan koordynatora karty · w worktree")
        self.assertEqual(self.js("source({link: 'field', where: 'board'}, 'spec')"), "z pola spec karty · w tablicy")


class ProbeCase(unittest.TestCase):
    """Wspólne: pakiet okna Tide i dane przykładowej tablicy (jak na mocku)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-board-probe-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.data = preview_data(os.path.join(cls.tmp, "fx"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, scenario: str, data: dict | None = None) -> dict:
        data_path = os.path.join(self.tmp, "data.json")
        with open(data_path, "w", encoding="utf-8") as fh:
            json.dump(self.data if data is None else data, fh, ensure_ascii=False)
        script = os.path.join(self.tmp, "scenario.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(scenario)
        result = subprocess.run(["/usr/bin/python3", PROBE, self.pkg, data_path, script], env=offscreen(),
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DragTest(ProbeCase):
    def test_drag_between_and_within_columns(self):
        out = self.probe("""
col = lambda c: js(f"page.cards.filter(x => x.boardColumn === '{c}').map(x => x.id)")
drag(card("K-15"), column("todo", fy=0.75))
out["toTodo"] = calls()[-1]
out["todo"] = col("todo")
out["hint"] = js("page.columnItems()[1].hintVisible")
out["dialog"] = js("page.launchOpen && page.launchCardId")
js("page.launchDialog.moveOnlyRequested()")
set_snapshot(json.loads(backend.snapshotJson))  # nowa migawka — przeniesienie „w locie” znika
drag(card("K-10"), card("K-14", fy=0.15))
out["reorder"] = calls()[-1]
out["backlog"] = col("backlog")
drag(card("K-13"), card("K-4", fy=0.1))
out["toDone"] = calls()[-1]
drag(card("K-12"), column("progress"))
out["count"] = len(calls())
out["toast"] = js("page.toast")
drag(card("K-6"), column("backlog"))
out["derived"] = len(calls())
click(card("K-11"))
out["clickedPanel"] = js("page.panelOpen && page.panelCardId")
""")
        self.assertEqual(out["toTodo"], ["MoveCard", "K-15", "todo", -1])
        self.assertEqual(out["todo"], ["K-11", "K-9", "K-15"], "karta od razu w nowej kolumnie")
        self.assertTrue(out["hint"], "podpowiedź „Upuść kartę z Backlogu”")
        self.assertEqual(out["dialog"], "K-15", "po przeniesieniu z Backlogu — „Co ma zrobić agent?”")
        self.assertEqual(out["reorder"], ["MoveCard", "K-10", "backlog", 1], "przed K-14, wśród kart bez przenoszonej")
        self.assertEqual(out["backlog"][:3], ["K-15", "K-10", "K-14"])
        self.assertEqual(out["toDone"], ["MoveCard", "K-13", "done", 0], "na górę Gotowych")
        self.assertEqual(out["count"], 3, "W toku nie przyjmuje karty ręcznie")
        self.assertIn("trafia sama", out["toast"])
        self.assertEqual(out["derived"], 3, "karty prowadzonej przez koordynatora nie da się przeciągnąć")
        self.assertEqual(out["clickedPanel"], "K-11", "kliknięcie bez ruchu otwiera kartę")

    def test_failed_move_returns_card_and_shows_message(self):
        out = self.probe("""
replies.append({"ok": False, "error": "git-busy", "message": "w repozytorium tablicy trwa git revert — dokończ go"})
drag(card("K-15"), column("todo", fy=0.75))
wait(150)
js("page.launchDialog.moveOnlyRequested()")
out["todo"] = js("page.cards.filter(x => x.boardColumn === 'todo').map(x => x.id)")
out["toast"] = js("page.toast")
out["tone"] = js("page.toastTone")
replies.append({"ok": True, "id": "K-14", "changed": True, "commitError": "index.lock: File exists"})
drag(card("K-14"), column("todo", fy=0.75))
wait(150)
js("page.launchDialog.moveOnlyRequested()")
out["notice"] = js("page.toast")
out["noticeTone"] = js("page.toastTone")
""")
        self.assertEqual(out["todo"], ["K-11", "K-9"], "po błędzie karta wraca")
        self.assertEqual(out["toast"], "Nie przeniesiono K-15: w repozytorium tablicy trwa git revert — dokończ go")
        self.assertEqual(out["tone"], "rose")
        self.assertEqual(out["notice"], "Zapisane w pliku, commit czeka: index.lock: File exists")
        self.assertEqual(out["noticeTone"], "butter")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DerivedCardsTest(ProbeCase):
    """Karty ze stanem z koordynatora (PT-5): potwierdzenie z Do sprawdzenia i przejście do agenta."""

    def test_review_card_goes_to_done_or_back_to_todo_only(self):
        out = self.probe("""
drag(card("K-7"), column("backlog", fy=0.8))
out["refused"] = [len(calls()), js("page.toast")]
drag(card("K-7"), column("done", fy=0.8))
out["done"] = calls()[-1]
set_snapshot(json.loads(backend.snapshotJson))
js("page.findCard('K-7').forceActiveFocus()")
key(Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
out["back"] = calls()[-1]
out["dialog"] = js("page.launchOpen")
""")
        self.assertEqual(out["refused"][0], 0, "z Do sprawdzenia nie do Backlogu")
        self.assertIn("do Gotowych", out["refused"][1])
        self.assertEqual(out["done"][:3], ["MoveCard", "K-7", "done"], "potwierdzenie: Gotowe")
        self.assertEqual(out["back"], ["MoveCard", "K-7", "todo", -1], "Ctrl+← — do poprawy")
        self.assertFalse(out["dialog"], "odesłanie do poprawy nie pyta o nowego agenta")

    def test_panel_and_drag_move_review_card_back_to_todo(self):
        # Karta „Do sprawdzenia” ma zapisane todo (K-7): „Do zrobienia” w panelu i przeciągnięcie do Do zrobienia
        # to odesłanie do poprawy — MoveCard musi pójść, choć zapisana kolumna się nie zmienia.
        out = self.probe("""
out["stored"] = js("page.snapshot.cards.find(c => c.id === 'K-7').column")
click(card("K-7"))
out["current"] = obj("cardColumn").property("current")
engine.rootContext().setContextProperty("probeTarget", obj("cardColumn"))
js("probeTarget.picked('todo')")
wait(100)
out["panel"] = calls()[-1] if calls() else None
set_snapshot(json.loads(backend.snapshotJson))
key(Qt.Key.Key_Escape)
n = len(calls())
drag(card("K-7"), column("todo", fy=0.8))
out["drag"] = calls()[n] if len(calls()) > n else None
""")
        self.assertEqual(out["stored"], "todo")
        self.assertEqual(out["current"], "review", "panel pokazuje kolumnę z ekranu")
        self.assertEqual(out["panel"][:3], ["MoveCard", "K-7", "todo"])
        self.assertEqual(out["drag"][:3], ["MoveCard", "K-7", "todo"])

    def test_agent_jump_and_sessions_link_are_separate(self):
        # Uwaga użytkownika: „Przejdź do agenta” przenosiło do widoku Sesje. Teraz „Przejdź do agenta” zawsze
        # aktywuje okno agenta (/Agents ActivateSession), a „Otwórz w Sesjach” — koordynatora w widoku Sesje.
        data = json.loads(json.dumps(self.data))
        for c in data["cards"]:
            if c["id"] == "K-6":
                c["derived"]["coordinator"] = "codex:019a-koordynator"
                c["derived"]["session"] = ""
        out = self.probe("""
for cid in ("K-5", "K-6"):
    # Te same reguły co agentSession/sessionsTarget w board.js (sprawdzone niżej w QJSEngine).
    out[cid] = js(f"(function () {{ const it = page.findCard('{cid}'); if (!it) return 'brak'; const d = it.card.derived; const a = d.session || d.coordinator || ''; it.agentRequested(a); return [a, d.coordinator || ''] }})()")
out["links"] = js("(function () { const it = page.findCard('K-5'); const r = []; for (const n of ['agentLink', 'sessionsLink']) { const o = (function f(x) { if (x.objectName === n) return x; for (const c of x.children) { const y = f(c); if (y) return y } return null })(it); r.push(o ? o.visible : null) } return r })()")
js("(function () { const it = page.findCard('K-6'); it.sessionRequested(it.card.derived.coordinator) })()")
out["calls"] = calls()
""", data)
        self.assertEqual(out["K-5"], ["claude:5f0c", ""])
        self.assertEqual(out["links"], [True, False], "karta bez koordynatora: jest „Przejdź do agenta”, nie ma „Otwórz w Sesjach”")
        self.assertEqual(out["K-6"], ["codex:019a-koordynator", "codex:019a-koordynator"])
        self.assertEqual(out["calls"], [["/Agents ActivateSession", "claude:5f0c"],
                                        ["/Agents ActivateSession", "codex:019a-koordynator"],
                                        ["/Coordination ShowWindow", "codex:019a-koordynator"]], out)


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class PanelTest(ProbeCase):
    def test_flow_step_buttons_route_enter_and_abandon(self):
        data = json.loads(json.dumps(self.data))
        card_data = next(card for card in data["cards"] if card["id"] == "K-12")
        card_data["derived"] = {"column": "waiting", "flow": {"steps": [
            {"id": "K-12-PT-67-1", "ticket": "PT-67", "mode": "interactive", "result": "stopped",
             "reason": "interactive-closed", "canEnter": True}]}}
        out = self.probe(FIND_PY + """
click(card('K-12'))
out['flow'] = js('page.cardPanel.card.derived.flow')
out['repeaterCount'] = js('page.cardPanel.flowRepeater.count')
out['enterText'] = js(FIND + "(page.cardPanel, 'enterStep').text")
out['abandonVisible'] = js(FIND + "(page.cardPanel, 'abandonStep').visible")
js("page.cardPanel.enterStepRequested('K-12', 'K-12-PT-67-1')")
wait(80)
out['enterCall'] = calls()[-1]
js("page.cardPanel.abandonStepRequested('K-12', 'K-12-PT-67-1')")
wait(80)
out['abandonCall'] = calls()[-1]
""", data)
        self.assertEqual(out["enterText"], "Wznów", out)
        self.assertTrue(out["abandonVisible"])
        self.assertEqual(out["enterCall"], ["EnterStep", "K-12", "K-12-PT-67-1"])
        self.assertEqual(out["abandonCall"], ["AbandonStep", "K-12", "K-12-PT-67-1"])

    def test_create_card_from_column_and_errors_in_place(self):
        out = self.probe("""
js("page.columnItems()[1].addRequested('todo')")
wait(80)
out["open"] = js("page.panelOpen")
out["column"] = js("page.cardPanel.column")
replies.append({"ok": False, "error": "bad-request", "message": "nie ma projektu „app5”"})
js("page.cardPanel.save()")
out["empty"] = js("page.cardPanel.error")
out["calls0"] = len(calls())
obj("cardTitle").setProperty("text", "Eksport do CSV")
obj("cardNote").setProperty("text", "pilne na piątek")
js("page.cardPanel.tags = ['pilne', 'nowy-tag']")
js("page.cardPanel.projectKind = 'repo'; page.cardPanel.project = '/home/user/DevWorkspace/legal-agents'")
click(named("cardSave"))  # „Dodaj kartę”
wait(100)
out["call"] = calls()[-1]
out["serviceError"] = js("page.cardPanel.error")
out["stillOpen"] = js("page.panelOpen")
out["busy"] = js("page.cardPanel.busy")
js("page.cardPanel.save()")
wait(100)
out["closed"] = not js("page.panelOpen")
out["selected"] = js("page.selectedId")
""")
        self.assertTrue(out["open"])
        self.assertEqual(out["column"], "todo")
        self.assertEqual(out["empty"], "Karta potrzebuje tytułu.")
        self.assertEqual(out["calls0"], 0, "pusty tytuł nie idzie do usługi")
        member, request = out["call"][0], json.loads(out["call"][1])
        self.assertEqual(member, "CreateCard")
        self.assertEqual(request, {"title": "Eksport do CSV", "description": "", "note": "pilne na piątek",
                                   "tags": ["pilne", "nowy-tag"], "repo": "/home/user/DevWorkspace/legal-agents",
                                   "column": "todo"}, "repozytorium zakłada projekt przy zapisie karty")
        self.assertEqual(out["serviceError"], "nie ma projektu „app5”", "błąd usługi w panelu, po polsku")
        self.assertTrue(out["stillOpen"])
        self.assertFalse(out["busy"])
        self.assertTrue(out["closed"], "po udanym zapisie panel się zamyka")
        self.assertEqual(out["selected"], "K-99", "nowa karta zaznaczona")

    def test_edit_sends_only_changes_and_archive(self):
        out = self.probe("""
click(card("K-12"))
out["panel"] = js("page.cardPanel.card.id")
out["dirty0"] = js("page.cardPanel.dirty")
out["inherited"] = js("page.cardPanel.inherited")
obj("cardNote").setProperty("text", "do faktury na 1. listopada")
out["dirty1"] = js("page.cardPanel.dirty")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(100)
out["update"] = calls()[-1]
click(card("K-13"))
js("page.cardPanel.moveRequested('archived')")
wait(100)
out["archive"] = calls()[-1]
out["closedAfterArchive"] = not js("page.panelOpen")
out["visible"] = js("page.cards.some(c => c.id === 'K-13')")
click(card("K-14"))
key(Qt.Key.Key_Escape)
out["esc"] = js("page.panelOpen")
""")
        self.assertEqual(out["panel"], "K-12")
        self.assertFalse(out["dirty0"])
        self.assertEqual(out["inherited"], ["własne"], "tag z projektu TimeTracker")
        self.assertTrue(out["dirty1"])
        self.assertEqual(out["update"][:2], ["UpdateCard", "K-12"])
        self.assertEqual(json.loads(out["update"][2]), {"note": "do faktury na 1. listopada"})
        self.assertEqual(out["archive"], ["MoveCard", "K-13", "archived", 0])
        self.assertTrue(out["closedAfterArchive"])
        self.assertFalse(out["visible"], "zarchiwizowana znika z tablicy od razu")
        self.assertFalse(out["esc"], "Esc zamyka panel")

    def test_edit_keeps_changes_made_elsewhere_while_panel_is_open(self):
        # Regresja z review: formularz porównujemy z kartą z chwili otwarcia, nie z żywą migawką.
        out = self.probe("""
import copy
click(card("K-12"))
snap = copy.deepcopy(data)
for c in snap["cards"]:
    if c["id"] == "K-12":
        c["description"] = "opis zmieniony z zewnątrz"
        c["note"] = "notatka z zewnątrz"
set_snapshot(snap)
wait(100)
out["dirtyAfterOutside"] = js("page.cardPanel.dirty")
obj("cardTitle").setProperty("text", "Nowy tytuł")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(100)
out["update"] = calls()[-1]
""")
        self.assertFalse(out["dirtyAfterOutside"], "zmiana z zewnątrz nie robi formularza zmienionym")
        self.assertEqual(out["update"][:2], ["UpdateCard", "K-12"])
        self.assertEqual(json.loads(out["update"][2]), {"title": "Nowy tytuł"})


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardDocsPanelTest(ProbeCase):
    """Zakładki „Plan” i „Specyfikacja” panelu karty na odpowiedziach CardDocs z boardfix (K-5, K-14)."""

    def test_plan_tab_table_markdown_and_open_buttons(self):
        out = self.probe("""
click(card("K-5"))
wait(150)
out["requests"] = backend.docsRequests
out["tabs"] = js("page.cardPanel.docTabs.map(t => t.label)")
out["tabsVisible"] = obj("cardTabs").property("visible")
out["width0"] = js("page.cardPanel.width")
js("page.cardPanel.tab = 'plan'")
wait(300)
out["shown"] = js("page.cardPanel.shownTab")
out["formHidden"] = not obj("cardTitle").property("visible") or not js("page.cardPanel.docView.visible === false")
out["width1"] = js("page.cardPanel.width")
out["title"] = obj("docTitle").property("text")
out["status"] = obj("docStatus").property("word")
out["tickets"] = js("page.cardPanel.docView.tickets.map(t => t.id + ':' + t.state)")
out["markdown"] = js("(function () { const m = page.cardPanel.docView.markdownItem; return m.getText(0, m.length).replace(/[\\\\u2028\\\\u2029]/g, ' ') })()")
out["manualVisible"] = obj("docManual").property("visible")
path = js("page.cardPanel.docs.plan.path")
out["path"] = path
click(named("docOpen"))
click(named("docFolder"))
out["opens"] = calls()[-2:]
js("page.cardPanel.docView.markdownItem.linkActivated('javascript:alert(1)')")
js("page.cardPanel.docView.markdownItem.linkActivated('file:///etc/passwd')")
js("page.cardPanel.docView.markdownItem.linkActivated('https://example.com/tide/docs/tide-daemon.md')")
out["links"] = [c for c in calls() if c[0] == "openLink"]
out["foreign"] = backend.openFile("/etc/passwd")
click(named("cardExpand"))
wait(300)
out["width2"] = js("page.cardPanel.width")
out["pageWidth"] = js("page.width")
js("page.cardPanel.tab = 'spec'")
wait(200)
out["specTitle"] = obj("docTitle").property("text")
out["specStatus"] = obj("docStatus").property("word")
out["manual"] = js("(function () { const m = page.cardPanel.docView.manualItem; return m.getText(0, m.length).replace(/[\\\\u2028\\\\u2029]/g, ' ') })()")
out["manualShown"] = obj("docManual").property("visible")
js("page.cardPanel.tab = 'opis'")
wait(200)
out["width3"] = js("page.cardPanel.width")
out["titleField"] = obj("cardTitle").property("visible")
""")
        self.assertGreaterEqual(out["requests"], 1, "panel prosi o CardDocs przy otwarciu")
        self.assertEqual(out["tabs"], ["Opis", "Plan · 2/6", "Specyfikacja"])
        self.assertTrue(out["tabsVisible"])
        self.assertEqual(out["width0"], 420, "Opis — zwykła szerokość")
        self.assertEqual(out["shown"], "plan")
        self.assertEqual(out["width1"], 860, "dokument — szerszy panel")
        self.assertEqual((out["title"], out["status"]), ("Tablica pracy z backlogiem i tagami", "aktywny"))
        self.assertEqual(out["tickets"], ["PT-1:gotowe", "PT-2:gotowe", "PT-3:review", "PT-4:w-toku", "PT-5:czeka",
                                          "PT-6:todo"])
        self.assertIn("Decyzje", out["markdown"])
        self.assertIn("(obraz: mock tablicy)", out["markdown"], "obraz jako tekst, bez ładowania")
        self.assertNotIn("| Ticket |", out["markdown"])
        self.assertFalse(out["manualVisible"], "plan bez sekcji testu ręcznego")
        self.assertEqual(out["path"], "/home/user/DevWorkspace/plasma-tide/docs/agents/plans/2026-10-27-tablica.md")
        self.assertEqual(out["opens"], [["openFile", out["path"]], ["showInFolder", out["path"]]])
        self.assertEqual(out["links"], [["openLink", "https://example.com/tide/docs/tide-daemon.md"]],
                         "tylko http(s)")
        self.assertFalse(out["foreign"], "ścieżka spoza odpowiedzi CardDocs się nie otwiera")
        self.assertEqual(out["width2"], out["pageWidth"] - 24, "Rozwiń — prawie całe okno")
        self.assertEqual((out["specTitle"], out["specStatus"]), ("K-5: Tablica pracy z backlogiem i tagami", "gotowa"))
        self.assertTrue(out["manualShown"])
        self.assertIn("Przeciągnij kartę z Backlogu", out["manual"])
        self.assertEqual(out["width3"], 420)
        self.assertTrue(out["titleField"])

    def test_markdown_rendering_has_no_images_html_or_foreign_links(self):
        # Regresje z review: każdy przypadek, w którym wyrażenia regularne rozjeżdżały się z md4c.
        cases = {
            "unmatched-backticks-html": "``<img src=\"http://evil.example/a.png\">`",
            "unmatched-backticks-img": "``![a](http://evil.example/b.png)`",
            "invalid-fence-info": "```foo`bar\n<img src=\"http://evil.example/c.png\">\n![x](http://evil.example/c2.png)",
            "list-fence": "- ```\n  code\n  ```\n\n<img src=\"http://evil.example/d.png\">\n\n![x](http://evil.example/d2.png)",
            "multiline-alt": "![alt\ntext](http://evil.example/e.png)",
            "refdef": "![a]\n\n[a]: http://evil.example/f.png",
            "html": "<img src=\"http://evil.example/g.png\"> <b>gruby</b> <i>x</i> <a href=\"http://evil.example\">a</a>",
            "links": "[f](file:///etc/passwd) [j](javascript:alert(1)) [r](../specs/k-1.md) [h](https://ok.example/x)",
            "pathological": "![" * 262_000,
        }
        out = self.probe(f"""
import re, time
from PyQt6.QtGui import QColor, QTextDocument
cases = {cases!r}
for name, md in cases.items():
    start = time.monotonic()
    html = module.render_markdown(md, QColor("#a6c8f0"))
    elapsed = time.monotonic() - start
    doc = QTextDocument()
    doc.setHtml(html)
    out[name] = {{"img": re.findall(r"<img[^>]*>", html), "href": re.findall(r'href="([^"]*)"', html),
                  "bold": "font-weight:700" in html, "italicOnlyImages": html.count("(obraz:"),
                  "text": doc.toPlainText()[:300], "elapsed": elapsed}}
""")
        for name, result in out.items():
            self.assertEqual(result["img"], [], name)
            self.assertFalse(result["bold"], f"{name}: HTML ze źródła nie formatuje")
            self.assertLess(result["elapsed"], 15.0, name)
        self.assertEqual(out["links"]["href"], ["https://ok.example/x"], "tylko linki http(s)")
        self.assertEqual(out["html"]["href"], [])
        self.assertIn('<img src="http://evil.example/g.png">', out["html"]["text"], "HTML jako zwykły tekst")
        self.assertIn("(obraz: alt text)", out["multiline-alt"]["text"])
        self.assertEqual(out["multiline-alt"]["italicOnlyImages"], 1, "opis z kilku linii to jeden obraz")
        self.assertIn("(obraz: a)", out["refdef"]["text"])
        for name in ("unmatched-backticks-img", "invalid-fence-info", "list-fence"):
            self.assertIn("(obraz:", out[name]["text"], name)

    def test_poll_waits_for_the_outstanding_reply(self):
        out = self.probe("""
held = []
backend._got_docs = lambda card_id, text: held.append((card_id, text))   # odpowiedź „w drodze”
click(card("K-5"))
wait(100)
first = backend.docsRequests
for _ in range(3):
    js("page.cardPanel.requestDocs()")
out["whilePending"] = backend.docsRequests - first
out["pending"] = js("page.cardPanel.docsPending")
del backend._got_docs
type(backend)._got_docs(backend, *held[0])
wait(50)
out["cleared"] = js("page.cardPanel.docsPending")
js("page.cardPanel.requestDocs()")
wait(50)
out["afterReply"] = backend.docsRequests - first
""")
        self.assertEqual(out["whilePending"], 0, "bez nowych próśb, póki odpowiedź nie wróciła")
        self.assertEqual(out["pending"], "K-5")
        self.assertEqual(out["cleared"], "")
        self.assertEqual(out["afterReply"], 1)

    def test_tabs_only_with_documents_and_refresh_on_changed(self):
        out = self.probe("""
click(card("K-12"))
wait(150)
out["noDocsTabs"] = obj("cardTabs").property("visible")
out["noDocsReading"] = js("page.cardPanel.reading")
js("page.closePanel()")
click(card("K-14"))
wait(150)
out["k14Tabs"] = js("page.cardPanel.docTabs.map(t => t.value)")
js("page.cardPanel.tab = 'plan'")
out["fallback"] = js("page.cardPanel.shownTab")
js("page.cardPanel.tab = 'spec'")
wait(100)
out["k14Status"] = obj("docStatus").property("word")
before = backend.docsRequests
import copy
backend._docs_data["K-14"] = copy.deepcopy(backend._docs_data["K-14"])
backend._docs_data["K-14"]["spec"]["title"] = "Historia diagnoz — wersja 2"
backend._docs_data["K-14"]["spec"]["status"] = "gotowa"
backend._docs_data["K-14"]["spec"]["ready"] = True
set_snapshot(data)
wait(500)
out["after"] = backend.docsRequests - before
out["k14Title"] = obj("docTitle").property("text")
out["k14Status2"] = obj("docStatus").property("word")
out["stillSpec"] = js("page.cardPanel.shownTab")
# zapamiętana zakładka: karta bez specyfikacji — Opis, następna ze specyfikacją — znów Specyfikacja
js("page.closePanel()")
click(card("K-12"))
wait(150)
out["rememberedNoDoc"] = js("page.cardPanel.shownTab")
js("page.closePanel()")
click(card("K-5"))
wait(150)
out["rememberedSpec"] = js("page.cardPanel.shownTab")
""")
        self.assertFalse(out["noDocsTabs"], "karta bez planu i specyfikacji — bez zakładek")
        self.assertFalse(out["noDocsReading"])
        self.assertEqual(out["k14Tabs"], ["opis", "spec"])
        self.assertEqual(out["fallback"], "opis", "zakładki Plan nie ma — Opis")
        self.assertEqual(out["k14Status"], "szkic")
        self.assertGreaterEqual(out["after"], 1, "nowa migawka (Changed) — CardDocs ponownie")
        self.assertEqual((out["k14Title"], out["k14Status2"], out["stillSpec"]),
                         ("Historia diagnoz — wersja 2", "gotowa", "spec"))
        self.assertEqual((out["rememberedNoDoc"], out["rememberedSpec"]), ("opis", "spec"),
                         "ostatnia zakładka zostaje dla kolejnych kart, bez dokumentu — Opis")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class FiltersAndStatesTest(ProbeCase):
    def test_filters_search_and_lanes(self):
        out = self.probe("""
out["summary"] = obj("boardSummary").property("text")
js("page.tagFilter = 'pilne'")
out["tag"] = js("page.cards.map(c => c.id)")
out["tagSummary"] = obj("boardSummary").property("text")
js("page.tagFilter = ''; page.projectFilter = 'logviewer'")
out["project"] = js("page.cards.map(c => c.id)")
js("page.projectFilter = ''")
js("page.focusSearch()")
obj("searchInput").setProperty("text", "eksport pdf")
js("page.query = 'eksport pdf'")
wait(80)
out["query"] = js("page.cards.map(c => c.id)")
key(Qt.Key.Key_Escape)
out["cleared"] = js("page.query")
js("page.layoutMode = 'lanes'")
wait(80)
out["lanes"] = js("page.lanes.map(l => l.name + ':' + l.cards.length)")
""")
        self.assertEqual(out["summary"], "11 kart · 2 w toku · 1 czeka na ciebie · 1 do sprawdzenia")
        self.assertEqual(out["tag"], ["K-12", "K-7"])
        self.assertEqual(out["tagSummary"], "2 karty · 1 do sprawdzenia · filtr")
        self.assertEqual(out["project"], ["K-13", "K-8", "K-7", "K-4"])
        self.assertEqual(out["query"], ["K-13"])
        self.assertEqual(out["cleared"], "", "Esc w szukaniu czyści je")
        self.assertEqual(out["lanes"][:3], ["agent-team:1", "LogViewer:4", "mobileapp:1"])

    def test_empty_board_and_first_card(self):
        with tempfile.TemporaryDirectory(prefix="tide-board-empty-") as tmp:
            data = empty_snapshot(tmp)
        out = self.probe("""
out["empty"] = obj("emptyState").property("visible")
out["filters"] = obj("filterRow").property("visible")
out["summary"] = obj("boardSummary").property("text")
out["rail"] = js("railInfo.note")
click(named("firstCardButton"))
out["panel"] = js("page.panelOpen && page.cardPanel.column")
""", data)
        self.assertTrue(out["empty"])
        self.assertFalse(out["filters"])
        self.assertEqual(out["summary"], "jeszcze pusta")
        self.assertEqual(out["rail"], "jeszcze nie założona — powstanie przy pierwszej karcie")
        self.assertEqual(out["panel"], "backlog", "„＋ Pierwsza karta” otwiera nową kartę")

    def test_file_errors_bar_and_rail_info(self):
        data = json.loads(json.dumps(self.data))
        data["errors"] = [{"path": data["boardDir"] + "/cards/K-30.md", "message": "<b>brak</b> frontmattera"}]
        out = self.probe("""
bar = obj("boardErrors")
out["visible"] = bar.property("visible")
out["lines"] = js("page.errorLines")
out["rail"] = js("[railInfo.label, railInfo.value, railInfo.note, railInfo.link.text]")
js("railInfo.link.activate()")
out["managing"] = js("page.managing")
key(Qt.Key.Key_Escape)
out["back"] = js("page.managing")
out["badge"] = [backend.badgeText, backend.badgeTone]
""", data)
        self.assertTrue(out["visible"])
        self.assertEqual(out["lines"], ["cards/K-30.md: <b>brak</b> frontmattera"])
        self.assertEqual(out["rail"], ["Dane tablicy", "~/DevWorkspace/tide-board", "lokalne repo · zapisano 12:04",
                                       "Projekty i tagi"])
        self.assertTrue(out["managing"], "link „Projekty i tagi” otwiera stronę")
        self.assertFalse(out["back"], "Esc wraca do tablicy")
        self.assertEqual(out["badge"], ["● 1", "waiting"])

    def test_show_item_keyboard_and_shortcuts(self):
        data = json.loads(json.dumps(self.data))
        out = self.probe("""
snap = json.loads(backend.snapshotJson)
set_snapshot({"exists": False, "cards": []})
js("showItem('k-12')")
out["pending"] = js("pendingItem")
js("page.tagFilter = 'Firma'")
set_snapshot(snap)
wait(120)
out["selected"] = js("page.selectedId")
out["filterCleared"] = js("page.tagFilter")
out["focus"] = js("page.findCard('K-12').activeFocus")
key(Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
out["keyMove"] = calls()[-1] if calls() else None
out["keyDialog"] = js("page.launchOpen")
key(Qt.Key.Key_Escape)  # Anuluj — karta wraca do Backlogu
out["keyBack"] = calls()[-1]
key(Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
out["ctrlN"] = js("page.panelOpen && page.panelCardId === ''")
key(Qt.Key.Key_Escape)
out["closed"] = not js("page.panelOpen")
""", data)
        self.assertEqual(out["pending"], "k-12", "przed migawką karta czeka")
        self.assertEqual(out["selected"], "K-12")
        self.assertEqual(out["filterCleared"], "", "filtr, który chował kartę, znika")
        self.assertTrue(out["focus"])
        self.assertEqual(out["keyMove"], ["MoveCard", "K-12", "todo", -1], "Ctrl+→ — następna kolumna ręczna")
        self.assertTrue(out["keyDialog"], "przeniesienie klawiaturą też pyta o agenta")
        self.assertEqual(out["keyBack"], ["MoveCard", "K-12", "backlog", 3], "Esc — Anuluj, karta na swoje miejsce")
        self.assertTrue(out["ctrlN"])
        self.assertTrue(out["closed"])

    def test_project_default_entry_in_manage_page(self):
        data = json.loads(json.dumps(self.data))
        data["projects"] = [p for p in data["projects"] if p["slug"] == "plasma-tide"]   # FIND bierze pierwszy wiersz
        data["projects"][0].update(entry="spec", mode="light")
        out = self.probe(FIND_PY + """
find = lambda name: FIND + f"(page.managePage, '{name}')"
js("page.managing = true")
js("page.managePage.openProject = 'plasma-tide'")
wait(100)
out["spec"] = [js(find("projectEntry") + ".current"), js(find("projectMode") + ".visible")]
js(find("projectEntry") + ".picked('planning')")
wait(50)
out["planning"] = [js(find("projectEntry") + ".current"), js(find("projectMode") + ".visible"), js(find("projectMode") + ".current")]
js(find("manageProjectsFlick") + ".contentY = Math.max(0, " + find("manageProjectsFlick") + ".contentHeight - " + find("manageProjectsFlick") + ".height)")
wait(60)
click(at(find("projectSave")))
wait(80)
out["save"] = calls()[-1]
""", data)
        self.assertEqual(out["spec"], ["spec", False], "Tylko specyfikacja: bez wyboru trybu")
        self.assertEqual(out["planning"], ["planning", True, "light"])
        self.assertEqual(json.loads(out["save"][-1]), {"slug": "plasma-tide", "entry": "planning"})

    def test_tags_and_projects(self):
        out = self.probe("""
click(named("addTagChip"))
obj("tagName").setProperty("text", "na weekend")
click(named("tagSave"))
out["quick"] = calls()[-1]
js("page.managing = true")
js("page.managePage.openProject = 'cookapp'")
wait(80)
js("page.managePage.send('project', 'cookapp', {slug: 'cookapp', mode: 'light'})")
wait(80)
out["project"] = calls()[-1]
out["closed"] = js("page.managePage.openProject")
replies.append({"ok": False, "error": "exists", "message": "tag „pilne” już jest"})
js("page.managePage.openTag = 'pomysł'")
js("page.managePage.send('tag', 'pomysł', {name: 'pomysł', newName: 'pilne', color: 'butter', description: ''})")
wait(80)
out["tag"] = calls()[-1]
out["tagError"] = js("page.managePage.tagError")
out["tagOpen"] = js("page.managePage.openTag")
js("page.managePage.send('repo', '/r', {repo: '/home/user/DevWorkspace/legal-agents'})")
wait(80)
out["repo"] = calls()[-1]
""")
        self.assertEqual(out["quick"][0], "UpsertTag")
        self.assertEqual(json.loads(out["quick"][1])["name"], "na weekend")
        self.assertEqual(out["project"][0], "UpsertProject")
        self.assertEqual(json.loads(out["project"][1]), {"slug": "cookapp", "mode": "light"})
        self.assertEqual(out["closed"], "", "po zapisie formularz projektu się zamyka")
        self.assertEqual(out["tag"][0], "UpsertTag")
        self.assertEqual(out["tagError"], "tag „pilne” już jest")
        self.assertEqual(out["tagOpen"], "pomysł", "po błędzie formularz zostaje")
        self.assertEqual(json.loads(out["repo"][1]), {"repo": "/home/user/DevWorkspace/legal-agents"})


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class LaunchMarkStoreTest(unittest.TestCase):
    """Żądanie UpdateCard po starcie (board.js launchMarkRequest) przyjęte przez prawdziwy BoardStore.
    Pole `check` dodaje store z PT-5 — bez niego test jest pomijany."""

    def test_mark_request_is_accepted_by_the_store(self):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine
        from tide_daemon.board import store as store_mod
        from .boardfix import git_env
        if "check" not in store_mod.CARD_FIELDS:
            self.skipTest("store bez pola check (PT-5)")
        QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        with open(os.path.join(APP_DIR, "qml", "board.js"), encoding="utf-8") as fh:
            engine.evaluate(fh.read().replace(".pragma library", ""))
        for result, choice in (({"worktree": "/w/k-1", "branch": "claude/k-1-grabie", "tool": "claude"},
                                {"entry": "planning", "mode": "full", "tool": "claude", "check": "manual"}),
                               ({"worktree": "", "branch": "master", "tool": "codex"},
                                {"entry": "spec", "mode": "light", "tool": "codex", "check": "auto"})):
            value = engine.evaluate(f"JSON.stringify(launchMarkRequest({json.dumps(result)}, {json.dumps(choice)}, "
                                    f"isoNow(1793099040000)))")
            self.assertFalse(value.isError(), value.toString())
            request = json.loads(value.toString())
            with tempfile.TemporaryDirectory(prefix="tide-board-mark-") as tmp:
                from unittest import mock
                with mock.patch.dict(os.environ, git_env(tmp)):
                    board = store_mod.BoardStore(os.path.join(tmp, "board"))
                    board.create_card({"title": "Grabie", "column": "todo", "session": "claude:poprzednia"})
                    self.assertTrue(board.update_card("K-1", request)["ok"])
                    card = board.snapshot()["cards"][0]
            self.assertEqual((card["entry"], card["mode"], card["tool"], card["worktree"]),
                             (choice["entry"], choice["mode"] if choice["entry"] == "planning" else "", choice["tool"],
                              result["worktree"]))
            self.assertEqual(card["branch"], result["branch"] if result["worktree"] else "")
            self.assertEqual(card.get("check"), choice["check"])
            self.assertEqual(card["session"], "", "poprzednia sesja odpięta — usługa przypnie nową")


class LaunchTest(ProbeCase):
    """„Co ma zrobić agent?”: otwarcie po przeniesieniu i z przycisku, Anuluj, Tylko przenieś, przebieg
    UpdateCard → RenderLaunch → Start → UpdateCard, tryb spec bez worktree, karta bez projektu, błędy."""

    FLOW = {"ok": True, "id": "K-15", "mode": "full", "tool": "claude", "branch": "claude/k-15-tryb-skupienia",
            "docsDir": "/home/user/DevWorkspace/plasma-tide/docs/agents", "aiDocs": "repo",
            "repo": "/home/user/DevWorkspace/plasma-tide", "project": "plasma-tide", "text": "Karta K-15: polecenie"}

    def test_move_then_cancel_or_move_only(self):
        out = self.probe("""
drag(card("K-15"), column("todo", fy=0.75))
out["open"] = js("[page.launchOpen, page.launchCardId, page.launchFromMove]") + [obj("launchWhere").property("text")]
out["defaults"] = js("[page.launchDialog.mode, page.launchDialog.tool, page.launchDialog.branch, page.launchDialog.taskEdit.text]")
out["moveOnlyVisible"] = obj("launchMoveOnly").property("visible")
click(named("launchCancel"))
out["cancel"] = calls()[-1]
out["closed"] = not js("page.launchOpen")
out["backlog"] = js("page.cards.filter(x => x.boardColumn === 'backlog').map(x => x.id)")
drag(card("K-14"), column("todo", fy=0.75))
n = len(calls())
click(named("launchMoveOnly"))
out["moveOnlyCalls"] = len(calls()) - n
out["todo"] = js("page.cards.filter(x => x.boardColumn === 'todo').map(x => x.id)")
out["closed2"] = not js("page.launchOpen")
""")
        self.assertEqual(out["open"], [True, "K-15", True, "Backlog → Do zrobienia"])
        self.assertEqual(out["defaults"], ["full", "claude", "claude/k-15-tryb-skupienia-wyciszone",
                                           "Tryb skupienia: wyciszone powiadomienia i przygaszony dock"],
                         "tryb i narzędzie z projektu, zadanie z tytułu (opis pusty)")
        self.assertTrue(out["moveOnlyVisible"])
        self.assertEqual(out["cancel"], ["MoveCard", "K-15", "backlog", 0], "Anuluj — karta wraca na swoje miejsce")
        self.assertTrue(out["closed"])
        self.assertEqual(out["backlog"][0], "K-15")
        self.assertEqual(out["moveOnlyCalls"], 0, "Tylko przenieś — bez agenta i bez zapisu")
        self.assertIn("K-14", out["todo"])
        self.assertTrue(out["closed2"])

    def test_launch_full_flow_sends_requests_in_order(self):
        out = self.probe("""
replies.extend([{"ok": True, "id": "K-15", "changed": True, "commitError": ""},
                {"ok": True, "id": "K-15", "changed": True, "commitError": ""},
                {"ok": False, "error": "branch-exists", "message": "gałąź karty już istnieje"}])
drag(card("K-15"), column("todo", fy=0.75))
obj("launchTask").setProperty("text", "Tryb skupienia: wycisz powiadomienia na 25 min")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(300)
out["inPlace"] = js("[page.launchOpen, page.launchDialog.busy, page.launchDialog.error]")
out["calls1"] = calls()
replies.append({"ok": True, "id": "K-15", "cardBranch": "card/k-15-tryb-skupienia",
                "step": "K-15-plan-1-0", "executor": "Sonnet", "interactive": True})
click(named("launchStart"))
wait(300)
out["calls2"] = calls()[len(out["calls1"]):]
out["closed"] = not js("page.launchOpen")
out["toast"] = js("[page.toast, page.toastTone]")
""")
        self.assertEqual(out["inPlace"], [True, False, "gałąź karty już istnieje"], "błąd w oknie")
        members = [c[0] for c in out["calls1"]]
        self.assertEqual(members, ["MoveCard", "UpdateCard", "StartCard"])
        self.assertEqual(json.loads(out["calls1"][1][2]), {"description": "Tryb skupienia: wycisz powiadomienia na 25 min"},
                         "zmienione zadanie zapisane w karcie przed poleceniem")
        self.assertEqual(json.loads(out["calls1"][2][2]),
                         {"entry": "planning", "mode": "full", "executor": "Sonnet", "check": "auto",
                          "preferredProvider": "anthropic", "images": []})
        self.assertEqual([c[0] for c in out["calls2"]], ["StartCard"],
                         "ponowienie bez drugiego zapisu opisu")
        self.assertTrue(out["closed"])
        self.assertEqual(out["toast"][1], "mint")
        self.assertIn("ruszyło w Konsoli", out["toast"][0])

    def test_spec_mode_from_tile_intent_without_worktree(self):
        flow = json.dumps(dict(self.FLOW, id="K-14", tool="codex", mode="spec", aiDocs="tide",
                               docsDir="/home/user/DevWorkspace/tide-board/docs/mobileapp",
                               repo="/home/user/DevWorkspace/mobileapp"), ensure_ascii=False)
        out = self.probe(FIND_PY + f"""
js("showItem('launch:k-14')")
wait(150)
out["open"] = js("[page.launchOpen, page.launchCardId, page.launchFromMove, page.selectedId]")
out["moveOnlyVisible"] = obj("launchMoveOnly").property("visible")
out["docs"] = obj("launchDocs").property("text")
click(at(FIND + "(page.launchDialog, 'launchEntry-spec')"))
js("page.launchDialog.pickTool('codex')")
js("page.launchDialog.manualCheck = true")
wait(50)
out["branch"] = obj("launchBranch").property("text")
out["worktreeEnabled"] = obj("launchWorktreeToggle").property("enabled")
out["footer"] = obj("launchFooter").property("text")
replies.extend([json.loads({flow!r}),
                {{"ok": True, "cwd": "/home/user/DevWorkspace/mobileapp", "branch": "master", "worktree": "", "tool": "codex",
                  "title": "mobileapp · master", "mode": "tab"}},
                {{"ok": True, "id": "K-14", "changed": True, "commitError": ""}},
                {{"ok": True, "id": "K-14", "changed": True, "commitError": ""}}])
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(300)
out["calls"] = calls()
""")
        self.assertEqual(out["open"], [True, "K-14", False, "K-14"])
        self.assertFalse(out["moveOnlyVisible"], "bez przeniesienia nie ma „Tylko przenieś”")
        self.assertEqual(out["docs"], "w Tide · tide-board/docs/mobileapp/")
        self.assertEqual(out["branch"], "bez gałęzi")
        self.assertFalse(out["worktreeEnabled"], "spec — bez worktree")
        self.assertIn("Codex w katalogu projektu, bez worktree", out["footer"])
        self.assertEqual([c[0] for c in out["calls"]], ["RenderLaunch", "Start", "UpdateCard", "MoveCard"])
        self.assertEqual(json.loads(out["calls"][0][2]), {"entry": "spec", "tool": "codex", "check": "manual"})
        start = json.loads(out["calls"][1][1])
        self.assertEqual((start["newWorktree"], start["branch"], start["addDirs"], start["tool"]),
                         (False, "", ["/home/user/DevWorkspace/tide-board/docs/mobileapp"], "codex"))
        mark = json.loads(out["calls"][2][2])
        self.assertEqual({k: mark[k] for k in ("branch", "worktree", "mode", "tool", "check")},
                         {"branch": "", "worktree": "", "mode": "", "tool": "codex", "check": "manual"})
        self.assertEqual(mark["entry"], "spec")
        self.assertEqual(out["calls"][3], ["MoveCard", "K-14", "todo", -1], "karta z Backlogu idzie do kolejki")

    def test_how_to_start_shows_the_mode_only_for_planning(self):
        out = self.probe(FIND_PY + """
shown = lambda name: js(FIND + f"(page.launchDialog, '{name}').visible")
choose = lambda name: click(at(FIND + f"(page.launchDialog, '{name}')"))
js("page.openLaunch('K-9', false)")
wait(100)
out["default"] = js("[page.launchDialog.entry, page.launchDialog.mode]")
out["entries"] = [shown("launchEntry-planning"), shown("launchEntry-spec"),
                  js(FIND + "(page.launchDialog, 'launchEntry-planning').Accessible.name")]
out["planning"] = [shown("launchModes"), shown("launchMode-full"), shown("launchMode-light")]
choose("launchMode-light")
out["light"] = js("[page.launchDialog.entry, page.launchDialog.mode]")
choose("launchEntry-spec")
wait(50)
out["spec"] = [js("page.launchDialog.entry"), shown("launchModes"), shown("launchMode-light"),
               obj("launchWorktreeToggle").property("enabled")]
choose("launchEntry-planning")
wait(50)
out["back"] = [js("page.launchDialog.entry"), js("page.launchDialog.mode"), shown("launchModes")]
""")
        self.assertEqual(out["default"], ["planning", "full"])
        self.assertEqual(out["entries"][:2], [True, True])
        self.assertIn("Planowanie", out["entries"][2])
        self.assertEqual(out["planning"], [True, True, True])
        self.assertEqual(out["light"], ["planning", "light"])
        self.assertEqual(out["spec"], ["spec", False, False, False], "Tylko specyfikacja: bez wyboru trybu i bez worktree")
        self.assertEqual(out["back"], ["planning", "light", True], "wybrany tryb wraca z Planowaniem")

    def test_project_default_entry_and_legacy_mode_spec(self):
        data = json.loads(json.dumps(self.data))
        for p in data["projects"]:
            if p["slug"] == "plasma-tide":
                p["entry"], p["mode"] = "spec", "light"
            if p["slug"] == "mobileapp":
                p.pop("entry", None)
                p["mode"] = "spec"   # usługa sprzed PT-77 / stary zapis: tryb spec to wejście Tylko specyfikacja
        out = self.probe("""
out["tide"] = js("page.openLaunch('K-9', false), [page.launchDialog.entry, page.launchDialog.mode]")
click(named("launchCancel"))
out["legacy"] = js("page.openLaunch('K-14', false), [page.launchDialog.entry, page.launchDialog.mode]")
""", data)
        self.assertEqual(out["tide"], ["spec", "light"], "domyślne wejście projektu, tryb zapamiętany na Planowanie")
        self.assertEqual(out["legacy"], ["spec", "full"])

    def test_card_button_and_card_without_project(self):
        data = json.loads(json.dumps(self.data))
        for c in data["cards"]:
            if c["id"] == "K-11":
                c["project"], c["projectName"] = "", ""
        out = self.probe("""
click(at("page.findCard('K-9')", 0.5, 1, 0, -24))   # „Uruchom agenta…” na karcie
out["open"] = js("[page.launchOpen, page.launchCardId, page.launchFromMove]")
click(named("launchCancel"))
out["calls0"] = len(calls())
js("page.openLaunch('K-11', false)")
wait(80)
out["blocker"] = [obj("launchBlocker").property("visible"), obj("launchBlockerText").property("text")]
out["canStart"] = js("page.launchDialog.canStart")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
out["calls1"] = len(calls())
js("page.launchDialog.projectChosen('project', 'plasma-tide')")
wait(100)
out["project"] = calls()[-1]
""", data)
        self.assertEqual(out["open"], [True, "K-9", False])
        self.assertEqual(out["calls0"], 0, "Anuluj z przycisku karty niczego nie przenosi")
        self.assertTrue(out["blocker"][0])
        self.assertIn("Karta nie ma projektu", out["blocker"][1])
        self.assertFalse(out["canStart"])
        self.assertEqual(out["calls1"], 0, "bez projektu nie uruchamiamy")
        self.assertEqual(out["project"][:2], ["UpdateCard", "K-11"])
        self.assertEqual(json.loads(out["project"][2]), {"project": "plasma-tide"})


# Element po objectName wewnątrz delegatów Repeatera (findChild ich nie widzi): funkcja QML (korzeń, nazwa).
FIND_PY = """
FIND = "(function find(it, name) { if (!it) return null; if (it.objectName === name) return it; " \\
       "for (const c of it.children) { const r = find(c, name); if (r) return r } return null })"
"""


class ExtraProjectsTest(ProbeCase):
    """PT-28: „Dodatkowe projekty” w oknie uruchomienia (chipy, wybór z listy, repozytorium jako nowy
    projekt) → UpdateCard {projects} przed StartCard; chipy na karcie."""

    def test_pick_extra_projects_then_launch(self):
        out = self.probe(FIND_PY + f"""
replies.extend([{{"ok": True, "id": "K-15", "changed": True, "commitError": ""}},          # MoveCard
                {{"ok": True, "id": "K-15", "changed": True, "commitError": ""}},          # UpdateCard (projekty)
                {{"ok": True, "id": "K-15", "executor": "Sonnet", "interactive": True}}]) # StartCard
def type_text(text):
    for ch in text:
        QTest.keyClick(window, Qt.Key(ord(ch.upper())))
    wait(60)


def pick(text):
    click(named("launchExtraPicker"))
    type_text(text)
    key(Qt.Key.Key_Return)


drag(card("K-15"), column("todo", fy=0.75))
out["visible"] = obj("launchExtras").property("visible")
pick("mobi")
pick("legal")
pick("app4")
out["picked"] = js("page.launchDialog.extras.map(p => p.kind + ':' + p.name)")
click(at(FIND + "(page.launchDialog, 'launchExtraRemove-logviewer')"))
out["afterRemove"] = js("page.launchDialog.extras.map(p => p.key)")
out["footer"] = obj("launchFooter").property("text")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(400)
out["calls"] = calls()
out["closed"] = not js("page.launchOpen")
""")
        self.assertTrue(out["visible"])
        self.assertEqual(out["picked"], ["project:mobileapp", "repo:legal-agents", "project:LogViewer"])
        self.assertEqual(out["afterRemove"], ["mobileapp", "/home/user/DevWorkspace/legal-agents"])
        self.assertIn("Dodatkowe projekty jako kontekst: mobileapp, legal-agents", out["footer"])
        self.assertEqual([c[0] for c in out["calls"]], ["MoveCard", "UpdateCard", "StartCard"])
        self.assertEqual(json.loads(out["calls"][1][2]),
                         {"projects": ["mobileapp", "/home/user/DevWorkspace/legal-agents"]},
                         "projekty (repozytorium zakłada projekt) zapisane przed poleceniem, opis bez zmian")
        start = json.loads(out["calls"][2][2])
        self.assertEqual((start["entry"], start["mode"]), ("planning", "full"))
        self.assertTrue(out["closed"])

    def test_card_shows_extra_projects_as_chips(self):
        data = json.loads(json.dumps(self.data))
        for c in data["cards"]:
            if c["id"] == "K-9":
                c["projects"] = ["mobileapp"]
                c["extraProjects"] = [{"slug": "mobileapp", "name": "mobileapp", "repo": "/r/mobileapp", "repoMissing": False}]
        out = self.probe(FIND_PY + """
out["chip"] = js(FIND + "(page.findCard('K-9'), 'cardExtraProjectText').text")
out["dialog"] = js("page.openLaunch('K-9', false); page.launchDialog.extras.map(p => p.key)")
""", data)
        self.assertEqual(out["chip"], "+ mobileapp")
        self.assertEqual(out["dialog"], ["mobileapp"], "okno startuje z projektami zapisanymi w karcie")


def models_status(**role) -> dict:
    """Status() z /Models z rolą `agent` (domyślnie dostępny qwen3-coder:30b na tym komputerze)."""
    agent = {"endpoint": "local", "model": "qwen3-coder:30b", "ctx": None, "installed": True, "available": True,
             "error": ""}
    agent.update(role)
    return {"checked": True, "default": "local", "agentProgram": "codex",
            "endpoints": {"local": {"url": "http://127.0.0.1:11434", "kind": "ollama", "online": True}},
            "roles": {"agent": agent}}


# Wspólne dla scenariuszy PT-43: katalog zadań w katalogu tymczasowym testu i obrazy PNG.
SHOTS_PY = FIND_PY + """
import os
from PyQt6.QtGui import QColor, QImage
backend.tray.images.root = os.path.join(TMP, "tasks")


def png(name, color="#6fd6c6"):
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    path = os.path.join(TMP, name)
    image.save(path, "PNG")
    return path


def shots():
    return json.loads(backend.imagesJson)
"""


class LaunchShotsTest(ProbeCase):
    """PT-43: okno uruchomienia ma to, co okno „Nowe zadanie”: zrzuty ekranu (przeciąganie, Ctrl+V, „Zrób zrzut”,
    usuwanie) trafiające do /Tasks Start i model chmura/lokalny z roli `agent` w /Models."""

    def scenario(self, body: str) -> str:
        return f"TMP = {self.tmp!r}\n" + SHOTS_PY + body

    def test_drop_paste_remove_and_start_with_images(self):
        out = self.probe(self.scenario(f"""
js("page.openLaunch('K-9', false)")
wait(100)
out["strip"] = [obj("launchShots").property("visible"), js(FIND + "(page.launchDialog, 'shotHint').text")]
# Przeciągnięte pliki (DropArea okna zgłasza filesDropped): obraz kopiowany do katalogu zadania, tekst pominięty.
notes = os.path.join(TMP, "notatki.txt")
open(notes, "w").close()
js("page.launchDialog.filesDropped(['file://" + png("ekran.png") + "', 'file://" + notes + "'])")
wait(100)
out["dropped"] = shots()
out["problem"] = obj("launchError").property("text")
# Ctrl+V z obrazem w schowku w polu zadania: zrzut, tekst zadania bez zmian.
QGuiApplication.clipboard().setImage(QImage(png("schowek.png", "#f5b99a")))
js("page.launchDialog.taskEdit.forceActiveFocus()")
before = js("page.launchDialog.taskEdit.text")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
wait(100)
out["pasted"] = [len(shots()), js("page.launchDialog.taskEdit.text") == before]
# Ctrl+V z tekstem — zwykłe wklejenie do pola.
QGuiApplication.clipboard().setText(" i jeszcze to")
js("page.launchDialog.taskEdit.cursorPosition = page.launchDialog.taskEdit.length")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
wait(100)
out["text"] = [len(shots()), js("page.launchDialog.taskEdit.text").endswith(" i jeszcze to")]
out["thumbs"] = js("page.launchDialog.images.length")
# „×” na pierwszej miniaturze usuwa plik.
first = shots()[0]
name = os.path.basename(first)
click(at(FIND + "(page.launchDialog, 'shotRemove-" + name + "')"))
out["removed"] = [os.path.exists(first), shots()]
kept = shots()
folder = os.path.dirname(kept[0])
replies.extend([{{"ok": True, "id": "K-9", "changed": True, "commitError": ""}},   # UpdateCard (opis)
                {{"ok": True, "id": "K-9", "executor": "Sonnet", "interactive": True}}]) # StartCard
click(named("launchStart"))
wait(400)
out["calls"] = calls()
out["closed"] = not js("page.launchOpen")
out["after"] = [os.path.isfile(kept[0]), shots(), js("page.launchDialog.images.length")]
"""))
        self.assertEqual(out["strip"], [True, "albo przeciągnij plik\nalbo wklej Ctrl+V"])
        self.assertEqual(len(out["dropped"]), 1)
        self.assertTrue(out["dropped"][0].startswith(os.path.join(self.tmp, "tasks") + os.sep), out["dropped"])
        self.assertIn("notatki.txt to nie obraz", out["problem"])
        self.assertEqual(out["pasted"], [2, True], "obraz ze schowka trafia do zrzutów, nie do pola")
        self.assertEqual(out["text"], [2, True], "tekst ze schowka wkleja pole zadania")
        self.assertEqual(out["thumbs"], 2)
        self.assertEqual(out["removed"][0], False)
        self.assertEqual(len(out["removed"][1]), 1)
        self.assertEqual([c[0] for c in out["calls"]], ["UpdateCard", "StartCard"])
        request = json.loads(out["calls"][1][2])
        self.assertEqual((request["images"], request["mode"]), (out["removed"][1], "full"),
                         "StartCard dostaje zrzuty z katalogu zadania")
        self.assertTrue(out["closed"])
        self.assertEqual(out["after"], [True, [], 0], "po starcie katalog zostaje agentowi, następne okno zaczyna od zera")

    def test_cancel_discards_images_and_capture_with_fake_spectacle(self):
        fake = os.path.join(self.tmp, "spectacle")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && cp \"$TIDE_FAKE_SHOT\" \"$2\"; shift; done\n")
        os.chmod(fake, 0o755)
        out = self.probe(self.scenario(f"""
os.environ["TIDE_FAKE_SHOT"] = png("obszar.png")
backend.tray.spectacle = {fake!r}
js("page.canCapture = true")
js("page.openLaunch('K-9', false)")
wait(100)
out["tile"] = js(FIND + "(page.launchDialog, 'captureTile').visible")
click(at(FIND + "(page.launchDialog, 'captureTile')"))
out["hidden"] = not window.isVisible()
until(lambda: len(shots()) == 1, 5000)
until(lambda: window.isVisible(), 2000)
out["captured"] = [len(shots()), window.isVisible()]
folder = os.path.dirname(shots()[0])
click(named("launchCancel"))
wait(100)
out["cancel"] = [os.path.exists(folder), shots(), calls()]
"""))
        self.assertTrue(out["tile"])
        self.assertTrue(out["hidden"], "okno Tide chowa się na czas zaznaczania obszaru")
        self.assertEqual(out["captured"], [1, True])
        self.assertEqual(out["cancel"], [False, [], []], "Anuluj — katalog zadania znika, nic nie wysłane")

    def test_local_model_from_models_status(self):
        flow = dict(LaunchTest.FLOW, id="K-9", tool="codex", branch="codex/k-9-wlasne-okno-kafelek-dockera")
        start = {"ok": True, "cwd": "/w/k-9", "branch": flow["branch"], "worktree": "/w/k-9", "tool": "codex",
                 "title": "plasma-tide · k-9 · lokalnie", "mode": "tab",
                 "local": {"endpoint": "local", "model": "qwen3-coder:30b", "server": "Ten komputer"}}
        data = json.loads(json.dumps(self.data))
        data["models"] = {**models_status(), "roles": {}}
        data["tasks"] = {"tools": {"claude": True, "codex": True}, "model": "cloud"}
        ok, down = json.dumps(models_status()), json.dumps(models_status(available=False, installed=False))
        out = self.probe(self.scenario(f"""
OK, DOWN = {ok!r}, {down!r}


def models(text):
    backend._models = text
    backend.modelsChanged.emit()
    wait(50)


js("page.openLaunch('K-9', false)")
wait(100)
click(at(FIND + "(page.launchDialog, 'launchEntry-spec')"))
d = "page.launchDialog."
# Rola `agent` nie ustawiona: „Lokalny” wyłączony z powodem w podpowiedzi, kliknięcie nic nie zmienia.
out["unset"] = js("[" + d + "localOption.enabled, " + d + "localOption.reason]")
click(at(FIND + "(page.launchDialog, 'launchModel-local')"))
out["unsetModel"] = js(d + "model")
models(OK)
click(at(FIND + "(page.launchDialog, 'launchModel-local')"))
out["local"] = [js(d + "model"), obj("launchTool").property("visible"), obj("launchLocalProgram").property("text"),
                obj("launchLocalDetail").property("text"), js(d + "branch"), obj("launchModelNote").property("text"),
                obj("launchFooter").property("text")]
click(named("launchProgramLink"))
out["openModels"] = calls()[-1]
# Serwer padł przy wybranym „Lokalny”: okno nie przechodzi samo na chmurę, start czeka z powodem.
models(DOWN)
out["blocked"] = [js(d + "model"), js(d + "canStart"), obj("launchFooter").property("text")]
models(OK)
replies.extend([json.loads({json.dumps(flow, ensure_ascii=False)!r}), json.loads({json.dumps(start, ensure_ascii=False)!r}),
                {{"ok": True, "id": "K-9", "changed": True, "commitError": ""}}])
n = len(calls())
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(400)
out["calls"] = calls()[n:]
out["toast"] = js("page.toast")
# Brak programu: start zablokowany z powodem.
js("page.openLaunch('K-9', false)")
js("page.taskDefaults = ({{tools: {{claude: false, codex: true}}, model: 'cloud'}})")
js(d + "model = 'cloud'; " + d + "pickTool('claude')")
wait(50)
out["missing"] = [js(d + "canStart"), obj("launchFooter").property("text")]
"""), data)
        self.assertEqual(out["unset"], [False, "Model agenta nie jest ustawiony"])
        self.assertEqual(out["unsetModel"], "cloud", "wyłączony „Lokalny” nie daje się wybrać")
        model, tool_visible, program, detail, branch, note, footer = out["local"]
        self.assertEqual((model, tool_visible), ("local", False), "przy „Lokalny” zamiast przełącznika linia agenta")
        self.assertEqual((program, detail), ("◎ Codex", " na qwen3-coder:30b · Ten komputer"))
        self.assertEqual(branch, "codex/k-9-wlasne-okno-kafelek-dockera", "prefiks gałęzi idzie za programem agenta lokalnego")
        self.assertIn("agent lokalny jest wolny i słaby", note)
        self.assertIn("Codex na qwen3-coder:30b", footer)
        self.assertEqual(out["openModels"], ["openModels"])
        self.assertEqual(out["blocked"][:2], ["local", False])
        self.assertIn("Model lokalny niedostępny", out["blocked"][2])
        self.assertEqual([c[0] for c in out["calls"]], ["RenderLaunch", "Start", "UpdateCard"])
        self.assertEqual(json.loads(out["calls"][0][2])["tool"], "codex", "polecenie trybu dla programu, który ruszy")
        request = json.loads(out["calls"][1][1])
        self.assertEqual((request["model"], request["tool"]), ("local", "codex"))
        self.assertIn("na qwen3-coder:30b", out["toast"])
        self.assertFalse(out["missing"][0])
        self.assertIn("Nie znaleziono programu Claude Code", out["missing"][1])


EXECUTORS = {"list": [
    {"name": "Haiku", "program": "claude", "model": "haiku", "provider": "anthropic", "local": False},
    {"name": "Sonnet", "program": "claude", "model": "sonnet", "provider": "anthropic", "local": False},
    {"name": "Opus", "program": "claude", "model": "opus", "provider": "anthropic", "local": False},
    {"name": "Codex standard", "program": "codex", "model": "gpt-6.1-sol", "provider": "openai", "local": False},
    {"name": "Lokalny", "program": "codex", "model": "qwen3-coder:30b", "provider": "local", "local": True}]}


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class LaunchPlanTest(ProbeCase):
    def test_full_planning_warns_when_selected_provider_has_low_limit(self):
        data = json.loads(json.dumps(self.data))
        data["models"] = dict(models_status(), executors=EXECUTORS)
        data["limits"] = {"available": True, "services": {
            "claude": {"session": {"remaining": 8, "reason": "tooLittleData"},
                       "weekly": {"remaining": None, "reason": "noData"}}}}
        out = self.probe(FIND_PY + """
js("page.openLaunch('K-15', false)")
js("page.launchDialog.entry = 'planning'; page.launchDialog.mode = 'full'; page.launchDialog.executor = 'Sonnet'")
wait(50)
out["low"] = [obj("launchPlanningLimitWarning").property("visible"),
              obj("launchPlanningLimitWarning").property("text")]
backend._limits = '{"available":false,"services":{"claude":{"session":{"remaining":8}}}}'
backend.limitsChanged.emit()
wait(50)
out["unavailable"] = obj("launchPlanningLimitWarning").property("visible")
""", data=data)
        self.assertTrue(out["low"][0])
        self.assertIn("8%", out["low"][1])
        self.assertFalse(out["unavailable"])

    def test_bound_plan_is_selected_but_cardless_plan_is_only_suggested(self):
        data = json.loads(json.dumps(self.data))
        exact = "/repo/docs/agents/plans/2026-10-28-exact.md"
        hint = "/repo/docs/agents/plans/2026-10-29-hint.md"
        for card in data["cards"]:
            if card["id"] == "K-15":
                card["planPath"] = ""
                card["planCandidates"] = [
                    {"path": hint, "title": "Podpowiedź", "card": "", "status": "active", "updated": "2026-10-29", "updatedTs": 2},
                    {"path": exact, "title": "Plan karty", "card": "K-15", "status": "active", "updated": "2026-10-28", "updatedTs": 1}]
        out = self.probe(FIND_PY + """
js("page.openLaunch('K-15', false)")
wait(100)
out["auto"] = js("[page.launchDialog.entry, page.launchDialog.planPath, page.launchDialog.planCandidates.map(p => p.path)]")
out["choice"] = js(FIND + "(page.launchDialog, 'launchPlanCandidates').visible")
out["request"] = js("page.launchDialog.launchState.planPath")
""", data)
        self.assertEqual(out["auto"], ["plan", exact, [exact, hint]])
        self.assertTrue(out["choice"])
        self.assertEqual(out["request"], exact)

        for card in data["cards"]:
            if card["id"] == "K-15":
                card["planCandidates"] = [card["planCandidates"][0]]
        out = self.probe(FIND_PY + """
js("page.openLaunch('K-15', false)")
wait(100)
out["hint"] = js("[page.launchDialog.entry, page.launchDialog.planPath, page.launchDialog.planCandidates.length]")
click(at(FIND + "(page.launchDialog, 'launchEntry-plan')"))
wait(50)
out["before"] = js("page.launchDialog.canStart")
js("page.launchDialog.planPath = page.launchDialog.planCandidates[0].path")
out["after"] = js("[page.launchDialog.entry, page.launchDialog.canStart]")
""", data)
        self.assertEqual(out["hint"], ["planning", "", 1])
        self.assertFalse(out["before"])
        self.assertEqual(out["after"], ["plan", True])

    def test_selected_plan_is_saved_on_card_before_start(self):
        data = json.loads(json.dumps(self.data))
        path = "/repo/docs/agents/plans/2026-10-29-import.md"
        for card in data["cards"]:
            if card["id"] == "K-15":
                card["planPath"] = ""
                card["planCandidates"] = [{"path": path, "title": "Plan karty", "card": "K-15",
                                           "status": "active", "updated": "2026-10-29", "updatedTs": 1}]
        out = self.probe("""
js("page.openLaunch('K-15', false)")
wait(100)
replies.extend([{"ok": True, "id": "K-15", "changed": True, "commitError": ""},
                {"ok": True, "id": "K-15", "imported": True, "needsCompletion": False}])
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(300)
out["calls"] = calls()
out["closed"] = not js("page.launchOpen")
""", data)
        self.assertEqual([call[0] for call in out["calls"][:2]], ["UpdateCard", "StartCard"])
        self.assertEqual(json.loads(out["calls"][0][2]), {"planPath": path})
        request = json.loads(out["calls"][1][2])
        self.assertEqual((request["entry"], request["planPath"]), ("import", path))
        self.assertTrue(out["closed"])


class LaunchNowTest(ProbeCase):
    """PT-58: wejście „Od razu” — wykonawca Claude z katalogu wykonawców (Status() z /Models), zlecenie przez
    /Board StartCard (bez RenderLaunch, /Tasks Start i Konsoli); pracującą kartę z dziennikiem da się przeciągnąć
    do Backlogu (wstrzymanie) i do Gotowych (koniec)."""

    def test_cloud_executor_ignores_hidden_local_model_default(self):
        data = json.loads(json.dumps(self.data))
        data["tasks"] = {"tools": {"claude": True, "codex": True}, "model": "local"}
        data["models"] = dict(models_status(available=False, installed=False,
                                             error="Serwer lokalny nie odpowiada"), executors=EXECUTORS["list"])
        out = self.probe(FIND_PY + """
js("page.openLaunch('K-15', false)")
wait(100)
click(at(FIND + "(page.launchDialog, 'launchEntry-now')"))
wait(50)
out["state"] = js("[page.launchDialog.model, page.launchDialog.executor, page.launchDialog.modelBlocker, page.launchDialog.canStart]")
""", data)
        self.assertEqual(out["state"], ["local", "Sonnet", "", True])

    def test_preferred_provider_filters_background_executors_in_dialog(self):
        status = dict(models_status(), executors=EXECUTORS["list"])
        out = self.probe(FIND_PY + f"""
backend._models = {json.dumps(status, ensure_ascii=False)!r}
backend.modelsChanged.emit()
wait(50)
js("page.openLaunch('K-15', false)")
wait(100)
out["providerVisible"] = js(FIND + "(page.launchDialog, 'launchPreferredProvider').visible")
click(at(FIND + "(page.launchDialog, 'launchEntry-now')"))
out["claudePolicyNotice"] = js(FIND + "(page.launchDialog, 'launchCodexCommandPolicyNotice').visible")
click(at(FIND + "(page.launchDialog, 'launchPreferredProvider-openai')"))
wait(50)
out["openai"] = [js("page.launchDialog.preferredProvider"),
                 js("page.launchDialog.executorOptions.map(o => o.value)"), js("page.launchDialog.executor")]
out["codexPolicyNotice"] = [js(FIND + "(page.launchDialog, 'launchCodexCommandPolicyNotice').visible"),
                            js(FIND + "(page.launchDialog, 'launchCodexCommandPolicyNotice').text")]
click(at(FIND + "(page.launchDialog, 'launchPreferredProvider-local')"))
wait(50)
out["local"] = [js("page.launchDialog.executorOptions.map(o => o.value)"), js("page.launchDialog.executor")]
""")
        self.assertTrue(out["providerVisible"])
        self.assertFalse(out["claudePolicyNotice"])
        self.assertEqual(out["openai"], ["openai", ["Codex standard"], "Codex standard"])
        self.assertTrue(out["codexPolicyNotice"][0])
        self.assertIn("wskazówką", out["codexPolicyNotice"][1])
        self.assertEqual(out["local"], [["Lokalny"], "Lokalny"])

    def test_now_entry_picks_a_claude_executor_and_sends_start_card(self):
        status = dict(models_status(), executors=EXECUTORS)
        out = self.probe(FIND_PY + f"""
backend._models = {json.dumps(status, ensure_ascii=False)!r}
backend.modelsChanged.emit()
wait(50)
shown = lambda name: js(FIND + f"(page.launchDialog, '{{name}}').visible")
choose = lambda name: click(at(FIND + f"(page.launchDialog, '{{name}}')"))
js("page.openLaunch('K-15', false)")
wait(100)
out["entries"] = [shown("launchEntry-planning"), shown("launchEntry-spec"), shown("launchEntry-now")]
out["planning"] = [shown("launchExecutor"), shown("launchModel")]
choose("launchEntry-now")
wait(50)
out["now"] = [js("page.launchDialog.entry"), shown("launchExecutor"), shown("launchModes"), shown("launchModel"),
              shown("launchWorktree"), shown("launchExtras")]
out["options"] = js("page.launchDialog.executorOptions.map(o => o.value)")
out["default"] = js("page.launchDialog.executor")
choose("launchExecutor-Opus")
out["reviewer"] = [shown("launchReviewer"), js("page.launchDialog.reviewer"),
                    js("page.launchDialog.reviewerOptions.map(o => o.value)")]
js("page.launchDialog.manualCheck = true")
out["checkManual"] = obj("launchCheckNote").property("text")
js("page.launchDialog.manualCheck = false")
out["checkAuto"] = obj("launchCheckNote").property("text")
js("page.launchDialog.manualCheck = true")
out["footer"] = obj("launchFooter").property("text")
replies.append({{"ok": False, "error": "flow-active", "message": "K-15: krok tej karty jeszcze trwa"}})
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(300)
out["failed"] = [js("page.launchOpen"), js("page.launchDialog.busy"), js("page.launchDialog.error")]
replies.extend([{{"ok": True, "id": "K-15", "ticket": "PT-7", "branch": "ticket/pt-7-tryb", "cardBranch": "card/k-15-tryb",
                  "step": "K-15-PT-7-1", "executor": "Opus"}},
                {{"ok": True, "id": "K-15", "changed": True, "commitError": ""}}])
n = len(calls())
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(300)
out["calls"] = calls()
out["second"] = calls()[n:]
out["closed"] = not js("page.launchOpen")
out["toast"] = js("[page.toast, page.toastTone]")
""")
        self.assertEqual(out["entries"], [True, True, True])
        self.assertEqual(out["planning"], [False, False], "w Planowaniu widać wybór planującego")
        self.assertEqual(out["now"], ["now", True, False, False, False, False],
                         "„Od razu”: wykonawca zamiast trybu, modelu, worktree i dodatkowych projektów")
        self.assertEqual(out["options"], ["Haiku", "Sonnet", "Opus"], "tylko Claude w chmurze z katalogu")
        self.assertEqual(out["default"], "Sonnet")
        self.assertEqual(out["reviewer"], [True, "Sonnet", ["Haiku", "Sonnet"]])
        self.assertIn("poczeka na Twój test ręczny", out["checkManual"])
        self.assertIn("zostanie scalony z gałęzią karty", out["checkAuto"])
        self.assertNotIn("review AI", out["checkManual"] + out["checkAuto"])
        self.assertIn("Opus", out["footer"])
        self.assertIn("bez Konsoli", out["footer"])
        self.assertEqual(out["failed"], [True, False, "K-15: krok tej karty jeszcze trwa"], "błąd zostaje w oknie")
        self.assertNotIn("RenderLaunch", [c[0] for c in out["calls"]])
        self.assertNotIn("Start", [c[0] for c in out["calls"]])
        self.assertEqual([c[0] for c in out["second"]], ["StartCard", "MoveCard"])
        self.assertEqual(out["second"][0][1], "K-15")
        self.assertEqual(json.loads(out["second"][0][2]),
                         {"entry": "now", "executor": "Opus", "reviewer": "Sonnet",
                          "preferredProvider": "anthropic", "check": "manual", "images": []})
        self.assertEqual(out["second"][1], ["MoveCard", "K-15", "todo", -1], "karta z Backlogu idzie do kolejki")
        self.assertTrue(out["closed"])
        self.assertEqual(out["toast"][1], "mint")
        self.assertIn("PT-7", out["toast"][0])
        self.assertIn("Opus", out["toast"][0])

    def test_review_verdict_is_visible_in_card_panel(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "note": "poprawki po review",
                                   "flow": {"ticket": "PT-7", "verdict": "REQUEST_CHANGES",
                                            "blockers": 1, "majors": 2, "nits": 3, "round": 2}}
                card["boardColumn"] = "waiting"
        out = self.probe('''
click(card("K-6"))
out["verdict"] = [obj("cardReviewVerdict").property("visible"), obj("cardReviewVerdict").property("text")]
''', data=data)
        self.assertEqual(out["verdict"], [True, "Review REQUEST_CHANGES · BLOCKER 1 · MAJOR 2 · NIT 3 · runda 2"])

    def test_review_question_has_working_decision_button(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "note": "po 3 rundach review",
                                   "flow": {"ticket": "PT-7", "verdict": "REQUEST_CHANGES",
                                            "blockers": 0, "majors": 1, "nits": 0, "round": 3,
                                            "reviewQuestion": True}}
                card["boardColumn"] = "waiting"
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["visible"] = [obj("cardReviewDecision").property("visible"), obj("cardReviewRetry").property("enabled")]
click(at(FIND + "(page.cardPanel, 'cardReviewRetry')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(out["visible"], [True, True])
        self.assertEqual(out["call"], ["ResolveCardReview", "K-6", "retry"])

    def test_working_flow_card_drags_to_backlog_and_done_but_a_coordinator_card_does_not(self):
        data = json.loads(json.dumps(self.data))
        for c in data["cards"]:
            if c["id"] == "K-6":
                c["derived"] = dict(c.get("derived") or {}, column="progress", link="flow", coordinator="", session="",
                                    flow={"ticket": "PT-7", "step": "K-6-PT-7-1", "executor": "Sonnet"})
                c["boardColumn"] = "progress"
        out = self.probe("""
drag(card("K-6"), column("backlog", fy=0.8))
out["paused"] = calls()[-1] if calls() else None
set_snapshot(json.loads(backend.snapshotJson))
drag(card("K-6"), column("done", fy=0.8))
out["done"] = calls()[-1]
n = len(calls())
drag(card("K-5"), column("backlog", fy=0.8))
out["legacy"] = len(calls()) - n
""", data)
        self.assertEqual(out["paused"][:3], ["MoveCard", "K-6", "backlog"], "pracująca karta z dziennikiem — wstrzymanie")
        self.assertEqual(out["done"][:3], ["MoveCard", "K-6", "done"])
        self.assertEqual(out["legacy"], 0, "karta dawnego przepływu jak dotąd")


class StepDecisionPanelTest(ProbeCase):
    def flow_card(self, action, **extra):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "note": "krok czeka",
                                   "flow": {"ticket": "PT-7", "action": action, **extra}}
                card["boardColumn"] = "waiting"
        return data

    def test_question_is_visible_and_answer_goes_to_service(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["visible"] = [obj("cardStepDecision").property("visible"), obj("cardStepAnswer").property("visible")]
js("page.cardPanel.stepAnswer = 'ISO 8601'")
out["enabled"] = obj("cardStepAnswerSend").property("enabled")
js(FIND + "(page.cardPanel, 'cardStepAnswerSend').clicked()")
out["call"] = calls()[-1]
""", data=self.flow_card("answer", question="Który format daty?"))
        self.assertEqual(out["visible"], [True, True])
        self.assertTrue(out["enabled"])
        self.assertEqual(out["call"][:2], ["ResolveCardStep", "K-6"])
        self.assertEqual(json.loads(out["call"][2]), {"choice": "answer", "answer": "ISO 8601"})

    def test_permission_has_exact_command_and_forbidden_cannot_be_allowed(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["buttons"] = [obj("cardStepAllow").property("enabled"), obj("cardStepDeny").property("enabled")]
click(at(FIND + "(page.cardPanel, 'cardStepDeny')"))
out["call"] = calls()[-1]
""", data=self.flow_card("permission", command="make install", canAllow=False))
        self.assertEqual(out["buttons"], [False, True])
        self.assertEqual(json.loads(out["call"][2]), {"choice": "deny"})

    def test_error_shows_retry_and_change_executor(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["visible"] = [obj("cardStepRetry").property("visible"), obj("cardStepChangeExecutor").property("visible")]
js("page.cardPanel.stepExecutor = 'Haiku'")
click(at(FIND + "(page.cardPanel, 'cardStepChangeExecutor')"))
out["call"] = calls()[-1]
""", data=self.flow_card("error", error="bez postępu", stalledSince="2026-10-29T01:00:00Z",
                         provider="anthropic", executor="Sonnet", reviewerModel="opus"))
        self.assertEqual(out["visible"], [True, True])
        self.assertEqual(json.loads(out["call"][2]), {"choice": "change-executor", "executor": "Haiku"})

    def test_tracker_error_has_retry_without_changing_executor(self):
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["visible"] = [obj("cardStepRetry").property("visible"), obj("cardStepExecutor").property("visible"),
                  obj("cardStepChangeExecutor").property("visible")]
click(at(FIND + "(page.cardPanel, 'cardStepRetry')"))
out["call"] = calls()[-1]
""", data=self.flow_card("error", error="YouTrack odpowiedział 400", trackerError=True))
        self.assertEqual(out["visible"], [True, False, False])
        self.assertEqual(out["call"][:2], ["ResolveCardStep", "K-6"])
        self.assertEqual(json.loads(out["call"][2]), {"choice": "retry"})

    def test_exhausted_limit_offers_transfer_or_wait(self):
        data = self.flow_card("limit", limit={"provider": "anthropic", "reset": "piątek 15:00",
                                               "reason": "Limit Claude wyczerpany"}, provider="anthropic")
        data["models"] = dict(models_status(), executors=EXECUTORS)
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["label"] = obj("cardStepDecision").property("visible")
out["text"] = obj("cardStepDecisionText").property("text")
out["before"] = [obj("cardLimitTransfer").property("enabled"), obj("cardLimitWait").property("enabled")]
js("page.cardPanel.limitExecutor = 'Codex standard'")
replies.append({"ok": True, "id": "K-6", "choice": "transfer"})
click(at(FIND + "(page.cardPanel, 'cardLimitTransfer')"))
out["transfer"] = calls()[-1]
out["transferNotice"] = js("page.cardPanel.notice")
""", data=data)
        self.assertTrue(out["label"])
        self.assertIn("piątek 15:00", out["text"])
        self.assertEqual(out["before"], [False, True])
        self.assertEqual(out["transfer"][:2], ["ResolveCardStep", "K-6"])
        self.assertEqual(json.loads(out["transfer"][2]), {"choice": "transfer", "executor": "Codex standard"})
        self.assertEqual(out["transferNotice"], "Przekazuję krok innemu dostawcy.")

        out = self.probe(FIND_PY + """
click(card("K-6"))
replies.append({"ok": True, "id": "K-6", "choice": "wait-limit"})
click(at(FIND + "(page.cardPanel, 'cardLimitWait')"))
out["call"] = calls()[-1]
out["notice"] = js("page.cardPanel.notice")
""", data=data)
        self.assertEqual(json.loads(out["call"][2]), {"choice": "wait-limit"})
        self.assertEqual(out["notice"], "Krok czeka na odnowienie limitu.")

    def test_exhausted_limit_reset_appears_once_when_reason_includes_it(self):
        data = self.flow_card("limit", limit={"provider": "anthropic", "reset": "piątek 15:00",
                                               "reason": "Limit Claude wyczerpany do piątek 15:00"},
                              provider="anthropic")
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["text"] = obj("cardStepDecisionText").property("text")
""", data=data)
        self.assertEqual(out["text"].count("piątek 15:00"), 1)
        self.assertNotIn("· odnowienie", out["text"])

    def test_running_step_can_be_transferred_to_another_provider(self):
        data = self.flow_card("", steps=[{"id": "step-1", "ticket": "PT-7", "result": "", "kind": "ticket",
                                              "executor": "Sonnet", "provider": "anthropic",
                                              "canTransfer": True, "canEnter": False}])
        data["models"] = dict(models_status(), executors=EXECUTORS)
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["before"] = js(FIND + "(page.cardPanel, 'cardStepTransfer') !== null")
if out["before"]:
    out["enabled"] = js(FIND + "(page.cardPanel, 'cardStepTransfer').enabled")
    out["targets"] = js(FIND + "(page.cardPanel, 'cardStepTransferExecutor').model.map(o => o.value)")
    js("page.cardPanel.flowRepeater.itemAt(0).transferExecutor = 'Codex standard'")
    click(at(FIND + "(page.cardPanel, 'cardStepTransfer')"))
    out["call"] = calls()[-1]
""", data=data)
        self.assertTrue(out["before"], out)
        self.assertFalse(out["enabled"])
        self.assertIn("Codex standard", out["targets"])
        self.assertNotIn("Haiku", out["targets"])
        self.assertEqual(json.loads(out["call"][2]),
                         {"choice": "transfer", "step": "step-1", "executor": "Codex standard"})

    def test_open_full_planning_can_be_transferred_from_console(self):
        data = self.flow_card("", entry="planning", mode="full",
                              steps=[{"id": "K-6-plan-1-0", "ticket": "", "kind": "planning",
                                      "mode": "interactive", "result": "", "executor": "Sonnet",
                                      "provider": "anthropic", "canTransfer": True, "canEnter": True}])
        data["models"] = dict(models_status(), executors=EXECUTORS)
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"]["column"] = "progress"
                card["boardColumn"] = "progress"
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["visible"] = js(FIND + "(page.cardPanel, 'cardStepTransfer').visible")
out["before"] = js(FIND + "(page.cardPanel, 'cardStepTransfer').enabled")
out["targets"] = js(FIND + "(page.cardPanel, 'cardStepTransferExecutor').model.map(o => o.value)")
js("page.cardPanel.flowRepeater.itemAt(0).transferExecutor = 'Codex standard'")
click(at(FIND + "(page.cardPanel, 'cardStepTransfer')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertTrue(out["visible"])
        self.assertFalse(out["before"])
        self.assertIn("Codex standard", out["targets"])
        self.assertNotIn("Haiku", out["targets"])
        self.assertEqual(out["call"][:2], ["ResolveCardStep", "K-6"])
        self.assertEqual(json.loads(out["call"][2]),
                         {"choice": "transfer", "step": "K-6-plan-1-0", "executor": "Codex standard"})

    def test_auto_transfer_setting_is_saved_from_manage_page(self):
        data = json.loads(json.dumps(self.data))
        data["models"] = dict(models_status(), executors=EXECUTORS)
        data["flowSettings"] = {"ok": True, "limitTransfer": {"mode": "ask", "executor": ""},
                                "limitThresholdPercent": 10}
        out = self.probe(FIND_PY + """
js("page.managing = true")
wait(50)
out["initial"] = [js("page.managePage.limitTransferMode"), obj("limitTransferSave").property("enabled")]
js("page.managePage.limitTransferMode = 'auto'; page.managePage.limitTransferExecutor = 'Codex standard'; page.managePage.flowSettingsDirty = true")
out["targetVisible"] = obj("limitTransferExecutor").property("visible")
click(at(FIND + "(page.managePage, 'limitTransferSave')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(out["initial"], ["ask", False])
        self.assertTrue(out["targetVisible"])
        self.assertEqual(out["call"][0], "SetCardFlowSettings")
        self.assertEqual(json.loads(out["call"][1]),
                         {"limitTransfer": {"mode": "auto", "executor": "Codex standard"}})

    def test_project_command_lists_are_editable_and_saved(self):
        data = json.loads(json.dumps(self.data))
        data["projects"] = [p for p in data["projects"] if p["slug"] == "plasma-tide"]
        data["projects"][0].update(allowedCommands=["git status", "git log"], deniedCommands=["make install"])
        out = self.probe(FIND_PY + """
find = lambda name: FIND + f"(page.managePage, '{name}')"
js("page.managing = true")
js("page.managePage.openProject = 'plasma-tide'")
wait(100)
out["initial"] = [js(find("projectAllowedCommands") + ".text"),
                  js(find("projectDeniedCommands") + ".text")]
js(find("projectAllowedCommands") + ".text = 'git status\\ngit log\\ngit fetch origin'")
js(find("manageProjectsFlick") + ".contentY = Math.max(0, " + find("manageProjectsFlick") + ".contentHeight - " + find("manageProjectsFlick") + ".height)")
wait(60)
click(at(find("projectSave")))
wait(80)
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(out["initial"], ["git status\ngit log", "make install"])
        self.assertEqual(json.loads(out["call"][-1]),
                         {"slug": "plasma-tide", "allowedCommands": ["git status", "git log", "git fetch origin"]})

class PlanPanelTest(ProbeCase):
    def test_failed_revision_can_be_returned_again_or_discarded(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "flow": {"entry": "planning", "plan": {
                    "status": "approved", "review": {"verdict": "APPROVE"}, "rows": [], "waves": [],
                    "revision": {"seq": 12, "failed": True}, "errors": ["Nieprawidłowa poprawka"]}}}
                card["boardColumn"] = "waiting"
        out = self.probe(FIND_PY + """
click(card("K-6"))
obj("cardPlanFeedback").setProperty("text", "Popraw ponownie")
wait(50)
out["retryEnabled"] = obj("cardPlanReturn").property("enabled")
click(at(FIND + "(page.cardPanel, 'cardPlanDiscardRevision')"))
wait(50)
out["call"] = calls()[-1]
""", data=data)
        self.assertTrue(out["retryEnabled"])
        self.assertEqual(out["call"][:2], ["EditCardPlan", "K-6"])
        self.assertEqual(json.loads(out["call"][2])["action"], "discard-revision")


    def test_plan_review_and_ticket_editor_send_changes_to_service(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "flow": {"entry": "planning", "plan": {
                    "status": "pending", "review": {"verdict": "REQUEST_CHANGES", "reviewer": "Opus", "findings": "Uzupełniono ryzyka."},
                    "waves": [["#1"]], "rows": [{"workId": "#1", "title": "Eksport", "class": "standard",
                        "executor": "Sonnet", "reviewer": "Opus", "blockers": [], "note": "", "editable": True,
                        "body": "# Eksport\n- [ ] Eksport działa.", "cardPath": "/board/tickets/1.md"}]}}}
                card["boardColumn"] = "waiting"
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["review"] = obj("cardPlanReview").property("text")
click(at(FIND + "(page.cardPanel, 'cardPlanTicket-1')"))
js("page.cardPanel.planEditTicket = page.cardPanel.flowPlan.rows[0]")
obj("cardPlanExecutor").setProperty("text", "Opus")
obj("cardPlanReviewer").setProperty("text", "Sonnet")
click(at(FIND + "(page.cardPanel, 'cardPlanSaveTicket')"))
wait(80)
out["call"] = calls()[-1]
""", data=data)
        self.assertIn("Uzupełniono ryzyka.", out["review"])
        self.assertEqual(out["call"][:2], ["EditCardPlan", "K-6"])
        request = json.loads(out["call"][2])
        self.assertEqual((request["action"], request["ticket"], request["executor"], request["reviewer"]),
                         ("edit", "#1", "Opus", "Sonnet"))


    def test_plan_from_snapshot_shows_waves_and_approval_calls_service(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "waiting", "note": "plan do zatwierdzenia",
                                   "flow": {"entry": "plan", "plan": {
                                       "status": "pending", "errors": [],
                                       "waves": [["#1"], ["#2"]],
                                       "rows": [
                                           {"workId": "#1", "title": "Pierwszy", "executor": "Sonnet",
                                            "reviewer": "Opus", "blockers": [], "note": "pilne",
                                            "cardPath": "/board/flow/K-6/plan/tickets/1.md"},
                                           {"workId": "#2", "title": "Drugi", "executor": "Codex mocny",
                                            "reviewer": "Codex Astra", "blockers": ["#1"], "note": "po pierwszym",
                                            "cardPath": "/board/flow/K-6/plan/tickets/2.md"}]}}}
                card["boardColumn"] = "waiting"
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["entries"] = js("page.cardPanel.planEntries().map(r => [r.wave, r.workId, r.executor, r.reviewer, r.blockers])")
out["shown"] = [obj("cardPlanProposal").property("visible"), obj("cardPlanApprove").property("visible")]
click(at(FIND + "(page.cardPanel, 'cardPlanTicket-1')"))
out["ticketCall"] = calls()[-1]
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(out["entries"], [[1, "#1", "Sonnet", "Opus", []],
                                          [2, "#2", "Codex mocny", "Codex Astra", ["#1"]]])
        self.assertEqual(out["shown"], [True, True])
        self.assertEqual(out["ticketCall"], ["openFile", "/board/flow/K-6/plan/tickets/1.md"])
        self.assertEqual(out["call"], ["ApproveCardPlan", "K-6"])


class ClosingPanelTest(ProbeCase):
    def test_closed_card_exposes_diff_main_merge_and_return(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "review", "flow": {
                    "closed": True, "summary": "Zrobiono eksport. Co przetestować ręcznie i jak: otwórz plik.",
                    "mainMerged": False, "plan": {"status": "done", "rows": [], "waves": [], "revision": {}}}}
                card["boardColumn"] = "review"
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["shown"] = obj("cardFlowClosing").property("visible")
out["summary"] = js("page.cardPanel.card.derived.flow.summary")
click(at(FIND + "(page.cardPanel, 'cardBranchDiff')"))
out["diffCall"] = calls()[-1]
obj("cardClosingFeedback").setProperty("text", "Dopisz błąd eksportu")
click(at(FIND + "(page.cardPanel, 'cardReturnForChanges')"))
out["returnCall"] = calls()[-1]
click(at(FIND + "(page.cardPanel, 'cardMergeMain')"))
out["mergeCall"] = calls()[-1]
""", data=data)
        self.assertTrue(out["shown"])
        self.assertIn("Zrobiono eksport", out["summary"])
        self.assertEqual(out["diffCall"], ["CardBranchDiff", "K-6"])
        self.assertEqual(out["mergeCall"], ["MergeCardToMain", "K-6"])
        self.assertEqual(out["returnCall"][:2], ["EditCardPlan", "K-6"])
        self.assertEqual(json.loads(out["returnCall"][2])["feedback"], "Dopisz błąd eksportu")


class WavePanelTest(ProbeCase):
    def test_wave_tickets_timeline_and_pause_are_read_from_file_snapshot(self):
        data = json.loads(json.dumps(self.data))
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["boardColumn"] = "progress"
                card["derived"] = {"column": "progress", "note": "fala 1 z 2", "done": 1, "total": 3,
                    "tickets": [{"id": "PT-1", "state": "gotowe", "wave": 1, "waitReason": ""},
                                {"id": "PT-2", "state": "czeka", "wave": 1, "waitReason": "limit kroków dostawcy anthropic: 3/3"},
                                {"id": "PT-3", "state": "todo", "wave": 2, "waitReason": ""}],
                    "flow": {"entry": "plan", "waveLabel": "fala 1 z 2", "paused": False,
                        "timeline": [{"seq": 1, "ts": "2026-10-29T10:00:00Z", "type": "step.started", "ticket": "PT-1", "kind": "ticket", "executor": "Sonnet"},
                                     {"seq": 2, "ts": "2026-10-29T10:01:00Z", "type": "step.waiting", "ticket": "PT-2", "reason": "limit kroków dostawcy anthropic: 3/3"}],
                        "steps": [{"id": "K-6-PT-1-1", "ticket": "PT-1", "kind": "ticket", "executor": "Sonnet", "result": "done", "canEnter": True}]}}
        out = self.probe(FIND_PY + """
click(card("K-6"))
out["wave"] = obj("cardWaveLabel").property("text")
out["tileWave"] = js(FIND + "(page.findCard('K-6'), 'cardTileWaveLabel').text")
out["tileWaveVisible"] = js(FIND + "(page.findCard('K-6'), 'cardTileWaveLabel').visible")
out["timeline"] = obj("cardTimeline").property("count")
out["tickets"] = obj("cardTicketStrip").property("count")
out["gap"] = js(FIND + "(page.findCard('K-6'), 'cardTicketStrip').itemAt(2).gap")
out["pause"] = obj("cardPause").property("text")
click(at(FIND + "(page.cardPanel, 'cardPause')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(out["wave"], "fala 1 z 2")
        self.assertEqual(out["tileWave"], "fala 1 z 2")
        self.assertTrue(out["tileWaveVisible"])
        self.assertEqual(out["timeline"], 2)
        self.assertEqual(out["tickets"], 3)
        self.assertTrue(out["gap"], "między falami mini-pasek ma przerwę")
        self.assertEqual(out["pause"], "Wstrzymaj kartę")
        self.assertEqual(out["call"], ["SetCardPaused", "K-6", True])
        next(card for card in data["cards"] if card["id"] == "K-6")["derived"]["flow"]["paused"] = True
        resumed = self.probe(FIND_PY + """
click(card("K-6"))
out["text"] = obj("cardPause").property("text")
click(at(FIND + "(page.cardPanel, 'cardPause')"))
out["call"] = calls()[-1]
""", data=data)
        self.assertEqual(resumed["text"], "Wznów")
        self.assertEqual(resumed["call"], ["SetCardPaused", "K-6", False])



class LaunchReviewTest(ProbeCase):
    """PT-43 po review: upuszczenie pliku na pole zadania, Tab do „Zrób zrzut”, schowek z tekstem i obrazem,
    otwarcie okna w trakcie startu, Start bez odpowiedzi, Defaults po otwarciu okna."""

    def scenario(self, body: str) -> str:
        return f"TMP = {self.tmp!r}\n" + SHOTS_PY + body

    def test_drop_on_task_field_tab_and_mixed_clipboard(self):
        out = self.probe(self.scenario("""
from PyQt6.QtCore import QMimeData, QPointF, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
js("page.canCapture = true")
js("page.openLaunch('K-9', false)")
wait(100)
before = js("page.launchDialog.taskEdit.text")
# Plik upuszczony na pole zadania: zrzut, nie ścieżka w tekście.
mime = QMimeData()
mime.setUrls([QUrl.fromLocalFile(png("upuszczony.png"))])
point = QPointF(at("page.launchDialog.taskEdit"))
copy = Qt.DropAction.CopyAction
for event in (QDragEnterEvent(point.toPoint(), copy, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
              QDragMoveEvent(point.toPoint(), copy, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
              QDropEvent(point, copy, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)):
    QGuiApplication.sendEvent(window, event)
    wait(30)
wait(100)
out["dropped"] = [len(shots()), js("page.launchDialog.taskEdit.text") == before]
# Tab z pola zadania — „Zrób zrzut”.
js("page.launchDialog.taskEdit.forceActiveFocus()")
key(Qt.Key.Key_Tab)
out["tab"] = js(FIND + "(page.launchDialog, 'captureTile').activeFocus")
# Schowek z tekstem i obrazem (komórki LibreOffice): w polu wkleja się tekst, poza polem — obraz.
both = QMimeData()
both.setText("A1\tB1")
both.setImageData(QImage(png("komorki.png", "#f5b99a")))
QGuiApplication.clipboard().setMimeData(both)
js("page.launchDialog.taskEdit.forceActiveFocus(); page.launchDialog.taskEdit.cursorPosition = page.launchDialog.taskEdit.length")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
out["inField"] = [len(shots()), js("page.launchDialog.taskEdit.text").endswith("A1\tB1")]
js("page.launchDialog.forceActiveFocus()")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
out["outside"] = len(shots())
"""))
        self.assertEqual(out["dropped"], [1, True], "upuszczony plik to zrzut, tekst zadania bez zmian")
        self.assertTrue(out["tab"], "Tab z pola zadania prowadzi do „Zrób zrzut”")
        self.assertEqual(out["inField"], [1, True], "w polu tekst wygrywa z obrazem")
        self.assertEqual(out["outside"], 2, "poza polem — obraz")

    def test_busy_start_without_reply_keeps_images_and_defaults_after_open(self):
        data = json.loads(json.dumps(self.data))
        data["models"] = models_status()
        data["tasks"] = {"tools": {"claude": True, "codex": True}, "model": "cloud"}
        flow = dict(LaunchTest.FLOW, id="K-9", branch="claude/k-9-x")
        out = self.probe(self.scenario(f"""
js("page.openLaunch('K-9', false)")
wait(100)
click(at(FIND + "(page.launchDialog, 'launchEntry-spec')"))
d = "page.launchDialog."
# Odświeżone Defaults() po otwarciu: zapamiętany „Lokalny”, póki nikt nie ruszał modelu.
js("page.taskDefaults = ({{tools: {{claude: true, codex: true}}, model: 'local'}})")
out["defaults"] = js(d + "model")
wait(300)   # wiersz modelu układa się na nowo po zmianie na „Lokalny”
click(at(FIND + "(page.launchDialog, 'launchModel-cloud')"))
out["clicked"] = [js(d + "model"), js(d + "modelTouched")]
js("page.taskDefaults = ({{tools: {{claude: true, codex: true}}, model: 'local', x: 1}})")
out["touched"] = js(d + "model")
js("page.launchDialog.filesDropped(['file://" + png("a.png") + "'])")
wait(100)
folder = os.path.dirname(shots()[0])
# Start w drodze: otwarcie okna innej karty (popup kafelka) niczego nie rusza.
replies.extend([json.loads({json.dumps(flow, ensure_ascii=False)!r}),
                {{"ok": False, "error": "no-service", "message": "Usługa tide-daemon nie odpowiada"}}])
backend._write_orig = backend._write
held = []
def slow(tag, member, args):
    if member == "Start":
        held.append((tag, member, args))
        return
    backend._write_orig(tag, member, args)
backend._write = slow
click(named("launchStart"))
wait(200)
js("showItem('launch:K-14')")
wait(100)
out["busy"] = [js("page.launchCardId"), js(d + "busy"), len(shots())]
backend._write = backend._write_orig
backend._write_orig(*held[0])
wait(200)
out["launchError"] = obj("launchError").property("text")
click(named("launchCancel"))
wait(100)
out["kept"] = [os.path.isdir(folder), shots()]
"""), data)
        self.assertEqual(out["defaults"], "local")
        self.assertEqual(out["clicked"], ["cloud", True], out)
        self.assertEqual(out["touched"], "cloud", "wybór użytkownika wygrywa z odświeżonymi Defaults()")
        self.assertEqual(out["busy"], ["K-9", True, 1], "w trakcie startu inne okno się nie otwiera, zrzuty zostają")
        self.assertIn("nie odpowiada", out["launchError"])
        self.assertEqual(out["kept"], [True, []], "Start bez odpowiedzi mógł ruszyć agenta — katalog zostaje")


class TaskImagesTest(unittest.TestCase):
    """apps/common/taskimages.py (wspólne z oknem „Nowe zadanie”): błędy zapisu bez zawieszonego okna,
    nieudana kopia w środku listy, plik, który nie jest obrazem, zrzut Spectacle po zamknięciu zadania."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        from apps.common import taskimages
        cls.mod = taskimages

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-taskimages-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def png(self, name):
        from PyQt6.QtGui import QColor, QImage
        image = QImage(20, 10, QImage.Format.Format_RGB32)
        image.fill(QColor("#6fd6c6"))
        path = os.path.join(self.tmp, name)
        image.save(path, "PNG")
        return path

    def spectacle(self, delay="0"):
        fake = os.path.join(self.tmp, "spectacle")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/bash\nsleep {delay}\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && cp \"{self.png('shot.png')}\" \"$2\"; "
                     "shift; done\n")
        os.chmod(fake, 0o755)
        return fake

    def test_failed_copy_mid_batch_and_unreadable_image(self):
        tray = self.mod.ImageTray("", os.path.join(self.tmp, "tasks"))
        changed, problems = [], []
        tray.imagesChanged.connect(lambda: changed.append(1))
        tray.imageProblem.connect(problems.append)
        fake = os.path.join(self.tmp, "udaje.png")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("to nie png")
        tray.add_files([f"file://{self.png('a.png')}", f"file://{fake}"])
        self.assertEqual(len(json.loads(tray.paths_json())), 1)
        self.assertIn("udaje.png nie daje się odczytać jako obraz", problems[-1])
        folder = tray.images.folder
        os.chmod(folder, 0o500)
        self.addCleanup(os.chmod, folder, 0o700)
        n = len(changed)
        tray.add_files([f"file://{self.png('b.png')}", f"file://{self.png('c.png')}"])
        self.assertGreater(len(changed), n, "lista odświeżona mimo błędu")
        self.assertEqual(problems[-1].count("nie udało się skopiować"), 2, "każdy plik osobno, bez przerwania")
        self.assertEqual(sorted(os.listdir(folder)), ["zrzut-1.png"], "bez pustych zrzut-N")

    def test_capture_without_folder_reports_problem(self):
        blocker = os.path.join(self.tmp, "plik")
        open(blocker, "w").close()
        tray = self.mod.ImageTray(self.spectacle(), os.path.join(blocker, "tasks"))
        finished, problems = [], []
        tray.captureFinished.connect(finished.append)
        tray.imageProblem.connect(problems.append)
        tray.capture()
        self.assertEqual(len(finished), 1)
        self.assertIn("Nie udało się zapisać zrzutu", finished[0], "okno wraca z komunikatem, nie zostaje schowane")

    def test_capture_result_after_discard_is_dropped(self):
        tray = self.mod.ImageTray(self.spectacle("0.4"), os.path.join(self.tmp, "tasks"))
        tray.add_files([f"file://{self.png('a.png')}"])
        finished = []
        tray.captureFinished.connect(finished.append)
        tray.capture()
        tray.discard()
        tray.add_files([f"file://{self.png('b.png')}"])
        self.assertTrue(wait_until(lambda: finished, timeout=10))
        paths = json.loads(tray.paths_json())
        self.assertEqual([os.path.basename(p) for p in paths], ["zrzut-1.png"], "zrzut starego zadania nie trafia do nowego")
        tray.discard()


class GrabTest(ProbeCase):
    def grab(self, name, *argv, data=None) -> str:
        data_path = os.path.join(self.tmp, name + ".json")
        with open(data_path, "w", encoding="utf-8") as fh:
            json.dump(self.data if data is None else data, fh, ensure_ascii=False)
        png = os.path.join(self.tmp, name + ".png")
        env = offscreen()
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "board", "--data",
                                 data_path, "--grab", png, "--width", "1440", "--height", "900", *argv],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIsNone(re.search(QML_PROBLEMS, result.stderr), result.stderr[-3000:])
        self.assertGreater(os.path.getsize(png), 30_000, name)
        return png

    def test_views_render_without_warnings(self):
        self.grab("board")
        self.grab("card", "--preview", "card=K-13")
        self.grab("card-plan", "--preview", "card=K-5", "--preview", "tab=plan")
        self.grab("card-spec", "--preview", "card=K-5", "--preview", "tab=spec")
        self.grab("card-expand", "--preview", "card=K-5", "--preview", "tab=plan", "--preview", "expand=1")
        self.grab("card-plan-min", "--preview", "card=K-5", "--preview", "tab=plan", "--width", "1380", "--height", "620")
        self.grab("new", "--preview", "new=todo")
        self.grab("manage", "--preview", "manage=logviewer")
        self.grab("lanes", "--preview", "layout=lanes")
        self.grab("select", "--select", "K-1")
        self.grab("min-width", "--width", "1380", "--height", "620")
        for name, value in (("launch-full", "K-9,move"), ("launch-full-codex", "K-9,codex,move"),
                            ("launch-light", "K-14,light"), ("launch-light-codex", "K-14,light,codex,manual"),
                            ("launch-spec", "K-14,spec,move"), ("launch-spec-codex", "K-14,spec,codex")):
            self.grab(name, "--preview", "launch=" + value)
        self.grab("launch-min", "--preview", "launch=K-9,move", "--width", "1380", "--height", "620")
        shot = os.path.join(self.tmp, "board.png")
        data = dict(self.data, models=models_status(), tasks={"tools": {"claude": True, "codex": True}, "model": "local"})
        self.grab("launch-shots-local", "--preview", "launch=K-9,move", "--preview", f"shots={shot},{shot}", data=data)
        with tempfile.TemporaryDirectory(prefix="tide-board-empty-") as tmp:
            self.grab("empty", data=empty_snapshot(tmp))


@requires_dbus
class BackendTest(unittest.TestCase):
    def test_plan_card_paths_require_current_card_and_current_snapshot(self):
        path = "/board/flow/K-1/plan/tickets/1.md"
        card = {"id": "K-1", "derived": {"flow": {"plan": {"rows": [{"cardPath": path}]}}}}
        backend = self.module.create(None, {"exists": True, "cards": [card, {"id": "K-2"}]})
        self.assertFalse(backend.openFile(path))
        backend.cardDocs("K-1")
        self.assertTrue(backend.openFile(path))
        self.assertFalse(backend.openFile("/etc/passwd"))
        backend.cardDocs("K-2")
        self.assertFalse(backend.openFile(path))
        backend.cardDocs("K-1")
        backend._got_snapshot(True, json.dumps({"cards": [{"id": "K-1"}]}))
        self.assertFalse(backend.openFile(path))

    """backend.py z prawdziwym tide-daemon na prywatnej magistrali i tymczasową tablicą."""

    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.tmp = tempfile.mkdtemp(prefix="tide-board-bus-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = PrivateBus("tide-board-view-test")
        self.addCleanup(self.bus.stop)
        workspace = os.path.join(self.tmp, "ws")
        os.makedirs(workspace)
        self.board = os.path.join(self.tmp, "board")
        self.bus.spawn_daemon("--claude-dir", os.path.join(self.tmp, "c"), "--codex-dir", os.path.join(self.tmp, "x"),
                              "--workspace", workspace, "--no-processes", "--no-notifications", "--no-krunner",
                              "--no-idle", "--no-limits", "--no-report", "--no-coordination", "--no-docker",
                              "--board-dir", self.board, extra_env=git_env(self.tmp))
        sys.path.insert(0, APP_DIR)
        import importlib.util
        spec = importlib.util.spec_from_file_location("tide_board_backend_live", os.path.join(APP_DIR, "backend.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_writes_reload_snapshot_and_badge(self):
        backend = self.module.create(self.bus.bus, None)
        replies = []
        backend.replied.connect(lambda tag, text: replies.append((tag, json.loads(text))))
        self.assertTrue(wait_until(lambda: backend.available, timeout=10))
        self.assertFalse(json.loads(backend.snapshotJson)["exists"])
        self.assertEqual(backend.badgeText, "")
        backend.createCard("create:1", json.dumps({"title": "Pierwsza karta", "column": "todo"}))
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertEqual(replies[0], ("create:1", {"ok": True, "id": "K-1", "commitError": ""}))
        self.assertTrue(wait_until(lambda: json.loads(backend.snapshotJson).get("exists"), timeout=10))
        self.assertTrue(wait_until(lambda: backend.badgeText == "1", timeout=5))
        backend.createCard("create:2", json.dumps({"title": "Druga"}))
        backend.moveCard("move:K-2", "K-2", "todo", 0)
        self.assertTrue(wait_until(lambda: len(replies) == 3, timeout=10))
        self.assertEqual(replies[2][1]["ok"], True)
        self.assertTrue(wait_until(lambda: [c["id"] for c in json.loads(backend.snapshotJson)["cards"]
                                            if c["column"] == "todo"] == ["K-2", "K-1"], timeout=10))
        backend.moveCard("move:K-2", "K-2", "progress", 0)
        backend.updateCard("update:K-9", "K-9", json.dumps({"title": "x"}))
        self.assertTrue(wait_until(lambda: len(replies) == 5, timeout=10))
        for _tag, reply in replies[3:]:
            self.assertEqual(sorted(reply), ["error", "message", "ok"])
            self.assertFalse(reply["ok"])
        self.assertEqual([r[1]["error"] for r in replies[3:]], ["bad-request", "not-found"])
        backend.upsertTag("tag:1", json.dumps({"name": "pilne", "color": "rose"}))
        backend.upsertProject("project:1", json.dumps({"name": "dom"}))
        self.assertTrue(wait_until(lambda: len(replies) == 7, timeout=10))
        snap = json.loads(backend.snapshotJson)
        self.assertTrue(wait_until(lambda: any(t["name"] == "pilne" for t in json.loads(backend.snapshotJson)["tags"]),
                                   timeout=5))
        self.assertIn("dom", [p["name"] for p in json.loads(backend.snapshotJson)["projects"]])
        self.assertEqual(snap["boardDir"], self.board)
        # Uruchomienie z karty: RenderLaunch idzie do /Board, Start do /Tasks (z długim terminem).
        backend.renderLaunch("launch-render:K-1", "K-1", json.dumps({"entry": "spec", "tool": "codex"}))
        backend.startTask("launch-start:K-1", json.dumps({"repoPath": "/nie/ma", "tool": "gemini", "prompt": "a b"}))
        self.assertTrue(wait_until(lambda: len(replies) == 9, timeout=15))
        by_tag = dict(replies[7:])
        self.assertEqual((by_tag["launch-render:K-1"]["ok"], by_tag["launch-render:K-1"]["entry"],
                          by_tag["launch-render:K-1"]["mode"], by_tag["launch-render:K-1"]["tool"]),
                         (True, "spec", "", "codex"))
        self.assertIn("Karta K-1: Pierwsza karta", by_tag["launch-render:K-1"]["text"])
        self.assertEqual(by_tag["launch-start:K-1"]["error"], "bad-tool", "odpowiedź z /Tasks")
        # PT-43: /Tasks Defaults (katalog zrzutów, programy) i stan modeli z /Models — jak w oknie „Nowe zadanie”.
        self.assertTrue(wait_until(lambda: json.loads(backend.tasksJson).get("imagesDir"), timeout=10))
        self.assertEqual(backend.tray.images.root, json.loads(backend.tasksJson)["imagesDir"],
                         "zrzuty w katalogu, z którego /Tasks Start je przyjmie")
        self.assertIn("tools", json.loads(backend.tasksJson))
        self.assertTrue(wait_until(lambda: (json.loads(backend.modelsJson) or {}).get("roles") is not None, timeout=10))

    def test_card_docs_over_bus(self):
        repo = os.path.join(self.tmp, "ws", "dom")
        os.makedirs(os.path.join(repo, "docs", "agents", "plans"))
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True, env=dict(os.environ, **git_env(self.tmp)))
        plan = os.path.join(repo, "docs", "agents", "plans", "2026-10-27-dom.md")
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan domu\ncard: K-1\n---\n\n## Cel\n\nDom.\n")
        backend = self.module.create(self.bus.bus, None)
        replies, docs = [], []
        backend.replied.connect(lambda tag, text: replies.append(json.loads(text)))
        backend.docsReplied.connect(lambda card_id, text: docs.append((card_id, json.loads(text))))
        self.assertTrue(wait_until(lambda: backend.available, timeout=10))
        backend.createCard("create:1", json.dumps({"title": "Dom", "repo": repo}))
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        backend.cardDocs("K-1")
        backend.cardDocs("K-9")
        self.assertTrue(wait_until(lambda: len(docs) == 2, timeout=10))
        by_id = dict(docs)
        self.assertEqual((by_id["K-1"]["plan"]["title"], by_id["K-1"]["plan"]["path"]), ("Plan domu", os.path.realpath(plan)))
        self.assertEqual(by_id["K-9"]["error"], "not-found")
        self.assertFalse(backend.openFile("/etc/passwd"), "tylko ścieżki z odpowiedzi CardDocs")
        self.assertFalse(backend.showInFolder(os.path.join(repo, "README.md")))
        self.assertFalse(backend.openLink("javascript:alert(1)"))
        self.assertEqual(backend.docsRequests, 2)

    def test_preview_mode_records_calls(self):
        backend = self.module.create(None, {"exists": True, "cards": [{"id": "K-1", "column": "backlog"}],
                                            "cardDocs": {"K-1": {"ok": True, "card": "K-1", "others": [],
                                                                 "plan": {"path": "/b/docs/x/plans/p.md"}, "spec": None}}})
        docs = []
        backend.docsReplied.connect(lambda card_id, text: docs.append((card_id, json.loads(text))))
        backend.cardDocs("K-1")
        backend.cardDocs("K-2")
        self.assertTrue(wait_until(lambda: len(docs) == 2, timeout=2))
        self.assertEqual(docs[0][1]["plan"]["path"], "/b/docs/x/plans/p.md")
        self.assertEqual(docs[1][1], {"ok": True, "card": "K-2", "plan": None, "spec": None, "others": [], "warnings": []})
        self.assertNotIn("cardDocs", json.loads(backend.snapshotJson), "dane podglądu dokumentów nie trafiają do migawki")
        self.assertTrue(backend.openFile("/b/docs/x/plans/p.md"))
        self.assertTrue(backend.showInFolder("/b/docs/x/plans/p.md"))
        self.assertFalse(backend.openFile("/b/inne.md"))
        self.assertTrue(backend.openLink("https://example.com"))
        self.assertEqual(json.loads(backend.callsJson), [["openFile", "/b/docs/x/plans/p.md"],
                                                         ["showInFolder", "/b/docs/x/plans/p.md"],
                                                         ["openLink", "https://example.com"]])

    def test_preview_mode_images_and_models(self):
        """PT-43: zrzuty (przeciąganie, usuwanie, po starcie i po anulowaniu) i dane modeli w podglądzie; schowek
        sprawdza sonda w prawdziwym oknie (LaunchShotsTest)."""
        from PyQt6.QtGui import QColor, QImage
        status = models_status()
        backend = self.module.create(None, {"exists": True, "cards": [], "models": status,
                                            "tasks": {"tools": {"claude": True, "codex": False}, "model": "local"}})
        self.assertEqual(json.loads(backend.modelsJson), status)
        self.assertEqual(json.loads(backend.tasksJson)["model"], "local")
        self.assertNotIn("models", json.loads(backend.snapshotJson), "dane modeli nie trafiają do migawki")
        self.assertEqual(json.loads(self.module.create(None, {"exists": True}).modelsJson), None)
        backend.tray.images.root = os.path.join(self.tmp, "tasks")
        image = QImage(20, 10, QImage.Format.Format_RGB32)
        image.fill(QColor("#6fd6c6"))
        source = os.path.join(self.tmp, "a.png")
        image.save(source, "PNG")
        backend.addFiles([f"file://{source}", f"file://{source}"])
        paths = json.loads(backend.imagesJson)
        self.assertEqual(len(paths), 2)
        backend.removeImage(paths[0])
        self.assertFalse(os.path.exists(paths[0]))
        backend.keepImages()
        self.assertTrue(os.path.isfile(paths[1]), "po starcie zrzut zostaje agentowi")
        self.assertEqual(json.loads(backend.imagesJson), [])
        backend.addFiles([f"file://{source}"])
        folder = os.path.dirname(json.loads(backend.imagesJson)[0])
        self.assertNotEqual(folder, os.path.dirname(paths[1]), "następne zadanie — nowy katalog")
        backend.discardImages()
        self.assertFalse(os.path.exists(folder))
        backend.previewImages([source])
        self.assertEqual(json.loads(backend.imagesJson), [source], "podgląd: miniatury bez kopiowania")
        self.assertEqual(backend.openModels(), "")
        self.assertEqual(json.loads(backend.callsJson)[-1], ["openModels"])

    def test_preview_mode_records_writes(self):
        backend = self.module.create(None, {"exists": True, "cards": [{"id": "K-1", "column": "backlog"}]})
        replies = []
        backend.replied.connect(lambda tag, text: replies.append(json.loads(text)))
        backend.moveCard("move:K-1", "K-1", "todo", -1)
        backend.renderLaunch("launch-render:K-1", "K-1", "{}")
        backend.startTask("launch-start:K-1", "{}")
        self.assertEqual(json.loads(backend.callsJson), [["MoveCard", "K-1", "todo", -1], ["RenderLaunch", "K-1", "{}"],
                                                         ["Start", "{}"]])
        self.assertEqual(replies[0]["error"], "preview")
        self.assertEqual((backend.badgeText, backend.badgeTone), ("1", "muted"))
        self.assertTrue(backend.available and backend.preview)

    def test_without_daemon_writes_fail_softly(self):
        self.bus.stop()
        backend = self.module.create(None, None)
        replies = []
        backend.replied.connect(lambda tag, text: replies.append(json.loads(text)))
        backend.createCard("create:1", json.dumps({"title": "x"}))
        self.assertEqual(replies[0]["error"], "no-service")
        self.assertFalse(backend.available)
        self.assertEqual(backend.badgeText, "")


if __name__ == "__main__":
    unittest.main()


class LightPlanningViewTest(ProbeCase):
    def test_light_planning_selects_planner_and_sends_background_request(self):
        status = dict(models_status(), executors=EXECUTORS)
        out = self.probe(FIND_PY + f"""
backend._models = {json.dumps(status, ensure_ascii=False)!r}
backend.modelsChanged.emit()
wait(50)
js("page.openLaunch('K-15', false)")
wait(100)
js("page.launchDialog.mode = 'light'")
wait(50)
out["plannerVisible"] = js(FIND + "(page.launchDialog, 'launchPlanner').visible")
js(FIND + "(page.launchDialog, 'launchPlanner').picked('Opus')")
out["planner"] = js("page.launchDialog.executor")
out["footer"] = obj("launchFooter").property("text")
key(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
wait(200)
out["calls"] = calls()
""")
        self.assertTrue(out["plannerVisible"])
        self.assertEqual(out["planner"], "Opus")
        self.assertIn("bez Konsoli", out["footer"])
        start = next(c for c in out["calls"] if c[0] == "StartCard")
        self.assertEqual(json.loads(start[-1])["mode"], "light")
        self.assertEqual(json.loads(start[-1])["executor"], "Opus")
        self.assertFalse(any(c[0] in ("RenderLaunch", "Start") for c in out["calls"]))

    def test_assignment_rules_can_be_edited_globally_and_per_project(self):
        data = json.loads(json.dumps(self.data))
        data["assignmentRules"] = "standard: wspólne"
        data["projects"] = [p for p in data["projects"] if p["slug"] == "plasma-tide"]
        data["projects"][0]["assignmentRules"] = ""
        out = self.probe(FIND_PY + """
js("page.managing = true")
wait(80)
obj("globalAssignmentRules").setProperty("text", "mocny: wspólne")
js(FIND + "(page.managePage, 'globalAssignmentRulesSave').clicked()")
wait(80)
out["global"] = calls()[-1]
js("page.managePage.openProject = 'plasma-tide'")
wait(80)
js(FIND + "(page.managePage, 'projectAssignmentRules').text = 'lekki: projekt'")
js(FIND + "(page.managePage, 'projectSave').clicked()")
wait(80)
out["project"] = calls()[-1]
""", data)
        self.assertEqual(json.loads(out["global"][-1]), {"globalAssignmentRules": "mocny: wspólne"})
        self.assertEqual(json.loads(out["project"][-1]), {"slug": "plasma-tide", "assignmentRules": "lekki: projekt"})
