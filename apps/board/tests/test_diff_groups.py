"""Widok diffa z grupami plików i listą nieśledzonych (K-36, PT-360): apps/common/qml/git/DiffView.qml i diff.js
na odpowiedzi źródła `worktree` (sonda offscreen na atrapie backendu). Źródła bez grup: test_diff_view.py."""
import json
import unittest

from .test_diff_view import DIFF, ISO, SHA, QmlCase, file_entry, hunk, line

STAGED_A = file_entry("a.txt", "M", 1, 1, [hunk(1, [line("-", 1, None, "stare"), line("+", None, 1, "STAGED")])])
STAGED_BOTH = file_entry("both.txt", "M", 1, 0, [hunk(1, [line("+", None, 1, "w indeksie")])])
UNSTAGED_B = file_entry("b.txt", "M", 2, 0, [hunk(1, [line("+", None, 1, "x"), line("+", None, 2, "y")])])
UNSTAGED_BOTH = file_entry("both.txt", "M", 1, 0, [hunk(1, [line("+", None, 1, "poza indeksem")])])
for entry, group in ((STAGED_A, "staged"), (STAGED_BOTH, "staged"), (UNSTAGED_B, "unstaged"),
                     (UNSTAGED_BOTH, "unstaged")):
    entry["group"] = group

WORKTREE = {"ok": True, "source": "worktree", "repo": "/w/alpha", "title": "Niezacommitowane zmiany: /w/alpha",
            "from": {"ref": "HEAD", "sha": SHA, "date": ISO}, "to": {"ref": "worktree", "sha": "", "date": ""},
            "files": [STAGED_A, STAGED_BOTH, UNSTAGED_B, UNSTAGED_BOTH],
            "groups": [{"id": "staged", "name": "W indeksie", "totals": {"files": 2, "additions": 2, "deletions": 1}},
                       {"id": "unstaged", "name": "Poza indeksem",
                        "totals": {"files": 2, "additions": 3, "deletions": 0}}],
            "untracked": ["new.txt", "<b>dir</b>/ś.txt"], "totals": {"files": 4, "additions": 5, "deletions": 1},
            "truncated": False, "warnings": []}
REQUEST = {"source": "worktree", "path": "/w/alpha"}

SCENARIO = '''
WT = json.loads(%s)
REQ = json.loads(%s)
def open_wt(data=None):
    open_view(REQ)
    reply(last_tag(), data or WT)
''' % (repr(json.dumps(WORKTREE)), repr(json.dumps(REQUEST)))


