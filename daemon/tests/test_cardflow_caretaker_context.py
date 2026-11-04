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
from .test_cardflow import FlowInProcess
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

    def collect(self, j, problem=None, step="", env=ENV, tide_docs=""):
        problem = problem or caretaker.problems(j.st)[0]
        return {f.path: f.render() for f in caretakerctx.collect(problem, j.st, j.events, card="K-1",
                                                                 card_dir=self.card_dir,
                                                                 worktree=self.worktree, step=step, env=env,
                                                                 tide_docs=tide_docs)}


class MaskingTest(unittest.TestCase):
    def test_json_keys_and_collisions_are_masked_in_written_context(self):
        tree = Tree(self)
        j = tree.journal()
        secret = "correct horse battery staple"
        pairs = [(TOKEN, {"message": "token"}), (ENV_ONLY, {"message": "env"}),
                 (f'password: "{secret}"', {"message": "pair"}),
                 ("•••", {"message": "literal"}), ("••• [2]", {"message": "suffix"})]
        context = os.path.join(tree.step_dir, "in", "context")
        for order in (pairs, list(reversed(pairs))):
            detail = dict(order)
            j.add("step.note", step="K-1-PT-7-2", detail=detail)
            tree.write(os.path.join(tree.card_dir, "steps", "K-1-PT-7-2", "result.json"),
                       json.dumps({"result": "error", "detail": detail}))
            caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1",
                               card_dir=tree.card_dir, worktree=tree.worktree, context_dir=context,
                               env=ENV, tide_docs="")
            written = {}
            for folder, _, names in os.walk(context):
                for name in names:
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as fh:
                        written[os.path.relpath(path, context)] = fh.read()
            for path, text in written.items():
                with self.subTest(order=order[0][1], path=path):
                    for part in (TOKEN, ENV_ONLY, *secret.split()):
                        self.assertNotIn(part, text)
            journal = [json.loads(line) for line in written["journal.jsonl"].splitlines()]
            result = json.loads(written["steps/K-1-PT-7-2/result.json"])
            for source, data in (("journal", journal[-1]), ("result", result)):
                with self.subTest(order=order[0][1], source=source):
                    self.assertEqual(len(data["detail"]), len(detail), "kolizja nie usuwa pól")
                    self.assertEqual({v["message"] for v in data["detail"].values()},
                                     {"token", "env", "pair", "literal", "suffix"})
                    self.assertEqual(data["detail"]["••• [2]"]["message"], "suffix")

    def test_quoted_tool_input_secrets_are_masked_in_collect_and_build(self):
        tree = Tree(self)
        j = tree.journal()
        secret = "correct horse battery staple"
        tool_input = {"payload": f'password: "{secret}"',
                      "nested": [{"password": secret}], "status": "readable"}
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__demo", "input": tool_input}]}},
            {"type": "result", "permission_denials": [
                {"tool_name": "mcp__demo", "tool_input": tool_input}]},
        ]
        context = os.path.join(tree.step_dir, "in", "context")
        for event in events:
            tree.write(os.path.join(tree.step_dir, "out", "step.log"), json.dumps(event) + "\n")
            collected = tree.collect(j)
            caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1",
                               card_dir=tree.card_dir, worktree=tree.worktree, context_dir=context,
                               env=ENV, tide_docs="")
            written = {}
            for folder, _, names in os.walk(context):
                for name in names:
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as fh:
                        written[os.path.relpath(path, context)] = fh.read()
            for stage, files in (("collect", collected), ("build", written)):
                with self.subTest(event=event["type"], stage=stage):
                    for path, text in files.items():
                        for part in secret.split():
                            self.assertNotIn(part, text, path)
                    log = files["steps/K-1-PT-7-2/step-log.md"]
                    self.assertIn("•••", log)
                    self.assertIn("mcp__demo", log)
                    self.assertIn("readable", log)

    def test_json_values_are_decoded_before_masking_in_collect_and_build(self):
        tree = Tree(self)
        j = tree.journal()
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        secret = "correct horse battery staple"
        question = f'Potrzebne password: "{secret}"'
        result = json.dumps({"result": "question", "question": question})
        for name in ("K-1-PT-7-2", "K-1-care-1"):
            tree.write(os.path.join(tree.card_dir, "steps", name, "result.json"), result)
        tree.write(os.path.join(tree.step_dir, "out", "plan-review.json"),
                   json.dumps({"verdict": "REQUEST_CHANGES", "findings": [{"text": question}]}))
        collected = tree.collect(j)
        context = os.path.join(tree.step_dir, "in", "context")
        caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1",
                           card_dir=tree.card_dir, worktree=tree.worktree, context_dir=context,
                           env=ENV, tide_docs="")
        written = {}
        for folder, _, names in os.walk(context):
            for name in names:
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as fh:
                    written[os.path.relpath(path, context)] = fh.read()
        for stage, files in (("collect", collected), ("build", written)):
            with self.subTest(stage=stage):
                for path, text in files.items():
                    for part in secret.split():
                        self.assertNotIn(part, text, path)
                data = json.loads(files["steps/K-1-PT-7-2/result.json"])
                self.assertIn("•••", data["question"])
                review = json.loads(files["steps/K-1-PT-7-2/plan-review.json"])
                self.assertIn("•••", review["findings"][0]["text"])
                self.assertIn("#### result.json", files["attempts.md"])
                self.assertIn("•••", files["attempts.md"])

    def test_invalid_json_is_omitted_instead_of_masking_escaped_fragments(self):
        tree = Tree(self)
        j = tree.journal()
        tree.write(os.path.join(tree.card_dir, "steps", "K-1-PT-7-2", "result.json"),
                   json.dumps({"question": 'password: "correct horse battery staple"'})[:-1])
        text = tree.collect(j)["steps/K-1-PT-7-2/result.json"]
        self.assertIn("pominięto", text)
        self.assertNotIn("horse", text)

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

    def test_secret_fields_lose_whole_value_with_spaces_and_numbers(self):
        tree = Tree(self)
        j = tree.journal()
        j.add("step.note", step="K-1-PT-7-2", password="correct horse battery staple",
              detail={"apiKey": 123456789, "pin": 42, "count": 7}, secret=["raz dwa", 3.5])
        files = tree.collect(j)
        text = files["journal.jsonl"]
        for part in ("horse", "battery", "staple", "123456789", "raz dwa"):
            self.assertNotIn(part, text)
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["password"], "•••")
        self.assertEqual(event["detail"]["apiKey"], "•••")
        self.assertEqual(event["secret"], ["•••", "•••"])
        self.assertEqual(event["detail"]["count"], 7)               # zwykłe pole liczbowe zostaje

    def test_number_equal_to_a_secret_env_value_is_masked(self):
        tree = Tree(self)
        j = tree.journal()
        j.add("step.note", step="K-1-PT-7-2", code=987654321)
        files = tree.collect(j, env={**ENV, "TIDE_DB_PASSWORD": "987654321"})
        self.assertNotIn("987654321", files["journal.jsonl"])

    def test_tool_input_is_masked_before_shortening_in_collect_and_build(self):
        tree = Tree(self)
        j = tree.journal()
        tool_input = {"payload": "x" * 169 + " " + "ghp_" + "a" * 36}
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__demo", "input": tool_input}]}},
            {"type": "result", "permission_denials": [
                {"tool_name": "mcp__demo", "tool_input": tool_input}]},
        ]
        context = os.path.join(tree.step_dir, "in", "context")
        for event in events:
            tree.write(os.path.join(tree.step_dir, "out", "step.log"), json.dumps(event) + "\n")
            collected = tree.collect(j)
            caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1",
                               card_dir=tree.card_dir, worktree=tree.worktree, context_dir=context,
                               env=ENV, tide_docs="")
            written = {}
            for folder, _, names in os.walk(context):
                for name in names:
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8") as fh:
                        written[os.path.relpath(path, context)] = fh.read()
            for stage, files in (("collect", collected), ("build", written)):
                with self.subTest(event=event["type"], stage=stage):
                    for path, text in files.items():
                        self.assertNotIn("ghp_", text, path)
                    self.assertIn("mcp__demo", files["steps/K-1-PT-7-2/step-log.md"])
                    self.assertIn("•••", files["steps/K-1-PT-7-2/step-log.md"])

    def test_token_cut_by_the_log_tail_boundary_does_not_leak(self):
        tree = Tree(self)
        j = tree.journal()
        secret_tail = "a" * 19
        tree.write(os.path.join(tree.step_dir, "out", "step.log"),
                   "GITHUB_TOKEN=ghp_" + "a" * 36 + "\n" + "z" * (caretakerctx.LOG_TAIL_BYTES - 20) + "\n"
                   + "ostatnia linia\n")
        log = tree.collect(j)["steps/K-1-PT-7-2/step-log.md"]
        self.assertNotIn(secret_tail, log)
        self.assertIn("ostatnia linia", log)

    def test_token_cut_by_the_file_head_boundary_does_not_leak(self):
        # Granica `FILE_READ_MAX` wypada wewnątrz drugiego tokenu; pierwsze przypisanie kurczy się po maskowaniu,
        # więc całość mieści się w limicie i `_fit` niczego by już nie przyciął.
        tree = Tree(self)
        j = tree.journal()
        first = 'PASSWORD="' + "p" * (caretakerctx.FILE_READ_MAX - 14 - 12) + '"\n'
        self.assertEqual(len(first), caretakerctx.FILE_READ_MAX - 14)
        tree.write(os.path.join(tree.card_dir, "plan", "plan.md"), first + "ghp_" + "a" * 36 + "\n")
        leaked = "ghp_" + "a" * 10
        files = tree.collect(j)
        plan = files["plan/plan.md"]
        self.assertNotIn(leaked, plan)
        self.assertIn("PASSWORD=", plan)
        self.assertIn("dalsza część pliku pominięta", plan)
        context = os.path.join(tree.step_dir, "in", "context")
        caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1", card_dir=tree.card_dir,
                           worktree=tree.worktree, context_dir=context, env=ENV, tide_docs="")
        for folder, _, names in os.walk(context):
            for name in names:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    self.assertNotIn(leaked, fh.read(), name)

    def test_limited_head_read_drops_the_unfinished_last_line(self):
        tree = Tree(self)
        path = os.path.join(tree.card_dir, "plan", "plan.md")
        tree.write(path, "pierwsza\n" + "GITHUB_TOKEN=ghp_" + "b" * 36 + "\n")
        ctx = caretakerctx.Ctx(problem={}, st=None, card="K-1", card_dir=tree.card_dir, worktree=tree.worktree,
                               roots=(caretakerctx._root(tree.card_dir),))
        text = ctx.read_text(path, limit=20)
        self.assertEqual(text.splitlines()[0], "pierwsza")
        self.assertNotIn("ghp_", text)


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
            tree.write(os.path.join(tree.card_dir, "plan", name), ("plan " * 20 + "\n") * 15_000)
        files = caretakerctx.collect(caretaker.problems(j.st)[0], j.st, j.events, card="K-1", card_dir=tree.card_dir,
                                     worktree=tree.worktree, env=ENV)
        self.assertLessEqual(caretakerctx.total_bytes(files), caretakerctx.MAX_BYTES)
        self.assertTrue(any(f.cut for f in files))

    def test_many_files_stay_under_the_hard_limit_with_one_note(self):
        files = [caretakerctx.File(f"plan/tickets/t-{i:05d}.md", "kartka", text="k" * 1000) for i in range(6000)]
        caretakerctx._fit(files)
        self.assertLessEqual(caretakerctx.total_bytes(files), caretakerctx.MAX_BYTES)
        self.assertGreater(caretakerctx.total_bytes(files), caretakerctx.MAX_BYTES * 9 // 10)
        notes = [f for f in files if f.path == caretakerctx.DROPPED_PATH]
        self.assertEqual(len(notes), 1)
        self.assertIn("pominięto", notes[0].text)
        self.assertFalse(any(f.text == caretakerctx.CUT_NOTE.format(kb=400) for f in files))

    def test_many_files_through_collect_and_build_stay_under_the_limit(self):
        tree = Tree(self)
        j = tree.journal()
        for i in range(600):
            tree.write(os.path.join(tree.card_dir, "plan", "tickets", f"pt-{i:04d}.md"), "t" * 1000)
        context = os.path.join(tree.step_dir, "in", "context")
        caretakerctx.build(caretaker.problems(j.st)[0], j.st, j.events, card="K-1", card_dir=tree.card_dir,
                           worktree=tree.worktree, context_dir=context, env=ENV, tide_docs="")
        size = sum(os.path.getsize(os.path.join(folder, name))
                   for folder, _, names in os.walk(context) for name in names)
        self.assertLessEqual(size, caretakerctx.MAX_BYTES)


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
                         {caretakerctx._root(p) for p in (tree.card_dir, tree.worktree, tree.step_dir)})

    def test_step_dir_replaced_by_a_symlink_is_not_a_root(self):
        """Cały katalog kroku z dziennika podmieniony na dowiązanie do katalogu spoza karty (np. `$HOME`)."""
        tree = Tree(self)
        fake = os.path.join(tree.home, "fake-step")
        tree.write(os.path.join(fake, "out", "step.log"), LEAK + "\n")
        tree.write(os.path.join(fake, "out", "plan-review.json"), json.dumps({"x": LEAK}))
        tree.write(os.path.join(fake, "in", "prompt.txt"), LEAK + "\n")
        shutil.rmtree(tree.step_dir)
        os.symlink(fake, tree.step_dir)
        j = tree.journal()
        roots = caretakerctx.roots_for(caretaker.problems(j.st)[0], j.st, card_dir=tree.card_dir,
                                       worktree=tree.worktree)
        self.assertNotIn(os.path.normpath(tree.step_dir), {root.path for root in roots})
        self.assertNotIn(os.path.realpath(fake), {root.path for root in roots})
        files = tree.collect(j)
        for path, text in files.items():
            self.assertNotIn(LEAK, text, path)
        self.assertIn("spoza katalogu karty i worktree", files["steps/K-1-PT-7-2/step-log.md"])

    def test_worktree_replaced_by_a_symlink_is_not_read(self):
        tree = Tree(self)
        tree.write(os.path.join(tree.home, "repo", "AGENTS.md"), f"## Przepływ karty\n\n{LEAK}\n")
        shutil.rmtree(tree.worktree)
        os.symlink(os.path.join(tree.home, "repo"), tree.worktree)
        j = tree.journal()
        files = tree.collect(j)
        for path, text in files.items():
            self.assertNotIn(LEAK, text, path)


