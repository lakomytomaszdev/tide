"""PT-279 (K-69 #2): kartki ticketu, poprawek, review i domknięcia z efektywnego kontraktu testów.

Testy jednostkowe decyzji `effective_tests`, reguły wykrycia `make_targets` i publicznych funkcji kartek —
bez usługi i D-Bus. Wzorcowe fragmenty z master leżą niżej jako dane (`MASTER_FRAGMENTS`).
"""
import json
import os
import re

import shlex
import tempfile
import unittest

from tide_daemon.cardflow import handoff, testpolicy
from tide_daemon.cardflow.testpolicy import effective_tests, make_targets, master_policy


# --- Dane wzorca i drzewa worktree -------------------------------------------------------------
# MASTER_FRAGMENTS wygenerowano publicznymi funkcjami kartek z master `04a23c16` na drzewach
# `VARIANTS` i wycięciami `fragments` niżej. Nie zmieniaj drzew ani wycięć bez ponownego
# wygenerowania wzorca z master — inaczej porównanie przestaje znaczyć „identyczne z master”.


BASE = "0123456789abcdef0123456789abcdef01234567"
MASTER = "04a23c16c74fd991e325c4ddb601f84d9f84845d"

# plasma-tide: `test` w Makefile, `test-changed` w mk/tests.mk, uruchamiacz z `--modules`.
CHANGED = {
    "Makefile": ".PHONY: help test\ninclude mk/*.mk\n\ntest:\n\t@/usr/bin/python3 tools/test_runner.py\n",
    "mk/tests.mk": ".PHONY: test-changed\n\ntest-changed:\n\t@/usr/bin/python3 tools/test_runner.py --changed\n",
    "tools/test_runner.py": 'parser.add_argument("--modules", nargs="+")\n',
}
# Starsza gałąź karty (K-37/K-38 sprzed K-54): tylko `test`, uruchamiacz bez `--modules`.
LEGACY = {
    "Makefile": ".PHONY: test\ninclude mk/*.mk\n\ntest:\n\t@/usr/bin/python3 tools/test_runner.py\n",
    "mk/tests.mk": "test-daemon:\n\t@true\n",
    "tools/test_runner.py": "def execute():\n    pass\n",
}
VARIANTS = {"changed": CHANGED, "legacy": LEGACY}


