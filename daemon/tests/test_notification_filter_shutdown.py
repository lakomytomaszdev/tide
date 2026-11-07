"""Zamykanie filtra Stream, gdy odpowiedź Inhibit ściga się z końcem pętli Qt (PT-253)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tide_daemon import activity_state
from .privatebus import app


class NotificationShutdownTest(unittest.TestCase):
    def setUp(self):
        app()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bus = mock.Mock()
        self.state = SimpleNamespace(allowed={}, changed=mock.Mock())
        with mock.patch.object(activity_state, "QDBusServiceWatcher"):
            self.filter = activity_state.NotificationFilter(
                self.state, saved=Path(temp.name, "saved.json"), bus=self.bus)
        self.filter._restore = mock.Mock()
        self.filter._allow = mock.Mock()
        reply = SimpleNamespace(type=lambda: activity_state.QDBusMessage.MessageType.ReplyMessage,
                                arguments=lambda: [101])
        patcher = mock.patch.object(activity_state, "QDBusPendingReply",
                                    return_value=SimpleNamespace(reply=lambda: reply))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_consumes_inhibit_once_when_wait_delivers_finished_signal(self):
        watcher = mock.Mock()
        self.filter._pending.add(watcher)
        watcher.waitForFinished.side_effect = lambda: self.filter._inhibited(watcher)
        self.filter.stop()
        self.assertEqual(self.bus.call.call_count, 1, "zamknięcie czeka na jedno UnInhibit")
        self.assertEqual(self.bus.asyncCall.call_count, 0, "callback nie zwalnia tego samego ciasteczka osobno")
        self.assertEqual(self.filter.cookie, 0)
        self.assertFalse(self.filter._pending)

    def test_late_finished_signal_and_repeated_stop_do_not_release_the_cookie_again(self):
        watcher = mock.Mock()
        self.filter._pending.add(watcher)
        self.filter.stop()
        self.filter._inhibited(watcher)  # sygnał już był w kolejce przed synchronicznym odbiorem
        self.filter.stop()
        self.assertEqual(self.bus.call.call_count, 1)
        self.assertEqual(self.bus.asyncCall.call_count, 0)
        self.assertEqual(self.filter.cookie, 0)

    def test_stopped_filter_does_not_accept_a_late_activity_or_server_signal(self):
        self.filter.stop()
        self.state.allowed = {"services": ["test-service"]}
        with mock.patch.object(activity_state, "QDBusPendingCallWatcher"):
            self.filter.update()
            self.filter._server_up(activity_state.NOTIFY[0])
        self.bus.asyncCall.assert_not_called()
        self.filter._allow.assert_not_called()
        self.assertFalse(self.filter._wanted)

    def test_leaving_stream_before_reply_still_releases_asynchronously_once(self):
        watcher = mock.Mock()
        self.filter._pending.add(watcher)
        self.filter.release()
        self.filter._inhibited(watcher)
        self.filter._inhibited(watcher)
        self.assertEqual(self.bus.asyncCall.call_count, 1)
        self.bus.call.assert_not_called()
        self.assertEqual(self.filter.cookie, 0)


if __name__ == "__main__":
    unittest.main()
