"""„Na daily” w raporcie porannym: fakty z syntetycznego świata (reportfix.py + plik tablicy
koordynacji), podsumowanie z reguł, wejście dla modelu (limity, usuwanie sekretów), walidacja
odpowiedzi (także złośliwej i niepełnej) i wywołanie modelu przez atrapę `claude` (gotowy JSON,
błąd, zawieszenie, śmieci, pamięć podręczna). Prawdziwy `claude` nigdy nie jest wołany: atrapa
jest podawana jawnie przez $TIDE_CLAUDE_BIN w środowisku wywołania."""
import json
import os
import re
import shutil
import stat
import tempfile
import time
import types
import unittest

from . import reportfix
from .reportfix import NOW, START, H
from tide_daemon import reports
from tide_daemon.agents.claude import escape_cwd
from tide_daemon.agents.timeutil import iso
from tide_daemon.coordination import actions
from tide_daemon.reports import plans, render, schedule, standup, summarize
from tide_daemon.tabmarks import palette_candidates

_OLD_TZ = None

FAKE_CLAUDE = r'''#!/usr/bin/python3
"""Atrapa `claude -p`: zapisuje argv i stdin, odpowiada według $FAKE_CLAUDE_MODE."""
import json, os, sys, time
log = os.environ["FAKE_CLAUDE_LOG"]
data = sys.stdin.read()
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "stdin": data, "pid": os.getpid(), "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "fail":
    sys.stderr.write("boom\n")
    sys.exit(3)
if mode == "hang":
    time.sleep(120)
if mode == "garbage":
    print("to nie jest JSON")
    sys.exit(0)
if mode == "error":
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""}))
    sys.exit(0)
with open(os.environ["FAKE_CLAUDE_ANSWER"], encoding="utf-8") as fh:
    answer = fh.read()
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "total_cost_usd": 0.001}))
'''

HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)>'

ANSWER = {"projects": [
    {"name": "alpha", "headline": "Indeks wyszukiwania gotowy, wyszukiwarka w UI czeka na review.",
     "theme": "Wydanie z wyszukiwaniem: indeks i eksport.",
     "done": [{"ref": "ALPHA-1", "text": "Indeks wyszukiwania " + HOSTILE, "about": "Wyniki szybciej."},
              {"ref": "EVIL-666", "text": "Pozycja z wymyślonym ID", "about": ""},
              "goły napis zamiast obiektu",
              {"ref": "ALPHA-1", "text": ""},
              42],
     "doing": [{"ref": "alpha-2", "text": "Wyszukiwanie — implementacja gotowa, trwa review", "next": "poprawki"}],
     # Same śmieci — walidacja je odrzuca, a puste „Czeka na mnie” uzupełniają reguły.
     "waiting": [{"text": "Nie ma teraz decyzji dla Ciebie. Agent napisał, że nie potrzebuje odpowiedzi."},
                 {"ref": "ALPHA-2", "text": "26 gałęzi ALPHA-2 nie jest scalonych."},
                 {"text": "25 gotowych ticketów nie ma jeszcze review."},
                 {"text": "Nie scalone pozostały dwie gałęzie: feat/x oraz worktree-agent-a024."}],
     "next": [{"ref": "ALPHA-4", "text": "Eksport do CSV"}],
     "chores": [{"text": "Plan pracy agentów dopisany do instrukcji " + HOSTILE}]},
    {"name": "gamma-evil", "done": [{"text": "projekt spoza raportu"}]},
    {"name": "beta", "theme": 7, "done": "nie lista"},
]}


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


def coordination_cache(p: dict) -> dict:
    """Plik tablicy koordynacji dla alpha: tickety we wszystkich stanach i jeden sprzed okresu."""
    def ticket(ident, title, state, ts, **extra):
        return dict({"id": ident, "title": title, "state": state, "note": "", "lastActivityTs": ts, "url": "",
                     "branch": "", "review": "", "result": "", "question": ""}, **extra)
    return {"version": actions.CACHE_VERSION, "generated": NOW, "coordinators": [{
        "id": "claude:c-coord", "tool": "claude", "project": "alpha", "repoPath": p["alpha"], "cwd": p["alpha"],
        "title": "Wyszukiwanie w alpha", "label": "ALPHA-0", "state": "working", "live": True,
        "startedTs": NOW - 20 * H, "lastActivityTs": NOW - H, "counts": {}, "progress": {},
        "plan": {"path": "", "title": "Wyszukiwanie w alpha", "tracker": "local", "status": "active", "updated": "",
                 "goal": "Wyszukiwanie w aplikacji: indeks, UI i eksport do CSV."},
        "tracker": {"kind": "local", "available": None, "error": None},
        "question": "ALPHA-3: czy wydajemy w piątek?",
        "tickets": [
            ticket("ALPHA-1", "Indeks wyszukiwania", "gotowe", NOW - 5 * H, note="review OK", review="bez uwag",
                   description="Indeks pełnotekstowy dla wyszukiwarki w aplikacji.",
                   result="Dodałem indeks pełnotekstowy, wyszukiwanie działa bez skanowania plików."),
            ticket("ALPHA-2", "Wyszukiwarka w UI", "w-toku", NOW - 3 * H, note="skończył, czeka na review",
                   branch="feat/search"),
            ticket("ALPHA-3", "Termin wydania", "czeka", NOW - 2 * H,
                   note="czeka na ciebie: „ALPHA-3: czy wydajemy w piątek?”", question="ALPHA-3: czy wydajemy w piątek?"),
            ticket("ALPHA-4", "Eksport do CSV", "todo", 0),
            ticket("ALPHA-5", "Filtry", "w-toku", NOW - 4 * H, note="po review: 1 MAJOR — do poprawy",
                   review="1 MAJOR"),
            ticket("ALPHA-6", "Stary ticket", "gotowe", START - 3 * 86400, note="review OK"),
        ]}]}


class StandupWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-standup-")
        cls.p = p = reportfix.build(cls.tmp)
        alpha = p["alpha"]
        # Gałąź z ID ticketu scalona do master w okresie — jednostka „scalona gałąź”.
        reportfix.git(alpha, "checkout", "-q", "-b", "feat/tide-9-export", ts=NOW - 9 * H)
        reportfix.commit(alpha, "export.py", NOW - 9 * H, "feat(export): csv writer")
        reportfix.commit(alpha, "export_test.py", NOW - 8.5 * H, "test(export): cover csv writer")
        reportfix.git(alpha, "checkout", "-q", "master", ts=NOW - 8 * H)
        reportfix.git(alpha, "merge", "-q", "--no-ff", "-m", "Merge branch 'feat/tide-9-export'",
                      "feat/tide-9-export", ts=NOW - 8 * H)
        # Gałąź porządków: same instrukcje i plan agentów.
        reportfix.git(alpha, "checkout", "-q", "-b", "docs/agent-plan", "master", ts=NOW - 6 * H)
        os.makedirs(os.path.join(alpha, "docs", "agents", "plans"))
        reportfix.commit(alpha, "CLAUDE.md", NOW - 6 * H, "docs(plan): agent work plan for the board")
        reportfix.commit(alpha, "docs/agents/plans/2026-10-26-search.md", NOW - 5.8 * H, "docs: search plan table")
        # Gałąź integracyjna z gałęzią ticketu scaloną do niej (nie do master).
        reportfix.git(alpha, "checkout", "-q", "-b", "feat/int", "master", ts=NOW - 7 * H)
        reportfix.commit(alpha, "int.py", NOW - 7 * H, "feat(int): integration base")
        reportfix.git(alpha, "checkout", "-q", "-b", "feat/tide-12-part", ts=NOW - 6.9 * H)
        reportfix.commit(alpha, "part.py", NOW - 6.8 * H, "feat(part): add the part")
        reportfix.git(alpha, "checkout", "-q", "feat/int", ts=NOW - 6.5 * H)
        reportfix.git(alpha, "merge", "-q", "--no-ff", "-m", "Merge branch 'feat/tide-12-part' into feat/int",
                      "feat/tide-12-part", ts=NOW - 6.5 * H)
        reportfix.git(alpha, "checkout", "-q", "master", ts=NOW - 6.4 * H)
        # Wewnętrzna gałąź podagenta w osobnym worktree — nie jest pracą do opowiedzenia.
        cls.agent_wt = os.path.join(cls.tmp, "agentwt", "agent-abc123")
        reportfix.git(alpha, "worktree", "add", "-q", "-b", "worktree-agent-abc123", cls.agent_wt, ts=NOW - 5 * H)
        reportfix.commit(cls.agent_wt, "agent_tmp.py", NOW - 4.9 * H, "feat: internal agent step")
        # Sesja spoza repozytoriów (katalog bez gita) — „Poza projektami”, bez „Na daily”.
        cls.notes = os.path.join(p["work"], "notes")
        os.makedirs(cls.notes)
        reportfix._write_jsonl(os.path.join(p["claude"], "projects", escape_cwd(cls.notes), "c-notes.jsonl"), [
            reportfix._claude("c-notes", cls.notes, "", NOW - 2 * H, "user",
                              message={"role": "user", "content": "Dobierz tapetę do telefonu"}),
            reportfix._claude("c-notes", cls.notes, "", NOW - 1.5 * H, "assistant",
                              message={"role": "assistant", "stop_reason": "end_turn",
                                       "content": [{"type": "text", "text": "Tapeta gotowa: spokojniejszy wariant."}]}),
            {"type": "system", "subtype": "turn_duration", "isSidechain": False, "timestamp": iso(NOW - 1.5 * H + 1),
             "cwd": cls.notes, "gitBranch": "", "sessionId": "c-notes"},
        ])
        cls.cache = os.path.join(cls.tmp, "coordination.json")
        with open(cls.cache, "w", encoding="utf-8") as fh:
            json.dump(coordination_cache(p), fh)
        cls.palette = reports.load_palette(palette_candidates())
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.bin)
        cls.fake = os.path.join(cls.bin, "claude")
        with open(cls.fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(cls.fake, 0o755)
        cls.answer = os.path.join(cls.tmp, "answer.json")
        with open(cls.answer, "w", encoding="utf-8") as fh:
            json.dump(ANSWER, fh)
        cls.index, cls.report = cls.generate()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def generate(cls, root=None, **kwargs):
        return reports.generate(root or os.path.join(cls.tmp, "reports"), reportfix.DAY, START, NOW,
                                palette=cls.palette, workspace=cls.p["work"], claude_root=cls.p["claude"],
                                codex_root=cls.p["codex"], live=reportfix.live_sessions(cls.p),
                                limits_result={"ok": True, "status": reportfix.STATUS}, home=cls.p["home"],
                                coordination_cache=cls.cache, **kwargs)

    def env(self, mode="ok", log=None, answer=None):
        log = log or os.path.join(tempfile.mkdtemp(dir=self.tmp), "calls.jsonl")
        return dict(os.environ, TIDE_CLAUDE_BIN=self.fake, FAKE_CLAUDE_MODE=mode, FAKE_CLAUDE_LOG=log,
                    FAKE_CLAUDE_ANSWER=answer or self.answer)

    @staticmethod
    def calls(env) -> list[dict]:
        try:
            with open(env["FAKE_CLAUDE_LOG"], encoding="utf-8") as fh:
                return [json.loads(line) for line in fh]
        except OSError:
            return []

    def facts(self, name="alpha"):
        return next(f for f in standup.facts(self.report) if f["project"] == name)


class FactsTest(StandupWorld):
    def test_tickets_from_board_only_relevant(self):
        ids = [t["id"] for t in self.facts()["tickets"]]
        self.assertEqual(ids, ["ALPHA-1", "ALPHA-2", "ALPHA-3", "ALPHA-4", "ALPHA-5"], "ALPHA-6 jest sprzed okresu")
        first = self.facts()["tickets"][0]
        self.assertEqual(first["result"], "Dodałem indeks pełnotekstowy, wyszukiwanie działa bez skanowania plików.")
        self.assertEqual(self.facts()["questions"][0]["text"], "ALPHA-3: czy wydajemy w piątek?")

    def test_units_merged_branch_and_sessions(self):
        units = self.facts()["units"]
        merged = next(u for u in units if u["kind"] == "merged")
        self.assertEqual(merged["branch"], "feat/tide-9-export")
        self.assertEqual(merged["commits"], ["feat(export): csv writer", "test(export): cover csv writer"])
        main = [u for u in units if u["kind"] == "main"]
        self.assertEqual(sum(u["count"] for u in main), 2, "dwa bezpośrednie commity w master (bez scalenia)")
        search = next(u for u in units if u.get("branch") == "feat/search")
        self.assertEqual(search["ticket"], "ALPHA-2", "gałąź ticketu z tablicy")
        self.assertEqual([s["title"] for s in search["sessions"]], ["Search <b>index</b>"])
        self.assertTrue(search["sessions"][0]["message"].startswith("Gotowe: indeks wyszukiwania działa."))
        ask = next(u for u in units if u["kind"] == "session")
        self.assertEqual((ask["title"], ask["sessions"][0]["state"]), ("Release plan", "waiting"))

    def test_scheduled_runs_and_codex_branch(self):
        beta = self.facts("beta")
        upload = next(u for u in beta["units"] if u.get("branch") == "codex/fix-upload")
        self.assertEqual(upload["sessions"][0]["message"], "Poprawiłem ponawianie wysyłki.")
        sched = next(s["scheduled"] for u in beta["units"] for s in u["sessions"] if s.get("scheduled"))
        self.assertEqual((sched["name"], sched["runs"]), ("Nocny przegląd", 3))

    def test_text_helpers(self):
        msg = "**Gotowe.**\n\n- dodałem `builder` w [module](/x/y.py)\n- testy przechodzą\n\nKolejny akapit."
        self.assertEqual(standup.first_sentences(msg, 2), "Gotowe. Dodałem builder w module. Testy przechodzą.")
        beat = ("<heartbeat>\n <automation_id>x</automation_id>\n <decision>DONT_NOTIFY</decision>\n"
                " <message>V3 scalone; trwa integracja.</message>\n</heartbeat>")
        self.assertEqual(standup.first_sentences(beat), "V3 scalone; trwa integracja.")
        self.assertEqual(standup.last_question("Zrobiłem A. Czy mogę scalić? Dzięki."), "Czy mogę scalić?")
        self.assertIn("nie przechodzą", standup.failing_tests("Gotowe. Dwa testy nie przechodzą na CI."))
        self.assertEqual(standup.failing_tests("Wszystkie testy przechodzą, 0 failed."), "")
        self.assertEqual(standup.ticket_key("la-035"), "LA-35")
        self.assertEqual(standup.ticket_ids("codex/la035-quote", dashless=True), ["LA-35"])
        self.assertEqual(standup.ticket_ids("utf-8 i SHA-256"), [])


class RulesTest(StandupWorld):
    def rules(self, name="alpha"):
        return standup.rules(self.facts(name))

    def test_groups_by_ticket(self):
        r = self.rules()
        done = {i["ref"]: i for i in r["done"] if i["ref"]}
        self.assertIn("ALPHA-1", done)
        self.assertEqual(done["ALPHA-1"]["text"], "Indeks wyszukiwania")
        self.assertTrue(done["ALPHA-1"]["about"].startswith("Dodałem indeks pełnotekstowy"))
        doing = {i["ref"]: i for i in r["doing"]}
        self.assertEqual(doing["ALPHA-2"]["text"], "Wyszukiwarka w UI — implementacja gotowa, czeka na review")
        waiting = " | ".join(i["text"] for i in r["waiting"])
        self.assertIn("Termin wydania: czy wydajemy w piątek?", waiting)
        self.assertNotIn("Pytanie koordynatora", waiting, "pytanie jest już przy tickecie ALPHA-3")
        self.assertIn("Filtry — review zgłosił 1 MAJOR", " | ".join(i["text"] for i in r["doing"]),
                      "uwagi z review poprawia agent — to praca w toku, nie sprawa dla użytkownika")
        self.assertIn(f"Release plan: agent pyta: „{reportfix.ASK_QUESTION}”", waiting)
        self.assertNotIn("Pushować", json.dumps(r, ensure_ascii=False), "stare pytanie z tekstu (PT-23)")
        self.assertEqual([i["ref"] for i in r["next"]], ["ALPHA-4"])
        self.assertNotIn("ALPHA-6", json.dumps(r))
        self.assertEqual(r["theme"], "Wyszukiwanie w alpha: Wyszukiwanie w aplikacji: indeks, UI i eksport do CSV.",
                         "cel planu")

    def test_merged_branch_not_commit_list(self):
        r = self.rules()
        merged = next(i for i in r["done"] if "Export" in i["text"])
        self.assertEqual((merged["ref"], merged["text"]), ("TIDE-9", "Export"),
                         "tytuł z nazwy gałęzi, nie temat commita; numer w ref, nie w treści")
        self.assertIn("Zmiany są już w głównej wersji", merged["about"])
        self.assertFalse(any("feat(" in i["text"] for g in standup.GROUP_KEYS for i in r[g]),
                         "żadnego surowego tematu commita z prefiksem typu")

    def test_session_branch_in_progress_and_scheduled(self):
        r = self.rules("beta")
        self.assertTrue(any(i["text"] == "check — zmiany czekają na scalenie" and i.get("about") == "Poprawiłem ponawianie wysyłki."
                            for i in r["doing"]), r["doing"])
        self.assertTrue(any(i["text"] == "Nocny przegląd: 3 przebiegi" for i in r["done"]), r["done"])

    def test_many_loose_branches_collapse_and_cap(self):
        fact = {"project": "x", "plans": [], "tickets": [], "questions": [], "units": [
            {"kind": "branch", "branch": f"codex/b{i}", "title": f"b{i}", "commits": ["c"], "count": 1,
             "sessions": [], "ticket": ""} for i in range(6)]}
        r = standup.rules(fact)
        self.assertEqual(len(r["doing"]), 1)
        self.assertTrue(r["doing"][0]["text"].startswith("6 rozpoczętych zmian czeka na przejrzenie: b0, b1"))
        many = {"project": "y", "plans": [], "questions": [], "units": [], "tickets": [
            {"id": f"Y-{i}", "key": f"Y-{i}", "title": f"t{i}", "state": "gotowe", "note": "", "result": "",
             "question": "", "branch": "", "review": ""} for i in range(20)]}
        done = standup.rules(many)["done"]
        self.assertEqual(len(done), standup.ITEMS_MAX)
        self.assertEqual(done[-1]["text"], "i 13 więcej — szczegóły niżej")


class ModelInputTest(StandupWorld):
    def test_secrets_stripped(self):
        secrets = {
            "anthropic": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
            "github": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "gitlab": "glpat-abcdefghijklmnop1234",
            "aws": "AKIAABCDEFGHIJKLMNOP",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl",
            "youtrack": "perm:dG9tZWs=.NDQtMQ==.AbCdEfGhIjKlMnOpQrStUv",
            "pass": "hunter2hunter2",
            "envval": "s3cr3tValue",
            "random": "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0LmNbVcXz",
            "urlpass": "pa55word",
        }
        message = (f"Klucz {secrets['anthropic']} i {secrets['github']}, {secrets['gitlab']}, {secrets['aws']}.\n"
                   f"Token: Bearer {secrets['jwt']}\nYouTrack {secrets['youtrack']}\n"
                   f"password={secrets['pass']}\nAPI_TOKEN={secrets['envval']}\nlosowy {secrets['random']}\n"
                   f"https://user:{secrets['urlpass']}@example.com/repo\n"
                   "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
                   f"Plik w {self.p['home']}/projekt/.env")
        fact = {"project": "s", "plans": [], "tickets": [], "questions": [], "unpushed": [], "units": [
            {"kind": "session", "title": "Sekrety", "branch": "", "commits": [], "count": 0, "ticket": "",
             "sessions": [{"title": "Sekrety", "state": "done", "reason": "", "message": message, "full": message,
                           "failing": ""}]}]}
        cleaned = summarize.strip_secrets(message, self.p["home"])
        for key, value in secrets.items():
            self.assertNotIn(value, cleaned, key)
        self.assertNotIn("b3BlbnNzaC1rZXktdjEAAAAA", cleaned)
        self.assertNotIn(self.p["home"], cleaned)
        self.assertIn("~/projekt/.env", cleaned)
        # To samo w wejściu dla modelu (wycinek wiadomości, tytuły, notatki ticketów).
        fact["tickets"] = [{"id": "S-1", "key": "S-1", "title": f"Rotacja {secrets['github']}", "state": "gotowe",
                            "note": "", "review": "", "result": f"API_KEY: {secrets['envval']} w {self.p['home']}/x",
                            "question": ""}]
        payload, names = summarize.model_input([fact], self.p["home"])
        text = json.dumps(payload, ensure_ascii=False)
        for key, value in secrets.items():
            self.assertNotIn(value, text, key)
        self.assertNotIn(self.p["home"], text)
        self.assertIn("[ukryte]", text)
        self.assertIn("~/x", text)
        self.assertEqual(names, ["s"])
        # Zwykłe nazwy gałęzi, ścieżki i krótkie skróty commitów zostają.
        self.assertEqual(summarize.strip_secrets("feat/tide-12-very-long-branch-name-for-the-report-view 732815f"),
                         "feat/tide-12-very-long-branch-name-for-the-report-view 732815f")

    def test_size_caps(self):
        long = "Zdanie o pracy agenta, które jest dość długie i powtarza się wiele razy. " * 200
        units = [{"kind": "branch", "branch": f"feat/b{i}", "title": f"Gałąź {i}", "ticket": "",
                  "commits": [f"feat(x): commit {j} " + "x" * 100 for j in range(40)], "count": 40,
                  "sessions": [{"title": f"S{i}", "state": "done", "reason": "", "message": long[:300], "full": long,
                                "failing": ""}]} for i in range(60)]
        fact = {"project": "big", "plans": [], "questions": [], "unpushed": [], "units": units,
                "tickets": [{"id": f"B-{i}", "key": f"B-{i}", "title": "t" * 150, "state": "todo", "note": long[:220],
                             "review": "", "result": long[:240], "question": ""} for i in range(80)]}
        payload, names = summarize.model_input([fact], "")
        size = len(json.dumps(payload["projects"][0], ensure_ascii=False))
        self.assertLessEqual(size, summarize.PROJECT_CAP)
        self.assertGreater(size, summarize.PROJECT_CAP // 4, "przycięte, ale nie puste")
        # Limit całości: projekty ponad limit zostają przy regułach.
        facts = [dict(fact, project=f"p{i}") for i in range(10)]
        payload, names = summarize.model_input(facts, "")
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), summarize.TOTAL_CAP + 200)
        self.assertLess(len(names), 10)
        self.assertEqual(names, [f"p{i}" for i in range(len(names))])

    def test_input_of_world_has_no_internal_paths_or_full_logs(self):
        payload, names = summarize.model_input(standup.facts(self.report), self.p["home"])
        self.assertEqual(names, ["alpha", "beta"])
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('"full"', text)
        self.assertNotIn(self.p["home"], text)
        alpha = payload["projects"][0]
        self.assertEqual([t["id"] for t in alpha["tickets"]], ["ALPHA-1", "ALPHA-2", "ALPHA-3", "ALPHA-4", "ALPHA-5"])


