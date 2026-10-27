"""Czytelność raportu po PT-24 (raport „dla ludzi, nie dla AI, bez bełkotu”), na przykładach z prawdziwego dnia:

1. zdania nigdy nie są ucinane w pół zdania („…ma jasne znaczenie w modelu i w…”, „…ewaluatora.…”): całe zdania
   w limicie albo nic, a na końcu zdanie z liczbami — model, reguły, „Dziś w skrócie”, „Teraz czeka”,
2. projekty pod nazwą z tablicy pracy (`LegalApp`, `LogTool`) zamiast katalogu (`agent-team`,
   `LogViewer`) — na kartach, w tekście do schowka, w zdaniach z reguł i na wejściu modelu,
3. gałąź karty jako numer i tytuł karty („K-1 Data ważności wiadomości”), bez karty — czytelny tytuł z nazwy
   gałęzi; karta w kilku projektach (PT-28) opisana raz, w pozostałych krótka wzmianka.

Prawdziwy `claude` nigdy nie jest wołany (atrapa przez $TIDE_CLAUDE_BIN)."""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from .reportfix import H, NOW, START
from .test_report_standup import FAKE_CLAUDE
from tide_daemon.reports import cards, collect, data, render, standup, summarize

# Prawdziwe zdania z „Dziś w skrócie”, które wyszły ucięte (PT-24): jedno za długie zdanie i dwa zdania,
# z których drugie się nie mieściło.
LONG_ONE = ("LegalApp: ostrzeżenie o widoczności przepisu ma jasne znaczenie w modelu i w raporcie, "
            "bo rozróżnia przepis uchylony, przepis zmieniony po dacie wydania aktu i przepis, którego "
            "jeszcze nie ma w bazie, a każdy z tych przypadków dostaje osobny komunikat dla prawnika "
            "i osobny wpis w historii zmian, więc nikt nie musi już zgadywać, skąd się wziął, a przy okazji "
            "raport pokazuje datę ostatniej zmiany, numer dziennika urzędowego, w którym ją ogłoszono, listę "
            "spraw, w których przepis był już powoływany, i ostrzeżenie dla każdej z nich, jeśli od tamtej pory "
            "treść przepisu się zmieniła albo przepis przestał obowiązywać w całości lub w części.")
LONG_TWO = ("LegalApp: ewaluator przepisów działa teraz na nowym zestawie przykładów i daje wynik w kilka minut. "
            "Dalej trwa porównanie z poprzednią wersją, a do końca dnia zostaje jeszcze płatny przebieg "
            "ewaluatora na pełnym zbiorze, po którym wyniki trafią do raportu i do opisu zmian dla zespołu prawników, "
            "a następnie do porównania z ręczną oceną z zeszłego tygodnia, żeby było widać, czy nowy zestaw "
            "przykładów nie przesunął ocen w żadną stronę i czy liczby z raportu da się pokazać na spotkaniu.")

SNAPSHOT = {
    "projects": [
        {"slug": "legalapp", "name": "LegalApp", "repo": "/w/agent-team"},
        {"slug": "logtool", "name": "LogTool", "repo": "/w/LogViewer"},
        {"slug": "toolssuite", "name": "toolssuite", "repo": "/w/toolssuite"},
        {"slug": "legacyinfra", "name": "legacyinfra", "repo": "/w/legacyinfra"},
        {"slug": "mobileapp", "name": "mobileapp", "repo": "/w/mobileapp"},
    ],
    "cards": [
        {"id": "K-1", "title": "Data ważności wiadomości", "project": "toolssuite",
         "projects": ["legacyinfra", "mobileapp"], "branch": "", "session": ""},
        {"id": "K-12", "title": "Eksport logów do CSV", "project": "logtool", "projects": [],
         "branch": "claude/export-csv", "session": ""},
        {"id": "K-7", "title": "Przegląd umów", "project": "legalapp", "projects": [], "branch": "",
         "session": "claude:s-legal"},
        {"id": "K-3", "title": "Nowy ekran logowania", "project": "mobileapp", "projects": [], "branch": "",
         "session": ""},
    ],
}


