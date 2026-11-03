"""PT-111: tools/question_corpus.py — wiadomości z syntetycznych dzienników, przegląd, pomiar i zestaw prawdziwych wiadomości.

Dzienniki w testach są syntetyczne (katalog tymczasowy); prawdziwe dzienniki czyta tylko użytkownik, poza piaskownicą.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "tools", "question_corpus.py")
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "question-corpus.jsonl")
# Najniższa trafność obecnych reguł na zatwierdzonym zestawie (pełne punkty procentowe, w dół): wartość z
# `question_corpus.py bench`. Kolejne tickety mogą ją tylko podnieść. None — zestaw jeszcze nie zatwierdzony.
RULES_ACCURACY_FLOOR = None


def load_tool():
    spec = importlib.util.spec_from_file_location("question_corpus", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qc = load_tool()


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def c_user(text, ts="2026-10-30T10:00:00.000Z", **extra):
    return {"type": "user", "isSidechain": False, "timestamp": ts, "message": {"role": "user", "content": text}, **extra}


def c_assistant(blocks, stop="end_turn", ts="2026-10-30T10:01:00.000Z", **extra):
    return {"type": "assistant", "isSidechain": False, "timestamp": ts,
            "message": {"role": "assistant", "stop_reason": stop, "content": blocks}, **extra}


def c_text(text, **kw):
    return c_assistant([{"type": "text", "text": text}], **kw)


def c_tool(name="Bash"):
    return c_assistant([{"type": "tool_use", "id": "t1", "name": name, "input": {}}], stop="tool_use")


def c_result():
    return {"type": "user", "isSidechain": False, "timestamp": "2026-10-30T10:02:00.000Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def x_event(kind, ts="2026-10-31T09:00:00.000Z", **payload):
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": kind, **payload}}


def x_item(kind, ts="2026-10-31T09:00:01.000Z", **payload):
    return {"timestamp": ts, "type": "response_item", "payload": {"type": kind, **payload}}


def x_say(text):
    return x_item("message", role="assistant", content=[{"type": "output_text", "text": text}])


class ClaudeMessagesTest(unittest.TestCase):
    def read(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            write_jsonl(path, records)
            return qc.claude_messages(path)

    def test_last_message_of_each_finished_turn(self):
        out = self.read([c_user("a"), c_text("Pierwsza odpowiedź."), c_user("b"), c_tool(), c_result(),
                         c_text("Czy scalić?", ts="2026-10-31T08:00:00.000Z")])
        self.assertEqual([(m["text"], m["date"]) for m in out],
                         [("Pierwsza odpowiedź.", "2026-10-30"), ("Czy scalić?", "2026-10-31")])

    def test_turn_ending_with_tool_call_has_no_final_message(self):
        # Tekst przed narzędziem na końcu tury nie jest wiadomością końcową (usługa też czyści wtedy pytanie).
        out = self.read([c_user("a"), c_text("Zaraz sprawdzę?", stop="tool_use"), c_tool()])
        self.assertEqual(out, [])
        out = self.read([c_user("a"), c_text("Sprawdzam.", stop="tool_use"), c_tool(), c_result()])
        self.assertEqual(out, [])

    def test_text_before_tool_is_replaced_by_the_final_one(self):
        out = self.read([c_user("a"), c_text("Sprawdzam.", stop="tool_use"), c_tool(), c_result(), c_text("Gotowe.")])
        self.assertEqual([m["text"] for m in out], ["Gotowe."])

    def test_interrupted_sidechain_and_synthetic_entries_are_skipped(self):
        out = self.read([c_user("a"), c_text("Pytanie?"), c_user("[Request interrupted by user]"),
                         c_user("b"), {**c_text("Podagent."), "isSidechain": True},
                         c_user("<system-reminder>x</system-reminder>"), c_user("c", isMeta=True), c_text("Koniec.")])
        self.assertEqual([m["text"] for m in out], ["Koniec."])

    def test_unfinished_message_is_skipped(self):
        self.assertEqual(self.read([c_user("a"), c_text("W trakcie", stop=None)]), [])

    def test_long_message_is_clipped_from_the_start(self):
        text = "x" * 7000 + " Czy scalić?"
        out = self.read([c_user("a"), c_text(text)])
        self.assertEqual(len(out[0]["text"]), qc.MAX_CHARS)
        self.assertEqual(out[0]["text"], text[-qc.MAX_CHARS:])
        self.assertTrue(out[0]["text"].endswith("Czy scalić?"))


class CodexMessagesTest(unittest.TestCase):
    def read(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollout-2026-10-31T09-00-00-0000000a-0000-4000-8000-000000000001.jsonl")
            write_jsonl(path, [{"timestamp": "2026-10-31T09:00:00.000Z", "type": "session_meta",
                                "payload": {"id": "x", "source": "cli"}}] + records)
            return qc.codex_messages(path)

    def test_message_from_task_complete(self):
        out = self.read([x_event("task_started"), x_say("Pośrednio."), x_event("task_complete", last_agent_message="Wolisz A czy B?")])
        self.assertEqual([(m["text"], m["date"]) for m in out], [("Wolisz A czy B?", "2026-10-31")])

    def test_last_text_when_task_complete_has_no_message(self):
        out = self.read([x_event("task_started"), x_say("Gotowe."), x_event("task_complete")])
        self.assertEqual([m["text"] for m in out], ["Gotowe."])

    def test_turn_ending_with_tool_call_and_aborted_turn_have_no_message(self):
        out = self.read([x_event("task_started"), x_say("Zaraz uruchomię."), x_item("function_call", name="shell", call_id="c1"),
                         x_event("task_complete"),
                         x_event("task_started"), x_say("Pytanie?"), x_event("turn_aborted")])
        self.assertEqual(out, [])

    def test_subagent_log_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollout-x.jsonl")
            write_jsonl(path, [{"type": "session_meta", "payload": {"source": {"subagent": {"other": "guardian"}}}},
                               x_event("task_started"), x_event("task_complete", last_agent_message="Ocena.")])
            self.assertEqual(qc.codex_messages(path), [])

    def test_long_message_is_clipped_from_the_start(self):
        text = "y" * 9000 + " Daj znać."
        out = self.read([x_event("task_started"), x_event("task_complete", last_agent_message=text)])
        self.assertEqual(out[0]["text"], text[-qc.MAX_CHARS:])


def make_home(tmp, claude=(), codex=()):
    """Katalogi ~/.claude i ~/.codex z syntetycznymi dziennikami: claude — [(nazwa, rekordy)], codex — j.w."""
    for name, records in claude:
        write_jsonl(os.path.join(tmp, "claude", "projects", "-p", name + ".jsonl"), records)
    for name, records in codex:
        write_jsonl(os.path.join(tmp, "codex", "sessions", "2026", "10", "31", f"rollout-{name}.jsonl"), records)
    return os.path.join(tmp, "claude"), os.path.join(tmp, "codex")


class CollectTest(unittest.TestCase):
    def test_both_tools_date_range_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude, codex = make_home(
                tmp,
                claude=[("a", [c_user("1"), c_text("Raz?"), c_user("2"), c_text("Dwa.", ts="2026-09-01T10:00:00.000Z")]),
                        ("b", [c_user("1"), c_text("Raz?")])],
                codex=[("c", [x_event("task_started"), x_event("task_complete", last_agent_message="Trzy?")])])
            found = qc.collect(claude, codex, "2026-10-01", "2026-11-30")
            self.assertEqual(sorted((m["text"], m["source"]) for m in found),
                             [("Raz?", "claude 2026-10-30"), ("Trzy?", "codex 2026-10-31")])
            self.assertEqual(found[0]["source"], "codex 2026-10-31")   # najnowsze pierwsze

    def test_subagent_logs_of_claude_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(os.path.join(tmp, "claude", "projects", "-p", "s", "subagents", "agent-1.jsonl"),
                        [c_user("1"), c_text("Z podagenta.")])
            self.assertEqual(qc.collect(os.path.join(tmp, "claude"), os.path.join(tmp, "codex"), "2026-01-01", "2026-12-31"), [])


class LabelsTest(unittest.TestCase):
    def test_rules_label_follows_ending_question(self):
        self.assertTrue(qc.rules_waits("Zrobione.\n\nKtóry wariant wolisz?"))
        self.assertFalse(qc.rules_waits("Gotowe. Testy przechodzą."))

    def test_combined_uses_model_only_for_doubtful_endings(self):
        doubtful = "Jasne.\n\n**Krok 1:** uruchom ten select i wklej mi wynik:"
        self.assertTrue(qc.question.doubtful(doubtful))
        self.assertTrue(qc.combined_waits(doubtful, True))
        self.assertFalse(qc.combined_waits(doubtful, False))
        self.assertFalse(qc.combined_waits(doubtful, None))
        self.assertTrue(qc.combined_waits("Który wariant wolisz?", False))   # reguły widzą pytanie — model nie decyduje
        self.assertFalse(qc.combined_waits("Gotowe.", True))

    def test_ollama_must_be_local(self):
        with self.assertRaises(qc.UsageError):
            qc.Ollama("http://przyklad.example:11434")
        self.assertEqual(qc.Ollama("http://127.0.0.1:11434/").url, "http://127.0.0.1:11434")

    def test_probe_cases_are_the_twelve_from_28_10(self):
        self.assertEqual(len(qc.PROBE_CASES), 12)
        self.assertEqual(sum(w for _, w in qc.PROBE_CASES), 5)


class FakeModel:
    """Podstawka Ollamy: „czeka”, gdy tekst zawiera „?”."""
    def __init__(self, url=None, model=None):
        self.model = model
        self.calls = []

    def available(self):
        return ""

    def judge(self, text, full=False):
        self.calls.append((text, full))
        return "?" in text, 0.5


class NoModel(FakeModel):
    def available(self):
        return "Ollama nie odpowiada pod http://localhost:11434 (testowa)"


def log_set(waits, idle):
    """Dzienniki Claude Code z `waits` pytaniami i `idle` raportami (każda wiadomość w osobnej sesji)."""
    sessions = []
    for i in range(waits):
        sessions.append((f"w{i}", [c_user("x"), c_text(f"Raport numer {i}.\n\nKtóry wariant numer {i} wolisz?")]))
    for i in range(idle):
        sessions.append((f"i{i}", [c_user("x"), c_text(f"Gotowe, zadanie numer {i} scalone i zainstalowane.")]))
    return sessions


class ExtractTest(unittest.TestCase):
    def run_extract(self, tmp, sessions, *extra, model=FakeModel):
        claude, codex = make_home(tmp, claude=sessions)
        out = os.path.join(tmp, "out", "candidates.json")
        argv = ["extract", "--out", out, "--claude", claude, "--codex", codex, "--since", "2026-10-01",
                "--until", "2026-11-30", *extra]
        err = io.StringIO()
        with mock.patch.object(qc, "Ollama", model), contextlib.redirect_stderr(err):
            code = qc.main(argv)
        return code, out, err.getvalue()

    def test_writes_candidates_with_probe_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_extract(tmp, log_set(20, 20))
            self.assertEqual(code, 0, err)
            items = read_json(out)["items"]
            self.assertEqual(len(items), 12 + 40)
            self.assertEqual(sum(1 for i in items if i["source"] == qc.PROBE_SOURCE), 12)
            self.assertTrue(all(i["id"].startswith("m") and i["text"] for i in items))
            real = [i for i in items if i["source"].startswith("claude")]
            self.assertTrue(all(i["rules"] == ("?" in i["text"]) and i["model"] == ("?" in i["text"]) for i in real))

    def test_too_few_in_one_class_asks_with_numbers_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 12 przypadków stałych to 5 „czeka” i 7 „nie czeka”; z dzienników tylko 3 „czeka”.
            code, out, err = self.run_extract(tmp, log_set(3, 40))
            self.assertEqual(code, 3)
            self.assertFalse(os.path.exists(out))
            self.assertIn("„czeka” 8", err)
            self.assertIn("„nie czeka” 47", err)   # 7 przypadków stałych + 40 z dzienników (limit na klasę nie fałszuje liczb)
            self.assertIn("szerszy zakres dat", err)
            self.assertIn("mniejszy zestaw", err)

    def test_candidates_from_note_are_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = os.path.join(tmp, "note.json")
            write_json(note, [{"text": "Kandydat z notki: wybierz A albo B?", "source": "notka PT-110"},
                              {"text": "Inny kandydat.", "waits": False}])
            code, out, err = self.run_extract(tmp, log_set(20, 20), "--candidates", note)
            self.assertEqual(code, 0, err)
            items = {i["text"]: i for i in read_json(out)["items"]}
            self.assertEqual(items["Kandydat z notki: wybierz A albo B?"]["source"], "notka PT-110")
            self.assertTrue(items["Kandydat z notki: wybierz A albo B?"]["proposal"])
            self.assertIs(items["Inny kandydat."]["proposal"], False)

    def test_without_ollama_proposals_come_from_rules_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_extract(tmp, log_set(20, 20), model=NoModel)
            self.assertEqual(code, 0, err)
            self.assertIn("tylko z reguł", err)
            items = read_json(out)["items"]
            self.assertTrue(all(i["model"] is None for i in items))

    def test_output_inside_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude, codex = make_home(tmp)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = qc.main(["extract", "--out", os.path.join(qc.ROOT, "candidates.json"), "--claude", claude, "--codex", codex])
            self.assertEqual(code, 2)
            self.assertFalse(os.path.exists(os.path.join(qc.ROOT, "candidates.json")))


def candidates(waits=20, idle=30):
    items = []
    for i in range(waits + idle):
        w = i < waits
        text = f"Wiadomość {i}. Który wariant wolisz?" if w else f"Wiadomość {i}. Gotowe."
        items.append({"id": qc.message_id(text), "text": text, "source": "claude 2026-10-30", "rules": w, "model": w,
                      "proposal": w, "known": False})
    return {"model": "qwen3:8b", "items": items}


class ReviewTest(unittest.TestCase):
    def test_markdown_has_message_label_and_remove_field(self):
        md = qc.render_review(candidates(1, 0))
        self.assertIn("- przejrzane: nie", md)
        self.assertIn("- czeka: tak", md)
        self.assertIn("- usuń: nie", md)
        self.assertIn("> Wiadomość 0. Który wariant wolisz?", md)
        self.assertIn("reguły tak, model tak", md)

    def test_apply_requires_reviewed_flag(self):
        md = qc.render_review(candidates())
        _, errors = qc.apply_review(md)
        self.assertTrue(any("przejrzany" in e for e in errors))
        records, errors = qc.apply_review(md.replace("- przejrzane: nie", "- przejrzane: tak"))
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 50)

    def test_removed_messages_and_changed_labels(self):
        md = qc.render_review(candidates()).replace("- przejrzane: nie", "- przejrzane: tak")
        first = qc.message_id("Wiadomość 0. Który wariant wolisz?")
        second = qc.message_id("Wiadomość 1. Który wariant wolisz?")
        head, rest = md.split(f"## {first}", 1)
        rest = rest.replace("- usuń: nie", "- usuń: tak", 1)
        md = head + f"## {first}" + rest
        head, rest = md.split(f"## {second}", 1)
        md = head + f"## {second}" + rest.replace("- czeka: tak", "- czeka: nie", 1)
        records, errors = qc.apply_review(md)
        self.assertEqual(errors, [])
        by_id = {r["id"]: r for r in records}
        self.assertNotIn(first, by_id)
        self.assertIs(by_id[second]["waits"], False)
        self.assertEqual(len(records), 49)

    def test_apply_refuses_paths_tokens_and_customer_names(self):
        for secret in ("Zobacz /home/ktos/projekt/plik.py", "Napisz do jan@firma.pl", "token ghp_abcdefgh12345678",
                       "klient Firma prosi o zmianę", "klucz " + "a1" * 20):
            data = candidates()
            data["items"][0]["text"] += "\n" + secret
            md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
            self.assertIn("UWAGA prywatność", md, secret)
            records, errors = qc.apply_review(md)
            self.assertTrue(any(data["items"][0]["id"] in e and "linia tekstu 2" in e for e in errors), (secret, errors))

    def test_text_edited_in_markdown_passes_after_redaction(self):
        data = candidates()
        data["items"][0]["text"] += "\nPlik /home/ktos/projekt/a.py"
        md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
        md = md.replace("/home/ktos/projekt/a.py", "<ścieżka>")
        records, errors = qc.apply_review(md)
        self.assertEqual(errors, [])
        self.assertIn("Plik <ścieżka>", records[0]["text"])

    def test_apply_refuses_unlabelled_and_too_small_sets(self):
        md = qc.render_review(candidates()).replace("- przejrzane: nie", "- przejrzane: tak")
        _, errors = qc.apply_review(md.replace("- czeka: tak", "- czeka: ?", 1))
        self.assertTrue(any("pole „czeka”" in e for e in errors))
        _, errors = qc.apply_review(qc.render_review(candidates(10, 40)).replace("- przejrzane: nie", "- przejrzane: tak"))
        self.assertTrue(any("czeka 10" in e and "po 15" in e for e in errors))

    def test_multiline_text_with_fences_and_blank_lines_roundtrips(self):
        data = candidates()
        text = "Akapit.\n\n```sql\nSELECT 1;\n```\n\n- punkt\n\nKtóry wariant wolisz?"
        data["items"][0]["text"] = text
        md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
        records, errors = qc.apply_review(md)
        self.assertEqual(errors, [])
        self.assertEqual(records[0]["text"], text)

    def test_cli_review_then_apply_writes_corpus_with_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand, md, corpus = (os.path.join(tmp, n) for n in ("c.json", "r.md", "corpus.jsonl"))
            write_json(cand, candidates())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(qc.main(["review", cand, "--out", md]), 0)
                self.assertEqual(qc.main(["review", "--apply", md, "--out", corpus]), 1)   # jeszcze nieprzejrzany
                self.assertFalse(os.path.exists(corpus))
                with open(md, encoding="utf-8") as fh:
                    text = fh.read().replace("- przejrzane: nie", "- przejrzane: tak")
                with open(md, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.assertEqual(qc.main(["review", "--apply", md, "--out", corpus]), 0)
            rows = qc.load_corpus(corpus)
            self.assertEqual(len(rows), 50)
            self.assertEqual(set(rows[0]), {"id", "text", "waits", "source"})

    def test_review_output_inside_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = os.path.join(tmp, "c.json")
            write_json(cand, candidates())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qc.main(["review", cand, "--out", os.path.join(qc.ROOT, "review.md")]), 2)
            self.assertFalse(os.path.exists(os.path.join(qc.ROOT, "review.md")))


class BenchTest(unittest.TestCase):
    CORPUS = [{"id": "a", "text": "Który wariant wolisz?", "waits": True, "source": "s"},
              {"id": "b", "text": "Gotowe.", "waits": False, "source": "s"},
              {"id": "c", "text": "Czy warto? Raczej nie.", "waits": False, "source": "s"},
              {"id": "d", "text": "Uruchom ten select i wklej mi wynik.", "waits": True, "source": "s"}]

    def test_score_counts_false_waits_and_false_idle(self):
        result = qc.score(self.CORPUS, [True, True, False, None], [1.0, 3.0, 2.0, 2.0])
        self.assertEqual((result["right"], result["accuracy"]), (2, 50.0))
        self.assertEqual((result["false_waits"], result["false_idle"], result["errors"]), (1, 0, 1))
        self.assertEqual((result["mean_s"], result["max_s"]), (2.0, 3.0))

    def test_bench_runs_both_inputs_and_reports_times(self):
        model = FakeModel()
        result = qc.bench(self.CORPUS, model)
        self.assertEqual(result["rules"]["n"], 4)
        for key in ("model_tail", "model_full", "combined_tail", "combined_full"):
            self.assertIn(key, result)
        self.assertEqual(result["model_tail"]["mean_s"], 0.5)
        self.assertEqual(sum(1 for _, full in model.calls if full), 4 + 0)   # 4 wiadomości w wariancie „cała wiadomość”
        text = qc.format_bench(result, "qwen3:8b")
        self.assertIn("model qwen3:8b, końcówka", text)
        self.assertIn("cała wiadomość", text)

    def test_model_tail_is_shorter_than_full_message(self):
        long_text = "z " * 3000 + "Który wariant wolisz?"
        with mock.patch.object(qc.Ollama, "_post", return_value={"response": '{"waits": true}'}) as post:
            model = qc.Ollama("http://localhost:11434")
            model.judge(long_text)
            model.judge(long_text, full=True)
        tail_prompt, full_prompt = (c.args[1]["prompt"] for c in post.call_args_list)
        self.assertLess(len(tail_prompt), len(full_prompt))
        self.assertTrue(full_prompt.endswith(long_text))
        self.assertFalse(post.call_args_list[0].args[1]["think"])

    def test_bench_without_ollama_exits_nonzero_with_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k.jsonl")
            write_jsonl(path, self.CORPUS)
            err = io.StringIO()
            with mock.patch.object(qc, "Ollama", NoModel), contextlib.redirect_stderr(err):
                code = qc.main(["bench", path])
            self.assertEqual(code, 4)
            self.assertIn("Ollama nie odpowiada", err.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(qc.main(["bench", path, "--rules-only"]), 0)
            self.assertIn("reguły", out.getvalue())

    def test_unreachable_ollama_reports_reason(self):
        problem = qc.Ollama("http://127.0.0.1:9").available()   # port zamknięty
        self.assertIn("Ollama nie odpowiada", problem)


class RealCorpusTest(unittest.TestCase):
    """Zestaw prawdziwych wiadomości zatwierdzony przez użytkownika (daemon/tests/fixtures/question-corpus.jsonl)."""

    def setUp(self):
        if RULES_ACCURACY_FLOOR is None or not os.path.exists(CORPUS):
            self.skipTest("zestaw prawdziwych wiadomości jeszcze nie zatwierdzony")
        self.corpus = qc.load_corpus(CORPUS)

    def test_shape_and_size(self):
        self.assertGreaterEqual(len(self.corpus), qc.MIN_TOTAL)
        self.assertEqual(len({m["id"] for m in self.corpus}), len(self.corpus))
        for m in self.corpus:
            self.assertEqual(set(m), {"id", "text", "waits", "source"})
            self.assertIsInstance(m["waits"], bool)
            self.assertTrue(m["text"].strip())
            self.assertLessEqual(len(m["text"]), qc.MAX_CHARS)
        waits = sum(m["waits"] for m in self.corpus)
        self.assertGreaterEqual(waits, qc.MIN_PER_CLASS)
        self.assertGreaterEqual(len(self.corpus) - waits, qc.MIN_PER_CLASS)

    def test_contains_the_twelve_probe_cases(self):
        texts = {m["text"]: m["waits"] for m in self.corpus}
        for text, waits in qc.PROBE_CASES:
            self.assertIs(texts.get(text), waits, text[:60])

    def test_nothing_private(self):
        for m in self.corpus:
            self.assertEqual(qc.privacy_problems(m["text"]), [], m["id"])

    def test_rules_accuracy_does_not_drop(self):
        self.assertGreaterEqual(qc.rules_accuracy(self.corpus), RULES_ACCURACY_FLOOR)


if __name__ == "__main__":
    unittest.main()
