"""Raport poranny z syntetycznego świata (reportfix.py): repozytoria git, dzienniki Claude Code
i Codexa, zaplanowane zadanie, Limity AI — przy zamrożonym zegarze i czasie Europe/Warsaw.

Sprawdzamy model (projekty, commity po gałęziach, agent przy commicie, sesje i ich ostatnie
wiadomości, przebiegi zadań, worktrees), pliki (jeden HTML na projekt z ruchem, podmiana całego
katalogu, uprawnienia) i escapowanie wszystkiego, co przychodzi z gita i z dzienników."""
import base64
import hashlib
import http.server
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import unittest

from . import reportfix
from .reportfix import INJECTION, NOW, START
from tide_daemon import reports
from tide_daemon.report_monitor import notification_text
from tide_daemon.reports import collect, limits, render, sessions
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


class ReportWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-report-")
        cls.p = reportfix.build(cls.tmp)
        cls.palette = reports.load_palette(palette_candidates())
        cls.index, cls.report = cls.generate()
        cls.dir = os.path.dirname(cls.index)
        cls.pages = {}
        for name in os.listdir(cls.dir):
            with open(os.path.join(cls.dir, name), encoding="utf-8") as fh:
                cls.pages[name] = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def generate(cls, start=START, end=NOW, limits_result=None):
        return reports.generate(cls.p["reports"], reportfix.DAY, start, end, palette=cls.palette,
                                workspace=cls.p["work"], claude_root=cls.p["claude"], codex_root=cls.p["codex"],
                                live=reportfix.live_sessions(cls.p),
                                limits_result=limits_result or {"ok": True, "status": reportfix.STATUS},
                                home=cls.p["home"])

    def project(self, name):
        return next(p for p in self.report.projects if p.name == name)


