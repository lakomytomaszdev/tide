"""Okno „Tide: nowe zadanie”.

- pakiet: launcher, QML i biblioteka z Tokens.qml; bez kolorów wpisanych na sztywno,
- QML (qml6, offscreen) na przykładowych danych: zapamiętany wybór, szukanie repozytoriów bez
  polskich znaków, co blokuje start, propozycja gałęzi i ręczna zmiana, prośba dla usługi,
- launcher: zrzut okna z --data (bez D-Bus) w kilku stanach,
- zrzuty ekranu: kopie przeciągniętych plików, obraz ze schowka, „Zrób zrzut” przez atrapę
  Spectacle, usuwanie, sprzątanie katalogu po zamknięciu bez startu,
- backend na prywatnej magistrali z prawdziwym tide-daemon (Defaults, Repos, odmowa Start)
  i drugie uruchomienie, które tylko prosi o pokazanie okna,
- instalacja i odinstalowanie (okno, .desktop, ikona, profil Konsoli) na tymczasowym HOME.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import APP_DIR, REPO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

_GUI = QGuiApplication.instance() or QGuiApplication([])  # zanim privatebus utworzy QCoreApplication

from daemon.tests import gitrepos  # noqa: E402
from daemon.tests.fakebin import calls, files_under, make_env  # noqa: E402
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until  # noqa: E402

NOW = 1_793_016_000
QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|Cannot read"


def sample() -> dict:
    def repo(name, ago, branch="master", main="master", dirty=0):
        return {"name": name, "path": f"/w/{name}", "branch": branch, "mainBranch": main, "dirty": dirty,
                "worktreeDir": f"/w/{name}/.claude/worktrees", "lastActivityTs": NOW - ago}
    return {
        "now": NOW,
        "repos": [repo("plasma-tide", 120), repo("Zażółć", 3600, dirty=2), repo("legal-agents", 7200, "feat/x", "main"),
                  repo("trunk-only", 86400, "trunk", "")],
        "defaults": {"repoPath": "/w/legal-agents", "tool": "codex", "newWorktree": True,
                     "tools": {"claude": True, "codex": True}, "terminal": True},
        "suggestion": {"ok": True, "branch": "codex/popraw-liczenie", "path": "/w/legal-agents/.claude/worktrees/popraw-liczenie",
                       "base": "main"},
    }


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", APP_DIR, out], check=True)
    return out


def run_qml(path: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", XDG_CONFIG_DIRS="/etc/xdg")
    return subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=60).stderr


def warned(log: str, tag: str) -> str:
    match = re.search(rf"{tag} (.*)$", log, re.M)
    return match.group(1).strip() if match else f"<brak {tag}>\n{log[-3000:]}"


def load_launcher():
    spec = importlib.util.spec_from_file_location("tide_new_task_window", os.path.join(APP_DIR, "tide-new-task.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(path: str, color: str = "#6fd6c6") -> str:
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(path, "PNG")
    return path


class PackageTest(unittest.TestCase):
    def test_package_and_palette(self):
        with tempfile.TemporaryDirectory(prefix="tide-nt-pkg-") as tmp:
            files = files_under(stage(os.path.join(tmp, "pkg")))
        for name in ("tide-new-task.py", "qml/Main.qml", "qml/NewTaskPage.qml", "qml/newtask.js", "qml/PillButton.qml",
                     "qml/tide/Tokens.qml", "qml/tide/Theme.qml", "qml/tide/qmldir"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])
        for dirpath, _dirs, names in os.walk(os.path.join(APP_DIR, "qml")):
            for name in names:
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    def test_form_logic(self):
        with tempfile.TemporaryDirectory(prefix="tide-nt-qml-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            harness = os.path.join(pkg, "qml", "Smoke.qml")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write("""import QtQuick
