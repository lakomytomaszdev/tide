"""Opis kart tablicy koordynacji (coordination/describe.py): reguły (formułki poleceń, stan i review słowami),
walidacja odpowiedzi modelu, fakty bez sekretów i bez całych poleceń, pamięć podręczna i odstępy między
wywołaniami — model to zawsze atrapa (nigdy prawdziwe `claude`)."""
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from tide_daemon.coordination import describe
from tide_daemon.models import config as models_config
from tide_daemon.coordination.board import STATE_WORDS
from tide_daemon.reports import standup, summarize

from .test_models_core import OLLAMA_CHAT, closed_port, start_server

BRIEF_05 = ("Implementujesz zadanie 05 funkcji „Wartość w logu” w LogViewerze (.NET 10, Blazor Server, ES 7.17): "
            "kolumnę przebiegu wartości w wynikach. Wykonujesz tylko to zadanie. **Gdzie pracujesz** - Worktree: x")


def ticket(key="~t", state="gotowe", note="", **extra):
    t = {"key": key, "id": "", "state": state, "note": note, "word": STATE_WORDS[state], "source": "runs", "planState": "",
         "officialTitle": "", "officialFrom": "", "officialNote": "", "taskTitle": "Implement thing", "brief": "",
         "description": "", "planNote": "", "review": None, "question": None, "agents": [], "events": [],
         "runs": [], "inPlan": False, "lastActivityTs": 100.0, "title": "Implement thing"}
    t.update(extra)
    return t


def board(tickets, cid="claude:s", live=True, last=1000.0, runs=()):
    return {"id": cid, "project": "demo", "title": "Koordynator", "live": live, "lastActivityTs": last,
            "tickets": tickets, "runs": list(runs)}


class RulesTest(unittest.TestCase):
    def test_title_from_brief_formulas(self):
        self.assertEqual(describe.title_from_brief(BRIEF_05), "Wartość w logu: kolumnę przebiegu wartości w wynikach")
        self.assertEqual(describe.title_from_brief(
            "Jesteś wykonawcą zadania 01 funkcji „Wartość w logu” w repo LogViewer. Rola: `x`. Model: opus."),
            "Wartość w logu — zadanie 01")
        self.assertEqual(describe.title_from_brief(
            "Mała poprawka testu w repo LogViewer. Rola: `fix`. Model: sonnet (jednoznaczna zmiana testu)."),
            "Mała poprawka testu w repo LogViewer")
        self.assertEqual(describe.title_from_brief(
            "Jesteś wykonawcą zadania `anon-key-sync` w repo LogViewer. Rola: `impl`. Model: opus."), "",
            "same formułki — nic do pokazania")
        self.assertEqual(describe.title_from_brief(
            "Ticket LOG-232 / 08 (feature „Wartość w logu”). Implement the acceptance fixes."), "",
            "angielskie polecenie z polską nazwą w cudzysłowie to nie polski opis")
        self.assertEqual(describe.title_from_brief("Repo: /home/x. Read-only research, do not query ES."), "")

    def test_title_prefers_plan_tracker_backlog_then_brief_then_task(self):
        self.assertEqual(describe.rules_title(ticket(officialTitle="Eksport do CSV", brief=BRIEF_05)), "Eksport do CSV")
        self.assertEqual(describe.rules_title(ticket(brief=BRIEF_05)), "Wartość w logu: kolumnę przebiegu wartości w wynikach")
        self.assertEqual(describe.rules_title(ticket(taskTitle="/08: implement acceptance fixes")),
                         "08: implement acceptance fixes")
        self.assertEqual(describe.rules_title(ticket(taskTitle="", id="AB-1")), "AB-1")

    def test_now_says_state_and_review_in_words(self):
        nits = {"blocker": 0, "major": 0, "nit": 9, "ok": True}
        major = {"blocker": 0, "major": 1, "nit": 2, "ok": False}
        cases = [
            (ticket(note="zmiany po review, bez ponownego review", review=nits),
             "Review: 9 drobnych uwag; poprawki wprowadzone, nie przejrzane ponownie"),
            (ticket(note="review OK", review=nits), "Review OK: 9 drobnych uwag, bez błędów blokujących"),
            (ticket(note="review OK", review={"blocker": 0, "major": 0, "nit": 0, "ok": True}), "Review bez uwag"),
            (ticket(state="w-toku", note="po review: 1 MAJOR, 2 NIT — do poprawy", review=major),
             "Review: 1 poważna uwaga i 2 drobne — do poprawy"),
            (ticket(state="review", note="gotowe, czeka na review"), "Gotowe, czeka na review"),
            (ticket(note="bez review"), "Gotowe, bez review"),
            (ticket(note="rozpoznanie skończone"), "Rozpoznanie skończone"),
            (ticket(state="w-toku", note="przerwany — koordynator już nie działa"),
             "Przerwane w trakcie — koordynator już nie działa"),
            (ticket(state="todo", inPlan=True), "Jeszcze nie zaczęte — w planie"),
            (ticket(state="czeka", question={"text": "AB-1: usunąć tabelę?"}), "Czeka na ciebie: „AB-1: usunąć tabelę?”"),
            (ticket(state="review", agents=[{"state": "running", "role": "reviewer", "word": "ponowne review"},
                                            {"state": "running", "role": "implementer", "word": "poprawki"}]),
             "Trwa ponowne review po poprawkach; równolegle poprawki"),
            (ticket(state="w-toku", events=[{"kind": "finished", "state": "done", "word": "implementacja",
                                             "text": "Gotowe. 2 testy nie przechodzą: X i Y."}]),
             "W toku. 2 testy nie przechodzą: X i Y."),
        ]
        for t, expected in cases:
            self.assertEqual(describe.rules_now(t), expected, t["note"] or t["state"])

    def test_done_is_the_last_implementation_report(self):
        events = [{"kind": "finished", "state": "done", "word": "implementacja", "text": "Kolumna działa, testy przechodzą."},
                  {"kind": "finished", "state": "attention", "word": "review", "review": {}, "text": "do poprawy"},
                  {"kind": "finished", "state": "done", "word": "poprawki", "text": "Poprawki zrobione."}]
        self.assertEqual(describe.rules_done(ticket(events=events)), "Kolumna działa, testy przechodzą.")
        self.assertEqual(describe.rules_done(ticket(events=events[1:])), "Poprawki zrobione.")
        self.assertEqual(describe.rules_done(ticket()), "")