class ValidateTest(StandupWorld):
    def test_valid_and_hostile_answer(self):
        facts = standup.facts(self.report)
        out = standup.validate(ANSWER, facts)
        self.assertEqual(set(out), {"alpha"}, "nieznany projekt odrzucony, beta bez pozycji → reguły")
        alpha = out["alpha"]
        self.assertEqual(alpha["done"][0]["ref"], "ALPHA-1")
        self.assertIn(HOSTILE, alpha["done"][0]["text"], "tekst zostaje tekstem — escapuje go render")
        self.assertEqual(alpha["done"][1]["ref"], "", "wymyślone ID ticketu znika")
        self.assertEqual(alpha["done"][2]["text"], "goły napis zamiast obiektu")
        self.assertEqual(len(alpha["done"]), 3, "pusta pozycja i liczba odrzucone")
        self.assertEqual(alpha["doing"][0]["ref"], "ALPHA-2", "ID w innej pisowni wraca do postaci z planu")
        merged = standup.merge(out, facts)
        self.assertEqual(merged["beta"]["source"], "rules")
        self.assertEqual(merged["alpha"]["source"], "model")
        self.assertTrue(any("Termin wydania" in i["text"] for i in merged["alpha"]["waiting"]),
                        "model pominął „Czeka na mnie” — zostają pozycje z reguł")

    def test_malformed(self):
        facts = standup.facts(self.report)
        for bad in (None, [], "x", {"projects": "nie lista"}, {"projects": [{"name": "nieznany", "done": []}]},
                    {"projects": [{"name": "alpha"}]}, {"projects": [{"done": [{"text": "bez nazwy"}]}]}):
            with self.assertRaises(standup.Invalid, msg=repr(bad)):
                standup.validate(bad, facts)

    def test_lengths_capped_and_control_chars(self):
        facts = standup.facts(self.report)
        out = standup.validate({"projects": [{"name": "alpha", "theme": "a\x00b" + "t" * 5000,
                                              "done": [{"text": "x" * 5000, "why": "y‮" * 2000}] * 30}]}, facts)
        alpha = out["alpha"]
        self.assertLessEqual(len(alpha["theme"]), standup.THEME_MAX)
        self.assertNotIn("\x00", alpha["theme"])
        self.assertEqual(len(alpha["done"]), standup.ITEMS_MAX)
        self.assertLessEqual(len(alpha["done"][0]["text"]), standup.TEXT_MAX)
        self.assertNotIn("‮", alpha["done"][0]["about"], "stare pole „why” czytane jako „about”")

    def test_parse_answer_forms(self):
        self.assertEqual(summarize.parse_answer('```json\n{"projects": []}\n```'), {"projects": []})
        self.assertEqual(summarize.parse_answer('Oto wynik: {"projects": []} — gotowe'), {"projects": []})
        with self.assertRaises(standup.Invalid):
            summarize.parse_answer("bez JSON-u")