class AttemptsTest(unittest.TestCase):
    def test_extra_problem_notes_from_result_and_journal_are_in_context(self):
        tree = Tree(self)
        j = tree.journal()
        name = "K-1-PT-7-2"
        j.events[-1].update(notes=["journal-note.md"], kept=["kept-note.md", "diagnosis.md"])
        tree.write(os.path.join(tree.card_dir, "steps", name, "result.json"),
                   json.dumps({"result": "error", "notes": ["diagnosis.md"]}))
        for entry in ("diagnosis.md", "journal-note.md", "kept-note.md"):
            tree.write(os.path.join(tree.card_dir, "steps", name, entry),
                       f"Ustalenia {entry}: password={PAIR_SECRET}\n")
        files = tree.collect(j)
        for entry in ("diagnosis.md", "journal-note.md", "kept-note.md"):
            text = files[f"steps/{name}/{entry}"]
            self.assertIn(f"Ustalenia {entry}", text)
            self.assertNotIn(PAIR_SECRET, text)

    def test_extra_caretaker_diagnoses_are_in_next_attempt(self):
        tree = Tree(self)
        j = tree.journal()
        name = "K-1-care-1"
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, name)
        j.add("step.finished", step=name, result="done", kept=["journal-diagnosis.md"])
        tree.write(os.path.join(tree.card_dir, "steps", name, "result.json"),
                   json.dumps({"result": "done", "notes": ["diagnosis.md"]}))
        for entry in ("diagnosis.md", "journal-diagnosis.md"):
            tree.write(os.path.join(tree.card_dir, "steps", name, entry),
                       f"Diagnoza {entry}: password={PAIR_SECRET}\n")
        attempts = tree.collect(j, step="K-1-care-2")["attempts.md"]
        for entry in ("diagnosis.md", "journal-diagnosis.md"):
            self.assertIn(f"Diagnoza {entry}", attempts)
        self.assertNotIn(PAIR_SECRET, attempts)

    def test_extra_notes_cannot_escape_archive_or_follow_symlinks(self):
        tree = Tree(self)
        j = tree.journal()
        secret = os.path.join(tree.home, "tajne.md")
        for name in ("K-1-PT-7-2", "K-1-care-1"):
            tree.write(os.path.join(tree.card_dir, "steps", name, "result.json"), json.dumps({
                "notes": [secret, "../../../home/tajne.md", "../other/diagnosis.md", "link.md", 42]}))
            os.symlink(secret, os.path.join(tree.card_dir, "steps", name, "link.md"))
        tree.write(os.path.join(tree.card_dir, "steps", "other", "diagnosis.md"), LEAK)
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        for path, text in tree.collect(j).items():
            self.assertNotIn(LEAK, text, path)

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
TIDE_DOCS = (("docs/agent-plan-standard.md", "# Standard\n\nSTANDARD-PLANU-MIGAWKA\n"),
             ("docs/sandbox-and-permissions.md", "# Piaskownica\n\nPIASKOWNICA-MIGAWKA\n"),
             ("AGENTS.md", AGENTS_MD))


