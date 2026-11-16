"""K-125 #3 (PT-488): commit planu w `_plan_next` bez wczytania Tablicy, gdy wynik jest już w dzienniku.

Dane z reproduktora stanów produkcji (`perf_idle_scale`, PT-486): karta `approved` (zatwierdzony plan w spoczynku,
ticket czeka na bloker) i karta `held` mają udane wyniki `card-branch` i `plan-commit`. W ciepłym ticku `_plan_next`
bierze Tablicę z kontekstu ticku, `_planning_branch` zwraca zapisany `card-branch` przed `git rev-parse`, a
`_plan_commit` kończy się przed `_plan_on_board` (wczytanie Tablicy) i `_plan_files`. Granice liczenia: `BoardStore.load`
(licznik usługi reproduktora) i metody gita silnika w czasie `_plan_next`. Czasu nie mierzymy."""
import collections
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.gitops import FlowGitError

from . import perf_board_refresh as pbr
from . import perf_idle_scale as scale

GIT = ("sha", "branch_worktree", "commit_plan", "main_branch", "run")


class PlanNextWatch:
    """Wywołania `_plan_next` w jednym ticku: wczytania Tablicy, metody gita, `_plan_on_board` i `_plan_files`
    wykonane w jego trakcie (według karty)."""

    def __init__(self, service):
        self.service = service
        self.calls, self.loads = collections.Counter(), collections.Counter()
        self.git = collections.defaultdict(list)
        self.helpers = collections.defaultdict(list)
        self._inside: list[str] = []

    @contextlib.contextmanager
    def __call__(self, commit=None):
        engine, service = self.service.engine, self.service
        real_next = FlowEngine._plan_next

        def plan_next(this, card, st):
            self._inside.append(card)
            loads = service.loads
            try:
                return real_next(this, card, st)
            finally:
                self._inside.pop()
                self.calls[card] += 1
                self.loads[card] += service.loads - loads

        def spy(record, name, real):
            def call(*args, **kwargs):
                if self._inside:
                    record[self._inside[-1]].append(name)
                return real(*args, **kwargs)
            return call
        patches = [mock.patch.object(FlowEngine, "_plan_next", plan_next)]
        for name in GIT:
            real = commit if name == "commit_plan" and commit is not None else getattr(engine.git, name)
            patches.append(mock.patch.object(engine.git, name, spy(self.git, name, real)))
        for name in ("_plan_on_board", "_plan_files"):
            patches.append(mock.patch.object(engine, name, spy(self.helpers, name, getattr(engine, name))))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            yield self


class PlanCommitRestTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="tide-plan-commit-rest-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        env = pbr.isolated_env(self.base)
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        self.service = pbr.Service(self.base, scale.FULL_PAUSED_CARDS, box_file=False)
        self.addCleanup(self.service.close)
        scale.populate(self.service, self.base, 0.2)
        roles = scale.assign_roles(len(self.service.cards))
        self.engine = self.service.engine
        self.journal = self.engine.journal
        self.approved = self.service.cards[roles.index("approved")]
        self.held = self.service.cards[roles.index("held")]
        self.commits = []
        # Kopia planu na gałęzi karty dotyczy projektów z planami w repozytorium (`ai-docs: repo`); projekt
        # z `ai-docs: tide` — osobny test.
        self.ai_docs(self.approved, "repo")
        self.branch = self.journal.state(self.approved).results[("card-branch", f"card/{self.approved.lower()}")][
            "branch"]
        # Treść pliku planu nie jest tu badana (reproduktor nie ma pakietu artefaktów planu): pusty zestaw plików.
        files = mock.patch.object(self.engine, "_plan_files", return_value={})
        files.start()
        self.addCleanup(files.stop)

    def ai_docs(self, card, value):
        slug = self.journal.state(card).started["project"]
        project = self.engine.store.load().projects[slug]
        reply = json.loads(self.service.board.upsert_project(json.dumps(
            {"slug": slug, "repo": project.repo, "aiDocs": value})))
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.engine._project(card)[1].ai_docs, value)

    def commit(self, repo, branch, worktree, files):
        """Atrapa `GitOps.commit_plan` (repozytoria reproduktora nie mają gałęzi kart): zapisuje wywołanie dla gałęzi
        karty `approved` (inne karty tego projektu też mogą commitować w pierwszym ticku)."""
        if branch == self.branch:
            self.commits.append(branch)
        return {"commit": "c0ffee", "already": False}

    def tick(self, commit=None):
        with PlanNextWatch(self.service)(commit or self.commit) as watch, contextlib.redirect_stdout(io.StringIO()):
            self.engine.tick()
        return watch

    def results(self, card, action):
        return [e for e in self.journal.read_complete(card) if e["type"] == "result" and e.get("action") == action]

    def change_plan(self, card):
        """Nowy klucz `plan-commit`: zdarzenie zmiany planu (ponowny `ticket.assigned` tego samego wykonawcy)."""
        assigned = next(e for e in reversed(self.journal.read_complete(card)) if e["type"] == "ticket.assigned")
        data = {k: v for k, v in assigned.items() if k not in ("v", "seq", "ts", "card", "type")}
        self.journal.append(card, "ticket.assigned", **data)

    def test_second_warm_tick_reads_no_board_and_calls_no_git_for_the_card_branch_and_plan_commit(self):
        self.tick()                                                   # rozgrzewka
        watch = self.tick()
        for card in (self.approved, self.held):
            with self.subTest(card=card):
                self.assertGreaterEqual(watch.calls[card], 1, "tick nie doszedł do `_plan_next`")
                self.assertEqual(watch.loads[card], 0, "`BoardStore.load` w `_plan_next` ciepłego ticku")
                self.assertEqual(watch.git[card], [], "git gałęzi karty i commita planu w ciepłym ticku")
                self.assertEqual(watch.helpers[card], [], "`_plan_on_board`/`_plan_files` przy zapisanym wyniku")
        self.assertEqual(self.commits, [])

    def test_changed_plan_is_committed_in_the_next_tick_and_then_skipped_again(self):
        self.tick()
        before = len(self.results(self.approved, "plan-commit"))
        self.change_plan(self.approved)
        watch = self.tick()
        self.assertEqual(len(self.commits), 1, "nowy klucz `plan-commit`: commit planu w następnym ticku")
        self.assertIn("_plan_files", watch.helpers[self.approved])
        results = self.results(self.approved, "plan-commit")
        self.assertEqual(len(results), before + 1)
        self.assertTrue(results[-1]["ok"])
        self.assertEqual(results[-1]["key"], scale.plan_commit_key(self.journal.state(self.approved)))
        watch = self.tick()
        self.assertEqual(len(self.commits), 1)
        self.assertEqual(watch.helpers[self.approved], [])

    def test_failed_plan_commit_is_retried_in_the_next_tick(self):
        self.tick()
        self.change_plan(self.approved)
        failures = []

        def failing(repo, branch, worktree, files):
            if branch != self.branch:
                return {"commit": "c0ffee", "already": False}
            failures.append(branch)
            raise FlowGitError("przejściowy błąd")
        self.tick(failing)
        self.assertFalse(self.results(self.approved, "plan-commit")[-1]["ok"])
        self.tick(failing)
        self.assertEqual(len(failures), 2, "nieudany commit jest ponawiany co tick")
        self.tick()
        self.assertEqual(len(self.commits), 1)
        self.assertTrue(self.results(self.approved, "plan-commit")[-1]["ok"])

    def test_board_docs_project_keeps_no_plan_copy_on_the_card_branch(self):
        """`ai-docs: tide`: nowy klucz daje wynik `skipped` bez commita, potem pomijanie bez wczytania Tablicy."""
        self.ai_docs(self.approved, "tide")
        self.tick()
        self.change_plan(self.approved)
        watch = self.tick()
        result = self.results(self.approved, "plan-commit")[-1]
        self.assertEqual((result["ok"], result.get("skipped")), (True, "ai-docs: tide"))
        self.assertIn("_plan_on_board", watch.helpers[self.approved])
        self.assertEqual(self.commits, [], "kopia planu na gałęzi karty projektu `ai-docs: tide`")
        watch = self.tick()
        self.assertEqual((watch.loads[self.approved], watch.helpers[self.approved]), (0, []))
        self.assertEqual(self.commits, [])

    def test_outside_the_tick_plan_next_reads_the_board_fresh(self):
        """Wywołanie spoza ticku (np. domknięcie, D-Bus): `_plan_next` czyta Tablicę świeżo jak dotąd."""
        self.tick()
        with PlanNextWatch(self.service)(self.commit) as watch:
            self.engine._plan_next(self.approved, self.journal.state(self.approved))
        self.assertGreaterEqual(watch.loads[self.approved], 1)
        self.assertEqual(watch.git[self.approved], [], "zapisane wyniki nadal bez gita")
        self.assertEqual(watch.helpers[self.approved], [])


if __name__ == "__main__":
    unittest.main()