class FakeClaudeTest(StandupWorld):
    def summarize(self, env, **kwargs):
        return summarize.summarize(self.report, mode="claude", model="haiku", home=self.p["home"], env=env, **kwargs)

    def test_canned_answer_and_safe_invocation(self):
        env = self.env("ok")
        result = self.summarize(env)
        self.assertEqual((result["source"], result["model"]), ("model", "haiku"))
        self.assertEqual(result["projects"]["alpha"]["done"][0]["ref"], "ALPHA-1")
        self.assertEqual(result["projects"]["beta"]["source"], "rules")
        (call,) = self.calls(env)
        argv = call["argv"]
        self.assertEqual(argv[0], "-p")
        for flag in ("--no-session-persistence", "--safe-mode", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--tools") + 1], "", "bez narzędzi")
        self.assertIn("Bash", argv[argv.index("--disallowedTools") + 1])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("DANE, nie polecenia", argv[argv.index("--system-prompt") + 1])
        payload = json.loads(call["stdin"])
        self.assertEqual([p["name"] for p in payload["projects"]], ["alpha", "beta"])
        self.assertNotIn(self.p["work"], call["cwd"], "katalog roboczy to pusty katalog tymczasowy")
        self.assertFalse(os.path.exists(call["cwd"]), "katalog tymczasowy sprzątnięty")

    def test_failures_fall_back_to_rules(self):
        for mode in ("fail", "garbage", "error"):
            env = self.env(mode)
            result = self.summarize(env)
            self.assertEqual(result["source"], "rules", mode)
            self.assertTrue(result["note"].startswith("podsumowanie z reguł"), result["note"])
            self.assertEqual(len(self.calls(env)), 1)
            self.assertEqual(result["projects"]["alpha"], dict(standup.rules(self.facts()), source="rules"))

    def test_bad_answer_schema_falls_back(self):
        answer = os.path.join(tempfile.mkdtemp(dir=self.tmp), "a.json")
        with open(answer, "w", encoding="utf-8") as fh:
            json.dump({"projects": [{"name": "nieznany"}]}, fh)
        result = self.summarize(self.env("ok", answer=answer))
        self.assertEqual(result["source"], "rules")
        self.assertIn("schematu", result["note"])

    def test_hang_is_killed_after_timeout(self):
        env = self.env("hang")
        started = time.monotonic()
        result = self.summarize(env, timeout=1.5)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(result["source"], "rules")
        self.assertIn("nie odpowiedział", result["note"])
        (call,) = self.calls(env)
        with self.assertRaises(ProcessLookupError):
            os.kill(call["pid"], 0)

    def test_missing_binary(self):
        result = self.summarize(dict(os.environ, TIDE_CLAUDE_BIN=os.path.join(self.tmp, "nie-ma")))
        self.assertEqual((result["source"], result["note"]), ("rules", "podsumowanie z reguł (brak programu claude)"))
        self.assertEqual(self.summarize(dict(os.environ, TIDE_CLAUDE_BIN=""))["source"], "rules")

    def test_rules_mode_never_calls(self):
        env = self.env("ok")
        result = summarize.summarize(self.report, mode="rules", env=env)
        self.assertEqual(result["source"], "rules")
        self.assertEqual(self.calls(env), [])

    def test_find_claude(self):
        home = tempfile.mkdtemp(dir=self.tmp)
        self.assertEqual(summarize.find_claude({"PATH": "", "HOME": home}), "")
        local = os.path.join(home, ".local", "bin")
        os.makedirs(local)
        shutil.copy(self.fake, os.path.join(local, "claude"))
        self.assertEqual(summarize.find_claude({"PATH": "/nonexistent", "HOME": home}), os.path.join(local, "claude"))
        self.assertEqual(summarize.find_claude({"PATH": self.bin, "HOME": home}), self.fake)
        self.assertEqual(summarize.find_claude({"PATH": self.bin, "HOME": home, "TIDE_CLAUDE_BIN": ""}), "")


class RenderTest(StandupWorld):
    def test_generate_with_model_cache_and_pages(self):
        root = tempfile.mkdtemp(dir=self.tmp)
        env = self.env("ok")
        index, report = self.generate(root, summary="claude", env=env)
        folder = os.path.dirname(index)
        cache = os.path.join(folder, summarize.CACHE_NAME)
        self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o600)
        with open(index, encoding="utf-8") as fh:
            page = fh.read()
        # Indeks to przegląd: karty projektów na górze, szczegóły zwinięte niżej.
        self.assertLess(page.index("<h2>Projekty"), page.index('<details class="details">'))
        self.assertNotIn('<details class="details" open', page)
        self.assertLess(page.index('<details class="details">'), page.index("Limity AI"))
        self.assertIn("opis: Claude Sonnet", page)
        self.assertIn("idą do Anthropic", page)
        # Złośliwy tekst modelu escapowany; jedyny skrypt to „Kopiuj”.
        self.assertNotIn("<img src=x", page)
        self.assertEqual(len(re.findall(r"<script>", page)), 1)
        # Karta alpha: zdanie z modelu, liczniki, „Czeka na mnie” (pytanie z reguł — śmieci modelu odrzucone),
        # odnośnik do strony projektu; wyróżniona i pierwsza, bo coś czeka.
        cards = re.findall(r'<article class="card ov( wait)?">.*?</article>', page, re.S)
        self.assertEqual(len(cards), 2)
        alpha_card = re.search(r'<article class="card ov wait">.*?</article>', page, re.S).group(0)
        self.assertIn('<a href="alpha.html">alpha</a>', alpha_card)
        self.assertIn("Indeks wyszukiwania gotowy, wyszukiwarka w UI czeka na review.", alpha_card)
        self.assertIn('<span class="chip mint">zrobione 3</span>', alpha_card)
        self.assertIn('<span class="chip peach">czeka ', alpha_card)
        self.assertIn('<span class="chip">porządki 1</span>', alpha_card)
        self.assertIn("Termin wydania: czy wydajemy w piątek?", alpha_card)
        self.assertNotIn("nie potrzebuje odpowiedzi", page)
        self.assertNotIn("26 gałęzi", page)
        self.assertLess(page.index('href="alpha.html"'), page.index('href="beta.html"'))
        self.assertNotIn('<span class="ref">ALPHA-4</span>', page, "„Dalej” tylko na stronie projektu")
        # Kopiuj wszystko: standup wszystkich projektów zwykłym tekstem.
        copy = re.search(r'data-copy="([^"]*)" data-label="Kopiuj wszystko jako tekst"', page)
        text = render.html.unescape(copy.group(1))
        self.assertTrue(text.startswith("Na daily · poniedziałek, 26 października 2026\n\nDziś w skrócie:\n- alpha — "), text)
        self.assertIn("\n\nalpha\n", text)
        self.assertIn("\nbeta\n", text, "beta z reguł")
        self.assertNotIn("notes", text, "sesja spoza repozytoriów nie trafia do „Na daily”")

        # Strona projektu: własne „Na daily” na górze, kopiowanie tylko tego projektu, porządki zwinięte.
        with open(os.path.join(folder, "alpha.html"), encoding="utf-8") as fh:
            alpha = fh.read()
        self.assertLess(alpha.index("<h2>Na daily</h2>"), alpha.index('<details class="details">'))
        copy = render.html.unescape(re.search(r'data-copy="([^"]*)" data-label="Kopiuj jako tekst"', alpha).group(1))
        self.assertTrue(copy.startswith("Na daily · poniedziałek, 26 października 2026\n\nalpha\n"), copy)
        self.assertNotIn("\nbeta\n", copy)
        self.assertIn("Na czym polega: Wydanie z wyszukiwaniem: indeks i eksport.", copy)
        self.assertIn("Zrobione:\n- ALPHA-1 Indeks wyszukiwania " + HOSTILE + " — Wyniki szybciej.", copy)
        self.assertIn("- ALPHA-2 Wyszukiwanie — implementacja gotowa, trwa review (dalej: poprawki)", copy)
        self.assertIn("Czeka na mnie:\n", copy)
        self.assertIn("Porządki:\n- Plan pracy agentów dopisany do instrukcji " + HOSTILE, copy)
        self.assertLess(copy.index("Dalej:"), copy.index("Porządki:"), "porządki po pracy")
        self.assertIn('<span class="ref">ALPHA-2</span>', alpha)
        self.assertIn('<details class="chores"><summary>Porządki <span class="n">1</span>', alpha)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", alpha)
        self.assertNotIn("<img src=x", alpha)
        self.assertEqual(len(re.findall(r"<script>", alpha)), 1)
        self.assertIn("Release plan", alpha)
        self.assertIn("feat(search): index builder", alpha)
        self.assertEqual(len(self.calls(env)), 1)

        # Drugie Generate() z tymi samymi faktami: odpowiedź z summary.json, bez wołania modelu.
        index2, report2 = self.generate(root, summary="claude", env=env)
        self.assertEqual(report2.standup["source"], "model")
        self.assertEqual(len(self.calls(env)), 1)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(index2), summarize.CACHE_NAME)))

    def test_rules_footer_and_no_cache(self):
        with open(self.index, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("opis z reguł (bez modelu)", page)
        self.assertIn("bez wysyłania czegokolwiek", page)
        self.assertNotIn("Anthropic", page)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.index), summarize.CACHE_NAME)))
        self.assertRegex(page, r'<p class="headline">Zrobione: [^<]+</p>', "zdanie do karty z reguł")
        with open(os.path.join(os.path.dirname(self.index), "alpha.html"), encoding="utf-8") as fh:
            alpha = fh.read()
        self.assertIn('<span class="ref">ALPHA-2</span>', alpha)
        self.assertIn('<details class="chores">', alpha, "gałąź z samymi instrukcjami agentów to porządki")

    def test_outside_projects(self):
        (outside,) = self.report.outside
        self.assertEqual([s.id for s in outside.sessions], ["claude:c-notes"])
        self.assertNotIn(outside.name, [f["project"] for f in standup.facts(self.report)])
        folder = os.path.dirname(self.index)
        self.assertFalse(os.path.exists(os.path.join(folder, f"{outside.slug}.html")), "bez własnej strony")
        with open(self.index, encoding="utf-8") as fh:
            page = fh.read()
        section = re.search(r'<details class="quiet outside">.*?</details>', page, re.S).group(0)
        self.assertIn("Poza projektami", section)
        self.assertIn("Dobierz tapetę do telefonu", section)
        self.assertIn("Tapeta gotowa: spokojniejszy wariant.", section)
        self.assertIn('id="s-claude-c-notes"', section)
        self.assertIn("claude --resume c-notes", section)
        self.assertLess(page.index("Poza projektami"), page.index('<details class="details">'))

    def test_quiet_period(self):
        root = tempfile.mkdtemp(dir=self.tmp)
        index, report = self.generate(root, )
        empty = render.daily(type("R", (), {"standup": {"projects": {}}, "day": report.day})(), [], index=True)
        self.assertIn("nie ma nic do opowiedzenia", empty)


