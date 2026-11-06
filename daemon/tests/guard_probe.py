"""Celowo zielony test wewnętrzny; uruchamiany wyłącznie przez test kontrolny strażnika.

Nazwa bez test* wyklucza automatyczne discover. Cele pochodzą z syntetycznego
HOME/XDG testu kontrolnego, nigdy z katalogów bieżącego użytkownika.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from . import DAEMON_DIR


JOURNAL_CODE = '''
import os, sys
sys.path.insert(0, sys.argv[1])
from tide_daemon.cardflow.journal import Journal
from tools.test_guard import _inherited
assert _inherited(), "brak filtra odziedziczonego przez exec"
try:
    Journal(sys.argv[2]).append("K-1", "synthetic.guard")
except PermissionError:
    print("SERVICE SWALLOWED", flush=True)
'''


class GuardProbe(unittest.TestCase):
    def test_swallowed_violation(self):
        action = os.environ["TIDE_GUARD_PROBE_ACTION"]
        target = Path(os.environ["TIDE_GUARD_PROBE_TARGET"])
        other = Path(os.environ["TIDE_GUARD_PROBE_OTHER"])
        if action.startswith("shell-"):
            script = {"shell-create": 'printf danger > "$TIDE_GUARD_PROBE_TARGET"',
                      "shell-overwrite": 'printf danger > "$TIDE_GUARD_PROBE_TARGET"',
                      "shell-unlink": 'rm -- "$TIDE_GUARD_PROBE_TARGET"',
                      "shell-rename": 'mv -- "$TIDE_GUARD_PROBE_TARGET" "$TIDE_GUARD_PROBE_OTHER"',
                      "shell-rename-into": 'mv -- "$TIDE_GUARD_PROBE_OTHER" "$TIDE_GUARD_PROBE_TARGET"'}[action]
            # Obcy proces rzeczywiście wykonuje operację; rodzic ignoruje jego wynik.
            result = subprocess.run(["/bin/sh", "-c", script], check=False)
            self.assertNotEqual(result.returncode, 0, "obcy proces ominął strażnika")
        elif action in ("service", "grandchild", "cleanenv"):
            env = dict(os.environ, XDG_STATE_HOME=os.environ["TIDE_GUARD_PROBE_OLD_STATE"])
            if action == "cleanenv":
                env = {key: value for key, value in env.items() if not key.startswith("TIDE_TEST_")}
            command = [sys.executable, "-c", JOURNAL_CODE, DAEMON_DIR, str(other)]
            if action == "grandchild":
                command = [sys.executable, "-c", '''
import json, subprocess, sys
subprocess.run(json.loads(sys.argv[1]), check=False)
''', json.dumps(command)]
            # Proces używa prawdziwego domyślnego Journal i połyka jego odmowę.
            self.assertEqual(subprocess.run(command, env=env, check=False).returncode, 0)
        elif action in ("orphan", "orphan-success", "late-orphan"):
            gate, done = other.with_name("orphan-gate"), other.with_name("orphan-done")
            code = '''
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tide_daemon.cardflow.journal import Journal
if os.fork():
    os._exit(0)
gate, done, other = map(Path, sys.argv[2:5])
while not gate.exists():
    time.sleep(.01)
if sys.argv[5] == "late-orphan":
    while True:
        try:
            os.kill(int(sys.argv[6]), 0)
        except ProcessLookupError:
            break
        time.sleep(.01)
try:
    if sys.argv[5] != "orphan-success":
        Journal(str(other)).append("K-1", "synthetic.orphan")
    else:
        other.write_text("legal orphan")
except PermissionError:
    print("SERVICE SWALLOWED", flush=True)
done.write_text("done")
'''
            env = dict(os.environ, XDG_STATE_HOME=os.environ["TIDE_GUARD_PROBE_OLD_STATE"])
            self.assertEqual(subprocess.run([sys.executable, "-c", code, DAEMON_DIR, str(gate),
                                            str(done), str(other), action, str(os.getpid())], env=env).returncode, 0)
            gate.touch()  # rodzic dalszego potomka już się zakończył
            if action != "late-orphan":
                deadline = time.monotonic() + 3
                while not done.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(done.exists(), "osierocony proces stracił ochronę lub prawo legalnego zapisu")
        elif action == "futimens":
            with other.open("r+b") as stream:
                os.utime(stream.fileno(), ns=(123000000000, 123000000000))
            self.assertEqual(other.stat().st_mtime_ns, 123000000000)
        elif action == "unlink-alias":
            target.unlink()
        elif action == "leaked-child":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], start_new_session=True)
            other.with_name("leaked-pid").write_text(str(child.pid))
        elif action == "wait":
            print("GUARD READY", flush=True)
            sys.stdin.readline()
        elif action == "success":
            pass
        elif action == "exit":
            try:
                target.write_text("danger")
            except PermissionError:
                pass
            os._exit(0)
        else:
            try:
                if action in ("create", "overwrite"):
                    target.write_text("danger")
                elif action == "unlink":
                    target.unlink()
                elif action == "rename":
                    target.rename(other)
                elif action == "rename-into":
                    other.rename(target)
                elif action == "ancestor":
                    target.rename(other)
                elif action == "dirfd":
                    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.open(target.name, os.O_WRONLY | os.O_CREAT, dir_fd=fd)
                    finally:
                        os.close(fd)
                elif action == "fdwrite":
                    os.write(int(os.environ["TIDE_GUARD_PROBE_FD"]), b"danger")
                else:
                    raise AssertionError(action)
            except PermissionError:
                print("PYTHON SWALLOWED", flush=True)
            else:
                self.fail("Python ominął strażnika")
        print("INNER GREEN", flush=True)
