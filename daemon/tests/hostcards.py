"""PT-291 (K-38 F3): porównanie kartek hosta (HostRunner) z kartkami wygenerowanymi przez przypięty master.

- `MASTER_SHA` to commit master zintegrowany z gałęzią K-38 (`git merge-base` gałęzi integracji i master; scalony
  w 047c79b2 i dbb740b6). Ten sam commit jest wzorcem i bazą porównania; po kolejnej integracji przypnij go na nowo
  (`write_fixtures`).
- `load_master()` czyta moduły `cardflow/handoff.py` i `cardflow/testrerun.py` wprost z obiektów gita
  (`git show <sha>:<ścieżka>`) do osobnego pakietu w katalogu tymczasowym — bez checkoutu, worktree ani zmiany
  gałęzi użytkownika — i importuje je pod własną nazwą obok bieżącego `tide_daemon`.
- `render(module, root)` składa kartki ticket/fix/review/close (kartka + lista poleceń hosta, tak jak silnik
  dla HostRunnera) dla trzech klas projektów z F1 na tych samych syntetycznych worktree i tych samych danych.
- `normalize()` jest jawny i wąski: zakończenia linii, końcowe białe znaki linii i pliku oraz wyłącznie nazwane
  zmienne dane (`VARIABLES`: katalog tymczasowy testu, katalog zasobów Tide). Niczego więcej nie usuwa.
- `APPROVED` to zamknięta lista zatwierdzonych różnic K-38 niezależnych od runnera oraz różnic K-69 (polityka
  testów kroku, PT-279), z ticketem i commitem: każda jest dokładną zamianą fragmentu master na fragment gałęzi,
  wymaganą dokładnie raz w każdej kartce swoich rodzajów i klas projektów (`projects`; puste — wszystkie klasy).
- Integracja master → K-69 (scalenie f585b33c) zostawia `MASTER_SHA`: master od K-38 ma `runner_policy`, więc nie
  jest już wzorcem „sprzed pudełka”, którego wymaga `test_master_module_is_read_from_git_objects`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER_SHA = "a6efe28cdfe2e727bbc1dec085ac3aebfb76abe6"
# `resources.py` (poziom pakietu): handoff master od 321590c4 importuje `from .. import resources`.
MASTER_MODULES = ("daemon/tide_daemon/cardflow/handoff.py", "daemon/tide_daemon/cardflow/testrerun.py",
                  "daemon/tide_daemon/resources.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "host-cards-master")
PROVENANCE = os.path.join(FIXTURES, "provenance.json")

KINDS = ("ticket", "fix", "review", "close")
# Trzy klasy projektów z F1 (test_cardflow_box_switch.PROJECTS): plasma-tide (ekspozycja v1), projekt z profilem
# pudełka i projekt bez profilu. Worktree odtwarzają ich pliki istotne dla kartki: cel `test-changed` i runner
# z `--modules` (plasma-tide), projekt node bez make (zasada ogólna), stara gałąź z samym `make test`.
PROJECTS = {
    "plasma-tide": {
        "files": {"Makefile": "test:\n\t@true\n\ntest-changed:\n\t@true\n",
                  "tools/test_runner.py": 'ARGS = ["--modules"]\n'},
        "allowed": ["make test", "make test-changed *", "git status *", "python3 -m unittest *"],
        "denied": ["make install"],
    },
    "proj": {
        "files": {"package.json": '{"name": "proj", "scripts": {"test": "node --test"}}\n'},
        "allowed": ["npm test", "npx playwright test *"],
        "denied": ["npm publish"],
    },
    "bez-profilu": {
        "files": {"Makefile": "test:\n\t@true\n"},
        "allowed": [],
        "denied": [],
    },
}


def inputs(root: str, project: str) -> dict:
    """Te same dane wejściowe dla obu wersji; jedyną zmienną daną jest `root` (katalog tymczasowy)."""
    worktree = os.path.join(root, "wt", project)
    return {
        "card_id": "K-90", "title": "Eksport raportu", "project": project, "ticket": "PT-901",
        "description": "Dodaj eksport raportu do CSV.\n\nKryteria: plik ma nagłówek.",
        "branch": "ticket/pt-901-eksport", "card_branch": "card/k-90-eksport", "worktree": worktree,
        "base": "0123456789abcdef0123456789abcdef01234567",
        "steps": {"ticket": worktree + ".step", "fix": worktree + ".fix-1.step",
                  "review": worktree + ".review-1.step", "close": os.path.join(root, "wt", "card") + ".close-K-90-1.step"},
        "card_worktree": os.path.join(root, "wt", "card"),
        "diff": "diff --git a/export.py b/export.py\n+def export():\n+    return 'a,b'\n",
        "note": "Zrobione: eksport. make test-changed BASE=0123456 — OK.",
        "rules": "# Zasady\n- testy przed oddaniem\n",
        "review": "REQUEST_CHANGES: MAJOR export.py:2 brak nagłówka.",
        "tickets": [{"ticket": "PT-901", "result": "done"}],
        "allowed": PROJECTS[project]["allowed"], "denied": PROJECTS[project]["denied"],
    }


def make_worktrees(root: str) -> None:
    for project, spec in PROJECTS.items():
        base = os.path.join(root, "wt", project)
        for rel, text in spec["files"].items():
            path = os.path.join(base, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
    os.makedirs(os.path.join(root, "wt", "card"), exist_ok=True)


def render(handoff, root: str) -> dict[str, str]:
    """Kartki hosta `<projekt>/<rodzaj>`: kartka rodzaju + lista poleceń hosta (`command_policy` bez dodatków
    pudełka), w kolejności z silnika (`_launch`: kartka, potem polityka kroku). Te same wywołania na obu wersjach."""
    cards = {}
    for project in PROJECTS:
        data = inputs(root, project)
        worktree = data["worktree"]

        def policy(kind):
            return handoff.command_policy(data["allowed"], data["denied"], step_dir=data["steps"][kind])
        out = {kind: os.path.join(data["steps"][kind], "out") for kind in KINDS}
        cards[f"{project}/ticket"] = handoff.ticket_card(
            card_id=data["card_id"], title=data["title"], description=data["description"], project=project,
            ticket=data["ticket"], branch=data["branch"], card_branch=data["card_branch"], worktree=worktree,
            out_dir=out["ticket"], images=(), test_base=data["base"]) + policy("ticket")
        cards[f"{project}/fix"] = handoff.fix_card(
            card_id=data["card_id"], title=data["title"], description=data["description"], ticket=data["ticket"],
            round_no=1, branch=data["branch"], worktree=worktree, out_dir=out["fix"], review=data["review"],
            test_base=data["base"]) + policy("fix")
        cards[f"{project}/review"] = handoff.review_card(
            card_id=data["card_id"], title=data["title"], description=data["description"], ticket=data["ticket"],
            round_no=1, card_branch=data["card_branch"], ticket_branch=data["branch"], worktree=worktree,
            out_dir=out["review"], diff=data["diff"], note=data["note"], rules=data["rules"],
            test_base=data["base"]) + policy("review")
        # Domknięcie biegnie w worktree karty; klasa projektu wybiera pliki tego worktree (ten sam katalog
        # co krok ticketu, żeby zasady testów zależały od projektu).
        cards[f"{project}/close"] = handoff.close_card(
            card_id=data["card_id"], title=data["title"], description=data["description"], worktree=worktree,
            out_dir=out["close"], tickets=data["tickets"], manual=project == "proj") + policy("close")
    return cards


# --- normalizacja -----------------------------------------------------------------------------------------

ROOT_TOKEN = "<TMP>"
RESOURCES_TOKEN = "<RESOURCES>"
VARIABLES = (
    # (nazwa, opis) — jedyne zmienne dane podmieniane w treści kartki.
    ("tmp-root", "katalog tymczasowy przebiegu testu (worktree, katalogi kroków) → <TMP>"),
    # Od 321590c4 kartka ticketu odsyła do standardu planu ścieżką bezwzględną (`resources.instructions`);
    # obie wersje biorą zasoby z tego checkoutu (`REPO`), więc wzorzec nie może zależeć od jego położenia.
    ("tide-resources", "katalog zasobów Tide = checkout repozytorium (docs/, tools/) → <RESOURCES>"),
)


def normalize(text: str, root: str) -> str:
    """Zakończenia linii (CRLF/CR → LF), końcowe spacje i tabulatory linii, końcowe puste linie, a z danych
    tylko ścieżka katalogu tymczasowego i katalogu zasobów Tide. Nie usuwa linii, akapitów, poleceń, zasad testów,
    outside ani uprawnień."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n")).rstrip("\n") + "\n"
    if root:
        text = text.replace(os.path.realpath(root), ROOT_TOKEN).replace(root, ROOT_TOKEN)
    return text.replace(os.path.realpath(REPO) + os.sep, RESOURCES_TOKEN + os.sep)


