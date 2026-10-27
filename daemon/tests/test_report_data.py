"""Dane raportu dla widoku „Raporty” okna Tide (reports/data.py) i nowe wejścia ReportMonitor.

- report.json z syntetycznego świata (reportfix.py): kolejność kart jak w indeksie, „Na daily”,
  liczniki, teksty „Kopiuj jako tekst” z tego samego generatora co strona, napisy bez escapowania
  (widok pokazuje je jako zwykły tekst), plik 0600,
- kolejność kart: czekające pierwsze, same porządki na końcu (syntetyczne „Na daily”),
- lista dni (nowe pierwsze, raport sprzed report.json, zepsuty plik, dowiązania, złe nazwy),
- Day(): brak doby, złe nazwy, legacy, najnowszy i nieprzeczytany,
- ścieżki stron do otwarcia (index, slug, „index”, ../, dowiązanie),
- ReportMonitor: mark_read i open_day (najnowsza doba gasi kropkę, starsza — nie)."""
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from types import SimpleNamespace

from . import reportfix
from .reportfix import INJECTION, NOW, START
from tide_daemon import reports
from tide_daemon.report_monitor import ReportMonitor
from tide_daemon.reports import data, render, schedule
from tide_daemon.reports.schedule import State
from tide_daemon.tabmarks import palette_candidates

_OLD_TZ = None


def setUpModule():
    global _OLD_TZ
    _OLD_TZ = os.environ.get("TZ")
    os.environ["TZ"] = reportfix.TZ
    time.tzset()


def tearDownModule():
    if _OLD_TZ is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _OLD_TZ
    time.tzset()


def write_day(root: str, day: str, pages=(), report=None, mtime=None) -> str:
    folder = os.path.join(root, day)
    os.makedirs(folder, exist_ok=True)
    for name in ("index",) + tuple(pages):
        with open(os.path.join(folder, f"{name}.html"), "w", encoding="utf-8") as fh:
            fh.write("<html></html>")
    if report is not None:
        with open(os.path.join(folder, data.NAME), "w", encoding="utf-8") as fh:
            fh.write(report if isinstance(report, str) else json.dumps(report))
    if mtime:
        os.utime(os.path.join(folder, "index.html"), (mtime, mtime))
    return folder


class GeneratedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-report-data-")
        cls.p = reportfix.build(cls.tmp)
        palette = reports.load_palette(palette_candidates())
        cls.index, cls.report = reports.generate(
            cls.p["reports"], reportfix.DAY, START, NOW, palette=palette, workspace=cls.p["work"],
            claude_root=cls.p["claude"], codex_root=cls.p["codex"], live=reportfix.live_sessions(cls.p),
            limits_result={"ok": True, "status": reportfix.STATUS}, home=cls.p["home"])
        cls.path = os.path.join(os.path.dirname(cls.index), data.NAME)
        with open(cls.path, encoding="utf-8") as fh:
            cls.data = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_file_mode_and_header(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        d = self.data
        self.assertEqual((d["version"], d["day"], d["title"]), (1, reportfix.DAY, "Poniedziałek, 26 października 2026"))
        self.assertEqual((d["generatedAt"], d["start"], d["end"]), (NOW, START, NOW))
        self.assertEqual(d["since"], "Od niedzieli 07:00")
        self.assertEqual(d["period"], render.period_text(self.report))
        self.assertEqual(d["source"], "opis z reguł (bez modelu)")
        self.assertEqual(d["counts"], {"projects": 2, "waiting": 1, "waitingSessions": 1, "commits": 5, "sessions": 4,
                                       "outside": 0, "outsideWaiting": 0, "quiet": 1})

    def test_projects_daily_and_copy_text(self):
        names = [p["name"] for p in self.data["projects"]]
        self.assertEqual(names, ["alpha", "beta"], "projekt, w którym coś czeka, pierwszy")
        alpha = self.data["projects"][0]
        self.assertEqual((alpha["slug"], alpha["page"], alpha["path"]), ("alpha", "alpha.html", self.p["alpha"]))
        self.assertTrue(alpha["hot"])
        self.assertEqual(alpha["counts"], {"done": 1, "doing": 1, "waiting": 1, "next": 0, "attention": 0, "chores": 0})
        self.assertEqual(alpha["waiting"], [{"ref": "", "text": f"Release plan: agent pyta: „{reportfix.ASK_QUESTION}”",
                                             "kind": "blocked", "sessionId": "claude:c-ask",
                                             "since": reportfix.NOW - 30 * 60}])
        self.assertEqual(alpha["doing"][0]["next"], "przejrzeć i scalić")
        self.assertEqual((alpha["commits"], alpha["sessions"], alpha["waitingSessions"], alpha["tools"]),
                         (4, 2, 1, ["claude"]))
        standup = self.report.standup["projects"]["alpha"]
        for key in ("done", "doing", "waiting", "next", "chores"):
            self.assertEqual([i["text"] for i in alpha[key]], [i["text"] for i in standup.get(key) or []], key)
        # Ten sam tekst co przycisk „Kopiuj jako tekst” na stronie projektu i „Kopiuj wszystko” w indeksie.
        self.assertEqual(alpha["copyText"], render.daily_text(self.report, [self.report.repos[0]]))
        self.assertEqual(self.data["copyAll"], render.daily_text(self.report, self.report.repos, brief=True))
        self.assertIn("\nDziś w skrócie:\n- alpha — ", self.data["copyAll"])
        self.assertEqual(self.data["brief"], self.report.standup["brief"])
        self.assertTrue(alpha["copyText"].startswith("Na daily · poniedziałek, 26 października 2026\n\nalpha\n"))

    def test_text_is_raw_not_html(self):
        """Napisy są surowe (widok pokazuje je jako zwykły tekst) — bez encji i bez znaczników HTML raportu."""
        alpha = self.data["projects"][0]
        self.assertIn(INJECTION, alpha["done"][0]["text"])
        self.assertIn("Search <b>index</b>", alpha["doing"][0]["text"])
        raw = json.dumps(self.data, ensure_ascii=False)
        self.assertNotIn("&lt;", raw)
        self.assertNotIn('class="', raw)

    def test_day_and_days_from_generated_report(self):
        state = State(day=reportfix.DAY, opened="")
        listing = data.days(self.p["reports"], state, reportfix.DAY, 30)
        self.assertEqual(listing["days"], [{"day": reportfix.DAY, "generatedAt": NOW, "projects": 2, "waiting": 1,
                                            "data": True}])
        self.assertEqual((listing["latest"], listing["unread"], listing["today"], listing["keepDays"]),
                         (reportfix.DAY, True, reportfix.DAY, 30))
        day = data.day(self.p["reports"], reportfix.DAY, state)
        self.assertEqual((day["ok"], day["latest"], day["unread"], day["legacy"], day["index"]),
                         (True, True, True, False, self.index))
        self.assertEqual(day["projects"], self.data["projects"])
        read = data.day(self.p["reports"], reportfix.DAY, State(opened=reportfix.DAY))
        self.assertFalse(read["unread"])


def project(name, waiting=0):
    return SimpleNamespace(name=name, slug=name.lower(), path=f"/w/{name}", sessions=[], commits=1,
                           waiting=[object()] * waiting, last_ts=NOW, plans=[])


class OrderingTest(unittest.TestCase):
    """Kolejność kart jak w render.overview: czekające (z „Na daily” albo sesji), zwykłe, same porządki."""

    def test_hot_first_chores_last(self):
        repos = [project("chores"), project("plain"), project("asks"), project("session", waiting=1)]
        standup = {"source": "model", "model": "sonnet", "note": "", "projects": {
            "chores": {"chores": [{"ref": "", "text": "Porządek w planie"}]},
            "plain": {"done": [{"ref": "TIDE-1", "text": "Zrobione <i>coś</i>", "about": "po co"}],
                      "headline": "Nagłówek"},
            "asks": {"waiting": [{"ref": "TIDE-2", "text": "Pytanie?"}, {"text": "i 3 więcej", "more": True}],
                     "theme": "Na czym polega"},
            "session": {}}}
        report = SimpleNamespace(day="2026-10-27", generated=NOW, start=NOW - 86400, end=NOW, repos=repos,
                                 active=repos, outside=[], quiet=[], waiting=[(repos[3], object())],
                                 standup=standup)
        out = data.build(report)
        self.assertEqual([p["name"] for p in out["projects"]], ["asks", "session", "plain"],
                         "projekt z samymi porządkami nie jest kartą")
        self.assertEqual([(c["name"], [i["text"] for i in c["chores"]]) for c in out["choreProjects"]],
                         [("chores", ["Porządek w planie"])], "tylko w zwiniętej linii „Porządki”")
        asks = out["projects"][0]
        self.assertEqual(asks["waiting"], [{"ref": "TIDE-2", "text": "Pytanie?"},
                                           {"ref": "", "text": "i 3 więcej", "more": True}])
        self.assertEqual(asks["counts"]["waiting"], 4, "„i 3 więcej” liczy się jako 3")
        self.assertEqual(asks["headline"], "Na czym polega", "bez headline — theme jak w indeksie")
        self.assertEqual(out["counts"]["waiting"], 4)
        self.assertEqual(out["source"], "opis: Claude Sonnet")
        self.assertEqual(out["since"], "Od niedzieli 07:00")

    def test_since_text(self):
        day = 86400
        self.assertEqual(data.since_text(NOW - 2 * 3600, NOW), "Od 05:00")
        self.assertEqual(data.since_text(NOW - day, NOW), "Od niedzieli 07:00")
        # 25.10 zmiana czasu: wcześniejsze doby są w czasie letnim (07:00 CET = 08:00 CEST).
        self.assertEqual(data.since_text(NOW - 6 * day, NOW), "Od wtorku 08:00")
        self.assertEqual(data.since_text(NOW - 7 * day, NOW), "Od 19.10 08:00")
        self.assertEqual(data.since_text(0, NOW), "")


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-days-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")
        os.makedirs(self.root)

    def test_days_newest_first_with_legacy_and_broken(self):
        write_day(self.root, "2026-10-24", pages=("alpha", "beta"), mtime=1000.0)     # sprzed report.json
        write_day(self.root, "2026-10-25", report="{zepsuty", mtime=2000.0)            # zepsuty plik
        write_day(self.root, "2026-10-27", report={"projects": [], "generatedAt": 3000.0,
                                                   "counts": {"projects": 5, "waiting": 2}})
        os.makedirs(os.path.join(self.root, "2026-10-26"))                              # bez index.html
        os.makedirs(os.path.join(self.root, "notatki"))
        os.symlink(os.path.join(self.root, "2026-10-27"), os.path.join(self.root, "2026-10-28"))
        listing = data.days(self.root, State(opened="2026-10-27"), "2026-10-27", 30)
        self.assertEqual([d["day"] for d in listing["days"]], ["2026-10-27", "2026-10-25", "2026-10-24"])
        self.assertEqual(listing["days"][0], {"day": "2026-10-27", "generatedAt": 3000.0, "projects": 5, "waiting": 2,
                                              "data": True})
        self.assertEqual(listing["days"][1], {"day": "2026-10-25", "generatedAt": 2000.0, "projects": 0,
                                              "waiting": None, "data": False})
        self.assertEqual(listing["days"][2]["projects"], 2)
        self.assertEqual((listing["latest"], listing["unread"]), ("2026-10-27", False))
        self.assertEqual(data.days(os.path.join(self.tmp, "brak"), State(), "2026-10-27", 30)["days"], [])
        self.assertFalse(data.days(os.path.join(self.tmp, "brak"), State(), "2026-10-27", 30)["unread"])

    def test_day_missing_invalid_and_legacy(self):
        write_day(self.root, "2026-10-24", pages=("alpha", "beta-2"), mtime=1000.0)
        write_day(self.root, "2026-10-25", report={"projects": [{"name": "x"}], "day": "inna"})
        for bad in ("", "../etc", "2026-13-45", "2026-10-26", "2026-10-24/../2026-10-25", None):
            self.assertEqual(data.day(self.root, bad, State())["ok"], False, bad)
        legacy = data.day(self.root, "2026-10-24", State())
        self.assertEqual((legacy["ok"], legacy["legacy"], legacy["latest"], legacy["unread"], legacy["generatedAt"]),
                         (True, True, False, False, 1000.0))
        self.assertEqual([p["slug"] for p in legacy["projects"]], ["alpha", "beta-2"])
        self.assertEqual(legacy["title"], "Sobota, 24 października 2026")
        fresh = data.day(self.root, "2026-10-25", State(opened="2026-10-24"))
        self.assertEqual((fresh["day"], fresh["latest"], fresh["unread"], fresh["legacy"]),
                         ("2026-10-25", True, True, False), "pole day zawsze z nazwy katalogu")

    def test_page_path(self):
        folder = write_day(self.root, "2026-10-25", pages=("alpha",))
        os.symlink("/etc/passwd", os.path.join(folder, "evil.html"))
        self.assertEqual(data.page_path(self.root, "2026-10-25"), os.path.join(folder, "index.html"))
        self.assertEqual(data.page_path(self.root, "2026-10-25", "alpha"), os.path.join(folder, "alpha.html"))
        for bad in ("index", "../alpha", "Alpha", "brak", "evil", "alpha.html", ".hidden"):
            self.assertEqual(data.page_path(self.root, "2026-10-25", bad), "", bad)
        self.assertEqual(data.page_path(self.root, "2026-10-26", "alpha"), "")


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-monitor-data-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")
        self.state_file = os.path.join(self.tmp, "state", "report-state.json")
        write_day(self.root, "2026-10-26", pages=("alpha",))
        write_day(self.root, "2026-10-27", pages=("alpha",), report={"projects": [], "counts": {"projects": 0}})
        schedule.save_state(self.state_file, State(day="2026-10-27", opened="2026-10-26"))
        self.opened: list[str] = []
        self.monitor = ReportMonitor(
            schedule.Settings(), root=self.root, state_file=self.state_file, palette_paths=[], workspace=self.tmp,
            claude_root=self.tmp, codex_root=self.tmp, clock=lambda: 1793084400.0,  # 27.10 07:00
            opener=lambda path: self.opened.append(path) or True, fetch_limits=False)
        self.addCleanup(self.monitor.stop)
        self.changes = 0
        self.monitor.changed.connect(self.count)

    def count(self):
        self.changes += 1

    def unread(self) -> bool:
        return json.loads(self.monitor.days_json())["unread"]

    def test_days_and_day_json(self):
        listing = json.loads(self.monitor.days_json())
        self.assertEqual((listing["today"], listing["latest"], listing["unread"], listing["keepDays"]),
                         ("2026-10-27", "2026-10-27", True, 30))
        self.assertEqual(json.loads(self.monitor.day_json("2026-10-26"))["legacy"], True)
        self.assertEqual(json.loads(self.monitor.day_json("nie"))["ok"], False)

    def test_mark_read_only_latest_day(self):
        self.assertFalse(self.monitor.mark_read("2026-10-26"), "starsza doba nie gasi kropki")
        self.assertFalse(self.monitor.mark_read("../x"))
        self.assertTrue(self.unread())
        self.assertEqual(self.changes, 0)
        self.assertTrue(self.monitor.mark_read("2026-10-27"))
        self.assertFalse(self.unread())
        self.assertEqual(self.changes, 1, "przeczytanie → changed (widżety pobierają Status())")
        self.assertFalse(self.monitor.mark_read("2026-10-27"), "drugi raz bez zmiany i bez sygnału")
        self.assertEqual(self.changes, 1)
        self.assertEqual(schedule.load_state(self.state_file).opened, "2026-10-27")
        self.assertEqual(self.opened, [], "mark_read niczego nie otwiera")

    def test_open_day_pages(self):
        self.assertTrue(self.monitor.open_day("2026-10-26", "alpha"))
        self.assertEqual(self.opened, [os.path.join(self.root, "2026-10-26", "alpha.html")])
        self.assertTrue(self.unread(), "strona starszego raportu nie gasi kropki przy nowszym")
        self.assertFalse(self.monitor.open_day("2026-10-27", "../2026-10-26/alpha"))
        self.assertFalse(self.monitor.open_day("2026-10-28", ""))
        self.assertEqual(len(self.opened), 1, "zła strona — bez xdg-open")
        self.assertTrue(self.monitor.open_day("2026-10-27", "alpha"))
        self.assertFalse(self.unread(), "strona projektu najnowszego raportu — przeczytany")
        self.assertEqual(self.changes, 1)
        self.assertTrue(self.monitor.open_day("2026-10-27", ""))
        self.assertEqual(self.opened[-1], os.path.join(self.root, "2026-10-27", "index.html"))

    def test_open_path_outside_root_does_not_mark(self):
        other = write_day(os.path.join(self.tmp, "inny"), "2026-10-27")
        self.assertTrue(self.monitor.open_path(os.path.join(other, "index.html")))
        self.assertTrue(self.unread(), "ta sama nazwa doby w innym katalogu nie gasi kropki")


if __name__ == "__main__":
    unittest.main()
