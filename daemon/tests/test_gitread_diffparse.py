"""PT-359: parser diffu jednolitego (gitread/diffparse.py) — czysta funkcja testowana tekstami.

Teksty są zapisane ręcznie w formacie `git diff`; żaden test nie uruchamia gita ani nie czyta plików.
"""
import builtins
import subprocess
import unittest
from unittest import mock

from tide_daemon.gitread.diffparse import parse_diff


def diff(*lines):
    return "\n".join(lines) + "\n"


def one(text):
    result = parse_diff(text)
    assert len(result["files"]) == 1, result
    return result["files"][0]


class StatusTest(unittest.TestCase):
    def test_added_file(self):
        f = one(diff("diff --git a/new.txt b/new.txt", "new file mode 100644",
                     "index 0000000..e69de29", "--- /dev/null", "+++ b/new.txt",
                     "@@ -0,0 +1,2 @@", "+one", "+two"))
        self.assertEqual((f["path"], f["oldPath"], f["status"]), ("new.txt", None, "A"))
        self.assertEqual((f["additions"], f["deletions"], f["binary"]), (2, 0, False))
        self.assertEqual(f["hunks"][0]["lines"], [
            {"kind": "+", "oldNo": None, "newNo": 1, "text": "one"},
            {"kind": "+", "oldNo": None, "newNo": 2, "text": "two"}])
        self.assertEqual(f["warnings"], [])

    def test_deleted_file(self):
        f = one(diff("diff --git a/old.txt b/old.txt", "deleted file mode 100644",
                     "index e69de29..0000000", "--- a/old.txt", "+++ /dev/null",
                     "@@ -1,2 +0,0 @@", "-one", "-two"))
        self.assertEqual((f["path"], f["status"]), ("old.txt", "D"))
        self.assertEqual((f["additions"], f["deletions"]), (0, 2))
        self.assertEqual(f["hunks"][0]["lines"][1],
                         {"kind": "-", "oldNo": 2, "newNo": None, "text": "two"})

    def test_modified_file_with_context_and_numbers(self):
        f = one(diff("diff --git a/a.py b/a.py", "index 1..2 100644", "--- a/a.py", "+++ b/a.py",
                     "@@ -10,4 +10,4 @@ def foo():", " keep1", "-old", "+new", " keep2", " keep3"))
        h = f["hunks"][0]
        self.assertEqual((h["oldStart"], h["oldLines"], h["newStart"], h["newLines"]),
                         (10, 4, 10, 4))
        self.assertEqual(h["header"], "def foo():")
        self.assertEqual([(l["kind"], l["oldNo"], l["newNo"], l["text"]) for l in h["lines"]], [
            (" ", 10, 10, "keep1"), ("-", 11, None, "old"), ("+", None, 11, "new"),
            (" ", 12, 12, "keep2"), (" ", 13, 13, "keep3")])
        self.assertEqual((f["status"], f["additions"], f["deletions"]), ("M", 1, 1))

    def test_pure_rename(self):
        f = one(diff("diff --git a/old.txt b/new.txt", "similarity index 100%",
                     "rename from old.txt", "rename to new.txt"))
        self.assertEqual((f["path"], f["oldPath"], f["status"]), ("new.txt", "old.txt", "R"))
        self.assertEqual(f["hunks"], [])

    def test_rename_with_changes(self):
        f = one(diff("diff --git a/old.txt b/new.txt", "similarity index 80%",
                     "rename from old.txt", "rename to new.txt", "index 1..2 100644",
                     "--- a/old.txt", "+++ b/new.txt", "@@ -1 +1 @@", "-a", "+b"))
        self.assertEqual((f["path"], f["oldPath"], f["status"]), ("new.txt", "old.txt", "R"))
        self.assertEqual((f["additions"], f["deletions"]), (1, 1))

    def test_copy(self):
        f = one(diff("diff --git a/src.txt b/dup.txt", "similarity index 100%",
                     "copy from src.txt", "copy to dup.txt"))
        self.assertEqual((f["path"], f["oldPath"], f["status"]), ("dup.txt", "src.txt", "C"))

    def test_mode_change(self):
        f = one(diff("diff --git a/run.sh b/run.sh", "old mode 100644", "new mode 100755"))
        self.assertEqual((f["path"], f["status"]), ("run.sh", "T"))
        self.assertEqual((f["oldMode"], f["newMode"]), ("100644", "100755"))
        self.assertEqual(f["hunks"], [])

    def test_rename_wins_over_mode_change(self):
        f = one(diff("diff --git a/a b/b", "old mode 100644", "new mode 100755",
                     "similarity index 100%", "rename from a", "rename to b"))
        self.assertEqual(f["status"], "R")

    def test_binary_file(self):
        f = one(diff("diff --git a/img.png b/img.png", "index 1..2 100644",
                     "Binary files a/img.png and b/img.png differ"))
        self.assertTrue(f["binary"])
        self.assertEqual((f["status"], f["hunks"], f["additions"], f["deletions"]),
                         ("M", [], 0, 0))

    def test_new_binary_file_is_added(self):
        f = one(diff("diff --git a/img.png b/img.png", "new file mode 100644", "index 0..2",
                     "Binary files /dev/null and b/img.png differ"))
        self.assertEqual((f["status"], f["binary"], f["path"]), ("A", True, "img.png"))

    def test_git_binary_patch_data_is_not_diff_lines(self):
        result = parse_diff(diff(
            "diff --git a/x.bin b/x.bin", "index 1..2 100644", "GIT binary patch",
            "literal 10", "zc-+-+garbage", "", "literal 0", "HcmV?d00001", "",
            "diff --git a/b.txt b/b.txt", "--- a/b.txt", "+++ b/b.txt", "@@ -1 +1 @@", "-x", "+y"))
        self.assertEqual([f["path"] for f in result["files"]], ["x.bin", "b.txt"])
        self.assertTrue(result["files"][0]["binary"])
        self.assertEqual(result["files"][0]["warnings"], [])
        self.assertEqual(result["totals"]["additions"], 1)


