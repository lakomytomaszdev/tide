"""PT-198: granice zaufania, refspec i odczyt CLI, bez sieci i gniazd."""
from contextlib import contextmanager
import io
import json
import os
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from . import hostingfakes
from tide_daemon.cardflow import hostingcli as cli, linksuggestions as links


class LinkIdentityTest(unittest.TestCase):
    def test_repo_protocols_credentials_and_exact_hosts(self):
        for remote in ("git@github.com:Team/proj.git", "ssh://git@github.com/Team/proj.git",
                       "https://user:secret@github.com/Team/proj.git"):
            self.assertEqual(links.repo_identity(remote), ("github.com", "Team/proj"))
        self.assertTrue(links.same_project(("github.com", "Team/Proj"), ("github.com", "team/proj"), "gh"))
        self.assertFalse(links.same_project(("github.com.evil", "team/proj"), ("github.com", "team/proj"), "gh"))
        self.assertEqual(links.parse_link("https://GitHub.com/Team/Proj/pull/17")["project"], "team/proj")
        for url in ("https://github.com@evil.example/team/proj/pull/17", "https://github.com:444/team/proj/pull/17",
                    "https://github.com/team/proj/pull/17?secret=x", "https://github.com/team/proj/pull/17/commits",
                    "https://github.com/team/%2e%2e/pull/17", "https://localhost/team/proj/pull/17",
                    "https://127.0.0.1/team/proj/pull/17", "https://github.com/team/$(id)/pull/17",
                    "http://github.com/team/proj/pull/17", "https://github.com\\@evil/team/proj/pull/17"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                links.parse_link(url)

    def test_metadata_source_not_target_and_metacharacters_remain_data(self):
        branch = "feature/$(touch${IFS}PWNED);<tag>"
        data = hostingfakes.github("a" * 40, source="user/proj", branch=branch)
        parsed = links.parse_link(data["html_url"])
        with patch.object(links, "read_json", return_value=data) as read:
            meta = links.metadata(parsed, "/repo")
        self.assertEqual(meta["sourceBranch"], branch)
        self.assertEqual(meta["targetBranch"], "release/target")
        read.assert_called_once_with("gh", "github.com", "repos/team/proj/pulls/17", "/repo", deadline=None)
        self.assertNotIn(branch, str(read.call_args))
        for field, replacement in (("html_url", "https://evil.example/team/proj/pull/17"), ("number", 18)):
            with patch.object(links, "read_json", return_value={**data, field: replacement}), self.assertRaises(ValueError):
                links.metadata(parsed, "/repo")

    def test_gitlab_normalized_subgroups_and_numeric_source_project_lookup(self):
        responses = hostingfakes.gitlab("c" * 40)
        parsed = links.parse_link("https://git.example.org/team/sub/proj/-/merge_requests/17")
        with patch.object(links, "read_json", side_effect=lambda program, host, endpoint, repo, **kw: responses[endpoint]) as read:
            meta = links.metadata(parsed, "/repo")
        self.assertEqual(meta["sourceProject"], "user/sub/proj")
        self.assertEqual(meta["sourceCommit"], "c" * 40)
        self.assertEqual([c.args[2] for c in read.call_args_list],
                         ["projects/team%2Fsub%2Fproj/merge_requests/17", "projects/1", "projects/2"])
        responses["projects/2"]["web_url"] = "https://evil.example/user/sub/proj"
        with patch.object(links, "read_json", side_effect=lambda p, h, e, r, **kw: responses[e]), self.assertRaises(ValueError):
            links.metadata(parsed, "/repo")

    def test_custom_refspec_negative_and_foreign_ref_collision(self):
        self.assertEqual(links.tracking_refs({"fetch": ["refs/heads/a*abc:refs/remotes/custom/*"]}, "abc"), [])
        meta = {"host": "github.com", "project": "team/proj", "sourceProject": "user/proj",
                "sourceBranch": "feature/base", "sourceCommit": "a" * 40, "targetBranch": "main", "link": "link"}
        config = {"identity": ("github.com", "user/proj"),
                  "fetch": ["+refs/heads/feature/*:refs/remotes/custom/*"]}
        branches = [{"ref": "refs/heads/feature/base", "commit": "a" * 40},
                    {"ref": "refs/remotes/origin/feature/base", "commit": "a" * 40},
                    {"ref": "refs/remotes/custom/base", "name": "custom/base", "commit": "b" * 40, "selectable": True}]
        remotes = {"fork": config}
        candidate = links.candidates_for(meta, "gh", branches, remotes)[0]
        self.assertEqual(candidate["ref"], "refs/remotes/custom/base")
        self.assertEqual(candidate["commit"], "b" * 40)
        self.assertTrue(candidate["stale"])
        config["fetch"].append("^refs/heads/feature/*")
        self.assertFalse(links.candidates_for(meta, "gh", branches, remotes)[0]["selectable"])
        config["fetch"].pop()
        remotes["origin"] = {"identity": ("github.com", "team/proj"),
                             "fetch": ["+refs/heads/other:refs/remotes/custom/base"]}
        self.assertFalse(links.candidates_for(meta, "gh", branches, remotes)[0]["selectable"])

    def test_local_branch_requires_upstream_of_actual_source_repository(self):
        meta = {"host": "github.com", "project": "team/proj", "sourceProject": "user/proj",
                "sourceBranch": "topic", "sourceCommit": "a" * 40, "targetBranch": "main", "link": "link"}
        branches = [{"ref": "refs/heads/topic", "commit": "a" * 40, "selectable": True}]
        remotes = {"origin": {"identity": ("github.com", "team/proj"), "locals": [("refs/heads/topic", "refs/heads/topic")]},
                   "fork": {"identity": ("github.com", "user/proj")}}
        self.assertFalse(links.candidates_for(meta, "gh", branches, remotes)[0]["selectable"])
        remotes["fork"]["locals"] = [("refs/heads/topic", "refs/heads/topic")]
        result = links.candidates_for(meta, "gh", branches, remotes)
        self.assertEqual([c["ref"] for c in result], ["refs/heads/topic"])
        self.assertTrue(result[0]["selectable"])

    def test_unknown_url_host_no_cli_call_and_bounded_request_count(self):
        config = {"origin": {"identity": ("github.com", "team/proj"), "fetch": []}}
        with patch.object(links, "remote_config", return_value=config), patch.object(links, "metadata") as read:
            result = links.suggest_links(None, "/repo", [], "https://foreign.example/team/proj/pull/17",
                                         "https://github.com/other/proj/pull/17")
            read.assert_not_called()
            self.assertTrue(result["warnings"])
            read.side_effect = cli.HostingError("Brak logowania")
            result = links.suggest_links(None, "/repo", [], "", "\n".join(
                f"https://github.com/team/proj/pull/{n}" for n in range(1, 100)))
            self.assertEqual(read.call_count, 4)
            self.assertIn("4 linki", result["warnings"][-1])


    def test_all_links_and_gitlab_project_reads_share_deadline_and_keep_partial_result(self):
        now = [100.0]
        config = {"gh": {"identity": ("github.com", "team/proj")},
                  "glab": {"identity": ("git.example.org", "team/sub/proj")}}
        responses = hostingfakes.gitlab("a" * 40)
        responses["repos/team/proj/pulls/17"] = hostingfakes.github("a" * 40)

        def read(program, host, endpoint, repo, *, deadline):
            if now[0] + 4 >= deadline:
                now[0] = deadline
                raise cli.HostingTimeout("Przekroczono czas analizy linków PR/MR.")
            now[0] += 4
            return responses[endpoint]

        with patch.object(links.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(links, "read_json", side_effect=read) as calls:
            result = links.suggest_links(None, "/repo", [], "https://github.com/team/proj/pull/17",
                                         "https://git.example.org/team/sub/proj/-/merge_requests/17\n"
                                         "https://git.example.org/team/sub/proj/-/merge_requests/18", remotes=config)
        self.assertEqual([c.args[2] for c in calls.call_args_list],
                         ["repos/team/proj/pulls/17", "projects/team%2Fsub%2Fproj/merge_requests/17", "projects/1"])
        self.assertEqual([c.kwargs["deadline"] for c in calls.call_args_list], [110.0] * 3)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["link"], "https://github.com/team/proj/pull/17")
        self.assertIn("czas analizy", " ".join(result["warnings"]))

    def test_ordinary_documentation_links_do_not_produce_unsafe_link_warning(self):
        with patch.object(links, "metadata", return_value=hostingfakes.github("a" * 40)) as read:
            result = links.suggest_links(None, "/repo", [], "https://docs.example.org/guide", "", remotes={})
        read.assert_not_called()
        self.assertNotIn("niebezpieczny", " ".join(result["warnings"]))


class GatewayTest(unittest.TestCase):
    def handler(self, path="http://git.example.org/api/v4/projects/1"):
        handler = cli._Gateway.__new__(cli._Gateway)
        handler.path = path
        handler.headers = {"Host": "git.example.org", "Authorization": "Bearer FAKE_SECRET"}
        handler.server = SimpleNamespace(host="git.example.org", path="/api/v4/projects/1", failure="", requested=False,
                                         streams=set(), stream_lock=threading.Lock(), stopping=False)
        handler.wfile = io.BytesIO()
        handler.send_error, handler.send_response = Mock(), Mock()
        handler.send_header, handler.end_headers = Mock(), Mock()
        return handler

    def test_redirect_any_host_never_causes_second_request_or_returns_location(self):
        for target in ("https://evil.example/", "http://127.0.0.1/secret", "https://git.example.org/another"):
            with self.subTest(target=target), patch.object(cli.http.client, "HTTPSConnection") as connection:
                response = connection.return_value.getresponse.return_value
                response.status = 302
                response.getheader.return_value = target
                handler = self.handler()
                handler.do_GET()
                connection.assert_called_once_with("git.example.org", timeout=10)
                connection.return_value.request.assert_called_once_with(
                    "GET", "/api/v4/projects/1", headers={"Authorization": "Bearer FAKE_SECRET"})
                response.read.assert_not_called()
                self.assertIn("przekierowanie", handler.server.failure)
                self.assertEqual(handler.wfile.getvalue(), b"")
                handler.send_header.assert_not_called()

    def test_foreign_host_or_endpoint_and_body_rejected_before_network(self):
        for path in ("http://evil.example/api/v4/projects/1", "http://git.example.org/api/v4/projects/2",
                     "http://git.example.org/api/v4/projects/1?token=secret"):
            with patch.object(cli.http.client, "HTTPSConnection") as connection:
                handler = self.handler(path)
                handler.do_GET()
                connection.assert_not_called()
                handler.send_error.assert_called_with(403)

    def test_gateway_bounds_body_and_keeps_only_authenticated_get(self):
        with patch.object(cli.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status, response.read.return_value = 200, b"x" * (cli.MAX_OUTPUT + 1)
            handler = self.handler()
            handler.do_GET()
            self.assertIn("1 MiB", handler.server.failure)
            response.read.assert_called_once_with(cli.MAX_OUTPUT + 1)
            self.assertEqual(handler.wfile.getvalue(), b"")


class CliReadTest(unittest.TestCase):
    @contextmanager
    def fake_gateway(self, *args):
        yield SimpleNamespace(failure=""), "/unused-proxy"

    def test_real_process_output_errors_timeout_limits_and_missing_cli(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cli, "host_gateway", self.fake_gateway):
            binary = os.path.join(folder, "gh")
            hostingfakes.install(folder, {"responses": {"endpoint": {"ok": True}}})
            self.assertEqual(cli.read_json(binary, "github.com", "endpoint", folder), {"ok": True})
            for mode in ("error", "bad-json", "large", "large-stderr", "timeout"):
                hostingfakes.install(folder, {"mode": mode})
                with self.subTest(mode=mode), patch.object(cli, "TIMEOUT", 0.15 if mode == "timeout" else 10):
                    with self.assertRaises(cli.HostingError) as caught:
                        cli.read_json(binary, "github.com", "endpoint", folder)
                    self.assertNotIn("FAKE_SECRET", str(caught.exception))
            with self.assertRaises(cli.HostingError):
                cli.read_json("/nonexistent/gh", "github.com", "endpoint", folder)

    def test_remaining_deadline_limits_process_and_expired_deadline_never_starts_cli(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cli, "host_gateway", self.fake_gateway):
            hostingfakes.install(folder, {"mode": "timeout"})
            started = time.monotonic()
            with self.assertRaises(cli.HostingTimeout):
                cli.read_json(os.path.join(folder, "gh"), "github.com", "endpoint", folder,
                              deadline=started + .15)
            self.assertLess(time.monotonic() - started, 1)
            with patch.object(cli.subprocess, "Popen") as process, self.assertRaises(cli.HostingTimeout):
                cli.read_json("gh", "github.com", "endpoint", folder, deadline=started)
            process.assert_not_called()


class GatewayTransportTest(unittest.TestCase):
    def setUp(self):
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
            with tempfile.TemporaryDirectory() as folder, socket.socket(socket.AF_UNIX) as sock:
                sock.bind(os.path.join(folder, "probe.sock"))
        except OSError:
            self.skipTest("piaskownica blokuje lokalne gniazda; test wymaga wykonania poza nią")

    def test_both_cli_transports_use_fixed_tls_api_and_stop_foreign_redirects(self):
        with tempfile.TemporaryDirectory() as folder:
            hostingfakes.install(folder, {"mode": "transport"})
            with patch.dict(os.environ, PATH=folder + os.pathsep + os.environ.get("PATH", "")):
                for program, host, expected_host, expected_path in (
                        ("gh", "github.com", "api.github.com", "/repos/team/proj/pulls/17"),
                        ("gh", "github.example.org", "github.example.org", "/api/v3/repos/team/proj/pulls/17"),
                        ("glab", "git.example.org", "git.example.org", "/api/v4/projects/team%2Fsub%2Fproj/merge_requests/17")):
                    endpoint = expected_path.removeprefix("/api/v3").removeprefix("/api/v4").lstrip("/")
                    for status in (200, 302):
                        with self.subTest(program=program, host=host, status=status), \
                                patch.object(cli.http.client, "HTTPSConnection") as connection:
                            response = connection.return_value.getresponse.return_value
                            response.status = status
                            response.read.return_value = b'{"ok": true}'
                            response.getheader.return_value = "http://127.0.0.1/secret"
                            if status == 200:
                                self.assertEqual(cli.read_json(program, host, endpoint, folder), {"ok": True})
                            else:
                                with self.assertRaisesRegex(cli.HostingError, "przekierowanie"):
                                    cli.read_json(program, host, endpoint, folder)
                            connection.assert_called_once_with(expected_host, timeout=10)
                            connection.return_value.request.assert_called_once_with(
                                "GET", expected_path, headers={"Authorization": "Bearer FAKE_SECRET"})
