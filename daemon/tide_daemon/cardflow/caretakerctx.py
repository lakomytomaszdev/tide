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
- maskowanie wyłącznie `faults.mask` — każdy tekst i każde zdarzenie przed liczeniem rozmiaru (w JSON — każda wartość
  tekstowa razem z nazwą pola, więc `{"token": "…"}` też traci wartość, a plik pozostaje poprawnym JSON-em),
- tylko pliki z katalogu karty w tablicy (`flow/<K-…>/`), worktree kroku opiekuna oraz worktree i katalogów kroków
  problemu z dziennika; ścieżka po `realpath` musi leżeć w `realpath` swojego korzenia, sam plik nie może być
  dowiązaniem (`O_NOFOLLOW`) — dowiązanie w worktree do `$HOME` daje dopisek „pominięto”, nie treść,
- twardy limit `MAX_BYTES` (400 kB) na całość: najpierw przycinane są najstarsze zdarzenia (dziennik, potem
  dzienniki kroków, potem historia) z dopiskiem o przycięciu, na końcu — gdyby nadal brakowało miejsca — końcówki
  największych plików.
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
SKIPPED = "(pominięto: {why})"
DOCS = (("docs/agent-plan-standard.md", "docs/agent-plan-standard.md", "standard planu i kontrakt kroku (migawka)"),
        ("docs/sandbox-and-permissions.md", "docs/sandbox-and-permissions.md",
         "piaskownica i uprawnienia kroków (migawka)"))
AGENTS_SECTION = "Przepływ karty"


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
    masked_json: bool = False   # zdarzenia JSON zamaskowane już wartościami (`_mask_json`, też `faults.mask`)

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
        if stat.S_ISLNK(info.st_mode):
            return "dowiązanie symboliczne"
        if not _inside_roots(path, self.roots):
            return "plik spoza katalogu karty i worktree"
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
    items = [json.dumps(_mask_json(event, env=ctx.env), ensure_ascii=False) for event in events]
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


def _steps(ctx: Ctx) -> list[File]:
    """Notki, review, `plan-review.json` i końcówka `out/step.log` (po ludzku) każdego kroku problemu."""
    out = []
    for name in ctx.steps():
        step = ctx.st.steps.get(name) or {}
        kept = os.path.join(ctx.card_dir, "steps", name)
        for entry in STEP_FILES:
            text = ctx.read_text(os.path.join(kept, entry))
            if text is not None:
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
        for entry in ("note.md", "result.json"):
            text = ctx.read_text(os.path.join(ctx.card_dir, "steps", name, entry))
            if text is not None:
                lines += ["", f"#### {entry}", "", text.strip()]
        lines.append("")
    return [File("attempts.md", "poprzednie próby kroków i diagnozy opiekuna przy tym problemie",
                 text="\n".join(lines) + "\n")]


def _docs(ctx: Ctx) -> list[File]:
    """Migawki dokumentów Tide z worktree kroku opiekuna; brak albo dowiązanie — dopisek bez treści."""
    out = []
    for rel, target, title in DOCS:
        path = os.path.join(ctx.worktree, rel)
        text = ctx.read_text(path)
        out.append(File(target, title, text=text if text is not None else
                        f"# {rel}\n\n" + SKIPPED.format(why=ctx.why_not(path)) + "\n"))
    path = os.path.join(ctx.worktree, "AGENTS.md")
    text = ctx.read_text(path)
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
    return tuple(dict.fromkeys(os.path.realpath(r) for r in roots if r and os.path.isabs(r)))


def collect(problem: dict, st, events: list, *, card: str, card_dir: str, worktree: str, step: str = "",
            env: dict | None = None) -> list[File]:
    """Pliki kontekstu w pamięci: zamaskowane i przycięte do `MAX_BYTES`. `st` — `fold(events)`, `events` — surowe
    zdarzenia dziennika karty, `step` — bieżący krok opiekuna."""
    ctx = Ctx(problem, st, card, card_dir, worktree, env,
              roots_for(problem, st, card_dir=card_dir, worktree=worktree), step, list(events))
    files: list[File] = []
    seen = set()
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
            item.path = rel
            # Jedyne maskowanie: `faults.mask` — tekst w całości, zdarzenia każde osobno (przed liczeniem rozmiaru).
            item.text = faults.mask(item.text, env) if item.text else ""
            item.head = faults.mask(item.head, env) if item.head else ""
            if item.masked_json:
                item.items = [str(line) for line in item.items]
            else:
                item.items = [faults.mask(str(line), env) for line in item.items]
            files.append(item)
    _fit(files)
    return files


def build(problem: dict, st, events: list, *, card: str, card_dir: str, worktree: str, context_dir: str,
          step: str = "", env: dict | None = None) -> list[dict]:
    """Zapisuje kontekst w `context_dir` (wcześniejsza zawartość jest usuwana) i zwraca `[{path, title, trimmed}]`."""
    files = collect(problem, st, events, card=card, card_dir=card_dir, worktree=worktree, step=step, env=env)
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
    for item in sorted(files, key=lambda f: -len(f.render().encode("utf-8"))):
        if excess <= 0:
            break
        data = item.render().encode("utf-8")
        note = CUT_NOTE.format(kb=MAX_BYTES // 1000)
        keep = max(0, len(data) - excess - len(note.encode("utf-8")))
        text = data[:keep].decode("utf-8", errors="ignore") + note
        item.text, item.items, item.head, item.trimmed, item.cut = text, [], "", 0, True
        excess -= len(data) - len(text.encode("utf-8"))


# --- odczyt w granicach korzeni ------------------------------------------------------------------

def _inside_roots(path: str, roots: tuple) -> bool:
    if not path or not os.path.isabs(path):
        return False
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def _read(path: str, roots: tuple, *, limit: int, tail: bool) -> bytes | None:
    """Zwykły plik w korzeniach kontekstu: bez dowiązania na żadnym poziomie poniżej korzenia (`realpath` w korzeniu)
    i bez dowiązania na końcu (`O_NOFOLLOW`); po otwarciu jeszcze raz — czy to zwykły plik."""
    if not _inside_roots(path, roots):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            if tail:
                fh.seek(max(0, info.st_size - limit))
            return fh.read(limit)
    except OSError:
        return None
    finally:
        os.close(fd)


def _listdir(folder: str, roots: tuple) -> list[str]:
    if not _inside_roots(folder, roots) or os.path.islink(folder):
        return []
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


# --- pomocnicze ----------------------------------------------------------------------------------

def _mask_json(value, key: str = "", env: dict | None = None):
    """Zdarzenie dziennika z zamaskowanymi wartościami tekstowymi (`faults.mask` z nazwą pola jako `nazwa=wartość`,
    więc pole o sekretnej nazwie traci wartość); struktura JSON zostaje poprawna."""
    if isinstance(value, dict):
        return {k: _mask_json(v, str(k), env) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_json(v, key, env) for v in value]
    if not isinstance(value, str):
        return value
    if key:
        paired = faults.mask(f"{key}={value}", env)
        if paired.startswith(f"{key}="):
            return paired[len(key) + 1:]
    return faults.mask(value, env)


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
