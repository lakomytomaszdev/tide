"""Synchronizacja stanu z YouTrackiem (PT-7) bez Qt: silnik board/ytsync.py na atrapie REST API (fake_youtrack.py).

Każdy zapis idzie wyłącznie do atrapy na 127.0.0.1 — nigdy do prawdziwego YouTracka.
"""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest

from . import coordfix as F
from .fake_youtrack import BASE_MS, FakeYouTrack
from tide_daemon.board import ytsync
from tide_daemon.coordination import youtrack as yt

T0 = BASE_MS / 1000 + 100          # karty powstały 100 s po pierwszych zgłoszeniach atrapy
TOKEN = F.TOKEN_SENTINEL


def card(ident, issue, column="todo", shown=None, derived=None, moved=T0, moved_from="", created=T0):
    return {"id": ident, "youtrack": issue, "column": column, "boardColumn": shown or column, "derived": derived,
            "movedTs": moved, "movedFrom": moved_from, "createdTs": created, "path": f"/tablica/cards/{ident}.md"}


def snap(*cards, projects=(("legalapp", "LEGAL", True), ("logtool", "LOG", True))):
    return {"projects": [{"slug": s, "name": s, "youtrack": k, "youtrackSync": on} for s, k, on in projects],
            "cards": list(cards), "errors": []}


def coordinator(ident, *tickets, tracker="youtrack", plan_ts=0.0, last=0.0):
    return {"id": ident, "title": "Plan", "lastActivityTs": last,
            "plan": {"tracker": tracker, "title": "Plan LOG", "updatedTs": plan_ts},
            "tickets": [dict(t) for t in tickets]}