# --- zatwierdzone różnice K-38 niezależne od runnera -----------------------------------------------------

@dataclass(frozen=True)
class Approved:
    ticket: str
    commits: tuple[str, ...]
    kinds: tuple[str, ...]
    master: str        # dokładny fragment z kartki master
    k38: str           # dokładny fragment w kartce gałęzi (wymagany dokładnie raz)
    projects: tuple[str, ...] = ()   # klasy projektów (`PROJECTS`), których dotyczy różnica; puste — wszystkie


DESK = ("Test wymagający prawdziwego pulpitu oznacz w notce w sekcji „Do sprawdzenia na pulpicie” (nagłówek, "
        "pod nim lista punktów: co zrobić i czego oczekiwać), nie proś o to użytkownika w trakcie. Tide zbierze te "
        "punkty do listy ręcznych testów w domknięciu karty.")
APPROVED = (
    # PT-183 (K-38, gotowe): lista „do sprawdzenia na pulpicie” w domknięciu karty, niezależna od pudełka.
    Approved("PT-183", ("1a059b60", "946faa0f"), ("ticket",),
             "(co zrobiono, jak sprawdzono, ograniczenia),\n- ostatnią czynnością",
             f"(co zrobiono, jak sprawdzono, ograniczenia),\n- {DESK}\n- ostatnią czynnością"),
    Approved("PT-183", ("1a059b60", "946faa0f"), ("review",),
             "- BLOCKER lub MAJOR oznacza REQUEST_CHANGES.\n",
             f"- BLOCKER lub MAJOR oznacza REQUEST_CHANGES,\n- {DESK}\n"),
    # PT-279 (K-69): jawna linia źródła polecenia testów. Klasy z `Makefile` (cele `test`/`test-changed`) mają
    # źródło `detected`; projekt bez `Makefile` (proj) ma źródło zgodności `compat`, bez linii — kartka jak master.
    Approved("PT-279", ("e92603f6",), ("ticket", "fix", "review"),
             "\nTesty ticketu (także po poprawkach): ",
             "\nŹródło polecenia testów ticketu: wykryte z Makefile.\nTesty ticketu (także po poprawkach): ",
             ("plasma-tide", "bez-profilu")),
    Approved("PT-279", ("e92603f6",), ("close",),
             "\nPrzed napisaniem podsumowania uruchom raz ",
             "\nŹródło polecenia pełnych testów: wykryte z Makefile.\nPrzed napisaniem podsumowania uruchom raz ",
             ("plasma-tide", "bez-profilu")),
)


