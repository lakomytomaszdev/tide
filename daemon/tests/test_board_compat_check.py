"""PT-109: tools/board_compat_check.py — pola kart liczone kodem sprzed i po integracji, na syntetycznej Tablicy.

Tablica z kartą każdej klasy (dawny przepływ z `session` i planem, `projects` z PT-28, tryb „spec” sprzed PT-77,
Archiwum, przepływ K-28). Kod „przed” to albo to samo drzewo (0 różnic w każdej klasie), albo prawdziwy kod
sprzed integracji K-28 i K-29 (wspólny przodek `master` i gałęzi karty), z różnicami wpisanymi niżej z powodem.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tide_daemon.cardflow.journal import Journal

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "tools", "board_compat_check.py")
# Ostatni commit `master` przed scaleniem K-28 i K-29 na gałąź karty K-33 (integracja PT-108).
PRE_INTEGRATION = "812900c2cf0ced61294045b26c0967c809b3abaa"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}

# Różnice kodu sprzed i po integracji, których oczekujemy na syntetycznej Tablicy: (karta, pole) → powód.
# Pusto: `git diff --stat PRE_INTEGRATION HEAD -- daemon apps` pokazuje tylko testy — K-28 (przepływ karty, wejścia
# „Mam plan” i „Od razu”) był już na `master`, a integracja przyniosła K-29 (widżety, motyw, skrypty), które nie
# dotykają usługi ani logiki Tablicy. Każda klasa kart ma więc dawać 0 różnic; test pilnuje tego na prawdziwym
# kodzie sprzed integracji, a osobny test (zmieniony board.js) pokazuje, że różnica nie przejdzie bez śladu.
EXPECTED = {}


def load_tool():
    spec = importlib.util.spec_from_file_location("board_compat_check", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def listing(root):
    """Ścieżka → (rozmiar, mtime, skrót) całego drzewa, także .git — dowód, że narzędzie niczego nie zapisało."""
    out = {}
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            stat = os.lstat(path)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = (stat.st_size, stat.st_mtime_ns, hashlib.sha1(fh.read()).hexdigest())
        for name in _dirs:
            out[os.path.relpath(os.path.join(folder, name), root) + "/"] = ()
    return out


class BoardFixture(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tide-compat-test-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.alpha, self.beta = (os.path.join(self.base, "ws", name) for name in ("alpha", "beta"))
        for repo in (self.alpha, self.beta):
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "master")
            write(os.path.join(repo, "README.md"), "projekt\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "init")
        self.plan = os.path.join(self.alpha, "docs", "agents", "plans", "2026-10-01-stary.md")
        write(self.plan, "---\ntide-plan: 1\ntitle: Stary\ncard: K-1\ncoordinator: claude\nsession: abc-123\n---\n\n"
                         "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n| --- | --- | --- | --- | --- | --- |\n"
                         "| PT-1 | Pierwszy | gotowe | | Sonnet | |\n| PT-2 | Drugi | todo | | Sonnet | |\n")
        self.board = os.path.join(self.base, "copy", "tide-board")
        write(os.path.join(self.board, "board.md"), "---\ntide-board: 1\n---\n")
        write(os.path.join(self.board, "projects", "alpha.md"), f"---\nname: Alpha\nrepo: {self.alpha}\n---\n")
        write(os.path.join(self.board, "projects", "beta.md"), f"---\nname: Beta\nrepo: {self.beta}\n---\n")
        self.card(1, "Dawny przepływ", project="alpha", column="todo", session="abc-123", plan=self.plan,
                  branch="claude/k-1-stary", launched="2026-10-01T10:00:00+02:00", entry="planning", mode="full")
        self.card(2, "Dwa projekty", project="alpha", column="backlog", projects="beta")
        self.card(3, "Tryb spec", project="alpha", column="backlog", mode="spec", spec="docs/agents/specs/s.md")
        self.card(4, "Archiwalna", project="alpha", column="archived", note="stara notatka")
        self.card(5, "Przepływ K-28", project="alpha", column="todo")
        self.card(6, "Bez projektu", column="backlog")
        journal = Journal(self.board, mirror=os.path.join(self.base, "mirror"))
        journal.append("K-5", "card.started", entry="now", executor="Sonnet", program="claude", model="sonnet",
                       provider="anthropic", reviewer="Opus", reviewerProgram="claude", reviewerModel="opus",
                       reviewerProvider="anthropic", preferredProvider="anthropic", check="auto",
                       repo=self.alpha, project="alpha", title="Przepływ K-28")
        shutil.rmtree(os.path.join(self.base, "mirror"), ignore_errors=True)  # kopia Tablicy nie ma kopii usługi
        git(self.board, "init", "-q", "-b", "master")
        git(self.board, "add", "-A")
        git(self.board, "commit", "-q", "-m", "board")
        self.tool = load_tool()

    def card(self, num, title, **fields):
        head = "".join(f"{key}: {value}\n" for key, value in fields.items())
        write(os.path.join(self.board, "cards", f"K-{num}.md"),
              f"---\ntitle: {title}\ncreated: 2026-10-01T09:00:00+02:00\n{head}---\n\nOpis karty {num}.\n")

    def compare(self, **kwargs):
        scratch = os.path.join(self.base, "scratch")
        os.makedirs(scratch, exist_ok=True)
        tree = self.tool.default_tree()
        before = kwargs.get("before") or tree
        return self.tool.report(self.tool.compute(before, self.board, scratch, "before"),
                                self.tool.compute(tree, self.board, scratch, "after"))


class SameCodeTest(BoardFixture):
    def test_every_class_gives_zero_differences_for_the_same_code(self):
        result = self.compare()
        self.assertEqual(result["differences"], [])
        self.assertEqual(result["classes"], {"archiwum": 1, "przepływ K-28": 1, "spec sprzed PT-77": 1,
                                             "projekty dodatkowe": 1, "dawny przepływ": 1, "inne": 1})
        self.assertEqual(result["loadErrors"], [])

    def test_backlog_cards_all_give_a_launch_preview(self):
        result = self.compare()
        self.assertEqual((result["backlog"], result["backlogBroken"]), (3, []))
        self.assertEqual(result["renderErrors"], {})

    def test_tool_never_writes_to_the_board_copy(self):
        before = listing(self.board)
        self.compare()
        self.assertEqual(listing(self.board), before)

    def test_text_report_says_zero_differences_and_counts_classes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.tool.main([self.board, "--before-tree", self.tool.default_tree()])
        self.assertEqual(code, 0, out.getvalue())
        text = out.getvalue()
        self.assertIn("Karty: 6;", text)
        self.assertIn("Podgląd zlecenia kart z Backlogu: 3 z 3 bez błędu", text)
        self.assertTrue(text.rstrip().endswith("0 różnic"), text)

    def test_directory_without_board_marker_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.tool.main([self.base]), 2)
        self.assertIn("board.md", err.getvalue())

    def test_changed_code_is_reported_per_card_and_field(self):
        tree = os.path.join(self.base, "old-tree")
        shutil.copytree(os.path.join(self.tool.default_tree(), "daemon", "tide_daemon"),
                        os.path.join(tree, "daemon", "tide_daemon"))
        os.makedirs(os.path.join(tree, "apps", "board", "qml"))
        with open(os.path.join(self.tool.default_tree(), "apps", "board", "qml", "board.js"), encoding="utf-8") as fh:
            source = fh.read()
        old = source.replace('return "✓ specyfikacja gotowa"', 'return "specyfikacja gotowa (dawniej)"')
        self.assertNotEqual(old, source)
        write(os.path.join(tree, "apps", "board", "qml", "board.js"), old)
        result = self.compare(before=tree)
        self.assertEqual([(c, f) for c, f, _o, _n in result["differences"]], [("K-3", "marker")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tool.main([self.board, "--before-tree", tree])
        self.assertIn("| karta | pole | przed | po |", out.getvalue())
        self.assertIn("| K-3 | marker |", out.getvalue())


class PreIntegrationCodeTest(BoardFixture):
    def test_code_before_integration_differs_only_as_expected(self):
        probe = subprocess.run(["git", "-C", self.tool.default_tree(), "cat-file", "-e", PRE_INTEGRATION + "^{commit}"],
                               capture_output=True)
        if probe.returncode:
            self.skipTest("brak commitu sprzed integracji w tym repozytorium")
        tree = os.path.join(self.base, "before-tree")
        os.makedirs(tree)
        self.tool.extract_ref(self.tool.default_tree(), PRE_INTEGRATION, tree)
        result = self.compare(before=tree)
        found = {(card, field): (old, new) for card, field, old, new in result["differences"]}
        self.assertEqual(sorted(found), sorted(EXPECTED), json.dumps(result["differences"], ensure_ascii=False))
        self.assertEqual(result["loadErrors"], [])


class ComparisonTableTest(unittest.TestCase):
    def test_compare_lists_card_field_before_after_and_missing_cards(self):
        tool = load_tool()
        card = {"column": "todo", "marker": "", "panel": {}, "entries": ["planning"], "entry": "planning",
                "project": "a", "repo": "/r", "projects": [], "check": "auto", "blocker": ""}
        before = {"cards": {"K-2": dict(card), "K-10": dict(card), "K-3": dict(card)}, "problems": []}
        after = {"cards": {"K-2": {**card, "entries": ["planning", "now"]}, "K-10": dict(card),
                           "K-11": dict(card)}, "problems": []}
        self.assertEqual(tool.compare(before, after),
                         [("K-2", "entries", ["planning"], ["planning", "now"]), ("K-3", "karta", "jest", "brak"),
                          ("K-11", "karta", "brak", "jest")])
        text = tool.render_text({"cards": 3, "classes": {"inne": 3}, "backlog": 0, "backlogBroken": [],
                                 "renderErrors": {}, "loadErrors": [], "differences": tool.compare(before, after)})
        self.assertIn("| K-2 | entries | [\"planning\"] | [\"planning\", \"now\"] |", text)
        self.assertNotIn("0 różnic", text)


if __name__ == "__main__":
    unittest.main()
