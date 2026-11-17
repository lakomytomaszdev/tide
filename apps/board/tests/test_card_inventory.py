"""Inwentarz kafelka karty na Tablicy (PT-505, karta K-128): zabezpieczenie odświeżenia `BoardCard.qml` „bez regresji”.

Test zbiera z każdej karty danych `inventoryfix` (czyli `boardfix` z kartami pokrywającymi 13 punktów listy
„Kafelek karty na Tablicy”) listę widocznych elementów kafelka: nazwa obiektu, tekst, kolor znaczenia (pastel
z palety, a gdy go nie ma — „neutral”), podpowiedź, `Accessible.name`, `Accessible.description` i rola
dostępności (także puste wartości). Lista każdej karty jest porównywana ze wzorcem `data/card_inventory.json`,
zapisanym na stanie sprzed zmiany wyglądu. Kolejność elementów nie ma znaczenia (wygląd może je przestawić),
liczy się zestaw. Sprawdzane są też stany: czekająca, zaznaczona, fokus, najechana, przeciągana i duch.

Wzorzec odświeżamy świadomie: usuń plik i uruchom test (zapisze nowy i zgłosi błąd, żeby nikt tego nie przeoczył).
Zmiana wzorca w commicie, który zmienia wygląd, jest regresją do wyjaśnienia w review.
"""
import json
import os
import shutil
import unittest

from . import APP_DIR, REPO
from .inventoryfix import EXTRA_IDS, inventory_data
from .test_view import ProbeCase

GOLDEN = os.path.join(APP_DIR, "tests", "data", "card_inventory.json")
WALKER = os.path.join(APP_DIR, "tests", "data", "CardInventory.qml")

SCENARIO = r"""
from PyQt6.QtCore import QUrl
from PyQt6.QtQml import QQmlComponent

WALKER = %(walker)r
component = QQmlComponent(engine, QUrl.fromLocalFile(WALKER))
walker = component.create()
if walker is None:
    raise AssertionError([e.toString() for e in component.errors()])
engine.rootContext().setContextProperty("walker", walker)
wait(600)
ids = js("page.cards.filter(c => (c.boardColumn || c.column) !== 'archived').map(c => c.id)")
out["ids"] = ids
out["cards"] = {i: json.loads(js("walker.dump(page.findCard('%%s'))" %% i)) for i in ids}
out["contrast"] = {i: json.loads(js("walker.contrast(page.findCard('%%s'))" %% i)) for i in ids}

# Stany: czekająca (K-8 z danych), zaznaczona i fokus, najechana, przeciągana, duch.
states = {}
def tile(i):
    return "page.findCard('%%s')" %% i
def fill(i):
    return js(tile(i) + ".color.toString()")
base_fill = fill("K-11")
js("page.selectedId = 'K-11'")
wait(80)
states["selected"] = json.loads(js("walker.dump(" + tile("K-11") + ")"))
js("page.selectedId = ''")
js(tile("K-9") + ".forceActiveFocus()")
wait(80)
states["focused"] = json.loads(js("walker.dump(" + tile("K-9") + ")"))
QTest.mouseMove(window, card("K-11"))
wait(150)
states["hoverChangesFill"] = fill("K-11") != base_fill
QTest.mouseMove(window, QPoint(2, 2))
wait(150)
states["hoverReleased"] = fill("K-11") == base_fill
# Przeciąganie ręcznej karty: kafelek przygaszony (0,35), duch bez przycisków.
start = card("K-11")
QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
wait(30)
for step in range(1, 9):
    QTest.mouseMove(window, QPoint(start.x() + step * 14, start.y() + step * 6))
    wait(20)
wait(80)
states["dragging"] = js("page.dragging")
states["draggedOpacity"] = js(tile("K-11") + ".opacity")
states["dragged"] = js(tile("K-11") + ".dragged")
states["ghost"] = json.loads(js("walker.dump(page.ghost)"))
QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                   QPoint(start.x() + 8 * 14, start.y() + 8 * 6))
wait(200)
# Pulsowanie pracującego ticketu: ze wspólnej fazy strony, reszta zawsze 1.
js("page.workPulseTimer.stop(); page.workPulse = 0.45")
wait(30)
pulsed = json.loads(js("walker.dump(" + tile("K-5") + ", true)"))
states["pulse"] = [[e["phase"], e["opacity"]] for e in pulsed["items"] if e.get("name") == "ticketSegment"]
out["states"] = states
"""


