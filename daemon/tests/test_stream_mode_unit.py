"""Kolejność odpowiedzi Inhibit przy zamykaniu usługi, bez magistrali pulpitu ani zapisów konfiguracji."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from .privatebus import app
from tide_daemon import activity_state


class NotificationShutdownTest(unittest.TestCase):
    def setUp(self):
        app()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bus = Mock()
        # Podmieniamy wyłącznie transport i obserwację serwera; cały cykl stop/release jest rzeczywisty.
        with patch.object(activity_state, "QDBusServiceWatcher"):
            self.filter = activity_state.NotificationFilter(
                Mock(), saved=Path(self.tmp.name) / "state.json", bus=self.bus)
        self.addCleanup(self.filter.deleteLater)
        self.filter._restore = Mock()  # Żaden kreadconfig/kwriteconfig nie jest uruchamiany.
        reply = Mock()
        reply.type.return_value = activity_state.QDBusMessage.MessageType.ReplyMessage
        reply.arguments.return_value = [101]
        pending = patch.object(activity_state, "QDBusPendingReply")
        pending.start().return_value.reply.return_value = reply
        self.addCleanup(pending.stop)

    def pending_inhibit(self):
        target = self.filter

        class Watcher:
            def waitForFinished(self):
                pass

            def finished(self):
                target._inhibited(self)

        watcher = Watcher()
        target._wanted = True
        target._pending.add(watcher)
        return watcher

    def assert_released_once(self):
        messages = [call.args[0] for call in self.bus.call.call_args_list
                    + self.bus.asyncCall.call_args_list]
        self.assertEqual([message.member() for message in messages], ["UnInhibit"])
        self.assertEqual(self.filter.cookie, 0)
        self.assertFalse(self.filter._pending)
        self.assertFalse(self.filter._wanted)
        self.filter._restore.assert_called_once_with()

    def test_finished_signal_during_wait_does_not_release_the_same_cookie_twice(self):
        watcher = self.pending_inhibit()
        watcher.waitForFinished = watcher.finished
        self.filter.stop()
        self.assert_released_once()

    def test_finished_signal_during_uninhibit_does_not_restore_the_consumed_cookie(self):
        watcher = self.pending_inhibit()
        self.bus.call.side_effect = lambda *args, **kwargs: watcher.finished()
        self.filter.stop()
        self.assert_released_once()

    def test_repeated_stop_after_a_completed_reply_does_not_release_twice(self):
        watcher = self.pending_inhibit()
        watcher.finished()
        self.filter.stop()
        self.filter.stop()
        messages = [call.args[0] for call in self.bus.call.call_args_list
                    + self.bus.asyncCall.call_args_list]
        self.assertEqual([message.member() for message in messages], ["UnInhibit"])
        self.assertEqual(self.filter.cookie, 0)
