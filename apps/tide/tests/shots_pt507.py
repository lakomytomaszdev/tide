"""Zrzuty PNG do notki ticketu PT-507 (nie jest modułem zestawu testów: nazwa bez `test_`).

`python3 -m unittest apps.tide.tests.shots_pt507` zapisuje w build/pt507-shots/ (katalog ignorowany przez gita;
`TIDE_PT507_SHOTS=<katalog>` zmienia miejsce) zrzuty całego okna Tide z danymi z plików (`tide.py --grab`):
- wide — okno 1380×1000 (pasek boczny 200 px, treść widoku 1180 px): Tablica, Projekty i tagi, Docker, Modele, Sesje,
  Raporty, Worktrees, Sprawy; okno „Nowe zadanie” osobno (tide-new-task.py),
- narrow — najwęższe okno Tide 1380×620 (`minimumWidth`×`minimumHeight` z apps/tide/qml/Main.qml): panel boczny
  z pozycją „Projekty i tagi” (Tablica) i strona Projekty i tagi z powrotem po lewej.
Zawsze QT_QPA_PLATFORM=offscreen i bez magistrali D-Bus — bez okien na pulpicie.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

from . import REPO
from .test_window import QML_PROBLEMS, stage

# Ostrzeżenie „Binding loop … rows” w IssuesView.qml:54 jest sprzed tego ticketu; zrzut go nie sprawdza.
SHOT_PROBLEMS = QML_PROBLEMS.replace("|Binding loop", "")
from apps.board.tests.boardfix import preview_data
from apps.docker.tests import fixture as docker
from apps.issues.tests import fixture as issues
from apps.models.tests import fixture as models
from apps.newtask.tests.test_new_task import sample as newtask_sample
from apps.reports.tests import fixture as reports
from apps.sessions.tests.test_window import fixture_data
from apps.worktrees.tests.test_window import sample as worktrees_sample

DIR = os.environ.get("TIDE_PT507_SHOTS") or os.path.join(REPO, "build", "pt507-shots")
WIDE = (1380, 1000)
NARROW = (1380, 620)


class Pt507Shots(unittest.TestCase):
    def test_write_shots(self):
        os.makedirs(DIR, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tide-pt507-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            files = {}
            for name, value in (("board", preview_data(tmp)), ("docker", docker.data()), ("models", models.sample()),
                                ("reports", reports.sample()), ("sessions", fixture_data(tmp)),
                                ("worktrees", worktrees_sample()), ("issues", issues.data())):
                files[name] = os.path.join(tmp, name + ".json")
                with open(files[name], "w", encoding="utf-8") as fh:
                    json.dump(value, fh, ensure_ascii=False)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                       XDG_CONFIG_DIRS="/etc/xdg", XDG_CONFIG_HOME=os.path.join(tmp, "config"),
                       XDG_DATA_HOME=os.path.join(tmp, "data"))
            env.pop("DBUS_SESSION_BUS_ADDRESS", None)

            def grab(name, size, view, *extra):
                out = os.path.join(DIR, name + ".png")
                argv = ["/usr/bin/python3", os.path.join(pkg, "tide.py"), "--view", view]
                for key, path in files.items():
                    argv += ["--data", f"{key}={path}"]
                run = subprocess.run(argv + list(extra) + ["--grab", out, "--width", str(size[0]), "--height", str(size[1])],
                                     env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(run.returncode, 0, run.stderr[-3000:])
                self.assertIsNone(re.search(SHOT_PROBLEMS, run.stderr), run.stderr[-3000:])

            for view in ("board", "docker", "models", "sessions", "reports", "worktrees", "issues"):
                grab(f"{view}-wide", WIDE, view)
            grab("manage-wide", WIDE, "board", "--select", "projekty")
            grab("rail-narrow", NARROW, "board")
            grab("manage-narrow", NARROW, "board", "--select", "projekty")

            data = os.path.join(tmp, "newtask.json")
            with open(data, "w", encoding="utf-8") as fh:
                json.dump(newtask_sample(), fh)
            out = os.path.join(DIR, "newtask-wide.png")
            newtask = os.path.join(tmp, "newtask")
            subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app",
                            os.path.join(REPO, "apps", "newtask"), newtask], check=True)
            run = subprocess.run(["/usr/bin/python3", os.path.join(newtask, "tide-new-task.py"), "--data", data,
                                  "--prompt", "Popraw liczenie VIN-ów w raporcie", "--grab", out,
                                  "--width", "1180", "--height", "1000"],
                                 env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        self.assertTrue(all(os.path.getsize(os.path.join(DIR, n)) > 5000 for n in os.listdir(DIR)))