import "newtask.js" as Logic
Item {
    id: root
    width: 860; height: 790
    property var requests: []
    property var suggested: []
    readonly property var sample: %(data)s
    NewTaskPage {
        id: page
        anchors.fill: parent
        nowTs: %(now)d
        homeDir: "/w"
        repos: root.sample.repos
        defaults: root.sample.defaults
        images: ["/t/zrzut-1.png"]
        onStartRequested: request => root.requests.push(JSON.parse(request))
        onSuggestRequested: (repo, tool, prompt) => root.suggested.push([repo, tool, prompt])
    }
    Timer {
        interval: 400; running: true
        onTriggered: {
            console.warn("DEFAULTS " + [page.repoPath, page.tool, page.newWorktree].join("|"))
            console.warn("FILTER " + Logic.filterRepos(page.repos, "zolc").map(r => r.name).join(",") + "|"
                         + Logic.filterRepos(page.repos, "tide").map(r => r.name).join(",") + "|"
                         + Logic.filterRepos(page.repos, "a").map(r => r.name).join(","))
            console.warn("EMPTY " + page.blocker + "|" + page.canStart)
            page.prompt = "jedno"
            console.warn("ONEWORD " + page.blocker)
            page.prompt = 'Popraw "liczenie"\\nw raporcie'
            console.warn("MISSING " + page.blocker)
            page.suggestion = root.sample.suggestion
            console.warn("BRANCH " + page.branch + "|" + page.blocker + "|" + page.canStart)
            page.start()
            console.warn("REQUEST " + JSON.stringify(root.requests[0]))
            page.branchEdited = true
            page.branch = "fix/moja"
            page.suggestion = ({ branch: "codex/inna" })
            console.warn("EDITED " + page.branch + "|" + page.worktreePath)
            page.newWorktree = false
            page.start()
            console.warn("MAIN " + root.requests[1].newWorktree + "|" + root.requests[1].branch + "|"
                         + page.hint.text + "|" + page.hint.warn)
            page.repoPath = "/w/Zażółć"
            page.newWorktree = true
            console.warn("DIRTY " + page.hint.text + "|" + page.hint.warn)
            page.repoPath = "/w/trunk-only"
            console.warn("NOMAIN " + page.blocker)
            console.warn("COUNT " + [Logic.charCount("a"), Logic.charCount("abc"), Logic.charCount("x".repeat(12)),
                                     Logic.charCount("x".repeat(22))].join("|"))
            console.warn("AGO " + Logic.changesAgo(%(now)d - 720, %(now)d) + "|" + Logic.shortAgo(%(now)d - 7200, %(now)d))
            console.warn("OUTCOME " + Logic.outcome(page.repos[0], "codex", true) + "|"
                         + Logic.outcome(page.repos[0], "claude", false))
            console.warn("SUGGESTED " + root.suggested.length)
            Qt.quit()
        }
    }
}
""" % {"data": json.dumps(sample(), ensure_ascii=False), "now": NOW})
            log = run_qml(harness)
        self.assertEqual([l for l in log.splitlines() if re.search(QML_PROBLEMS, l)], [])
        self.assertEqual(warned(log, "DEFAULTS"), "/w/legal-agents|codex|true")
        self.assertEqual(warned(log, "FILTER"), "Zażółć|plasma-tide|plasma-tide,Zażółć,legal-agents")
        self.assertEqual(warned(log, "EMPTY"), "Napisz, co agent ma zrobić|false")
        self.assertEqual(warned(log, "ONEWORD"), "Opisz zadanie co najmniej dwoma słowami")
        self.assertEqual(warned(log, "MISSING"), "Podaj nazwę gałęzi")
        self.assertEqual(warned(log, "BRANCH"), "codex/popraw-liczenie||true")
        self.assertEqual(json.loads(warned(log, "REQUEST")), {
            "repoPath": "/w/legal-agents", "tool": "codex", "prompt": 'Popraw "liczenie"\nw raporcie',
            "newWorktree": True, "branch": "codex/popraw-liczenie", "images": ["/t/zrzut-1.png"]})
        self.assertEqual(warned(log, "EDITED"), "fix/moja|/w/legal-agents/.claude/worktrees/fix-moja")
        self.assertEqual(warned(log, "MAIN"), "false||Agent pracuje w głównym katalogu na feat/x|false")
        self.assertEqual(warned(log, "DIRTY"),
                         "Na master są niezacommitowane zmiany — nowy worktree ich nie zawiera|true")
        self.assertEqual(warned(log, "NOMAIN"), "Repozytorium nie ma master, main ani develop — wyłącz „Nowy worktree”")
        self.assertEqual(warned(log, "COUNT"), "1 znak|3 znaki|12 znaków|22 znaki")
        self.assertEqual(warned(log, "AGO"), "zmiany 12 min temu|2 h")
        self.assertEqual(warned(log, "OUTCOME"), "Otworzy się nowa karta Konsoli z Codexem w tym worktree.|"
                                                 "Otworzy się nowa karta Konsoli z Claude Code w plasma-tide.")
        self.assertNotEqual(warned(log, "SUGGESTED"), "0")


class LauncherTest(unittest.TestCase):
    def test_grab_with_static_data_in_several_states(self):
        with tempfile.TemporaryDirectory(prefix="tide-nt-grab-") as tmp:
            pkg = stage(os.path.join(tmp, "pkg"))
            data = os.path.join(tmp, "data.json")
            with open(data, "w", encoding="utf-8") as fh:
                json.dump(sample(), fh)
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_DIRS="/etc/xdg",
                       XDG_CONFIG_HOME=os.path.join(tmp, "config"), XDG_DATA_HOME=os.path.join(tmp, "data"))
            env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # --data nie potrzebuje magistrali
            shot = png(os.path.join(tmp, "zrzut-1.png"))
            base = ["/usr/bin/python3", os.path.join(pkg, "tide-new-task.py"), "--data", data]
            outputs = []
            for extra in (["--prompt", "Popraw liczenie VIN-ów w raporcie", "--image", shot],
                          ["--prompt", "Popraw", "--no-worktree", "--error", "gałąź codex/x już istnieje — zmień nazwę"]):
                out = os.path.join(tmp, f"shot-{len(outputs)}.png")
                run = subprocess.run(base + extra + ["--grab", out], env=env, timeout=60, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual([l for l in run.stderr.splitlines() if re.search(QML_PROBLEMS, l)], [])
                with open(out, "rb") as fh:
                    content = fh.read()
                self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
                self.assertGreater(len(content), 10_000)
                outputs.append(content)
            self.assertNotEqual(outputs[0], outputs[1])

    def test_shortcut_text_follows_kglobalshortcutsrc(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory(prefix="tide-nt-keys-") as tmp:
            rc = os.path.join(tmp, "kglobalshortcutsrc")
            self.assertEqual(launcher.shortcut_text(rc), "Meta+Alt+A")
            with open(rc, "w", encoding="utf-8") as fh:
                fh.write("[kwin]\nTideNewAgentTask=Meta+Ctrl+N\tMeta+Alt+A,Meta+Alt+A,Tide: nowe zadanie\n")
            self.assertEqual(launcher.shortcut_text(rc), "Meta+Ctrl+N")


class ImagesTest(unittest.TestCase):
    """Zrzuty bez usługi: Backend(None) z katalogiem zadań w tymczasowym XDG_DATA_HOME."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-nt-img-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.launcher = load_launcher()
        self.backend = self.launcher.Backend(None, spectacle="")
        self.backend.images.root = os.path.join(self.tmp, "tasks")
        self.problems = []
        self.backend.imageProblem.connect(self.problems.append)

    def paths(self):
        return json.loads(self.backend.imagesJson)

    def test_drop_paste_remove_and_discard(self):
        source = png(os.path.join(self.tmp, "ekran.png"))
        notes = os.path.join(self.tmp, "notatki.txt")
        open(notes, "w").close()
        self.backend.addFiles([f"file://{source}", f"file://{notes}"])
        self.assertEqual(len(self.paths()), 1)
        copied = self.paths()[0]
        self.assertEqual(os.path.dirname(os.path.dirname(copied)), self.backend.images.root)
        self.assertTrue(os.path.isfile(copied) and copied != source)
        self.assertIn("notatki.txt to nie obraz", self.problems[0])
        QGuiApplication.clipboard().setImage(QImage(png(os.path.join(self.tmp, "schowek.png"), "#f5b99a")))
        self.assertTrue(self.backend.pasteImage())
        self.assertEqual(len(self.paths()), 2)
        self.assertTrue(self.paths()[1].endswith("zrzut-2.png"))
        QGuiApplication.clipboard().setText("zwykły tekst")
        self.assertFalse(self.backend.pasteImage(), "tekst wkleja pole tekstowe, nie zrzuty")
        self.backend.removeImage(copied)
        self.assertFalse(os.path.exists(copied))
        folder = self.backend.images.folder
        self.backend.discard()
        self.assertFalse(os.path.exists(folder), "zamknięte bez startu — katalog zadania znika")
        self.assertTrue(os.path.isfile(source), "oryginał przeciągniętego pliku zostaje")

    def test_kept_after_start(self):
        self.backend.addFiles([f"file://{png(os.path.join(self.tmp, 'a.png'))}"])
        self.backend.finish()
        self.backend.discard()
        self.assertTrue(os.path.isfile(self.paths()[0]))

    def test_capture_with_fake_spectacle(self):
        fake = os.path.join(self.tmp, "spectacle")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\necho \"$*\" > \"$(dirname \"$0\")/spectacle.args\"\n"
                     "while [ $# -gt 0 ]; do [ \"$1\" = -o ] && cp \"$TIDE_FAKE_SHOT\" \"$2\"; shift; done\n")
        os.chmod(fake, 0o755)
        os.environ["TIDE_FAKE_SHOT"] = png(os.path.join(self.tmp, "region.png"))
        self.addCleanup(os.environ.pop, "TIDE_FAKE_SHOT", None)
        backend = self.launcher.Backend(None, spectacle=fake)
        backend.images.root = os.path.join(self.tmp, "tasks")
        self.assertTrue(backend.canCapture)
        finished = []
        backend.captureFinished.connect(finished.append)
        backend.capture()
        self.assertTrue(wait_until(lambda: finished, timeout=10))
        self.assertEqual(finished, [""])
        self.assertEqual(len(json.loads(backend.imagesJson)), 1)
        with open(os.path.join(self.tmp, "spectacle.args"), encoding="utf-8") as fh:
            args = fh.read().split()
        self.assertEqual(args[:4], ["-b", "-n", "-r", "-o"])
        # Anulowane zaznaczanie (Spectacle bez pliku) — bez zrzutu i bez błędu.
        os.environ["TIDE_FAKE_SHOT"] = os.path.join(self.tmp, "nie-ma.png")
        finished.clear()
        backend.capture()
        self.assertTrue(wait_until(lambda: finished, timeout=10))
        self.assertEqual((finished, len(json.loads(backend.imagesJson))), ([""], 1))
        backend.discard()


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class BackendOverDbusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-nt-dbus-")
        cls.root = os.path.join(cls.tmp, "work")
        cls.alpha = os.path.join(cls.root, "alpha")
        os.makedirs(cls.alpha)
        gitrepos.git(cls.alpha, "init", "-q", "-b", "master")
        gitrepos.commit(cls.alpha, "README.md")
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.bin)
        for name in ("claude", "codex", "konsole"):
            path = os.path.join(cls.bin, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        cls.service = PrivateBus("tide-nt-test")
        cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--no-notifications", "--no-krunner", "--no-idle", "--workspace", cls.root,
            extra_env={"PATH": cls.bin + os.pathsep + "/usr/bin:/bin", "HOME": os.path.join(cls.tmp, "home"),
                       "XDG_DATA_HOME": os.path.join(cls.tmp, "data"),
                       "XDG_RUNTIME_DIR": os.path.join(cls.tmp, "run"),
                       "XDG_STATE_HOME": os.path.join(cls.tmp, "state")})
        cls.launcher = load_launcher()

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_defaults_repos_and_refused_start(self):
        backend = self.launcher.Backend(self.service.bus)
        self.assertTrue(wait_until(lambda: json.loads(backend.reposJson), timeout=20))
        repos = json.loads(backend.reposJson)
        self.assertEqual([r["name"] for r in repos], ["alpha"])
        defaults = json.loads(backend.defaultsJson)
        self.assertEqual(defaults["tools"], {"claude": True, "codex": True})
        self.assertTrue(backend.available)
        self.assertEqual(backend.images.root, os.path.join(self.tmp, "data", "tide", "tasks"))
        backend.suggest(self.alpha, "claude", "Popraw raport miesięczny")
        self.assertTrue(wait_until(lambda: json.loads(backend.suggestionJson).get("branch"), timeout=10))
        self.assertEqual(json.loads(backend.suggestionJson)["branch"], "claude/popraw-raport-miesieczny")
        results = []
        backend.startFinished.connect(results.append)
        backend.start(json.dumps({"repoPath": self.alpha, "tool": "claude", "prompt": "Popraw raport",
                                  "newWorktree": True, "branch": "master", "images": []}))
        self.assertTrue(backend.busy)
        self.assertTrue(wait_until(lambda: results, timeout=20))
        self.assertFalse(backend.busy)
        result = json.loads(results[0])
        self.assertEqual((result["ok"], result["error"]), (False, "protected-branch"))
        self.assertEqual(gitrepos.branches(self.alpha), ["master"])

    def test_second_launch_only_raises_first_window(self):
        from PyQt6.QtDBus import QDBusConnection
        first = load_launcher()
        backend = first.Backend(None, {})
        raised = []
        backend.raiseRequested.connect(lambda: raised.append(True))
        service = first.WindowService(backend)
        self.assertTrue(self.service.bus.registerService(first.WINDOW_SERVICE))
        self.service.bus.registerObject(first.WINDOW_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)
        try:
            env = dict(self.service.env, QT_QPA_PLATFORM="offscreen")
            proc = subprocess.Popen(["/usr/bin/python3", os.path.join(APP_DIR, "tide-new-task.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertTrue(wait_until(lambda: raised, timeout=15), "drugie uruchomienie nie poprosiło o Raise")
            self.assertEqual(proc.wait(15), 0)
        finally:
            self.service.bus.unregisterObject(first.WINDOW_PATH)
            self.service.bus.unregisterService(first.WINDOW_SERVICE)


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-nt-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.data = self.env["XDG_DATA_HOME"]

    def make(self, target):
        return subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def test_install_then_uninstall(self):
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "konsolerc"), "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nDefaultProfile=Tide.profile\n\n[General]\nDefaultProfile=Inny.profile\n")
        out = self.make("install-new-task")
        self.assertIn("make install-daemon", out, "bez usługi instalator mówi, czego brakuje")
        dest = os.path.join(self.data, "tide/new-task")
        for name in ("tide-new-task.py", "qml/Main.qml", "qml/NewTaskPage.qml", "qml/tide/Tokens.qml"):
            self.assertIn(name, files_under(dest))
        self.assertTrue(os.access(os.path.join(dest, "tide-new-task.py"), os.X_OK))
        desktop = os.path.join(self.data, "applications/tide-new-task.desktop")
        with open(desktop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"Exec=/usr/bin/python3 -s {dest}/tide-new-task.py\n", text)
        self.assertIn("StartupWMClass=tide-new-task\n", text)
        if shutil.which("desktop-file-validate"):
            check = subprocess.run(["desktop-file-validate", desktop], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        with open(os.path.join(self.data, "icons/hicolor/256x256/apps/tide-new-task.png"), "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        with open(os.path.join(self.data, "konsole/TideTask.profile"), encoding="utf-8") as fh:
            profile = fh.read()
        launcher = os.path.join(self.data, "tide/daemon/tide_daemon/tasks/launch.py")
        self.assertEqual(profile, "[General]\nName=Tide: zadanie agenta\nParent=Tide.profile\n"
                                  f"Command=/usr/bin/python3 -s {launcher}\nX-Tide-Component=new-task\n")
        self.assertIn(f"update-desktop-database {self.data}/applications", calls(self.env))

        # Cudzy profil o tej samej nazwie przy odinstalowaniu zostaje.
        self.make("uninstall-new-task")
        self.assertEqual(files_under(self.data), [])
        os.makedirs(os.path.join(self.data, "konsole"))
        with open(os.path.join(self.data, "konsole/TideTask.profile"), "w", encoding="utf-8") as fh:
            fh.write("[General]\nName=Mój\n")
        self.make("uninstall-new-task")
        self.assertEqual(files_under(self.data), ["konsole/TideTask.profile"])

    def test_profile_parent_falls_back_to_builtin(self):
        self.make("install-new-task")
        with open(os.path.join(self.data, "konsole/TideTask.profile"), encoding="utf-8") as fh:
            self.assertIn("Parent=FALLBACK/\n", fh.read())


if __name__ == "__main__":
    unittest.main()
