"""PT-198: kontrakt prawdziwych CLI, tylko lokalna bramka i atrapa upstream.

Brak programu lub lokalnych gniazd oznacza pominięcie, nigdy zastąpienie CLI atrapą.
Testy nie korzystają z logowania użytkownika ani z API hostingu.
"""
from contextlib import contextmanager
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tide_daemon.cardflow import hostingcli as cli


class RealCliTransportTest(unittest.TestCase):
    def setUp(self):
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
        except OSError:
            self.skipTest("piaskownica blokuje lokalne gniazda")

    def require(self, program):
        if not shutil.which(program):
            self.skipTest("brak prawdziwego " + program)
        version = subprocess.run([program, "--version"], capture_output=True, timeout=10, check=True)
        print(version.stdout.decode().splitlines()[0])

    @contextmanager
    def no_gateway(self, *args):
        yield SimpleNamespace(failure=""), "http://127.0.0.1:1"

    def test_gh_config_socket_success_and_closed_gateway_failure(self):
        self.require("gh")
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, GH_TOKEN="FAKE_SECRET", GH_ENTERPRISE_TOKEN="FAKE_SECRET",
                           GH_CONFIG_DIR=folder), \
                patch.object(cli.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status, response.read.return_value = 200, b'{"ok": true}'
            for host, api_host in (("github.com", "api.github.com"), ("github.example.invalid", "github.example.invalid")):
                with self.subTest(host=host):
                    connection.reset_mock()
                    self.assertEqual(cli.read_json("gh", host, "repos/team/proj/pulls/17", folder), {"ok": True})
                    connection.assert_called_once_with(api_host, timeout=10)
            connection.reset_mock()
            with patch.object(cli, "host_gateway", self.no_gateway), self.assertRaises(cli.HostingError):
                cli.read_json("gh", "github.com", "repos/team/proj/pulls/17", folder)
            connection.assert_not_called()

    def test_gh_does_not_rewrite_migrated_user_hosts_on_success_or_failure(self):
        self.require("gh")
        hosts_text = ('github.com:\n    users:\n        tide-test:\n            oauth_token: FAKE_SECRET\n'
                      '    git_protocol: https\n    oauth_token: FAKE_SECRET\n    user: tide-test\n')
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as cwd, \
                patch.object(cli.http.client, "HTTPSConnection") as connection:
            env = {key: value for key, value in os.environ.items()
                   if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")}
            env["GH_CONFIG_DIR"] = source
            hosts = os.path.join(source, "hosts.yml")
            with open(hosts, "w", encoding="utf-8") as fh:
                fh.write(hosts_text)
            with open(os.path.join(source, "config.yml"), "w", encoding="utf-8") as fh:
                fh.write('version: "1"\n')
            os.utime(hosts, ns=(1_000_000_000, 1_000_000_000))
            before = os.stat(hosts).st_mtime_ns
            response = connection.return_value.getresponse.return_value
            with patch.dict(os.environ, env, clear=True):
                response.status, response.read.return_value = 200, b'{"ok": true}'
                self.assertEqual(cli.read_json("gh", "github.com", "repos/team/proj/pulls/17", cwd), {"ok": True})
                response.status, response.read.return_value = 404, b'{"message": "Not Found"}'
                with self.assertRaises(cli.HostingError):
                    cli.read_json("gh", "github.com", "repos/team/proj/pulls/18", cwd)
            self.assertEqual(os.stat(hosts).st_mtime_ns, before)
            with open(hosts, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), hosts_text)

    def test_glab_connect_carries_tls_not_plain_token_and_closed_proxy_fails(self):
        self.require("glab")
        client, peer = socket.socketpair()
        received = []

        def receive_hello():
            with peer:
                peer.settimeout(5)
                received.append(peer.recv(65536))

        worker = threading.Thread(target=receive_hello, daemon=True)
        worker.start()
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, GLAB_CONFIG_DIR=folder, GITLAB_TOKEN="FAKE_SECRET",
                           GITLAB_HOST="git.example.invalid", GLAB_API_PROTOCOL="http", API_PROTOCOL="http"), \
                patch.object(cli.socket, "create_connection", return_value=client) as connect:
            # Istniejący plik zapobiega szukaniu konfiguracji użytkownika w innych katalogach.
            with open(os.path.join(folder, "config.yml"), "w", encoding="utf-8") as fh:
                fh.write("hosts: {}\n")
            with self.assertRaises(cli.HostingError):
                cli.read_json("glab", "git.example.invalid", "projects/1", folder)
            worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertTrue(connect.called, "prawdziwy glab ominął proxy CONNECT")
            self.assertEqual(connect.call_args.args[0], ("git.example.invalid", 443))
            self.assertTrue(received and received[0].startswith(b"\x16\x03"), "oczekiwano TLS ClientHello")
            self.assertNotIn(b"FAKE_SECRET", received[0])
            connect.reset_mock()
            with patch.object(cli, "host_gateway", self.no_gateway), self.assertRaises(cli.HostingError):
                cli.read_json("glab", "git.example.invalid", "projects/1", folder)
            connect.assert_not_called()
