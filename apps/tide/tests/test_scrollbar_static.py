"""Test statyczny K-128: każdy przewijany widok w apps/ używa Tide.ScrollBar.

Żaden plik QML w apps/ poza listą wyjątków K-104 nie zawiera gołego ScrollBar {} (też ScrollIndicator)
ani ScrollView bez Tide.ScrollBar.
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPS = os.path.join(REPO, "apps")

# K-104, zgranie w etapie A2; lista z bazy 74dab9ba25941f0179214f532be562507df9c22b.
# Widok zmian (K-104) nie dostaje Tide.ScrollBar w tym tickecie. DiffView.qml, SyntaxText.qml i ModuleHighlighter.qml
# to części widoku zmian na tej bazie; pliki dodane commitem z K-104 w opisie
# (git log --diff-filter=A --name-only --format=%h --grep=K-104 <baza> -- apps/common/qml/git) — na tej bazie brak.
# To dosłowne nazwy plików: MergeWindow.qml, MergeSection.qml, BranchPicker.qml i BranchInput.qml podlegają testowi.
K104_EXCEPTIONS = ["DiffView.qml", "SyntaxText.qml", "ModuleHighlighter.qml"]
K104_DIR = os.path.join("apps", "common", "qml", "git")

# Goły pasek to każdy ScrollBar/ScrollIndicator z prefiksem innym niż `Tide.` (QQC2., T., Controls. itd.) albo bez prefiksu.
BARE_BAR = re.compile(r"(?<![\w.])(?:(?!Tide\.)\w+\.)*Scroll(?:Bar|Indicator)\s*\{")
FLICKABLE = re.compile(r"(?<![\w.])(?:Flickable|ListView|GridView)\s*\{")
SCROLL_VIEW = re.compile(r"(?<![\w.])(?:QQC2\.|Controls\.)?ScrollView\s*\{")


def qml_files():
    for directory, _, names in os.walk(APPS):
        if os.sep + "tests" in directory + os.sep or "__pycache__" in directory:
            continue
        for name in sorted(names):
            if name.endswith(".qml"):
                path = os.path.join(directory, name)
                relative = os.path.relpath(path, REPO)
                if os.path.dirname(relative) == K104_DIR and name in K104_EXCEPTIONS:
                    continue
                yield relative, path


def block(text, start):
    """Treść klamr od pierwszej `{` w `start` do jej pary (bez analizy napisów — wystarcza dla QML Tide)."""
    depth = 0
    for index in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start:index + 1]
    return text[start:]


class ScrollBarStaticTest(unittest.TestCase):
    def test_no_bare_scroll_bars(self):
        offenders = []
        for relative, path in qml_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            offenders += [relative for _ in BARE_BAR.finditer(text)]
        self.assertEqual(offenders, [], "Gołe ScrollBar {} / ScrollIndicator {}: użyj Tide.ScrollBar {}")

    def test_every_scroll_view_has_a_tide_bar(self):
        offenders = []
        for relative, path in qml_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for found in SCROLL_VIEW.finditer(text):
                if "Tide.ScrollBar" not in block(text, found.start()):
                    offenders.append(relative)
        self.assertEqual(offenders, [], "ScrollView bez Tide.ScrollBar")

    def test_every_flickable_has_a_tide_bar_or_a_reason(self):
        """Każdy Flickable/ListView/GridView ma Tide.ScrollBar albo komentarz `// bez paska: <powód>` w bloku."""
        offenders = []
        for relative, path in qml_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for found in FLICKABLE.finditer(text):
                body = block(text, found.start())
                if "Tide.ScrollBar" not in body and "// bez paska:" not in body:
                    offenders.append("%s:%d" % (relative, text.count("\n", 0, found.start()) + 1))
        self.assertEqual(offenders, [], "Flickable/ListView/GridView bez Tide.ScrollBar (ani `// bez paska: powód`)")

    def test_exception_list_is_literal_and_does_not_cover_the_whole_git_directory(self):
        self.assertEqual(sorted(K104_EXCEPTIONS), ["DiffView.qml", "ModuleHighlighter.qml", "SyntaxText.qml"])
        checked = {relative for relative, _ in qml_files()}
        for name in ("MergeWindow.qml", "MergeSection.qml", "BranchPicker.qml", "BranchInput.qml"):
            self.assertIn(os.path.join(K104_DIR, name), checked)
        for name in K104_EXCEPTIONS:
            self.assertTrue(os.path.isfile(os.path.join(REPO, K104_DIR, name)), name)
            self.assertNotIn(os.path.join(K104_DIR, name), checked)

    def test_checker_catches_violations(self):
        self.assertTrue(BARE_BAR.search("QQC2.ScrollBar.vertical: QQC2.ScrollBar {}"))
        self.assertTrue(BARE_BAR.search("ScrollBar { policy: 1 }"))
        self.assertTrue(BARE_BAR.search("T.ScrollBar {"))
        self.assertTrue(FLICKABLE.search("    Flickable {"))
        self.assertFalse(BARE_BAR.search("QQC2.ScrollBar.vertical: Tide.ScrollBar {}"))
        self.assertTrue(SCROLL_VIEW.search("QQC2.ScrollView {"))


if __name__ == "__main__":
    unittest.main()
