"""Test statyczny K-128 (PT-507): jeden zestaw przycisków w całej aplikacji.

- w `apps/` nie ma plików `PillButton.qml` ani `Action.qml`,
- poza `DiffView.qml` (K-104, `PillAction`) żaden plik QML nie używa `PillButton`, `Action` ani `PillAction`
  jako komponentu (też w komentarzach nie ma nazwy dawnej kopii: została tylko w DiffView),
- w żadnym widoku ani dialogu nie ma więcej niż jednego `Tide.Button` w wariancie głównym: plik z dwoma literalnymi
  `variant: "primary"` jest wyjątkiem tylko na liście niżej (wariant zależny od stanu, np. `x ? "primary" : "normal"`,
  liczy się jako stan, nie jako dodatkowy główny przycisk),
- przyciski nie mają już parametrów dawnych kopii (`pillRadius`, `horizontalPadding`, `pixelSize`, `outline`,
  `flat`, `leftAligned`, `wrapText` ustawianych na `Tide.Button`).
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPS = os.path.join(REPO, "apps")

# Jedyny plik, który zachowuje PillAction (K-104, widok zmian) — poza testem.
DIFF_VIEW = os.path.join("apps", "common", "qml", "git", "DiffView.qml")
# Pliki z więcej niż jednym literalnym głównym przyciskiem: panel karty (PT-504) ma w jednym dialogu kilka sekcji
# z własnym „głównym” (zatwierdzenie planu, ponowienie poprawek, zapis); zostaje do osobnej decyzji o układzie A2.
SEVERAL_PRIMARY = {os.path.join("apps", "board", "qml", "CardPanel.qml")}

OLD_COMPONENT = re.compile(r"(?<![\w.])(?:PillButton|PillAction|Action)\b")
PRIMARY = re.compile(r'(?<![\w.])variant\s*:\s*"primary"')
BUTTON_START = re.compile(r"(?<![\w.])Tide\.Button\s*\{")
OLD_PARAMETERS = re.compile(r"^\s*(?:pillRadius|horizontalPadding|pixelSize|outline|flat|leftAligned|wrapText|fill|"
                            r"textColor|strong)\s*:", re.M)


def qml_files():
    for directory, _, names in os.walk(APPS):
        if os.sep + "tests" in directory + os.sep or "__pycache__" in directory:
            continue
        for name in sorted(names):
            if name.endswith(".qml"):
                path = os.path.join(directory, name)
                yield os.path.relpath(path, REPO), path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def block(text, start):
    """Treść od `{` na pozycji start do pasującego `}` (bez uwzględniania klamer w napisach — pliki QML ich tu nie mają)."""
    depth = 0
    for position in range(start, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[start:position + 1]
    return text[start:]


def own_lines(button):
    """Linie samego przycisku, bez zagnieżdżonych bloków (handlery, Popup, pomocnicze elementy)."""
    lines, depth = [], 0
    for line in button.split("\n"):
        if depth <= 1:
            lines.append(line)
        depth += line.count("{") - line.count("}")
    return "\n".join(lines)


class ButtonsStaticTest(unittest.TestCase):
    def test_old_copies_are_gone(self):
        found = []
        for directory, _, names in os.walk(APPS):
            for name in names:
                if name in ("PillButton.qml", "Action.qml"):
                    found.append(os.path.relpath(os.path.join(directory, name), REPO))
        self.assertEqual(found, [])

    def test_no_old_component_outside_diff_view(self):
        offenders = []
        for relative, path in qml_files():
            if relative == DIFF_VIEW:
                continue
            for number, line in enumerate(read(path).split("\n"), 1):
                if OLD_COMPONENT.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()[:100]}")
        self.assertEqual(offenders, [])

    def test_diff_view_keeps_pill_action(self):
        self.assertIn("PillAction", read(os.path.join(REPO, DIFF_VIEW)))

    def test_at_most_one_primary_button_per_file(self):
        crowded = {}
        for relative, path in qml_files():
            count = len(PRIMARY.findall(read(path)))
            if count > 1:
                crowded[relative] = count
        self.assertEqual(sorted(crowded), sorted(SEVERAL_PRIMARY), crowded)

    def test_buttons_do_not_set_old_copy_parameters(self):
        offenders = []
        for relative, path in qml_files():
            text = read(path)
            for match in BUTTON_START.finditer(text):
                button = own_lines(block(text, match.end() - 1))
                for found in OLD_PARAMETERS.finditer(button):
                    number = text[:match.start()].count("\n") + 1 + button[:found.start()].count("\n")
                    offenders.append(f"{relative}:{number}: {found.group(0).strip()}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
