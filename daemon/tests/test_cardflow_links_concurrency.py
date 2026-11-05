"""PT-198: zawieszony CLI nie zatrzymuje lokalnego StartCard (bez gniazd i sieci)."""
from contextlib import contextmanager
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

from . import hostingfakes
from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from tide_daemon.cardflow import hostingcli as cli
from tide_daemon.cardflow import linksuggestions as links


class LinkConcurrencyTest(FlowInProcess):
    def test_two_hosting_reads_reject_extra_without_blocking_text_suggestions(self):
        card = self.card()
        reading, results, overflow, text = [], [], [], []
        release = threading.Event()

        def read(*args, **kwargs):
            reading.append(threading.current_thread().name)
            release.wait(5)
            return {"candidates": [], "warnings": []}

        with patch.object(links, "suggest_links", side_effect=read):
            try:
                for revision in (1, 2):
                    self.board.suggest_card_base(card, json.dumps({"text": "", "revision": revision, "scope": "links"}),
                                                 results.append)
                self.assertTrue(wait_until(lambda: len(reading) == 2))
                self.board.suggest_card_base(card, json.dumps({"text": "", "revision": 3, "scope": "links"}),
                                             overflow.append)
                self.assertTrue(wait_until(lambda: overflow, timeout=1))
                self.assertEqual(json.loads(overflow[0])["error"], "unavailable")
                self.board.suggest_card_base(card, json.dumps({"text": "master", "revision": 4, "scope": "text"}),
                                             text.append)
                self.assertTrue(wait_until(lambda: text, timeout=1))
                self.assertTrue(json.loads(text[0])["ok"])
                self.assertEqual(results, [])
                self.assertTrue(all(name.startswith("tide-hosting") for name in reading), reading)
            finally:
                release.set()
                self.assertTrue(wait_until(lambda: len(results) == 2))

    def test_unexpected_link_error_logs_type_without_request_or_exception_secrets(self):
        card = self.card(description="FAKE_DESCRIPTION_SECRET")
        with patch.object(links, "suggest_links", side_effect=IndexError("FAKE_EXCEPTION_SECRET")), \
                patch("builtins.print") as log:
            result = self.flow.engine.suggest_card_base(card, {"text": "FAKE_TEXT_SECRET", "revision": 1, "scope": "links"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])
        log.assert_called_once_with("tide-daemon: SuggestCardBase: IndexError", flush=True)
        self.assertNotIn("FAKE_", json.dumps(result))
        self.assertEqual(self.events(card), [])

    def test_hanging_cli_allows_parallel_local_start(self):
        git(self.repo, "remote", "add", "origin", "git@github.com:team/proj.git")
        folder = os.path.join(self.base, "fakebin")
        hostingfakes.install(folder, {"mode": "timeout"})
        card = self.card(description="https://github.com/team/proj/pull/17\nATRAPA: pytanie")
        suggestions, started = [], []

        @contextmanager
        def gateway(*args):
            yield SimpleNamespace(failure=""), "/unused-proxy"

        with patch.object(cli, "host_gateway", gateway), patch.object(cli, "TIMEOUT", 4):
            self.board.suggest_card_base(card, json.dumps({"text": "", "revision": 1, "scope": "links"}),
                                         suggestions.append)
            try:
                self.assertTrue(wait_until(lambda: os.path.exists(os.path.join(folder, "hosting-calls.jsonl"))))
                self.board.start_card(card, json.dumps({"entry": "now", "executor": "Sonnet",
                                                       "baseRef": "refs/heads/master", "baseCommit": self.master}),
                                      started.append)
                self.assertTrue(wait_until(lambda: started, timeout=2), "StartCard czeka na zawieszony CLI")
                self.assertTrue(json.loads(started[0])["ok"], started)
                self.assertEqual(suggestions, [], "StartCard powinien odpowiedzieć w trakcie odczytu CLI")
                self.assertEqual(self.of(card, "card.started")[0]["baseCommit"], self.master)
            finally:
                self.assertTrue(wait_until(lambda: suggestions, timeout=6))
            result = json.loads(suggestions[0])
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["candidates"], [])
            self.assertTrue(result["warnings"])