def approved_for(name: str) -> list[Approved]:
    """Zatwierdzone różnice kartki `<projekt>/<rodzaj>`."""
    project, kind = name.rsplit("/", 1)
    return [change for change in APPROVED
            if kind in change.kinds and (not change.projects or project in change.projects)]


def expected(name: str, master_text: str) -> str:
    """Kartka master po zastosowaniu zatwierdzonych różnic tego rodzaju; brak fragmentu master to błąd."""
    text = master_text
    for change in approved_for(name):
        if text.count(change.master) != 1:
            raise AssertionError(f"{name}: fragment master zatwierdzonej różnicy {change.ticket} "
                                 f"nie występuje dokładnie raz")
        text = text.replace(change.master, change.k38)
    return text


def mismatch(name: str, master_text: str, k38_text: str) -> str:
    """Pusty napis, gdy znormalizowana kartka K-38 = master z zatwierdzonymi różnicami; inaczej opis różnicy."""
    want = expected(name, master_text)
    if want == k38_text:
        return ""
    import difflib
    diff = "".join(difflib.unified_diff(want.splitlines(True), k38_text.splitlines(True),
                                        f"master {MASTER_SHA[:8]}: {name}", f"K-38: {name}", n=1))
    return diff or f"{name}: różnica bez zmian linii"


