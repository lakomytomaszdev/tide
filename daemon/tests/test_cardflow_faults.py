"""PT-125: czyja wina błędu kroku (`fault`) z dziennika kroku, podpis błędu i wycinek bez sekretów.

Fixture'y w fixtures/step-logs/ to końcówki `out/step.log` kroku (Claude stream-json, Codex JSON) dla katalogu
wyjściowego `/wt/K-33-plan-2.step/out` — jak w scenariuszu B makiety K-35 (review planu bez wyniku).
"""
import json
import os
import unittest

from tide_daemon.cardflow import faults

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "fixtures", "step-logs")
OUT = "/wt/K-33-plan-2.step/out"
ENV = {"TIDE_SECRET_KEY": "wartosc-sekretna-z-env-123", "HOME": "/home/tester", "LANG": "pl_PL.UTF-8"}


def lines(name):
    return faults.read_log(os.path.join(LOGS, name))


def classify(name, reason="no-result", tool="claude"):
    return faults.classify(reason, lines(name), out_dir=OUT, tool=tool, log_path=os.path.join(LOGS, name))


class FaultFromStepLogTest(unittest.TestCase):
    def test_write_denial_without_result_is_tide(self):
        self.assertEqual(classify("write-denied.log"), {"fault": "tide", "cause": "write-denied"})
        self.assertEqual(classify("sandbox-read-only.log", tool="codex"), {"fault": "tide", "cause": "write-denied"},
                         "piaskownica Codexa: Read-only file system przy pliku wyniku")

    def test_limit_code_is_provider(self):
        self.assertEqual(classify("limit.log", reason="exit"), {"fault": "provider", "cause": "limit"})
        self.assertEqual(faults.classify("limit"), {"fault": "provider", "cause": "limit"})

    def test_provider_failure_other_than_limit(self):
        self.assertEqual(classify("auth.log", reason="exit"), {"fault": "provider", "cause": "auth"})
        self.assertEqual(classify("outage.log", reason="exit", tool="codex"), {"fault": "provider", "cause": "outage"})

    def test_unknown_when_the_log_does_not_decide(self):
        self.assertEqual(classify("unknown.log"), {"fault": "unknown", "cause": "no-result"},
                         "odmowa gdzie indziej i tekst agenta o odmowie nie są dowodem winy Tide")
        self.assertEqual(faults.classify("exit", []), {"fault": "unknown", "cause": "exit"})
        self.assertEqual(faults.classify("awaria testowa", []), {"fault": "unknown", "cause": "other"},
                         "swobodny powód agenta nie jest przyczyną — kod ze skończonego zbioru")

    def test_free_text_cause_from_old_journal_is_other(self):
        self.assertEqual(faults.of_finished({"reason": "x", "fault": "unknown", "faultCause": "curl z ghp_x nie"}),
                         {"fault": "unknown", "cause": "other"})
        self.assertEqual(faults.signature("ticket", "unknown", faults.classify("inny tekst agenta")["cause"]),
                         faults.signature("ticket", "unknown", faults.classify("jeszcze inny tekst")["cause"]),
                         "ta sama awaria z innym tekstem agenta ma ten sam podpis")

    def test_write_denial_needs_no_result_and_this_steps_out_dir(self):
        self.assertEqual(classify("write-denied.log", reason="bad-result")["fault"], "model",
                         "zły plik wyniku to nie odmowa zapisu")
        self.assertEqual(classify("write-denied.log", reason="exit")["fault"], "unknown")
        other = faults.classify("no-result", lines("write-denied.log"), out_dir="/wt/K-33-plan-3.step/out")
        self.assertEqual(other["fault"], "unknown", "odmowa w katalogu innego kroku")
        self.assertEqual(faults.classify("no-result", lines("write-denied.log"))["fault"], "unknown",
                         "bez katalogu kroku niczego nie zgadujemy")

    def test_reasons_set_by_tide_and_by_the_executor(self):
        self.assertEqual(faults.classify("start")["fault"], "tide")
        self.assertEqual(faults.classify("lost")["fault"], "tide")
        self.assertEqual(faults.classify("bad-review")["fault"], "model")
        self.assertEqual(faults.classify("abandoned")["fault"], "user")
        self.assertEqual(faults.classify("local-unavailable")["fault"], "provider")

    def test_finished_step_keeps_the_recorded_fault(self):
        self.assertEqual(faults.of_finished({"reason": "no-result", "fault": "tide", "faultCause": "write-denied"}),
                         {"fault": "tide", "cause": "write-denied"})
        self.assertEqual(faults.of_finished({"reason": "no-result"}), {"fault": "unknown", "cause": "no-result"},
                         "stary dziennik bez pola fault")


