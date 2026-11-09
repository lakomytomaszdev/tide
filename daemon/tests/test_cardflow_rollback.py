"""PT-293 (K-38 F5): próba downgrade'u nowej historii K-38 → przypięty master. Opis harnessu: rollback_trial.py.

Historia powstaje kodem K-38 na atrapach (bez ręcznego JSON), stary silnik działa w osobnym procesie na kodzie
z `git archive` przypiętego mastera i czyta ten sam autorytatywny dziennik (mirror usługi). Kontrola przed
wycofaniem: tide_daemon/cardflow/rollback.py, procedura: docs/box-decisions.md (PT-293).

Teza obalona (czerwony test PT-293 na bazie karty): fold mastera bez wyjątku nie oznacza bezpiecznego wznowienia.
Zielony dowód: procedura (pauza → koniec procesów → kontrola → stary silnik → restart) bez skutków, próby negatywne
zatrzymują się przed startem starego silnika, kopia Tablicy jest tylko porównaniem (mirror ma pierwszeństwo).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tide_daemon.box import state as bs
from tide_daemon.cardflow import rollback
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold

from .boxflow import SLUG
from .privatebus import wait_until
from .rollback_trial import RollbackFlow

EXPECTED_CLASSES = {"host-only": rollback.SAFE, "env-pending": rollback.HOLD, "env-approved": rollback.HOLD,
                    "rebuild": rollback.HOLD, "box-question": rollback.DECISION, "box-running": rollback.DECISION}


def codes(report) -> set:
    return {(s["code"], s["card"]) for s in report["stops"]}


class DowngradeTrialTest(RollbackFlow):
    maxDiff = None

    def pause_all_and_wait_for_end(self, cards):
        """Pauza każdej karty, potem dowód końca: brak kroku bez wyniku, jednostka pudełka nieaktywna, box.release."""
        for card in cards.values():
            if not fold(self.events(card)).paused:
                self.pause(card)
        journal = self.flow.engine.journal
        self.assertTrue(wait_until(lambda: not self.box_unit_pid(self.resumed_step), timeout=60))
        self.assertTrue(wait_until(lambda: all(not fold(journal.read(c)).running() for c in cards.values()),
                                   timeout=60), [fold(journal.read(c)).running() for c in cards.values()])
        self.assertTrue(wait_until(lambda: not bs.running_steps(SLUG, SLUG), timeout=20))

    def stop_box(self):
        """Zwolnienie zasobów: `StopBox` usługi K-38 (jak `tide box stop`), potem slot i instancja wolne."""
        reply = []
        self.flow.stop_box(SLUG, "", reply.append)
        self.assertTrue(wait_until(lambda: reply, timeout=30), "StopBox bez odpowiedzi")
        data = json.loads(reply[0])
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(self.incus().instance(SLUG)["status"].lower(), "stopped")

    def test_naive_downgrade_after_clean_fold_is_unsafe(self):
        """Niebezpieczny scenariusz kontrolny: usługa K-38 zatrzymana bez pauzy i bez końca kroku w pudełku."""
        cards = self.generate()
        self.stop_flow()
        refused = rollback.check(self.flow.engine)
        self.assertFalse(refused["ok"])
        running = self.resumed_step["step"]
        self.assertIn(("not-paused", cards["box-running"]), codes(refused))
        self.assertIn(("step-running", cards["box-running"]), codes(refused))
        self.assertIn(("process-alive", cards["box-running"]), codes(refused))
        report = self.old_engine("naive", actions=self.user_actions(cards))
        self.write_artifact("history", self.snapshot(cards))
        self.write_artifact("old-engine", report)
        # Fold mastera czyta całą historię bez wyjątku…
        self.assertEqual({c: f["ok"] for c, f in report["fold"].items()}, {c: True for c in cards.values()})
        self.assertFalse(any(f["boxFieldsKnown"] for f in report["fold"].values()))
        # …a mimo to wznowienie jest niebezpieczne.
        effects = self.unsafe_effects(cards, report)
        self.write_artifact("effects", effects)
        unit = self.resumed_step["unit"]
        expected = [f"hostowe sprawdzanie jednostki kroku z pudełka: systemctl --user is-active {unit}.scope",
                    f"{cards['box-running']}: stary silnik zakończył krok z pudełka {running} jako error/lost "
                    "bez dowodu końca procesu w pudełku",
                    f"{cards['box-running']} (box-running): start na hoście tide-step-{cards['box-running']}-care-1",
                    f"{cards['env-pending']} (env-pending): start na hoście",
                    f"{cards['env-approved']} (env-approved): start na hoście",
                    f"{cards['rebuild']} (rebuild): start na hoście",
                    f"{cards['box-question']}: wznowienie na hoście sesji kroku z pudełka"]
        for text in expected:
            self.assertTrue(any(e.startswith(text) for e in effects), (text, effects))
        # Krok w pudełku nadal żyje, choć stary silnik zapisał jego koniec (podwójna praca z opiekunem na hoście).
        self.assertTrue(self.box_unit_pid(self.resumed_step))

    def test_rollback_procedure_runs_old_engine_and_its_restart_without_effects(self):
        cards = self.generate()
        names = {v: k for k, v in cards.items()}
        self.pause_all_and_wait_for_end(cards)
        held = rollback.check(self.flow.engine)
        self.assertEqual({s["code"] for s in held["stops"]}, {"box-slot-held"})   # pudełko nadal działa
        self.stop_box()
        live = rollback.check(self.flow.engine)              # przy działającej usłudze K-38
        self.assertTrue(live["ok"], live["stops"])
        self.stop_flow()                                      # zatrzymanie usługi przed instalacją starego kodu
        journal = self.flow.engine.journal
        offline = rollback.check(self.flow.engine)
        self.assertTrue(offline["ok"], offline["stops"])
        self.assertEqual({names[c]: v["class"] for c, v in offline["cards"].items()}, EXPECTED_CLASSES)
        self.assertEqual(offline["fingerprint"], live["fingerprint"])
        self.assertFalse(offline["batch1Safe"])
        self.assertEqual(sorted(offline["decisionRequired"]), sorted([cards["box-question"], cards["box-running"]]))
        self.write_artifact("check", offline)
        self.write_artifact("history", self.snapshot(cards))
        # Kopia Tablicy: tylko dowód porównania (nie źródło odtwarzania).
        board_copy = {c: _read(journal.path(c)) for c in cards.values()}
        mirror_copy = {c: _read(journal.mirror_path(c)) for c in cards.values()}
        self.assertEqual(board_copy, mirror_copy)
        for label in ("old-start", "old-restart"):
            report = self.old_engine(label)
            self.write_artifact(label, report)
            self.assertEqual([t for t in report["ticks"] if not t["ok"]], [])
            self.assertEqual(report["starts"], [], label)               # brak (podwójnego) startu
            self.assertEqual(report["systemctl"], [], label)            # brak hostowego sprawdzania jednostek
            self.assertEqual({c: e for c, e in report["appended"].items() if e}, {}, label)
            self.assertEqual(report["foreignBefore"], report["foreignAfter"], label)
            self.assertEqual(self.unsafe_effects(cards, report), [], label)
            self.assertEqual(report["procs"], [])
        self.assertEqual(rollback.verify_preserved(journal, offline["fingerprint"]), [])
        self.assertEqual({c: _read(journal.mirror_path(c)) for c in cards.values()}, mirror_copy)
        self.assertEqual({c: _read(journal.path(c)) for c in cards.values()}, board_copy)

        # Pierwszeństwo mirrora: plik Tablicy nadpisany starszą kopią (jak rsync) nie zmienia historii usługi;
        # stary silnik czyta mirror i przywraca z niego Tablicę. Komplet zdarzeń zostaje.
        victim = cards["box-running"]
        with open(journal.path(victim), "w", encoding="utf-8") as fh:
            fh.write("".join(board_copy[victim].splitlines(keepends=True)[:3]))
        report = self.old_engine("board-overwritten")
        self.assertEqual((report["starts"], report["systemctl"]), ([], []))
        self.assertEqual(_read(journal.path(victim)), mirror_copy[victim])
        self.assertEqual(rollback.verify_preserved(journal, offline["fingerprint"]), [])

        # Karta `safe` (bez znaczeń pudełka) odblokowana na starym kodzie zachowuje się jak w K-38 przy
        # `box.enabled: false`: wznowienie sesji na hoście. Pozostałe karty zostają wstrzymane i nietknięte.
        host = cards["host-only"]
        step = fold(journal.read(host)).steps[fold(journal.read(host)).last_step]
        report = self.old_engine("safe-resumed", actions=[{"op": "resume", "card": host},
                                                          {"op": "answer", "card": host, "step": step["step"]}])
        self.write_artifact("safe-resumed", report)
        self.assertTrue(report["starts"])
        for argv in report["starts"]:
            self.assertTrue(any(a.startswith(f"--unit=tide-step-{host}-") for a in argv), argv)
        self.assertIn(step["session"], report["starts"][0])
        self.assertEqual({c for c, e in report["appended"].items() if e}, {host})
        self.assertEqual(self.unsafe_effects(cards, report), [])

    def test_procedure_stops_before_downgrade_without_pause_or_proof_of_end(self):
        cards = self.generate()
        self.stop_flow()                       # strażnik K-38 nie działa: proces w pudełku żyje dalej
        engine = self.flow.engine
        unpaused = rollback.check(engine)
        self.assertFalse(unpaused["ok"])
        self.assertEqual({c for code, c in codes(unpaused) if code == "not-paused"},
                         set(cards.values()) - {cards["rebuild"]})
        refused = self.downgrade("no-pause")
        self.assertEqual(refused["ran"], False)
        # Pauza bez końca procesu też nie wystarcza (pauza nie kończy procesów).
        for card in cards.values():
            if not fold(engine.journal.read(card)).paused:
                engine.set_paused(card, True)
        paused = rollback.check(engine)
        self.assertLessEqual({"step-running", "process-alive", "box-use-held", "box-slot-held"},
                             {code for code, _c in codes(paused)})
        self.assertNotIn("not-paused", {code for code, _c in codes(paused)})
        self.assertEqual(self.downgrade("paused-alive")["ran"], False)
        # Nieznany stan jednostki (np. incus exec bez odpowiedzi) = stop, nie dowód końca.
        unknown = rollback.check(engine, unit_state=lambda step: "unknown" if step.get("runner") == "box" else "inactive")
        self.assertIn("process-unknown", {code for code, _c in codes(unknown)})
        # Stary silnik nie ruszył: proces w pudełku żyje, historia bez zmian, brak katalogów starego silnika.
        self.assertTrue(self.box_unit_pid(self.resumed_step))
        self.assertFalse([n for n in os.listdir(self.base) if n.startswith("old-") and n != "old-master"])
        self.write_artifact("refused", {"unpaused": unpaused, "pausedAlive": paused, "unknown": unknown})

    def downgrade(self, label) -> dict:
        """Wycofanie według procedury: stary silnik tylko po udanej kontroli (bez niej — odmowa)."""
        report = rollback.check(self.flow.engine)
        if not report["ok"]:
            return {"ran": False, "check": report}
        return {"ran": True, "check": report, "old": self.old_engine(label)}


class CheckUnitTest(unittest.TestCase):
    """Przeszkody kontroli na syntetycznym dzienniku (sondy procesów jako atrapy; próba K-38 jest wyżej)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-rollback-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.journal = Journal(os.path.join(self.tmp, "board"), mirror=os.path.join(self.tmp, "mirror"))
        self.engine = type("Engine", (), {"journal": self.journal})()

    def card(self, *events, paused=True):
        card = f"K-{len(self.journal.cards()) + 1}"
        self.journal.append(card, "card.started", entry="now", project=SLUG)
        for kind, data in events:
            self.journal.append(card, kind, **data)
        if paused:
            self.journal.append(card, "card.paused")
        return card

    def check(self, **probes):
        defaults = {"unit_state": lambda step: "inactive", "outside_state": lambda unit: "inactive",
                    "console_state": lambda unit: "inactive", "process_alive": lambda path: False,
                    "box_running": lambda slug, box: [], "admission_load": lambda: {"owner": None, "queue": []}}
        return rollback.check(self.engine, **{**defaults, **probes})

    def started(self, step, **extra):
        return ("step.started", {"step": step, "kind": "ticket", "ticket": "PT-1", "unit": "tide-step-" + step,
                                 **extra})

    def test_finished_host_card_is_safe_and_ok(self):
        card = self.card(self.started("S-1"), ("step.finished", {"step": "S-1", "result": "done"}))
        report = self.check()
        self.assertTrue(report["ok"], report["stops"])
        self.assertEqual(report["cards"][card]["class"], rollback.SAFE)
        self.assertTrue(report["batch1Safe"])

    def test_deferred_environment_with_disabled_box_stays_safe(self):
        card = self.card(("box.wait", {"value": "", "deferred": "environment"}))
        report = self.check()
        self.assertEqual(report["cards"][card]["class"], rollback.SAFE)
        self.assertEqual([m["code"] for m in report["cards"][card]["meanings"]], ["environment-deferred"])

    def test_step_held_by_the_trial_gate_requires_a_decision(self):
        card = self.card(("step.started", {"step": "K-38-PT-173-1", "kind": "ticket", "ticket": "PT-173",
                                           "unit": "tide-step-K-38-PT-173-1"}),
                         ("step.finished", {"step": "K-38-PT-173-1", "result": "question"}))
        report = self.check()
        self.assertEqual(report["cards"][card]["class"], rollback.DECISION)
        self.assertIn("trial-hold", [m["code"] for m in report["cards"][card]["meanings"]])

    def test_each_obstacle_stops_the_procedure(self):
        cases = {
            "outside-active": (self.started("O-1"), ("step.finished", {"step": "O-1", "result": "denied"}),
                               ("outside.started", {"step": "O-1", "command": "make test"})),
            "console-open": (self.started("C-1", mode="interactive"),),
            "open-intent": (("intent", {"action": "step-start", "key": "I-1", "kind": "ticket", "ticket": "PT-1",
                                        "unit": "tide-step-I-1"}),),
        }
        for code, events in cases.items():
            with self.subTest(code=code):
                card = self.card(*events)
                self.assertIn(code, {s["code"] for s in self.check()["stops"] if s["card"] == card})

    def test_unknown_or_alive_probes_and_held_resources_stop(self):
        self.card(self.started("S-1", runner="box", box=SLUG, boxSlug=SLUG, processFile="/nonexistent"),
                  ("step.finished", {"step": "S-1", "result": "done"}))
        for probes, code in (({"unit_state": lambda s: "unknown"}, "process-unknown"),
                             ({"unit_state": lambda s: "active"}, "process-alive"),
                             ({"process_alive": lambda p: None}, "process-unknown"),
                             ({"box_running": lambda slug, box: ["S-1"]}, "box-use-held"),
                             ({"admission_load": lambda: {"owner": {"name": SLUG}, "queue": []}}, "box-slot-held"),
                             ({"admission_load": lambda: (_ for _ in ()).throw(ValueError("x"))}, "box-slot-unknown")):
            with self.subTest(code=code):
                self.assertIn(code, {s["code"] for s in self.check(**probes)["stops"]})

    def test_unreadable_history_is_unknown_not_empty(self):
        card = self.card()
        with open(self.journal.mirror_path(card), "a", encoding="utf-8") as fh:
            fh.write('{"v": 1, "seq": 99')
        report = self.check()
        self.assertIn(("unknown-history", card), codes(report))
        self.assertNotIn(card, report["cards"])

    def test_command_line_check_and_verify_are_read_only_json(self):
        import io
        board = os.path.join(self.tmp, "cli-board")
        os.makedirs(board)
        out = io.StringIO()
        self.assertEqual(rollback.main(["check", "--board", board], out=out), 0)
        report = json.loads(out.getvalue())
        self.assertEqual((report["ok"], report["stops"], report["batch1Safe"]), (True, [], True))
        path = os.path.join(self.tmp, "check.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fingerprint": {"K-7": {"events": 1, "sha256": "0"}}}, fh)
        out = io.StringIO()
        self.assertEqual(rollback.main(["verify", path, "--board", board], out=out), 1)
        self.assertEqual([x["card"] for x in json.loads(out.getvalue())["lost"]], ["K-7"])
        self.assertEqual(os.listdir(board), [])

    def test_truncated_mirror_is_reported_as_lost_history(self):
        card = self.card(self.started("S-1"), ("step.finished", {"step": "S-1", "result": "done"}))
        fingerprint = self.check()["fingerprint"]
        lines = _read(self.journal.mirror_path(card)).splitlines(keepends=True)
        with open(self.journal.mirror_path(card), "w", encoding="utf-8") as fh:   # rsync starszej kopii nad mirrorem
            fh.write("".join(lines[:2]))
        self.assertEqual([x["card"] for x in rollback.verify_preserved(self.journal, fingerprint)], [card])