# --- wersja master z obiektów gita ---------------------------------------------------------------------------

def git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True, text=True).stdout


def blob_ids(sha: str = MASTER_SHA) -> dict[str, str]:
    return {path: git("rev-parse", f"{sha}:{path}").strip() for path in MASTER_MODULES}


def load_master(target: str, sha: str = MASTER_SHA):
    """Moduł handoff z commitu `sha`: pliki z `git show` w pakiecie `<target>/pt291_master_<sha>/cardflow`."""
    name = f"pt291_master_{sha[:12]}"
    package = os.path.join(target, name)
    os.makedirs(os.path.join(package, "cardflow"), exist_ok=True)
    for path in (os.path.join(package, "__init__.py"), os.path.join(package, "cardflow", "__init__.py")):
        open(path, "w").close()
    for path in MASTER_MODULES:
        relative = path.removeprefix("daemon/tide_daemon/")
        with open(os.path.join(package, relative), "w", encoding="utf-8") as fh:
            fh.write(git("show", f"{sha}:{path}"))
    for module in (name, f"{name}.cardflow", f"{name}.resources"):
        sys.modules.pop(module, None)
    sys.modules.pop(f"{name}.cardflow.handoff", None)
    sys.modules.pop(f"{name}.cardflow.testrerun", None)
    sys.path.insert(0, target)
    try:
        spec = importlib.util.find_spec(f"{name}.cardflow.handoff")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(target)
    if os.path.realpath(module.__file__) != os.path.realpath(os.path.join(package, "cardflow", "handoff.py")):
        raise AssertionError(f"wczytano obcy moduł handoff: {module.__file__}")
    return module


def master_cards(root: str) -> dict[str, str]:
    """Świeżo wygenerowane i znormalizowane kartki master dla danych z `inputs` (bez plików wzorca)."""
    # Master od 321590c4 czyta zasoby Tide (docs/, tools/) obok modułu; wczytany z obiektów gita leży w katalogu
    # tymczasowym, więc wskazujemy mu to samo repozytorium, z którego czyta bieżący kod.
    previous = os.environ.get("TIDE_RESOURCES_DIR")
    os.environ["TIDE_RESOURCES_DIR"] = REPO
    try:
        with tempfile.TemporaryDirectory(prefix="pt291-master-") as target:
            module = load_master(target)
            return {name: normalize(text, root) for name, text in render(module, root).items()}
    finally:
        if previous is None:
            os.environ.pop("TIDE_RESOURCES_DIR", None)
        else:
            os.environ["TIDE_RESOURCES_DIR"] = previous


def fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name.replace("/", "--") + ".txt")


def write_fixtures(root: str) -> None:
    """Zapis wzorców z master (tylko wywołanie jawne, np. z build/; testy nigdy nie zapisują wzorców)."""
    os.makedirs(FIXTURES, exist_ok=True)
    make_worktrees(root)          # te same syntetyczne worktree co w testach (klasa projektu wybiera zasady testów)
    cards = master_cards(root)
    for name, text in cards.items():
        with open(fixture_path(name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    provenance = {
        "source": "git show <sha>:<ścieżka> (bez checkoutu), render() z daemon/tests/hostcards.py",
        "masterSha": MASTER_SHA,
        "why": "merge-base gałęzi integracji K-38 i master; master scalony do integracji K-38 w 047c79b2 i dbb740b6",
        "blobs": blob_ids(),
        "cards": sorted(cards),
        "normalization": [name for name, _ in VARIABLES] + ["line-endings", "trailing-whitespace"],
        "regenerate": "daemon/tests/hostcards.py: write_fixtures(<katalog tymczasowy>)",
    }
    with open(PROVENANCE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(provenance, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def read_fixture(name: str) -> str:
    with open(fixture_path(name), encoding="utf-8", newline="") as fh:
        return fh.read()


def card_names() -> list[str]:
    return [f"{project}/{kind}" for project in PROJECTS for kind in KINDS]