class SignatureAndExcerptTest(unittest.TestCase):
    def test_tool_inputs_mask_decoded_values_before_json_and_shortening(self):
        secret = "correct horse battery staple"
        inputs = [
            {"payload": f'password: "{secret}"', "status": "readable"},
            {"payload": [{"text": f"password: '{secret}'"}], "status": "readable"},
            {"password": secret, "status": "readable"},
            {"payload": {"secret": [secret, 123456789]}, "status": "readable"},
        ]
        for tool_input in inputs:
            events = [
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "mcp__demo", "input": tool_input}]}},
                {"type": "result", "permission_denials": [
                    {"tool_name": "mcp__demo", "tool_input": tool_input}]},
            ]
            for event in events:
                with self.subTest(tool_input=tool_input, event=event["type"]):
                    text = faults.excerpt([json.dumps(event)], env={})
                    for part in (*secret.split(), "123456789"):
                        self.assertNotIn(part, text)
                    self.assertIn(faults.MASK, text)
                    self.assertIn("mcp__demo", text)
                    self.assertIn("readable", text)

    def test_signature_is_kind_fault_and_cause(self):
        self.assertEqual(faults.signature("plan-review", "tide", "write-denied"), "plan-review/tide/write-denied")
        self.assertEqual(faults.signature("", "unknown", "Konflikt: nierozwiązany!"),
                         "krok/unknown/konflikt-nierozwi-zany")

    def test_excerpt_masks_tokens_and_environment_variables(self):
        text = faults.excerpt(lines("secrets.log"), env=ENV)
        for secret in ("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "sk-ant-api03-Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe",
                       "abcDEF123ghiJKL456mnoPQR789", "perm:dG9tZWs=", "xoxb-1234567890-abcdefghij",
                       "wartosc-sekretna-z-env-123", "/home/tester"):
            self.assertNotIn(secret, text)
        self.assertIn("GITHUB_TOKEN=" + faults.MASK, text)
        self.assertIn("ANTHROPIC_API_KEY=" + faults.MASK, text)
        self.assertIn("HOME=" + faults.MASK, text)
        self.assertIn("odmowa Write: /wt/K-33-plan-2.step/out/result.json", text, "zdarzenia po ludzku zostają")
        self.assertNotIn("```", text)

    def test_occurrence_masks_the_user_description_too(self):
        block = faults.occurrence(when="2026-11-03 23:02", card="K-33", step="K-33-plan-2", step_kind="plan-review",
                                  ticket="", executor="Haiku", attempt=1, result="error", reason="no-result",
                                  fault="tide", cause="write-denied",
                                  description="Po aktualizacji to samo.\nMój token: ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
                                  log_excerpt="[result] odmowa Write", env=ENV)
        self.assertIn("### 2026-11-03 23:02 · K-33 · review planu (Haiku)", block)
        self.assertIn("> Po aktualizacji to samo.", block)
        self.assertNotIn("ghp_A1b2", block)
        self.assertIn("```text\n[result] odmowa Write\n```", block)

    def test_reason_written_by_the_agent_is_masked_in_title_and_occurrence(self):
        reason = "curl z GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 nie zadziałał"
        block = faults.occurrence(when="2026-11-03 23:02", card="K-33", step="K-33-PT-1-2", step_kind="ticket",
                                  ticket="PT-1", executor="Sonnet", attempt=1, result="error", reason=reason,
                                  fault="unknown", cause=reason, description="opis", log_excerpt="", env=ENV)
        title = faults.card_title("ticket", "unknown", reason, env=ENV)
        body = faults.card_body(card="K-33", sig="ticket/unknown/other", happened=f"Krok: {reason}.", first=block,
                                env=ENV)
        for text in (block, title, body):
            self.assertNotIn("ghp_A1b2", text)
        self.assertIn("GITHUB_TOKEN=" + faults.MASK, block)

    def test_commit_hashes_and_plain_words_stay_readable(self):
        text = faults.mask("commit 4edbaeb1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7 w K-33-plan-2, wynik no-result", env={})
        self.assertIn("4edbaeb1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7", text)
        self.assertIn("K-33-plan-2", text)


if __name__ == "__main__":
    unittest.main()
