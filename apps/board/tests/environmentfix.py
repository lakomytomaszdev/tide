"""Sekcja „Środowisko karty” (PT-172) dla testów panelu: model nie jest wpisany ręcznie — liczy go usługa
(`cardflow.envconsent.model`) z profilu projektu w tymczasowej Tablicy, propozycji `out/box.yaml` i dziennika karty,
więc panel dostaje dokładnie kontrakt `derived.flow.plan.environment`. `plan_card(data, ...)` wkłada plan do
zatwierdzenia z tą sekcją do karty K-6 w kopii danych podglądu.
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

from . import REPO

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.box import profile as bp  # noqa: E402
from tide_daemon.box import state as bs  # noqa: E402
from tide_daemon.cardflow import envconsent  # noqa: E402
from tide_daemon.cardflow.state import fold  # noqa: E402

CARD = "K-6"
SLUG = "plasma-tide"

PROJECT = {"tide-box": 1, "stacks": ["docker"],
           "docker": {"enabled": True, "images": ["testcontainers/ryuk:0.11.0"]}}
CARD_BOX = {"tide-box": 1, "network": {"domains": ["api.example.org", "github.com"]},
            "ports": {"pool": [4300, 4399]}}
ROW = {"workId": "#1", "title": "Eksport", "class": "standard", "executor": "Sonnet", "reviewer": "Opus",
       "blockers": [], "note": "", "editable": True, "body": "# Eksport", "cardPath": "/board/tickets/1.md"}


class Engine:
    """Tyle silnika, ile czyta `envconsent.model`: projekt karty."""

    def __init__(self, repo):
        self.repo = repo

    def _project(self, card):
        return None, SimpleNamespace(slug=SLUG, repo=self.repo)


def events(box, wait=""):
    items = [{"type": "card.started", "entry": "plan", "executor": "Opus", "program": "claude", "provider": "anthropic"},
             {"type": "plan.proposed", "planner": "Opus", "rows": [ROW], "box": box},
             {"type": "plan.reviewed", "reviewer": "Sonnet", "verdict": "APPROVE", "findings": []}]
    if wait:
        items.append({"type": "box.wait", "value": wait})
    return [{"v": 1, "seq": n, "ts": f"2026-11-03T10:{n:02d}:00+01:00", "card": CARD, **e}
            for n, e in enumerate(items, 1)]


def environment(project=PROJECT, box=CARD_BOX, wait="", prepare=None, repo_files=None) -> dict:
    """Model sekcji z usługi. `prepare(board_dir)` może dopisać stan pudełka (np. migawkę `clean`)."""
    with tempfile.TemporaryDirectory(prefix="tide-env-board-") as board, \
            tempfile.TemporaryDirectory(prefix="tide-env-repo-") as repo:
        for name, body in (repo_files or {}).items():
            with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        with mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": board}):
            if project is not None:
                bp.save_project(SLUG, project)
            if prepare:
                prepare()
            return json.loads(json.dumps(envconsent.model(Engine(repo), CARD, fold(events(box, wait)))))


def clean_snapshot_then_change():
    """Pudełko `clean` zbudowane z `PROJECT`; profil projektu zmieniony potem (inny obraz) — „do przebudowy”."""
    recipe = envconsent._stamp(PROJECT)
    name = bs.box_name(SLUG)
    bs.record(SLUG, "box.approved", name, source="ui", accepted_red=[], items=["docker", "image:testcontainers/ryuk:0.11.0"],
              recipe=recipe)
    bs.record(SLUG, "box.ready", name, hash=recipe, duration=1.0)
    bp.save_project(SLUG, {**PROJECT, "docker": {"enabled": True, "images": ["postgres:17"]}})


def plan_card(data: dict, model: dict, status: str = "pending", wait: str = "") -> dict:
    """Kopia danych podglądu, w której K-6 ma plan z sekcją środowiska."""
    data = json.loads(json.dumps(data))
    for card in data["cards"]:
        if card["id"] == CARD:
            card["derived"] = {"column": "waiting", "note": "plan do zatwierdzenia", "flow": {
                "entry": "plan", "boxWait": wait, "plan": {
                    "status": status, "errors": [], "waves": [["#1"]], "rows": [ROW], "environment": model}}}
            card["boardColumn"] = "waiting"
    return data
