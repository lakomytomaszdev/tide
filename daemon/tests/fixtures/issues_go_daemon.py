"""Prawdziwa usługa z kontrolowanymi przerwaniami na granicy StartCard (PT-222)."""
import json
import os
from pathlib import Path
import runpy
import sys

control = Path(sys.argv[1])
sys.argv = sys.argv[2:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon import issue_acceptance


def trigger(stage):
    if not control.exists():
        return
    value = json.loads(control.read_text())
    if value["stage"] != stage:
        return
    if not value.get("persistent"):
        control.unlink()
    if value["action"] == "crash":
        os._exit(91)
    if value["action"] == "refuse":
        raise FlowError("test-refusal", "Kontrolowana przyczyna nieudanego przekazania")
    raise OSError("Kontrolowana awaria zapisu")


original_start = FlowEngine._start


def start(self, *args, **kwargs):
    if kwargs.get("_order"):
        trigger("before-handoff")
    return original_start(self, *args, **kwargs)


FlowEngine._start = start
original_append = Journal.append


def append(self, card, kind, **data):
    trigger("before-" + kind)
    result = original_append(self, card, kind, **data)
    trigger("after-" + kind)
    return result


Journal.append = append
original_save = issue_acceptance._save


def save(store, path, value):
    original_save(store, path, value)
    if path.startswith(".git/tide-issue-starts/") and value.get("state") == "prepared":
        trigger("after-prepared")
    if path.startswith(".git/tide-issue-start-results/") and "response" in value:
        trigger("after-response")


issue_acceptance._save = save
runpy.run_path(sys.argv[0], run_name="__main__")
