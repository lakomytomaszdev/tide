"""PT-295 (K-38 F7): audyt wszystkich historii kart — inwentaryzacja Tablicy i mirrora, odczyt kodem dokładnych SHA.

Historie syntetyczne powstają publicznym zapisem dziennika (`Journal.append`) w katalogu tymczasowym; żaden test
nie dotyka prawdziwej Tablicy ani mirrora usługi. Kandydat to HEAD repozytorium, odniesienie — przypięty master
(ten sam co próba wycofania F5, `rollback_trial.OLD_SHA`). Kod obu SHA pochodzi z `git archive`.

Czerwone przypadki: usunięta karta, pole i zdarzenie, zmieniony runner i wartość zgody, ucięta linia, duplikat
i luka seq, nieznany format, katalog karty bez dziennika, błąd listowania, zmiana źródła w trakcie odczytu,
próba zapisu przez kod kandydata, różnica i niesklasyfikowane pole stanu. Zielone: pełna zgodna kopia (karty
aktywne, wstrzymane, zamknięte, z Konsolą, outside i zgodą) oraz legalne nowe pola kandydata z jawną regułą.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

from tide_daemon.cardflow import historyaudit as ha
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold

REPO_ROOT = ha.REPO_ROOT
# Przypięty master próby wycofania F5 (rollback_trial.OLD_SHA); bez importu jej harnessu pudełka.
OLD_SHA = os.environ.get("TIDE_HISTORY_AUDIT_REFERENCE") or "a6efe28cdfe2e727bbc1dec085ac3aebfb76abe6"
TICKET_STEP = "K-1-PT-901-1"


def snapshot(*roots):
    """Treść i metadane wszystkich plików i katalogów źródeł (dowód braku zapisu)."""
    out = {}
    for root in roots:
        for dirpath, dirs, names in os.walk(root):
            dirs.sort()
            st = os.lstat(dirpath)
            out[dirpath] = (st.st_mode, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, tuple(sorted(dirs + names)))
            for name in sorted(names):
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
                out[path] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, digest)
    return out


class Recorder:
    """`run` audytu: zapisuje wywołania i pozwala podmienić pliki w archiwum jednego SHA (atrapa wersji kodu)."""

    def __init__(self, patches=None):
        self.calls, self.patches = [], patches or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        done = subprocess.run(argv, **kwargs)
        if len(argv) > 5 and argv[3] == "archive" and argv[5] in self.patches and done.returncode == 0:
            return subprocess.CompletedProcess(argv, 0, _patch_tar(done.stdout, self.patches[argv[5]]), b"")
        return done


def _patch_tar(data, changes):
    """Archiwum z zamienionymi fragmentami plików: {ścieżka: [(stary, nowy), …]}; brak fragmentu to błąd testu."""
    src, out = tarfile.open(fileobj=io.BytesIO(data)), io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as dst:
        for member in src.getmembers():
            content = src.extractfile(member).read() if member.isfile() else None
            if member.name in changes:
                text = content.decode()
                for old, new in changes[member.name]:
                    assert old in text, (member.name, old)
                    text = text.replace(old, new, 1)
                content = text.encode()
                member.size = len(content)
            dst.addfile(member, io.BytesIO(content) if content is not None else None)
    return out.getvalue()


class HistoryAuditTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = temp.name
        self.board = os.path.join(self.base, "board")
        self.mirror = os.path.join(self.base, "state", "tide", "cardflow", "abc")
        os.makedirs(self.board)
        self.journal = Journal(self.board, mirror=self.mirror)
        self.candidate = ha.resolve(REPO_ROOT, "HEAD", subprocess.run)
        self.history()
        self.runs = 0

    # --- historia syntetyczna (publiczny zapis dziennika) ----------------------------------------------------

    def history(self):
        j = self.journal
        # K-1: aktywna, plan, krok ticketu z runnerem, pytanie, odpowiedź, zgoda, outside, Konsola, review.
        j.append("K-1", "card.started", entry="now", executor="Opus", project="proj", repo="/repo/proj",
                 baseRef="refs/heads/master", baseBranch="master", baseCommit="a" * 40)
        j.append("K-1", "plan.approved", rows=[{"id": "PT-901", "title": "Eksport", "blockers": []}])
        j.append("K-1", "result", action="ticket-branch", key="PT-901", ok=True, ticket="PT-901",
                 branch="ticket/pt-901", worktree="/wt/pt-901", base="b" * 40)
        j.append("K-1", "intent", action="step-start", key=TICKET_STEP, ticket="PT-901", kind="ticket")
        j.append("K-1", "result", action="step-start", key=TICKET_STEP, ok=True)
        j.append("K-1", "step.started", step=TICKET_STEP, ticket="PT-901", kind="ticket", executor="Opus",
                 runner="host", unit="tide-step-K-1-PT-901-1", worktree="/wt/pt-901", branch="ticket/pt-901",
                 session="s-1")
        j.append("K-1", "step.entered", step=TICKET_STEP, session="s-1", consoleUnit="tide-console-1",
                 processFile="/run/console-1.pid")
        j.append("K-1", "step.console_closed", step=TICKET_STEP)
        j.append("K-1", "step.finished", step=TICKET_STEP, ticket="PT-901", result="denied", outside=True,
                 command="make test")
        j.append("K-1", "outside.ticket-consent", step=TICKET_STEP, ticket="PT-901", orderSeq=1, baseVersion=0)
        j.append("K-1", "step.decision", step=TICKET_STEP, ticket="PT-901", choice="run-ticket")
        j.append("K-1", "outside.started", step=TICKET_STEP, ticket="PT-901", unit="tide-outside-1")
        j.append("K-1", "outside.finished", step=TICKET_STEP, ticket="PT-901", exit=0)
        j.append("K-1", "review.verdict", step=TICKET_STEP, ticket="PT-901", round=1, verdict="APPROVE")
        j.append("K-1", "box.wait", value="", deferred="environment")
        # K-2: wstrzymana.
        j.append("K-2", "card.started", entry="now", executor="Sonnet", project="proj")
        j.append("K-2", "card.paused")
        # K-3: zamknięta.
        j.append("K-3", "card.started", entry="now", executor="Sonnet", project="other")
        j.append("K-3", "card.closed", step="K-3-close-1")
        j.append("K-3", "card.finished", column="done")
        self.journal_path = {c: (self.journal.mirror_path(c), self.journal.path(c)) for c in ("K-1", "K-2", "K-3")}

    def lines(self, card, source="board"):
        path = self.journal_path[card][0 if source == "mirror" else 1]
        with open(path, encoding="utf-8") as fh:
            return path, fh.read().splitlines(keepends=True)

    def rewrite(self, card, source, edit):
        path, lines = self.lines(card, source)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(edit(lines)))

    def edit_event(self, card, source, seq, change):
        def edit(lines):
            out = []
            for line in lines:
                event = json.loads(line)
                if event["seq"] == seq:
                    change(event)
                    line = json.dumps(event, ensure_ascii=False) + "\n"
                out.append(line)
            return out
        self.rewrite(card, source, edit)

    def seq_of(self, card, kind):
        return next(e["seq"] for e in self.journal.read_complete(card) if e["type"] == kind)

    # --- przebieg ----------------------------------------------------------------------------------------------

    def audit(self, run=None, **kwargs):
        self.runs += 1
        out = os.path.join(self.base, f"out-{self.runs}")
        return ha.audit(self.board, out, mirror=self.mirror, repo=REPO_ROOT, candidate=self.candidate,
                        reference=OLD_SHA, run=run or Recorder(), **kwargs)

    def codes(self, report, card=None):
        return {e["code"] for e in report["errors"] if card is None or e["card"] == card}

    def assert_red(self, report, *codes, card=None):
        self.assertFalse(report["ok"], json.dumps(report["errors"], ensure_ascii=False, indent=1))
        self.assertTrue(set(codes) <= self.codes(report, card), (codes, report["errors"]))
        # Mianownik obejmuje wszystkie karty także przy błędach.
        self.assertGreaterEqual(report["counts"]["cards"], 3)

    # --- zielone -----------------------------------------------------------------------------------------------

    def test_full_consistent_copy_passes_with_classified_new_fields(self):
        before = snapshot(self.board, self.mirror)
        run = Recorder()
        report = self.audit(run)
        self.assertTrue(report["ok"], json.dumps(report["errors"], ensure_ascii=False, indent=1))
        self.assertEqual(report["counts"]["label"], "3/3")
        self.assertEqual((report["counts"]["rejected"], report["counts"]["unexplained"]), (0, 0))
        self.assertEqual(before, snapshot(self.board, self.mirror))
        self.assertTrue(report["stable"])
        self.assertEqual((report["candidate"]["sha"], report["reference"]["sha"]), (self.candidate, OLD_SHA))
        rows = {r["card"]: r for r in report["cards"]}
        self.assertEqual({c: r["class"] for c, r in rows.items()},
                         {"K-1": "obie kopie, aktywna", "K-2": "obie kopie, wstrzymana",
                          "K-3": "obie kopie, zamknięta"})
        k1 = rows["K-1"]["sources"]["mirror"]
        self.assertEqual((k1["events"], k1["seqMin"], k1["seqMax"]), (15, 1, 15))
        self.assertEqual(k1["types"]["step.started"], 1)
        with open(self.journal.mirror_path("K-1"), "rb") as fh:
            self.assertEqual(k1["sha256"], hashlib.sha256(fh.read()).hexdigest())
        fields = report["completeness"]["stateFields"]
        # Każde pole eksportowanego stanu obu wersji jest porównane albo ma regułę nowego pola.
        self.assertEqual({k for k, v in fields.items() if v["status"] == "niesklasyfikowane"}, set())
        for name in ("box_wait", "box_env_deferred", "process_identities", "lifted"):
            self.assertEqual((fields[name]["inReference"], fields[name]["inCandidate"]), (False, True))
            self.assertEqual(fields[name]["status"], "nowe pole: reguła oczekiwania spełniona")
        for name in ("paused", "steps", "finished", "verdicts", "step_decisions", "ticket_consents",
                     "outside_started", "derived:open_consoles", "started", "results", "plan_approved"):
            self.assertEqual((fields[name]["status"], fields[name]["equal"]), ("porównane", 3), name)
        keys = {(k["type"], k["key"]) for k in report["completeness"]["eventKeys"]}
        self.assertTrue({("step.started", "runner"), ("outside.ticket-consent", "orderSeq"),
                         ("box.wait", "deferred")} <= keys)
        entry = report["completeness"]["entryKeys"]
        self.assertEqual(entry["steps._identityHeld"]["status"],
                         "nakładka tożsamości kandydata (reguła steps/intents-overlay)")
        self.assertEqual(entry["steps.runner"]["status"], "porównane")
        # Wyłącznie odczyt obiektów git i sterowniki obu SHA; żadnych innych procesów.
        for argv in run.calls:
            allowed = argv[:1] == ["git"] and argv[3] in ("rev-parse", "archive", "status") or \
                argv[:4] == [ha.PYTHON, "-I", "-B", ha.DRIVER]
            self.assertTrue(allowed, argv)
        self.assertEqual(sum(1 for a in run.calls if a[0] == ha.PYTHON), 2)
        with open(os.path.join(report["out"], "report.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["ok"])
        with open(os.path.join(report["out"], "report.md"), encoding="utf-8") as fh:
            self.assertIn("ZALICZONY", fh.read())

    def test_read_only_sources_still_pass_and_stay_unchanged(self):
        """Źródła bez prawa zapisu (atrapa zakazu): przebieg przechodzi, każda próba zapisu skończyłaby się błędem."""
        if os.geteuid() == 0:
            self.skipTest("root pomija prawa plików")
        paths = []
        for root in (self.board, self.mirror):
            for dirpath, _dirs, names in os.walk(root):
                paths.append(dirpath)
                paths += [os.path.join(dirpath, n) for n in names]
        modes = {p: stat.S_IMODE(os.lstat(p).st_mode) for p in paths}
        self.addCleanup(lambda: [os.chmod(p, m) for p, m in modes.items()])
        for path in sorted(paths, key=len, reverse=True):
            os.chmod(path, 0o555 if os.path.isdir(path) else 0o444)
        before = snapshot(self.board, self.mirror)
        report = self.audit()
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(before, snapshot(self.board, self.mirror))

    def test_existing_strict_read_hides_board_divergence(self):
        """Kontrola: dotychczasowy ścisły odczyt (mirror wygrywa) nie widzi rozjazdu Tablicy — widzi go audyt."""
        shutil.rmtree(os.path.dirname(self.journal.path("K-2")))
        self.edit_event("K-1", "board", self.seq_of("K-1", "step.started"), lambda e: e.pop("runner"))
        reader = Journal(self.board, mirror=self.mirror)
        for card in ("K-1", "K-2"):
            fold(reader.read_complete(card))   # bez wyjątku: brak karty i pola w Tablicy niewidoczny
        report = self.audit()
        self.assert_red(report, "card-missing-in-board", card="K-2")
        self.assert_red(report, "field-missing-in-board", card="K-1")

    # --- czerwone: kopie --------------------------------------------------------------------------------------

    def test_card_removed_from_one_copy(self):
        shutil.rmtree(os.path.dirname(self.journal.path("K-2")))
        os.unlink(self.journal.mirror_path("K-3"))
        report = self.audit()
        self.assert_red(report, "card-missing-in-board", card="K-2")
        self.assert_red(report, "card-missing-in-mirror", card="K-3")
        rows = {r["card"]: r["class"] for r in report["cards"]}
        self.assertEqual((rows["K-2"], rows["K-3"]), ("tylko mirror, wstrzymana", "tylko Tablica, zamknięta"))
        self.assertEqual(report["counts"]["cards"], 3)

    def test_field_removed_from_one_copy(self):
        self.edit_event("K-1", "mirror", self.seq_of("K-1", "step.started"), lambda e: e.pop("unit"))
        report = self.audit()
        self.assert_red(report, "field-missing-in-mirror", card="K-1")
        error = next(e for e in report["errors"] if e["code"] == "field-missing-in-mirror")
        self.assertEqual(error["key"], "unit")

    def test_event_removed_from_one_copy(self):
        seq = self.seq_of("K-1", "step.decision")
        self.rewrite("K-1", "board", lambda lines: [l for l in lines if json.loads(l)["seq"] != seq])
        report = self.audit()
        self.assert_red(report, "event-missing-in-board", "seq-gap", card="K-1")

    def test_runner_changed(self):
        self.edit_event("K-1", "board", self.seq_of("K-1", "step.started"), lambda e: e.update(runner="box"))
        report = self.audit()
        self.assert_red(report, "value-diff", card="K-1")
        error = next(e for e in report["errors"] if e["code"] == "value-diff")
        self.assertEqual((error["key"], error["mirror"], error["board"]), ("runner", '"host"', '"box"'))

    def test_consent_value_changed(self):
        self.edit_event("K-1", "board", self.seq_of("K-1", "outside.ticket-consent"),
                        lambda e: e.update(orderSeq=2))
        self.edit_event("K-1", "board", self.seq_of("K-1", "step.decision"), lambda e: e.update(choice="run"))
        report = self.audit()
        self.assert_red(report, "value-diff", card="K-1")
        self.assertEqual({e["key"] for e in report["errors"] if e["code"] == "value-diff"}, {"orderSeq", "choice"})

    def test_truncated_line_in_board_and_in_authoritative_mirror(self):
        self.rewrite("K-2", "board", lambda lines: lines[:-1] + [lines[-1][:-10]])
        self.rewrite("K-3", "mirror", lambda lines: lines[:-1] + [lines[-1].rstrip("\n")])
        report = self.audit()
        self.assert_red(report, "truncated-line", "event-missing-in-board", card="K-2")
        self.assert_red(report, "truncated-line", "read-rejected", card="K-3")
        self.assertEqual(report["counts"]["label"], "2/3")

    def test_duplicate_seq_and_unknown_format(self):
        self.rewrite("K-2", "board", lambda lines: lines + [lines[-1]])
        self.rewrite("K-3", "board", lambda lines: [json.dumps({**json.loads(lines[0]), "v": 2}) + "\n"] + lines[1:])
        report = self.audit()
        self.assert_red(report, "duplicate-seq", card="K-2")
        self.assert_red(report, "unknown-format", "event-missing-in-board", card="K-3")

    def test_card_dir_without_journal_and_unknown_entry(self):
        os.makedirs(os.path.join(self.board, "flow", "K-9"))
        with open(os.path.join(self.mirror, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        report = self.audit()
        self.assert_red(report, "journal-missing", "card-missing-in-mirror", "read-rejected", card="K-9")
        self.assertIn("unknown-entry", self.codes(report))
        self.assertEqual(report["counts"]["label"], "3/4")

    def test_listing_error_blocks_and_keeps_denominator(self):
        if os.geteuid() == 0:
            self.skipTest("root pomija prawa katalogu")
        os.chmod(self.mirror, 0)
        self.addCleanup(os.chmod, self.mirror, 0o700)
        report = self.audit()
        self.assert_red(report, "registry-unreadable")
        self.assertIn("registry-rejected", self.codes(report))
        self.assertFalse(report["ok"])

    def test_change_during_read_invalidates_run(self):
        report = self.audit(between=lambda: self.journal.append("K-2", "card.resumed"))
        self.assert_red(report, "unstable")

    def test_output_inside_sources_is_refused(self):
        with self.assertRaises(ha.AuditError):
            ha.audit(self.board, os.path.join(self.mirror, "out"), mirror=self.mirror, repo=REPO_ROOT,
                     candidate=self.candidate, reference=OLD_SHA, run=Recorder())
        with self.assertRaises(ha.AuditError):
            ha.audit(self.board, os.path.join(self.base, "x"), mirror=self.mirror, repo=REPO_ROOT,
                     candidate="0" * 40, reference=OLD_SHA, run=Recorder())

    # --- czerwone: kod dokładnych SHA -----------------------------------------------------------------------

    def candidate_patch(self, changes):
        return Recorder({self.candidate: changes})

    def test_candidate_fold_difference_is_unexplained(self):
        run = self.candidate_patch({"daemon/tide_daemon/cardflow/state.py": [
            ('        elif kind == "card.paused":\n            st.paused = True',
             '        elif kind == "card.paused":\n            st.paused = False')]})
        report = self.audit(run)
        self.assert_red(report, "state-diff", card="K-2")
        self.assertIn("paused", {e.get("field") for e in report["errors"]})
        self.assertEqual(report["completeness"]["stateFields"]["paused"]["status"], "różnice niewyjaśnione")

    def test_new_field_value_against_rule_fails(self):
        run = self.candidate_patch({"daemon/tide_daemon/cardflow/state.py": [
            ('st.box_env_deferred = not st.box_wait and e.get("deferred") == "environment"',
             'st.box_env_deferred = False')]})
        report = self.audit(run)
        self.assert_red(report, "state-diff", card="K-1")

    def test_unclassified_new_field_fails(self):
        run = self.candidate_patch({"daemon/tide_daemon/cardflow/state.py": [
            ("    box_wait: str = \"\"", "    audit_probe: int = 0\n    box_wait: str = \"\"")]})
        report = self.audit(run)
        self.assert_red(report, "field-unclassified")
        self.assertEqual(report["completeness"]["stateFields"]["audit_probe"]["status"], "niesklasyfikowane")

    def test_rejected_history_in_candidate_is_reported(self):
        run = self.candidate_patch({"daemon/tide_daemon/cardflow/trialhold.py": [
            ("    events = journal.read_complete(card)\n",
             "    events = journal.read_complete(card)\n"
             "    if card == 'K-2':\n        raise ValueError('odmowa próbna')\n")]})
        report = self.audit(run)
        self.assert_red(report, "read-rejected", card="K-2")
        self.assertEqual(report["counts"]["label"], "2/3")

    def test_write_attempts_by_candidate_code_are_blocked(self):
        before = snapshot(self.board, self.mirror)
        run = self.candidate_patch({"daemon/tide_daemon/cardflow/trialhold.py": [
            ("    events = journal.read_complete(card)\n",
             "    events = journal.read_complete(card)\n"
             "    if card == 'K-1':\n        journal.append(card, 'audit.probe')\n"
             "    if card == 'K-2':\n        open(journal.mirror + '/K-2.jsonl', 'a').write('x')\n"
             "    if card == 'K-3':\n        import subprocess; subprocess.run(['true'])\n")]})
        report = self.audit(run)
        for card in ("K-1", "K-2", "K-3"):
            self.assert_red(report, "read-rejected", card=card)
        forbidden = [e["text"] for e in report["errors"] if e["code"] == "forbidden-operation"]
        self.assertTrue(any("journal.append" in t for t in forbidden), forbidden)
        self.assertTrue(any("open do zapisu" in t for t in forbidden), forbidden)
        self.assertTrue(any("subprocess.Popen" in t for t in forbidden), forbidden)
        self.assertEqual(before, snapshot(self.board, self.mirror))


class CliTest(unittest.TestCase):
    def test_cli_requires_explicit_sources_and_shas(self):
        with self.assertRaises(SystemExit):
            ha.main(["--board", "/nonexistent"], out=io.StringIO())

    def test_default_mirror_matches_journal(self):
        from tide_daemon.cardflow.journal import mirror_root
        env = {"XDG_STATE_HOME": "/state"}
        self.assertEqual(ha.default_mirror("/board", env), mirror_root("/board", env))


if __name__ == "__main__":
    unittest.main()


# --- Tide public copy: skipped tests (fix-tests.py) ---
import unittest as _pub_unittest


def _pub_skip(reason, targets):
    g = globals()
    for name in targets:
        cls, _, meth = name.partition('.')
        klass = g.get(cls)
        if klass is None:
            continue
        if not meth:
            g[cls] = _pub_unittest.skip(reason)(klass)
        elif hasattr(klass, meth):
            setattr(klass, meth, _pub_unittest.skip(reason)(getattr(klass, meth)))


_pub_skip('public copy: history rewritten during anonymization; the test reads commits of the private repository', [
    'HistoryAuditTest.test_candidate_fold_difference_is_unexplained',
    'HistoryAuditTest.test_card_dir_without_journal_and_unknown_entry',
    'HistoryAuditTest.test_card_removed_from_one_copy',
    'HistoryAuditTest.test_change_during_read_invalidates_run',
    'HistoryAuditTest.test_consent_value_changed',
    'HistoryAuditTest.test_duplicate_seq_and_unknown_format',
    'HistoryAuditTest.test_event_removed_from_one_copy',
    'HistoryAuditTest.test_existing_strict_read_hides_board_divergence',
    'HistoryAuditTest.test_field_removed_from_one_copy',
    'HistoryAuditTest.test_full_consistent_copy_passes_with_classified_new_fields',
    'HistoryAuditTest.test_listing_error_blocks_and_keeps_denominator',
    'HistoryAuditTest.test_new_field_value_against_rule_fails',
    'HistoryAuditTest.test_read_only_sources_still_pass_and_stay_unchanged',
    'HistoryAuditTest.test_rejected_history_in_candidate_is_reported',
    'HistoryAuditTest.test_runner_changed',
    'HistoryAuditTest.test_truncated_line_in_board_and_in_authoritative_mirror',
    'HistoryAuditTest.test_unclassified_new_field_fails',
    'HistoryAuditTest.test_write_attempts_by_candidate_code_are_blocked',
])
# --- end of public copy block ---
