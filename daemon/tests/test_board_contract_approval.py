"""Zatwierdzenie kontraktu testów (K-69, PT-285): magazyn odcisków i `testsApproved` w JSON projektu."""
import json
import os
import stat
import unittest
from unittest import mock

from tide_daemon.board import contract_approval as approval
from tide_daemon.board.store import TESTS_UNAPPROVED

from .test_board_core import BoardCase, git_out

CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
OTHER = {"changed": "make test-changed BASE={base} JOBS=2", "full": "make test"}


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class ApprovalCase(BoardCase):
    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.tmp, "state")
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.state})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = os.path.join(self.state, "tide", "test-contracts.json")

    def box(self, slug="app"):
        return os.path.join(self.root, "projects", slug, "box.yaml")

    def write_box(self, text, slug="app"):
        with open(self.box(slug), "w", encoding="utf-8") as fh:
            fh.write(text)

    def snapshot(self):
        return self.store.snapshot()

    def project(self, slug="app", snapshot=None):
        return {p["slug"]: p for p in (snapshot or self.snapshot())["projects"]}[slug]

    def unapproved_errors(self, snapshot):
        return [e for e in snapshot["errors"] if TESTS_UNAPPROVED in e["message"]]

    def head(self):
        return git_out(self.root, "rev-parse", "HEAD").strip()

    def approve(self, tests, slug="app"):
        return self.store.approve_project_tests({"slug": slug, "tests": tests})

    def stored(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class FingerprintTest(unittest.TestCase):
    def test_equivalent_contracts_have_the_same_fingerprint(self):
        base = approval.fingerprint({"changed": "make c {base}", "full": ""})
        for variant in ({"changed": "  make c {base}\t", "full": ""}, {"full": "", "changed": "make c {base}"},
                        {"changed": "make c {base}"}, {"changed": "make c {base}", "full": "   "},
                        {"changed": "make c {base}", "full": None}):
            with self.subTest(variant=variant):
                self.assertEqual(approval.fingerprint(variant), base)
        self.assertEqual(approval.canonical({"full": " x ", "changed": "y"}), '{"changed":"y","full":"x"}')
        self.assertEqual(approval.fingerprint(CONTRACT),
                         __import__("hashlib").sha256(json.dumps(CONTRACT, separators=(",", ":")).encode()).hexdigest())

    def test_inner_whitespace_is_significant(self):
        quoted = {"changed": 'pytest -k "a b" {base}', "full": ""}
        for changed in ('pytest -k "a  b" {base}', 'pytest  -k "a b" {base}', 'pytest -k "a b " {base}'):
            with self.subTest(changed=changed):
                self.assertNotEqual(approval.fingerprint({**quoted, "changed": changed}), approval.fingerprint(quoted))


class StoreRecordTest(ApprovalCase):
    """Magazyn bez Tablicy: jedno wejście odczytu (approved) i jedno zapisu (record)."""

    def test_record_and_read(self):
        self.assertFalse(approval.approved(self.root, "app", CONTRACT))
        approval.record(self.root, "app", CONTRACT)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))
        self.assertTrue(approval.approved(self.root, "app", {"full": " make test", "changed": "make test-changed BASE={base} "}))
        self.assertFalse(approval.approved(self.root, "app", OTHER))
        self.assertFalse(approval.approved(self.root, "other", CONTRACT))
        self.assertFalse(approval.approved(os.path.join(self.tmp, "inna"), "app", CONTRACT))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        data = self.stored()
        self.assertEqual(data, {"version": 1, "boards": {os.path.realpath(self.root): {"app": approval.fingerprint(CONTRACT)}}})

    def test_empty_contract_is_never_approved_and_clears(self):
        approval.record(self.root, "app", CONTRACT)
        approval.record(self.root, "app", {"changed": "", "full": ""})
        self.assertEqual(self.stored()["boards"], {})
        self.assertFalse(approval.approved(self.root, "app", {"changed": "", "full": ""}))
        os.unlink(self.path)
        approval.record(self.root, "app", {})   # czyszczenie bez magazynu niczego nie zakłada
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_store_is_replaced_by_record(self):
        approval.record(self.root, "app", CONTRACT)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{zepsute")
        self.assertFalse(approval.approved(self.root, "app", CONTRACT))
        approval.record(self.root, "app", CONTRACT)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))

    def test_tide_dir_with_wrong_mode_is_fixed_by_record(self):
        os.makedirs(os.path.dirname(self.path), mode=0o755)
        os.chmod(os.path.dirname(self.path), 0o755)
        approval.record(self.root, "app", CONTRACT)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))


