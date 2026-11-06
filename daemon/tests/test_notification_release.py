"""Zamknięcie usługi podczas kończenia Inhibit: ta sama odpowiedź jest rozliczana raz."""
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon import activity_state as activity


class NotificationReleaseTest(unittest.TestCase):
    def test_signal_during_blocking_wait_cannot_restore_an_already_released_cookie(self):
        watcher = mock.Mock()
        released = []
        state = SimpleNamespace(_pending={watcher}, _wanted=True, cookie=0,
                                _retry=mock.Mock(), _retry_ms=0, retry_first=2000, _restore=lambda: None)
        state._inhibited = lambda w, release_now=True: activity.NotificationFilter._inhibited(state, w, release_now)

        def uninhibit(blocking):
            released.append(state.cookie)
            state.cookie = 0

        state._uninhibit = uninhibit
        # waitForFinished może dostarczyć finished przed powrotem do ręcznego odbioru w release().
        watcher.waitForFinished.side_effect = lambda: state._inhibited(watcher)
        reply = mock.Mock()
        reply.type.return_value = activity.QDBusMessage.MessageType.ReplyMessage
        reply.arguments.return_value = [17]
        with mock.patch.object(activity, "QDBusPendingReply") as pending:
            pending.return_value.reply.return_value = reply
            activity.NotificationFilter.release(state, blocking=True)
        self.assertEqual(released, [17])
        self.assertEqual(state.cookie, 0)
        self.assertEqual(state._pending, set())
