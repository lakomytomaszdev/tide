"""Tablica pracy bez Qt: format plików, ID, kolumny, tagi, projekty, szablony, commity i błędy.

Każdy test ma własny katalog tymczasowy i własną konfigurację gita (GIT_CONFIG_GLOBAL), więc
nie dotyka prawdziwej tablicy ani konfiguracji użytkownika.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from . import gitrepos  # noqa: F401 — ustawia sys.path przez pakiet testów
from tide_daemon.board import is_board_repo
from tide_daemon.board.frontmatter import Document, FrontmatterError
from tide_daemon.board.model import BoardError
from tide_daemon.board.store import BoardStore
from tide_daemon.worktrees import find_repositories

NOW = 1_793_100_000  # 2026-10-27


def git_out(cwd, *args) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-board-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        self.root = os.path.join(self.ws, "tide-board")
        self.clock = [NOW]
        self.store = BoardStore(self.root, clock=lambda: self.clock[0])

    def repo(self, name, agents_docs=False) -> str:
        path = os.path.join(self.ws, name)
        os.makedirs(path)
        git_out(path, "init", "-q")
        if agents_docs:
            os.makedirs(os.path.join(path, "docs", "agents"))
        return path

    def log(self) -> list[str]:
        return git_out(self.root, "log", "--format=%s").splitlines()[::-1]

    def card_text(self, card_id) -> str:
        with open(os.path.join(self.root, "cards", f"{card_id}.md"), encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def cards(self) -> dict:
        return {c["id"]: c for c in self.store.snapshot()["cards"]}

    def column(self, name) -> list[str]:
        return [c["id"] for c in self.store.snapshot()["cards"] if c["column"] == name]


class FrontmatterTest(unittest.TestCase):
    TEXT = ("---\n"
            "id: K-3\n"
            "# komentarz użytkownika\n"
            "Title: Stary tytuł\n"
            "custom-key: 'zostaje'\n"
            "\n"
            "priority: high # ważne\n"
            "---\n"
            "\nOpis z **Markdown**\n\n- punkt\n")

    def test_round_trip_keeps_unknown_keys_comments_and_body(self):
        doc = Document.parse(self.TEXT)
        self.assertEqual(doc.text(), self.TEXT)
        self.assertEqual(doc.get("title"), "Stary tytuł")
        self.assertEqual(doc.get("custom-key"), "zostaje")
        self.assertEqual(doc.get("priority"), "high")
        doc.set("title", "Nowy")
        doc.set("column", "todo")
        text = doc.text()
        self.assertIn("# komentarz użytkownika\ntitle: Nowy\ncustom-key: 'zostaje'\n\npriority: high # ważne\n"
                      "column: todo\n---\n", text)
        self.assertTrue(text.endswith("\nOpis z **Markdown**\n\n- punkt\n"))

    def test_values_that_need_quotes_survive(self):
        for value in ("Hotfix: unikalne frazy", "C# i #tag", " spacja", "\"cytat\"", "'x'", "- lista",
                      "a #b", "50% szybciej", "zwykły tekst", ""):
            doc = Document()
            doc.set("title", value)
            again = Document.parse(doc.text())
            self.assertEqual(again.get("title"), value, value)

    def test_newlines_collapse_into_one_line(self):
        doc = Document()
        doc.set("title", "a\nb\r\nc")
        self.assertEqual(Document.parse(doc.text()).get("title"), "a b c")
        self.assertEqual(len(doc.text().splitlines()), 3)

    def test_missing_frontmatter_is_an_error(self):
        with self.assertRaises(FrontmatterError):
            Document.parse("tytuł bez frontmattera\n")
        with self.assertRaises(FrontmatterError):
            Document.parse("---\ntitle: x\n")


class CreateBoardTest(BoardCase):
    def test_snapshot_before_first_write_creates_nothing(self):
        snap = self.store.snapshot()
        self.assertFalse(snap["exists"])
        self.assertFalse(os.path.exists(self.root))
        self.assertEqual([t["name"] for t in snap["tags"]], ["Firma", "własne"])
        self.assertEqual(snap["nextId"], "K-1")
        self.assertEqual(snap["cards"], [])

    def test_first_write_creates_repository_with_marker_and_seeds(self):
        result = self.store.create_card({"title": "Pierwsza karta"})
        self.assertEqual(result, {"ok": True, "id": "K-1"})
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".git")))
        self.assertTrue(is_board_repo(self.root))
        for rel in ("board.md", ".gitignore", "tags/firma.md", "tags/wlasne.md", "flows/full.md",
                    "flows/light.md", "flows/spec.md", "cards/K-1.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.root, rel)), rel)
        self.assertEqual(self.log(), ["board: create", "K-1: create"])
        self.assertEqual(git_out(self.root, "remote").strip(), "")
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")
        author = git_out(self.root, "log", "-1", "--format=%an <%ae>").strip()
        self.assertEqual(author, "Tester <tester@example.com>")
        tags = {t["name"]: t["color"] for t in self.store.snapshot()["tags"]}
        self.assertEqual(tags, {"Firma": "sky", "własne": "lavender"})

    def test_foreign_directory_is_never_written(self):
        os.makedirs(self.root)
        self.write("notatki.txt", "moje\n")
        with self.assertRaises(BoardError) as ctx:
            self.store.create_card({"title": "x"})
        self.assertIn("nie jest tablicą Tide", str(ctx.exception))
        self.assertEqual(os.listdir(self.root), ["notatki.txt"])

    def test_identity_fallback_when_git_has_no_user(self):
        config = os.path.join(self.tmp, "noidentity")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tuseConfigOnly = true\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config}):
            for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
                os.environ.pop(key, None)
            store = BoardStore(self.root, clock=lambda: NOW)
            store.create_card({"title": "x"})
        self.assertEqual(git_out(self.root, "log", "-1", "--format=%an <%ae>").strip(), "Tide <tide@localhost>")


class CardTest(BoardCase):
    def test_card_file_format(self):
        self.store.create_card({"title": "Hotfix: unikalne wyszukiwanie fraz", "tags": ["pilne"],
                                "description": "Opis **karty**.\n\n- punkt", "note": "robię sam",
                                "youtrack": "TOM-142"})
        text = self.card_text("K-1")
        self.assertEqual(text, (
            "---\n"
            "id: K-1\n"
            "title: \"Hotfix: unikalne wyszukiwanie fraz\"\n"
            "project:\n"
            "tags: pilne\n"
            "column: backlog\n"
            "order: 10\n"
            "note: robię sam\n"
            "created: " + self.cards()["K-1"]["created"] + "\n"
            "updated: " + self.cards()["K-1"]["created"] + "\n"
            "youtrack: TOM-142\n"
            "spec:\n"
            "session:\n"
            "branch:\n"
            "---\n"
            "\nOpis **karty**.\n\n- punkt\n"))
        card = self.cards()["K-1"]
        self.assertEqual(card["title"], "Hotfix: unikalne wyszukiwanie fraz")
        self.assertEqual(card["description"], "Opis **karty**.\n\n- punkt\n")
        self.assertIsNone(card["derived"])
        self.assertEqual(card["boardColumn"], "backlog")
        self.assertEqual(card["createdTs"], NOW)

    def test_ids_are_max_plus_one_and_never_reused(self):
        for title in ("a", "b", "c"):
            self.store.create_card({"title": title})
        os.remove(os.path.join(self.root, "cards", "K-3.md"))
        self.assertEqual(self.store.create_card({"title": "d"})["id"], "K-4")  # licznik w board.md
        self.write("cards/K-40.md", "---\nid: K-40\ntitle: ręcznie\n---\n")
        self.assertEqual(self.store.create_card({"title": "e"})["id"], "K-41")
        self.write("cards/K-60.md", "zepsuty plik bez frontmattera\n")
        self.assertEqual(self.store.create_card({"title": "f"})["id"], "K-61")  # zepsuty plik też zajmuje numer

    def test_new_card_goes_on_top_of_its_column(self):
        for title in ("a", "b", "c"):
            self.store.create_card({"title": title})
        self.store.create_card({"title": "d", "column": "todo"})
        self.assertEqual(self.column("backlog"), ["K-3", "K-2", "K-1"])
        self.assertEqual(self.column("todo"), ["K-4"])

    def test_move_and_reorder(self):
        for title in ("a", "b", "c", "d"):
            self.store.create_card({"title": title})
        self.assertEqual(self.column("backlog"), ["K-4", "K-3", "K-2", "K-1"])
        self.assertEqual(self.store.move_card("K-1", "todo", 0), {"ok": True, "id": "K-1", "changed": True})
        self.assertEqual(self.store.move_card("K-3", "todo", -1)["changed"], True)
        self.assertEqual(self.column("todo"), ["K-1", "K-3"])
        self.store.move_card("K-2", "todo", 1)
        self.assertEqual(self.column("todo"), ["K-1", "K-2", "K-3"])
        self.store.move_card("K-3", "todo", 0)
        self.assertEqual(self.column("todo"), ["K-3", "K-1", "K-2"])
        self.assertEqual(self.store.move_card("K-3", "todo", 0)["changed"], False)  # bez zmian — bez commita
        self.store.move_card("K-4", "done", 99)
        self.store.move_card("K-4", "archived", 0)
        self.assertEqual(self.log()[-6:], ["K-1: move to todo", "K-3: move to todo", "K-2: move to todo",
                                           "K-3: reorder in todo", "K-4: move to done", "K-4: move to archived"])
        orders = {c["id"]: c["order"] for c in self.store.snapshot()["cards"] if c["column"] == "todo"}
        self.assertEqual(orders, {"K-3": 10, "K-1": 20, "K-2": 30})
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_derived_and_unknown_columns_are_refused(self):
        self.store.create_card({"title": "a"})
        for column in ("progress", "waiting", "review", "nowa"):
            with self.assertRaises(BoardError):
                self.store.move_card("K-1", column, 0)
        with self.assertRaises(BoardError):
            self.store.create_card({"title": "b", "column": "progress"})
        with self.assertRaises(BoardError):
            self.store.move_card("K-9", "todo", 0)

    def test_update_keeps_hand_edits_and_unknown_keys(self):
        self.store.create_card({"title": "a", "description": "stary opis"})
        text = self.card_text("K-1").replace("branch:\n", "branch:\npriorytet: wysoki # moje\n")
        self.write("cards/K-1.md", text + "\nDopisek ręczny.\n")
        self.store.sync()
        self.clock[0] = NOW + 60
        result = self.store.update_card("K-1", {"title": "Nowy tytuł", "tags": "pilne, Pilne, własne"})
        self.assertEqual(result, {"ok": True, "id": "K-1", "changed": True})
        text = self.card_text("K-1")
        self.assertIn("priorytet: wysoki # moje\n", text)
        self.assertIn("Dopisek ręczny.", text)
        self.assertIn("title: Nowy tytuł\n", text)
        self.assertIn("tags: pilne, własne\n", text)
        card = self.cards()["K-1"]
        self.assertGreater(card["updatedTs"], card["createdTs"])
        self.assertEqual(self.log()[-1], "K-1: update title, tags")
        self.assertEqual(self.store.update_card("K-1", {"title": "Nowy tytuł"})["changed"], False)

    def test_update_validation(self):
        self.store.create_card({"title": "a"})
        bad = [{"column": "todo"}, {"order": 1}, {"nieznane": 1}, {"title": ""}, {"title": "x" * 201},
               {"tags": [{"a": 1}]}, {"tags": ["a,b"]}, {"note": "a\x00b"}, {"description": "\x07"},
               {"project": "nie-ma"}, {"title": 12.5, "note": ["lista"]}]
        before = self.card_text("K-1")
        for data in bad:
            with self.assertRaises(BoardError, msg=data):
                self.store.update_card("K-1", data)
        self.assertEqual(self.card_text("K-1"), before)
        with self.assertRaises(BoardError) as ctx:
            self.store.update_card("K-1", {"column": "todo"})
        self.assertIn("MoveCard", str(ctx.exception))
        for bad_id in ("K-0", "K-1/../x", "../K-1", "X-1", ""):
            with self.assertRaises(BoardError, msg=bad_id):
                self.store.update_card(bad_id, {"title": "y"})


class ProjectAndTagTest(BoardCase):
    def test_card_for_repo_creates_project_lazily_with_default_ai_docs(self):
        plain = self.repo("TimeTracker")
        agents = self.repo("plasma-tide", agents_docs=True)
        self.assertEqual(self.store.create_card({"title": "a", "repo": plain})["id"], "K-1")
        self.store.create_card({"title": "b", "repo": agents + "/"})
        self.store.create_card({"title": "c", "repo": plain})  # ten sam projekt, bez drugiego wpisu
        projects = {p["slug"]: p for p in self.store.snapshot()["projects"]}
        self.assertEqual(set(projects), {"timetracker", "plasma-tide"})
        self.assertEqual(projects["timetracker"]["aiDocs"], "tide")
        self.assertEqual(projects["timetracker"]["docsDir"], os.path.join(self.root, "docs", "timetracker"))
        self.assertEqual(projects["plasma-tide"]["aiDocs"], "repo")
        self.assertEqual(projects["plasma-tide"]["docsDir"], os.path.join(agents, "docs", "agents"))
        self.assertEqual(projects["timetracker"]["name"], "TimeTracker")
        self.assertEqual(projects["timetracker"]["cards"], 2)
        self.assertEqual(self.log(), ["board: create", "project timetracker: create", "K-1: create",
                                      "project plasma-tide: create", "K-2: create", "K-3: create"])
        with open(os.path.join(self.root, "projects", "timetracker.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), f"---\nname: TimeTracker\nrepo: {plain}\ntags:\nai-docs: tide\n"
                                        "tool: claude\nmode: light\n---\n")

    def test_missing_ai_docs_key_uses_repo_default(self):
        agents = self.repo("agent-team", agents_docs=True)
        self.store.upsert_project({"repo": agents})
        path = os.path.join(self.root, "projects", "agent-team.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.write("projects/agent-team.md", text.replace("ai-docs: repo\n", ""))
        project = self.store.snapshot()["projects"][0]
        self.assertEqual((project["aiDocs"], project["aiDocsDefault"]), ("repo", True))

    def test_projects_without_repo_and_explicit_settings(self):
        result = self.store.upsert_project({"name": "Dom i ogród", "tags": ["własne"], "tool": "codex",
                                            "mode": "spec", "description": "Rzeczy poza kodem."})
        self.assertEqual(result, {"ok": True, "slug": "dom-i-ogrod", "changed": True})
        project = self.store.snapshot()["projects"][0]
        self.assertEqual((project["repo"], project["aiDocs"], project["tool"], project["mode"]),
                         ("", "tide", "codex", "spec"))
        self.assertEqual(project["description"], "Rzeczy poza kodem.")
        self.assertEqual(self.store.upsert_project({"slug": "dom-i-ogrod", "mode": "spec"})["changed"], False)
        self.store.upsert_project({"slug": "dom-i-ogrod", "mode": "full"})
        self.assertEqual(self.log()[-2:], ["project dom-i-ogrod: create", "project dom-i-ogrod: update"])
        with self.assertRaises(BoardError):
            self.store.upsert_project({"slug": "dom-i-ogrod", "aiDocs": "repo"})  # bez repozytorium

    def test_project_validation(self):
        notgit = os.path.join(self.ws, "zwykly")
        os.makedirs(notgit)
        repo = self.repo("alpha")
        self.store.upsert_project({"repo": repo})
        for data in ({"repo": "wzgledna/sciezka"}, {"repo": notgit}, {"repo": os.path.join(self.ws, "brak")},
                     {"repo": self.root}, {"slug": "../wyjscie", "name": "x"}, {"slug": "A B", "name": "x"},
                     {"name": "y", "tool": "vim"}, {"name": "y", "mode": "turbo"}, {"name": "y", "aiDocs": "gdzies"},
                     {}, {"slug": "inny", "repo": repo}, {"name": "x", "nieznane": True}):
            with self.assertRaises(BoardError, msg=data):
                self.store.upsert_project(data)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "projects"))), ["alpha.md"])

    def test_tag_inheritance_project_union_own(self):
        repo = self.repo("mobileapp")
        self.store.upsert_project({"repo": repo, "tags": ["Firma"]})
        self.store.create_card({"title": "a", "project": "mobileapp", "tags": ["pilne", "firma"]})
        self.store.create_card({"title": "b", "tags": ["pomysł"]})
        cards = self.cards()
        self.assertEqual(cards["K-1"]["tags"], ["pilne", "firma"])
        self.assertEqual(cards["K-1"]["resolvedTags"], ["Firma", "pilne"])
        self.assertEqual(cards["K-1"]["projectName"], "mobileapp")
        self.assertEqual(cards["K-2"]["resolvedTags"], ["pomysł"])
        tags = {t["name"]: t for t in self.store.snapshot()["tags"]}
        self.assertTrue(tags["Firma"]["defined"])
        self.assertEqual(tags["Firma"]["cards"], 1)
        self.assertFalse(tags["pilne"]["defined"])
        self.assertIn(tags["pilne"]["color"], ("teal", "lavender", "peach", "butter", "rose", "mint", "sky"))
        self.store.upsert_project({"slug": "mobileapp", "tags": []})
        self.assertEqual(self.cards()["K-1"]["resolvedTags"], ["pilne", "firma"])

    def test_tags_create_update_rename(self):
        self.store.create_card({"title": "a", "tags": ["pilne", "własne"]})
        self.store.upsert_project({"name": "Dom", "tags": ["własne"]})
        self.assertEqual(self.store.upsert_tag({"name": "pilne", "color": "rose"}),
                         {"ok": True, "name": "pilne", "changed": True})
        self.assertEqual(self.store.upsert_tag({"name": "Pilne", "color": "rose"})["changed"], False)
        self.store.upsert_tag({"name": "własne", "newName": "Własne", "color": "mint"})
        snap = self.store.snapshot()
        self.assertEqual({t["name"]: t["color"] for t in snap["tags"]},
                         {"Firma": "sky", "pilne": "rose", "Własne": "mint"})
        self.assertEqual(snap["cards"][0]["tags"], ["pilne", "Własne"])
        self.assertEqual(snap["projects"][0]["tags"], ["Własne"])
        self.assertEqual(self.log()[-2:], ["tag pilne: create", "tag własne: rename to Własne"])
        for data in ({"name": "x", "color": "fiolet"}, {"name": ""}, {"name": "a,b"}, {"name": "nowy", "newName": "x"},
                     {"name": "pilne", "newName": "Firma"}):
            with self.assertRaises(BoardError, msg=data):
                self.store.upsert_tag(data)
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_colors_come_from_palette_names(self):
        store = BoardStore(self.root, clock=lambda: NOW, colors={"teal": "#6fd6c6", "sky": "#a6c8f0"})
        store.upsert_tag({"name": "x", "color": "teal"})
        with self.assertRaises(BoardError):
            store.upsert_tag({"name": "y", "color": "rose"})  # nie ma w tej palecie
        snap = store.snapshot()
        self.assertEqual(snap["colors"], {"teal": "#6fd6c6", "sky": "#a6c8f0"})
        self.assertTrue(all(t["color"] in ("teal", "sky") for t in snap["tags"]))


class FlowTest(BoardCase):
    def test_render_flow_fills_placeholders(self):
        repo = self.repo("mobileapp")
        self.store.upsert_project({"repo": repo, "mode": "full", "tool": "codex"})
        self.store.create_card({"title": "Historia diagnoz z filtrem po VIN", "project": "mobileapp",
                                "description": "Opis z {title} i {\"json\": 1}"})
        flow = self.store.render_flow("K-1", "")
        self.assertEqual((flow["mode"], flow["tool"]), ("full", "codex"))
        self.assertEqual(flow["branch"], "codex/k-1-historia-diagnoz-filtrem-vin")
        self.assertEqual(flow["docsDir"], os.path.join(self.root, "docs", "mobileapp"))
        self.assertEqual(flow["template"], os.path.join(self.root, "flows", "full.md"))
        text = flow["text"]
        self.assertTrue(text.startswith("Karta K-1: Historia diagnoz z filtrem po VIN\n"))
        self.assertIn(f"Repozytorium: {repo}\n", text)
        self.assertIn("Opis z {title} i {\"json\": 1}", text)  # opis nie jest szablonem
        self.assertNotIn("tide-flow", text)
        self.assertIn("card: K-1", text)

    def test_project_override_and_unknown_braces(self):
        self.store.upsert_project({"name": "Dom"})
        self.store.create_card({"title": "Grabie", "project": "dom"})
        self.store.update_card("K-1", {"branch": "moja/galaz"})
        self.write("flows/dom/spec.md", "Spec {card_id} {branch} {nieznane} {docs_dir}\n")
        flow = self.store.render_flow("k-1", "spec")
        self.assertEqual(flow["text"], f"Spec K-1 moja/galaz {{nieznane}} {os.path.join(self.root, 'docs', 'dom')}\n")
        self.assertEqual(self.store.render_flow("K-1", "light")["template"],
                         os.path.join(self.root, "flows", "light.md"))
        with self.assertRaises(BoardError):
            self.store.render_flow("K-1", "turbo")
        with self.assertRaises(BoardError):
            self.store.render_flow("K-7", "full")


class SyncAndErrorsTest(BoardCase):
    def test_external_changes_are_committed_separately(self):
        self.store.create_card({"title": "a"})
        self.assertFalse(self.store.sync())
        self.write("docs/mobileapp/plans/2026-10-27-vin.md", "# plan\n")
        with open(os.path.join(self.root, "cards", "K-1.md"), "a", encoding="utf-8") as fh:
            fh.write("\nRęczny dopisek.\n")
        self.write(".tide-tmp-abc", "śmieć")
        self.write("cards/.K-1.md.swp", "edytor")
        self.assertTrue(self.store.sync())
        self.assertEqual(self.log()[-1], "sync external changes")
        files = git_out(self.root, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(files), ["cards/K-1.md", "docs/mobileapp/plans/2026-10-27-vin.md"])
        self.assertFalse(self.store.sync())

    def test_our_commit_does_not_take_pending_external_changes(self):
        self.store.create_card({"title": "a"})
        self.write("docs/notatka.md", "agent pisze\n")
        self.store.create_card({"title": "b"})
        files = git_out(self.root, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(files), ["board.md", "cards/K-2.md"])
        self.assertTrue(self.store.sync())

    def test_bad_files_are_reported_not_fatal(self):
        self.store.create_card({"title": "dobra"})
        self.store.upsert_project({"name": "Dom"})
        self.write("cards/K-2.md", "bez frontmattera\n")
        self.write("cards/K-3.md", "---\nid: K-9\ntitle:\ncolumn: w-toku\norder: dużo\nproject: brak\n---\n")
        self.write("cards/notatka.md", "---\ntitle: x\n---\n")
        with open(os.path.join(self.root, "cards", "K-4.md"), "wb") as fh:
            fh.write(b"---\ntitle: \xff\xfe\n---\n")
        self.write("cards/K-5.md", "---\ntitle: wielka\n---\n" + "x" * (300 * 1024))
        self.write("projects/Zla Nazwa.md", "---\nname: x\n---\n")
        self.write("projects/zly.md", "---\nname: Zły\nrepo: wzgledna\ntool: vim\nmode: turbo\nai-docs: gdzies\n---\n")
        self.write("tags/kolor.md", "---\nname: kolor\ncolor: fiolet\n---\n")
        snap = self.store.snapshot()
        messages = " | ".join(e["message"] for e in snap["errors"])
        for fragment in ("K-2.md: brak frontmattera", "id „K-9” nie zgadza się", "K-3.md: brak tytułu",
                         "nieznana kolumna „w-toku”", "niepoprawna kolejność", "K-3: nieznany projekt „brak”",
                         "notatka.md: nazwa pliku karty", "K-4.md: plik nie jest w UTF-8", "większy niż",
                         "Zla Nazwa.md", "repo musi być ścieżką bezwzględną", "nieznane narzędzie „vim”",
                         "nieznany tryb „turbo”", "nieznane ai-docs", "nieznany kolor „fiolet”"):
            self.assertIn(fragment, messages)
        cards = {c["id"]: c for c in snap["cards"]}
        self.assertEqual(set(cards), {"K-1", "K-3"})
        self.assertEqual((cards["K-3"]["title"], cards["K-3"]["column"], cards["K-3"]["order"]), ("K-3", "backlog", 0))
        self.assertEqual(snap["nextId"], "K-6")
        with self.assertRaises(BoardError) as ctx:
            self.store.update_card("K-2", {"title": "naprawa"})
        self.assertIn("uszkodzony", str(ctx.exception))
        self.assertEqual(self.store.update_card("K-3", {"title": "naprawiona"})["changed"], True)
        self.assertIn("id: K-9\n", self.card_text("K-3"))  # nie poprawiamy po cichu tego, czego nie zmieniamy


class ScannerExclusionTest(BoardCase):
    def test_board_repo_is_not_a_project_anywhere(self):
        alpha = self.repo("alpha")
        self.store.create_card({"title": "a"})
        renamed = os.path.join(self.ws, "group", "zupelnie-inna-nazwa")
        os.makedirs(os.path.dirname(renamed))
        BoardStore(renamed, clock=lambda: NOW).create_card({"title": "b"})
        lookalike = self.repo("not-a-board")
        with open(os.path.join(lookalike, "board.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntitle: tablica, ale nie Tide\n---\n")
        found = find_repositories(self.ws)
        self.assertEqual(sorted(found), sorted([alpha, lookalike]))
        snap = self.store.snapshot(repos=found)
        self.assertEqual([r["path"] for r in snap["repos"]], [alpha, lookalike])
        self.store.upsert_project({"repo": alpha})
        self.assertEqual([r["path"] for r in self.store.snapshot(repos=found)["repos"]], [lookalike])


if __name__ == "__main__":
    unittest.main()
