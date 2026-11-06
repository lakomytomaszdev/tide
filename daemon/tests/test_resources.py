"""Odczyty z instalacji, niezależne od ścieżek checkoutu i prywatnej konfiguracji."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from . import DAEMON_DIR
from .fakebin import make_env
from .test_board_core import BoardCase, git_out
from .test_board_launch import old_seed
from tide_daemon import resources
from tide_daemon.board import flows

REPO = Path(DAEMON_DIR).parent


def install(env):
    for component in ("daemon", "cli"):
        subprocess.run(["bash", str(REPO / component / "install.sh")], env=env,
                       check=True, capture_output=True, text=True)
    return Path(env["XDG_DATA_HOME"]) / "tide/daemon"


class ResourceInstallTest(unittest.TestCase):
    def test_installed_modules_and_tools_read_resources_without_source_imports(self):
        with tempfile.TemporaryDirectory(prefix="tide-resources-") as tmp:
            env = make_env(tmp)
            env.update(HOME=str(Path(tmp) / "inna osoba"), USER="inna-osoba",
                       XDG_DATA_HOME=str(Path(tmp) / "inne dane"), PYTHONPATH="")
            Path(env["HOME"]).mkdir()
            dest = install(env)
            # -I wyklucza cwd i PYTHONPATH; dostępna jest wyłącznie kopia produktu i biblioteka standardowa.
            code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from tide_daemon import resources
from tide_daemon.board import flows
from tide_daemon.cardflow import handoff
from tide_daemon.issues import IssueService
info = json.loads(IssueService().call('Info'))
for item in info['resources'].values():
    assert item['available'], item
    assert item['path'].startswith(str(pathlib.Path(sys.argv[1]).parent / 'resources'))
    assert pathlib.Path(item['path']).read_bytes()
text = flows.render(flows.SEED_FLOWS['full'], {'repo': '/home/inna osoba/projekt'})
assert "tide next-id '/home/inna osoba/projekt'" in text
assert 'DevWorkspace/plasma-tide' not in text
standard = resources.resolve('plan-standard').read_text()
assert 'Przepływ karty' in standard and 'tide next-id' in standard
prompt = handoff.ticket_card(card_id='K-1', title='Test', description='Test', project='proj',
    ticket='PT-1', branch='ticket/pt-1', card_branch='card/k-1', worktree='/work', out_dir='/out')
assert str(resources.resolve('plan-standard')) in prompt
print(json.dumps(info, ensure_ascii=False))
"""
            proc = subprocess.run([sys.executable, "-I", "-c", code, str(dest)], env=env,
                                  cwd=tmp, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            info = json.loads(proc.stdout)
            self.assertEqual(info["resources"]["plan-standard"]["version"], "1.3")
            check = subprocess.run([sys.executable, info["resources"]["check-plans"]["path"],
                                    tmp, "--no-youtrack", "--json"], cwd=tmp, env=env,
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(json.loads(check.stdout), [])
            probe = Path(tmp) / "resource-step-probe.py"
            shutil.copyfile(REPO / "daemon/tests/fixtures/resource_step_probe.py", probe)
            steps = subprocess.run([sys.executable, "-I", str(probe), str(dest)], env=env,
                                   cwd=tmp, capture_output=True, text=True)
            self.assertEqual(steps.returncode, 0, steps.stdout + steps.stderr)
            proofs = json.loads(steps.stdout)
            self.assertEqual([(p["tool"], p["resume"]) for p in proofs],
                             [("claude", False), ("claude", True), ("codex", False), ("codex", True)])
            self.assertTrue(all(p["read"] and p["sha256"] == info["resources"]["plan-standard"]["sha256"]
                                for p in proofs))
            evidence = REPO / "build/test-results/pt-223-resource-reads.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps(proofs, ensure_ascii=False, indent=2))
            for unit in (Path(env["XDG_CONFIG_HOME"]) / "systemd/user/tide-daemon.service",
                         Path(env["XDG_DATA_HOME"]) / "dbus-1/services/org.tide.Daemon.service"):
                self.assertIn(f'"{dest}/tide-daemon.py"', unit.read_text())
            subprocess.run(["bash", str(REPO / "daemon/uninstall.sh")], env=env, check=True,
                           capture_output=True)
            self.assertFalse(dest.exists())
            self.assertFalse((dest.parent / "resources").exists())

    def test_missing_configured_resource_is_explicit_and_info_still_works(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TIDE_RESOURCES_DIR": tmp}):
            from tide_daemon.issues import IssueService
            reply = json.loads(IssueService().call("Info"))
            self.assertTrue(reply["ok"])
            self.assertFalse(reply["resources"]["plan-standard"]["available"])
            with self.assertRaisesRegex(resources.ResourceError, "TIDE_RESOURCES_DIR"):
                flows.render(flows.SEED_FLOWS["light"], {})

    def test_cli_forwards_tool_arguments_without_shell(self):
        spec = importlib.util.spec_from_file_location("resource_cli", REPO / "cli/tide.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        path = str(resources.resolve("next-id"))
        reply = {"ok": True, "resources": {"next-id": {"available": True, "path": path}}}
        with mock.patch.object(cli, "PublicApi") as api, mock.patch.object(cli.subprocess, "run") as run:
            api.return_value.call.return_value = reply
            run.return_value.returncode = 0
            self.assertEqual(cli.main(["next-id", "/repo ze spacjami", "--title", "$(nie wykonuj)",
                                       "--prefix", "PT", "--dry-run"]), 0)
            self.assertEqual(run.call_args.args[0], [sys.executable, path, "/repo ze spacjami", "--prefix", "PT",
                                                    "--count", "1", "--title", "$(nie wykonuj)", "--dry-run"])


class ResourceMigrationTest(BoardCase):
    def test_known_seeds_migrate_custom_bytes_and_warning_survive(self):
        self.store.create_card({"title": "Migracja"})
        self.write("flows/full.md", old_seed("full"))
        custom = b"---\r\nmode: light\r\n---\r\nMoje: ~/DevWorkspace/plasma-tide/docs/agent-plan-standard.md\r\n"
        target = Path(self.root) / "flows/light.md"
        target.write_bytes(custom)
        git_out(self.root, "add", "-A")
        git_out(self.root, "commit", "-qm", "fixtures")
        with mock.patch.object(flows, "STANDARD", "/nonexistent/tide-standard"):
            errors = self.store.snapshot()["errors"]
        self.assertTrue(any("checkoutu" in e["message"] for e in errors), errors)
        self.assertEqual(self.store.migrate_flows(), ["flows/full.md"])
        self.assertEqual(target.read_bytes(), custom)
        self.assertEqual((Path(self.root) / "flows/full.md").read_text(), flows.SEED_FLOWS["full"])
        self.assertEqual(self.store.migrate_flows(), [])
