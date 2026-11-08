"""Backend widoku i zainstalowane CLI korzystają z rzeczywistego publicznego /Issues."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from daemon.tests import test_issues_dbus as base
from daemon.tests.privatebus import requires_dbus, wait_until
from .fixture import PNG


@requires_dbus
class BackendDbusTest(unittest.TestCase):
    start = base.IssuesDbusTest.start
    call = base.IssuesDbusTest.call
    run_cli = base.IssuesDbusTest.run_cli
    board_call = base.IssuesDbusTest.board_call

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from apps.issues import backend
        cls.module = backend

    def setUp(self):
        base.IssuesDbusTest.setUp(self)
        self.start()
        self.run_cli("issue", "create", "--report", self.report, "--title", "Pierwszy")
        self.backend = self.module.Backend(self.bus.bus)
        self.addCleanup(self.close_backend)
        self.assertTrue(wait_until(lambda: self.backend.available and len(self.state()["issues"]) == 1))
        self.backend.selectIssue("S-1")
        self.assertTrue(wait_until(lambda: self.state()["issue"] is not None))

    def close_backend(self):
        self.backend._timer.stop()
        wait_until(lambda: not self.backend._pending, timeout=5)
        self.backend.deleteLater()
        self.app.processEvents()

    def state(self):
        return json.loads(self.backend.stateJson)

    def version(self, number):
        self.assertTrue(wait_until(lambda: not self.state()["busy"] and
            self.state()["issue"]["version"] == number), self.backend.stateJson)

    def test_metadata_lifecycle_empty_manifest_and_server_validation(self):
        self.board_call("UpsertProject", '{"name":"Tide","slug":"plasma-tide"}')
        original = self.state()["issue"]["report"]
        self.backend.updateDraft("project", "plasma-tide")
        self.backend.save()
        self.version(2)
        self.assertEqual(self.state()["issue"]["report"], original)
        self.assertEqual(self.state()["issue"]["project"], "plasma-tide")
        self.backend.updateDraft("project", "")
        self.backend.save()
        self.version(3)
        self.assertIsNone(self.call("Show", "S-1")["issue"]["project"])
        self.backend.updateDraft("project", "nieznany")
        self.backend.save()
        self.assertTrue(wait_until(lambda: not self.state()["busy"] and bool(self.state()["notice"])))
        self.assertEqual(self.call("Show", "S-1")["issue"]["version"], 3)
        self.assertEqual(self.state()["draft"]["project"], "nieznany")
        self.backend.updateDraft("project", "")
        self.backend.updateDraft("report", "Opisany brak obrazu; świadomie przygotowuję raport tekstowy.")
        self.backend.save()
        self.version(4)
        self.backend.transition("Submit")
        self.version(5)
        self.assertEqual(self.state()["issue"]["state"], "ready")
        self.backend.updateDraft("title", "Poprawiony")
        self.backend.save()
        self.version(6)
        self.assertEqual(self.state()["issue"]["state"], "draft")
        self.backend.transition("Submit")
        self.version(7)
        self.backend.transition("Reject")
        self.version(8)
        self.assertEqual(self.state()["issue"]["state"], "rejected")
        self.backend.transition("Reopen")
        self.version(9)
        self.assertEqual(self.call("Show", "S-1")["issue"]["state"], "draft")

    def test_accept_uses_rendered_version_conflicts_then_shares_card_with_cli(self):
        self.backend.transition("Submit")
        self.version(2)
        self.run_cli("issue", "update", "S-1", "--expected-version", "2", "--title", "Nowszy raport")
        self.backend.acceptIssue(2)
        self.assertTrue(wait_until(lambda: self.state()["conflict"] == 3))
        self.assertEqual(self.state()["issue"]["version"], 2)
        self.assertEqual(self.board_call("Snapshot")["cards"], [])
        self.backend.reloadSelected()
        self.version(3)
        self.backend.transition("Submit")
        self.version(4)
        self.backend.acceptIssue(4)
        self.version(5)
        self.assertEqual(self.state()["issue"]["card"], "K-1")
        replay = self.run_cli("backlog", "S-1", "--expected-version", "4", "--request-id", "cli-po-przycisku")
        self.assertTrue(replay["alreadyAccepted"])
        self.assertEqual(replay["card"], "K-1")
        self.assertEqual(len(self.board_call("Snapshot")["cards"]), 1)

    def test_conflict_keeps_local_report_and_reload_requires_explicit_action(self):
        self.backend.updateDraft("report", "Lokalnie wpisany raport")
        current = self.run_cli("issue", "update", "S-1", "--expected-version", "1", "--title", "Równoległy zapis")["issue"]
        self.backend.save()
        self.assertTrue(wait_until(lambda: self.state()["current"] is not None))
        self.assertEqual(self.state()["conflict"], 2)
        self.assertEqual(self.state()["draft"]["report"], "Lokalnie wpisany raport")
        self.assertEqual(self.state()["current"], current)
        self.backend.save()
        self.assertEqual(self.call("Show", "S-1")["issue"], current)
        self.backend.reloadSelected()
        self.assertTrue(self.state()["busy"])
        self.backend.updateDraft("report", "Nie zmieniaj podczas wczytywania")
        self.version(2)
        self.assertFalse(self.state()["dirty"])
        self.assertEqual(self.state()["draft"]["report"], current["report"])

    def test_attachments_public_bytes_hash_safe_open_detach_and_broken_copy(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtQml import QQmlEngine
        engine = QQmlEngine()
        self.backend.install_image_provider(engine)
        source = Path(self.tmp, "dowod.png")
        source.write_bytes(PNG)
        self.backend.attach(QUrl.fromLocalFile(str(source)))
        self.version(2)
        self.assertTrue(wait_until(lambda: len(self.backend._images) == 1))
        issue = self.state()["issue"]
        entry = issue["attachments"][0]
        source.unlink()
        received = []
        self.backend._read(issue, entry, lambda result, raw: received.append((result, raw)))
        self.assertTrue(wait_until(lambda: bool(received)))
        self.assertTrue(received[0][0]["ok"])
        self.assertEqual(received[0][1], PNG)
        with mock.patch.object(self.module.QDesktopServices, "openUrl", return_value=True) as opened:
            self.assertEqual(opened.call_count, 0)
            self.backend.openAttachment(entry["id"])
            self.assertTrue(wait_until(lambda: opened.call_count == 1))
            path = Path(opened.call_args.args[0].toLocalFile())
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        # Dowiązanie kopii nie może posłużyć ani do podglądu, ani do otwarcia.
        copy = Path(self.board, "issues/S-1", entry["path"])
        copy.unlink()
        copy.symlink_to(self.report)
        with mock.patch.object(self.module.QDesktopServices, "openUrl") as opened:
            self.backend.openAttachment(entry["id"])
            self.assertTrue(wait_until(lambda: bool(self.state()["notice"])))
            opened.assert_not_called()
        self.backend.transition("Submit")
        self.assertTrue(wait_until(lambda: not self.state()["busy"] and bool(self.state()["notice"])))
        self.assertEqual(self.call("Show", "S-1")["issue"]["version"], 2)
        self.backend.detach(entry["id"])
        self.version(3)
        self.assertEqual(self.state()["issue"]["attachments"], [])
        self.backend.transition("Submit")
        self.version(4)
        self.assertEqual(self.state()["issue"]["state"], "ready")

    def test_html_and_svg_open_only_as_text_and_bad_source_is_refused(self):
        from PyQt6.QtCore import QUrl
        source = Path(self.tmp, "dowod.svg")
        raw = b'<svg onload="alert(1)"><script>bad()</script></svg>'
        source.write_bytes(raw)
        self.backend.attach(QUrl.fromLocalFile(str(source)))
        self.version(2)
        entry = self.state()["issue"]["attachments"][0]
        self.assertEqual(self.backend._images, {})
        with mock.patch.object(self.module.QDesktopServices, "openUrl", return_value=True) as opened:
            self.backend.openAttachment(entry["id"])
            self.assertTrue(wait_until(lambda: opened.call_count == 1))
            path = Path(opened.call_args.args[0].toLocalFile())
            self.assertEqual(path.suffix, ".txt")
            self.assertEqual(path.read_bytes(), raw)
        link = Path(self.tmp, "link")
        link.symlink_to(source)
        self.backend.attach(QUrl.fromLocalFile(str(link)))
        self.assertEqual(self.call("Show", "S-1")["issue"]["version"], 2)
        self.assertTrue(self.state()["notice"])

    def test_cli_open_reaches_existing_window_and_selects_exact_issue(self):
        from widgets.stage import stage_app
        self.run_cli("issue", "create", "--report", self.report, "--title", "Drugi")
        package = str(Path(self.tmp, "window"))
        stage_app(str(base.REPO / "apps/tide"), package)
        helper = str(Path(__file__).with_name("window_probe.py"))
        self.bus.spawn(["/usr/bin/python3", helper, package], "org.tide.Window",
                       extra_env=dict(self.env, QT_QPA_PLATFORM="offscreen"))
        reply = self.run_cli("issue", "open", "S-2")
        self.assertTrue(reply["ok"])
        def selected():
            reply = self.bus.call("org.tide.Window", "/Probe", "org.tide.Test.Window", "Selected")
            return reply.arguments() == ["S-2"]
        self.assertTrue(wait_until(selected))
        self.assertEqual(self.run_cli("issue", "open", "S-99", code=5)["error"], "not-found")
        self.assertTrue(selected())

    def test_missing_broken_and_disappearing_service_are_readable(self):
        self.backend.selectIssue("S-99")
        self.assertTrue(wait_until(lambda: "S-99" in self.state()["error"]))
        self.backend.selectIssue("S-1")
        self.assertTrue(wait_until(lambda: self.state()["issue"] is not None))
        self.backend.updateDraft("report", "Zachowany po awarii")
        Path(self.board, "issues/S-1/attachments.json").write_text("broken")
        self.backend.reload()
        self.assertTrue(wait_until(lambda: bool(self.state()["listErrors"])))
        self.assertEqual(self.state()["listErrors"][0]["id"], "S-1")
        self.backend.selectIssue("S-1")
        self.assertTrue(wait_until(lambda: bool(self.state()["error"])))
        self.assertEqual(self.state()["draft"]["report"], "Zachowany po awarii")
        self.proc.terminate()
        self.proc.wait(10)
        self.assertTrue(wait_until(lambda: not self.backend.available))
        self.assertEqual(self.state()["draft"]["report"], "Zachowany po awarii")
