"""Kontrakt instrumentacji atrapy, bez usługi, D-Bus i repozytorium gita."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from . import flowfakes
from .test_cardflow_evidence_dbus import instrument


class EvidenceInstrumentationTest(unittest.TestCase):
    def test_instrumentation_rejects_missing_or_ambiguous_hook(self):
        marker = "# TIDE_FAKE_AGENT_READY: instrumentacja po inicjalizacji, przed obsługą kroku."
        with tempfile.TemporaryDirectory(prefix="tide-fake-unit-") as tmp:
            for contents in ("brak punktu instrumentacji", marker + "\n" + marker):
                with self.subTest(contents=contents):
                    binary = Path(tmp, "claude")
                    binary.write_text(contents)
                    with self.assertRaises(ValueError):
                        instrument(tmp)
                    self.assertEqual(binary.read_text(), contents)

    def test_instrumented_agents_read_evidence_after_initialization(self):
        with tempfile.TemporaryDirectory(prefix="tide-fake-unit-") as tmp:
            root = Path(tmp)
            binaries = flowfakes.install(str(root / "bin"))
            instrument(root / "bin")
            evidence = root / "in/evidence"
            evidence.mkdir(parents=True)
            raw = b"dowod\x00\xff"
            (evidence / "attachment").write_bytes(raw)
            (evidence / "attachment").chmod(0o444)
            (evidence / "report.md").write_text("# Dowody\n")
            manifest = {"attachments": [{"id": "A-1", "name": "dowod", "path": "attachment",
                                        "sha256": hashlib.sha256(raw).hexdigest()}]}
            (evidence / "attachments.json").write_text(json.dumps(manifest))
            for program in ("claude", "codex"):
                for kind in ("ticket", "planning", "caretaker"):
                    with self.subTest(program=program, kind=kind):
                        out = root / program / kind
                        result = out / "result.json"
                        header = {"ticket": "Krok ticketu", "planning": "Krok planowania Lekkiego karty K-1",
                                  "caretaker": "Jesteś opiekunem karty K-1"}[kind]
                        prompt = (header + "\nATRAPA: plan-question\n"
                                  f"Manifest dowodów sprawy: {evidence / 'attachments.json'}\n")
                        if kind == "caretaker":
                            prompt += (f"Plik wyniku: {root / 'old/result.json'}\n"
                                       f"Worktree kroku (wyłącznie do odczytu): {root}\n")
                        prompt += ("Zapisz wynik. " if kind == "planning" else "") + f"Plik wyniku: {result}\n"
                        reply = subprocess.run([binaries[program], "--session-id", "test"], input=prompt,
                                               cwd=tmp, env=dict(os.environ, HOME=str(root / "home")),
                                               capture_output=True, text=True, timeout=5)
                        self.assertEqual(reply.returncode, 0, reply.stderr)
                        observed = json.loads((out / "read-evidence.json").read_text())
                        self.assertEqual(bytes.fromhex(observed["attachments"][0]["bytes"]), raw)
                        self.assertEqual(observed["attachments"][0]["observedHash"],
                                         manifest["attachments"][0]["sha256"])
                        self.assertEqual(observed["attachments"][0]["writeBits"], 0)
                        self.assertEqual(observed["report"], "# Dowody\n")
                        expected = "done" if kind == "caretaker" else "question"
                        self.assertEqual(json.loads(result.read_text())["result"], expected)
                        self.assertFalse((root / "old/result.json").exists())