class CategoryTest(StandupWorld):
    """Praca czy porządki: sygnały z gita, tematów i poleceń jako podpowiedź (model może poprawić)."""

    def test_signals(self):
        chore = standup.commit_is_chore
        self.assertTrue(chore("docs(plan): track LA-035 review"))
        self.assertTrue(chore("chore: bump tools"))
        self.assertTrue(chore("feat: add resumable YouTrack migration tool", ["tools/migrate.py"]))
        self.assertTrue(chore("docs: agent rules", ["CLAUDE.md", "docs/agents/workflow.md", ".claude/settings.json"]))
        self.assertTrue(chore("fix: token check", ["tools/youtrack-mcp/run.sh"]))
        self.assertFalse(chore("feat(queries): compare a part of the log value", ["src/Queries/Value.cs", "CLAUDE.md"]))
        self.assertFalse(chore("docs(library): record acceptance", ["docs/library/x.md"]))
        self.assertFalse(chore("perf: use a linear scan", ["src/a.py"]), "„linear” małą literą to nie tracker")
        self.assertTrue(standup.text_is_chore("Migruj integrację Linear do YouTrack"))
        self.assertTrue(standup.text_is_chore("usuń MyAI z instrukcji we wszystkich projektach"))
        self.assertFalse(standup.text_is_chore("Dodaj warunek na wartościach logów"))
        self.assertEqual(standup.areas(["src/App.Web/a.cs", "src/App.Web/b.cs", "README.md", "daemon/x/y.py",
                                        "tests/Unit/a.cs"]),
                         [["src/App.Web", 2], ["README.md", 1], ["daemon", 1], ["tests/Unit", 1]])

    def test_chore_branch_and_ticket(self):
        fact = self.facts()
        plan = next(u for u in fact["units"] if u.get("branch") == "docs/agent-plan")
        self.assertEqual(plan["category"], "chores")
        self.assertEqual(plan["areas"], [["CLAUDE.md", 1], ["docs", 1]])
        search = next(u for u in fact["units"] if u.get("branch") == "feat/search")
        self.assertEqual(search["category"], "work")
        self.assertEqual(search["areas"], [["search.py", 1], ["search_test.py", 1]])
        r = standup.rules(fact)
        title = standup._unit_title(plan)
        self.assertIn(title, [i["text"] for i in r["chores"]])
        for key in standup.GROUP_KEYS:
            self.assertNotIn(title, [i["text"] for i in r[key]], key)
        payload, _names = summarize.model_input([fact], self.p["home"])
        unit = next(u for u in payload["projects"][0]["work"] if u.get("branch") == "docs/agent-plan")
        self.assertEqual(unit["category"], "chores")
        self.assertLessEqual(len(unit.get("commits") or []), 2, "tematy porządków nie zajmują miejsca w wejściu")
        # Ticket planu o trackerze — porządek; pytanie przy nim i tak idzie do „Czeka na mnie”.
        tracker = dict(fact, tickets=[
            {"id": "TR-1", "key": "TR-1", "title": "Przepięcie na YouTrack", "state": "gotowe", "note": "",
             "result": "", "question": "", "branch": "", "review": "", "category": "chores"},
            {"id": "TR-2", "key": "TR-2", "title": "Migracja zadań do YouTracka", "state": "czeka", "note": "",
             "result": "", "question": "Czy usunąć stare zadania?", "branch": "", "review": "", "category": "chores"}],
            units=[], questions=[])
        r = standup.rules(tracker)
        self.assertEqual([i["ref"] for i in r["chores"]], ["TR-1"])
        self.assertEqual([i["ref"] for i in r["waiting"]], ["TR-2"])
        self.assertEqual(r["done"], [])

    def test_agent_branches_skipped(self):
        alpha = next(p for p in self.report.active if p.name == "alpha")
        self.assertIn("worktree-agent-abc123", [w.get("branch") for w in alpha.unpushed], "świat ma taką gałąź")
        fact = self.facts()
        self.assertNotIn("worktree-agent-abc123", fact["unpushed"])
        self.assertFalse(any((u.get("branch") or "").startswith("worktree-agent-") for u in fact["units"]))
        payload, _names = summarize.model_input(standup.facts(self.report), self.p["home"])
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("worktree-agent", text)
        self.assertNotIn("unmergedBranches", text, "liczniki gałęzi nie idą do modelu")

    def test_merged_into_integration_branch(self):
        part = next(u for u in self.facts()["units"] if u.get("branch") == "feat/tide-12-part")
        self.assertEqual(part["mergedInto"], "feat/int")
        done = standup.rules(self.facts())["done"]
        item = next(i for i in done if i["ref"] == "TIDE-12")
        self.assertEqual(item["text"], "Part")
        self.assertIn("w wersji zbiorczej", item["about"])
        payload, _names = summarize.model_input([self.facts()], self.p["home"])
        unit = next(u for u in payload["projects"][0]["work"] if u.get("branch") == "feat/tide-12-part")
        self.assertEqual((unit["kind"], unit["mergedInto"]), ("merged-into-integration-branch", "feat/int"))

    def test_task_and_ticket_context_in_input(self):
        search = next(u for u in self.facts()["units"] if u.get("branch") == "feat/search")
        self.assertEqual(search["sessions"][0]["task"], "Build the search index", "pierwsze polecenie użytkownika")
        payload, _names = summarize.model_input(standup.facts(self.report), self.p["home"])
        alpha = payload["projects"][0]
        unit = next(u for u in alpha["work"] if u.get("branch") == "feat/search")
        self.assertEqual(unit["sessions"][0]["task"], "Build the search index")
        self.assertEqual(unit["commits"], ["feat(search): index builder", "test(search): cover the index"])
        self.assertIn("search.py (1)", unit["areas"])
        first = alpha["tickets"][0]
        self.assertEqual(first["id"], "ALPHA-1")
        self.assertNotIn("note", first, "„review OK” przy gotowym tickecie tylko myli — gotowe to koniec")
        self.assertEqual(first["description"], "Indeks pełnotekstowy dla wyszukiwarki w aplikacji.")
        self.assertEqual(alpha["plans"][0]["goal"], "Wyszukiwanie w aplikacji: indeks, UI i eksport do CSV.")

    def test_ticket_description_one_line(self):
        text = ("Źródło: docs/agents/backlog/LA-035.md\nOryginalny identyfikator: LA-035\n# LA-035: Pokrycie ustaw\n\n"
                "**Status:** `in-progress` — [szkice](../x.md) gotowe.")
        self.assertEqual(standup.description(text), "LA-035: Pokrycie ustaw Status: in-progress — szkice gotowe.")

    def test_pick_commits(self):
        subjects = ["docs(plan): track", "test(x): a", "feat(x): b", "docs(lib): c", "fix(x): d", "chore: e"]
        self.assertEqual(summarize.pick_commits(subjects, 3), ["test(x): a", "feat(x): b", "fix(x): d"])