class FactsAndValidationTest(unittest.TestCase):
    def test_facts_strip_secrets_and_keep_only_heads(self):
        secret = "sk-ant-" + "a" * 40
        t = ticket(brief=BRIEF_05 + " token=" + secret + " " + "x" * 2000, runs=["r1"],
                   events=[{"kind": "finished", "state": "done", "word": "implementacja",
                            "text": "Zrobione w /home/user/repo, klucz " + secret}])
        facts = describe.ticket_facts(t, {"r1": {"description": "Implement ticket 05 value chain"}}, home="/home/user")
        text = json.dumps(facts, ensure_ascii=False)
        self.assertNotIn(secret, text)
        self.assertNotIn("/home/user", text)
        self.assertLessEqual(len(facts["brief"]), 400)
        self.assertEqual(facts["tasks"], ["Implement ticket 05 value chain"])
        self.assertEqual(facts["reports"][0]["role"], "implementacja")
        self.assertNotIn("title", facts, "fakty nie zawierają opisu z describe (inaczej skrót by się kręcił)")

    def test_hash_depends_on_facts_and_model(self):
        facts = describe.ticket_facts(ticket(), {})
        self.assertEqual(describe.facts_hash(facts, "sonnet"), describe.facts_hash(dict(facts), "sonnet"))
        self.assertNotEqual(describe.facts_hash(facts, "sonnet"), describe.facts_hash(facts, "haiku"))
        self.assertNotEqual(describe.facts_hash(facts, "sonnet"),
                            describe.facts_hash(dict(facts, state="w-toku"), "sonnet"))

    def test_validate_only_known_tickets_strings_and_limits(self):
        answer = {"tickets": [
            {"key": "~a", "title": "**Kolumna** przebiegu `wartości`", "done": "x" * 900, "now": "Trwa review"},
            {"key": "~b", "title": "Wymyślony", "done": "", "now": ""},
            {"key": "~c", "title": 5, "done": "d", "now": "n"},
            {"key": "~d", "title": "Tytuł", "done": ["lista"], "now": "brak"},
            {"key": "~a", "title": "Drugi raz", "done": "", "now": ""},
            "śmieć",
        ]}
        valid = describe.validate(answer, {"~a", "~c", "~d"})
        self.assertEqual(set(valid), {"~a", "~d"}, "tylko znane tickety z tytułem-napisem, pierwszy wpis wygrywa")
        self.assertEqual(valid["~a"]["title"], "Kolumna przebiegu wartości")
        self.assertLessEqual(len(valid["~a"]["done"]), describe.DONE_MAX)
        self.assertEqual(valid["~d"], {"title": "Tytuł", "done": "", "now": ""}, "„brak” i nie-napisy → pusty napis")
        for bad in ({}, {"tickets": "x"}, {"tickets": [{"key": "~zz", "title": "x"}]}, []):
            with self.assertRaises(standup.Invalid):
                describe.validate(bad, {"~a"})

    def test_markdown_lists_in_fields(self):
        # PT-13: gpt-oss wkładał listy w Markdownie do jednego pola.
        valid = describe.validate({"tickets": [{"key": "~a", "title": "- **Kolumna** przebiegu\n- Druga rzecz",
                                                "done": "- Dodano kolumnę\n- Testy przechodzą",
                                                "now": "**Teraz:**\nTrwa review"}]}, {"~a"})
        self.assertEqual(valid["~a"], {"title": "Kolumna przebiegu", "done": "Dodano kolumnę. Testy przechodzą",
                                       "now": "Teraz: Trwa review"})