class ModelTest(ReportWorld):
    def test_projects_with_activity_and_quiet_ones(self):
        self.assertEqual([p.name for p in self.report.active], ["alpha", "beta"], "czekający projekt pierwszy")
        self.assertEqual([p.name for p in self.report.quiet], ["gamma"])
        self.assertEqual(len(self.project("gamma").to_clean), 1)

    def test_commits_per_branch_only_from_period(self):
        alpha = self.project("alpha")
        branches = {b.name: [c.subject for c in b.commits] for b in alpha.branches}
        self.assertEqual(branches, {
            "master": ["feat: agent helper", f'fix: handle {INJECTION} & "quotes"'],
            "feat/search": ["test(search): cover the index", "feat(search): index builder"],
        })
        self.assertEqual([b.name for b in alpha.branches], ["master", "feat/search"], "gałąź główna pierwsza")
        self.assertTrue(alpha.branches[0].is_main)
        self.assertEqual(alpha.branches[1].worktree, self.p["search"])
        commit = alpha.branches[1].commits[0]
        self.assertEqual((commit.files, commit.added, commit.deleted, commit.author), (1, 2, 0, "Tomek"))

    def test_agent_detection(self):
        alpha, beta = self.project("alpha"), self.project("beta")
        master = {c.subject: (c.agent, c.agent_why) for c in alpha.branches[0].commits}
        self.assertEqual(master["feat: agent helper"], ("claude", "trailer"))
        self.assertEqual(master[f'fix: handle {INJECTION} & "quotes"'], ("", ""), "bez śladu agenta — bez znacznika")
        self.assertEqual({(c.agent, c.agent_why) for c in alpha.branches[1].commits}, {("claude", "session")})
        upload = beta.branches[0]
        self.assertEqual((upload.name, upload.tool), ("codex/fix-upload", "codex"))
        self.assertEqual((upload.commits[0].agent, upload.commits[0].agent_why), ("codex", "branch"))

    def test_sessions_states_and_final_messages(self):
        alpha = self.project("alpha")
        by_id = {s.id: s for s in alpha.sessions}
        self.assertEqual(set(by_id), {"claude:c-ask", "claude:c-search"}, "sesja sprzed okresu pominięta")
        ask, search = by_id["claude:c-ask"], by_id["claude:c-search"]
        self.assertEqual((ask.state, ask.reason, ask.title, ask.live), ("waiting", "question", "Release plan", True))
        self.assertEqual(search.state, "done", "bezczynna po skończonej turze to w raporcie „skończył”")
        self.assertEqual(search.title, "Search <b>index</b>")
        self.assertTrue(search.final_message.startswith("Gotowe: indeks wyszukiwania działa."))
        self.assertIn(INJECTION, search.final_message)
        self.assertEqual(search.worktree, "search")
        self.assertEqual(search.first_ts, NOW - 6 * 3600)

    def test_scheduled_codex_runs(self):
        beta = self.project("beta")
        ids = {s.session_id for s in beta.sessions}
        self.assertEqual(ids, {reportfix.THREAD, reportfix.CODEX_WT_THREAD}, "wątek podagenta pominięty")
        thread = next(s for s in beta.sessions if s.session_id == reportfix.THREAD)
        self.assertEqual(thread.title, "Nocny przegląd buildu")
        self.assertEqual(thread.automation["name"], "Nocny przegląd")
        self.assertEqual([r.result for r in thread.runs],
                         ["Wszystko zielone (1)", "Wszystko zielone (2)", f"Znalazłem błąd {INJECTION} w buildzie"],
                         "tylko przebiegi z okresu i tylko z automatyzacji")
        self.assertTrue(all(r.ended - r.started == 60 for r in thread.runs))
        self.assertEqual(thread.final_message, f"Znalazłem błąd {INJECTION} w buildzie")
        self.assertEqual(beta.runs, 3)

    def test_worktrees_and_main_checkout(self):
        alpha = self.project("alpha")
        self.assertEqual([w["branch"] for w in alpha.to_clean], ["feat/old"])
        self.assertEqual([(w["branch"], w["unpushed"]) for w in alpha.unpushed], [("feat/search", 2)])
        self.assertEqual(sorted(alpha.main_dirty), [("??", "<b>evil.txt"), ("M", "README.md")])
        self.assertEqual(alpha.main_dirty_count, 2)

    def test_waiting_list_and_notification_text(self):
        self.assertEqual([(p.name, s.id) for p, s in self.report.waiting], [("alpha", "claude:c-ask")])
        summary, body = notification_text(self.report)
        self.assertEqual(summary, "Raport poranny gotowy — 2 projekty")
        self.assertEqual(body, "5 commitów · 4 sesje agentów · 1 czeka na ciebie")

    def test_limits_snapshot_and_forecast_hook(self):
        snap = self.report.limits
        self.assertTrue(snap["ok"])
        self.assertEqual([(s["name"], [m["remaining"] for m in s["metrics"]]) for s in snap["services"]],
                         [("GPT", [91.0]), ("Claude", [12.0, 34.0])])
        self.assertIsNone(self.report.forecast)