class RevertPlanTest(unittest.TestCase):
    """Odwrócenie integracji na syntetycznym repozytorium, z commitem scalenia jak w MergeCardToMain."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="tide-rollback-git-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "T",
               "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.env = {**os.environ, **env}
        self.git("init", "-q", "-b", "master")
        self.commit("a.txt", "master 1")
        self.git("checkout", "-q", "-b", "card")
        self.commit("box.txt", "card 1")
        self.commit("box.txt", "card 2")
        self.git("checkout", "-q", "master")
        self.commit("m.txt", "master 2")

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", self.repo, *args], env=self.env, capture_output=True, text=True,
                              check=True).stdout.strip()

    def run_git(self, *args):
        done = subprocess.run(["git", "-C", self.repo, *args], env=self.env, capture_output=True, text=True)
        return done.returncode == 0, done.stdout

    def commit(self, name, text):
        with open(os.path.join(self.repo, name), "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        self.git("add", name)
        self.git("commit", "-q", "-m", text)

    def test_tide_merge_commit_is_reverted_with_mainline_one(self):
        from tide_daemon.cardflow.gitops import FlowGit
        from tide_daemon.cardflow.mergeop import MergeWorktree
        before, source = self.git("rev-parse", "master"), self.git("rev-parse", "card")
        tree = self.git("merge-tree", "--write-tree", before, source)
        _already, body, commit = MergeWorktree.commit_plan(FlowGit(), self.repo, before, source, tree, "Merge card")
        # Ten sam obiekt, który zapisuje MergeWorktree.execute (`hash-object -t commit -w`).
        made = subprocess.run(["git", "-C", self.repo, "hash-object", "-t", "commit", "-w", "--stdin"], input=body,
                              env=self.env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(made, commit)
        self.git("update-ref", "refs/heads/master", commit, before)
        self.git("reset", "-q", "--hard", "master")
        plan = rollback.revert_plan({"resultCommit": commit, "targetCommitBefore": before, "sourceCommit": source},
                                    self.run_git)
        self.assertEqual((plan["kind"], plan["commands"]), ("merge", [f"git revert -m 1 {commit}"]))
        self.git("revert", "--no-edit", "-m", "1", commit)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), self.git("rev-parse", before + "^{tree}"))
        # Commity karty zostają w historii (odwrócenie nie przepisuje ani nie kasuje).
        self.git("merge-base", "--is-ancestor", source, "HEAD")

    def test_integration_without_merge_commit_reverts_the_commits(self):
        before = self.git("rev-parse", "master")
        self.git("cherry-pick", "master..card")
        tip = self.git("rev-parse", "HEAD")
        plan = rollback.revert_plan({"resultCommit": tip, "targetCommitBefore": before, "sourceCommit": tip},
                                    self.run_git)
        self.assertEqual(plan["kind"], "non-merge")
        self.assertEqual(plan["commits"], self.git("rev-list", f"{before}..{tip}").split())
        self.git("revert", "--no-edit", *plan["commits"])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), self.git("rev-parse", before + "^{tree}"))

    def test_missing_or_inconsistent_record_gives_no_command(self):
        before, source = self.git("rev-parse", "master"), self.git("rev-parse", "card")
        self.assertEqual(rollback.revert_plan(None, self.run_git)["commands"], [])
        self.assertEqual(rollback.revert_plan({"resultCommit": before, "targetCommitBefore": before},
                                              self.run_git)["kind"], "no-commit")
        self.git("merge", "-q", "--no-ff", "-m", "manual", "card")
        merge = self.git("rev-parse", "HEAD")
        wrong = rollback.revert_plan({"resultCommit": merge, "targetCommitBefore": source, "sourceCommit": before},
                                     self.run_git)
        self.assertEqual((wrong["kind"], wrong["commands"]), ("inconsistent", []))


class DecisionsDocTest(unittest.TestCase):
    def test_box_decisions_describe_the_table_and_the_procedure_for_the_pinned_master(self):
        from .rollback_trial import OLD_SHA, REPO_ROOT
        text = _read(os.path.join(REPO_ROOT, "docs", "box-decisions.md"))
        section = text[text.index("## PT-293"):]
        self.assertIn("| Rodzaj zdarzenia / pola | Znaczenie w K-38 | Znaczenie na przypiętym master | Ryzyko | "
                      "Warunek i działanie rollbacku |", section)
        for needle in ("`git revert -m 1 <resultCommit>`", "`make install`", "pozostają wstrzymane",
                       "a6efe28cdfe2e727bbc1dec085ac3aebfb76abe6", "Blokada wydania", "rollback check",
                       "rollback verify", "rollback revert-plan K-38", "osobnej zgodzie właściciela"):
            self.assertIn(needle, section)
        if "TIDE_ROLLBACK_OLD_SHA" not in os.environ:
            self.assertIn(OLD_SHA, section)


def _read(path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    unittest.main()
