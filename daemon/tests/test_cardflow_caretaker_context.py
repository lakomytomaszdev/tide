"""Kontekst opiekuna karty (K-37, PT-153): katalog `<krok>/in/context/` z `cardflow/caretakerctx.py`.

Najpierw moduł na złożonych dziennikach i katalogach w katalogu tymczasowym (maskowanie, limit 400 kB, pliki spoza
karty i dowiązania, poprzednie próby, rejestr źródeł), potem przebieg przez `FlowEngine` z atrapą `claude`.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker, caretakerctx

from .privatebus import wait_until
from .test_cardflow import FlowInProcess, git
from .test_cardflow_caretaker import Journal

TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
ENV_SECRET = "atrapa-sekret-z-env-42"
PAIR_SECRET = "haslo-z-pary-klucz-sekret"
ENV_ONLY = "wartosc-tylko-w-srodowisku-77"
SECRETS = (TOKEN, ENV_SECRET, PAIR_SECRET, ENV_ONLY)
ENV = {"TIDE_CTX_API_TOKEN": ENV_ONLY, "HOME": "/home/nikt"}
LEAK = "TAJNE-Z-HOME-NIE-MOZE-WYCIEC"


class Tree:
    """Tablica (katalog karty), worktree i katalog kroku problemu w katalogu tymczasowym."""

    def __init__(self, case):
        self.base = tempfile.mkdtemp(prefix="tide-ctx-")
        case.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.card_dir = os.path.join(self.base, "board", "flow", "K-1")
        self.worktree = os.path.join(self.base, "ws", "proj-pt-7")
        self.step_dir = self.worktree + ".step"
        self.home = os.path.join(self.base, "home")
        for folder in (self.card_dir, self.worktree, os.path.join(self.step_dir, "out"),
                       os.path.join(self.step_dir, "in"), self.home):
            os.makedirs(folder, exist_ok=True)
        self.write(os.path.join(self.home, "tajne.md"), LEAK + "\n")

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def journal(self):
        j = Journal()
        for event in j.events:
            event["repo"] = self.worktree
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        for event in j.events:
            if event["type"] == "step.started":
                event.update(worktree=self.worktree, stepDir=self.step_dir)
        return j

    def collect(self, j, problem=None, step="", env=ENV):
        problem = problem or caretaker.problems(j.st)[0]
        return {f.path: f.render() for f in caretakerctx.collect(problem, j.st, j.events, card="K-1",
                                                                 card_dir=self.card_dir,
                                                                 worktree=self.worktree, step=step, env=env)}


class MaskingTest(unittest.TestCase):
    def test_token_env_variable_and_key_secret_pair_are_masked_in_every_file(self):
        tree = Tree(self)
        j = tree.journal()
        reason = f"GITHUB_TOKEN={TOKEN} export TIDE_X_SECRET={ENV_SECRET} password: {PAIR_SECRET}"
        j.events[-1]["reason"] = reason
        j.add("step.note", step="K-1-PT-7-2", token=PAIR_SECRET, detail={"apiKey": PAIR_SECRET})
        tree.write(os.path.join(tree.step_dir, "out", "step.log"), "\n".join([
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": f"GITHUB_TOKEN={TOKEN} gh api user"}}]}}),
            f"export TIDE_X_SECRET={ENV_SECRET}",
            f'{{"password": "{PAIR_SECRET}"}}',
            f"wartość z bieżącego środowiska: {ENV_ONLY}",
        ]) + "\n")
        tree.write(os.path.join(tree.card_dir, "steps", "K-1-PT-7-2", "note.md"), f"api_key={PAIR_SECRET}\n")
        files = tree.collect(j)
        for path, text in files.items():
            for secret in SECRETS:
                self.assertNotIn(secret, text, path)
        self.assertIn("steps/K-1-PT-7-2/step-log.md", files)
        self.assertIn("•••", files["steps/K-1-PT-7-2/step-log.md"])
        events = [json.loads(line) for line in files["journal.jsonl"].splitlines()]
        self.assertEqual(events[-1]["token"], "•••")
        self.assertIn("GITHUB_TOKEN=•••", next(e for e in events if e["type"] == "step.finished"
                                               and e["step"] == "K-1-PT-7-2")["reason"])


class LimitTest(unittest.TestCase):
    def test_journal_over_the_limit_keeps_the_newest_events_with_a_note(self):
        tree = Tree(self)
        j = tree.journal()
        for index in range(400):
            j.add("step.note", step="K-1-PT-7-2", text=f"zdarzenie-{index:04d} " + "x" * 4000)
        tree.write(os.path.join(tree.step_dir, "out", "step.log"),
                   "\n".join(f"linia {i} " + "y" * 250 for i in range(2000)) + "\n")
        problem = caretaker.problems(j.st)[0]
        context = os.path.join(tree.step_dir, "in", "context")
        listed = caretakerctx.build(problem, j.st, j.events, card="K-1", card_dir=tree.card_dir, worktree=tree.worktree,
                                    context_dir=context, env=ENV)
        size = sum(os.path.getsize(os.path.join(folder, name))
                   for folder, _, names in os.walk(context) for name in names)
        self.assertLessEqual(size, caretakerctx.MAX_BYTES)
        self.assertGreater(size, caretakerctx.MAX_BYTES // 2)
        with open(os.path.join(context, "journal.jsonl"), encoding="utf-8") as fh:
            journal = fh.read()
        first, *rest = journal.splitlines()
        self.assertTrue(first.startswith("(przycięto "), first)
        self.assertIn("najstarszych zdarzeń", first)
        self.assertIn("zdarzenie-0399", journal)
        self.assertNotIn("zdarzenie-0200", journal)          # spoza ostatnich 200 zdarzeń
        kept = [json.loads(line)["seq"] for line in rest]
        self.assertEqual(kept, sorted(kept))
        self.assertEqual(kept[-1], j.events[-1]["seq"])
        self.assertTrue(next(item for item in listed if item["path"].endswith("journal.jsonl"))["trimmed"])

    def test_huge_text_files_are_cut_to_the_hard_limit(self):
        tree = Tree(self)
        j = tree.journal()
        for name in ("plan.md", "source.md"):
            tree.write(os.path.join(tree.card_dir, "plan", name), "plan " * 300_000)
        files = caretakerctx.collect(caretaker.problems(j.st)[0], j.st, j.events, card="K-1", card_dir=tree.card_dir,
                                     worktree=tree.worktree, env=ENV)
        self.assertLessEqual(caretakerctx.total_bytes(files), caretakerctx.MAX_BYTES)
        self.assertTrue(any(f.cut for f in files))


class OutsideTest(unittest.TestCase):
    def test_symlinks_and_files_outside_the_card_and_worktree_are_skipped(self):
        tree = Tree(self)
        secret = os.path.join(tree.home, "tajne.md")
        os.makedirs(os.path.join(tree.worktree, "docs"))
        os.symlink(secret, os.path.join(tree.worktree, "docs", "agent-plan-standard.md"))
        os.symlink(secret, os.path.join(tree.worktree, "AGENTS.md"))
        os.symlink(secret, os.path.join(tree.step_dir, "out", "step.log"))
        os.symlink(secret, os.path.join(tree.step_dir, "out", "plan-review.json"))
        os.makedirs(os.path.join(tree.card_dir, "plan"))
        os.symlink(tree.home, os.path.join(tree.card_dir, "plan", "tickets"))
        os.symlink(secret, os.path.join(tree.card_dir, "plan", "plan.md"))
        os.makedirs(os.path.join(tree.home, "docs"))
        tree.write(os.path.join(tree.home, "docs", "sandbox-and-permissions.md"), LEAK)
        j = tree.journal()
        files = tree.collect(j)
        for path, text in files.items():
            self.assertNotIn(LEAK, text, path)
        self.assertIn("dowiązanie symboliczne", files["docs/agent-plan-standard.md"])
        self.assertIn("dowiązanie symboliczne", files["steps/K-1-PT-7-2/step-log.md"])
        self.assertNotIn("steps/K-1-PT-7-2/plan-review.json", files)

        # Katalog-dowiązanie w worktree: plik pod nim leży po `realpath` w $HOME.
        os.unlink(os.path.join(tree.worktree, "docs", "agent-plan-standard.md"))
        os.rmdir(os.path.join(tree.worktree, "docs"))
        os.symlink(os.path.join(tree.home, "docs"), os.path.join(tree.worktree, "docs"))
        files = tree.collect(j)
        self.assertNotIn(LEAK, files["docs/sandbox-and-permissions.md"])
        self.assertIn("spoza katalogu karty i worktree", files["docs/sandbox-and-permissions.md"])

    def test_step_dir_from_another_place_is_not_a_root(self):
        tree = Tree(self)
        j = tree.journal()
        self.assertEqual(set(caretakerctx.roots_for(caretaker.problems(j.st)[0], j.st, card_dir=tree.card_dir,
                                                    worktree=tree.worktree)),
                         {os.path.realpath(p) for p in (tree.card_dir, tree.worktree, tree.step_dir)})


class AttemptsTest(unittest.TestCase):
    def test_previous_caretaker_actions_and_diagnoses_are_in_the_next_context(self):
        tree = Tree(self)
        j = tree.journal()
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus")
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", reason="",
              outcome={"ok": True, "action": "escalate"})
        j.add(caretaker.ESCALATED, problem="error:K-1-PT-7-1", step="K-1-care-1", target="K-1-PT-7-2",
              ticket="PT-7", action="escalate", summary="Diagnoza: test eksportu pada na strefie czasowej.",
              recommendation="Zmień wykonawcę.", optionId="retry", failed=False, error="", code="")
        tree.write(os.path.join(tree.card_dir, "steps", "K-1-care-1", "note.md"), "Diagnoza z notki opiekuna.\n")
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="")
        j.failed_twice("K-1-PT-7-3", "K-1-PT-7-4", parent="K-1-PT-7-2")
        problem = caretaker.problems(j.st)[0]
        self.assertEqual(problem["id"], "error:K-1-PT-7-1")
        j.care("K-1-PT-7-4", "K-1-PT-7-1", 2, "K-1-care-2")
        attempts = tree.collect(j, problem, step="K-1-care-2")["attempts.md"]
        for part in ("Działanie 1: K-1-care-1", "Diagnoza: test eksportu pada na strefie czasowej.",
                     "Zmień wykonawcę.", "Diagnoza z notki opiekuna.", "K-1-PT-7-1", "K-1-PT-7-4",
                     "decyzja: retry"):
            self.assertIn(part, attempts)
        self.assertNotIn("K-1-care-2", attempts)


class RegistryTest(unittest.TestCase):
    def test_source_registered_for_one_trigger_appears_only_in_its_context(self):
        tree = Tree(self)
        j = tree.journal()

        def plan_format(ctx):
            return [caretakerctx.File("plan-format.md", "błędy sprawdzania formatu planu", text="BŁĘDY FORMATU\n")]

        extra = {"plan-format": [caretakerctx.Source("plan-format", plan_format)]}
        with mock.patch.dict(caretakerctx.SOURCES, extra):
            error = tree.collect(j)
            other = tree.collect(j, {"id": "plan-format:plan", "trigger": "plan-format", "triggers": ["plan-format"],
                                     "step": "", "root": "", "ticket": "", "stage": "plan"})
            joined = tree.collect(j, {**caretaker.problems(j.st)[0], "triggers": ["error", "plan-format"]})
        self.assertNotIn("plan-format.md", error)
        self.assertEqual(other["plan-format.md"], "BŁĘDY FORMATU\n")
        self.assertIn("plan-format.md", joined)
        self.assertIn("history.md", other)                  # źródła wspólne są przy każdym wyzwalaczu
        self.assertNotIn("plan-format", caretakerctx.SOURCES)

    def test_failing_source_gives_a_note_instead_of_blocking(self):
        tree = Tree(self)
        j = tree.journal()

        def broken(ctx):
            raise RuntimeError(f"zepsute źródło {TOKEN}")

        with mock.patch.dict(caretakerctx.SOURCES, {"error": [caretakerctx.Source("broken", broken)]}):
            files = tree.collect(j)
        self.assertIn("zepsute źródło", files["errors/broken.md"])
        self.assertNotIn(TOKEN, files["errors/broken.md"])


AGENTS_MD = "# Projekt\n\n## Zasady\n\nZasada.\n\n## Przepływ karty\n\nKROK-W-TLE-ZASADA\n\n## Inne\n\nNIE-TA-SEKCJA\n"


class ContextFlowTest(FlowInProcess):
    """Przebieg przez FlowEngine z atrapą `claude`: błąd kroku ticketu → krok opiekuna z kontekstem."""

    def setUp(self):
        super().setUp()
        for rel, text in (("docs/agent-plan-standard.md", "# Standard\n\nSTANDARD-PLANU-MIGAWKA\n"),
                          ("docs/sandbox-and-permissions.md", "# Piaskownica\n\nPIASKOWNICA-MIGAWKA\n"),
                          ("AGENTS.md", AGENTS_MD)):
            Tree.write(os.path.join(self.repo, rel), text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "docs")

    def test_context_files_brief_paths_and_masking(self):
        secrets = f"GITHUB_TOKEN={TOKEN} export TIDE_FAKE_SECRET_TOKEN={ENV_SECRET} password: {PAIR_SECRET} "
        card_id = self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                                        f"ATRAPA-POWOD: test pada {secrets}ATRAPA-OPIEKUN: escalate")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.escalated"), timeout=40), self.events(card_id))
        step = next(s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker")
        target = next(e for e in self.of(card_id, "caretaker.started"))["target"]
        context = os.path.join(step["stepDir"], "in", "context")
        found = {os.path.relpath(os.path.join(folder, name), context): os.path.join(folder, name)
                 for folder, _, names in os.walk(context) for name in names}
        expected = {"history.md", "journal.jsonl", "plan/README.md", "attempts.md", "docs/agent-plan-standard.md",
                    "docs/sandbox-and-permissions.md", "docs/AGENTS-przeplyw-karty.md",
                    f"steps/{target}/note.md", f"steps/{target}/result.json", f"steps/{target}/step-log.md",
                    f"steps/{target}/prompt.txt"}
        self.assertTrue(expected <= set(found), sorted(found))
        texts = {}
        for rel, path in found.items():
            with open(path, encoding="utf-8") as fh:
                texts[rel] = fh.read()
            for secret in SECRETS[:3]:
                self.assertNotIn(secret, texts[rel], rel)
        self.assertLessEqual(sum(len(t.encode("utf-8")) for t in texts.values()), caretakerctx.MAX_BYTES)
        self.assertIn("Test pada: test pada", texts[f"steps/{target}/step-log.md"])
        self.assertIn("STANDARD-PLANU-MIGAWKA", texts["docs/agent-plan-standard.md"])
        self.assertIn("PIASKOWNICA-MIGAWKA", texts["docs/sandbox-and-permissions.md"])
        self.assertIn("KROK-W-TLE-ZASADA", texts["docs/AGENTS-przeplyw-karty.md"])
        self.assertNotIn("NIE-TA-SEKCJA", texts["docs/AGENTS-przeplyw-karty.md"])
        self.assertIn("Zlecenie: karta wystartowała", texts["history.md"])
        for line in texts["journal.jsonl"].splitlines():
            json.loads(line)
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn("## Kontekst", prompt)
        for rel in expected:
            self.assertIn(os.path.join(context, rel), prompt)
        for secret in SECRETS[:3]:
            self.assertNotIn(secret, prompt)


if __name__ == "__main__":
    unittest.main()