class FilesTest(ReportWorld):
    def test_one_page_per_active_project(self):
        self.assertEqual(sorted(self.pages), ["alpha.html", "beta.html", "index.html"])
        self.assertEqual(os.path.dirname(self.dir), self.p["reports"])
        self.assertEqual(os.path.basename(self.dir), reportfix.DAY)

    def test_private_permissions(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        for name in self.pages:
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, name)).st_mode), 0o600, name)

    def test_index_content(self):
        index = self.pages["index.html"]
        self.assertIn("Poniedziałek, 26 października 2026", index)
        self.assertIn("niedz. 25 paź 07:00 – pon. 26 paź 07:00", index)
        self.assertIn('href="alpha.html#s-claude-c-ask"', index)
        self.assertIn('href="beta.html"', index)
        self.assertIn("Pokaż projekty bez ruchu (1)", index)
        self.assertRegex(index, r"<span>gamma</span><span>1 do sprzątnięcia</span>")
        self.assertIn('class="fill lvl-rose" style="width:12%"', index)
        self.assertIn('class="fill lvl-mint" style="width:91%"', index)
        self.assertNotIn("gamma.html", index)

    def test_project_page_content(self):
        alpha, beta = self.pages["alpha.html"], self.pages["beta.html"]
        self.assertIn("feat(search): index builder", alpha)
        self.assertNotIn("commit before the period", alpha)
        self.assertIn("agent: Claude", alpha)
        self.assertIn("Gotowe: indeks wyszukiwania działa.", alpha)
        self.assertIn("czeka na odpowiedź", alpha)
        self.assertIn("Nocny przegląd · co 4 h · 3 przebiegi w okresie", beta)
        self.assertIn("Wszystko zielone (2)", beta)
        self.assertIn("agent: Codex", beta)
        self.assertIn("worktree beta", beta)
        # Polecenia do skopiowania są cytowane dla powłoki i escapowane w atrybucie.
        self.assertIn(f'data-copy="konsole --workdir {self.p["alpha"]}"', alpha)
        self.assertIn(f"cd {self.p['search']} &amp;&amp; claude --resume c-search", alpha)

    def test_everything_from_outside_is_escaped(self):
        for name, text in self.pages.items():
            self.assertNotIn(INJECTION, text, name)
            self.assertNotIn("<b>index</b>", text, name)
            self.assertNotIn("<b>evil", text, name)
            self.assertEqual(len(re.findall(r"<script\b", text)), 1, f"{name}: tylko własny skrypt")
        alpha = self.pages["alpha.html"]
        self.assertIn("fix: handle &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;quotes&quot;", alpha)
        self.assertIn("Search &lt;b&gt;index&lt;/b&gt;", alpha)
        self.assertIn("&lt;b&gt;evil.txt", alpha)

    def test_csp_allows_only_own_script(self):
        for name, text in self.pages.items():
            script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
            digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
            self.assertIn(f"script-src &#x27;sha256-{digest}&#x27;", text, name)
            self.assertIn("default-src &#x27;none&#x27;", text, name)

    def test_offline_and_fonts_from_system(self):
        for name, text in self.pages.items():
            self.assertNotRegex(text, r"(?:src|href)=\"https?://", name)
            self.assertNotIn("@import", text)
            self.assertIn('local("Geist SemiBold")', text)
            self.assertIn('local("Geist Mono")', text)


class RegenerateTest(ReportWorld):
    def test_same_day_regeneration_replaces_whole_directory(self):
        index, report = self.generate(start=NOW - 30 * 60)
        self.assertEqual(index, self.index)
        self.assertEqual([p.name for p in report.active], ["alpha"])
        self.assertEqual(sorted(os.listdir(os.path.dirname(index))), ["alpha.html", "index.html"],
                         "strona projektu bez ruchu znika")
        self.assertEqual([n for n in os.listdir(self.p["reports"]) if n.startswith(".")], [], "bez katalogów roboczych")

    def test_limits_unavailable(self):
        report = collect.build(reportfix.DAY, START, NOW, workspace=self.p["work"], claude_root=self.p["claude"],
                               codex_root=self.p["codex"], live=[], worktrees=[],
                               limits_result={"ok": False, "error": "usługa Limity AI nie odpowiada"})
        page = render.render_index(report, self.palette)
        self.assertIn("usługa Limity AI nie odpowiada", page)