def ticket(ident, state, source="runs", activity=T0, first=T0 - 50):
    return {"id": ident, "key": ident, "state": state, "source": source, "lastActivityTs": activity,
            "events": [{"ts": first}]}


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-ytsync-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeYouTrack(TOKEN)
        self.addCleanup(self.fake.stop)
        self.token_file = os.path.join(self.tmp, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(TOKEN + "\n")
        self.config = {"url": self.fake.url, "tokenFile": self.token_file, "tokenVar": ""}
        self.settings = yt.load_sync_settings(os.path.join(self.tmp, "nie-ma.json"))
        self.now = T0 + 1000
        self.state_file = os.path.join(self.tmp, "state", "tide", "youtrack-sync.json")
        self.engine = self.make_engine()

    def make_engine(self):
        client = ytsync.Client(config_loader=lambda: dict(self.config), timeout=1.0)
        return ytsync.SyncEngine(path=self.state_file, client=client, settings_loader=lambda: self.settings,
                                 clock=lambda: self.now)

    def step(self, snapshot, boards=(), wait=6.0, engine=None):
        """Jedno przeliczenie i — po odczekaniu `wait` s — to, co silnik uzna za gotowe do zrobienia."""
        engine = engine or self.engine
        engine.observe(snapshot, list(boards))
        self.now += wait
        engine.observe(snapshot, list(boards))
        items = engine.due()
        if items:
            engine.process(items)
        return items

    def posted(self):
        return [(path.split("?")[0].rsplit("/", 1)[1], body["customFields"][0]["value"]["name"])
                for path, body in self.fake.posts()]


class CardTransitionsTest(Sync):
    def test_each_transition_writes_once_with_youtrack_shape(self):
        self.fake.add("LEGAL-35", "Open")
        self.step(snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"})))
        self.assertEqual(self.posted(), [("LEGAL-35", "In Progress")], "pierwsze spotkanie: praca trwa, nikt nie ruszał")
        path, body = self.fake.posts()[0]
        self.assertTrue(path.startswith("/api/issues/LEGAL-35?fields="))
        self.assertEqual(body, {"customFields": [{"name": "State", "$type": "StateIssueCustomField",
                                                  "value": {"name": "In Progress"}}]})
        for shown, expected in (("waiting", "To be discussed"), ("review", "Fixed")):
            self.step(snap(card("K-16", "LEGAL-35", shown=shown, derived={"column": shown})))
            self.assertEqual(self.posted()[-1], ("LEGAL-35", expected))
        self.step(snap(card("K-16", "LEGAL-35", column="done", moved=self.now, moved_from="review")))
        self.assertEqual(self.posted()[-1], ("LEGAL-35", "Verified"))
        self.assertEqual(len(self.fake.posts()), 4)
        self.assertEqual(self.fake.state("LEGAL-35"), "Verified")
        self.assertEqual(self.engine.status("LEGAL-35")["status"], "ok")
        self.assertIn("Verified", self.engine.status("LEGAL-35")["text"])

    def test_no_write_without_transition_and_no_network_when_idle(self):
        self.fake.add("LEGAL-35", "Open")
        s = snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"}))
        self.step(s)
        calls = len(self.fake.calls)
        for _ in range(5):
            self.assertEqual(self.step(s), [])
        self.assertEqual(len(self.fake.calls), calls, "ten sam stan — ani zapisu, ani odczytu")

    def test_short_flicker_is_debounced(self):
        self.fake.add("LEGAL-35", "Open")
        progress = snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"}))
        self.step(progress)
        review = snap(card("K-16", "LEGAL-35", shown="review", derived={"column": "review"}))
        self.step(review, wait=2.0)            # za krótko
        self.step(progress, wait=2.0)          # i z powrotem
        self.assertEqual(self.posted(), [("LEGAL-35", "In Progress")])

    def test_archived_card_and_disabled_project_do_not_write(self):
        self.fake.add("LEGAL-35", "In Progress")
        self.fake.add("LOG-5", "Open")
        self.step(snap(card("K-1", "LEGAL-35", column="archived"),
                       card("K-2", "LOG-5", shown="progress", derived={"column": "progress"}),
                       projects=(("legalapp", "LEGAL", True), ("logtool", "LOG", False))))
        self.assertEqual(self.fake.calls, [])
        decorated = self.engine.decorate(snap(card("K-2", "LOG-5"), projects=(("logtool", "LOG", False),)))
        self.assertEqual(decorated["cards"][0]["youtrackSync"]["status"], "off")
        self.settings = dict(self.settings, sync=False)   # wyłącznik globalny
        self.step(snap(card("K-3", "LEGAL-35", shown="review", derived={"column": "review"})))
        self.assertEqual(self.fake.calls, [])


class FirstSyncTest(Sync):
    def test_adopts_when_issue_changed_after_link_resolved_or_open(self):
        self.fake.add("LEGAL-1", "Open", updated=BASE_MS + 500_000)     # zmienione po założeniu karty (T0)
        self.fake.add("LEGAL-2", "Fixed")                              # rozwiązane
        self.fake.add("LEGAL-3", "In Progress")                        # Tide chce „open” — nie cofamy
        self.step(snap(card("K-1", "LEGAL-1", shown="progress", derived={"column": "progress"}),
                       card("K-2", "LEGAL-2", shown="progress", derived={"column": "progress"}),
                       card("K-3", "LEGAL-3", column="backlog")))
        self.assertEqual(self.fake.posts(), [])
        self.assertEqual({k: r["state"] for k, r in self.engine.records.items()},
                         {"LEGAL-1": "Open", "LEGAL-2": "Fixed", "LEGAL-3": "In Progress"})
        self.assertTrue(all(r["adopted"] for r in self.engine.records.values()))
        # Po przyjęciu kolejne przejście już idzie.
        self.step(snap(card("K-1", "LEGAL-1", shown="review", derived={"column": "review"})))
        self.assertEqual(self.posted(), [("LEGAL-1", "Fixed")])

    def test_state_file_is_private_and_survives_restart(self):
        self.fake.add("LEGAL-35", "Open")
        self.step(snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"})))
        self.assertEqual(stat.S_IMODE(os.stat(self.state_file).st_mode), 0o600)
        with open(self.state_file, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["issues"]["LEGAL-35"]["state"], "In Progress")
        self.assertEqual(data["issues"]["LEGAL-35"]["key"], "progress")
        self.assertNotIn(TOKEN, json.dumps(data))
        again = self.make_engine()
        self.assertEqual(self.step(snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"})),
                                   engine=again), [])
        self.assertEqual(len(self.fake.posts()), 1)


class ConflictTest(Sync):
    def setUp(self):
        super().setUp()
        self.fake.add("LEGAL-35", "Open")
        self.step(snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"})))
        self.fake.human_sets("LEGAL-35", "To be discussed")

    def review(self):
        return snap(card("K-16", "LEGAL-35", shown="review", derived={"column": "review"}))

    def test_manual_change_is_not_overwritten_and_is_shown(self):
        self.step(self.review())
        self.assertEqual(len(self.fake.posts()), 1, "bez nadpisania zmiany ręcznej")
        self.assertEqual(self.fake.state("LEGAL-35"), "To be discussed")
        status = self.engine.status("LEGAL-35")
        self.assertEqual(status["status"], "conflict")
        self.assertEqual(status["conflict"], {"youtrack": "To be discussed", "tide": "Fixed"})
        self.assertIn("stan zmieniony ręcznie", status["text"])
        decorated = self.engine.decorate(self.review())
        self.assertEqual(decorated["cards"][0]["youtrackSync"]["status"], "conflict")
        self.assertTrue(any("K-16 · LEGAL-35" in e["message"] for e in decorated["errors"]))
        self.assertEqual(decorated["youtrack"]["conflicts"], 1)
        # Bez nowej decyzji nie pytamy w kółko (sprawdzenie najwyżej co 5 min).
        calls = len(self.fake.calls)
        self.step(self.review())
        self.assertEqual(len(self.fake.calls), calls)

    def test_take_from_youtrack_then_next_transition_writes(self):
        self.step(self.review())
        self.assertEqual(self.engine.resolve("LEGAL-35", "youtrack")["ok"], True)
        self.assertEqual(self.engine.status("LEGAL-35")["status"], "ok")
        self.step(self.review())
        self.assertEqual(len(self.fake.posts()), 1, "przyjęty stan z YouTracka — bez zapisu")
        self.step(snap(card("K-16", "LEGAL-35", column="done", moved=self.now, moved_from="review")))
        self.assertEqual(self.posted()[-1], ("LEGAL-35", "Verified"))

    def test_overwrite_from_tide(self):
        self.step(self.review())
        self.assertTrue(self.engine.resolve("LEGAL-35", "tide")["ok"])
        self.step(self.review(), wait=0)
        self.assertEqual(self.posted()[-1], ("LEGAL-35", "Fixed"))
        self.assertEqual(self.engine.status("LEGAL-35")["status"], "ok")
        self.assertFalse(self.engine.resolve("LEGAL-35", "tide")["ok"], "konflikt już rozwiązany")

    def test_conflict_clears_itself_when_states_agree(self):
        self.step(self.review())
        self.fake.human_sets("LEGAL-35", "Fixed")
        self.now += ytsync.CONFLICT_RECHECK_S
        self.step(self.review())
        self.assertEqual(self.engine.status("LEGAL-35")["status"], "ok")
        self.assertEqual(len(self.fake.posts()), 1)


class ReopenTest(Sync):
    def test_moved_back_from_done_is_reopened_not_open(self):
        self.fake.add("LOG-9", "Open")
        self.step(snap(card("K-5", "LOG-9", column="done", moved=T0 + 10, moved_from="todo")))
        self.assertEqual(self.posted(), [("LOG-9", "Verified")])
        self.now += 30                       # użytkownik przenosi kartę „do poprawy” chwilę później
        self.step(snap(card("K-5", "LOG-9", column="todo", moved=self.now, moved_from="done")))
        self.assertEqual(self.posted()[-1], ("LOG-9", "Reopened"))

    def test_lost_agent_state_without_move_does_not_reopen(self):
        self.fake.add("LOG-9", "Open")
        self.step(snap(card("K-5", "LOG-9", shown="review", derived={"column": "review"})))
        self.assertEqual(self.posted(), [("LOG-9", "Fixed")])
        # Koordynator wypadł z okna 7 dni (albo start usługi): karta pokazuje zapisane „todo”.
        self.step(snap(card("K-5", "LOG-9", column="todo", derived=None)))
        self.step(snap(card("K-5", "LOG-9", column="todo", derived=None)))
        self.assertEqual(len(self.fake.posts()), 1)
        self.assertEqual(self.engine.status("LOG-9")["status"], "ok")
        # Agent znów pracuje — prawdziwy powrót.
        self.step(snap(card("K-5", "LOG-9", shown="progress", derived={"column": "progress"})))
        self.assertEqual(self.posted()[-1], ("LOG-9", "In Progress"))


class PrecedenceTest(Sync):
    def test_ticket_wins_over_card_but_manual_done_wins(self):
        self.fake.add("LOG-232", "Open")
        boards = [coordinator("claude:a", ticket("LOG-232", "w-toku"))]
        linked = card("K-7", "LOG-232", column="todo", moved=T0, created=T0)
        self.step(snap(linked), boards)
        self.assertEqual(self.posted(), [("LOG-232", "In Progress")], "ticket dokładniejszy niż karta")
        self.assertEqual(self.engine.status("LOG-232")["via"], "ticket")
        boards = [coordinator("claude:a", ticket("LOG-232", "gotowe"))]
        self.step(snap(linked), boards)
        self.assertEqual(self.posted()[-1], ("LOG-232", "Fixed"))
        done = card("K-7", "LOG-232", column="done", moved=self.now, moved_from="review")
        self.step(snap(done), boards)
        self.assertEqual(self.posted()[-1], ("LOG-232", "Verified"), "ręczne Gotowe wygrywa")
        for _ in range(3):   # bez ping-ponga między kartą a ticketem
            self.step(snap(done), boards)
        self.assertEqual(len(self.fake.posts()), 3)

    def test_umbrella_card_syncs_itself_and_tracker_states_are_ignored(self):
        self.fake.add("LOG-100", "Open")
        self.fake.add("LOG-232", "Fixed")
        self.fake.add("LOG-233", "Open")
        boards = [coordinator("claude:a", ticket("LOG-232", "gotowe", source="tracker"),
                              ticket("LOG-233", "review")),
                  coordinator("claude:b", ticket("LOG-234", "w-toku"), tracker="local")]
        self.step(snap(card("K-8", "LOG-100", shown="progress", derived={"column": "progress"})), boards)
        self.assertEqual(sorted(self.posted()), [("LOG-100", "In Progress"), ("LOG-233", "In Progress")],
                         "review AI to W toku; stan z samego YouTracka i plan lokalny się nie liczą")

    def test_ticket_done_card_makes_it_verified_and_rejected_is_wont_fix(self):
        self.fake.add("LOG-1", "Open")
        self.fake.add("LOG-2", "Open")
        boards = [coordinator("claude:a", ticket("LOG-1", "gotowe"), ticket("LOG-2", "odrzucone"))]
        umbrella = card("K-9", "", column="done", derived={"column": "", "coordinator": "claude:a", "tickets": []})
        self.step(snap(umbrella), boards)
        self.assertEqual(sorted(self.posted()), [("LOG-1", "Verified"), ("LOG-2", "Won't fix")])

    def test_freshest_coordinator_wins_for_the_same_ticket(self):
        self.fake.add("LOG-3", "Open")
        boards = [coordinator("claude:old", ticket("LOG-3", "w-toku", activity=T0 - 500)),
                  coordinator("claude:new", ticket("LOG-3", "czeka", activity=T0 + 500))]
        self.step(snap(), boards)
        self.assertEqual(self.posted(), [("LOG-3", "To be discussed")])


class OfflineTest(Sync):
    def test_offline_queue_retry_with_backoff_and_restart(self):
        self.fake.add("LEGAL-35", "Open")
        live = self.config["url"]
        self.config["url"] = "http://127.0.0.1:9"          # zamknięty port — YouTrack niedostępny
        s = snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"}))
        self.step(s)
        status = self.engine.status("LEGAL-35")
        self.assertEqual(status["status"], "offline")
        self.assertEqual(self.engine.decorate(s)["youtrack"]["available"], False)
        self.assertEqual(self.step(s, wait=1.0), [], "ponowienie dopiero po przerwie")
        self.config["url"] = live
        self.now += ytsync.BACKOFF_S[0]
        self.step(s, wait=0)
        self.assertEqual(self.posted(), [("LEGAL-35", "In Progress")])
        self.assertEqual(self.engine.decorate(s)["youtrack"]["available"], True)
        # Kolejka przeżywa restart: przejście zapamiętane w pliku idzie po starcie, gdy YouTrack wróci.
        self.config["url"] = "http://127.0.0.1:9"
        review = snap(card("K-16", "LEGAL-35", shown="review", derived={"column": "review"}))
        self.step(review)
        again = self.make_engine()
        self.config["url"] = live
        self.step(review, engine=again)
        self.assertEqual(self.posted()[-1], ("LEGAL-35", "Fixed"))

    def test_server_errors_and_refused_states(self):
        self.fake.add("LEGAL-35", "Open")
        self.fake.status = 503
        s = snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"}))
        self.step(s)
        self.assertEqual(self.engine.status("LEGAL-35")["status"], "offline")
        self.fake.status = 200
        self.fake.workflow.remove("In Progress")          # inny workflow — YouTrack odmawia stanu
        self.now += 60
        self.step(s, wait=0)
        status = self.engine.status("LEGAL-35")
        self.assertEqual(status["status"], "error")
        self.assertIn("In Progress", status["text"])
        calls = len(self.fake.calls)
        self.step(s, wait=60)
        self.assertEqual(len(self.fake.calls), calls, "odmowa — ponowienie najwcześniej po 30 min")

    def test_token_never_leaks(self):
        self.fake.add("LEGAL-35", "Open")
        out = io.StringIO()
        s = snap(card("K-16", "LEGAL-35", shown="progress", derived={"column": "progress"}))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.fake.status = 401
            self.step(s)
            self.config["url"] = "http://127.0.0.1:9"
            self.now += 400
            self.step(s)
            self.engine.fetch_import()
        texts = [out.getvalue(), json.dumps(self.engine.decorate(s)), json.dumps(self.engine.status("LEGAL-35")),
                 json.dumps(self.engine.imported)]
        if os.path.exists(self.state_file):
            with open(self.state_file, encoding="utf-8") as fh:
                texts.append(fh.read())
        for text in texts:
            self.assertNotIn(TOKEN, text)
        self.assertTrue(all(auth == "Bearer " + TOKEN for _m, _p, auth, _b in self.fake.calls))
        self.assertTrue(all(TOKEN not in path for _m, path, _a, _b in self.fake.calls))


class ConfigTest(Sync):
    def test_state_mapping_from_settings_and_per_project(self):
        path = os.path.join(self.tmp, "youtrack.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"url": "http://yt:8111", "sync": True, "states": {"review": "In Review", "bogus": "x", "done": 5},
                       "projects": {"log": {"states": {"progress": "Develop"}}, "bad key": {}},
                       "importQueries": ["for: me"], "importCheckMinutes": 0}, fh)
        settings = yt.load_sync_settings(path)
        self.assertEqual(settings["states"]["review"], "In Review")
        self.assertEqual(settings["states"]["done"], "Verified", "zła wartość — domyślna")
        self.assertNotIn("bogus", settings["states"])
        self.assertEqual(list(settings["projects"]), ["LOG"])
        self.assertEqual(yt.states_for(settings, "LOG")["progress"], "Develop")
        self.assertEqual(yt.states_for(settings, "LOG")["review"], "In Review")
        self.assertEqual(yt.states_for(settings, "LEGAL")["progress"], "In Progress")
        self.assertEqual(settings["importQueries"], ["for: me"])
        self.assertEqual(settings["importCheckMinutes"], 0)
        self.assertEqual(yt.load_config(path)["url"], "http://yt:8111", "ustawienia odczytu bez zmian")
        defaults = yt.load_sync_settings(os.path.join(self.tmp, "brak.json"))
        self.assertEqual(defaults["states"], yt.DEFAULT_STATES)
        self.assertTrue(defaults["sync"])
        self.assertEqual(yt.config_path({"TIDE_YOUTRACK_CONFIG": "/x.json", "HOME": "/h"}), "/x.json")

        self.fake.workflow.append("In Review")
        self.fake.add("LEGAL-35", "Open")
        self.settings = dict(settings)
        self.step(snap(card("K-16", "LEGAL-35", shown="review", derived={"column": "review"})))
        self.assertEqual(self.posted(), [("LEGAL-35", "In Review")])

    def test_issue_keys(self):
        self.assertEqual(yt.issue_key(" legal-35 "), "LEGAL-35")
        self.assertEqual(yt.issue_key("http://10.20.1.159:8111/issue/LOG-232/opis"), "LOG-232")
        for bad in ("", "K-16x", "LOG-0", "LOG", "../LOG-1", "LOG-1/../x"):
            self.assertEqual(yt.issue_key(bad), "", bad)
        with self.assertRaises(yt.YouTrackError):
            yt.fetch_issue(self.fake.url, "../admin", TOKEN)
        self.assertEqual(self.fake.calls, [])


class ImportListTest(Sync):
    def test_mine_dedupes_marks_linked_and_new(self):
        self.fake.add("LOG-1", "Open", summary="Eksport CSV", description="Opis\r\nz linią", mine="assignee")
        self.fake.add("LOG-2", "Submitted", summary="Zgłoszone przeze mnie", mine="reporter")
        self.fake.add("LOG-3", "Fixed", mine="assignee")                # rozwiązane — poza listą
        self.fake.add("LEGAL-4", "Open", mine="other")                  # cudze
        self.fake.add("APP5-1", "Open", mine="reporter")                 # projekt bez odpowiednika na tablicy
        result = self.engine.fetch_import()
        self.assertEqual(result["error"], "")
        self.assertEqual(sorted(i["id"] for i in result["issues"]), ["APP5-1", "LOG-1", "LOG-2"])
        s = snap(card("K-3", "log-2"))
        items = {i["id"]: i for i in self.engine.import_list(s)}
        self.assertEqual(items["LOG-1"]["boardProject"], "logtool")
        self.assertEqual(items["APP5-1"]["boardProject"], "")
        self.assertEqual(items["LOG-2"]["card"], "K-3")
        self.assertFalse(items["LOG-2"]["new"], "już na tablicy")
        self.assertTrue(items["LOG-1"]["new"])
        self.assertEqual(items["LOG-1"]["url"], self.fake.url + "/issue/LOG-1")
        self.assertEqual(self.engine.new_count(s), 2)
        self.engine.mark_seen(["LOG-1", "APP5-1"])
        self.assertEqual(self.engine.new_count(s), 0)
        self.assertEqual(self.make_engine().seen, {"LOG-1", "APP5-1"}, "przejrzane przeżywają restart")
        issue = self.engine.issue_for_import("LOG-1")
        self.assertEqual(ytsync.card_title({"summary": "a\x07\n  b", "id": "X-1"}), "a b")
        self.assertEqual(ytsync.card_title({"summary": "", "id": "X-1"}), "X-1")
        self.assertEqual(ytsync.card_description(issue), "Opis\nz linią")
        long = ytsync.card_description({"description": "ż" * 40_000})
        self.assertLessEqual(len(long.encode("utf-8")), 60 * 1024 + 100)
        self.assertTrue(long.endswith("(opis skrócony — całość w YouTracku)"))
        queries = [p for p in self.fake.gets() if p.startswith("/api/issues?")]
        self.assertEqual(len(queries), 2, "dwa zapytania: przypisane do mnie i moje bez przypisania")


if __name__ == "__main__":
    unittest.main()
