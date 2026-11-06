"""Gotowość fake-call.json oznacza kompletny JSON, także przy wstrzymanym zapisie dużego promptu."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from . import flowfakes

SLOW_JSON = '''import json, os, time
from pathlib import Path
original_dump = json.dump
first = True
def blocked_dump(data, stream, *args, **kwargs):
    global first
    if first:
        first = False
        stream.write('{"prompt":')
        stream.flush()
        Path(os.environ["WRITER_STARTED"]).write_text("partial")
        while not Path(os.environ["WRITER_RELEASE"]).exists():
            time.sleep(0.005)
        stream.seek(0)
        stream.truncate()
    return original_dump(data, stream, *args, **kwargs)
json.dump = blocked_dump
'''


class PublicationTest(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("nie osiągnięto bariery procesu atrapy")

    def test_partial_write_is_not_published_as_a_ready_fake_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            (root / "sitecustomize.py").write_text(SLOW_JSON)
            fake = flowfakes.install(str(root / "bin"))["claude"]
            started, release = root / "started", root / "release"
            prompt = f"ATRAPA: czekaj\nPlik wyniku: {out / 'result.json'}\n" + "x" * 20000
            env = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "PYTHONPATH": str(root),
                   "WRITER_STARTED": str(started), "WRITER_RELEASE": str(release)}
            proc = subprocess.Popen([sys.executable, fake, "--session-id", "publication-test"], cwd=root,
                                    env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
                self.wait_for(started.exists)
                path = out / "fake-call.json"
                self.assertFalse(path.exists(), "czytelnik widzi plik gotowości podczas zapisu niepełnego JSON-a")
                release.write_text("continue")
                self.wait_for(path.exists)
                data = json.loads(path.read_text())
                self.assertEqual(data["prompt"], prompt)
                self.assertEqual(data["argv"], ["--session-id", "publication-test"])
                self.assertEqual(list(out.glob(".fake-call-*")), [])
            finally:
                release.write_text("continue")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stderr.close()
