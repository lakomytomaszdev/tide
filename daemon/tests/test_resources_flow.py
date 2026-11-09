"""Publiczny start i wznowienie kopii usługi; model zastępuje czytnik rzeczywistych plików."""
import json
import os
from pathlib import Path
import subprocess
import sys

from . import flowfakes
from .privatebus import SERVICE, requires_dbus
from .test_cardflow_dbus import FlowCase
from .test_resources import install


READ_PROOF = r'''
from pathlib import Path
standard_match = re.search(r"Standard planu Tide: (.+) \(wersja 1.3\)", prompt)
assert standard_match, prompt
standard_path = Path(standard_match.group(1))
standard_text = standard_path.read_text()
assert "Przepływ karty" in standard_text
catalog = json.loads((Path(out).parent / "in/skills.json").read_text())
instruction = next(item for item in catalog if item["name"] == "to-spec")
skill_path = Path(instruction["path"])
assert "procedura kontrolna" in skill_path.read_text()
assert (skill_path.parent / "references/zasob.txt").read_text() == "zasób kontrolny"
with open(os.path.join(out, "resource-read.json"), "w") as fh:
    json.dump({"standard": str(standard_path), "skill": str(skill_path), "read": True,
               "resumed": "--resume" in args}, fh)
'''


@requires_dbus
class InstalledResourceFlowTest(FlowCase):
    def spawn(self):
        if not hasattr(self, "installed"):
            self.env.update(HOME=os.path.join(self.base, "inna osoba"), USER="inna-osoba",
                            XDG_DATA_HOME=os.path.join(self.base, "dane ze spacjami"), PYTHONPATH="")
            Path(self.env["HOME"]).mkdir()
            self.installed = install(self.env)
            skill = Path(self.env["HOME"]) / ".claude/skills/to-spec"
            (skill / "references").mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: to-spec\ndescription: Kontrola\n---\nprocedura kontrolna")
            (skill / "references/zasob.txt").write_text("zasób kontrolny")
            fake = Path(self.base) / "fakebin/claude"
            # Atrapa publikuje fake-call.json atomowo (mkstemp + replace); dowód odczytu przed tym zapisem.
            anchor = 'fd, call_tmp = tempfile.mkstemp('
            self.assertIn(anchor, flowfakes.CLAUDE)
            body = flowfakes.CLAUDE.replace(anchor, READ_PROOF + '\n' + anchor)
            fake.write_text(body)
        return self.service.spawn([sys.executable, "-s", str(self.installed / "tide-daemon.py"),
             "--claude-dir", str(Path(self.env["HOME"]) / ".claude"),
             "--codex-dir", os.path.join(self.base, "codex"), "--no-processes", "--no-notifications",
             "--workspace", os.path.join(self.base, "ws"), "--board-dir", self.board,
             "--no-report", "--no-limits", "--no-models", "--no-docker", "--no-krunner",
             "--no-monitors", "--no-activity-look", "--no-stream-shortcuts", "--no-youtrack"],
             SERVICE, cwd=self.base, extra_env={**self.env, "XDG_STATE_HOME": os.path.join(self.base, "state"),
                 "XDG_CACHE_HOME": os.path.join(self.base, "cache"),
                 "WAYLAND_DISPLAY": "/nonexistent/tide-wayland", "TIDE_CODEX_DESKTOP": ""})

    def test_public_start_and_resume_read_installed_standard_and_skill_resources(self):
        cli = Path(self.env["HOME"]) / ".local/bin/tide"
        env = {**self.env, "DBUS_SESSION_BUS_ADDRESS": self.service.address}
        info = subprocess.run([str(cli), "info", "--json"], env=env, cwd=self.base,
                              text=True, capture_output=True, check=True)
        standard = json.loads(info.stdout)["resources"]["plan-standard"]
        self.assertTrue(standard["available"])
        self.assertTrue(standard["path"].startswith(str(self.installed.parent / "resources")))
        number = subprocess.run([str(cli), "next-id", self.repo, "--dry-run"], env=env,
                                cwd=self.base, text=True, capture_output=True)
        self.assertEqual(number.returncode, 0, number.stdout + number.stderr)
        self.assertIn("PT-1", number.stdout)
        card = self.card(description="Sprawdź pliki.\nATRAPA: pytanie")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting")
        first = self.of(card, "step.started")[0]
        proof = json.loads((Path(first["stepDir"]) / "out/resource-read.json").read_text())
        self.assertTrue(proof["read"])
        self.assertFalse(proof["resumed"])
        self.assertTrue(proof["standard"].startswith(str(self.installed.parent / "resources")))
        answer = self.call("ResolveCardStep", card, json.dumps({"choice": "answer", "answer": "ISO 8601"}))
        self.assertTrue(answer["ok"], answer)
        self.wait_column(card, "review")
        resumed = next(s for s in self.of(card, "step.started") if s.get("parentStep") == first["step"])
        second = json.loads((Path(resumed["stepDir"]) / "out/resource-read.json").read_text())
        self.assertTrue(second["read"])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["standard"], proof["standard"])