class TideDocsTest(unittest.TestCase):
    def test_project_without_tide_docs_gets_the_snapshots_of_the_running_tide(self):
        tree = Tree(self)
        docs = os.path.join(tree.base, "tide", "daemon", "tide-docs")
        for rel, text in TIDE_DOCS:
            tree.write(os.path.join(docs, rel), text)
        j = tree.journal()
        files = tree.collect(j, tide_docs=docs)
        self.assertIn("STANDARD-PLANU-MIGAWKA", files["docs/agent-plan-standard.md"])
        self.assertIn("PIASKOWNICA-MIGAWKA", files["docs/sandbox-and-permissions.md"])
        self.assertIn("KROK-W-TLE-ZASADA", files["docs/AGENTS-przeplyw-karty.md"])
        self.assertNotIn("NIE-TA-SEKCJA", files["docs/AGENTS-przeplyw-karty.md"])
        for text in files.values():
            self.assertNotIn("(pominięto: brak pliku)", text)

    def test_tide_docs_dir_is_not_a_root_for_other_sources(self):
        tree = Tree(self)
        docs = os.path.join(tree.base, "tide-docs")
        tree.write(os.path.join(docs, "docs", "agent-plan-standard.md"), "# Standard\n")
        tree.write(os.path.join(docs, "inne.md"), LEAK)
        seen = {}

        def probe(ctx):
            seen["text"] = ctx.read_text(os.path.join(docs, "inne.md"))
            return []

        j = tree.journal()
        with mock.patch.dict(caretakerctx.SOURCES, {"error": [caretakerctx.Source("probe", probe)]}):
            tree.collect(j, tide_docs=docs)
        self.assertIsNone(seen["text"])

    def test_default_docs_dir_is_the_tide_repository_when_running_from_source(self):
        base = caretakerctx.tide_docs_dir()
        self.assertTrue(base)
        with open(os.path.join(base, "docs", "agent-plan-standard.md"), encoding="utf-8") as fh:
            self.assertIn("Standard planu", fh.readline())


class ContextFlowTest(FlowInProcess):
    """Przebieg przez FlowEngine z atrapą `claude`: błąd kroku ticketu → krok opiekuna z kontekstem. Repozytorium
    projektu nie ma dokumentów Tide — migawki pochodzą z dokumentów działającej usługi (tu: repozytorium Tide)."""

    def test_context_files_brief_paths_and_masking(self):
        self.assertFalse(os.path.exists(os.path.join(self.repo, "docs", "agent-plan-standard.md")))
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
        self.assertIn("# Standard planu pracy agentów (Tide)", texts["docs/agent-plan-standard.md"])
        self.assertIn("# Piaskownica, uprawnienia", texts["docs/sandbox-and-permissions.md"])
        self.assertTrue(texts["docs/AGENTS-przeplyw-karty.md"].startswith("## Przepływ karty\n"))
        for rel in ("docs/agent-plan-standard.md", "docs/sandbox-and-permissions.md", "docs/AGENTS-przeplyw-karty.md"):
            self.assertNotIn("(pominięto:", texts[rel][:200], rel)
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
