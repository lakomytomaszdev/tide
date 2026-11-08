"""Ochrona zatwierdzenia kontraktu testów przed krokiem agenta (K-69, PT-286).

- `approval_for_step(project, step, board)`: kolizja rzeczywistych ścieżek magazynu odcisków z obszarami zapisu
  kroku (z tej samej funkcji `agentcmd.write_areas`, z której powstają argumenty piaskownicy) daje `False` z powodem
  i błąd Tablicy; bez kolizji wynik daje odczyt magazynu (PT-285),
- `UpsertProject` z `tests` od procesu w jednostce `tide-step-*` albo bez ustalonego PID jest odrzucany bez zmiany
  `box.yaml` i odcisku (atrapa `/proc`); bez `tests` — bez zmian.
Prawdziwy model ani prawdziwa piaskownica nie są tu uruchamiane (próby piaskownicy: notka PT-286).
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon import call_origin
from tide_daemon.board import contract_approval as approval
from tide_daemon.board.store import BoardStore
from tide_daemon.tasks import agentcmd

from . import gitrepos
from .test_board_core import BoardCase, git_out

CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
STEP_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/tide-step-K-1-PT-1-1.scope\n"
APP_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-org.kde.konsole-1234.scope\n"


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _inside(path, folder):
    return approval._inside(os.path.realpath(path), os.path.realpath(folder))


class StepCase(BoardCase):
    """Tablica z projektem `app` i zatwierdzonym kontraktem, repozytorium z worktree kroku i katalog kroku."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.code = os.path.join(self.ws, "code")
        os.makedirs(self.code)
        gitrepos.git(self.code, "init", "-q", "-b", "master")
        gitrepos.commit(self.code, "a.txt")
        self.worktree = os.path.join(self.ws, "code-wt", "pt-1")
        gitrepos.git(self.code, "worktree", "add", "-q", "-b", "ticket/pt-1", self.worktree)
        self.step_dir = self.worktree + ".step"
        self.out = os.path.join(self.step_dir, "out")
        self.tmp_root = os.path.join(self.tmp, "agent-tmp")
        os.makedirs(self.tmp_root)
        self.state = os.path.join(self.tmp, "state", "tide", approval.FILE_NAME)
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.code})
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})   # zapis pliku bez zatwierdzenia
        # Katalog tymczasowy testu w katalogu tymczasowym sandbox-runtime (test uruchomiony w kroku Claude) byłby
        # obszarem zapisu każdego kroku Claude — wtedy dla tych testów bez zapisu domyślnego programu.
        if any(_inside(self.tmp, area) for area in agentcmd.claude_implicit_tmp()):
            patcher = mock.patch.object(agentcmd, "claude_implicit_tmp", lambda: ())
            patcher.start()
            self.addCleanup(patcher.stop)

    def step(self, tool="claude", **kw):
        args = dict(tool=tool, binary="/bin/" + tool, prompt="Zrób", worktree=self.worktree, repo=self.code,
                    step_dir=self.step_dir, step_id="K-1-PT-1-1", session_id="11111111-2222-3333-4444-555555555555",
                    base_env={"HOME": self.home, "PATH": "/usr/bin:/bin"}, tmp_root=self.tmp_root, codex_mcp=())
        args.update(kw)
        return agentcmd.Step(**args)

    def project(self):
        return self.store.load().projects["app"]

    def check(self, path, step=None):
        """Zatwierdza kontrakt w magazynie `path` i pyta `approval_for_step` dla kroku."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        approval.record(self.root, "app", CONTRACT, path)
        return approval.approval_for_step(self.project(), step or self.step(), self.root, path)


class ApprovalForStepTest(StepCase):
    def assertCollision(self, result):
        approved, reason = result
        self.assertFalse(approved)
        self.assertTrue(reason.startswith(approval.STORE_IN_WRITE_AREA), reason)

    def test_store_outside_every_write_area_and_matching_fingerprint_is_approved(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                step = self.step(tool, read_profile=True)   # Codex: /tmp i $TMPDIR wykluczone
                self.assertEqual(self.check(self.state, step), (True, ""))

    def test_a_store_in_any_write_area_of_the_step_is_a_collision(self):
        tmpdir = os.path.join(self.tmp_root, "tide-step-K-1-PT-1-1")
        for name, path in (("worktree", os.path.join(self.worktree, ".state", approval.FILE_NAME)),
                           ("out", os.path.join(self.out, "state", approval.FILE_NAME)),
                           ("CLAUDE_CODE_TMPDIR", os.path.join(tmpdir, "tide", approval.FILE_NAME)),
                           ("repozytorium Tablicy", os.path.join(self.root, ".state", approval.FILE_NAME)),
                           ("wspólny .git", os.path.join(self.code, ".git", "tide", approval.FILE_NAME))):
            with self.subTest(area=name):
                self.assertCollision(self.check(path))

    def test_add_dir_without_deny_write_is_a_write_area_and_with_deny_write_is_not(self):
        extra = os.path.join(self.ws, "extra")
        areas = agentcmd.WriteAreas("claude", self.worktree, session_dirs=(extra,))
        self.assertIn(extra, areas.writable())
        self.assertNotIn(extra, agentcmd.WriteAreas("claude", self.worktree, read_dirs=(extra,),
                                                    deny_write=(extra,)).writable())
        # Krok: `out` jest jedynym --add-dir bez denyWrite; katalogi tylko do odczytu (--add-dir z denyWrite) nie.
        self.assertCollision(self.check(os.path.join(self.out, "x", approval.FILE_NAME)))
        readonly = os.path.join(self.ws, "readonly")
        os.makedirs(readonly)
        step = self.step(read_dirs=(readonly,))
        self.assertEqual(self.check(os.path.join(readonly, "tide", approval.FILE_NAME), step), (True, ""))
        self.assertIn("leży w obszarze zapisu " + extra,
                      approval.store_collision(os.path.join(extra, approval.FILE_NAME), areas.writable()))

    @unittest.skipUnless(tempfile.gettempdir() == "/tmp" or tempfile.gettempdir().startswith("/tmp/"),
                         "katalog tymczasowy testu poza /tmp")
    def test_codex_tmp_collision_follows_the_actual_settings_of_the_step(self):
        # Magazyn pod /tmp: krok Codexa bez wykluczenia /tmp — kolizja; z wykluczeniem (`read_profile`) — zgoda.
        self.assertTrue(self.state.startswith("/tmp/"))
        plain = self.step("codex")
        self.assertIn("/tmp", agentcmd.step_write_areas(plain).writable())
        self.assertCollision(self.check(self.state, plain))
        excluded = self.step("codex", read_profile=True)
        areas = agentcmd.step_write_areas(excluded)
        self.assertTrue(areas.exclude_slash_tmp and areas.exclude_tmpdir_env_var)
        self.assertNotIn("/tmp", areas.writable())
        self.assertEqual(self.check(self.state, excluded), (True, ""))
        # $TMPDIR programu Codexa (gdy ustawione) — obszar zapisu bez wykluczenia.
        self.assertIn("/var/tmp/x", agentcmd.write_areas(plain, env={"TMPDIR": "/var/tmp/x"}, git_dir="/g",
                                                          skills=[]).writable())

    def test_codex_tmp_collision_without_a_real_store_under_tmp(self):
        # To samo bez zależności od katalogu tymczasowego testu: kolizję liczy się przed odczytem magazynu.
        path = "/tmp/tide-pt286-nonexistent/tide/" + approval.FILE_NAME
        self.assertCollision(approval.approval_for_step(self.project(), self.step("codex"), self.root, path))
        approved, reason = approval.approval_for_step(self.project(), self.step("codex", read_profile=True),
                                                      self.root, path)
        self.assertFalse(approved)
        self.assertFalse(reason.startswith(approval.STORE_IN_WRITE_AREA), reason)   # brak odcisku, nie kolizja

    def test_symlinked_state_directory_into_a_write_area_is_a_collision(self):
        target = os.path.join(self.worktree, "hidden-state")
        os.makedirs(os.path.join(target, "tide"))
        state_base = os.path.join(self.tmp, "linked")
        os.symlink(target, state_base)          # $XDG_STATE_HOME → katalog w worktree kroku
        path = os.path.join(state_base, "tide", approval.FILE_NAME)
        approval.record(self.root, "app", CONTRACT, path)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT, path))   # odczyt sam nie widzi problemu
        self.assertCollision(approval.approval_for_step(self.project(), self.step(), self.root, path))
        # Dowiązany sam katalog `tide`.
        os.makedirs(os.path.join(self.out, "tide-link-target"))
        os.makedirs(os.path.join(self.tmp, "base2"))
        os.symlink(os.path.join(self.out, "tide-link-target"), os.path.join(self.tmp, "base2", "tide"))
        self.assertCollision(approval.approval_for_step(
            self.project(), self.step(), self.root, os.path.join(self.tmp, "base2", "tide", approval.FILE_NAME)))

    def test_link_then_dotdot_in_the_store_path_follows_the_link_like_the_file_system(self):
        # `safe/link/..` to w systemie plików katalog nadrzędny celu dowiązania (worktree), nie `safe`;
        # leksykalne `abspath` gubiło dowiązanie i badało `safe/tide/...` (review PT-286, BLOCKER 1).
        os.makedirs(os.path.join(self.worktree, "child"))
        safe = os.path.join(self.tmp, "safe")
        os.makedirs(os.path.join(safe, "tide"))                 # leksykalny cel istnieje i leży poza obszarami
        os.symlink(os.path.join(self.worktree, "child"), os.path.join(safe, "link"))
        dotted = os.path.join(safe, "link", "..")
        path = os.path.join(dotted, "tide", approval.FILE_NAME)
        os.makedirs(os.path.join(self.worktree, "tide"))
        approval.record(self.root, "app", CONTRACT, path)
        self.assertTrue(os.path.isfile(os.path.join(self.worktree, "tide", approval.FILE_NAME)))
        self.assertFalse(os.path.exists(os.path.join(safe, "tide", approval.FILE_NAME)))
        self.assertTrue(approval.approved(self.root, "app", CONTRACT, path))   # odczyt trafia do worktree
        self.assertCollision(approval.approval_for_step(self.project(), self.step(), self.root, path))
        # Domyślna ścieżka magazynu z `XDG_STATE_HOME=safe/link/..`.
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": dotted}):
            self.assertEqual(approval.state_path(), path)
            self.assertCollision(approval.approval_for_step(self.project(), self.step(), self.root))
        # Względna ścieżka z `..` za dowiązaniem — ta sama semantyka co w systemie plików.
        cwd = os.getcwd()
        os.chdir(safe)
        self.addCleanup(os.chdir, cwd)
        relative = os.path.join("link", "..", "tide", approval.FILE_NAME)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT, relative))
        self.assertCollision(approval.approval_for_step(self.project(), self.step(), self.root, relative))

    def test_a_write_area_inside_the_store_directory_is_a_collision(self):
        # Katalog odcisków nad worktree kroku: krok pisze w katalogu magazynu.
        path = os.path.join(self.ws, approval.FILE_NAME)
        self.assertCollision(approval.approval_for_step(self.project(), self.step(), self.root, path))
        self.assertIn("leży w katalogu odcisków", approval.store_collision(path, [self.worktree]))

    def test_every_doubt_is_not_approved(self):
        cases = {
            "zły krok": self.step(step_id="zły/krok"),
            "nieznany worktree": self.step(worktree=os.path.join(self.ws, "brak")),
        }
        for name, step in cases.items():
            with self.subTest(name):
                approved, reason = self.check(self.state, step)
                self.assertFalse(approved)
                self.assertIn("nie da się sprawdzić", reason)
        self.assertTrue(approval.store_collision(self.state, [""]))
        self.assertTrue(approval.store_collision(self.state, ["względna/ścieżka"]))
        self.assertTrue(approval.store_collision(self.state, ["/"]))
        with mock.patch.object(agentcmd, "step_write_areas", side_effect=RuntimeError("x")):
            self.assertFalse(self.check(self.state)[0])

    def test_unapproved_broken_or_empty_contract_is_not_approved_without_a_collision(self):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        approved, reason = approval.approval_for_step(self.project(), self.step(), self.root, self.state)
        self.assertEqual((approved, reason.startswith("kontrakt testów nie jest zatwierdzony")), (False, True))
        approval.record(self.root, "app", CONTRACT, self.state)
        project = self.project()
        project.tests_error = "zły blok"
        self.assertFalse(approval.approval_for_step(project, self.step(), self.root, self.state)[0])
        project.tests_error, project.tests = "", {"changed": "", "full": ""}
        self.assertFalse(approval.approval_for_step(project, self.step(), self.root, self.state)[0])

    def test_board_store_reports_the_collision_as_a_board_error_until_it_is_gone(self):
        store = BoardStore(self.root, approvals_path=os.path.join(self.worktree, ".s", approval.FILE_NAME))
        approved, reason = store.approval_for_step(self.project(), self.step())
        self.assertFalse(approved)
        errors = [e for e in store.snapshot()["errors"] if approval.STORE_IN_WRITE_AREA in e["message"]]
        self.assertEqual(len(errors), 1, store.snapshot()["errors"])
        self.assertTrue(errors[0]["message"].startswith("app: " + approval.STORE_IN_WRITE_AREA))
        store.approvals_path = self.state
        approval.record(self.root, "app", CONTRACT, self.state)
        self.assertEqual(store.approval_for_step(self.project(), self.step()), (True, ""))
        self.assertFalse([e for e in store.snapshot()["errors"] if approval.STORE_IN_WRITE_AREA in e["message"]])

    def test_step_arguments_and_the_approval_check_use_the_same_write_area_function(self):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        approval.record(self.root, "app", CONTRACT, self.state)
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool), mock.patch.object(agentcmd, "write_areas",
                                                            wraps=agentcmd.write_areas) as spy:
                step = self.step(tool)
                bg = agentcmd.background(step)
                approval.approval_for_step(self.project(), step, self.root, self.state)
                self.assertEqual(spy.call_count, 2)
                built, checked = (call.kwargs for call in spy.call_args_list)
                self.assertEqual(built, checked)   # te same wejścia: środowisko, katalog gita, skille
                areas = agentcmd.step_write_areas(step)
                if tool == "claude":
                    settings = json.loads(bg.files[os.path.join(self.step_dir, "in", "settings.json")])
                    self.assertEqual(settings["sandbox"]["filesystem"]["allowWrite"], list(areas.allow_write))
                    self.assertEqual(bg.env["CLAUDE_CODE_TMPDIR"], areas.tmp_dirs[0])
                    added = [bg.argv[i + 1] for i, a in enumerate(bg.argv) if a == "--add-dir"]
                    self.assertEqual(tuple(added), areas.add_dirs)
                else:
                    roots = next(a for a in bg.argv if a.startswith("sandbox_workspace_write.writable_roots="))
                    self.assertEqual(json.loads(roots.split("=", 1)[1]), list(areas.allow_write))
                    self.assertEqual(bg.argv[bg.argv.index("--cd") + 1], areas.cwd)


class CallOriginTest(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.mkdtemp(prefix="tide-proc-")
        self.addCleanup(shutil.rmtree, self.proc, True)

    def cgroup(self, pid, text):
        os.makedirs(os.path.join(self.proc, str(pid)), exist_ok=True)
        with open(os.path.join(self.proc, str(pid), "cgroup"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_step_unit_processes_are_refused_and_others_pass(self):
        self.cgroup(10, STEP_CGROUP)
        self.cgroup(11, "0::/user.slice/user-1000.slice/user@1000.service/app.slice/tide-step-X.scope/sub/inner\n")
        self.cgroup(12, "12:pids:/user.slice/tide-step-K-2-PT-2-1.scope\n0::/user.slice\n")   # hybryda v1
        self.cgroup(13, APP_CGROUP)
        self.cgroup(14, "0::/user.slice/user-1000.slice/user@1000.service/app.slice/run-u12.service\n")
        self.cgroup(15, "0::/user.slice/not-tide-step-x.scope\n")
        for pid, unit in ((10, "tide-step-K-1-PT-1-1.scope"), (11, "tide-step-X.scope"),
                          (12, "tide-step-K-2-PT-2-1.scope"), (13, ""), (14, ""), (15, "")):
            with self.subTest(pid=pid):
                self.assertEqual(call_origin.step_unit(pid, self.proc), unit)
                self.assertEqual(bool(call_origin.refusal(pid, self.proc)), bool(unit))
        self.assertIn("kroku agenta", call_origin.refusal(10, self.proc))

    def test_unknown_sender_is_refused(self):
        self.cgroup(20, "")
        self.cgroup(21, "zły format\n")
        self.cgroup(22, "0::relative\n")
        os.makedirs(os.path.join(self.proc, "23"))          # proces bez pliku cgroup
        os.makedirs(os.path.join(self.proc, "24"))
        with open(os.path.join(self.proc, "24", "cgroup"), "wb") as fh:
            fh.write(b"\xff\xfe")
        for pid in (None, 0, -1, "10", 1.5, True, 20, 21, 22, 23, 24, 99999):
            with self.subTest(pid=pid):
                self.assertTrue(call_origin.refusal(pid, self.proc))
        self.assertIn("nie da się ustalić procesu nadawcy", call_origin.refusal(None, self.proc))

    def test_the_real_proc_of_this_process_is_readable(self):
        unit = call_origin.step_unit(os.getpid())
        self.assertIsNotNone(unit)


class UpsertProjectOriginTest(StepCase):
    def setUp(self):
        super().setUp()
        from tide_daemon.board_monitor import BoardMonitor
        from .privatebus import app
        app()
        self.proc = os.path.join(self.tmp, "proc")
        for pid, text in ((100, STEP_CGROUP), (101, APP_CGROUP)):
            os.makedirs(os.path.join(self.proc, str(pid)))
            with open(os.path.join(self.proc, str(pid), "cgroup"), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.store.approvals_path = self.state
        self.monitor = BoardMonitor(self.store, debounce_ms=60_000, poll_ms=600_000, proc_root=self.proc)
        self.addCleanup(self.monitor.stop)

    def box(self):
        return read_bytes(os.path.join(self.root, "projects", "app", "box.yaml"))

    def upsert(self, data, pid):
        return json.loads(self.monitor.upsert_project(json.dumps(data), sender_pid=pid))

    def test_tests_from_a_step_unit_or_an_unknown_sender_are_refused_without_any_change(self):
        new = {"changed": "make test-changed BASE={base} JOBS=9", "full": "make test"}
        box, head = self.box(), git_out(self.root, "rev-parse", "HEAD")
        for pid in (100, None, 0, 555):   # krok, brak PID, PID 0, proces bez /proc (zakończony)
            with self.subTest(pid=pid):
                reply = self.upsert({"slug": "app", "tests": new}, pid)
                self.assertEqual((reply["ok"], reply["error"]), (False, "forbidden"), reply)
                self.assertTrue(reply["message"])
                self.assertEqual(self.box(), box)
                self.assertFalse(os.path.exists(self.state))
                self.assertEqual(git_out(self.root, "rev-parse", "HEAD"), head)
                self.assertFalse(self.project().tests_approved)
        self.assertIn("kroku agenta", self.upsert({"slug": "app", "tests": new}, 100)["message"])
        # Ten sam kontrakt, co w pliku (ponowne zatwierdzenie bez zmiany plików), też nie tworzy odcisku z kroku.
        self.assertEqual(self.upsert({"slug": "app", "tests": CONTRACT}, 100)["error"], "forbidden")
        self.assertFalse(os.path.exists(self.state))

    def test_the_user_window_approves_and_a_step_writing_the_file_does_not(self):
        reply = self.upsert({"slug": "app", "tests": CONTRACT}, 101)
        self.assertEqual((reply["ok"], reply["testsApproved"], reply["approvalError"]), (True, True, ""))
        self.assertTrue(self.project().tests_approved)
        # Krok zapisuje kontrakt wprost w pliku (bez UpsertProject): odcisk się nie zmienia, zatwierdzenia brak.
        stored = read_bytes(self.state)
        with open(os.path.join(self.root, "projects", "app", "box.yaml"), "w", encoding="utf-8") as fh:
            fh.write("tide-box: 1\ntests:\n  changed: \"make evil {base}\"\n  full: \"make test\"\n")
        self.assertFalse(self.project().tests_approved)
        self.assertEqual(read_bytes(self.state), stored)

    def test_without_tests_upsert_project_is_unchanged_and_needs_no_sender(self):
        for pid in (None, 100, 101):
            with self.subTest(pid=pid):
                reply = self.upsert({"slug": "app", "name": f"App {pid}"}, pid)
                self.assertEqual((reply["ok"], reply["changed"]), (True, True), reply)
                self.assertNotIn("testsApproved", reply)
                self.assertEqual(self.store.load().projects["app"].name, f"App {pid}")


class SenderPidTest(unittest.TestCase):
    def test_sender_pid_comes_from_the_bus_and_any_doubt_is_none(self):
        from tide_daemon.dbus_service import _sender_pid

        def bus(valid=True, value=4321, error=None):
            reply = mock.Mock()
            reply.isValid.return_value = valid
            reply.value.return_value = value
            iface = mock.Mock()
            iface.servicePid.side_effect = error
            iface.servicePid.return_value = reply
            fake = mock.Mock()
            fake.interface.return_value = iface
            return fake

        message = mock.Mock()
        message.service.return_value = ":1.42"
        self.assertEqual(_sender_pid(bus(), message), 4321)
        bus_ok = bus()
        _sender_pid(bus_ok, message)
        bus_ok.interface().servicePid.assert_called_with(":1.42")
        self.assertIsNone(_sender_pid(bus(valid=False), message))
        self.assertIsNone(_sender_pid(bus(value=0), message))
        self.assertIsNone(_sender_pid(bus(error=RuntimeError("x")), message))
        message.service.return_value = ""
        self.assertIsNone(_sender_pid(bus(), message))


if __name__ == "__main__":
    unittest.main()