class GroupsTest(QmlCase):
    def probe(self, scenario: str) -> dict:
        return super().probe(SCENARIO + scenario)

    def test_groups_have_headers_with_counts_and_untracked_list_follows(self):
        out = self.probe('''
open_wt()
out["header"] = texts("diffHeader")
out["groups"] = texts("diffGroupHeader")
out["untrackedHeader"] = texts("diffUntrackedHeader")
out["names"] = texts("diffFileName")
out["untracked"] = texts("diffUntracked")
out["order"] = field("diffFiles", "it.count")
out["groupOfRows"] = field("diffFile", "it.group")
out["totals"] = texts("diffTotals")
out["empty"] = texts("diffEmpty")
''')
        self.assertEqual(out["header"], ["Niezacommitowane zmiany: /w/alpha"])
        self.assertEqual(out["groups"], ["W indeksie · 2 pliki · +2 −1", "Poza indeksem · 2 pliki · +3 −0"])
        self.assertEqual(out["untrackedHeader"], ["Nieśledzone · 2 pliki (bez treści)"])
        self.assertEqual(out["names"], ["a.txt", "both.txt", "b.txt", "both.txt"])
        self.assertEqual(out["groupOfRows"], ["staged", "staged", "unstaged", "unstaged"])
        self.assertEqual(out["untracked"], ["new.txt", "<b>dir</b>/ś.txt"], "ścieżki jako zwykły tekst")
        self.assertEqual(out["order"], [9], "4 pliki + 2 nagłówki grup + nagłówek i 2 nieśledzone")
        self.assertEqual(out["totals"], ["4 pliki · +5 −1"])
        self.assertEqual(out["empty"], [])

    def test_same_path_in_both_groups_selects_the_clicked_entry(self):
        out = self.probe('''
open_wt()
click("diffFile", 1)
out["staged"] = [view("selectedPath"), view("selectedGroup"), view("fileIndex"),
                 [row[3] for row in field("diffLine", "[it.kind, it.oldLabel, it.newLabel, it.content]")
                  if row[0] == "+"]]
click("diffFile", 3)
out["unstaged"] = [view("selectedPath"), view("selectedGroup"), view("fileIndex"),
                   [row[3] for row in field("diffLine", "[it.kind, it.oldLabel, it.newLabel, it.content]")
                    if row[0] == "+"]]
out["selectedRows"] = field("diffFile", "it.selected")
''')
        self.assertEqual(out["staged"], ["both.txt", "staged", 1, ["w indeksie"]])
        self.assertEqual(out["unstaged"], ["both.txt", "unstaged", 3, ["poza indeksem"]])
        self.assertEqual(out["selectedRows"], [False, False, False, True])

    def test_next_file_walks_groups_in_display_order(self):
        out = self.probe('''
from PyQt6.QtCore import Qt
open_wt()
seen = [[view("selectedPath"), view("selectedGroup")]]
for _ in range(3):
    key(Qt.Key.Key_BracketRight)
    seen.append([view("selectedPath"), view("selectedGroup")])
out["seen"] = seen
''')
        self.assertEqual(out["seen"], [["a.txt", "staged"], ["both.txt", "staged"], ["b.txt", "unstaged"],
                                       ["both.txt", "unstaged"]])

    def test_content_of_a_truncated_file_is_requested_with_its_group(self):
        data = json.loads(json.dumps(WORKTREE))
        data["files"][3].update(hunks=[], truncated=True)
        out = self.probe('''
data = json.loads(%s)
open_wt(data)
click("diffFile", 3)
out["call"] = json.loads(calls()[-1][2])
''' % repr(json.dumps(data)))
        self.assertEqual(out["call"], {"source": "worktree", "path": "/w/alpha",
                                       "options": {"path": "both.txt", "group": "unstaged"}})

    def test_loaded_file_replaces_only_the_entry_of_its_group(self):
        data = json.loads(json.dumps(WORKTREE))
        data["files"][3].update(hunks=[], truncated=True)
        loaded = dict(UNSTAGED_BOTH, hunks=[hunk(1, [line("+", None, 1, "doczytane")])])
        out = self.probe('''
data = json.loads(%s)
open_wt(data)
click("diffFile", 3)
reply(last_tag(), {"ok": True, "source": "worktree", "path": "both.txt", "group": "unstaged",
                   "files": [json.loads(%s)]})
out["lines"] = [row[3] for row in field("diffLine", "[it.kind, it.oldLabel, it.newLabel, it.content]")
                if row[0] == "+"]
out["staged"] = view("files[1].loaded") is None
out["unstaged"] = view("files[3].loaded")
''' % (repr(json.dumps(data)), repr(json.dumps(loaded))))
        self.assertEqual(out["lines"], ["doczytane"])
        self.assertTrue(out["staged"], "wpis z grupy „W indeksie” zostaje bez zmian")
        self.assertTrue(out["unstaged"])

    def test_only_untracked_files_show_the_list_without_the_empty_message(self):
        data = dict(WORKTREE, files=[], groups=WORKTREE["groups"], totals={"files": 0, "additions": 0, "deletions": 0})
        out = self.probe('''
data = json.loads(%s)
open_wt(data)
out["groups"] = texts("diffGroupHeader")
out["untracked"] = texts("diffUntracked")
out["empty"] = texts("diffEmpty")
out["note"] = texts("diffUntrackedNote")
out["selected"] = view("fileIndex")
''' % repr(json.dumps(data)))
        self.assertEqual(out["groups"], [])
        self.assertEqual(out["untracked"], ["new.txt", "<b>dir</b>/ś.txt"])
        self.assertEqual(out["empty"], [])
        self.assertEqual(len(out["note"]), 1)
        self.assertEqual(out["selected"], -1)

    def test_clean_worktree_says_so(self):
        data = dict(WORKTREE, files=[], untracked=[], totals={"files": 0, "additions": 0, "deletions": 0})
        out = self.probe('''
data = json.loads(%s)
open_wt(data)
out["empty"] = texts("diffEmpty")
out["groups"] = texts("diffGroupHeader")
''' % repr(json.dumps(data)))
        self.assertEqual(out["empty"], ["Brak niezacommitowanych zmian."])
        self.assertEqual(out["groups"], [])

    def test_missing_directory_error_has_one_sentence_and_what_next(self):
        out = self.probe('''
open_view(REQ)
reply(last_tag(), {"ok": False, "error": "no-worktree",
                   "message": "Katalog worktree nie istnieje (został usunięty?): /w/alpha"})
out["shown"] = texts("diffError")
''')
        self.assertEqual(out["shown"], ["Katalog worktree nie istnieje (został usunięty?): /w/alpha. "
                                        "Odśwież listę Worktrees (r)."])

    def test_sources_without_groups_have_no_group_headers(self):
        out = self.probe('''
open_view()
reply(last_tag(), DIFF)
out["groups"] = [texts("diffGroupHeader"), texts("diffUntrackedHeader"), texts("diffUntracked")]
out["names"] = len(texts("diffFileName"))
''')
        self.assertEqual(out["groups"], [[], [], []])
        self.assertEqual(out["names"], 6)


if __name__ == "__main__":
    unittest.main()
