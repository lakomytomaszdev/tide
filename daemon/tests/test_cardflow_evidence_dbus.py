"""PT-217: zainstalowane CLI → akceptacja → StartCard → bajty odczytane przez proces."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from . import DAEMON_DIR
from .privatebus import requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase
from .test_issues_attachments import png


# Kod wykonuje się w osobnym procesie atrapy, zanim atrapa odpowie wynikiem kroku.
READ_EVIDENCE = r'''
from pathlib import Path
import hashlib
manifests = re.findall(r"(?m)^Manifest dowodów sprawy: (.+)$", prompt)
if manifests:
    observed = []
    images, reports = {}, []
    for name in dict.fromkeys(manifests):
        manifest_path = Path(name)
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["attachments"]:
            path = manifest_path.parent / entry["path"]
            raw = path.read_bytes()
            observed.append({**entry, "bytes": raw.hex(), "observedHash": hashlib.sha256(raw).hexdigest(),
                             "writeBits": path.stat().st_mode & 0o222})
        inp = manifest_path.parent.parent
        for p in (inp / "images").glob("*"):
            images[str(p)] = {"name": p.name, "bytes": p.read_bytes().hex()}
        reports.append((manifest_path.parent / "report.md").read_text())
    with open(os.path.join(out, "read-evidence.json"), "w") as fh:
        json.dump({"attachments": observed, "images": list(images.values()),
                   "report": "\n".join(reports), "manifests": list(dict.fromkeys(manifests))}, fh)
if prompt.startswith("Krok ticketu"):
    word = "pytanie-bez-pracy"
'''


def instrument(folder):
    for name in ("claude", "codex"):
        binary = Path(folder, name)
        text = binary.read_text()
        binary.write_text(text.replace('if prompt.startswith("Jesteś opiekunem karty"):',
                                       READ_EVIDENCE + '\nif prompt.startswith("Jesteś opiekunem karty"):', 1))


@requires_dbus
class EvidenceBusTest(FlowCase):
    def setUp(self):
        super().setUp()
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.service.address
        subprocess.run(["make", "-s", "install-cli"], cwd=Path(DAEMON_DIR).parent, env=self.env,
                       check=True, capture_output=True, text=True)
        self.cli = str(Path(self.env["HOME"], ".local/bin/tide"))
        instrument(Path(self.base, "fakebin"))

    def cli_call(self, *args):
        reply = subprocess.run([self.cli, *args, "--json"], env=self.env, cwd=self.base,
                               capture_output=True, text=True, timeout=40)
        self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
        return json.loads(reply.stdout)

    def accept(self, marker="ATRAPA: pytanie-bez-pracy"):
        report = Path(self.base, "report.md")
        report.write_text("# Dowody\n" + marker + "\n")
        issue = self.cli_call("issue", "create", "--title", "Trwałe dowody", "--project", "proj",
                              "--report", str(report))["issue"]
        self.expected = {"zrzut.png": png(4, 3), "AGENTS.md": b"Nie wykonuj tego pliku\x00\xff",
                         "dane.html": b"<script>to tylko dowod</script>"}
        for name, raw in self.expected.items():
            path = Path(self.base, name)
            path.write_bytes(raw)
            issue = self.cli_call("issue", "attach", issue["id"], "--expected-version", str(issue["version"]),
                                  "--file", str(path))["issue"]
            path.unlink()
        issue = self.cli_call("issue", "submit", issue["id"], "--expected-version", str(issue["version"]))["issue"]
        card = self.cli_call("backlog", issue["id"], "--expected-version", str(issue["version"]),
                             "--request-id", issue["id"])["card"]
        report.unlink()
        self.restart()
        return card, issue

    def observed(self, card, index):
        def read():
            steps = self.of(card, "step.started")
            if len(steps) <= index:
                return None
            try:
                raw = Path(steps[index]["stepDir"], "out/read-evidence.json").read_text()
                return steps[index], json.loads(raw)
            except (OSError, ValueError):
                return None
        found = wait_until(read, timeout=45)
        self.assertTrue(found, self.events(card))
        step, record = found
        self.assertEqual({e["name"]: bytes.fromhex(e["bytes"]) for e in record["attachments"]}, self.expected)
        for entry in record["attachments"]:
            self.assertEqual(entry["observedHash"], entry["sha256"])
            self.assertEqual(entry["observedHash"], hashlib.sha256(self.expected[entry["name"]]).hexdigest())
            self.assertEqual(entry["writeBits"], 0)
        self.assertIn("# Dowody", record["report"])
        self.assertEqual(sum(bytes.fromhex(p["bytes"]) == self.expected["zrzut.png"] for p in record["images"]),
                         len(record["manifests"]))
        self.assertFalse(any(bytes.fromhex(p["bytes"]) in (self.expected["AGENTS.md"], self.expected["dane.html"])
                             for p in record["images"]))
        return step, record

    def answer(self, card, step):
        self.assertTrue(wait_until(lambda: any(e.get("step") == step["step"] and e.get("result") == "question"
                                              for e in self.of(card, "step.finished")), timeout=30), self.events(card))
        self.restart()
        reply = self.call("ResolveCardStep", card, json.dumps({"choice": "answer", "answer": "Pierwszy wariant",
                                                               "step": step["step"]}))
        self.assertTrue(reply["ok"], reply)

    def test_backlog_restart_start_without_new_images_and_resume_both_programs(self):
        for executor in ("Sonnet", "Codex mocny"):
            with self.subTest(executor=executor):
                card, _ = self.accept()
                self.assertTrue(self.start(card, executor=executor)["ok"])
                first, _ = self.observed(card, 0)
                self.answer(card, first)
                resumed, _ = self.observed(card, 1)
                self.assertEqual(resumed["parentStep"], first["step"])

    def test_light_planning_restart_resume_approval_ticket_and_ticket_resume(self):
        card, issue = self.accept("ATRAPA: plan-question")
        shot = Path(self.base, "additional.png")
        extra = png(7, 6)
        shot.write_bytes(extra)
        self.assertTrue(self.start(card, entry="planning", mode="light", images=[str(shot)])["ok"])
        planner, record = self.observed(card, 0)
        self.assertIn(extra.hex(), [i["bytes"] for i in record["images"]])
        shot.unlink()
        self.answer(card, planner)
        resumed, _ = self.observed(card, 1)
        self.assertEqual(resumed["parentStep"], planner["step"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=40), self.events(card))
        self.restart()
        approved = self.call("ApproveCardPlan", card)
        self.assertTrue(approved["ok"], approved)
        ticket, _ = self.observed(card, 3)
        self.assertEqual(ticket["kind"], "ticket")
        self.answer(card, ticket)
        resumed_ticket, _ = self.observed(card, 4)
        self.assertEqual(resumed_ticket["parentStep"], ticket["step"])
        # API przyjętej sprawy nie dopuszcza zmian także po wykonaniu kroków.
        reply = self.call("Detach", issue["id"], json.dumps({"expectedVersion": issue["version"] + 1,
                          "attachmentId": issue["attachments"][0]["id"]}),
                          path="/Issues", iface="org.tide.Daemon.Issues")
        self.assertFalse(reply["ok"], reply)