class FakeModel:
    def __init__(self, answer=None, fail=False):
        self.payloads = []
        self.answer = answer
        self.fail = fail

    def __call__(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise summarize.Failed("atrapa: brak programu")
        if self.answer is not None:
            return self.answer
        return json.dumps({"tickets": [{"key": t["key"], "title": "Opis " + t["key"], "done": "Zrobione " + t["key"],
                                        "now": "Teraz " + t["key"]} for t in payload["tickets"]]})


class DescriberTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-describe-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cache = os.path.join(self.tmp, "cache", "tide", describe.CACHE_NAME)
        self.now = 1000.0

    def describer(self, model=None, mode="claude", **kw):
        return describe.Describer(mode=mode, model="sonnet", cache_file=self.cache, clock=lambda: self.now,
                                  call=model or FakeModel(), log=lambda text: None, **kw)

    def test_rules_mode_never_calls_the_model(self):
        model = FakeModel()
        d = self.describer(model, mode="rules")
        boards = [board([ticket(brief=BRIEF_05)])]
        d.decorate(boards)
        self.now += 10_000
        d.decorate(boards)
        self.assertIsNone(d.due(boards))
        self.assertEqual(model.payloads, [])
        t = boards[0]["tickets"][0]
        self.assertEqual((t["title"], t["describedBy"]), ("Wartość w logu: kolumnę przebiegu wartości w wynikach", "rules"))

    def test_settle_gap_cache_and_stale_descriptions(self):
        model = FakeModel()
        d = self.describer(model)
        boards = [board([ticket("~a"), ticket("~b", state="w-toku")])]
        d.decorate(boards)
        self.assertIsNone(d.due(boards), "fakty świeżo zmienione — czekamy SETTLE")
        self.now += describe.SETTLE
        d.decorate(boards)
        job = d.due(boards)
        self.assertEqual({t["key"] for t in job["tickets"]}, {"~a", "~b"})
        self.assertTrue(d.run(job))
        d.decorate(boards)
        a = boards[0]["tickets"][0]
        self.assertEqual((a["title"], a["done"], a["now"], a["describedBy"]), ("Opis ~a", "Zrobione ~a", "Teraz ~a", "model"))
        self.assertEqual(stat.S_IMODE(os.stat(self.cache).st_mode), 0o600)

        # Zmiana faktów jednego ticketu: tytuł i „zrobione” z poprzedniego opisu, „teraz” z reguł, bez wywołania
        # przed SETTLE i przed GAP od ostatniego wywołania koordynatora.
        boards = [board([ticket("~a"), ticket("~b", state="review", note="gotowe, czeka na review")])]
        d.decorate(boards)
        b = boards[0]["tickets"][1]
        self.assertEqual((b["title"], b["done"], b["now"], b["describedBy"]),
                         ("Opis ~b", "Zrobione ~b", "Gotowe, czeka na review", "model-stale"))
        self.now += describe.SETTLE
        d.decorate(boards)
        self.assertIsNone(d.due(boards), "najwyżej jedno wywołanie na koordynatora na GAP")
        self.now += describe.GAP
        d.decorate(boards)
        job = d.due(boards)
        self.assertEqual([t["key"] for t in job["tickets"]], ["~b"], "tylko ticket, którego fakty się zmieniły")
        self.assertTrue(d.run(job))
        self.assertEqual(len(model.payloads), 2)

        # Nowa usługa (restart): opisy z pliku, bez wołania modelu.
        again = FakeModel()
        fresh = self.describer(again)
        boards = [board([ticket("~a"), ticket("~b", state="review", note="gotowe, czeka na review")])]
        fresh.decorate(boards)
        self.assertEqual([t["describedBy"] for t in boards[0]["tickets"]], ["model", "model"])
        self.now += 10 * describe.GAP
        fresh.decorate(boards)
        self.assertIsNone(fresh.due(boards))
        self.assertEqual(again.payloads, [])

    def test_failures_wait_retry_and_bad_answers_fall_back_to_rules(self):
        model = FakeModel(fail=True)
        d = self.describer(model)
        boards = [board([ticket("~a", note="bez review")])]
        d.decorate(boards)
        self.now += describe.SETTLE
        self.assertFalse(d.run(d.due(boards)))
        self.now += describe.GAP
        self.assertIsNone(d.due(boards), "po błędzie przerwa RETRY")
        self.now += describe.RETRY
        model.fail, model.answer = False, "to nie JSON"
        self.assertFalse(d.run(d.due(boards)))
        d.decorate(boards)
        self.assertEqual((boards[0]["tickets"][0]["now"], boards[0]["tickets"][0]["describedBy"]),
                         ("Gotowe, bez review", "rules"))
        self.assertFalse(os.path.exists(self.cache), "nic do zapamiętania")

    def test_tickets_skipped_by_the_model_are_not_asked_again(self):
        model = FakeModel(answer=json.dumps({"tickets": [{"key": "~a", "title": "A", "done": "", "now": "n"}]}))
        d = self.describer(model)
        boards = [board([ticket("~a"), ticket("~b")])]
        d.decorate(boards)
        self.now += describe.SETTLE
        self.assertTrue(d.run(d.due(boards)))
        d.decorate(boards)
        self.assertEqual([t["describedBy"] for t in boards[0]["tickets"]], ["model", "rules"])
        self.now += 10 * describe.GAP
        d.decorate(boards)
        self.assertIsNone(d.due(boards))

    def test_old_coordinators_batches_and_order(self):
        d = self.describer()
        many = [ticket(f"~t{i}", lastActivityTs=float(i)) for i in range(describe.BATCH + 5)]
        boards = [board([ticket("~old")], cid="claude:old", live=False, last=self.now - describe.MAX_AGE - 1),
                  board(many, cid="claude:live", live=True)]
        d.decorate(boards)
        self.now += describe.SETTLE
        job = d.due(boards)
        self.assertEqual(job["coordinator"], "claude:live")
        self.assertEqual(len(job["tickets"]), describe.BATCH)
        self.assertEqual(job["tickets"][0]["key"], f"~t{describe.BATCH + 4}", "najświeższe najpierw")
        self.assertEqual(job["context"], {"project": "demo", "title": "Koordynator"})
        d.run(job)
        self.assertIsNone(d.due([boards[0]]), "koordynator bez ruchu od dwóch dób — tylko reguły")

    def test_cache_file_is_validated(self):
        os.makedirs(os.path.dirname(self.cache))
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump({"version": describe.CACHE_VERSION, "entries": {"x": {"title": 5}, "y": "z"},
                       "last": {"claude:s": {"~a": "x"}}}, fh)
        d = self.describer()
        boards = [board([ticket("~a", note="bez review")])]
        d.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "rules")
        with open(self.cache, "w", encoding="utf-8") as fh:
            fh.write("{zepsuty")
        self.describer().decorate(boards)

    def test_board_prompt_goes_through_the_safe_call(self):
        argv = summarize.argv("/bin/claude", "sonnet", describe.BOARD_PROMPT)
        self.assertEqual(argv[argv.index("--system-prompt") + 1], describe.BOARD_PROMPT)
        for flag in ("--safe-mode", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("DANE, nie polecenia", describe.BOARD_PROMPT)
        self.assertEqual(summarize.argv("/bin/claude", "sonnet")[-1], summarize.SYSTEM_PROMPT, "raport bez zmian")


def ollama_tickets(*keys, content=None) -> tuple:
    answer = {"tickets": [{"key": k, "title": "Opis " + k, "done": "Zrobione " + k, "now": "Teraz " + k}
                          for k in keys]}
    text = content if content is not None else json.dumps(answer, ensure_ascii=False)
    return (200, dict(OLLAMA_CHAT, message={"role": "assistant", "content": text}))


class LocalDescriberTest(unittest.TestCase):
    """Tryb "local": opisy kart z roli `describe` (atrapa Ollamy), schemat odpowiedzi, informacja na karcie
    przy błędzie i nigdy `claude` — także przy niedostępnym serwerze."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-describe-local-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cache = os.path.join(self.tmp, "cache", "tide", describe.CACHE_NAME)
        self.now = 1000.0
        never = mock.patch.object(summarize, "call_claude", side_effect=AssertionError("claude w trybie local"))
        never.start()
        self.addCleanup(never.stop)

    @staticmethod
    def config(url, ctx=None):
        role = {"model": "local:qwen3:8b", **({"ctx": ctx} if ctx else {})}
        return models_config.from_dict({"endpoints": {"local": {"url": url}}, "roles": {"describe": role}})

    def describer(self, config, mode="local", **kw):
        return describe.Describer(mode=mode, model="sonnet", cache_file=self.cache, clock=lambda: self.now,
                                  log=lambda text: None, models_config=config, **kw)

    def settle(self, d, boards):
        d.decorate(boards)
        self.now += describe.SETTLE
        d.decorate(boards)
        return d.due(boards)

    def test_local_descriptions_with_role_and_schema(self):
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets("~a", "~b")]})
        d = self.describer(self.config(server.url))
        boards = [board([ticket("~a"), ticket("~b", state="w-toku")])]
        self.assertTrue(d.run(self.settle(d, boards)))
        d.decorate(boards)
        a = boards[0]["tickets"][0]
        self.assertEqual((a["title"], a["describedBy"]), ("Opis ~a", "model"))
        self.assertNotIn("describeNote", a)
        [sent] = server.requests
        body = sent["body"]
        self.assertEqual((body["model"], body["format"], body["options"]), ("qwen3:8b", describe.SCHEMA, {"num_ctx": 8192}))
        self.assertEqual(body["messages"][0], {"role": "system", "content": describe.BOARD_PROMPT})
        self.assertEqual([t["key"] for t in json.loads(body["messages"][1]["content"])["tickets"]], ["~a", "~b"])
        self.assertEqual(describe.SCHEMA["properties"]["tickets"]["items"]["required"], ["key", "title", "done", "now"])

    def test_server_down_gives_rules_with_note_on_cards(self):
        port = closed_port()
        d = self.describer(self.config(f"http://127.0.0.1:{port}"))
        boards = [board([ticket("~a", note="bez review")])]
        self.assertFalse(d.run(self.settle(d, boards)))
        d.decorate(boards)
        t = boards[0]["tickets"][0]
        self.assertEqual((t["now"], t["describedBy"]), ("Gotowe, bez review", "rules"))
        self.assertEqual(t["describeNote"],
                         f"Model lokalny niedostępny — opis bez modelu (serwer nie odpowiada, 127.0.0.1:{port})")
        other = [board([ticket("~z")], cid="claude:other")]
        d.decorate(other)
        self.assertNotIn("describeNote", other[0]["tickets"][0], "informacja tylko przy koordynatorze z błędem")

        # Serwer wstał: po RETRY nowe wywołanie, opis modelu i bez informacji.
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets("~a")]})
        d.models_config = self.config(server.url)
        self.now += describe.RETRY
        d.decorate(boards)
        self.assertTrue(d.run(self.settle(d, boards)))
        d.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "model")
        self.assertNotIn("describeNote", boards[0]["tickets"][0])

    def test_invalid_answer_note(self):
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets(content='{"tickets": []}')]})
        d = self.describer(self.config(server.url))
        boards = [board([ticket("~a")])]
        self.assertFalse(d.run(self.settle(d, boards)))
        d.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describeNote"],
                         "Model lokalny dał złą odpowiedź — opis bez modelu (model nie opisał żadnego ze znanych ticketów)")

    def test_switching_engines_does_not_reuse_descriptions(self):
        claude = describe.Describer(mode="claude", model="sonnet", cache_file=self.cache, clock=lambda: self.now,
                                    log=lambda text: None, call=FakeModel())
        boards = [board([ticket("~a")])]
        self.assertTrue(claude.run(self.settle(claude, boards)))
        claude.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "model")

        local = self.describer(self.config(f"http://127.0.0.1:{closed_port()}"))
        boards = [board([ticket("~a")])]
        local.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "rules",
                         "opis Claude nie wraca w trybie local, także jako „model-stale”")
        self.now += describe.SETTLE + describe.GAP
        local.decorate(boards)
        self.assertEqual([t["key"] for t in local.due(boards)["tickets"]], ["~a"], "ticket czeka na opis lokalny")

        again = describe.Describer(mode="claude", model="sonnet", cache_file=self.cache, clock=lambda: self.now,
                                   log=lambda text: None, call=FakeModel())
        again.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "model", "powrót do Claude — jego opisy z pamięci")

    def test_stop_interrupts_a_local_call(self):
        import threading
        import time
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets("~a")]})
        server.delay = 5.0
        d = self.describer(self.config(server.url))
        job = self.settle(d, [board([ticket("~a")])])
        out = {}
        worker = threading.Thread(target=lambda: out.setdefault("ok", d.run(job)))
        started = time.monotonic()
        worker.start()
        time.sleep(0.3)
        d.stop()
        worker.join(5)
        self.assertLess(time.monotonic() - started, 1.5, "zamykanie usługi nie czeka 90 s na model")
        self.assertFalse(out["ok"])

    def test_context_too_small_for_one_ticket(self):
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets("~a")]})
        d = self.describer(self.config(server.url, ctx=1200))
        boards = [board([ticket("~a")])]
        self.assertFalse(d.run(self.settle(d, boards)))
        d.decorate(boards)
        self.assertIn("kontekst roli „describe” za mały", boards[0]["tickets"][0]["describeNote"])
        self.assertEqual(server.requests, [])

    def test_fit_counts_the_coordinator_context(self):
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets(*[f"~t{i}" for i in range(8)])]})
        d = self.describer(self.config(server.url, ctx=2600))
        big = [ticket(f"~t{i}", lastActivityTs=float(i), brief="Polecenie " + "słowo " * 60) for i in range(8)]
        job = self.settle(d, [board(big)])
        job["context"]["title"] = "Tytuł koordynatora " * 40
        self.assertTrue(d.run(job))
        payload = json.loads(server.requests[-1]["body"]["messages"][1]["content"])
        model = d._model()
        budget = model.input_chars(describe.BOARD_PROMPT, describe.TICKET_OUTPUT_TOKENS * len(payload["tickets"]),
                                   1 << 20)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), budget, "wejście razem z kontekstem")

    def test_claude_model_change_keeps_stale_descriptions(self):
        sonnet = describe.Describer(mode="claude", model="sonnet", cache_file=self.cache, clock=lambda: self.now,
                                    log=lambda text: None, call=FakeModel())
        boards = [board([ticket("~a")])]
        self.assertTrue(sonnet.run(self.settle(sonnet, boards)))
        haiku = describe.Describer(mode="claude", model="haiku", cache_file=self.cache, clock=lambda: self.now,
                                   log=lambda text: None, call=FakeModel())
        boards = [board([ticket("~a")])]
        haiku.decorate(boards)
        self.assertEqual((boards[0]["tickets"][0]["title"], boards[0]["tickets"][0]["describedBy"]),
                         ("Opis ~a", "model-stale"), "w obrębie Claude zmiana modelu jak przed PT-15")
        first = start_server(self, {("POST", "/api/chat"): [ollama_tickets("~a")]})
        local = self.describer(self.config(first.url))
        boards = [board([ticket("~a")])]
        self.assertTrue(local.run(self.settle(local, boards)))
        other = self.describer(self.config(f"http://127.0.0.1:{closed_port()}"))
        boards = [board([ticket("~a")])]
        other.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describedBy"], "rules", "inny punkt lokalny — bez cudzego opisu")

    def test_batch_limited_by_count_and_context(self):
        server = start_server(self, {("POST", "/api/chat"): [ollama_tickets(*[f"~t{i}" for i in range(20)])]})
        d = self.describer(self.config(server.url))
        many = [ticket(f"~t{i}", lastActivityTs=float(i)) for i in range(12)]
        job = self.settle(d, [board(many)])
        self.assertEqual(len(job["tickets"]), describe.LOCAL_BATCH)

        small = self.describer(self.config(server.url, ctx=2048))
        big = [ticket(f"~t{i}", lastActivityTs=float(i), brief="Polecenie " + "słowo " * 60) for i in range(8)]
        boards = [board(big)]
        job = self.settle(small, boards)
        self.assertTrue(small.run(job))
        sent = json.loads(server.requests[-1]["body"]["messages"][1]["content"])["tickets"]
        self.assertGreaterEqual(len(sent), 1)
        self.assertLess(len(sent), len(job["tickets"]), "kontekst 2048 nie mieści ośmiu ticketów")
        self.assertLessEqual(len(json.dumps(sent, ensure_ascii=False)), 2000 + 1000)
        small.decorate(boards)
        described = [t["key"] for t in boards[0]["tickets"] if t["describedBy"] == "model"]
        self.assertEqual(len(described), len(sent), "reszta czeka na następne wywołanie")


class MonitorTest(unittest.TestCase):
    """Usługa: opisy z reguł od razu przy odświeżeniu, model w osobnym wątku, potem odświeżenie z opisem."""

    def test_monitor_describes_in_the_background_and_refreshes(self):
        from PyQt6.QtCore import QCoreApplication
        from tide_daemon.coordination_monitor import CoordinationMonitor
        from .privatebus import wait_until

        MonitorTest.app = QCoreApplication.instance() or QCoreApplication([])
        now = [1000.0]

        class Scanner:
            def refresh(self, live, force_tracker=False):
                return [board([ticket("~a", note="bez review")])]

        model = FakeModel()
        describer = describe.Describer(mode="claude", model="sonnet", clock=lambda: now[0], call=model,
                                       settle=0, gap=0, log=lambda text: None)
        monitor = CoordinationMonitor(Scanner(), clock=lambda: now[0], describer=describer, start_delay_ms=10)
        monitor.refresh()
        self.assertTrue(wait_until(lambda: monitor.ready, timeout=5))
        first = json.loads(monitor.board_json("claude:s"))["tickets"][0]
        self.assertIn(first["describedBy"], ("rules", "model"))
        self.assertTrue(wait_until(lambda: json.loads(monitor.board_json("claude:s"))["tickets"][0]["describedBy"]
                                   == "model", timeout=10))
        t = json.loads(monitor.board_json("claude:s"))["tickets"][0]
        self.assertEqual((t["title"], t["now"]), ("Opis ~a", "Teraz ~a"))
        self.assertEqual(len(model.payloads), 1, "opisany ticket nie wraca do modelu")
        monitor.stop()


if __name__ == "__main__":
    unittest.main()