def normalise(entry: dict, names: dict) -> str:
    """Wpis elementu jako stabilny napis: kolory zamienione na nazwy pasteli (alfa pomijamy), reszta bez zmian."""
    entry = dict(entry)
    if entry.get("tone"):  # kolor tekstu, kropki, segmentu: pastel albo „neutral”
        entry["tone"] = names.get(color_rgb(entry["tone"]), "neutral")
    if "tones" in entry:   # przyciski: pastele tła i napisu (neutralne pomijamy)
        entry["tones"] = sorted({names[color_rgb(v)] for v in entry["tones"] if v and color_rgb(v) in names})
    if "border" in entry:  # obwódka kafelka: pastel albo nic
        entry["border"] = names.get(color_rgb(entry["border"]), "") if entry["border"] else ""
    return json.dumps(entry, ensure_ascii=False, sort_keys=True)


def color_rgb(value: str) -> str:
    value = str(value).lower().lstrip("#")
    return value[-6:] if len(value) in (6, 8) else value


def rgba(value: str) -> tuple:
    """„#rrggbb” albo „#aarrggbb” (jak String(color) w QML) → (r, g, b, a) w 0…1."""
    value = str(value).lstrip("#")
    alpha = int(value[:2], 16) / 255 if len(value) == 8 else 1.0
    value = value[-6:]
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)


