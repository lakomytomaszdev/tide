"""PT-111: tools/question_corpus.py — wiadomości z syntetycznych dzienników, przegląd, pomiar i zestaw prawdziwych wiadomości.

Dzienniki w testach są syntetyczne (katalog tymczasowy); prawdziwe dzienniki czyta tylko użytkownik, poza piaskownicą.
"""
import contextlib
import importlib.util
import io
import json
import os
import random
import tempfile
import unittest
from unittest import mock

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "tools", "question_corpus.py")
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "question-corpus.jsonl")
# Najniższa trafność obecnych reguł na zatwierdzonym zestawie (pełne punkty procentowe, w dół): wartość z
# `question_corpus.py bench` na zatwierdzonym zestawie. Kolejne tickety mogą ją tylko podnieść.
# Zestaw: 111 wiadomości (58 „czeka”, 53 „nie czeka”): 12 przypadków z 28.10 (`probe 2026-10-28`) i 99 z dzienników
# Claude Code tylko z własnych projektów (`extract --project plasma-tide/BO/LogTool/TimeTracker --drop-flagged`, bez
# treści klienta), reguły: 54% (w dół z dokładnego ilorazu). Etykiety: „czeka”, gdy wiadomość prosi użytkownika teraz
# o decyzję, odpowiedź albo działanie, od którego zależy dalsza praca (kliknięcie, polecenie, przegląd, ponowne
# zlecenie). „Nie czeka”: oferta warunkowa („jeśli chcesz…”), zadanie dopiero na później, zapowiedź pytania po wyniku
# polecenia, „zamknij i otwórz, żeby zobaczyć”, rada bez prośby („warto…”). Przegląd i etykiety zrobił koordynator
# (AI) na prośbę użytkownika (PT-111 r3); w r4 etykiety przejrzane ponownie według tej reguły (25 poprawek).
# None — zestaw jeszcze nie zatwierdzony (testy rozmiaru i progu są wtedy pomijane).
RULES_ACCURACY_FLOOR = 54


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

    def test_only_listed_projects_of_both_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = os.path.join(tmp, "claude")
            for project, text in (("-home-u-DevWorkspace-plasma-tide", "Własny?"),
                                  ("-home-u-DevWorkspace-plasma-tide-worktrees-pt-1-x", "Z worktree?"),
                                  ("-home-u-DevWorkspace-BO", "Z BO?"),
                                  ("-home-u-DevWorkspace-ROBOT", "Nie ten?"),
                                  ("-home-u-DevWorkspace-klient-repo", "Klienta?")):
                write_jsonl(os.path.join(claude, "projects", project, "s.jsonl"), [c_user("1"), c_text(text)])
            codex = os.path.join(tmp, "codex")
            for name, cwd, text in (("a", "/home/u/DevWorkspace/LogTool", "Codex własny?"),
                                    ("b", "/home/u/DevWorkspace/klient-repo", "Codex klienta?")):
                write_jsonl(os.path.join(codex, "sessions", "2026", "10", "31", f"rollout-{name}.jsonl"),
                            [{"type": "session_meta", "payload": {"cwd": cwd}}, x_event("task_started"),
                             x_event("task_complete", last_agent_message=text)])
            found = qc.collect(claude, codex, "2026-01-01", "2026-12-31", ["plasma-tide", "BO", "LogTool"])
            self.assertEqual(sorted(m["text"] for m in found),
                             ["Codex własny?", "Własny?", "Z BO?", "Z worktree?"])
            self.assertEqual(len(qc.collect(claude, codex, "2026-01-01", "2026-12-31")), 7)   # bez filtra — wszystkie


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

    def words(i, kind):   # różne treści, żeby nie były „prawie identyczne”
        rng = random.Random(f"{kind}{i}")
        return " ".join("".join(rng.choice("abcdefghijklmnoprstuwyz") for _ in range(rng.randint(4, 9))) for _ in range(14))

    for i in range(waits):
        sessions.append((f"w{i}", [c_user("x"), c_text(f"Raport {words(i, 'w')}.\n\nKtóry wariant {words(i, 'v')} wolisz?")]))
    for i in range(idle):
        sessions.append((f"i{i}", [c_user("x"), c_text(f"Gotowe, {words(i, 'i')} scalone i zainstalowane.")]))
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
            self.assertIn("„nie czeka” 37", err)   # 7 przypadków stałych + 30 z dzienników (limit --per-class)
            self.assertIn("może być w dziennikach większa", err)
            self.assertIn("szerszy zakres dat", err)
            self.assertIn("mniejszy zestaw", err)

    def test_near_identical_messages_count_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            same = [(f"s{i}", [c_user("x"), c_text(f"Krok {i + 20} kończę wynikiem `denied`: nie udało się usunąć plików, "
                                                   f"bo polecenie jest odrzucone. Czy mam spróbować inaczej?")])
                    for i in range(12)]
            code, out, err = self.run_extract(tmp, log_set(20, 20) + same)
            self.assertEqual(code, 0, err)
            texts = [i["text"] for i in read_json(out)["items"] if "kończę wynikiem" in i["text"]]
            self.assertEqual(len(texts), 1)

    def test_model_is_asked_only_until_class_limits_are_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = FakeModel()
            code, out, err = self.run_extract(tmp, log_set(60, 60), "--per-class", "20", model=lambda *a: model)
            self.assertEqual(code, 0, err)
            self.assertEqual(len(read_json(out)["items"]), 12 + 40)
            # po zapełnieniu obu limitów model nie dostaje reszty dzienników (razem byłoby 12 + 120 wywołań)
            self.assertLess(len(model.calls), 12 + 120)

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

    def test_drop_flagged_skips_client_content_without_using_the_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = [(f"k{i}", [c_user("x"), c_text(f"Repo coretools{i}infrastructure: który wariant {i} wolisz?")])
                      for i in range(10)]
            path = [("p", [c_user("x"), c_text("Plik /home/u/DevWorkspace/plasma-tide/x.py poprawiony. Scalić?")])]
            code, out, err = self.run_extract(tmp, client + path + log_set(20, 20), "--drop-flagged",
                                              "--per-class", "20")
            self.assertEqual(code, 0, err)
            texts = [i["text"] for i in read_json(out)["items"]]
            self.assertFalse(any("coretools" in t for t in texts))
            self.assertTrue(any("x.py poprawiony" in t for t in texts))   # sama ścieżka da się zastąpić przy --apply
            self.assertEqual(sum(1 for i in read_json(out)["items"] if not i["known"] and i["proposal"]), 20)

    def test_separate_limit_for_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_extract(tmp, log_set(40, 40), "--per-class", "15", "--per-waits", "30")
            self.assertEqual(code, 0, err)
            real = [i for i in read_json(out)["items"] if not i["known"]]
            self.assertEqual(sum(1 for i in real if i["proposal"]), 30)
            self.assertEqual(sum(1 for i in real if not i["proposal"]), 15)

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

    def test_redact_replaces_paths_and_company_names_in_any_spelling(self):
        text = "Plik /home/ktos/projekt/a.py i ~/x/y.md, klient Firma, FIRMA-a, Acme-Corp oraz acme corp."
        self.assertEqual(qc.redact(text), "Plik <ścieżka> i <ścieżka>, klient <firma>, <firma>, <firma> oraz <firma>.")
        self.assertEqual(qc.privacy_problems(qc.redact(text)), [])

    def test_redact_does_not_hide_emails_tokens_and_personal_numbers(self):
        for secret in ("jan@firma.pl", "token ghp_abcdefgh12345678", "tel. 601 234 567", "PESEL 44051401359",
                       "konto PL61 1090 1014 0000 0712 1981 2874"):
            self.assertNotEqual(qc.privacy_problems(qc.redact("Treść. " + secret)), [], secret)

    def test_apply_with_redaction_needs_reviewed_flag(self):
        data = candidates()
        data["items"][0]["text"] += "\nPlik /home/ktos/projekt/a.py dla Firma"
        md = qc.render_review(data)   # „przejrzane: nie”
        _, errors = qc.apply_review(md, redact_text=True)
        self.assertTrue(any("przejrzany" in e for e in errors))
        _, errors = qc.apply_review(md.replace("- przejrzane: nie", "- przejrzane: tak"))
        self.assertTrue(any("ścieżka" in e for e in errors))
        records, errors = qc.apply_review(md.replace("- przejrzane: nie", "- przejrzane: tak"), redact_text=True)
        self.assertEqual(errors, [])
        self.assertIn("Plik <ścieżka> dla <firma>", records[0]["text"])

    def test_no_way_around_the_reviewed_flag(self):
        self.assertNotIn("reviewed_note", qc.apply_review.__code__.co_varnames)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            qc.main(["review", "--apply", "x.md", "--out", "y.jsonl", "--reviewed-note", "ok"])

    def test_redaction_still_refuses_remaining_private_data(self):
        data = candidates()
        data["items"][0]["text"] += "\nPisz na jan@firma.pl, plik /home/ktos/a.py"
        md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
        errors = qc.apply_review(md, redact_text=True)[1]
        self.assertEqual(len(errors), 1)
        self.assertIn("adres e-mail", errors[0])

    def test_client_project_content_is_refused_and_not_redactable(self):
        for secret in ("repo infracore", "ticket DPT-5 nadal stoi", "w APP5 jest błąd",
                       "wiersz dpt-11-testy.fix-4.step/out/note.md", "id asst_sDfBnu0kDkEhKAFGftGCMiFi",
                       "stała `BUTTON_SCRIPT_DP4`", "klasa LogViewer", "repo LogViewer",
                       "GET /api/NecAdapters/IsActiveOnDevice/:id", "urządzenie TST4-000X"):
            self.assertNotEqual(qc.privacy_problems(qc.redact("Treść. " + secret)), [], secret)
        for fine in ("wersja DP45 nic nie znaczy", "plik udp4.c", "PT-111 i K-33 są w porządku"):
            self.assertEqual(qc.privacy_problems(fine), [], fine)

    def test_rules_accuracy_rounds_down_from_exact_ratio(self):
        idle = {"text": "Gotowe. Testy przechodzą.", "waits": False}
        wrong = {"text": "Gotowe. Testy przechodzą.", "waits": True}
        self.assertEqual(qc.rules_accuracy([idle] * 31 + [wrong] * 16), 65)   # 65,96%
        self.assertEqual(qc.rules_accuracy([]), 0)

    def test_drop_flagged_skips_messages_with_private_data_and_reports_ids_only(self):
        data = candidates(20, 30)
        data["items"][0]["text"] += "\nrepo infracore"
        data["items"][1]["text"] += "\nkrok dpt-11.fix-4.step"
        md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
        _, errors = qc.apply_review(md)
        self.assertEqual(len(errors), 2)
        dropped = []
        records, errors = qc.apply_review(md, drop_flagged=True, dropped=dropped)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 48)
        self.assertEqual(dropped, [data["items"][0]["id"], data["items"][1]["id"]])
        self.assertTrue(all(qc.privacy_problems(r["text"]) == [] for r in records))

    def test_drop_flagged_still_enforces_class_minimums(self):
        data = candidates(16, 30)
        for item in data["items"][:3]:
            item["text"] += "\nDPT-5"
        md = qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak")
        records, errors = qc.apply_review(md, drop_flagged=True)
        self.assertTrue(any("czeka 13" in e and "po 15" in e for e in errors))

    def test_result_file_names_alone_are_not_client_content(self):
        text = "Zapisałem `out/note.md` i `out/result.json` z wynikiem `done`."
        self.assertEqual(qc.privacy_problems(text), [])

    def test_cli_apply_with_redact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            md, corpus = os.path.join(tmp, "r.md"), os.path.join(tmp, "k.jsonl")
            data = candidates()
            data["items"][3]["text"] += "\nZob. /srv/klient/app.py (Firma)"
            with open(md, "w", encoding="utf-8") as fh:
                fh.write(qc.render_review(data).replace("- przejrzane: nie", "- przejrzane: tak"))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qc.main(["review", "--apply", md, "--out", corpus]), 1)
                self.assertEqual(qc.main(["review", "--apply", md, "--out", corpus, "--redact-paths"]), 0)
            rows = qc.load_corpus(corpus)
            self.assertIn("Zob. <ścieżka> (<firma>)", [r["text"] for r in rows][3])

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

    def test_bench_lists_each_probe_waits_case(self):
        corpus = [{"id": str(i), "text": t, "waits": w, "source": "s"} for i, (t, w) in enumerate(qc.PROBE_CASES)]

        class Silent(FakeModel):
            def judge(self, text, full=False):
                return False, 0.1   # model nigdy nie mówi „czeka”

        result = qc.bench(corpus, Silent())
        rows = result["probe_tail"]
        self.assertEqual(len(rows), 5)
        # przypadki z modelem w pętli, których model nie wykrył, są w wyniku jawnie przegapione
        for row in rows:
            self.assertEqual(row["detected"], row["rules"])
        self.assertIn("PRZEGAPIONA", qc.format_bench(result, "qwen3:8b"))

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
        if not os.path.exists(CORPUS):
            self.skipTest("brak pliku zestawu")
        self.corpus = qc.load_corpus(CORPUS)

    def approved_only(self):
        if RULES_ACCURACY_FLOOR is None:
            self.skipTest("zestaw jeszcze nie zatwierdzony przez użytkownika")

    def test_shape_and_size(self):
        self.assertEqual(len({m["id"] for m in self.corpus}), len(self.corpus))
        if RULES_ACCURACY_FLOOR is not None:
            self.assertGreaterEqual(len(self.corpus), qc.MIN_TOTAL)
        for m in self.corpus:
            self.assertEqual(set(m), {"id", "text", "waits", "source"})
            self.assertIsInstance(m["waits"], bool)
            self.assertTrue(m["text"].strip())
            self.assertLessEqual(len(m["text"]), qc.MAX_CHARS)
        waits = sum(m["waits"] for m in self.corpus)
        if RULES_ACCURACY_FLOOR is not None:
            self.assertGreaterEqual(waits, qc.MIN_PER_CLASS)
            self.assertGreaterEqual(len(self.corpus) - waits, qc.MIN_PER_CLASS)

    def test_no_near_duplicates(self):
        shapes = []
        for m in self.corpus:
            shape = qc.shape_of(m["text"])
            self.assertFalse(any(qc.near_duplicate(shape, other) for other in shapes), m["id"])
            shapes.append(shape)

    def test_contains_the_twelve_probe_cases(self):
        texts = {m["text"]: m["waits"] for m in self.corpus}
        for text, waits in qc.PROBE_CASES:
            self.assertIs(texts.get(text), waits, text[:60])

    def test_nothing_private(self):
        for m in self.corpus:
            self.assertEqual(qc.privacy_problems(m["text"]), [], m["id"])

    def test_rules_accuracy_does_not_drop(self):
        self.approved_only()
        self.assertGreaterEqual(qc.rules_accuracy(self.corpus), RULES_ACCURACY_FLOOR)

    def test_two_stage_with_oracle_model_is_not_worse_than_rules(self):
        # Atrapa modelu zwraca oznaczenia z zestawu: dwustopniowe liczenie nie może wypaść gorzej niż same reguły.
        rules = qc.score(self.corpus, [qc.rules_waits(m["text"]) for m in self.corpus])
        oracle = qc.score(self.corpus, [qc.combined_waits(m["text"], m["waits"]) for m in self.corpus])
        self.assertGreaterEqual(oracle["accuracy"], rules["accuracy"])
        self.assertLessEqual(oracle["false_waits"], rules["false_waits"])
        self.assertTrue(all(qc.combined_waits(m["text"], m["waits"]) for m in self.corpus
                            if m["waits"] and qc.question.doubtful(m["text"])))

    def test_every_waits_probe_case_is_seen_by_rules_or_sent_to_model(self):
        # Bez modelu: wiadomość „czeka” z 28.10 albo ma pytanie rozpoznane przez reguły, albo jest wątpliwa (idzie do
        # modelu). Inaczej żaden model jej nie uratuje. Kandydaci z #2 w zestawie mają źródło „claude 2026-11-03”
        # i nie da się ich odróżnić od reszty, więc to samo sprawdza ostatni test dla wszystkich „czeka” zestawu.
        for text, waits in qc.PROBE_CASES:
            if waits:
                self.assertTrue(qc.rules_waits(text) or qc.question.doubtful(text), text[:60])

    def test_every_waits_message_is_seen_by_rules_or_sent_to_model(self):
        # Zmierzone przy PT-113: 38 z 58 wiadomości „czeka” w zestawie nie ma pytania dla reguł i nie jest wątpliwych,
        # więc model ich nie zobaczy (sufit dwustopniowego liczenia). Test pilnuje, żeby ta liczba nie rosła.
        missed = [m["id"] for m in self.corpus
                  if m["waits"] and not (qc.rules_waits(m["text"]) or qc.question.doubtful(m["text"]))]
        self.assertLessEqual(len(missed), 38, missed)

    def test_doubtful_does_not_flag_obvious_cases(self):
        # Do modelu idą tylko wątpliwe końcówki; wiadomość z pytaniem rozpoznanym przez reguły nigdy.
        doubtful = [m for m in self.corpus if qc.question.doubtful(m["text"])]
        self.assertFalse(any(qc.rules_waits(m["text"]) for m in doubtful))
        self.assertLessEqual(len(doubtful), len(self.corpus) // 5)


if __name__ == "__main__":
    unittest.main()