def commit(subject, ts=NOW - 2 * H):
    return SimpleNamespace(sha=subject, ts=ts, subject=subject, merge=False, paths=["src/App/a.cs"])


def branch(name, *subjects):
    return SimpleNamespace(branch=name, name=name, is_main=False, commits=[commit(s) for s in subjects],
                           worktree="", tool="claude", merged_into="")


def session(sid, title, state="done", message="Gotowe.", branch_name=""):
    return SimpleNamespace(id=sid, tool="claude", title=title, prompt=title, state=state, reason="",
                           interrupted=False, final_message=message, final_ts=1, last_user_ts=0, waiting_text="",
                           since=0, first_ts=0, last_ts=NOW, last_action="", runs=[], automation={},
                           branch=branch_name, cwd="")


def project(name, path, branches=(), sessions=(), label=""):
    return SimpleNamespace(name=name, label=label, path=path, key=path, slug=name.lower(), is_repo=True, plans=[],
                           to_clean=[], unpushed=[], main_dirty_count=0, main_branch="main",
                           branches=list(branches), sessions=list(sessions), active=True, conflicts=[])


def report_of(*projects, board=SNAPSHOT):
    return SimpleNamespace(start=START, active=list(projects), board=cards.BoardInfo(board))


def texts(summary, *keys):
    return [i["text"] for k in keys for i in summary.get(k) or []]


# --- 1. bez ucinania w pół zdania ------------------------------------------------------------------------

class NoCutTest(unittest.TestCase):
    def test_fit_takes_whole_sentences_or_nothing(self):
        self.assertEqual(standup.fit(LONG_TWO, 200),
                         "LegalApp: ewaluator przepisów działa teraz na nowym zestawie przykładów i daje wynik "
                         "w kilka minut.")
        self.assertEqual(standup.fit(LONG_ONE, 200), "", "jedno za długie zdanie — nic zamiast „…i w…”")
        self.assertEqual(standup.fit("Krótko.", 200), "Krótko.")

    def test_model_brief_never_cut(self):
        good = "LogTool: wyszukiwanie przestało zwracać duplikaty."
        brief = standup.validate_brief([LONG_ONE, LONG_TWO, good], {})
        self.assertEqual(brief, ["LegalApp: ewaluator przepisów działa teraz na nowym zestawie przykładów "
                                 "i daje wynik w kilka minut.", good],
                         "za długie zdanie odpada, z dwóch zdań zostaje pierwsze całe")
        for sentence in brief:
            self.assertNotIn("…", sentence)

    def test_model_fields_whole_sentences_and_fallback(self):
        fact = {"project": "agent-team", "label": "LegalApp", "plans": [], "questions": [], "conflicts": [],
                "coordinatorSessions": [], "units": [],
                "tickets": [{"id": "LA-35", "key": "LA-35", "title": "Ostrzeżenie o widoczności przepisu",
                             "state": "gotowe", "note": "", "result": "", "description": "", "planNote": "",
                             "question": "", "branch": "", "review": "", "category": "work"}]}
        answer = {"projects": [{"name": "LegalApp", "headline": LONG_ONE, "theme": LONG_TWO + " " + LONG_TWO,
                                "done": [{"ref": "LA-35", "text": LONG_ONE, "about": LONG_TWO}]}]}
        model = standup.validate(answer, [fact])
        self.assertEqual(list(model), ["agent-team"], "nazwa z tablicy w odpowiedzi → projekt-katalog")
        out = standup.merge(model, [fact])["agent-team"]
        self.assertEqual(out["theme"], LONG_TWO + " " + LONG_TWO.split(". ")[0] + ".",
                         "z drugiego razu mieści się tylko pierwsze zdanie — całe")
        self.assertLessEqual(len(out["theme"]), standup.THEME_MAX)
        self.assertEqual(texts(out, "done"), ["Ostrzeżenie o widoczności przepisu"],
                         "za długa pozycja odrzucona — reguły zamiast uciętej")
        self.assertEqual(out["headline"], "Zrobione: Ostrzeżenie o widoczności przepisu.")
        for text in [out["theme"], out["headline"]] + texts(out, "done"):
            self.assertNotIn("…", text)

    def test_rules_headline_and_brief_shorten_by_parts_then_counts(self):
        long_title = "Ekran " + "bardzo " * 70 + "długiego tytułu"
        summary = {"done": [{"ref": "", "text": long_title}, {"ref": "", "text": "Eksport do CSV"}],
                   "doing": [{"ref": "", "text": "Import z linku — trwa praca"}]}
        headline = standup._headline(summary)
        self.assertEqual(headline, "W toku: Import z linku — trwa praca.", "zrobione nie mieszczą się — tylko W toku")
        brief = standup.rules_brief({"mobileapp": summary}, ["mobileapp"])
        self.assertEqual(brief, ["mobileapp — w toku: import z linku — trwa praca."])
        huge = {"done": [{"ref": "", "text": long_title * 2}], "doing": [{"ref": "", "text": long_title * 2}]}
        self.assertEqual(standup._headline(huge), "Zrobione: 1 rzecz, w toku: 1 rzecz.")
        self.assertEqual(standup.rules_brief({"x": huge}, ["x"]), ["x — zrobione: 1 rzecz, w toku: 1 rzecz."])

    def test_agent_message_as_whole_sentences(self):
        message = LONG_TWO.replace("LegalApp: e", "E")
        self.assertEqual(standup.first_sentences(message, 2, 200, whole=True),
                         "Ewaluator przepisów działa teraz na nowym zestawie przykładów i daje wynik w kilka minut.")
        self.assertEqual(standup.first_sentences(LONG_ONE, 2, 200, whole=True), "")
        self.assertTrue(standup.first_sentences(LONG_ONE, 2, 200).endswith("…"), "wejście modelu: sam początek")
        fact = standup._session_fact(session("claude:x", "Ewaluator", message=LONG_ONE))
        self.assertEqual(fact["message"], "", "dopisek pozycji tylko z całych zdań")

    def test_now_waiting_question_whole_or_generic(self):
        waiting = [{"project": "agent-team", "label": "LegalApp", "title": "", "reason": "question",
                    "text": LONG_ONE.replace(".", "?")}]
        self.assertEqual(data.now_text(waiting), "Teraz czeka na ciebie: LegalApp — agent czeka na odpowiedź.")
        waiting[0]["text"] = "Pushować?"
        self.assertEqual(data.now_text(waiting), "Teraz czeka na ciebie: LegalApp — agent pyta: „Pushować?”.")


