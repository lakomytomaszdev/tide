"""Zatrzymanie filtra: odpowiedź Inhibit wolno odebrać i zwolnić tylko raz, także przy sygnale Qt."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .privatebus import app
from tide_daemon import activity_state


class PendingInhibitionTest(unittest.TestCase):
    def test_blocking_stop_consumes_pending_reply_once_and_ignores_late_finished_signal(self):
        app()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(activity_state, "QDBusServiceWatcher"):
            bus = mock.Mock()
            state = SimpleNamespace(changed=mock.Mock())
            filter_ = activity_state.NotificationFilter(state, Path(root) / "saved.json", bus)
            filter_._wanted = True
            watcher = mock.Mock()
            filter_._pending.add(watcher)
            connected = [True]
            watcher.finished.disconnect.side_effect = lambda: connected.__setitem__(0, False)

            def complete():
                if connected[0]:
                    filter_._inhibited(watcher)

            watcher.waitForFinished.side_effect = complete
            reply = mock.Mock()
            reply.type.return_value = activity_state.QDBusMessage.MessageType.ReplyMessage
            reply.arguments.return_value = [123]
            with mock.patch.object(activity_state, "QDBusPendingReply") as pending:
                pending.return_value.reply.return_value = reply
                filter_.stop()
                bus.call.assert_called_once()
                self.assertEqual(bus.call.call_args.args[0].member(), "UnInhibit")
                bus.asyncCall.assert_not_called()
                self.assertFalse(filter_._pending)
                self.assertEqual(filter_.cookie, 0)
                # Zdarzenie finished już w kolejce Qt może dotrzeć mimo rozłączenia sygnału.
                filter_._inhibited(watcher)
                filter_.stop()
                bus.call.assert_called_once()
                bus.asyncCall.assert_not_called()
                self.assertEqual(filter_.cookie, 0)
