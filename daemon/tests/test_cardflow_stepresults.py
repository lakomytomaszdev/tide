"""PT-462: funkcje odczytu wyniku kroku (stepresults.py) na plikach tymczasowych, bez FlowEngine."""
import json
import os
import tempfile
import unittest

from tide_daemon.cardflow import engine, stepresults


class FileCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def write(self, data, name="result.json"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def missing(self):
        return os.path.join(self.dir, "none.json")


BAD = {"result": "error", "reason": "bad-result"}


class StepResultTest(FileCase):
    def test_missing_file(self):
        self.assertEqual(stepresults.step_result(self.missing()), {"result": "error", "reason": "no-result"})

    def test_too_large(self):
        path = self.write(json.dumps({"result": "done", "pad": "x" * stepresults.RESULT_MAX}))
        self.assertEqual(stepresults.step_result(path), BAD)

    def test_not_json_and_not_dict_and_unknown_result(self):
        self.assertEqual(stepresults.step_result(self.write("{nie json")), BAD)
        self.assertEqual(stepresults.step_result(self.write("[1]")), BAD)
        self.assertEqual(stepresults.step_result(self.write({"result": "maybe"})), BAD)

    def test_question_without_text(self):
        for question in (None, "", "  ", 5):
            self.assertEqual(stepresults.step_result(self.write({"result": "question", "question": question})), BAD)

    def test_denied_and_outside_need_single_line_command(self):
        for kind in ("denied", "outside"):
            for command in (None, "", "  ", 3, "a\nb", "a\rb", "a\0b"):
                self.assertEqual(stepresults.step_result(self.write({"result": kind, "command": command})), BAD)

    def test_done_defaults(self):
        self.assertEqual(stepresults.step_result(self.write({"result": "done", "notes": ["a.md", 3, "b.md"]})), {
            "result": "done", "reason": "", "exclusive": False, "question": "", "command": "", "options": [],
            "baseProposal": None, "waitFor": "", "notes": ["a.md", "b.md"]})

    def test_outside_is_normalized_to_denied(self):
        out = stepresults.step_result(self.write({
            "result": "outside", "command": "  make test  ", "reason": "r" * 700, "exclusive": True,
            "notes": [f"n{i}" for i in range(30)], "question": "ignorowane", "options": [{"title": "a"}]}))
        self.assertEqual(out, {"result": "denied", "outside": True, "reason": "r" * 600, "question": "",
                               "command": "make test", "exclusive": True, "notes": [f"n{i}" for i in range(20)]})

    def test_exclusive_only_for_denied_and_bad_type_is_false(self):
        self.assertTrue(stepresults.step_result(self.write(
            {"result": "denied", "command": "make", "exclusive": True}))["exclusive"])
        self.assertFalse(stepresults.step_result(self.write(
            {"result": "denied", "command": "make", "exclusive": "tak"}))["exclusive"])
        self.assertFalse(stepresults.step_result(self.write({"result": "done", "exclusive": True}))["exclusive"])
        self.assertFalse(stepresults.step_result(self.write(
            {"result": "outside", "command": "make", "exclusive": "tak"}))["exclusive"])

    def test_denied_keeps_command_untrimmed_and_truncates_reason(self):
        out = stepresults.step_result(self.write({"result": "denied", "command": " make ", "reason": "r" * 400}))
        self.assertEqual((out["command"], out["reason"], out["outside"] if "outside" in out else None),
                         (" make ", "r" * 300, None))

    def test_question_truncation_options_base_proposal(self):
        proposal = {"ref": "refs/heads/card", "evidence": " dowód "}
        out = stepresults.step_result(self.write({
            "result": "question", "question": "q" * 2500, "options": [{"title": "Tak", "recommended": True}],
            "baseProposal": proposal, "waitFor": "  PT-12  "}))
        self.assertEqual(out["question"], "q" * 2000)
        self.assertEqual(out["options"], [{"title": "Tak", "effect": "", "recommended": True}])
        self.assertEqual(out["baseProposal"], {"ref": "refs/heads/card", "evidence": "dowód"})
        self.assertEqual(out["waitFor"], "PT-12")

    def test_question_with_bad_options_gives_empty_list_and_bad_base_proposal_gives_error(self):
        out = stepresults.step_result(self.write({
            "result": "question", "question": "q", "options": "nie lista", "baseProposal": "x"}))
        self.assertEqual(out["options"], [])
        self.assertIn("error", out["baseProposal"])

    def test_options_and_base_proposal_only_for_question(self):
        out = stepresults.step_result(self.write({
            "result": "error", "reason": "e", "options": [{"title": "a"}],
            "baseProposal": {"ref": "refs/heads/x", "evidence": "d"}}))
        self.assertEqual((out["options"], out["baseProposal"]), ([], None))

    def test_wait_for_only_for_question_and_error_and_only_strings(self):
        self.assertEqual(stepresults.step_result(self.write({"result": "error", "waitFor": "K-43"}))["waitFor"], "K-43")
        self.assertEqual(stepresults.step_result(self.write({"result": "error", "waitFor": 5}))["waitFor"], "")
        self.assertEqual(stepresults.step_result(self.write({"result": "done", "waitFor": "PT-1"}))["waitFor"], "")
        self.assertEqual(stepresults.step_result(self.write(
            {"result": "denied", "command": "x", "waitFor": "PT-1"}))["waitFor"], "")
        self.assertEqual(stepresults.step_result(self.write({"result": "error", "waitFor": "W" * 50}))["waitFor"],
                         "W" * 32)

    def test_non_list_notes_are_ignored(self):
        self.assertEqual(stepresults.step_result(self.write({"result": "done", "notes": "note.md"}))["notes"], [])

    def test_symlink_is_followed_in_step_result(self):
        target = self.write({"result": "done"}, "real.json")
        link = os.path.join(self.dir, "link.json")
        os.symlink(target, link)
        self.assertEqual(stepresults.step_result(link)["result"], "done")


class ReviewResultTest(FileCase):
    def review(self, **fields):
        data = {"result": "done", "verdict": "APPROVE", "blockers": 0, "majors": 0, "nits": 2}
        data.update(fields)
        return stepresults.review_result(self.write(data))

    def test_valid(self):
        self.assertEqual(self.review(), {"verdict": "APPROVE", "blockers": 0, "majors": 0, "nits": 2})
        self.assertEqual(self.review(verdict="REQUEST_CHANGES", majors=1),
                         {"verdict": "REQUEST_CHANGES", "blockers": 0, "majors": 1, "nits": 2})
        self.assertEqual(self.review(nits=999)["nits"], 999)

    def test_missing_not_json_not_dict_not_done(self):
        self.assertIsNone(stepresults.review_result(os.path.join(self.dir, "none.json")))
        self.assertIsNone(stepresults.review_result(self.write("{")))
        self.assertIsNone(stepresults.review_result(self.write("[]")))
        self.assertIsNone(self.review(result="error"))

    def test_bad_verdict(self):
        self.assertIsNone(self.review(verdict="MAYBE"))
        self.assertIsNone(self.review(verdict=None))

    def test_verdict_inconsistent_with_counts(self):
        self.assertIsNone(self.review(verdict="APPROVE", blockers=1))
        self.assertIsNone(self.review(verdict="APPROVE", majors=1))
        self.assertIsNone(self.review(verdict="REQUEST_CHANGES"))

    def test_bad_counts(self):
        for value in (True, False, -1, 1000, 1.0, "0", None):
            for key in ("blockers", "majors", "nits"):
                self.assertIsNone(self.review(**{key: value}), (key, value))
        self.assertIsNone(stepresults.review_result(self.write(
            {"result": "done", "verdict": "APPROVE", "blockers": 0, "majors": 0})))

    def test_no_size_limit_and_symlink_followed(self):
        target = self.write({"result": "done", "verdict": "APPROVE", "blockers": 0, "majors": 0, "nits": 0}, "real.json")
        link = os.path.join(self.dir, "link.json")
        os.symlink(target, link)
        self.assertEqual(stepresults.review_result(link)["verdict"], "APPROVE")


class RawResultTest(FileCase):
    def test_object_and_missing(self):
        self.assertEqual(stepresults.raw_result(self.write({"a": 1})), {"a": 1})
        self.assertEqual(stepresults.raw_result(self.write("[1, 2]")), [1, 2])
        self.assertIsNone(stepresults.raw_result(self.missing()))

    def test_bad_inputs_give_empty_string(self):
        self.assertEqual(stepresults.raw_result(self.write("{nie json")), "")
        self.assertEqual(stepresults.raw_result(self.write("x" * (stepresults.RESULT_MAX + 1))), "")
        path = os.path.join(self.dir, "bytes.json")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\x00")
        self.assertEqual(stepresults.raw_result(path), "")

    def test_symlink_is_rejected(self):
        target = self.write({"a": 1}, "real.json")
        link = os.path.join(self.dir, "link.json")
        os.symlink(target, link)
        self.assertEqual(stepresults.raw_result(link), "")

    def test_directory_gives_empty_string(self):
        self.assertEqual(stepresults.raw_result(self.dir), "")


class ClaudeDeniedCommandTest(FileCase):
    def log(self, *events):
        return self.write("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n", "step.log")

    @staticmethod
    def result(*denials):
        return {"type": "result", "permission_denials": list(denials)}

    @staticmethod
    def bash(command):
        return {"tool_name": "Bash", "tool_input": {"command": command}}

    def test_command_is_returned_trimmed(self):
        self.assertEqual(stepresults.claude_denied_command(self.log(self.result(self.bash("  make test  ")))),
                         "make test")

    def test_missing_file(self):
        self.assertEqual(stepresults.claude_denied_command(self.missing()), "")

    def test_skips_non_json_other_events_and_other_tools(self):
        path = self.log('"permission_denials" nie json', {"type": "assistant", "permission_denials": [self.bash("a")]},
                        self.result({"tool_name": "Edit", "tool_input": {"command": "b"}}, "x", self.bash("c")))
        self.assertEqual(stepresults.claude_denied_command(path), "c")

    def test_multiline_command_is_skipped_for_next_valid_one(self):
        for bad in ("a\nb", "a\rb", "a\0b", "  ", "", 5):
            path = self.log(self.result(self.bash(bad), self.bash("ok")))
            self.assertEqual(stepresults.claude_denied_command(path), "ok", repr(bad))
        self.assertEqual(stepresults.claude_denied_command(self.log(self.result(self.bash("a\nb")))), "")

    def test_bad_tool_input_shapes(self):
        path = self.log(self.result({"tool_name": "Bash", "tool_input": "x"}, {"tool_name": "Bash"}))
        self.assertEqual(stepresults.claude_denied_command(path), "")

    def test_line_longer_than_limit_is_ignored(self):
        long_line = json.dumps(self.result(self.bash("make " + "x" * stepresults.RESULT_MAX)))
        self.assertEqual(stepresults.claude_denied_command(self.log(long_line)), "")

    def test_invalid_utf8_is_replaced_not_fatal(self):
        path = os.path.join(self.dir, "step.log")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\n" + json.dumps(self.result(self.bash("make"))).encode() + b"\n")
        self.assertEqual(stepresults.claude_denied_command(path), "make")


class EngineDelegationTest(FileCase):
    def test_constants_are_shared(self):
        self.assertIs(engine.RESULTS, stepresults.RESULTS)
        self.assertEqual(stepresults.RESULT_MAX, engine.OUTPUT_MAX)

    def test_wrappers_are_static_and_delegate(self):
        path = self.write({"result": "done"})
        self.assertEqual(engine.FlowEngine._result(path), stepresults.step_result(path))
        self.assertEqual(engine.FlowEngine._raw_result(path), {"result": "done"})
        self.assertIsNone(engine.FlowEngine._review_result(path))
        self.assertEqual(engine.FlowEngine._claude_denied_command(self.missing()), "")


if __name__ == "__main__":
    unittest.main()
