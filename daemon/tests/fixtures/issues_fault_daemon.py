"""Rzeczywisty daemon z jednorazową awarią I/O; wyłącznie fixture testów PT-228."""
import errno
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

control = Path(sys.argv[1])
sys.argv = sys.argv[2:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
from tide_daemon import issues
from tide_daemon import issue_acceptance


def trigger(stage):
    if not control.exists():
        return
    data = json.loads(control.read_text())
    if data["stage"] != stage:
        return
    control.unlink()
    if data["action"] == "crash":
        os._exit(91)
    raise OSError(errno.ENOSPC, "wymuszony brak miejsca")


write_blob = issues.IssueStore._write_blob
atomic = issues._atomic


def blob(path, raw):
    stage = "copy" if path.parent.name == "attachments" else "manifest"
    trigger("before-" + stage)
    if stage == "copy" and control.exists() and json.loads(control.read_text())["stage"] == "during-copy":
        fd, tmp = tempfile.mkstemp(prefix=".tide-tmp-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw[:len(raw) // 2])
            stream.flush()
            trigger("during-copy")
    write_blob(path, raw)
    trigger("after-" + stage)


def report(path, text):
    publish = path.name == "report.md" and path.parent.name.startswith("S-")
    if publish:
        trigger("before-publish")
    atomic(path, text)
    if publish:
        trigger("after-publish")


issues.IssueStore._write_blob = staticmethod(blob)
issues._atomic = report
accept_atomic = issue_acceptance._atomic


def accept_write(path, text):
    stage = None
    if path.parent.name == "tide-accept-requests": stage = "request"
    elif path.parent.name == "tide-acceptances":
        data = json.loads(text)
        stage = "done" if data["done"] else "published" if data["published"] else "intent"
    elif path.name == "tide-next-card": stage = "reserve"
    elif path.parent.name == "accepted": stage = "snapshot" if path.name == "report.md" else "manifest"
    elif path.parent.name == "cards": stage = "card"
    elif path.name == "board.md": stage = "counter"
    elif path.name == "report.md": stage = "accepted"
    if stage:
        trigger("accept-before-" + stage)
    accept_atomic(path, text)
    if stage:
        trigger("accept-after-" + stage)


issue_acceptance._atomic = accept_write
complete = issue_acceptance._complete


def accept_complete(store, ident):
    trigger("accept-before-complete")
    complete(store, ident)
    trigger("accept-after-complete")


issue_acceptance._complete = accept_complete
runpy.run_path(sys.argv[0], run_name="__main__")
