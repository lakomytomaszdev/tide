"""`Git.Diff` (K-36, PT-357) przez D-Bus na prywatnej magistrali: karta uruchomiona „Od razu” z atrapą agenta,
diff jej gałęzi od przypiętej bazy, bez zapisu do dziennika i bez zmiany refów."""
import json
import time

from .privatebus import SERVICE, requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase, git
from tide_daemon.gitread.diffparse import parse_diff

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    pass

GIT_PATH, GIT_IFACE = "/Git", "org.tide.Daemon.Git"


@requires_dbus
class GitDiffDbusTest(FlowCase):
    def diff(self, **request):
        return self.call("Diff", json.dumps(request), path=GIT_PATH, iface=GIT_IFACE)

    def test_card_diff_over_dbus_from_pinned_base(self):
        intro = self.service.call(SERVICE, GIT_PATH, "org.freedesktop.DBus.Introspectable", "Introspect")
        self.assertEqual(intro.type(), QDBusMessage.MessageType.ReplyMessage, intro.errorMessage())
        self.assertIn('<method name="Diff">', intro.arguments()[0])

        card_id = self.card()
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        branch = reply["cardBranch"]
        self.wait_column(card_id, "review")
        events = len(self.events(card_id))
        refs = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")

        data = self.diff(source="card", card=card_id)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["from"]["sha"], self.master)
        self.assertEqual(data["from"]["ref"], "refs/heads/master")
        self.assertEqual(data["to"]["ref"], f"refs/heads/{branch}")
        self.assertEqual(data["to"]["sha"], git(self.repo, "rev-parse", branch).strip())
        expected = parse_diff(git(self.repo, "diff", self.master, branch))
        self.assertEqual((data["files"], data["totals"]), (expected["files"], expected["totals"]))
        self.assertIn("ATRAPA.md", [f["path"] for f in data["files"]])
        numstat = git(self.repo, "diff", "--numstat", self.master, branch).splitlines()
        self.assertEqual(data["totals"]["files"], len(numstat))
        self.assertFalse(data["truncated"])

        target = self.diff(source="card", card=card_id, targetRef="refs/heads/master")
        self.assertEqual(target["from"]["sha"], self.master)
        self.assertEqual(self.diff(source="card", card=card_id, targetRef="nie-ma")["error"], "bad-request")
        self.assertEqual(self.diff(source="card", card="K-999")["error"], "unknown-card")
        self.assertEqual(self.diff(source="nieznane")["error"], "bad-request")
        # PT-349: źródła `ticket`, `commit`, `range` są zarejestrowane, a `repo` spoza list odrzuca walidacja.
        self.assertEqual(self.diff(source="ticket", card=card_id, ticket="PT-9999")["error"], "bad-request")
        self.assertEqual(self.diff(source="commit", repo="/etc", sha="HEAD")["error"], "bad-request")
        self.assertEqual(self.diff(source="range", repo=f"{self.repo}/..", **{"from": "a", "to": "b"})["error"],
                         "bad-request")

        # Repozytorium projektu Tablicy (lista z app.known_git_repos): `commit` i `range` przechodzą walidację.
        head = git(self.repo, "rev-parse", "HEAD").strip()
        commit = self.diff(source="commit", repo=str(self.repo), sha="HEAD")
        self.assertTrue(commit["ok"], commit)
        self.assertEqual(commit["to"]["sha"], head)
        ranged = self.diff(source="range", repo=str(self.repo), **{"from": "master", "to": branch})
        self.assertTrue(ranged["ok"], ranged)
        self.assertEqual((ranged["files"], ranged["totals"]), (data["files"], data["totals"]))

        self.assertEqual(len(self.events(card_id)), events)
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"), refs)
        # Pętla usługi odpowiada dalej na inne wywołania (odpowiedź Diff jest opóźniona, z puli wątków).
        self.assertTrue(self.call("Snapshot")["cards"])

    def settled(self, card_id, quiet=2.0, timeout=30.0) -> int:
        """Liczba zdarzeń dziennika karty, gdy przez `quiet` sekund nic nie przybyło."""
        last, since, deadline = -1, time.monotonic(), time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            count = len(self.events(card_id))
            if count != last:
                last, since = count, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return count
            time.sleep(.1)
        self.fail(f"{card_id}: dziennik karty nie ustał w {timeout:g} s")

    def branch_map(self, request):
        return self.call("BranchMap", json.dumps(request), path=GIT_PATH, iface=GIT_IFACE)

    def test_branch_map_over_dbus_from_real_card_journal(self):
        """`Git.BranchMap` (K-99, PT-387): projekty Tablicy i mapa gałęzi karty uruchomionej „Od razu”."""
        intro = self.service.call(SERVICE, GIT_PATH, "org.freedesktop.DBus.Introspectable", "Introspect")
        self.assertIn('<method name="BranchMap">', intro.arguments()[0])
        listed = self.branch_map({})
        self.assertTrue(listed["ok"], listed)
        self.assertEqual([(p["slug"], p["repo"], p["cards"]) for p in listed["projects"]], [("proj", self.repo, 0)])
        empty = self.branch_map({"project": "proj"})
        self.assertEqual((empty["ok"], empty["nodes"], empty["edges"], empty["total"]), (True, [], [], 0))

        card_id = self.card()
        reply = self.start(card_id)
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "review")
        # Silnik dopisuje jeszcze zdarzenia po wejściu karty do Do sprawdzenia (domknięcie, sprzątanie): dziennik
        # i refy porównujemy dopiero po domknięciu i ustaniu zapisów, żeby mierzyć tylko skutek odczytu mapy.
        self.assertTrue(wait_until(lambda: "card.closed" in {e["type"] for e in self.events(card_id)},
                                   timeout=30), self.events(card_id))
        events = self.settled(card_id)
        refs = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")

        data = self.branch_map({"project": "proj"})
        self.assertTrue(data["ok"], data)
        self.assertNotIn("mainRef", data)
        self.assertEqual((data["project"], data["repo"], data["total"], data["truncated"]), ("proj", self.repo, 2, False))
        nodes = {n["id"]: n for n in data["nodes"]}
        card = nodes[f"card:{card_id}"]
        # Atrapa agenta prowadzi kartę „Od razu” do końca: ticket scalony do gałęzi karty (i sprzątnięty), a krok
        # `close` zapisuje `card.closed` — karta domknięta, jeszcze nie scalona do głównej.
        events_by_type = {e["type"] for e in self.events(card_id)}
        self.assertIn("card.closed", events_by_type)
        self.assertEqual((card["branch"], card["state"], card["title"]), (reply["cardBranch"], "closed", "Eksport CSV"))
        self.assertNotIn("merged", card)
        self.assertEqual((card["started"]["ref"], card["started"]["sha"]), ("refs/heads/master", self.master))
        ticket = nodes[f"ticket:{card_id}:{reply['ticket']}"]
        self.assertEqual((ticket["branch"], ticket["state"], ticket["started"]["ref"]),
                         (reply["branch"], "merged", f"refs/heads/{reply['cardBranch']}"))
        self.assertEqual(ticket["merged"]["targetRef"], f"refs/heads/{reply['cardBranch']}")
        self.assertEqual(nodes["base:refs/heads/master"]["kind"], "base")
        self.assertIn({"kind": "start", "from": "base:refs/heads/master", "to": card["id"]}, data["edges"])
        self.assertIn({"kind": "start", "from": card["id"], "to": ticket["id"]}, data["edges"])
        self.assertIn({"kind": "merge", "from": ticket["id"], "to": card["id"]}, data["edges"])
        self.assertEqual(self.branch_map({})["projects"][0]["cards"], 1)

        self.assertEqual(self.branch_map({"project": "brak"})["error"], "unknown-project")
        bad = self.call("BranchMap", "nie json", path=GIT_PATH, iface=GIT_IFACE)
        self.assertEqual((bad["ok"], bad["error"]), (False, "bad-request"))
        self.assertEqual(len(self.events(card_id)), events)
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"), refs)
        self.assertTrue(self.call("Snapshot")["cards"])
