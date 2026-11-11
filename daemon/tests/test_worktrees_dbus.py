"""Cienki test kontraktu obszaru worktrees: te same dane co z rdzenia wychodzą przez D-Bus,
a Remove() zwraca wynik dla każdej ścieżki.

Usługa działa na prywatnej magistrali (privatebus.py) i na tymczasowych repozytoriach (gitrepos.py).
Agent jest zasymulowany sesją Claude Code z katalogiem roboczym w worktree „agent”.
Open() jest wołane tylko z argumentami, które nic nie uruchamiają.
"""
import json
import os
import shutil
import tempfile
import unittest

from . import gitrepos
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from tide_daemon.agents.timeutil import parse_ts
from tide_daemon.worktrees import SessionIndex, scan

try:
    from PyQt6.QtCore import QMetaType
    from PyQt6.QtDBus import QDBusArgument, QDBusMessage
except ImportError:  # pragma: no cover
    pass

PATH, IFACE = "/Worktrees", "org.tide.Daemon.Worktrees"
NOW_TEXT = "2026-10-26T12:00:00Z"


def write_agent_session(claude_dir: str, cwd: str) -> None:
    project = os.path.join(claude_dir, "projects", cwd.replace("/", "-"))
    os.makedirs(project)
    with open(os.path.join(project, "s-agent.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": "2026-10-26T11:59:00.000Z", "cwd": cwd,
                             "message": {"role": "user", "content": "hej"}}) + "\n")


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class WorktreesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-wt-dbus-")
        cls.root = os.path.join(cls.tmp, "work")
        cls.p = gitrepos.build(cls.root)
        # Do usuwania wymuszonego: develop w osobnym worktree (gałąź główna — nigdy) i drugi
        # niescalony worktree, którego gałąź zniknie tylko z deleteUnmergedBranches.
        cls.p["develop"] = os.path.join(cls.root, "alpha-wt", "develop")
        gitrepos.git(cls.p["alpha"], "worktree", "add", "-q", "-b", "develop", cls.p["develop"])
        gitrepos.commit(cls.p["develop"], "d.txt")
        cls.p["extra"] = os.path.join(cls.root, "alpha-wt", "extra")
        gitrepos.git(cls.p["alpha"], "worktree", "add", "-q", "-b", "feat/extra", cls.p["extra"])
        gitrepos.commit(cls.p["extra"], "e.txt")
        cls.agent_cwd = os.path.join(cls.p["agent"], "src")
        write_agent_session(os.path.join(cls.tmp, "claude"), cls.agent_cwd)
        cls.service = PrivateBus("tide-worktrees-test")
        # Nigdy prawdziwe okno z katalogu domowego — ShowWindow() ma tu zawsze odpowiadać False.
        cls.service.env["TIDE_WORKTREES_WINDOW"] = os.path.join(cls.tmp, "brak-okna.py")
        cls.daemon = cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--now", NOW_TEXT, "--workspace", cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, member, *args, timeout=10000):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args, timeout=timeout)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0] if reply.arguments() else None

    def listed(self):
        return json.loads(self.call("List"))

    def measured(self):
        items = self.listed()
        return items if items and all(w["size"] >= 0 or w["missing"] for w in items) else None

    def test_1_list_matches_core(self):
        over_dbus = wait_until(self.measured, timeout=15)
        self.assertTrue(over_dbus, "lista z rozmiarami nie pojawiła się")
        index = SessionIndex(os.path.join(self.tmp, "claude"), os.path.join(self.tmp, "codex"))
        self.assertEqual(over_dbus, scan(self.root, [self.agent_cwd], now=parse_ts(NOW_TEXT), sessions=index))
        agent = next(w for w in over_dbus if w["path"] == self.p["agent"])
        self.assertTrue(agent["hasAgent"], "sesja agenta z obszaru Agents oznacza worktree")
        self.assertEqual((agent["group"], agent["tool"], agent["sessionTitle"]), ("active", "claude", "hej"))
        groups = {os.path.basename(w["path"]): w["group"] for w in over_dbus}
        self.assertEqual((groups["merged"], groups["unmerged"], groups["alpha"]), ("clean", "decide", "main"))

    def test_2_remove_reports_each_path(self):
        wait_until(self.measured, timeout=15)
        ask = [self.p["merged"], self.p["dirty"], self.p["agent"], self.p["alpha"]]
        as_paths = QDBusArgument(ask, QMetaType.Type.QStringList.value)  # sygnatura „as”, nie „av”
        results = json.loads(self.call("Remove", as_paths, timeout=60000))
        self.assertEqual([(r["path"], r["removed"], r["reason"]) for r in results], [
            (self.p["merged"], True, ""),
            (self.p["dirty"], False, "dirty"),
            (self.p["agent"], False, "agent"),
            (self.p["alpha"], False, "main"),
        ])
        self.assertTrue(results[0]["branchDeleted"])
        self.assertFalse(os.path.exists(self.p["merged"]))
        gone = wait_until(lambda: self.p["merged"] not in {w["path"] for w in self.listed()})
        self.assertTrue(gone, "lista po sprzątaniu nie została odświeżona")

    def test_3_refresh_on_demand(self):
        wait_until(self.measured, timeout=15)
        with open(os.path.join(self.p["detached"], "nowy.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.call("Refresh")
        dirty = wait_until(lambda: next((w["dirty"] for w in self.listed() if w["path"] == self.p["detached"]), 0))
        self.assertEqual(dirty, 1)

    def remove_forced(self, paths, delete_unmerged, confirmed=""):
        as_paths = QDBusArgument(paths, QMetaType.Type.QStringList.value)
        return json.loads(self.call("RemoveForced", as_paths, delete_unmerged, confirmed, timeout=60000))

    def test_4_remove_forced_keeps_unmerged_branch_and_refuses_hard_blockers(self):
        wait_until(self.measured, timeout=15)
        listed = {w["path"]: w for w in self.listed()}
        self.assertEqual((listed[self.p["unmerged"]]["forceRemovable"], listed[self.p["unmerged"]]["unpushed"]),
                         (True, 2))
        self.assertEqual(listed[self.p["develop"]]["forceBlocker"], "protected")
        self.assertEqual(listed[self.p["agent"]]["forceBlocker"], "agent")
        ask = [self.p["unmerged"], self.p["topic"], self.p["agent"], self.p["alpha"], self.p["develop"]]
        confirmed = {p: {k: listed[p][k] for k in ("dirty", "unmerged", "unpushed", "branch")} for p in ask}
        results = self.remove_forced(ask, False, json.dumps(confirmed))
        self.assertEqual([(r["path"], r["status"], r["reason"]) for r in results], [
            (self.p["unmerged"], "branch-kept", ""),
            (self.p["topic"], "branch-kept", ""),
            (self.p["agent"], "refused", "agent"),
            (self.p["alpha"], "refused", "main"),
            (self.p["develop"], "refused", "protected"),
        ])
        self.assertTrue(all(r["forced"] for r in results))
        self.assertIn("2 commity poza master", results[0]["branchMessage"])
        self.assertFalse(os.path.exists(self.p["unmerged"]))
        self.assertIn("feat/unmerged", gitrepos.branches(self.p["alpha"]))
        for kept in ("agent", "alpha", "develop"):
            self.assertTrue(os.path.isdir(self.p[kept]), kept)
        self.assertIn("develop", gitrepos.branches(self.p["alpha"]))

    def test_5_remove_forced_deletes_unmerged_branch_only_on_request(self):
        wait_until(self.measured, timeout=15)
        [result] = self.remove_forced([self.p["extra"]], True)
        self.assertEqual((result["status"], result["branchDeleted"]), ("removed", True))
        self.assertNotIn("feat/extra", gitrepos.branches(self.p["alpha"]))
        # Ta sama flaga przy gałęzi głównej i głównym katalogu nie zmienia niczego.
        results = self.remove_forced([self.p["develop"], self.p["alpha"]], True)
        self.assertEqual([r["reason"] for r in results], ["protected", "main"])
        self.assertIn("develop", gitrepos.branches(self.p["alpha"]))
        self.assertIn("master", gitrepos.branches(self.p["alpha"]))

    def test_6_remove_forced_rejects_bad_confirmation(self):
        results = self.remove_forced([self.p["detached"]], False, "nie json")
        self.assertEqual([(r["status"], r["reason"]) for r in results], [("refused", "bad-request")])
        self.assertTrue(os.path.isdir(self.p["detached"]))

    def test_old_remove_contract_still_refuses_forceable_worktrees(self):
        # Remove() bez zmian: worktree ze zmianami (który RemoveForced by usunął) nie jest sprzątany.
        as_paths = QDBusArgument([self.p["dirty"]], QMetaType.Type.QStringList.value)
        [result] = json.loads(self.call("Remove", as_paths, timeout=60000))
        self.assertEqual((result["removed"], result["reason"], result["status"], result["forced"]),
                         (False, "dirty", "refused", False))
        self.assertTrue(os.path.isdir(self.p["dirty"]))

    def test_open_refuses_unknown_targets(self):
        self.assertIs(self.call("Open", os.path.join(self.root, "nie-ma"), "terminal"), False)
        self.assertIs(self.call("Open", self.p["alpha"], "nieznany-program"), False)

    def test_launchers_and_introspection(self):
        self.assertEqual(set(json.loads(self.call("Launchers"))), {"terminal", "rider", "window"})
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        self.assertIn('<arg name="paths" type="as" direction="in"/>', xml)
        self.assertIn('<signal name="Changed"', xml)
        self.assertIn('<method name="ShowWindow">', xml)
        self.assertIn('<arg name="repoPath" type="s" direction="in"/>', xml)
        self.assertIn('<method name="Remove">', xml)
        self.assertIn('<method name="RemoveForced">', xml)
        self.assertIn('<arg name="deleteUnmergedBranches" type="b" direction="in"/>', xml)
        self.assertIn('<arg name="confirmed" type="s" direction="in"/>', xml)

    def test_show_window_without_installed_window(self):
        # Usługa w teście nie widzi zainstalowanego okna (HOME testu nie ma launchera).
        self.assertIs(self.call("ShowWindow"), False)

    def test_fetch_is_in_introspection_and_refuses_unknown_or_remoteless_repository(self):
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn('<method name="Fetch">', xml)
        outside = json.loads(self.call("Fetch", os.path.join(self.root, "nie-ma"), timeout=30000))
        self.assertEqual((outside["ok"], outside["reason"]), (False, "bad-request"))
        traversal = json.loads(self.call("Fetch", os.path.join(self.p["alpha"], "..", ".."), timeout=30000))
        self.assertEqual((traversal["ok"], traversal["reason"]), (False, "bad-request"))
        # alpha jest na liście, ale nie ma zdalnego: odmowa bez sieci (odpowiedź z opóźnieniem).
        remoteless = json.loads(self.call("Fetch", self.p["alpha"], timeout=30000))
        self.assertEqual((remoteless["ok"], remoteless["reason"]), (False, "no-remote"))

    def test_prune_refuses_unknown_repository(self):
        result = json.loads(self.call("Prune", os.path.join(self.root, "nie-ma"), timeout=30000))
        self.assertEqual((result["ok"], result["pruned"]), (False, []))


if __name__ == "__main__":
    unittest.main()