class UncertainReadTest(ApprovalCase):
    """Każda niepewność odczytu daje False, a lista projektów działa dalej."""

    def setUp(self):
        super().setUp()
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.assertTrue(self.approve(CONTRACT)["testsApproved"])
        self.good = read_bytes(self.path)

    def assert_unapproved(self):
        snapshot = self.snapshot()   # bez wyjątku
        self.assertFalse(self.project(snapshot=snapshot)["testsApproved"])
        self.assertEqual(self.project(snapshot=snapshot)["tests"], CONTRACT)
        self.assertEqual(len(self.unapproved_errors(snapshot)), 1)
        self.assertFalse(approval.approved(self.root, "app", CONTRACT))

    def put(self, data: bytes, mode=0o600):
        if os.path.lexists(self.path):
            os.unlink(self.path)
        with open(self.path, "wb") as fh:
            fh.write(data)
        os.chmod(self.path, mode)

    def test_baseline_is_approved(self):
        snapshot = self.snapshot()
        self.assertTrue(self.project(snapshot=snapshot)["testsApproved"])
        self.assertEqual(self.unapproved_errors(snapshot), [])

    def test_missing_file(self):
        os.unlink(self.path)
        self.assert_unapproved()

    def test_missing_dir(self):
        os.unlink(self.path)
        os.rmdir(os.path.dirname(self.path))
        self.assert_unapproved()

    def test_corrupt_contents(self):
        fp = approval.fingerprint(CONTRACT)
        key = os.path.realpath(self.root)
        for data in (b"", b"{", b"\xff\xfe", b"[]", b"null",
                     json.dumps({"version": 2, "boards": {key: {"app": fp}}}).encode(),
                     json.dumps({"version": True, "boards": {key: {"app": fp}}}).encode(),
                     json.dumps({"version": 1, "boards": {key: {"app": fp.upper()}}}).encode(),
                     json.dumps({"version": 1, "boards": {key: {"app": fp[:-1]}}}).encode(),
                     json.dumps({"version": 1, "boards": {key: {"app": fp}}, "extra": 1}).encode(),
                     json.dumps({"version": 1, "boards": {key: ["app"]}}).encode(),
                     b"[" * 100000 + b"]" * 100000):
            with self.subTest(data=data[:60]):
                self.put(data)
                self.assert_unapproved()
        self.put(self.good)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))

    def test_too_big(self):
        self.put(self.good[:-2] + b" " * approval.MAX_BYTES + b"}\n")
        self.assert_unapproved()

    def test_wrong_file_mode(self):
        for mode in (0o644, 0o640, 0o400, 0o700, 0o666):
            with self.subTest(mode=oct(mode)):
                self.put(self.good, mode)
                self.assert_unapproved()

    def test_wrong_dir_mode(self):
        for mode in (0o755, 0o750, 0o711):
            with self.subTest(mode=oct(mode)):
                os.chmod(os.path.dirname(self.path), mode)
                self.assert_unapproved()
        os.chmod(os.path.dirname(self.path), 0o700)
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))

    def test_file_symlink(self):
        real = os.path.join(os.path.dirname(self.path), "real.json")
        os.rename(self.path, real)
        os.symlink(real, self.path)
        self.assert_unapproved()

    def test_dir_symlink(self):
        folder = os.path.dirname(self.path)
        moved = folder + "-real"
        os.rename(folder, moved)
        os.symlink(moved, folder)
        self.assert_unapproved()

    def test_not_regular_file(self):
        os.unlink(self.path)
        os.mkfifo(self.path, 0o600)   # O_NONBLOCK: odczyt nie wisi na potoku
        self.assert_unapproved()

    def test_wrong_owner(self):
        uid = os.geteuid()
        with mock.patch.object(approval.os, "geteuid", return_value=uid + 1):
            self.assert_unapproved()

    def test_interrupted_write_keeps_old_file(self):
        folder = os.path.dirname(self.path)
        with mock.patch.object(approval.os, "rename", side_effect=OSError(5, "atrapa przerwania")):
            result = self.approve(OTHER)
        self.assertFalse(result["testsApproved"])
        self.assertIn("atrapa przerwania", result["approvalError"])
        self.assertEqual(read_bytes(self.path), self.good)
        self.assertEqual(sorted(os.listdir(folder)), ["test-contracts.json"])   # plik tymczasowy sprzątnięty
        # kontrakt w pliku tablicy już jest nowy, odcisk stary — niezatwierdzony
        self.assertEqual(self.project()["tests"], OTHER)
        self.assertFalse(self.project()["testsApproved"])
        self.assertTrue(approval.approved(self.root, "app", CONTRACT))

    def test_interrupted_write_mid_file(self):
        with mock.patch.object(approval.os, "fsync", side_effect=OSError(28, "brak miejsca")):
            with self.assertRaises(approval.ApprovalError):
                approval.record(self.root, "app", OTHER)
        self.assertEqual(read_bytes(self.path), self.good)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["test-contracts.json"])


