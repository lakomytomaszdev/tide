"""Granica adaptera Qt: odpowiedź akceptacji musi pozostać napisem JSON."""
import json
import unittest
from unittest import mock

from . import DAEMON_DIR
from PyQt6.QtCore import QCoreApplication, QObject
from tide_daemon.dbus_service import IssuesAdaptor


class AcceptanceAdaptorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_acceptance_returns_success_json_and_refreshes_board(self):
        parent = QObject()
        board = mock.Mock(store=None)
        adaptor = IssuesAdaptor(parent, board)
        encoded = json.dumps({"ok": True, "apiVersion": 1, "card": "K-1"})
        adaptor.service = mock.Mock()
        adaptor.service.call.return_value = encoded
        self.assertEqual(adaptor.AcceptIssue("S-1", "{}"), encoded)
        board.publish.assert_called_once_with()

    def test_acceptance_returns_conflict_json_without_refreshing_board(self):
        parent = QObject()
        board = mock.Mock(store=None)
        adaptor = IssuesAdaptor(parent, board)
        encoded = json.dumps({"ok": False, "apiVersion": 1, "error": "version-conflict", "currentVersion": 3})
        adaptor.service = mock.Mock()
        adaptor.service.call.return_value = encoded
        self.assertEqual(adaptor.AcceptIssue("S-1", "{}"), encoded)
        board.publish.assert_not_called()