class JunkTest(StandupWorld):
    def test_waiting_junk_dropped(self):
        facts = standup.facts(self.report)
        junk = ["Nie ma teraz decyzji dla Ciebie.", "Nic nie czeka.", "26 gałęzi LA-035 nie jest scalonych.",
                "25 gotowych ticketów nie ma review.", "Sześć zadań skończonych, czekających na review.",
                "Bez ponownego review: synchronizacja klucza.", "Nie scalone pozostały gałęzie, m.in. feat/x.",
                "Gałąź worktree-agent-a024 ma zmiany."]
        out = standup.validate({"projects": [{"name": "alpha", "done": [{"text": "Indeks"}],
                                              "waiting": [{"text": t} for t in junk]
                                              + [{"ref": "ALPHA-3", "text": "Czy wydajemy w piątek?"},
                                                 {"text": "Dwa testy eksportu nie przechodzą."}]}]}, facts)
        self.assertEqual([i["text"] for i in out["alpha"]["waiting"]],
                         ["Czy wydajemy w piątek?", "Dwa testy eksportu nie przechodzą."])
        self.assertEqual(out["alpha"]["headline"], "Zrobione: Indeks.", "bez zdania od modelu — z pozycji")
        moved = standup.validate({"projects": [{"name": "alpha", "doing": [
            {"text": "Migracja z Lineara do YouTracka: zadania poprawione.", "next": "dokończyć"},
            {"text": "Eksport do CSV — trwa review"}]}]}, facts)["alpha"]
        self.assertEqual([i["text"] for i in moved["doing"]], ["Eksport do CSV — trwa review"])
        self.assertEqual([i["text"] for i in moved["chores"]], ["Migracja z Lineara do YouTracka: zadania poprawione."],
                         "pozycja o trackerze to porządek, nawet gdy model wpisał ją do pracy")

    def test_done_ticket_is_finished_not_waiting_for_review(self):
        fact = {"project": "x", "plans": [], "questions": [], "units": [], "unpushed": [], "tickets": [
            {"id": f"XY-{i}", "key": f"XY-{i}", "title": f"t{i}", "state": "gotowe", "note": "bez review",
             "result": "", "question": "", "branch": "", "review": ""} for i in range(3)] + [
            {"id": "XY-9", "key": "XY-9", "title": "w review", "state": "review", "note": "", "result": "",
             "question": "", "branch": "", "review": ""}]}
        r = standup.rules(fact)
        self.assertEqual(r["waiting"], [])
        self.assertEqual([i["ref"] for i in r["done"]], ["XY-0", "XY-1", "XY-2"])
        self.assertEqual([i["ref"] for i in r["doing"]], ["XY-9"])
        payload, _names = summarize.model_input([fact], "")
        self.assertFalse(any("note" in t for t in payload["projects"][0]["tickets"]))
        self.assertIn('stan "review" to praca w toku', summarize.SYSTEM_PROMPT)


