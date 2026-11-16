"""Skrypty odbioru rdzenia K-99 (PT-471) na atrapach: klient D-Bus, monitor, runner gita. Bez usługi i bez Tablicy."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from scripts.k99 import capture_acceptance as cap
from scripts.k99 import measure_branchmap as measure

HOME = "/home/tester"
FORBIDDEN = ("MergeCard", "MergeCardToMain", "CreateCard", "UpdateCard", "BranchMap", "Git.BranchMap")


class FakeClient:
    """Atrapa `BusClient`: zapisuje wywołania, odpowiada z tabeli `files` (karta → liczba plików podglądu)."""

    def __init__(self, files, can_merge=None, methods=None):
        self.files, self.can_merge, self._methods = files, can_merge or {}, methods
        self.calls = []

    def methods(self):
        return set(cap.READ_ONLY_CALLS) if self._methods is None else self._methods

    def call(self, member, *args):
        assert member in cap.READ_ONLY_CALLS, member
        self.calls.append((member, args[0] if args else ""))
        card = args[0]
        if member == "ListCardBranches":
            return {"ok": True, "branches": [{"ref": "refs/heads/master"}], "defaultTargetRef": "refs/heads/master",
                    "mainRef": "refs/heads/master", "warnings": [], "hasMore": False}
        if card not in self.files:
            return {"ok": False, "error": "no-base", "message": "brak gałęzi karty"}
        merge = self.can_merge.get(card, True)
        return {"ok": True, "id": card, "fileCount": self.files[card], "commitCount": 3, "canMerge": merge,
                "targetRef": "refs/heads/master", "conflictCount": 0, "previewToken": "tok-" + card,
                "repo": f"{HOME}/DevWorkspace/plasma-tide", "targetWorktree": f"{HOME}/DevWorkspace/plasma-tide",
                "blockedReasons": [] if merge else [{"reason": "source-dirty", "message": "Gałąź karty nie ma "
                                                                                         "czystego checkoutu."}]}


def cards(*specs):
    return [{"card": c, "state": s} for c, s in specs]


def tree(total=45, nodes=None):
    nodes = nodes or [{"id": f"card:K-{n}", "kind": "card", "card": f"K-{n}", "state": "closed"} for n in (3, 5, 7)]
    return {"ok": True, "project": "plasma-tide", "repo": f"{HOME}/DevWorkspace/plasma-tide", "nodes": nodes,
            "edges": [], "total": total, "truncated": False, "warnings": [f"{HOME}/x"]}


def run_capture(client, out, branch_map=None, **extra):
    statuses = []

    def status(repo):
        statuses.append(repo)
        return [" M a.txt"]

    return cap.capture(client=client, branch_map=branch_map or (lambda text: tree()), status=status,
                       rev=lambda repo, ref: "f" * 40, now="2026-11-13T10:00:00Z", out=out, home=HOME,
                       board_dir=f"{HOME}/DevWorkspace/tide-board", repo=f"{HOME}/DevWorkspace/plasma-tide",
                       worktree=f"{HOME}/DevWorkspace/plasma-tide-worktrees/pt-471", **extra)


class BusClientTest(unittest.TestCase):
    def fake(self, out='{"type":"s","data":["{\\"ok\\":true}"]}', code=0):
        argvs = []

        def runner(argv):
            argvs.append(argv)
            return code, out, "err"
        return cap.BusClient(runner), argvs

    def test_forbidden_members_are_refused_before_sending(self):
        client, argvs = self.fake()
        for member in FORBIDDEN:
            with self.assertRaises(cap.ForbiddenCall):
                client.call(member, "K-3", "{}")
        self.assertEqual((argvs, client.calls), ([], []))

    def test_read_calls_are_sent_as_busctl_with_string_arguments(self):
        client, argvs = self.fake()
        self.assertEqual(client.call("PreviewCardMerge", "K-3", '{"targetRef":"refs/heads/master"}'), {"ok": True})
        argv = argvs[0]
        self.assertEqual(argv[:3], ["busctl", "--user", "--json=short"])
        self.assertEqual(argv[-7:], ["org.tide.Daemon", "/Board", "org.tide.Daemon.Board", "PreviewCardMerge", "ss",
                                    "K-3", '{"targetRef":"refs/heads/master"}'])
        self.assertEqual(client.calls, [("PreviewCardMerge", "K-3")])

    def test_introspection_lists_methods_and_missing_one_is_an_error(self):
        listing = ("NAME                      TYPE      SIGNATURE RESULT/VALUE FLAGS\n"
                   "org.tide.Daemon.Board     interface -         -            -\n"
                   ".Snapshot                 method    -         s            -\n"
                   ".ListCardBranches         method    ss        s            -\n"
                   ".Changed                  signal    -         -            -\n")
        client, argvs = self.fake(out=listing)
        self.assertEqual(client.methods(), {"Snapshot", "ListCardBranches"})
        self.assertNotIn("--json=short", argvs[0])  # introspect nie ma wyjścia JSON
        with self.assertRaises(cap.CaptureError) as caught:
            cap.require_methods(client)
        self.assertIn("PreviewCardMerge", str(caught.exception))

    def test_busctl_failure_and_bad_reply_are_errors(self):
        client, _ = self.fake(code=1)
        with self.assertRaises(cap.CaptureError):
            client.call("Snapshot")
        client, _ = self.fake(out="nie json")
        with self.assertRaises(cap.CaptureError):
            client.call("Snapshot")


class ChooseCardTest(unittest.TestCase):
    def probe_from(self, files):
        order = []

        def probe(card):
            order.append(card)
            return {"card": card, "fileCount": files[card], "canMerge": True} if card in files else \
                {"card": card, "error": "no-base"}
        return probe, order

    def test_step1_k3_over_100_files(self):
        probe, order = self.probe_from({"K-3": 140, "K-5": 900})
        chosen = cap.choose_card(cards(("K-3", "closed"), ("K-5", "closed")), probe)
        self.assertEqual((chosen["step"], chosen["card"], chosen["fileCount"]), (1, "K-3", 140))
        self.assertEqual(order, ["K-3"])  # reszty nie trzeba oglądać

    def test_step2_other_card_over_100_files_prefers_closed(self):
        probe, order = self.probe_from({"K-3": 80, "K-4": 120, "K-9": 300})
        chosen = cap.choose_card(cards(("K-3", "closed"), ("K-4", "active"), ("K-9", "closed")), probe)
        self.assertEqual((chosen["step"], chosen["card"]), (2, "K-9"))
        self.assertEqual(order, ["K-3", "K-9"])
        self.assertEqual([r["card"] for r in chosen["reviewed"]], ["K-3", "K-9"])

    def test_step3_largest_card_even_when_cannot_merge_and_tie_goes_to_newer(self):
        files = {"K-3": 10, "K-4": 60, "K-8": 60, "K-9": 40}
        probe, _ = self.probe_from(files)
        chosen = cap.choose_card(cards(("K-3", "closed"), ("K-4", "active"), ("K-8", "active"), ("K-9", "merged"),
                                       ("K-11", "closed")), probe)
        self.assertEqual((chosen["step"], chosen["card"], chosen["fileCount"]), (3, "K-8", 60))
        reviewed = {r["card"]: r for r in chosen["reviewed"]}
        self.assertEqual(reviewed["K-11"], {"card": "K-11", "error": "no-base"})
        self.assertEqual(len(reviewed), 5)

    def test_step3_keeps_card_that_cannot_merge(self):
        client = FakeClient({"K-3": 20, "K-6": 90}, can_merge={"K-6": False})
        chosen = cap.choose_card(cards(("K-3", "closed"), ("K-6", "active")), cap.make_probe(client))
        self.assertEqual((chosen["step"], chosen["card"], chosen["result"]["canMerge"]), (3, "K-6", False))

    def test_no_preview_anywhere_gives_none_not_a_question(self):
        probe, _ = self.probe_from({})
        self.assertIsNone(cap.choose_card(cards(("K-3", "closed")), probe))


class ProbeAndCaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "k99")

    def read(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_probe_uses_default_target_from_list_and_only_read_calls(self):
        client = FakeClient({"K-3": 150})
        info = cap.make_probe(client)("K-3")
        self.assertEqual(client.calls, [("ListCardBranches", "K-3"), ("PreviewCardMerge", "K-3")])
        self.assertEqual(info["request"], {"targetRef": "refs/heads/master"})

    def test_capture_calls_only_reads_and_writes_four_files(self):
        client = FakeClient({"K-3": 150, "K-5": 20})
        meta = run_capture(client, self.out)
        self.assertEqual({m for m, _ in client.calls}, {"ListCardBranches", "PreviewCardMerge"})
        self.assertFalse({m for m, _ in client.calls} & set(FORBIDDEN))
        self.assertEqual(sorted(os.listdir(self.out)),
                         ["branchmap.json", "capture.json", "list_card_branches.json", "preview_card_merge.json"])
        saved = self.read("capture.json")
        self.assertEqual(saved["selection"], {"step": 1, "card": "K-3", "fileCount": 150, "threshold": 100,
                                              "reviewed": [{"card": "K-3", "fileCount": 150, "canMerge": True}]})
        self.assertEqual((saved["card"], saved["target"], saved["mapTotal"], saved["capturedAt"]),
                         ("K-3", "refs/heads/master", 45, "2026-11-13T10:00:00Z"))
        self.assertEqual(saved["cardBranch"], {"name": cap.DEFAULT_CARD_BRANCH, "sha": "f" * 40})
        self.assertTrue(saved["gitStatus"]["board"]["unchanged"] and saved["gitStatus"]["repo"]["unchanged"])
        self.assertEqual(meta["selection"], saved["selection"])
        self.assertEqual(self.read("preview_card_merge.json")["previewToken"], "tok-K-3")

    def test_paths_are_replaced_with_markers_in_every_file(self):
        run_capture(FakeClient({"K-3": 150}), self.out)
        for name in os.listdir(self.out):
            with open(os.path.join(self.out, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(HOME, text, name)
            self.assertNotIn("/home/", text, name)
        self.assertEqual(self.read("branchmap.json")["repo"], "<HOME>/DevWorkspace/plasma-tide")
        self.assertEqual(self.read("capture.json")["worktreeHead"], "f" * 40)

    def test_sanitizer_handles_worktrees_keys_and_nesting(self):
        clean = cap.sanitizer(HOME)
        value = {HOME + "/a": [HOME + "/DevWorkspace/plasma-tide-worktrees/pt-1/x", 3, None]}
        self.assertEqual(clean(value), {"<HOME>/a": ["<WORKTREES>/pt-1/x", 3, None]})

    def test_step3_result_is_written_without_asking_and_keeps_blocked_reasons(self):
        client = FakeClient({"K-3": 10, "K-5": 70}, can_merge={"K-5": False})
        run_capture(client, self.out)
        saved = self.read("capture.json")
        self.assertEqual((saved["selection"]["step"], saved["card"]), (3, "K-5"))
        self.assertFalse(saved["preview"]["canMerge"])
        self.assertEqual(saved["preview"]["blockedReasons"][0]["reason"], "source-dirty")
        self.assertFalse(self.read("preview_card_merge.json")["canMerge"])

    def test_small_map_writes_nothing(self):
        with self.assertRaises(cap.TooSmall):
            run_capture(FakeClient({"K-3": 150}), self.out, branch_map=lambda text: tree(total=39))
        self.assertFalse(os.path.exists(self.out))

    def test_changed_status_is_reported_not_hidden(self):
        states = iter([[" M a"], [" M a"], [" M a", "?? b"], [" M a"]])
        meta = cap.capture(client=FakeClient({"K-3": 150}), branch_map=lambda text: tree(), status=lambda r: next(states),
                           rev=lambda r, ref: "", now="x", out=self.out, home=HOME, board_dir="/b", repo="/r")
        self.assertFalse(meta["gitStatus"]["board"]["unchanged"])
        self.assertTrue(meta["gitStatus"]["repo"]["unchanged"])

    def test_git_status_never_refreshes_the_index(self):
        seen = []

        def runner(argv):
            seen.append(argv)
            return SimpleNamespace(returncode=0, stdout="?? b\n M a\n")
        self.assertEqual(cap.git_status("/r", runner), [" M a", "?? b"])
        self.assertEqual(seen[0], ["git", "--no-optional-locks", "-C", "/r", "status", "--short"])
        self.assertNotIn("fetch", seen[0])


class FakeHost:
    """Atrapa hosta: monitor odpowiada dopiero w `pump` (jak pula z sygnałem Qt) albo od razu (zły przypadek)."""

    def __init__(self, immediate=False):
        self.immediate, self.pending, self.compute_threads = immediate, None, []

    def pump(self, done, timeout=1):
        if self.pending:
            self.pending()
        return done()

    @property
    def monitor(self):
        return self

    def close(self):
        pass

    def branch_map(self, request, reply):
        text = json.dumps({"ok": True, "total": 45})
        self.compute_threads.append(-1)  # wątek roboczy ≠ wywołujący
        if self.immediate:
            reply(text)
        else:
            self.pending = lambda: reply(text)


class MeasureTest(unittest.TestCase):
    def test_summary_best_median_worst(self):
        stats = measure.summarize([0.9, 0.2, 0.5, 0.4, 0.3])
        self.assertEqual((stats["best"], stats["median"], stats["worst"]), (0.2, 0.4, 0.9))
        self.assertEqual(measure.summarize([1.0, 3.0, 2.0, 10.0])["median"], 2.5)
        with self.assertRaises(ValueError):
            measure.summarize([])

    def test_call_returns_before_reply(self):
        run = cap.time_branch_map(FakeHost(), "{}", clock=_ticks(0.0, 0.01, 0.75))
        self.assertTrue(run["returnedBeforeReply"] and run["replyOnCallingThread"] and run["computedOffCallingThread"])
        self.assertAlmostEqual(run["seconds"], 0.75)  # do odpowiedzi z puli, nie do powrotu wywołania
        self.assertAlmostEqual(run["returnSeconds"], 0.01)

    def test_synchronous_reply_is_detected(self):
        run = cap.time_branch_map(FakeHost(immediate=True), "{}")
        self.assertFalse(run["returnedBeforeReply"])

    def test_measure_runs_all_repeats_and_verdict(self):
        cold = measure.measure(FakeHost, "{}", 5, shared=False)
        warm = measure.measure(FakeHost, "{}", 5, shared=True)
        self.assertEqual((len(cold["samples"]), len(warm["samples"])), (5, 5))
        self.assertTrue(measure.verdict(cold, warm)["passed"])
        slow = dict(cold, median=1.2)
        self.assertFalse(measure.verdict(slow, warm)["passed"])
        self.assertFalse(measure.verdict(dict(cold, returnedBeforeReply=False), warm)["passed"])

    def test_real_monitor_host_returns_before_reply_and_computes_off_thread(self):
        """`Host` z prawdziwym `GitMonitor` i pustym dziennikiem: ten sam tor co w pomiarze, bez usługi."""
        import sys
        sys.path.insert(0, os.path.join(cap.REPO_ROOT, "daemon"))
        from tide_daemon.cardflow.journal import Journal
        with tempfile.TemporaryDirectory() as root:
            board = {"projects": [{"slug": "p", "name": "p", "repo": root}], "cards": {}}
            host = cap.Host(Journal(root, mirror=os.path.join(root, "mirror")), board)
            # Pusty dziennik liczy się szybciej, niż `add_done_callback` zdąży się podpiąć (wtedy odpowiedź wraca
            # synchronicznie — własność puli, nie błąd); przy prawdziwych danych liczenie trwa dłużej.
            compute = host.monitor._branch_map
            host.monitor._branch_map = lambda *args: (time.sleep(0.05), compute(*args))[1]
            try:
                run = cap.time_branch_map(host, json.dumps({"project": "p", "limit": 500}))
            finally:
                host.close()
        self.assertTrue(run["response"]["ok"], run["response"])
        self.assertEqual(run["response"]["total"], 0)
        self.assertEqual({k: run[k] for k in ("returnedBeforeReply", "replyOnCallingThread", "computedOffCallingThread")},
                         {"returnedBeforeReply": True, "replyOnCallingThread": True, "computedOffCallingThread": True})

    def test_fewer_than_five_repeats_is_refused(self):
        self.assertEqual(measure.main(["--repeats", "4"]), 1)


def _ticks(*values):
    it = iter(values)
    return lambda: next(it)


if __name__ == "__main__":
    unittest.main()