class UnitTest(unittest.TestCase):
    def test_slugs_are_safe_and_unique(self):
        projects = [collect.Project(key=str(i), name=name, path="/") for i, name in
                    enumerate(["Index", "a/b", "..", "a b", "A_B", "Łódź", "index"])]
        collect.assign_slugs(projects)
        slugs = [p.slug for p in projects]
        self.assertEqual(len(set(slugs)), len(slugs))
        for slug in slugs:
            self.assertRegex(slug, r"^[a-z0-9][a-z0-9._-]*$")
            self.assertNotEqual(slug, "index")

    def test_polish_plurals_and_durations(self):
        self.assertEqual([render.count(n, "projekt", "projekty", "projektów") for n in (1, 2, 5, 12, 22, 25)],
                         ["1 projekt", "2 projekty", "5 projektów", "12 projektów", "22 projekty", "25 projektów"])
        self.assertEqual([render.duration(s) for s in (30, 600, 4800, 7200, 90000)],
                         ["30 s", "10 min", "1 h 20 min", "2 h", "1 dzień 1 h"])
        self.assertEqual(render.rrule_text("RRULE:FREQ=MINUTELY;INTERVAL=30"), "co 30 min")

    def test_rich_message_escapes_first(self):
        self.assertEqual(render.rich('**gotowe** w `make test` <img src=x onerror="a()">'),
                         '<b>gotowe</b> w <code>make test</code> &lt;img src=x onerror=&quot;a()&quot;&gt;')
        self.assertEqual(render.rich("**<script>x</script>**"), "<b>&lt;script&gt;x&lt;/script&gt;</b>")
        self.assertEqual(render.rich("`<b>`"), "<code>&lt;b&gt;</code>")

    def test_excerpt_cuts_at_paragraph(self):
        first = "Pierwszy akapit z opisem tego, co agent zrobił w repozytorium."
        shown, cut = render.excerpt(first + "\n\n" + "słowo " * 200, 100)
        self.assertTrue(cut)
        self.assertEqual(shown, first + " …")
        shown, _ = render.excerpt("krótki wstęp\n\n" + "słowo " * 200, 100)
        self.assertTrue(shown.endswith("słowo …"), "akapit za krótki — cięcie na granicy słowa")
        self.assertEqual(render.excerpt("krótko"), ("krótko", False))

    def test_seek_since_skips_old_part_of_big_log(self):
        tmp = tempfile.mkdtemp(prefix="tide-report-seek-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "log.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for n in range(20000):
                fh.write(json.dumps({"timestamp": f"2026-10-25T{n // 3600 % 24:02d}:{n // 60 % 60:02d}:{n % 60:02d}Z",
                                     "type": "x", "payload": {"pad": "y" * 50}}) + "\n")
        with open(path, "rb") as fh:
            offset = sessions.seek_since(fh, reportfix.parse_ts("2026-10-25T05:00:00Z"))
            self.assertGreater(offset, 0)
            fh.seek(offset)
            fh.readline()
            first = json.loads(fh.readline())
        self.assertLessEqual(reportfix.parse_ts(first["timestamp"]), reportfix.parse_ts("2026-10-25T05:00:00Z"),
                             "start nieco wcześniej niż szukany czas — nic nie ginie")


class LimitsFetchTest(unittest.TestCase):
    """Odczyt z lokalnego API QuotaApp: token tylko w nagłówku, nigdy w komunikacie błędu."""

    def serve(self, code=200, body=None):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append((self.path, self.headers.get("X-Limity-Token")))
                payload = json.dumps(body if body is not None else reportfix.STATUS).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1], seen

    def config(self, port, token="sekret-123"):
        tmp = tempfile.mkdtemp(prefix="tide-limits-")
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"port": port, "token": token}, fh)
        return {"LIMITY_CONFIG": path}

    def test_reads_status_with_token_header(self):
        port, seen = self.serve()
        result = limits.fetch_status(self.config(port))
        self.assertTrue(result["ok"])
        self.assertEqual(seen, [("/status", "sekret-123")])
        self.assertEqual(limits.snapshot(result)["services"][1]["metrics"][0]["remaining"], 12.0)

    def test_errors_never_contain_token(self):
        port, _seen = self.serve(code=403, body={"error": "x"})
        for result in (limits.fetch_status(self.config(port)), limits.fetch_status(self.config(1)),
                       limits.fetch_status({"LIMITY_CONFIG": "/nonexistent/config.json"})):
            self.assertFalse(result["ok"])
            self.assertNotIn("sekret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