class HunkTest(unittest.TestCase):
    def test_no_newline_marker_is_not_a_content_line(self):
        f = one(diff("diff --git a/a b/a", "--- a/a", "+++ b/a", "@@ -1 +1 @@",
                     "-old", "\\ No newline at end of file", "+new",
                     "\\ No newline at end of file"))
        lines = f["hunks"][0]["lines"]
        self.assertEqual([(l["kind"], l["text"]) for l in lines], [("-", "old"), ("+", "new")])
        self.assertEqual((f["additions"], f["deletions"], f["warnings"]), (1, 1, []))

    def test_multiple_hunks_with_counts_defaulting_to_one(self):
        f = one(diff("diff --git a/a b/a", "--- a/a", "+++ b/a",
                     "@@ -1,2 +1,3 @@ first", " a", "+b", " c",
                     "@@ -20 +21 @@ second", "-x", "+y"))
        h1, h2 = f["hunks"]
        self.assertEqual((h1["header"], len(h1["lines"])), ("first", 3))
        self.assertEqual((h2["oldStart"], h2["oldLines"], h2["newStart"], h2["newLines"]),
                         (20, 1, 21, 1))
        self.assertEqual(h1["lines"][2], {"kind": " ", "oldNo": 2, "newNo": 3, "text": "c"})
        self.assertEqual(h2["lines"][0]["oldNo"], 20)
        self.assertEqual(h2["lines"][1]["newNo"], 21)
        self.assertEqual((f["additions"], f["deletions"]), (2, 1))

    def test_content_that_looks_like_headers_stays_content(self):
        f = one(diff("diff --git a/a b/a", "--- a/a", "+++ b/a", "@@ -1 +1 @@",
                     "--- gone", "+++ added"))
        self.assertEqual([(l["kind"], l["text"]) for l in f["hunks"][0]["lines"]],
                         [("-", "-- gone"), ("+", "++ added")])
        self.assertEqual((f["additions"], f["deletions"], f["warnings"]), (1, 1, []))

    def test_empty_diff(self):
        for text in ("", "\n", None):
            self.assertEqual(parse_diff(text), {
                "files": [], "totals": {"files": 0, "additions": 0, "deletions": 0},
                "warnings": []})


class PathTest(unittest.TestCase):
    def test_spaces_and_markup_are_plain_text(self):
        for path in ("dir with space/my file.txt", "<b>bold</b>.txt", "a b/<i>x</i> y.md"):
            f = one(diff(f"diff --git a/{path} b/{path}", f"--- a/{path}\t", f"+++ b/{path}\t",
                         "@@ -1 +1 @@", "-a", "+b"))
            self.assertEqual(f["path"], path)

    def test_spaces_in_header_only_diff(self):
        f = one(diff("diff --git a/my dir/a b.txt b/my dir/a b.txt", "old mode 100644",
                     "new mode 100755"))
        self.assertEqual(f["path"], "my dir/a b.txt")

    def test_quoted_unicode_octal_is_decoded(self):
        path = "zażółć/gęślą.txt"
        quoted = '"' + "".join(f"\\{b:03o}" if b > 127 else chr(b)
                              for b in path.encode()) + '"'
        f = one(diff(f'diff --git "a/{quoted[1:-1]}" "b/{quoted[1:-1]}"', "new file mode 100644",
                     "--- /dev/null", f'+++ "b/{quoted[1:-1]}"', "@@ -0,0 +1 @@", "+x"))
        self.assertEqual((f["path"], f["status"]), (path, "A"))

    def test_quoted_escapes_and_rename_lines(self):
        f = one(diff('diff --git "a/t\\tab \\"q\\".txt" "b/\\303\\251.txt"',
                     "similarity index 100%", 'rename from "t\\tab \\"q\\".txt"',
                     'rename to "\\303\\251.txt"'))
        self.assertEqual((f["oldPath"], f["path"]), ('t\tab "q".txt', "é.txt"))

    def test_quoted_header_without_other_lines(self):
        f = one(diff('diff --git "a/\\303\\251 x" "b/\\303\\251 x"', "old mode 100644",
                     "new mode 100755"))
        self.assertEqual(f["path"], "é x")


