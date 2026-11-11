"""`Git.Diff` (K-36, PT-357) przez D-Bus na prywatnej magistrali: karta uruchomiona „Od razu” z atrapą agenta,
diff jej gałęzi od przypiętej bazy, bez zapisu do dziennika i bez zmiany refów."""
import json

from .privatebus import SERVICE, requires_dbus
from .test_cardflow_dbus import FlowCase, git
from tide_daemon.gitread.diffparse import parse_diff

try:
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

        self.assertEqual(len(self.events(card_id)), events)
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"), refs)
        # Pętla usługi odpowiada dalej na inne wywołania (odpowiedź Diff jest opóźniona, z puli wątków).
        self.assertTrue(self.call("Snapshot")["cards"])