class ApproveProjectTestsTest(ApprovalCase):
    def test_approve_sets_flag_and_clear_removes_fingerprint(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        result = self.approve(CONTRACT)
        self.assertEqual((result["ok"], result["changed"], result["testsApproved"], result["approvalError"]),
                         (True, True, True, ""))
        self.assertNotIn("_testsContract", result)
        self.assertTrue(self.project()["testsApproved"])
        self.assertEqual(self.unapproved_errors(self.snapshot()), [])
        self.assertIn("app", self.stored()["boards"][os.path.realpath(self.root)])
        result = self.approve({"changed": "", "full": ""})
        self.assertEqual((result["changed"], result["testsApproved"]), (True, False))
        self.assertEqual(self.stored()["boards"], {})
        project = self.project()
        self.assertEqual((project["tests"], project["testsApproved"]), ({"changed": "", "full": ""}, False))
        self.assertEqual(self.unapproved_errors(self.snapshot()), [])   # pusty kontrakt — bez wpisu w błędach

    def test_partial_approval_fingerprints_the_whole_contract(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve(CONTRACT)
        result = self.approve({"full": "make test JOBS=1"})
        self.assertTrue(result["testsApproved"])
        self.assertTrue(approval.approved(self.root, "app", {**CONTRACT, "full": "make test JOBS=1"}))

    def test_direct_file_edit_is_unapproved_and_store_unchanged(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve(CONTRACT)
        before = read_bytes(self.path)
        # krok agenta pisze w box.yaml bez UpsertProject (także odstęp wewnątrz cytowanego argumentu)
        for changed in ('make test-changed BASE={base} ARGS="-k  x"', "make test-changed  BASE={base}"):
            with self.subTest(changed=changed):
                self.write_box("tide-box: 1\ntests:\n  changed: " + json.dumps(changed) + '\n  full: "make test"\n')
                snapshot = self.snapshot()
                self.assertFalse(self.project(snapshot=snapshot)["testsApproved"])
                errors = self.unapproved_errors(snapshot)
                self.assertEqual([e["message"] for e in errors], [f"projects/app/box.yaml: {TESTS_UNAPPROVED}"])
                self.assertEqual(read_bytes(self.path), before)
        # przywrócenie treści przywraca zgodność z odciskiem
        self.write_box('tide-box: 1\ntests:\n  changed: "make test-changed BASE={base}"\n  full: "make test"\n')
        self.assertTrue(self.project()["testsApproved"])

    def test_equivalent_manual_edit_stays_approved(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve({"changed": "  make test-changed BASE={base} ", "full": "make test"})
        # inna kolejność kluczy, apostrofy, komentarz — ta sama treść poleceń
        self.write_box("tide-box: 1\ntests:\n  full: 'make test'  # pełne\n  changed: make test-changed BASE={base}\n")
        self.assertTrue(self.project()["testsApproved"])

    def test_reapproval_of_identical_values_without_file_change(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve(CONTRACT)
        md = os.path.join(self.root, "projects", "app.md")
        md_before, box_before, head = read_bytes(md), read_bytes(self.box()), self.head()
        os.unlink(self.path)   # utrata odcisku
        self.assertFalse(self.project()["testsApproved"])
        result = self.approve(dict(CONTRACT))
        self.assertEqual((result["ok"], result["changed"], result["testsApproved"]), (True, False, True))
        self.assertTrue(self.project()["testsApproved"])
        self.assertEqual((read_bytes(md), read_bytes(self.box()), self.head()), (md_before, box_before, head))
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_reapproval_after_edit_and_restore(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve(CONTRACT)
        canonical = read_bytes(self.box())
        self.write_box("tide-box: 1\ntests:\n  changed: \"make evil {base}\"\n")
        self.store.sync()
        self.assertFalse(self.project()["testsApproved"])
        self.write_box(canonical.decode())   # ręczne przywrócenie treści, potem zatwierdzenie tych samych wartości
        self.store.sync()
        head = self.head()
        result = self.approve(CONTRACT)
        self.assertEqual((result["changed"], result["testsApproved"]), (False, True))
        self.assertEqual(self.head(), head)
        # zatwierdzenie wartości różnych od pliku przywraca plik i zatwierdza
        self.write_box("tide-box: 1\ntests:\n  changed: \"make evil {base}\"\n")
        result = self.approve(CONTRACT)
        self.assertEqual((result["changed"], result["testsApproved"]), (True, True))
        self.assertEqual(read_bytes(self.box()), canonical)

    def test_direct_store_upsert_never_creates_or_replaces_fingerprint(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        result = self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assertNotIn("testsApproved", result)
        self.assertNotIn("_testsContract", result)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(self.project()["testsApproved"])
        self.approve(CONTRACT)
        before = read_bytes(self.path)
        self.store.upsert_project({"slug": "app", "tests": OTHER})
        self.assertEqual(read_bytes(self.path), before)
        self.assertFalse(self.project()["testsApproved"])
        self.store.upsert_project({"slug": "app", "tests": {"changed": "", "full": ""}})   # czyszczenie też nie
        self.assertEqual(read_bytes(self.path), before)

    def test_reads_do_not_create_fingerprint(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.store.create_card({"title": "Karta", "project": "app"})
        self.store.load()
        self.snapshot()
        self.store.render_flow("K-1", "")
        self.store.render_launch("K-1", {})
        self.store.upsert_project({"slug": "app", "name": "App 2"})   # zapis bez `tests`
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))

    def test_rejected_contract_is_not_approved(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        from tide_daemon.board.model import BoardError
        with self.assertRaises(BoardError):
            self.approve({"changed": "make test"})   # bez {base}
        self.assertFalse(os.path.exists(self.path))

    def test_broken_block_is_not_approved_and_has_only_its_error(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.approve(CONTRACT)
        self.write_box("tide-box: 1\ntests: 5\n")
        snapshot = self.snapshot()
        self.assertFalse(self.project(snapshot=snapshot)["testsApproved"])
        self.assertTrue(self.project(snapshot=snapshot)["testsError"])
        self.assertEqual(self.unapproved_errors(snapshot), [])


if __name__ == "__main__":
    unittest.main()
