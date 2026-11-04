"""Kontekst kroku opiekuna (K-37, PT-153): katalog `<krok>/in/context/` budowany przez Tide przy starcie kroku.

Publiczny interfejs:
- `build(problem, st, events, *, card, card_dir, worktree, context_dir, step)` — zapisuje pliki kontekstu i zwraca listę
  `[{path, title, trimmed}]` (ścieżki bezwzględne, w kolejności rejestru) do kartki opiekuna,
- `collect(...)` — to samo bez zapisu (pliki w pamięci, `{ścieżka względna: tekst}`); dla testów i rozmowy z opiekunem,
- `SOURCES` — rejestr źródeł: wyzwalacz → lista `Source(name, build)`; `"*"` — źródła każdego problemu. Kolejne
  tickety dopisują własne źródła pod swoim wyzwalaczem (np. błędy sprawdzania formatu planu), bez edycji wspólnych
  gałęzi; problem dostaje źródła `"*"` i wszystkich swoich wyzwalaczy (`problem["triggers"]`).

`Source.build(ctx)` zwraca listę `File`: `text` (treść pliku) albo `items` (zdarzenia od najstarszego, przycinane
przy limicie) z nagłówkiem `head`. Źródło czyta pliki wyłącznie przez `ctx.read_text` / `ctx.read_tail`.

Granice bezpieczeństwa:
- maskowanie wyłącznie `faults.mask` — każdy tekst i każde zdarzenie przed liczeniem rozmiaru (w JSON — każdy klucz
  i każda wartość osobno; pole, które `faults.mask` uznaje za sekretne na próbce `nazwa=x`, traci całą wartość,
  też liczbową, a plik pozostaje poprawnym JSON-em); końcówka `step.log` zaczyna się od pełnej linii
  (urwany sekret nie omija wzorców); tytuły są maskowane, a ścieżka zawierająca sekret dostaje unikalny alias
  jeszcze przed zapisem (sekret wykrywany także w oryginale przed sanitacją nazwy),
- tylko pliki z katalogu karty w tablicy (`flow/<K-…>/`), worktree kroku opiekuna oraz worktree i katalogów kroków
  problemu z dziennika; korzeń nie może być dowiązaniem (`lstat`, tożsamość `st_dev`/`st_ino` sprawdzana przy
  otwarciu), a każdy składnik ścieżki poniżej korzenia jest otwierany względem poprzedniego z `O_NOFOLLOW` —
  dowiązanie (pliku, katalogu albo całego katalogu kroku) daje dopisek „pominięto”, nie treść; migawki dokumentów
  Tide pochodzą z dokumentów działającej usługi (`tide-docs/` obok pakietu albo repozytorium Tide) — osobny korzeń
  tylko dla stałych nazw w `_docs`,
- twardy limit `MAX_BYTES` (400 kB) na całość: najpierw przycinane są najstarsze zdarzenia (dziennik, potem
  dzienniki kroków, potem historia) z dopiskiem o przycięciu, potem końcówki największych plików, a pliki, z których
  zostałby sam dopisek, wypadają w całości (jeden zbiorczy `przycieto.md` z zarezerwowanym miejscem).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime

from . import faults
from .labels import result_label, step_name

MAX_BYTES = 400_000
JOURNAL_EVENTS = 200
LOG_EVENTS = 300
FILE_READ_MAX = MAX_BYTES                 # więcej z jednego pliku i tak nie zmieści się w kontekście
LOG_TAIL_BYTES = faults.LOG_TAIL_BYTES
CONTEXT_DIR = "context"
TRIM_NOTE = "(przycięto {n} najstarszych zdarzeń — limit kontekstu opiekuna {kb} kB)"
CUT_NOTE = "\n… (koniec pliku przycięty — limit kontekstu opiekuna {kb} kB)\n"
READ_CUT_NOTE = "… (dalsza część pliku pominięta — limit odczytu {kb} kB, bez urwanej ostatniej linii)\n"
DROPPED_PATH = "przycieto.md"
DROPPED_NOTE = "(pominięto {n} plików kontekstu w całości — limit kontekstu opiekuna {kb} kB)\n"
SKIPPED = "(pominięto: {why})"
DOCS = (("docs/agent-plan-standard.md", "docs/agent-plan-standard.md", "standard planu i kontrakt kroku (migawka)"),
        ("docs/sandbox-and-permissions.md", "docs/sandbox-and-permissions.md",
         "piaskownica i uprawnienia kroków (migawka)"))
AGENTS_SECTION = "Przepływ karty"
# Katalog nad pakietem `tide_daemon`: `daemon/` w repozytorium albo `$XDG_DATA_HOME/tide/daemon` po instalacji.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIDE_DOCS_DIRS = (os.path.join(_PACKAGE_PARENT, "tide-docs"), os.path.dirname(_PACKAGE_PARENT))


# --- pliki i źródła ------------------------------------------------------------------------------

@dataclass
class File:
    """Plik kontekstu: `path` względna w `in/context/`, `title` do kartki; `text` albo `items` (zdarzenia od
    najstarszego) z nagłówkiem `head`. `trim` — kolejność przycinania (mniejsza — wcześniej; None — bez przycinania
    zdarzeń)."""
    path: str
    title: str
    text: str = ""
    items: list = field(default_factory=list)
    head: str = ""
    trim: int | None = None
    trimmed: int = 0
    cut: bool = False
    masked_json: bool = False   # klucze i wartości JSON już zamaskowane (`faults.mask_json`, też `faults.mask`)

    def render(self) -> str:
        if not self.items and not self.trimmed:
            return self.text
        note = TRIM_NOTE.format(n=self.trimmed, kb=MAX_BYTES // 1000) + "\n" if self.trimmed else ""
        return self.head + note + "".join(item + "\n" for item in self.items)


@dataclass(frozen=True)
class Source:
    name: str
    build: object       # (ctx) -> list[File]


@dataclass
class Ctx:
    problem: dict
    st: object
    card: str
    card_dir: str
    worktree: str
    env: dict | None = None
    roots: tuple = ()
    step: str = ""          # bieżący krok opiekuna (bez niego w „poprzednich działaniach”)
    events: list = field(default_factory=list)      # surowe zdarzenia dziennika karty (`Journal.read`)
    tide_docs: str = ""     # katalog dokumentów Tide działającej usługi (`tide_docs_dir`)

    # Kroki problemu: łańcuch `parentStep` problemu i (gdy problem ma ticket) wszystkie kroki tego ticketu.
    def steps(self) -> list[str]:
        from .caretaker import chain_root
        root, ticket = self.problem.get("root") or "", self.problem.get("ticket") or ""
        out = []
        for name, step in self.st.steps.items():
            if (root and chain_root(self.st, name) == root) or (ticket and step.get("ticket") == ticket):
                out.append(name)
        return out

    def read_text(self, path: str, *, limit: int = FILE_READ_MAX) -> str | None:
        """Tekst pliku z korzeni kontekstu (początek, najwyżej `limit` bajtów); None — brak, dowiązanie albo plik
        spoza korzeni."""
        data = _read(path, self.roots, limit=limit, tail=False)
        return None if data is None else data.decode("utf-8", errors="replace")

    def read_tail(self, path: str, *, limit: int = LOG_TAIL_BYTES) -> list[str] | None:
        """Linie końcówki pliku (najwyżej `limit` bajtów) z korzeni kontekstu; None — jak `read_text`."""
        data = _read(path, self.roots, limit=limit, tail=True)
        return None if data is None else data.decode("utf-8", errors="replace").splitlines()

    def why_not(self, path: str) -> str:
        """Powód pominięcia pliku do dopisku w kontekście (bez treści)."""
        try:
            info = os.lstat(path)
        except OSError:
            return "brak pliku"
        if _link_below(path, self.roots):
            return "plik spoza katalogu karty i worktree"
        if stat.S_ISLNK(info.st_mode):
            return "dowiązanie symboliczne"
        return "plik nie jest zwykłym plikiem" if not stat.S_ISREG(info.st_mode) else "nie da się odczytać pliku"


def _history(ctx: Ctx) -> list[File]:
    from .history import build as history_build
    items = []
    for entry in history_build(ctx.st, []):
        when = _when(entry.get("ts"))
        who = entry.get("actor") or ""
        line = f"- {when} · {entry.get('kindLabel') or ''} · {entry.get('text') or ''}" + (f" ({who})" if who else "")
        for attempt in entry.get("attempts") or []:
            if len(entry["attempts"]) > 1:
                line += f"\n  - próba {int(attempt.get('attempt') or 0) + 1}: {attempt.get('step')} · " \
                        f"{attempt.get('executor') or '?'} · {attempt.get('resultLabel') or ''}"
        items.append(line)
    return [File("history.md", "historia karty po ludzku (od najstarszego wpisu)", items=items, trim=2,
                 head=f"# Historia karty {ctx.card}\n\n")]


def _journal(ctx: Ctx) -> list[File]:
    events = ctx.events[-JOURNAL_EVENTS:]
    items = [json.dumps(faults.mask_json(event, env=ctx.env), ensure_ascii=False) for event in events]
    return [File("journal.jsonl", f"ostatnie {JOURNAL_EVENTS} zdarzeń dziennika karty (JSON Lines, od najstarszego)",
                 items=items, trim=0, masked_json=True)]


def _plan(ctx: Ctx) -> list[File]:
    """Plan i kartki ticketów z katalogu karty (`plan/plan.md`, `plan/tickets/*.md`) oraz kartka kroku problemu."""
    out = []
    plan_dir = os.path.join(ctx.card_dir, "plan")
    for name in ("plan.md", "source.md"):
        text = ctx.read_text(os.path.join(plan_dir, name))
        if text is not None:
            out.append(File(f"plan/{name}", "plan karty" if name == "plan.md" else "źródło planu karty", text=text))
    tickets = os.path.join(plan_dir, "tickets")
    for name in _listdir(tickets, ctx.roots):
        if name.endswith(".md"):
            text = ctx.read_text(os.path.join(tickets, name))
            if text is not None:
                out.append(File(f"plan/tickets/{name}", f"kartka ticketu {name[:-3]}", text=text))
    if not out:
        out.append(File("plan/README.md", "plan karty", text="Karta nie ma planu w katalogu karty (wejście „Od razu” "
                                                              "albo plan jeszcze niezatwierdzony).\n"))
    target = ctx.st.steps.get(ctx.problem.get("step") or "") or {}
    if target.get("stepDir"):
        text = ctx.read_text(os.path.join(target["stepDir"], "in", "prompt.txt"))
        if text is not None:
            out.append(File(f"steps/{target['step']}/prompt.txt", f"kartka kroku problemu {target['step']}",
                            text=text))
    return out


STEP_FILES = ("result.json", "note.md", "review.md", "handoff.md")


def _step_files(ctx: Ctx, name: str, defaults: tuple = STEP_FILES) -> list[tuple[str, str]]:
    """Pliki archiwum kroku: stałe nazwy i notki z `notes`/`kept` wyniku oraz zdarzenia zakończenia.

    Dodatkowa notka musi być nazwą pliku Markdown bez składników ścieżki; także wewnątrz archiwum odczyt
    przechodzi przez `ctx.read_text`, więc dowiązanie nie pozwala odczytać pliku spoza kroku.
    """
    folder = os.path.join(ctx.card_dir, "steps", name)
    result = ctx.read_text(os.path.join(folder, "result.json"))
    try:
        data = json.loads(result) if result is not None else {}
    except (ValueError, RecursionError):
        data = {}
    entries = list(defaults)
    for source in (data, ctx.st.finished.get(name) or {}):
        if not isinstance(source, dict):
            continue
        for key in ("notes", "kept"):
            notes = source.get(key)
            if not isinstance(notes, list):
                continue
            entries += [entry for entry in notes if isinstance(entry, str) and entry.endswith(".md")
                        and "/" not in entry and "\\" not in entry]
    out = []
    for entry in dict.fromkeys(entries):
        text = result if entry == "result.json" else ctx.read_text(os.path.join(folder, entry))
        if text is not None:
            out.append((entry, text))
    return out


def _steps(ctx: Ctx) -> list[File]:
    """Notki, review, `plan-review.json` i końcówka `out/step.log` (po ludzku) każdego kroku problemu."""
    out = []
    for name in ctx.steps():
        step = ctx.st.steps.get(name) or {}
        for entry, text in _step_files(ctx, name):
            out.append(File(f"steps/{name}/{entry}", f"{entry} kroku {name}", text=text))
        step_dir = step.get("stepDir") or ""
        if not step_dir:
            continue
        review = os.path.join(step_dir, "out", "plan-review.json")
        text = ctx.read_text(review)
        if text is not None:
            out.append(File(f"steps/{name}/plan-review.json", f"plan-review.json kroku {name}", text=text))
        log = os.path.join(step_dir, "out", "step.log")
        lines = ctx.read_tail(log)
        head = f"# Dziennik kroku {name} ({step_name(step.get('kind'), step.get('ticket'))}, " \
               f"{step.get('executor') or '?'}): ostatnie {LOG_EVENTS} zdarzeń po ludzku\n\n"
        if lines is None:
            if os.path.lexists(log):
                out.append(File(f"steps/{name}/step-log.md", f"dziennik kroku {name}",
                                text=head + SKIPPED.format(why=ctx.why_not(log)) + "\n"))
            continue
        items = [line for line in faults.excerpt(lines, ctx.env, limit=LOG_EVENTS).splitlines() if line]
        out.append(File(f"steps/{name}/step-log.md", f"końcówka dziennika kroku {name} (out/step.log, po ludzku)",
                        items=items, head=head, trim=1))
    return out


def _attempts(ctx: Ctx) -> list[File]:
    """Poprzednie próby kroków problemu i poprzednie działania (diagnozy) opiekuna przy tym samym problemie."""
    st, ident = ctx.st, ctx.problem.get("id") or ""
    lines = [f"# Poprzednie próby i diagnozy problemu {ident}", "", "## Próby kroków problemu", ""]
    names = ctx.steps()
    for name in names:
        step, done = st.steps.get(name) or {}, st.finished.get(name) or {}
        found = faults.of_finished(done) if done.get("result") == "error" else {}
        result = done.get("result") or ""
        line = (f"- {_when(step.get('ts'))} · {name} · {step_name(step.get('kind'), step.get('ticket'))} · "
                f"{step.get('executor') or '?'} · próba {int(step.get('attempt') or 0) + 1} · "
                f"{result_label(result) if result else 'trwa'}")
        if done.get("reason"):
            line += f" · powód: {done['reason']}"
        if found:
            line += f" · czyja wina: {faults.FAULT_LABELS.get(found['fault'], found['fault'])}"
        decision = st.step_decisions.get(name) or {}
        if decision:
            line += f" · decyzja: {decision.get('choice') or '?'}" + (" (automat)" if decision.get("auto") else "")
        lines.append(line)
    if not names:
        lines.append("(problem bez kroków)")
    lines += ["", "## Poprzednie działania opiekuna przy tym problemie", ""]
    runs = [run for name, run in st.caretaker_runs.items() if run.get("problem") == ident and name != ctx.step]
    if not runs:
        lines.append("(to pierwsze działanie opiekuna przy tym problemie)")
    for run in runs:
        name = run.get("step") or ""
        outcome = st.caretaker_outcomes.get(name) or {}
        finished = (st.finished.get(name) or {}).get("outcome") or {}
        lines += [f"### Działanie {run.get('n') or '?'}: {name} ({_when(run.get('ts'))})", ""]
        if outcome:
            from .caretaker import history_text
            lines.append(f"- Wynik: {history_text(outcome) or outcome.get('type')}")
            for key, label in (("summary", "Streszczenie"), ("recommendation", "Rekomendacja"),
                               ("optionId", "Polecona możliwość"), ("error", "Błąd"), ("code", "Kod")):
                if outcome.get(key):
                    lines.append(f"- {label}: {outcome[key]}")
        elif finished:
            lines.append(f"- Wynik kroku: {'akcja ' + finished.get('action') if finished.get('ok') else finished.get('error') or '?'}")
        else:
            lines.append("- Bez wyniku (krok trwa albo nie wystartował).")
        for entry, text in _step_files(ctx, name, ("note.md", "result.json")):
            if entry.endswith(".json"):
                text = _json_text(text, ctx.env)
            lines += ["", f"#### {entry}", "", text.strip()]
        lines.append("")
    return [File("attempts.md", "poprzednie próby kroków i diagnozy opiekuna przy tym problemie",
                 text="\n".join(lines) + "\n")]


def tide_docs_dir() -> str:
    """Katalog z dokumentami Tide działającej usługi: `tide-docs/` obok pakietu (kopia z `install.sh`) albo
    repozytorium Tide przy uruchomieniu ze źródeł; "" — brak."""
    for base in TIDE_DOCS_DIRS:
        if os.path.isfile(os.path.join(base, DOCS[0][0])):
            return base
    return ""


def _doc_text(ctx: Ctx, rel: str) -> tuple[str | None, str]:
    """Treść dokumentu Tide i ścieżka do dopisku: najpierw dokumenty działającej usługi (`ctx.tide_docs`, tylko
    stałe nazwy z `DOCS` i `AGENTS.md`, osobny korzeń niedostępny innym źródłom), potem worktree kroku opiekuna."""
    if ctx.tide_docs:
        root = _root(ctx.tide_docs)
        if root is not None:
            data = _read(os.path.join(root.path, rel), (root,), limit=FILE_READ_MAX, tail=False)
            if data is not None:
                return data.decode("utf-8", errors="replace"), ""
    path = os.path.join(ctx.worktree, rel)
    return ctx.read_text(path), path


def _docs(ctx: Ctx) -> list[File]:
    """Migawki dokumentów Tide (`_doc_text`); brak albo dowiązanie — dopisek bez treści."""
    out = []
    for rel, target, title in DOCS:
        text, path = _doc_text(ctx, rel)
        out.append(File(target, title, text=text if text is not None else
                        f"# {rel}\n\n" + SKIPPED.format(why=ctx.why_not(path)) + "\n"))
    text, path = _doc_text(ctx, "AGENTS.md")
    section = _section(text, AGENTS_SECTION) if text is not None else None
    if section is None:
        section = f"## {AGENTS_SECTION}\n\n" + SKIPPED.format(
            why=ctx.why_not(path) if text is None else "AGENTS.md nie ma tej sekcji") + "\n"
    out.append(File("docs/AGENTS-przeplyw-karty.md", f"sekcja „{AGENTS_SECTION}” z AGENTS.md (migawka)",
                    text=section))
    return out


SOURCES: dict[str, list[Source]] = {
    "*": [Source("history", _history), Source("journal", _journal), Source("plan", _plan),
          Source("steps", _steps), Source("attempts", _attempts), Source("docs", _docs)],
}


# --- budowa --------------------------------------------------------------------------------------

def sources_for(problem: dict) -> list[Source]:
    triggers = list(dict.fromkeys([*(problem.get("triggers") or []), problem.get("trigger") or ""]))
    picked = list(SOURCES.get("*", []))
    for trigger in triggers:
        if trigger and trigger != "*":
            picked += SOURCES.get(trigger, [])
    return picked


def roots_for(problem: dict, st, *, card_dir: str, worktree: str) -> tuple:
    """Korzenie, z których wolno czytać: katalog karty, worktree opiekuna oraz worktree i katalogi kroków problemu
    (z dziennika, który pisze tylko Tide). `$HOME`, repozytorium projektu ani inne karty nie są korzeniami."""
    ctx = Ctx(problem, st, "", card_dir, worktree)
    roots = [card_dir, worktree]
    for name in ctx.steps():
        step = st.steps.get(name) or {}
        roots += [step.get("worktree") or "", step.get("stepDir") or ""]
    # Korzeń będący dowiązaniem (np. katalog kroku podmieniony na dowiązanie do `$HOME`) odpada w `_root`.
    return tuple(dict.fromkeys(root for root in map(_root, roots) if root is not None))


def collect(problem: dict, st, events: list, *, card: str, card_dir: str, worktree: str, step: str = "",
            env: dict | None = None, tide_docs: str | None = None) -> list[File]:
    """Pliki kontekstu w pamięci: zamaskowane i przycięte do `MAX_BYTES`. `st` — `fold(events)`, `events` — surowe
    zdarzenia dziennika karty, `step` — bieżący krok opiekuna, `tide_docs` — katalog dokumentów Tide (None —
    `tide_docs_dir()`)."""
    ctx = Ctx(problem, st, card, card_dir, worktree, env,
              roots_for(problem, st, card_dir=card_dir, worktree=worktree), step, list(events),
              tide_docs_dir() if tide_docs is None else tide_docs)
    files: list[File] = []
    seen = set()
    secret_paths = set()
    for source in sources_for(problem):
        try:
            made = source.build(ctx) or []
        except Exception as exc:        # noqa: BLE001 — błąd jednego źródła nie blokuje startu opiekuna
            made = [File(f"errors/{_safe_name(source.name)}.md", f"błąd źródła {source.name}",
                         text=f"Źródło kontekstu „{source.name}” nie dało plików: {exc}\n")]
        for item in made:
            rel = _safe_rel(item.path)
            if not rel or rel in seen:
                continue
            seen.add(rel)
            # Sprawdzamy też oryginał: `_safe_rel` skraca nazwy i zmienia białe znaki, co może urwać sekret.
            if faults.mask(item.path, env) != item.path:
                secret_paths.add(rel)
            item.path = rel
            item.title = faults.mask(item.title, env)
            # JSON maskujemy po odkodowaniu kluczy i wartości, zanim sekwencje ucieczki zmienią granice sekretów.
            # Pozostały tekst w całości, zdarzenia każde osobno; zawsze przez `faults.mask`, przed limitem.
            item.text = (_json_text(item.text, env) if rel.endswith(".json") else faults.mask(item.text, env)) \
                if item.text else ""
            item.head = faults.mask(item.head, env) if item.head else ""
            if item.masked_json:
                item.items = [str(line) for line in item.items]
            else:
                item.items = [faults.mask(str(line), env) for line in item.items]
            files.append(item)
    # Nazwy notek i plików dodatkowych źródeł mogą zawierać sekrety. Alias musi być rzeczywistą nazwą pliku,
    # a nie tylko zamaskowaną ścieżką w kartce. Rezerwujemy też wszystkie późniejsze nazwy, by nie tracić plików.
    for number, item in enumerate(files, 1):
        if item.path not in secret_paths and faults.mask(item.path, env) == item.path:
            continue
        suffix = os.path.splitext(item.path)[1]
        suffix = suffix if suffix in (".md", ".txt", ".json", ".jsonl") else ".txt"
        alias = f"masked-{number}{suffix}"
        while alias in seen or faults.mask(alias, env) != alias:
            number += 1
            alias = f"masked-{number}{suffix}"
        seen.add(alias)
        item.path = alias
    _fit(files)
    return files


def build(problem: dict, st, events: list, *, card: str, card_dir: str, worktree: str, context_dir: str,
          step: str = "", env: dict | None = None, tide_docs: str | None = None) -> list[dict]:
    """Zapisuje kontekst w `context_dir` (wcześniejsza zawartość jest usuwana) i zwraca `[{path, title, trimmed}]`."""
    files = collect(problem, st, events, card=card, card_dir=card_dir, worktree=worktree, step=step, env=env,
                    tide_docs=tide_docs)
    if os.path.islink(context_dir):
        os.unlink(context_dir)
    shutil.rmtree(context_dir, ignore_errors=True)
    os.makedirs(context_dir, exist_ok=True)
    listed = []
    for item in files:
        target = os.path.join(context_dir, item.path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(item.render())
        listed.append({"path": target, "title": item.title, "trimmed": bool(item.trimmed or item.cut)})
    return listed


def total_bytes(files: list[File]) -> int:
    return sum(len(item.render().encode("utf-8")) for item in files)


def _fit(files: list[File]) -> None:
    """Twardy limit `MAX_BYTES`: najstarsze zdarzenia przycinanych plików (według `trim`), potem końcówki plików."""
    excess = total_bytes(files) - MAX_BYTES
    for item in sorted((f for f in files if f.trim is not None), key=lambda f: f.trim):
        while excess > 0 and item.items:
            before = len(item.render().encode("utf-8"))
            # Partia najstarszych zdarzeń, które pokrywają nadmiar (jedno przeliczenie na partię, nie na zdarzenie).
            dropped, freed = 0, 0
            for line in item.items:
                if freed >= excess:
                    break
                dropped += 1
                freed += len(line.encode("utf-8")) + 1
            item.items = item.items[dropped:]
            item.trimmed += dropped
            excess -= before - len(item.render().encode("utf-8"))
    if excess <= 0:
        return
    # Końcówki największych plików; plik, z którego nie zostałoby nic poza dopiskiem, wypada w całości. Na zbiorczy
    # dopisek o usuniętych plikach budżet jest zarezerwowany z góry, więc i z nim całość mieści się w `MAX_BYTES`.
    excess += len(DROPPED_NOTE.format(n=len(files), kb=MAX_BYTES // 1000).encode("utf-8"))
    note = CUT_NOTE.format(kb=MAX_BYTES // 1000)
    dropped = []
    for item in sorted(files, key=lambda f: -len(f.render().encode("utf-8"))):
        if excess <= 0:
            break
        data = item.render().encode("utf-8")
        keep = len(data) - excess - len(note.encode("utf-8"))
        if keep <= 0:
            dropped.append(item)
            excess -= len(data)
            continue
        text = data[:keep].decode("utf-8", errors="ignore") + note
        item.text, item.items, item.head, item.trimmed, item.cut = text, [], "", 0, True
        excess -= len(data) - len(text.encode("utf-8"))
    if dropped:
        gone = {id(item) for item in dropped}
        files[:] = [item for item in files if id(item) not in gone]
        files.append(File(DROPPED_PATH, "dopisek o plikach usuniętych przez limit kontekstu",
                          text=DROPPED_NOTE.format(n=len(dropped), kb=MAX_BYTES // 1000), cut=True))


# --- odczyt w granicach korzeni ------------------------------------------------------------------

@dataclass(frozen=True)
class Root:
    """Korzeń odczytu: ścieżka (bezwzględna, znormalizowana, bez rozwijania dowiązań) i tożsamość katalogu z chwili
    sprawdzenia (`st_dev`, `st_ino`)."""
    path: str
    dev: int
    ino: int


def _root(path: str) -> Root | None:
    """Korzeń tylko wtedy, gdy sama ścieżka jest katalogiem, a nie dowiązaniem (`lstat`): dowiązanie zastępujące
    katalog kroku albo worktree nie legalizuje swojego celu."""
    if not path or not os.path.isabs(path):
        return None
    path = os.path.normpath(path)
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    return Root(path, info.st_dev, info.st_ino)


def _root_of(path: str, roots: tuple) -> tuple[Root, list[str]] | None:
    """Najbliższy korzeń, pod którym ścieżka leży leksykalnie, i składniki ścieżki poniżej niego."""
    if not path or not os.path.isabs(path):
        return None
    path = os.path.normpath(path)
    best = None
    for root in roots:
        if path == root.path or path.startswith(root.path.rstrip(os.sep) + os.sep):
            if best is None or len(root.path) > len(best.path):
                best = root
    if best is None:
        return None
    rel = os.path.relpath(path, best.path)
    parts = [] if rel == "." else rel.split(os.sep)
    return None if ".." in parts else (best, parts)


def _inside_roots(path: str, roots: tuple) -> bool:
    return _root_of(path, roots) is not None


def _open(path: str, roots: tuple, *, directory: bool = False) -> int | None:
    """Deskryptor ścieżki z korzeni: korzeń otwierany bez podążania za dowiązaniem i porównywany z tożsamością
    z chwili sprawdzenia, potem każdy składnik względem poprzedniego (`dir_fd`) z `O_NOFOLLOW` — dowiązanie na
    dowolnym poziomie (także podmienione w trakcie) kończy odczyt. None — brak, dowiązanie albo plik spoza korzeni."""
    found = _root_of(path, roots)
    if found is None:
        return None
    root, parts = found
    base = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(root.path, base | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (root.dev, root.ino):
            os.close(fd)
            return None
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            flags = base | (os.O_DIRECTORY if directory or not last else getattr(os, "O_NONBLOCK", 0))
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError:
        os.close(fd)
        return None


def _link_below(path: str, roots: tuple) -> bool:
    """Czy któryś katalog między korzeniem a plikiem jest dowiązaniem (do dopisku „spoza”)."""
    found = _root_of(path, roots)
    if found is None:
        return True
    root, parts = found
    current = root.path
    for part in parts[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return True
    return False


def _read(path: str, roots: tuple, *, limit: int, tail: bool) -> bytes | None:
    """Zwykły plik w korzeniach kontekstu (`_open`: bez dowiązania na żadnym poziomie). Obie granice ograniczonego
    odczytu odrzucają urwaną linię — sekret przecięty granicą straciłby nazwę zmiennej albo resztę tokenu i nie
    przeszedłby przez wzorce `faults.mask`: odczyt zaczynający się w środku pliku (końcówka, `tail`) odrzuca pierwszą
    linię, a odczyt kończący się przed końcem pliku (początek pliku albo plik dopisywany w trakcie odczytu) —
    ostatnią, niepełną linię, z dopiskiem `READ_CUT_NOTE`."""
    fd = _open(path, roots)
    if fd is None:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            start = max(0, info.st_size - limit) if tail else 0
            fh.seek(start)
            data = fh.read(limit + 1)      # bajt ponad limit — czy odczyt kończy się przed końcem pliku
        more = len(data) > limit
        data = data[:limit]
        if tail and start > 0:
            cut = data.find(b"\n")
            data = data[cut + 1:] if cut >= 0 else b""
        if more:
            data = data[:data.rfind(b"\n") + 1] + READ_CUT_NOTE.format(kb=limit // 1000).encode("utf-8")
        return data
    except OSError:
        return None
    finally:
        os.close(fd)


def _listdir(folder: str, roots: tuple) -> list[str]:
    fd = _open(folder, roots, directory=True)
    if fd is None:
        return []
    try:
        return sorted(os.listdir(fd))
    except OSError:
        return []
    finally:
        os.close(fd)


# --- pomocnicze ----------------------------------------------------------------------------------

def _json_text(text: str, env: dict | None) -> str:
    """Maskowanie odkodowanych kluczy i wartości JSON; niepoprawny lub przycięty JSON daje dopisek o pominięciu."""
    try:
        return json.dumps(faults.mask_json(json.loads(text), env=env), ensure_ascii=False, indent=2) + "\n"
    except (ValueError, RecursionError):
        return SKIPPED.format(why="niepoprawny lub niepełny JSON") + "\n"


def _section(text: str, title: str) -> str | None:
    """Sekcja `## <title>` z Markdownu (do następnego nagłówka tego samego albo wyższego poziomu)."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if match and match.group(2) == title:
            level = len(match.group(1))
            end = len(lines)
            for later in range(index + 1, len(lines)):
                other = re.match(r"^(#{1,6})\s", lines[later])
                if other and len(other.group(1)) <= level:
                    end = later
                    break
            return "\n".join(lines[index:end]).rstrip() + "\n"
    return None


def _safe_rel(path: str) -> str:
    """Ścieżka względna wewnątrz `in/context/` (bez `..`, bez ścieżek bezwzględnych)."""
    parts = [_safe_name(part) for part in str(path or "").replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part in ("", "..") for part in parts):
        return ""
    return "/".join(parts)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\-]+", "-", str(name or "")).strip("-")
    return "" if name in ("", ".", "..") else name[:120]


def _when(value) -> str:
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(value)
        else:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError, TypeError):
        return "?"