def over(layer: tuple, below: tuple) -> tuple:
    a = layer[3]
    return tuple(layer[i] * a + below[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(color: tuple) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(color[0]) + 0.7152 * lin(color[1]) + 0.0722 * lin(color[2])


def contrast_ratio(fg: tuple, bg: tuple) -> float:
    high, low = sorted((luminance(over(fg, bg)), luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def pastel_names() -> dict:
    with open(os.path.join(REPO, "theme", "palette", "mist.json"), encoding="utf-8") as fh:
        palette = json.load(fh)
    names = {color_rgb(v): k for k, v in palette["pastel"].items()}
    names[color_rgb(palette["accent"])] = "teal"
    return names


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardInventoryTest(ProbeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = inventory_data(os.path.join(cls.tmp, "inv"))
        cls.names = pastel_names()
        cls._raw = None

    @property
    def raw(self) -> dict:
        """Jedna sonda na całą klasę: zbiera inwentarz wszystkich kart i stany."""
        cls = type(self)
        if cls._raw is None:
            cls._raw = self.probe(SCENARIO % {"walker": WALKER}, data=cls.data)
        return cls._raw

    def inventory(self) -> dict:
        cards = {i: sorted(normalise(e, self.names) for e in dump["items"]) for i, dump in self.raw["cards"].items()}
        roots = {i: normalise(dump["root"], self.names) for i, dump in self.raw["cards"].items()}
        states = {key: ({"root": normalise(v["root"], self.names),
                         "items": sorted(normalise(e, self.names) for e in v["items"])}
                        if isinstance(v, dict) and "root" in v else v)
                  for key, v in self.raw["states"].items()}
        return {"cards": cards, "roots": roots, "states": states}

    # --- pokrycie listy (13 punktów) ------------------------------------------------------------

    def items(self, card_id: str) -> list:
        return [json.loads(normalise(e, self.names)) for e in self.raw["cards"][card_id]["items"]]

    def texts(self, card_id: str) -> list:
        return [e.get("text", "") for e in self.items(card_id)]

    def named(self, card_id: str, name: str) -> list:
        return [e for e in self.items(card_id) if e.get("name") == name]

    def root(self, card_id: str) -> dict:
        return json.loads(normalise(self.raw["cards"][card_id]["root"], self.names))

    def test_data_covers_every_element_of_the_list(self):
        ids = self.raw["ids"]
        for extra in EXTRA_IDS:
            self.assertIn(extra, ids)
        # 1, 2: zwarty wariant w Gotowe: numer, projekt, tytuł; długi tytuł jest w danych.
        for card_id in ("K-1", "K-2", "K-3", "K-4"):
            self.assertTrue(self.root(card_id)["compact"], card_id)
            self.assertIn(card_id, self.texts(card_id))
        self.assertGreater(len(self.data_card("K-4")["title"]), 120)
        self.assertIn("LogViewer", " ".join(self.texts("K-4")))
        # 3: pastylki dodatkowych projektów.
        self.assertEqual([e["text"] for e in self.named("K-12", "cardExtraProjectText")], ["+ mobileapp", "+ CookApp"])
        self.assertEqual([e["text"] for e in self.named("K-18", "cardExtraProjectText")], ["+ webservice"])
        # 4: znacznik YouTracka w każdym stanie, z podpowiedzią słowami.
        glyphs = {i: [e["text"] for e in self.named(i, "cardYouTrackSync")] for i in ("K-13", "K-12", "K-16", "K-7", "K-17", "K-19")}
        self.assertEqual(glyphs, {"K-13": ["✓"], "K-12": ["⟳"], "K-16": ["⟳"], "K-7": ["⚠"], "K-17": ["⚠"], "K-19": []})
        chips = {i: self.named(i, "cardYouTrack")[0] for i in glyphs}
        for card_id, chip in chips.items():
            self.assertTrue(chip["an"].startswith("YouTrack "), (card_id, chip))
        self.assertIn("zsynchronizowane", chips["K-13"]["an"])
        self.assertIn("zmieniony ręcznie", chips["K-7"]["an"])
        self.assertEqual(chips["K-7"]["tone"], "rose", "konflikt: róża")
        self.assertEqual(chips["K-13"]["tone"], "neutral", "bez konfliktu: tło neutralne")
        self.assertEqual(self.named("K-7", "cardYouTrackSync")[0]["tone"], "rose")
        self.assertEqual(self.named("K-17", "cardYouTrackSync")[0]["tone"], "butter")
        # 5: tytuł do 4 linii, tagi z kropkami w kolorze tagu.
        self.assertEqual(self.root("K-20")["titleMaxLines"], 4)
        self.assertEqual(self.root("K-4")["titleMaxLines"], 3)
        dots = {i: {e["text"]: e["tone"] for e in self.items(i) if e.get("kind") == "tag"}
                for i in ("K-12", "K-15", "K-13")}
        self.assertEqual(dots["K-12"], {"pilne": "rose", "własne": "lavender"})
        self.assertEqual(dots["K-15"], {"pomysł": "butter", "własne": "lavender"})
        self.assertEqual(dots["K-13"], {"Firma": "sky"}, "tag projektu też z kropką w kolorze tagu")
        # 6: znacznik fali.
        self.assertEqual([e["text"] for e in self.named("K-5", "cardTileWaveLabel")], ["fala 2 z 3"])
        self.assertEqual([e["text"] for e in self.named("K-8", "cardTileWaveLabel")], ["fala 1 z 1"])
        self.assertEqual([e["text"] for e in self.named("K-21", "cardTileWaveLabel")], ["fala 1 z 1"])
        # 7: pasek ticketów — każda faza, przerwa między falami, podpowiedź „ID · faza: dlaczego”.
        segments = self.named("K-5", "ticketSegment")
        self.assertEqual([s["phase"] for s in segments],
                         ["gotowe", "review", "pracuje", "kolejka", "ty", "todo", "odrzucone"])
        self.assertEqual([s["gapBefore"] for s in segments], [False, False, True, False, True, False, False])
        hints = [e["tip"] for e in self.items("K-5") if e["tip"].startswith("PT-")]
        self.assertIn("PT-3 · pracuje: implementer", hints)
        self.assertIn("PT-5 · czeka na Ciebie: pytanie o zakres", hints)
        self.assertEqual([s["phase"] for s in self.named("K-21", "ticketSegment")], ["pracuje", "gotowe", "ty"],
                         "starsza usługa bez `phase`: dawne stany")
        # 8: postęp koordynatora.
        for card_id, text in (("K-5", "Claude · koordynator · 3 z 10"), ("K-6", "Codex · koordynator · 8 z 13"),
                              ("K-8", "Claude · koordynator · 5 z 9"), ("K-7", "Claude · koordynator · 4 z 4")):
            self.assertIn(text, self.texts(card_id), card_id)
            self.assertEqual([e["ratio"] for e in self.items(card_id) if e.get("kind") == "progressBar"],
                             [round(int(text.split()[-3]) / int(text.split()[-1]), 3)], card_id)
        # 9: notatka każdego rodzaju, w kolorze `noteTint`.
        notes = {"K-5": ("PT-3: okno tablicy w trakcie", "neutral"),
                 "K-8": ("TOM-146: czy warunek ma działać też na archiwalnych logach?", "peach"),
                 "K-22": ("✓ specyfikacja gotowa", "mint"),
                 "K-9": ("przeniesiona, agent jeszcze nie ruszył", "neutral"),
                 "K-7": ("✓ review OK · 4 commity, gałąź niescalona", "mint")}
        for card_id, (text, tone) in notes.items():
            found = [e for e in self.items(card_id) if e.get("text") == text]
            self.assertEqual([e["tone"] for e in found], [tone], card_id)
        # 10, 11: Do sprawdzenia: gałąź w lawendzie i dwa przyciski.
        branch = [e for e in self.items("K-7") if e.get("text") == "hotfix/tom-142-unique"]
        self.assertEqual([e["tone"] for e in branch], ["lavender"])
        self.assertEqual([e["text"] for e in self.named("K-7", "cardTileDiff")], ["Zmiany gałęzi"])
        self.assertIn("Gotowe", self.texts("K-7"))
        # 12: pozostałe przyciski i linki.
        self.assertIn("Uruchom agenta…", self.texts("K-9"))
        self.assertIn("Uruchom ponownie…", self.texts("K-11"))
        self.assertEqual([e["text"] for e in self.named("K-8", "agentButton")], ["Przejdź do agenta"])
        self.assertEqual([e["tones"] for e in self.named("K-8", "agentButton")], [["peach"]])
        self.assertEqual([e["text"] for e in self.named("K-5", "agentLink")], ["Przejdź do agenta"])
        self.assertEqual([e["text"] for e in self.named("K-5", "sessionsLink")], ["Otwórz w Sesjach →"])
        self.assertEqual([e["text"] for e in self.named("K-8", "sessionsLink")], ["Otwórz w Sesjach →"])
        self.assertEqual(self.named("K-8", "agentLink"), [], "w Czeka na mnie jest przycisk, nie link")
        # 13: stany.
        self.assertTrue(self.root("K-8")["waiting"])
        self.assertEqual(self.root("K-8")["border"], "peach")
        states = self.raw["states"]
        self.assertTrue(states["selected"]["root"]["selected"])
        self.assertTrue(states["focused"]["root"]["activeFocus"])
        self.assertTrue(states["hoverChangesFill"] and states["hoverReleased"])
        self.assertTrue(states["dragging"] and states["dragged"])
        self.assertAlmostEqual(states["draggedOpacity"], 0.35)
        self.assertTrue(states["ghost"]["root"]["ghost"])

    def data_card(self, card_id: str) -> dict:
        return next(c for c in self.data["cards"] if c["id"] == card_id)

    # --- a11y: tile, przyciski i znaczniki ------------------------------------------------------

    def test_screen_reader_description_of_every_tile_and_its_controls(self):
        for card_id in self.raw["ids"]:
            card = self.data_card(card_id)
            root = self.root(card_id)
            self.assertEqual(root["an"], f"{card_id} {card['title']}", card_id)
            self.assertEqual(root["ar"], "button", card_id)
        k7 = self.root("K-7")
        self.assertIn("plasma-tide", self.root("K-5")["ad"])
        self.assertIn("pilne", k7["ad"])
        self.assertIn("✓ review OK", k7["ad"])
        names = {e["text"]: (e["an"], e["ad"]) for e in self.items("K-7") if e.get("kind") == "button"}
        self.assertEqual(names["Gotowe"], ("Przenieś kartę do Gotowych", ""))
        self.assertEqual(names["Zmiany gałęzi"], ("Zmiany gałęzi", ""))
        self.assertEqual([(e["an"], e["ar"]) for e in self.named("K-5", "agentLink")], [("Przejdź do agenta", "link")])
        self.assertEqual([(e["an"], e["ar"]) for e in self.named("K-5", "sessionsLink")],
                         [("Otwórz w Sesjach →", "link")])
        self.assertEqual([(e["an"], e["ar"]) for e in self.named("K-8", "agentButton")],
                         [("Przejdź do agenta", "button")])

    def test_ghost_has_no_buttons_and_waiting_tile_has_peach_border(self):
        ghost = self.raw["states"]["ghost"]
        self.assertEqual([e for e in ghost["items"] if e.get("kind") == "button"], [], "duch bez przycisków")
        self.assertTrue(any(e.get("text") == "K-11" for e in ghost["items"]))

    # --- kontrast tekstu na kafelku -------------------------------------------------------------

    def test_every_text_on_the_tile_has_contrast_of_at_least_4_5(self):
        with open(os.path.join(REPO, "theme", "palette", "mist.json"), encoding="utf-8") as fh:
            view = rgba(json.load(fh)["background"]["view"])
        checked = 0
        for card_id, rows in self.raw["contrast"].items():
            for row in rows:
                if len(row["text"].strip()) <= 1:
                    continue  # znaczki („●”, „✓”) są dekoracją przy tekście; ich sens czyta się ze słów obok
                background = view
                for layer in row["layers"]:
                    background = over(rgba(layer), background)
                ratio = contrast_ratio(rgba(row["fg"]), background)
                self.assertGreaterEqual(ratio, 4.5, f"{card_id}: „{row['text']}” {row['fg']} na {row['layers']}: {ratio:.2f}")
                checked += 1
        self.assertGreater(checked, 100,"sprawdzone teksty wszystkich kart, przycisków i linków")
        names = {row["text"] for rows in self.raw["contrast"].values() for row in rows}
        self.assertTrue({"Gotowe", "Uruchom agenta…", "Przejdź do agenta", "Zmiany gałęzi"} <= names)

    # --- ta sama lista przed i po zmianie wyglądu -----------------------------------------------

    def test_inventory_matches_the_recorded_baseline(self):
        current = self.inventory()
        if not os.path.exists(GOLDEN):
            os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
            with open(GOLDEN, "w", encoding="utf-8") as fh:
                json.dump(current, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.write("\n")
            self.fail(f"zapisano wzorzec inwentarza {GOLDEN}; uruchom test ponownie")
        with open(GOLDEN, encoding="utf-8") as fh:
            golden = json.load(fh)
        self.assertEqual(sorted(current["cards"]), sorted(golden["cards"]), "te same karty")
        for card_id in golden["cards"]:
            self.assertEqual(current["cards"][card_id], golden["cards"][card_id], f"elementy kafelka {card_id}")
            self.assertEqual(current["roots"][card_id], golden["roots"][card_id], f"stan i dostępność kafelka {card_id}")
        self.assertEqual(current["states"], golden["states"], "stany kafelka")


if __name__ == "__main__":
    unittest.main()


# --- Tide public copy: skipped tests (fix-tests.py) ---
import unittest as _pub_unittest


def _pub_skip(reason, targets):
    g = globals()
    for name in targets:
        cls, _, meth = name.partition('.')
        klass = g.get(cls)
        if klass is None:
            continue
        if not meth:
            g[cls] = _pub_unittest.skip(reason)(klass)
        elif hasattr(klass, meth):
            setattr(klass, meth, _pub_unittest.skip(reason)(getattr(klass, meth)))


_pub_skip('public copy: test data changed by anonymization (names, addresses, checksums)', [
    'CardInventoryTest.test_inventory_matches_the_recorded_baseline',
])
# --- end of public copy block ---