def tree(case, files: dict) -> str:
    """Katalog tymczasowy z plikami (`ścieżka względna → treść`), sprzątany po teście."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    for rel, text in files.items():
        path = os.path.join(tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return tmp.name


def cards(handoff, worktree: str, **extra) -> dict:
    """Kartki ticketu, poprawek, review i domknięcia dla plasma-tide (`extra` — np. `policy` po PT-279)."""
    common = dict(card_id="K-69", title="Kontrakt testów", description="Opis karty.", ticket="PT-279",
                  worktree=worktree, out_dir="/o")
    ticket_extra = {k: v for k, v in extra.items() if k != "close_policy"}
    close_extra = {"policy": extra["close_policy"]} if "close_policy" in extra else {}
    return {
        "ticket": handoff.ticket_card(**common, project="plasma-tide", branch="ticket/pt-279",
                                      card_branch="card/k-69", test_base=BASE, **ticket_extra),
        "fix": handoff.fix_card(**common, round_no=1, branch="ticket/pt-279", review="MAJOR: popraw",
                                test_base=BASE, **ticket_extra),
        "review": handoff.review_card(**common, round_no=1, card_branch="card/k-69",
                                      ticket_branch="ticket/pt-279", diff="diff", note="notka", rules="zasady",
                                      test_base=BASE, **ticket_extra),
        "close": handoff.close_card(card_id="K-69", title="Kontrakt testów", description="Opis karty.",
                                    worktree=worktree, out_dir="/o", tickets=[], manual=False, **close_extra),
    }


def _between(text: str, start: str, end: str, *, include_start=True, include_end=True) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i if include_start else i + len(start):j + len(end) if include_end else j]


TESTS_START = "Testy ticketu (także po poprawkach)"
TESTS_END = "właściciel.\n"


def fragments(texts: dict) -> dict:
    """Fragmenty polityki testów z kartek: testy ticketu, punkty recenzenta, testy domknięcia."""
    out = {}
    for kind in ("ticket", "fix", "review"):
        out[f"{kind}-tests"] = _between(texts[kind], TESTS_START, TESTS_END)
    out["review-bullets"] = _between(texts["review"], "- sprawdź zgodność z opisem karty i zasadami projektu,\n",
                                     "- podaj konkretne", include_start=False, include_end=False)
    out["close-tests"] = _between(texts["close"], "Przed napisaniem podsumowania", "bez nowych zmian.\n")
    return out


MASTER_FRAGMENTS = json.loads(r'''{
  "master": "04a23c16c74fd991e325c4ddb601f84d9f84845d",
  "base": "0123456789abcdef0123456789abcdef01234567",
  "fragments": {
    "changed": {
      "ticket-tests": "Testy ticketu (także po poprawkach): autor przed oddaniem uruchamia `make test-changed BASE=0123456789abcdef0123456789abcdef01234567`.\nBASE to przypięty punkt startu ticketu na gałęzi karty, a nie bieżący wierzchołek karty ani master.\nZapisz polecenie i wynik w notce. Pełny `make test` nie jest kryterium oddania ticketu ani jego review;\njest wykonywany raz przy domknięciu karty. Ta zasada zastępuje wymóg pełnego `make test` zapisany\nw kartkach istniejących planów (w tym K-37, K-38 i K-55); nie przepisuj tych planów.\nJeśli `make test-changed` wybierze pełny zestaw z mapy zmian, wykonaj go bez dodatkowego `make test`.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0) przez uruchamiacz projektu — ta sama pula maszyny i izolacja stanu co w pełnym przebiegu:\n`/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>`. Tide robi to także sam, gdy prosisz\no to samo pełne polecenie na tym samym HEAD.\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "fix-tests": "Testy ticketu (także po poprawkach): autor przed oddaniem uruchamia `make test-changed BASE=0123456789abcdef0123456789abcdef01234567`.\nBASE to przypięty punkt startu ticketu na gałęzi karty, a nie bieżący wierzchołek karty ani master.\nZapisz polecenie i wynik w notce. Pełny `make test` nie jest kryterium oddania ticketu ani jego review;\njest wykonywany raz przy domknięciu karty. Ta zasada zastępuje wymóg pełnego `make test` zapisany\nw kartkach istniejących planów (w tym K-37, K-38 i K-55); nie przepisuj tych planów.\nJeśli `make test-changed` wybierze pełny zestaw z mapy zmian, wykonaj go bez dodatkowego `make test`.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0) przez uruchamiacz projektu — ta sama pula maszyny i izolacja stanu co w pełnym przebiegu:\n`/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>`. Tide robi to także sam, gdy prosisz\no to samo pełne polecenie na tym samym HEAD.\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "review-tests": "Testy ticketu (także po poprawkach): autor przed oddaniem uruchamia `make test-changed BASE=0123456789abcdef0123456789abcdef01234567`.\nBASE to przypięty punkt startu ticketu na gałęzi karty, a nie bieżący wierzchołek karty ani master.\nZapisz polecenie i wynik w notce. Pełny `make test` nie jest kryterium oddania ticketu ani jego review;\njest wykonywany raz przy domknięciu karty. Ta zasada zastępuje wymóg pełnego `make test` zapisany\nw kartkach istniejących planów (w tym K-37, K-38 i K-55); nie przepisuj tych planów.\nJeśli `make test-changed` wybierze pełny zestaw z mapy zmian, wykonaj go bez dodatkowego `make test`.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0) przez uruchamiacz projektu — ta sama pula maszyny i izolacja stanu co w pełnym przebiegu:\n`/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>`. Tide robi to także sam, gdy prosisz\no to samo pełne polecenie na tym samym HEAD.\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "review-bullets": "- sprawdź w notce, że `make test-changed` z poniższą bazą przeszedł dla oddawanych zmian;\n  nie żądaj osobnego pełnego `make test` i nie uruchamiaj go ponownie na potrzeby review,\n- ponowienie samych padniętych modułów uznaj tylko przy tym samym HEAD co pełny przebieg i czystym drzewie\n  (zapis w notce); inaczej wymagaj pełnego polecenia z zasady testów; udane ponowienie nie zastępuje porażki,\n  oceń rozliczenie odstępstwa jak przy każdym nieudanym przebiegu,\n",
      "close-tests": "Przed napisaniem podsumowania uruchom raz pełny `make test` w worktree gałęzi karty, po scaleniu ticketów.\nZapisz polecenie i wynik w podsumowaniu. Jeśli testy nie przejdą, nie zgłaszaj done i nie powtarzaj całego\n`make test` na tym samym kodzie: dowody do rozliczenia zbierz ponowieniem tylko padniętych modułów (kontrakt\noutside) według zasady niżej, a potem zapisz wynik error z opisem niepowodzeń i tym rozliczeniem, jak przy innych\nproblemach domknięcia. O przyjęciu odstępstwa decyduje użytkownik.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0) przez uruchamiacz projektu — ta sama pula maszyny i izolacja stanu co w pełnym przebiegu:\n`/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>`. Tide robi to także sam, gdy prosisz\no to samo pełne polecenie na tym samym HEAD.\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n Gdy polecenie wymaga zgody lub wykonania\npoza piaskownicą, użyj kontraktu denied albo outside i poczekaj na wznowienie; nie powtarzaj udanego przebiegu\nbez nowych zmian.\n"
    },
    "legacy": {
      "ticket-tests": "Testy ticketu (także po poprawkach): ta gałąź karty powstała przed celem `make test-changed`\ni go nie ma. Obowiązuje dla niej dotychczasowa zasada: przed oddaniem uruchom raz pełny `make test` i zapisz\npolecenie oraz wynik w notce. Nie przenoś infrastruktury testów z master i nie twórz zamiennika celu.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0), każdy tym samym interpreterem co `make test` i we własnym katalogu stanu:\n`d=\"$(mktemp -d)\" && XDG_STATE_HOME=\"$d\" /usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>`\n(test bez izolacji nadpisał kiedyś stan działającej Tablicy).\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "fix-tests": "Testy ticketu (także po poprawkach): ta gałąź karty powstała przed celem `make test-changed`\ni go nie ma. Obowiązuje dla niej dotychczasowa zasada: przed oddaniem uruchom raz pełny `make test` i zapisz\npolecenie oraz wynik w notce. Nie przenoś infrastruktury testów z master i nie twórz zamiennika celu.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0), każdy tym samym interpreterem co `make test` i we własnym katalogu stanu:\n`d=\"$(mktemp -d)\" && XDG_STATE_HOME=\"$d\" /usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>`\n(test bez izolacji nadpisał kiedyś stan działającej Tablicy).\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "review-tests": "Testy ticketu (także po poprawkach): ta gałąź karty powstała przed celem `make test-changed`\ni go nie ma. Obowiązuje dla niej dotychczasowa zasada: przed oddaniem uruchom raz pełny `make test` i zapisz\npolecenie oraz wynik w notce. Nie przenoś infrastruktury testów z master i nie twórz zamiennika celu.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0), każdy tym samym interpreterem co `make test` i we własnym katalogu stanu:\n`d=\"$(mktemp -d)\" && XDG_STATE_HOME=\"$d\" /usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>`\n(test bez izolacji nadpisał kiedyś stan działającej Tablicy).\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n",
      "review-bullets": "- sprawdź w notce wynik pełnego `make test` (ta gałąź karty nie ma jeszcze `make test-changed`);\n  nie uruchamiaj go ponownie na potrzeby review,\n- ponowienie samych padniętych modułów uznaj tylko przy tym samym HEAD co pełny przebieg i czystym drzewie\n  (zapis w notce); inaczej wymagaj pełnego polecenia z zasady testów; udane ponowienie nie zastępuje porażki,\n  oceń rozliczenie odstępstwa jak przy każdym nieudanym przebiegu,\n",
      "close-tests": "Przed napisaniem podsumowania uruchom raz pełny `make test` w worktree gałęzi karty, po scaleniu ticketów.\nZapisz polecenie i wynik w podsumowaniu. Jeśli testy nie przejdą, nie zgłaszaj done i nie powtarzaj całego\n`make test` na tym samym kodzie: dowody do rozliczenia zbierz ponowieniem tylko padniętych modułów (kontrakt\noutside) według zasady niżej, a potem zapisz wynik error z opisem niepowodzeń i tym rozliczeniem, jak przy innych\nproblemach domknięcia. O przyjęciu odstępstwa decyduje użytkownik.\nPonowienie bez zmian kodu: gdy przebieg padł, a od jego commita nie zmieniasz żadnego pliku (podejrzenie\nniestabilności lub obciążenia), nie powtarzaj całego zestawu. Uruchom tylko moduły z listy „Wszystkie nieudane testy” (albo z `summary.json` przebiegu, `returncode`\nróżny od 0), każdy tym samym interpreterem co `make test` i we własnym katalogu stanu:\n`d=\"$(mktemp -d)\" && XDG_STATE_HOME=\"$d\" /usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>`\n(test bez izolacji nadpisał kiedyś stan działającej Tablicy).\nW notce zapisz pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, ścieżka raportu) i ponowienie\n(ten sam HEAD, czysty `git status`, lista ponowionych testów, wynik). Gdy zmieniasz jakikolwiek plik, obowiązuje\npełne polecenie z akapitu wyżej. Ponowienie zmienia tylko to, co uruchamiasz, nie ocenę: porażka pierwszego\nprzebiegu zostaje wynikiem, a udane ponowienie jej nie zastępuje. Rozlicz ją jak każde odstępstwo\nwedług zasad wyjątków od testów projektu (w Tide: `docs/testing.md`, „Wyjątki od testów”): ten sam test na bazie\nticketu i na głównej gałęzi projektu, obciążenie, zakres, właściciel.\n Gdy polecenie wymaga zgody lub wykonania\npoza piaskownicą, użyj kontraktu denied albo outside i poczekaj na wznowienie; nie powtarzaj udanego przebiegu\nbez nowych zmian.\n"
    }
  }
}''')



DOTNET = {"changed": "dotnet test Foo.Tests --filter Changed={base}", "full": "dotnet test Foo.sln"}
ONLY_CHANGED = {"Makefile": "include mk/*.mk\n", "mk/tests.mk": "test-changed:\n\t@true\n"}
ONLY_TEST = {"Makefile": "test:\n\t@true\n"}
NO_TARGETS = {"Makefile": "build:\n\t@true\n"}
NO_MAKEFILE = {"README.md": "bez make\n"}
DETECTED_LINE = "Źródło polecenia testów ticketu: wykryte z Makefile.\n"
DETECTED_CLOSE_LINE = "Źródło polecenia pełnych testów: wykryte z Makefile.\n"


def load_master():
    return MASTER_FRAGMENTS


class MasterFragmentsTest(unittest.TestCase):
    """Plasma-tide bez kontraktu: tekst polityki testów identyczny z master; jedyna różnica to linia źródła."""

    def test_reference_was_generated_from_master_with_the_same_base(self):
        data = load_master()
        self.assertEqual(data["master"], MASTER)
        self.assertEqual(data["base"], BASE)
        self.assertEqual(sorted(data["fragments"]), ["changed", "legacy"])

    def check(self, variant, texts):
        expected = load_master()["fragments"][variant]
        lines = {kind: DETECTED_CLOSE_LINE if kind == "close" else DETECTED_LINE for kind in texts}
        for kind, text in texts.items():
            with self.subTest(variant=variant, kind=kind):
                # Jedyna dopuszczona różnica: nowa linia źródła — dokładnie raz, tuż przed fragmentem polityki.
                self.assertEqual(text.count(lines[kind]), 1)
                name = "close-tests" if kind == "close" else f"{kind}-tests"
                self.assertIn(lines[kind] + expected[name], text)
        stripped = {kind: text.replace(lines[kind], "") for kind, text in texts.items()}
        self.assertEqual(fragments(stripped), expected)

    def test_cards_without_policy_keep_master_text(self):
        for variant, files in VARIANTS.items():
            self.check(variant, cards(handoff, tree(self, files)))

    def test_cards_from_effective_tests_without_contract_keep_master_text(self):
        for variant, files in VARIANTS.items():
            worktree = tree(self, files)
            policy = effective_tests(None, worktree, False, BASE)
            self.assertEqual(policy.source, testpolicy.DETECTED)
            self.assertEqual(policy.legacy, variant == "legacy")
            self.check(variant, cards(handoff, worktree, policy=policy, close_policy=policy))

    def test_review_bullets_identical_and_legacy_requires_full_make_test(self):
        expected = load_master()["fragments"]
        for variant, files in VARIANTS.items():
            with self.subTest(variant=variant):
                text = cards(handoff, tree(self, files))["review"]
                self.assertIn(expected[variant]["review-bullets"], text)
        self.assertIn("sprawdź w notce wynik pełnego `make test`", expected["legacy"]["review-bullets"])

    def test_runner_repository_keeps_rerun_recipe_and_conditions(self):
        text = cards(handoff, tree(self, CHANGED))["ticket"]
        self.assertIn(handoff.RERUN_MODULES, text)
        for phrase in ("ten sam HEAD, czysty `git status`", "udane ponowienie jej nie zastępuje",
                       "porażka pierwszego\nprzebiegu zostaje wynikiem"):
            self.assertIn(phrase, text)


class DetectionRuleTest(unittest.TestCase):
    """Tanie przypadki reguły wykrycia (cele badane niezależnie) i kontraktu."""

    def policy(self, files, contract=None, approved=False, base=BASE):
        return effective_tests(contract, tree(self, files), approved, base)

    def test_only_test_changed_gives_changed_detected_and_full_from_rules(self):
        policy = self.policy(ONLY_CHANGED)
        self.assertEqual((policy.changed, policy.changed_source), (f"make test-changed BASE={BASE}", "detected"))
        self.assertEqual((policy.full, policy.full_source, policy.legacy), ("", "rules", False))
        close = handoff.close_card(card_id="K", title="t", description="d", worktree="/w", out_dir="/o",
                                   tickets=[], manual=False, policy=policy)
        self.assertNotIn("make test`", close)
        self.assertIn(handoff.RULES_FULL, close)

    def test_only_test_is_legacy(self):
        policy = self.policy(ONLY_TEST)
        self.assertEqual((policy.changed, policy.full, policy.legacy), ("make test", "make test", True))

    def test_no_target_and_no_makefile_fall_back_to_rules(self):
        for files in (NO_TARGETS, NO_MAKEFILE):
            with self.subTest(files=files):
                policy = self.policy(files)
                self.assertEqual((policy.changed, policy.full, policy.source, policy.legacy), ("", "", "rules", False))

    def test_contract_with_only_one_field_is_completed_by_rules_not_detection(self):
        only_changed = self.policy(CHANGED, {"changed": "pytest -q --since {base}", "full": ""}, True)
        self.assertEqual((only_changed.changed_source, only_changed.full, only_changed.full_source),
                         ("profile", "", "rules"))
        only_full = self.policy(CHANGED, {"changed": "", "full": "pytest -q"}, True)
        self.assertEqual((only_full.changed, only_full.changed_source, only_full.full_source),
                         ("", "rules", "profile"))
        self.assertEqual(only_full.source, "profile")

    def test_approved_make_test_changed_on_branch_with_only_test_is_legacy(self):
        contract = {"changed": "make test-changed BASE={base}", "full": "make test"}
        policy = self.policy(ONLY_TEST, contract, True)
        self.assertTrue(policy.legacy)
        self.assertEqual((policy.changed, policy.full, policy.missing), ("make test", "make test", ()))
        text = handoff.ticket_tests(BASE, "/w", policy)
        self.assertIn("nie ma celu `test-changed` — obowiązuje dotychczasowa zasada pełnego `make test`", text)
        self.assertIn(handoff.OLD_BRANCH_TESTS, text)

    def test_approved_make_test_changed_on_branch_without_both_targets_goes_to_rules(self):
        contract = {"changed": "JOBS=4 make test-changed BASE={base}", "full": "make test"}
        policy = self.policy(NO_TARGETS, contract, True)
        self.assertEqual((policy.changed, policy.changed_source, policy.legacy), ("", "rules", False))
        self.assertEqual((policy.full, policy.full_source), ("", "rules"))
        self.assertEqual(policy.missing, ("changed", "full"))
        text = handoff.ticket_tests(BASE, "/w", policy)
        self.assertIn("nie ma celu `test-changed` ani `test` — obowiązuje zasada projektu", text)
        self.assertNotIn("uruchamia `", text)
        close = handoff.close_tests("/w", policy)
        self.assertIn("nie ma celu `test` — obowiązuje zasada projektu", close)
        self.assertIn(handoff.RULES_FULL, close)

    def test_approved_contract_wins_over_detection(self):
        policy = self.policy(CHANGED, DOTNET, True)
        self.assertEqual(policy.source, "profile")
        self.assertEqual(policy.changed, "dotnet test Foo.Tests --filter Changed=" + shlex.quote(BASE))

    def test_base_is_shell_quoted(self):
        policy = effective_tests(DOTNET, "", True, "a b")
        self.assertEqual(policy.changed, "dotnet test Foo.Tests --filter Changed='a b'")

    def test_unknown_base_stays_unexpanded_and_is_described(self):
        for files, contract in ((CHANGED, None), (NO_MAKEFILE, DOTNET)):
            with self.subTest(contract=contract):
                policy = self.policy(files, contract, contract is not None, base="")
                self.assertTrue(policy.base_pending)
                self.assertIn("{base}", policy.changed)
                text = handoff.ticket_tests("", "/w", policy)
                self.assertIn(f"uruchamia `{policy.changed}`", text)
                self.assertIn("`{base}` to przypięty punkt startu ticketu", text)
        self.assertFalse(self.policy(CHANGED).base_pending)

    def test_policy_serializes_for_step_event(self):
        data = self.policy(CHANGED).as_dict()
        self.assertEqual(data["changedSource"], "detected")
        self.assertEqual(data["basePending"], False)
        self.assertEqual(data["missing"], [])

    def test_approval_is_a_plain_input(self):
        # Decyzja nie sprawdza odcisku: tylko `approved is True` stosuje kontrakt.
        for approved in (False, None, "yes", 1):
            with self.subTest(approved=approved):
                self.assertNotEqual(self.policy(CHANGED, DOTNET, approved).source, "profile")


class ContractCardsTest(unittest.TestCase):
    """Kartki dla kontraktu innego niż `make` — zatwierdzonego i niezatwierdzonego."""

    def texts(self, files, contract, approved, error=""):
        worktree = tree(self, files)
        policy = effective_tests(contract, worktree, approved, BASE, error=error)
        return policy, cards(handoff, worktree, policy=policy, close_policy=policy)

    def test_approved_dotnet_contract(self):
        policy, texts = self.texts(CHANGED, DOTNET, True)
        command = "dotnet test Foo.Tests --filter Changed=" + shlex.quote(BASE)
        for kind in ("ticket", "fix", "review"):
            with self.subTest(kind=kind):
                self.assertIn(f"uruchamia `{command}`", texts[kind])
                self.assertIn("Źródło polecenia testów ticketu: zatwierdzony kontrakt testów projektu", texts[kind])
        self.assertIn("uruchom raz pełny `dotnet test Foo.sln`", texts["close"])
        self.assertIn("Źródło polecenia pełnych testów: zatwierdzony kontrakt", texts["close"])
        self.assertIn(f"`{command}`) przeszło dla oddawanych zmian", texts["review"])
        for kind, text in texts.items():
            with self.subTest(kind=kind):
                self.assertNotIn("make test", text)
                # Historia 34: kontrakt spoza `make` nie dostaje przepisu uruchamiacza Tide (testrerun).
                self.assertNotIn("tools/test_runner.py --modules", text)
                self.assertNotIn("unittest discover", text)
                self.assertIn(handoff.RERUN_GENERIC, text)

    def test_unapproved_contract_is_not_applied_and_is_reported(self):
        for files, source in ((CHANGED, "detected"), (NO_MAKEFILE, "rules")):
            policy, texts = self.texts(files, DOTNET, False)
            self.assertEqual((policy.source, policy.unapproved), (source, True))
            for kind, text in texts.items():
                with self.subTest(files=sorted(files), kind=kind):
                    self.assertNotIn("dotnet", text)
                    self.assertEqual(text.count(handoff.UNAPPROVED_LINE), 1)
                    self.assertNotIn("zatwierdzony kontrakt testów projektu (", text)

    def test_broken_profile_gives_cards_as_without_contract(self):
        for files in (CHANGED, LEGACY, NO_MAKEFILE):
            broken = self.texts(files, DOTNET, True, error="tests: zły napis w cudzysłowie")[1]
            plain = self.texts(files, None, True)[1]
            with self.subTest(files=sorted(files)):
                self.assertEqual({k: _without_paths(v) for k, v in broken.items()},
                                 {k: _without_paths(v) for k, v in plain.items()})
                self.assertNotIn(handoff.UNAPPROVED_LINE, broken["ticket"])


def _without_paths(text: str) -> str:
    return re.sub(r"/tmp\S*|" + re.escape(tempfile.gettempdir()) + r"\S*", "<tmp>", text)


class CardMatrixTest(unittest.TestCase):
    """Macierz: ticket, poprawki, review, domknięcie × zatwierdzony, niezatwierdzony, wykryty, brak, legacy."""

    CASES = {
        "approved": (CHANGED, DOTNET, True),
        "unapproved": (NO_MAKEFILE, DOTNET, False),
        "detected": (CHANGED, None, False),
        "none": (NO_MAKEFILE, None, False),
        "legacy": (LEGACY, None, False),
    }

    def test_no_variant_drops_test_requirement_or_full_run_at_close(self):
        for name, (files, contract, approved) in self.CASES.items():
            worktree = tree(self, files)
            policy = effective_tests(contract, worktree, approved, BASE)
            texts = cards(handoff, worktree, policy=policy, close_policy=policy)
            for kind, text in texts.items():
                with self.subTest(case=name, kind=kind):
                    self.assertEqual("zatwierdzony kontrakt testów projektu" in text, name == "approved")
                    self.assertEqual(handoff.UNAPPROVED_LINE in text, name == "unapproved")
                    self.assertRegex(text, r"Źródło polecenia (testów ticketu|pełnych testów): ")
                    if kind == "close":
                        self.assertIn("Przed napisaniem podsumowania uruchom raz pełn", text)
                        self.assertIn("nie zgłaszaj done", text)
                        self.assertIn("zapisz wynik error", text)
                        continue
                    self.assertIn("Testy ticketu (także po poprawkach)", text)
                    self.assertRegex(text, r"[Zz]apisz(\s+polecenie)? (i|oraz) wynik w notce|zapisz\npolecenie oraz wynik w notce")
                    if name != "legacy":
                        self.assertIn("nie jest kryterium oddania ticketu ani jego review", text)
            review = texts["review"]
            with self.subTest(case=name, kind="reviewer"):
                if name == "legacy":
                    self.assertIn("- sprawdź w notce wynik pełnego `make test`", review)
                    self.assertNotIn("nie żądaj osobnego", review)
                else:
                    self.assertIn("- sprawdź w notce", review)
                    self.assertIn("nie żądaj osobnego pełn", review)
                    self.assertNotIn("sprawdź w notce wynik pełnego", review)


class MakeTargetsTest(unittest.TestCase):
    def test_targets_from_makefile_and_mk_files(self):
        root = tree(self, {
            "Makefile": ".PHONY: test\nVAR := x\nOTHER ::= y\nPLAIN = z: q\ntest lint: deps\n\t@echo a: b\n"
                        "include mk/*.mk\n$(addprefix test-,$(SUITES)):\n\t@true\n",
            "mk/tests.mk": "test-changed::\n\t@true\n",
            "mk/notes.txt": "ignored:\n",
            "mk/.hidden.mk": "hidden:\n",
        })
        self.assertEqual(make_targets(root), [".PHONY", "lint", "test", "test-changed"])

    def test_missing_directory_and_missing_makefile(self):
        self.assertEqual(make_targets(""), [])
        self.assertEqual(make_targets("/nonexistent/tide-tests/worktree"), [])
        self.assertEqual(make_targets(tree(self, NO_MAKEFILE)), [])
        self.assertEqual(make_targets(tree(self, ONLY_TEST)), ["test"])

    def test_links_leaving_the_directory_are_skipped(self):
        outside = tree(self, {"evil.mk": "test-changed:\n", "Makefile": "test:\n",
                                          "mk/x.mk": "test-changed:\n"})
        file_link = tree(self, {"Makefile": "build:\n"})
        os.mkdir(os.path.join(file_link, "mk"))
        os.symlink(os.path.join(outside, "evil.mk"), os.path.join(file_link, "mk", "x.mk"))
        self.assertEqual(make_targets(file_link), ["build"])
        makefile_link = tree(self, {})
        os.symlink(os.path.join(outside, "Makefile"), os.path.join(makefile_link, "Makefile"))
        self.assertEqual(make_targets(makefile_link), [])
        dir_link = tree(self, {"Makefile": "build:\n"})
        os.symlink(os.path.join(outside, "mk"), os.path.join(dir_link, "mk"))
        self.assertEqual(make_targets(dir_link), ["build"])

    def test_link_inside_the_directory_is_read(self):
        root = tree(self, {"build/tests.mk": "test-changed:\n", "Makefile": "test:\n"})
        os.mkdir(os.path.join(root, "mk"))
        os.symlink(os.path.join(root, "build", "tests.mk"), os.path.join(root, "mk", "tests.mk"))
        self.assertEqual(make_targets(root), ["test", "test-changed"])

    def test_make_targets_is_the_only_makefile_reader_in_card_flow(self):
        package = os.path.dirname(testpolicy.__file__)
        readers = []
        for name in sorted(os.listdir(package)):
            if name.endswith(".py") and name != "testpolicy.py":
                with open(os.path.join(package, name), encoding="utf-8") as fh:
                    if re.search(r"[\"']Makefile[\"']|\.mk[\"']", fh.read()):
                        readers.append(name)
        self.assertEqual(readers, [])
        self.assertFalse(hasattr(handoff, "has_changed_target"))

    def test_make_target_of_command(self):
        self.assertEqual(testpolicy.make_target("make test-changed BASE={base}"), "test-changed")
        self.assertEqual(testpolicy.make_target("JOBS=4 make test"), "test")
        self.assertEqual(testpolicy.make_target("dotnet test Foo.sln"), "")
        self.assertEqual(testpolicy.make_target("make"), "")
        self.assertEqual(testpolicy.make_target("make 'unclosed"), "")


class MasterCompatibilityTest(unittest.TestCase):
    """Reguła zgodności do czasu #10: silnik woła kartki bez polityki i dostaje zachowanie z master."""

    def test_worktree_without_makefile_keeps_master_commands_without_source_line(self):
        for worktree in ("", "/nonexistent/tide-tests/w", tree(self, NO_MAKEFILE)):
            with self.subTest(worktree=worktree):
                policy = master_policy(worktree, BASE)
                self.assertEqual((policy.changed, policy.full), (f"make test-changed BASE={BASE}", "make test"))
                self.assertEqual(policy.source, testpolicy.COMPAT)
                texts = cards(handoff, worktree)
                self.assertNotIn("Źródło polecenia", "".join(texts.values()))
                self.assertIn(f"make test-changed BASE={BASE}", texts["ticket"])
                self.assertIn("uruchom raz pełny `make test`", texts["close"])

    def test_master_policy_matches_effective_tests_on_plasma_tide_trees(self):
        for files in (CHANGED, LEGACY):
            worktree = tree(self, files)
            self.assertEqual(master_policy(worktree, BASE), effective_tests(None, worktree, False, BASE))


if __name__ == "__main__":
    unittest.main()