class TotalsTest(unittest.TestCase):
    def test_totals_are_sum_of_files(self):
        result = parse_diff(diff(
            "diff --git a/a b/a", "--- a/a", "+++ b/a", "@@ -1,2 +1,3 @@", " k", "+n1", "+n2",
            "diff --git a/b b/b", "deleted file mode 100644", "--- a/b", "+++ /dev/null",
            "@@ -1 +0,0 @@", "-gone",
            "diff --git a/c b/c", "Binary files a/c and b/c differ"))
        self.assertEqual(result["totals"], {"files": 3, "additions": 2, "deletions": 1})
        self.assertEqual(sum(f["additions"] for f in result["files"]), 2)
        for f in result["files"]:
            self.assertEqual(f["additions"], sum(l["kind"] == "+" for h in f["hunks"] for l in h["lines"]))


class InvalidInputTest(unittest.TestCase):
    def test_truncated_hunk_warns_and_keeps_other_files(self):
        result = parse_diff(diff(
            "diff --git a/a b/a", "--- a/a", "+++ b/a", "@@ -1,5 +1,5 @@", " k", "-x",
            "diff --git a/b b/b", "--- a/b", "+++ b/b", "@@ -1 +1 @@", "-o", "+n"))
        a, b = result["files"]
        self.assertEqual(len(a["warnings"]), 1)
        self.assertIn("ucięty hunk", a["warnings"][0])
        self.assertEqual((a["additions"], a["deletions"]), (0, 1))
        self.assertEqual((b["warnings"], b["additions"], b["deletions"]), ([], 1, 1))

    def test_truncated_at_end_of_text(self):
        f = one("diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1,3 +1,3 @@\n k")
        self.assertIn("ucięty hunk", f["warnings"][0])

    def test_unknown_header_warns(self):
        result = parse_diff(diff(
            "diff --git a/a b/a", "mystery header: 1", "--- a/a", "+++ b/a", "@@ -1 +1 @@", "-o", "+n",
            "diff --git a/b b/b", "--- a/b", "+++ b/b", "@@ -1 +1 @@", "-o", "+n"))
        a, b = result["files"]
        self.assertTrue(any("nieznany nagłówek" in w for w in a["warnings"]))
        self.assertEqual((a["additions"], b["warnings"], b["additions"]), (1, [], 1))

    def test_garbage_does_not_raise(self):
        for text in ("diff --git", "diff --git \n", "diff --git \"a/\n", "@@ -x +y @@\n",
                     "random\ntext", "diff --git a/a b/a\n@@ -1 +1 @@\n+x\n\x00\xff",
                     "diff --git a/a b/a\n@@ -99999999999999999999 +1 @@\n-x"):
            result = parse_diff(text)
            self.assertIn("files", result)
            self.assertIn("totals", result)

    def test_text_before_first_header_is_a_top_level_warning(self):
        result = parse_diff("hello\n" + diff("diff --git a/a b/a", "old mode 100644",
                                              "new mode 100755"))
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(len(result["warnings"]), 1)


class PurityTest(unittest.TestCase):
    def test_no_processes_and_no_file_reads(self):
        text = diff("diff --git a/a b/a", "--- a/a", "+++ b/a", "@@ -1 +1 @@", "-o", "+n")
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("run")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Popen")), \
                mock.patch.object(subprocess, "check_output", side_effect=AssertionError("co")), \
                mock.patch("os.system", side_effect=AssertionError("system")), \
                mock.patch.object(builtins, "open", side_effect=AssertionError("open")):
            self.assertEqual(parse_diff(text)["totals"]["additions"], 1)

    def test_module_does_not_import_subprocess(self):
        from tide_daemon.gitread import diffparse
        self.assertFalse(hasattr(diffparse, "subprocess"))
        self.assertFalse(hasattr(diffparse, "os"))


if __name__ == "__main__":
    unittest.main()
