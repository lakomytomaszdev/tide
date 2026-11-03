"""Warianty palety: Mist i nakładki na nią (Streamer) wczytywane przez theme.palette.load."""
import itertools
import json
import math
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from theme import palette  # noqa: E402

MIST_FILE = ROOT / "theme/palette/mist.json"
# Kolory marki z brand/assets/brand/STYLE.md w repozytorium streamer.
BRAND = {"night": "#102E38", "forest": "#0B4C43", "gold": "#C5A463",
         "ivory": "#F1E9D3", "crimson": "#A52E29", "graphite": "#181D22"}
COLOR_GROUPS = ("background", "text", "pastel", "semantic", "windowButtons")


def key_paths(data, prefix=""):
    """Ścieżki kluczy słowników, np. {"radius.small", "wallpaper.blobs"}; listy to liście."""
    paths = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        paths.add(path)
        if isinstance(value, dict):
            paths |= key_paths(value, path + ".")
    return paths


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def lab(color: str) -> tuple[float, float, float]:
    """CIE L*a*b* (D65) koloru sRGB."""
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    xyz = (0.4124 * r + 0.3576 * g + 0.1805 * b, 0.2126 * r + 0.7152 * g + 0.0722 * b,
           0.0193 * r + 0.1192 * g + 0.9505 * b)
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
         for t in (xyz[0] / 0.95047, xyz[1], xyz[2] / 1.08883)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class LoaderTest(unittest.TestCase):
    def test_mist_loads_exactly_as_its_file(self):
        # Mist nie ma bazy: wczytanie to zwykły json.loads, więc generatory dostają to samo co dotąd.
        self.assertEqual(palette.load(MIST_FILE), json.loads(MIST_FILE.read_text()))
        self.assertEqual(palette.load("mist"), json.loads(MIST_FILE.read_text()))

    def test_names_and_paths(self):
        self.assertEqual(palette.names(), ["streamer", "mist"])
        self.assertEqual(palette.path("streamer"), ROOT / "theme/palette/streamer.json")
        with self.assertRaises(palette.PaletteError):
            palette.path("nie-ma")

    def test_overlay_merges_dicts_and_replaces_lists_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            base.write_text(json.dumps({"name": "Base", "a": {"x": 1, "y": [1, 2]}, "b": 3}))
            child = Path(tmp) / "child.json"
            child.write_text(json.dumps({"base": "base", "name": "Child", "a": {"y": [9]}, "c": 4}))
            self.assertEqual(palette.load(child), {"name": "Child", "a": {"x": 1, "y": [9]}, "b": 3, "c": 4})

    def test_base_cycle_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text(json.dumps({"base": "b"}))
            (Path(tmp) / "b.json").write_text(json.dumps({"base": "a"}))
            with self.assertRaises(palette.PaletteError):
                palette.load(Path(tmp) / "a.json")


class StreamerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mist = palette.load("mist")
        cls.k = palette.load("streamer")

    def test_has_every_key_of_mist(self):
        # Generatory i widżety czytają te same klucze, więc działają z obiema paletami bez zmian.
        self.assertEqual(key_paths(self.mist) - key_paths(self.k), set())

    def test_color_groups_have_the_same_keys(self):
        for group in COLOR_GROUPS:
            with self.subTest(group=group):
                self.assertEqual(set(self.k[group]), set(self.mist[group]))

    def test_extra_keys_are_only_variant_specific(self):
        extra = {p.split(".")[0] + ("." + p.split(".")[1] if p.startswith(("wallpaper.", "lockscreen.")) else "")
                 for p in key_paths(self.k) - key_paths(self.mist)}
        # lockscreen.agents: ekran blokady Stream bez linii agentów (decyzja 80).
        self.assertLessEqual(extra, {"brand", "wallpaper.map", "wallpaper.pictures", "lockscreen.agents"})

    def test_shapes_and_timings_come_from_mist(self):
        for group in ("radius", "size", "opacity", "shadow", "fonts", "limits", "splash", "plymouth", "terminal"):
            with self.subTest(group=group):
                self.assertEqual(self.k[group], self.mist[group])

    def test_names_give_the_kde_ids(self):
        self.assertEqual(self.k["name"], "Streamer")

    def test_brand_colors_in_their_roles(self):
        self.assertEqual({k: v.upper() for k, v in self.k["brand"].items()}, BRAND)
        self.assertEqual(self.k["background"]["window"].upper(), BRAND["night"])
        self.assertEqual(self.k["text"]["primary"].upper(), BRAND["ivory"])
        self.assertEqual(self.k["accent"].upper(), BRAND["gold"])

    def test_crimson_is_never_a_background_or_text(self):
        # STYLE.md: czerwień płaszcza nie jako dominujące tło ani drobny tekst.
        used = [*self.k["background"].values(), *self.k["text"].values(), *self.k["pastel"].values(),
                self.k["accent"]]
        self.assertNotIn(BRAND["crimson"].lower(), [c.lower() for c in used])

    def test_primary_text_is_readable_on_every_background(self):
        for name, background in self.k["background"].items():
            with self.subTest(background=name):
                self.assertGreaterEqual(contrast(self.k["text"]["primary"], background), 4.5)

    def test_status_pastels_are_readable_on_cards(self):
        # Pastele są kolorem tekstu stanów (np. „czeka”) na kartach i w popupach (karta to najjaśniejsze tło).
        for name, color in self.k["pastel"].items():
            with self.subTest(pastel=name):
                self.assertGreaterEqual(contrast(color, self.k["background"]["card"]), 4.5)

    def test_semantic_roles_are_told_apart(self):
        # Stany (pracuje, czeka, gotowe, błąd…) rozróżniamy kolorem kropki i tekstu: każda para ról
        # o różnym znaczeniu ΔE76 ≥ 20, a „negatywne” dodatkowo jaśnością (ΔL* ≥ 8) — także dla
        # osób słabiej rozróżniających barwy. runout to „czeka” w Limitach AI (ten sam pastel).
        semantic = self.k["semantic"]
        roles = ("positive", "neutral", "negative", "waiting", "working", "done")
        colors = {role: lab(self.k["pastel"][semantic[role]]) for role in roles}
        for a, b in itertools.combinations(roles, 2):
            with self.subTest(pair=(a, b)):
                self.assertGreaterEqual(math.dist(colors[a], colors[b]), 20)
                if "negative" in (a, b):
                    self.assertGreaterEqual(abs(colors[a][0] - colors[b][0]), 8)

    def test_selection_text_is_readable_on_the_accent(self):
        # Schemat kolorów: zaznaczenie ma tło akcentu i tekst w kolorze pulpitu.
        self.assertGreaterEqual(contrast(self.k["background"]["desktop"], self.k["accent"]), 4.5)

    def test_backgrounds_keep_the_mist_order(self):
        # Pulpit i widoki najciemniejsze, karta najjaśniejsza — jak w Mist.
        order = ("desktop", "window", "titlebar", "panel", "card")
        values = [luminance(self.k["background"][n]) for n in order]
        self.assertEqual(values, sorted(values))
        self.assertLess(luminance(self.k["background"]["view"]), luminance(self.k["background"]["window"]))


if __name__ == "__main__":
    unittest.main()