class PlanYouTrackTest(unittest.TestCase):
    """Plan z repozytorium, którego tablica nie zna: cel planu i opis ticketu z YouTracka (przez mapę migracji)."""

    def test_plan_goal_and_description(self):
        tmp = tempfile.mkdtemp(prefix="tide-standup-plan-")
        self.addCleanup(shutil.rmtree, tmp, True)
        plans_dir = os.path.join(tmp, "docs", "agents", "plans")
        os.makedirs(plans_dir)
        with open(os.path.join(plans_dir, "2026-10-26-la035.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: LA-035 dowód\ntracker: youtrack\ntracker-url: http://yt.local\n"
                     "tracker-project: LEGAL\nticket-prefix: LA\nstatus: active\nupdated: 2026-10-26T05:00:00Z\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n| --- | --- | --- | --- | --- | --- |\n"
                     "| LA-035 | Art. 58 | w-toku | feat/x | k | V3c scalone |\n\n## Cel\n\nOstrzeżenie z urzędowego tekstu.\n")
        migration = os.path.join(tmp, "docs", "agents", "youtrack-migration")
        os.makedirs(migration)
        with open(os.path.join(migration, "map.csv"), "w", encoding="utf-8") as fh:
            fh.write("source_key,youtrack_id,kind\nlocal:LA-035,LEGAL-35,ticket\n")
        asked = []

        class Tracker:
            def get(self, url, project, force=False):
                asked.append((url, project))
                return {"LEGAL-35": {"id": "LEGAL-35", "description": "Źródło: x\n# Pokrycie ustaw\nUPN i UOOZ."}}, {}

        project = types.SimpleNamespace(name="legal", path=tmp, is_repo=True, plans=[])
        plans.attach([project], START, NOW, None, Tracker())
        (entry,) = project.plans
        self.assertEqual(entry["goal"], "Ostrzeżenie z urzędowego tekstu.")
        (ticket,) = entry["all"]
        self.assertEqual((ticket["planNote"], ticket["branch"]), ("V3c scalone", "feat/x"))
        self.assertEqual(standup.description(ticket["description"]), "Pokrycie ustaw UPN i UOOZ.")
        self.assertEqual(asked, [("http://yt.local", "LEGAL")])
        plans.attach([project], START, NOW, None, None)
        self.assertEqual(project.plans[0]["all"][0]["description"], "", "bez YouTracka — bez opisu")


class CachePayloadTest(unittest.TestCase):
    def test_ticket_fields_for_daily(self):
        board = {"id": "claude:x", "tool": "claude", "question": {"text": "Czy scalić?", "ts": 1},
                 "tickets": [{"id": "T-1", "title": "A", "state": "gotowe", "branch": "feat/t-1",
                              "review": {"text": "1 NIT"}, "question": None,
                              "events": [{"kind": "finished", "state": "done", "text": "Dodałem A."},
                                         {"kind": "finished", "state": "done", "text": "„A” — 1 NIT",
                                          "review": {"nit": 1}}]}]}
        payload = actions.cache_payload([board], NOW)
        coordinator = payload["coordinators"][0]
        self.assertEqual(coordinator["question"], "Czy scalić?")
        ticket = coordinator["tickets"][0]
        self.assertEqual((ticket["branch"], ticket["review"], ticket["result"], ticket["question"]),
                         ("feat/t-1", "1 NIT", "Dodałem A.", ""), "wynik pracy, nie review")


class SettingsTest(unittest.TestCase):
    def test_summary_settings(self):
        tmp = tempfile.mkdtemp(prefix="tide-standup-settings-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "report.json")
        settings, warnings = schedule.load_settings(path)
        self.assertEqual((settings.summary, settings.summary_model, warnings), ("claude", "sonnet", []))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"summary": "rules", "summaryModel": "sonnet"}, fh)
        settings, warnings = schedule.load_settings(path)
        self.assertEqual((settings.summary, settings.summary_model, warnings), ("rules", "sonnet", []))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"summary": "gpt", "summaryModel": "haiku; rm -rf /"}, fh)
        settings, warnings = schedule.load_settings(path)
        self.assertEqual((settings.summary, settings.summary_model, len(warnings)), ("claude", "sonnet", 2))


if __name__ == "__main__":
    unittest.main()