# --- 2. nazwy projektów z tablicy ------------------------------------------------------------------------

class BoardNamesTest(unittest.TestCase):
    def test_labels_from_board_and_fallback(self):
        info = cards.BoardInfo(SNAPSHOT)
        projects = [collect.Project(key=p, name=os.path.basename(p), path=p)
                    for p in ("/w/agent-team", "/w/LogViewer", "/w/QuotaApp")]
        collect.assign_labels(projects, info)
        self.assertEqual([p.display for p in projects], ["LegalApp", "LogTool", "QuotaApp"],
                         "repozytorium spoza tablicy — nazwa katalogu")
        twins = [collect.Project(key="/w/a", name="a", path="/w/a"), collect.Project(key="/w/b", name="b", path="/w/b")]
        collect.assign_labels(twins, cards.BoardInfo({"projects": [{"slug": "x", "name": "X", "repo": "/w/a"},
                                                                   {"slug": "y", "name": "X", "repo": "/w/b"}]}))
        self.assertEqual([p.display for p in twins], ["X (a)", "X (b)"], "ta sama nazwa — z katalogiem")
        self.assertEqual(cards.BoardInfo(None).label("/w/agent-team"), "")

    def test_rules_brief_copy_text_and_pages_use_board_names(self):
        legal = project("agent-team", "/w/agent-team", label="LegalApp",
                        sessions=[session("claude:s1", "Ostrzeżenie o widoczności przepisu")])
        logs = project("LogViewer", "/w/LogViewer", label="LogTool",
                       sessions=[session("claude:s2", "Filtr po części wartości")])
        report = report_of(legal, logs)
        result = summarize.summarize(report, mode="rules")
        self.assertEqual(result["labels"], {"agent-team": "LegalApp", "LogViewer": "LogTool"})
        self.assertEqual(result["brief"], ["LegalApp — skończone: ostrzeżenie o widoczności przepisu.",
                                           "LogTool — skończone: filtr po części wartości."])
        text = standup.as_text(result, ["agent-team", "LogViewer"], "Na daily")
        self.assertIn("\nLegalApp\n", text)
        self.assertNotIn("agent-team", text)
        page_report = SimpleNamespace(day="2026-10-27", standup=result, notes={})
        for p in (legal, logs):
            p.commits, p.waiting, p.last_ts = 0, [], NOW
        html = render.overview(page_report, [legal, logs])
        self.assertIn('>LegalApp</a> <span class="folder">agent-team</span></h3>', html,
                      "nazwa z tablicy, katalog jako mały dopisek")
        self.assertIn("\nLogTool\n", render.daily_text(page_report, [logs]))

    def test_report_json_card_has_label_and_folder(self):
        p = SimpleNamespace(name="agent-team", label="LegalApp", slug="agent-team", path="/w/agent-team",
                            sessions=[], commits=1, waiting=[], last_ts=NOW, plans=[])
        q = SimpleNamespace(name="QuotaApp", label="", slug="quotaapp", path="/w/QuotaApp",
                            sessions=[], commits=1, waiting=[], last_ts=NOW, plans=[])
        summary = {"source": "rules", "labels": {"agent-team": "LegalApp"}, "projects": {
            "agent-team": {"done": [{"ref": "", "text": "Ostrzeżenie o widoczności"}]},
            "QuotaApp": {"done": [{"ref": "", "text": "Limity w panelu"}]}}}
        report = SimpleNamespace(day="2026-10-27", generated=NOW, start=NOW - 86400, end=NOW, repos=[p, q],
                                 active=[p, q], outside=[], quiet=[], waiting=[], standup=summary)
        out = data.build(report)
        cards_ = {c["name"]: c for c in out["projects"]}
        self.assertEqual((cards_["agent-team"]["label"], cards_["agent-team"]["folder"]),
                         ("LegalApp", "agent-team"))
        self.assertEqual((cards_["QuotaApp"]["label"], cards_["QuotaApp"]["folder"]), ("QuotaApp", ""))
        self.assertTrue(cards_["agent-team"]["copyText"].startswith("Na daily · "))
        self.assertIn("\nLegalApp\n", cards_["agent-team"]["copyText"])
        live = data.apply_live(out, "2026-10-27", [{"id": "claude:w", "state": "waiting", "cwd": "/w/agent-team/src",
                                                     "sinceTs": NOW, "reason": "question", "waitingText": "Pushować?"}])
        self.assertEqual(live["now"][0]["project"], "agent-team", "klucz zostaje (gorąca karta, notki)")
        self.assertEqual(live["nowText"], "Teraz czeka na ciebie: LegalApp — agent pyta: „Pushować?”.")
        self.assertIn("\nLegalApp\n", live["copyAll"])

    def test_model_gets_board_names_and_answer_maps_back(self):
        tmp = tempfile.mkdtemp(prefix="tide-names-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = os.path.join(tmp, "claude")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(fake, 0o755)
        answer_path, log = os.path.join(tmp, "answer.json"), os.path.join(tmp, "calls.jsonl")
        with open(answer_path, "w", encoding="utf-8") as fh:
            json.dump({"brief": ["LegalApp: prawnik widzi, czy przepis jest jeszcze w mocy.", LONG_ONE,
                                 "LogTool: logi można filtrować po części wartości."],
                       "projects": [{"name": "LegalApp", "headline": "Ostrzeżenie o widoczności przepisu działa.",
                                     "done": [{"ref": "", "text": "Prawnik widzi, czy przepis jest jeszcze w mocy."}]},
                                    {"name": "logtool", "headline": "Filtr po części wartości.",
                                     "done": [{"ref": "", "text": "Logi można filtrować po części wartości."}]}]}, fh)
        legal = project("agent-team", "/w/agent-team", label="LegalApp",
                        sessions=[session("claude:s1", "Ostrzeżenie o widoczności przepisu")])
        logs = project("LogViewer", "/w/LogViewer", label="LogTool",
                       sessions=[session("claude:s2", "Filtr po części wartości")])
        env = dict(os.environ, TIDE_CLAUDE_BIN=fake, FAKE_CLAUDE_LOG=log, FAKE_CLAUDE_ANSWER=answer_path)
        result = summarize.summarize(report_of(legal, logs), mode="claude", cache_dir=tmp, env=env)
        with open(log, encoding="utf-8") as fh:
            request = json.loads(json.loads(fh.readline())["stdin"])
        self.assertEqual([p["name"] for p in request["projects"]], ["LegalApp", "LogTool"])
        self.assertNotIn("agent-team", json.dumps(request, ensure_ascii=False))
        self.assertEqual((result["source"], result["projects"]["agent-team"]["source"],
                          result["projects"]["LogViewer"]["source"]), ("model", "model", "model"))
        self.assertEqual(result["brief"], ["LegalApp: prawnik widzi, czy przepis jest jeszcze w mocy.",
                                           "LogTool: logi można filtrować po części wartości."])
        summarize.write_cache(tmp, result["cache"])
        again = summarize.summarize(report_of(legal, logs), mode="claude", cache_dir=tmp, env=env)
        self.assertEqual((again["calls"], again["projects"]["agent-team"]["source"]), (0, "model"),
                         "odpowiedź pod nazwą z tablicy wraca z pamięci")


# --- 3. karty zamiast nazw gałęzi ------------------------------------------------------------------------

class CardBranchTest(unittest.TestCase):
    def test_card_title_and_id_instead_of_branch_slug(self):
        maui = project("mobileapp", "/w/mobileapp", branches=[branch("claude/k-3-nowy-ekran-logowania", "feat: login")])
        r = standup.rules(standup.facts(report_of(maui))[0])
        self.assertEqual(texts(r, "doing"), ["K-3 Nowy ekran logowania — zmiany czekają na scalenie"])
        self.assertEqual(r["headline"], "W toku: K-3 Nowy ekran logowania — zmiany czekają na scalenie.")

    def test_without_board_slug_is_humanized(self):
        maui = project("mobileapp", "/w/mobileapp", branches=[branch("claude/k-1-data-waznosci-wiadomosci", "feat: x")])
        r = standup.rules(standup.facts(report_of(maui, board=None))[0])
        self.assertEqual(texts(r, "doing"), ["K-1 Data waznosci wiadomosci — zmiany czekają na scalenie"],
                         "bez karty: numer wielkimi literami, zdanie z wielkiej litery (zamiast „k-1 · data…”)")
        self.assertEqual(standup.humane_branch_title("fix · unique phrase entry"), "Unique phrase entry")
        self.assertEqual(standup.humane_branch_title("summary-tabs 03 · version axis"),
                         "Summary-tabs 03 — version axis")

    def test_card_matching_rules(self):
        info = cards.BoardInfo(SNAPSHOT)
        self.assertEqual(info.card_for(branch="claude/export-csv", repo="/w/elsewhere")["id"], "K-12", "branch karty")
        self.assertEqual(info.card_for(sessions=["claude:s-legal"])["id"], "K-7", "session karty")
        self.assertEqual(info.card_for(sessions=["s-legal"])["id"], "K-7", "id sesji bez narzędzia")
        self.assertEqual(info.card_for(plan_card="k-12")["id"], "K-12", "klucz card: planu")
        self.assertEqual(info.card_for(branch="codex/k-1-fix", repo="/w/legacyinfra")["id"], "K-1",
                         "token w gałęzi, repozytorium dodatkowego projektu karty")
        self.assertIsNone(info.card_for(branch="codex/k-1-fix", repo="/w/QuotaApp"),
                          "obca gałąź „k-1-…” spoza projektów karty to nie karta")
        self.assertIsNone(info.card_for(branch="feat/k-8s-deploy", repo="/w/toolssuite"), "„k-8s” to nie numer")
        self.assertIsNone(info.card_for(branch="claude/k-99-x", repo="/w/toolssuite"), "karty nie ma na tablicy")

    def test_session_card_for_waiting_unit(self):
        s = session("claude:s-legal", "Przegląd", state="waiting")
        s.waiting_text, s.reason = "Który wariant umowy?", "question"
        legal = project("agent-team", "/w/agent-team", label="LegalApp", sessions=[s])
        r = standup.rules(standup.facts(report_of(legal))[0])
        self.assertEqual(texts(r, "waiting"), ["K-7 Przegląd umów: agent pyta: „Który wariant umowy?”"])


class MultiProjectCardTest(unittest.TestCase):
    """K-1 dotyka toolssuite (główny), legacyinfra i mobileapp — opisana raz."""

    def world(self, with_main=True):
        projects = [project("legacyinfra", "/w/legacyinfra",
                            branches=[branch("claude/k-1-data-waznosci-wiadomosci", "feat: ttl")]),
                    project("mobileapp", "/w/mobileapp",
                            branches=[branch("claude/k-1-data-waznosci-wiadomosci", "feat: expiry"),
                                      branch("claude/k-3-nowy-ekran-logowania", "feat: login")])]
        if with_main:
            projects.insert(1, project("toolssuite", "/w/toolssuite",
                                       branches=[branch("claude/k-1-data-waznosci-wiadomosci", "feat: expiry date")]))
        all_facts = standup.facts(report_of(*projects))
        return {f["project"]: standup.rules(f) for f in all_facts}, all_facts

    def test_described_once_under_main_project(self):
        out, all_facts = self.world()
        self.assertEqual(texts(out["toolssuite"], "doing"),
                         ["K-1 Data ważności wiadomości (pracuje też w: legacyinfra, mobileapp) — "
                          "zmiany czekają na scalenie"])
        self.assertEqual(texts(out["legacyinfra"], "doing"), ["Zmiany w ramach K-1 Data ważności wiadomości"])
        self.assertEqual(texts(out["mobileapp"], "doing"),
                         ["Zmiany w ramach K-1 Data ważności wiadomości",
                          "K-3 Nowy ekran logowania — zmiany czekają na scalenie"])
        brief = standup.rules_brief(out, [f["project"] for f in all_facts])
        self.assertEqual(sum(s.count("K-1") for s in brief), 1, brief)
        self.assertEqual(brief, ["toolssuite — w toku: K-1 Data ważności wiadomości (pracuje też w: "
                                 "legacyinfra, mobileapp) — zmiany czekają na scalenie.",
                                 "mobileapp — w toku: K-3 Nowy ekran logowania — zmiany czekają na scalenie."],
                         "legacyinfra ma tylko wzmiankę — bez osobnego zdania")
        payload, _names = summarize.model_input(all_facts)
        work = {p["name"]: p["work"] for p in payload["projects"]}
        self.assertEqual(work["toolssuite"][0]["card"], "K-1 Data ważności wiadomości")
        self.assertEqual(work["toolssuite"][0]["cardAlsoIn"], ["legacyinfra", "mobileapp"])
        self.assertEqual(work["mobileapp"][0]["cardDescribedIn"], "toolssuite")

    def test_main_project_without_work_first_project_describes(self):
        out, _facts = self.world(with_main=False)
        self.assertEqual(texts(out["legacyinfra"], "doing"),
                         ["K-1 Data ważności wiadomości (pracuje też w: toolssuite, mobileapp) — "
                          "zmiany czekają na scalenie"])
        self.assertEqual(texts(out["mobileapp"], "doing")[0], "Zmiany w ramach K-1 Data ważności wiadomości")

    def test_card_with_several_units_in_project_is_one_item(self):
        maui = project("mobileapp", "/w/mobileapp", branches=[branch("claude/k-3-nowy-ekran-logowania", "feat: a"),
                                                         branch("codex/k-3-poprawki", "fix: b")])
        r = standup.rules(standup.facts(report_of(maui))[0])
        self.assertEqual(texts(r, "doing"), ["K-3 Nowy ekran logowania — zmiany czekają na scalenie"])


if __name__ == "__main__":
    unittest.main()
