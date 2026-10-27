"""Rdzeń wtyczki KRunnera: zapytanie → dopasowania, (dopasowanie, akcja) → uruchomienie.

Bez D-Bus i bez Qt — dane przychodzą przez funkcje podane w konstruktorze:
- index           — SessionIndex (historia sesji z 30 dni),
- worktrees       — funkcja() → lista worktrees jak z /Worktrees List() (projekty to główne katalogi),
- live            — funkcja() → bieżące sesje obszaru Agents (czy sesja ma otwarte okno),
- activate        — funkcja(id) → bool: przejście do okna żywej sesji (AgentMonitor.activate),
- codex           — Codex Desktop: obiekt z available() i open_thread(id) → bool (otwiera wątek
                    w aplikacji i podnosi jej okno) albo None,
- launchers       — Launchers: Konsole, Rider, wznowienie sesji.

Rodzaje dopasowań (id dopasowania):
- `session:<narzędzie>:<id-sesji>` — Uruchom: przejście do okna, jeśli sesja jeszcze działa,
  wątek z Codex Desktop otwarty w aplikacji, inaczej wznowienie w nowym oknie Konsoli
  w katalogu sesji,
- `project:<ścieżka>`  — główny katalog repozytorium z katalogu projektów → Konsole,
- `worktree:<ścieżka>` — worktree (szukany po gałęzi) → Konsole,
- `report:latest`      — „raport” (albo „rap”, „raport poranny”) → najnowszy raport poranny w przeglądarce
                        (oznaczony jako przeczytany, jak z widżetu),
- `sessions:window`    — „sesje” → okno „Tide Sesje” (tablica koordynatorów),
- `coordination:<id>`  — „sesje <słowa>” → okno „Tide Sesje” z tym koordynatorem (projekt, tytuł, etykieta).
Akcje dodatkowe: `terminal` (sam katalog w Konsoli, dla sesji) i `rider` (katalog w Riderze).

Zapytanie zaczynające się od „ai ” pokazuje tylko sesje. Zwykłe zapytanie ma co najmniej
MIN_QUERY znaków (tak samo ogranicza je plik .desktop wtyczki).
"""
import os
import threading
import time

from ..worktrees.launchers import RIDER, TERMINAL
from . import search
from .index import HISTORY_AGE

MIN_QUERY = 3
SESSIONS_PREFIX = "ai "
LIMITS = {"session": 8, "project": 5, "worktree": 6}
SESSIONS_ONLY_LIMIT = 20
REFRESH_AFTER = 10.0  # s — częściej indeks nie jest odświeżany, nawet przy każdym naciśnięciu klawisza

TOOL_NAMES = {"claude": "Claude", "codex": "Codex"}
TOOL_ICONS = {"claude": "utilities-terminal", "codex": "openai"}
PROJECT_ICON = "folder-git"
WORKTREE_ICON = "vcs-branch"
CATEGORIES = {"session": "Sesje agentów", "project": "Projekty", "worktree": "Worktrees", "report": "Raport poranny",
              "coordination": "Tide Sesje"}
SESSIONS_KEYWORD = "sesje"
SESSIONS_MATCH = "sessions:window"
SESSIONS_ICON = "tide-sessions"
COORDINATION_LIMIT = 6
REPORT_KEYWORD = "raport poranny"
REPORT_MATCH = "report:latest"
REPORT_ICON = "view-calendar-day"
ACTIONS = {
    TERMINAL: ("Otwórz katalog w terminalu", "utilities-terminal"),
    RIDER: ("Otwórz w Riderze", "jetbrains-rider"),
}

# Ważność kategorii w KRunnerze 6 (KRunner::QueryMatch::CategoryRelevance).
HIGH, MODERATE, LOW = 70, 50, 30


class KRunnerCore:
    def __init__(self, index, worktrees, launchers, live=None, activate=None, clock=time.time,
                 exists=os.path.isdir, home: str | None = None, report=None, codex=None, coordination=None,
                 open_report=None):
        self.report = report or (lambda: "")  # funkcja() → index.html najnowszego raportu porannego
        # funkcja() → bool: otwarcie najnowszego raportu z oznaczeniem przeczytania (ReportMonitor.open);
        # bez niej samo xdg-open przez launchers
        self.open_report = open_report
        self.coordination = coordination      # funkcja() → koordynatorzy (/Coordination Coordinators()) albo None
        self.index = index
        self.worktrees = worktrees
        self.launchers = launchers
        self.live = live or (lambda: [])
        self.activate = activate
        self.codex = codex
        self.clock = clock
        self.exists = exists
        self.home = (home or os.path.expanduser("~")).rstrip("/")

    # --- akcje -----------------------------------------------------------------------------

    def actions(self) -> list[tuple[str, str, str]]:
        """Akcje dodatkowe (id, tekst, ikona) — tylko dla dostępnych programów."""
        available = self.launchers.available()
        return [(key, text, icon) for key, (text, icon) in ACTIONS.items() if available.get(key)]

    # --- dopasowanie -----------------------------------------------------------------------

    def match(self, query: str) -> list[dict]:
        query = (query or "").strip()
        words = search.tokens(query)
        if words and words[0] == SESSIONS_PREFIX.strip():
            # „ai” i dalsze słowa: tylko sesje; samo „ai” — najświeższe sesje.
            return self._sessions(words[1:], SESSIONS_ONLY_LIMIT, recent_if_empty=True)
        if len(query) < MIN_QUERY:
            return []
        report = self._report(query)
        board = self._coordination(words)
        return report + board + self._sessions(words, LIMITS["session"]) + self._projects(words) + self._worktrees(words)

    def _coordination(self, words) -> list[dict]:
        """„sesje” (od 3 liter) → okno Tide Sesje; „sesje <słowa>” → koordynatorzy, w których słowa trafiają."""
        if self.coordination is None or not words or not SESSIONS_KEYWORD.startswith(words[0]):
            return []
        if not getattr(self.launchers, "sessions_window", ""):
            return []
        try:
            items = list(self.coordination() or [])
        except Exception:  # lista koordynatorów nie może zepsuć KRunnera
            items = []
        rest = words[1:]
        exact = words[0] == SESSIONS_KEYWORD
        out = []
        if not rest:
            live = sum(1 for c in items if c.get("live"))
            sub = f"Koordynatorzy, tickety i podagenci · {len(items)} z ostatnich dni" + (f", {live} działa" if live else "")
            out.append(_match(SESSIONS_MATCH, "Tide Sesje", SESSIONS_ICON, HIGH if exact else MODERATE,
                              1.0 if exact else 0.8, sub, CATEGORIES["coordination"], []))
        scored = []
        for item in items:
            fields = [(search.fold(item.get("project")), 1.0), (search.fold(item.get("title")), 0.9),
                      (search.fold(item.get("label")), 1.0), (search.fold(item.get("branch")), 0.8)]
            if rest:
                hit = search.score(rest, fields)
                if not hit:
                    continue
                scored.append((hit[0], item))
            else:
                scored.append((0.6 if item.get("live") else 0.4, item))
        scored.sort(key=lambda s: (-s[0], -(s[1].get("lastActivityTs") or 0)))
        for value, item in scored[:COORDINATION_LIMIT]:
            counts = item.get("counts") or {}
            bits = [(item.get("progress") or {}).get("text", "")]
            if counts.get("czeka"):
                bits.append(f"{counts['czeka']} czeka")
            if counts.get("w-toku") or counts.get("review"):
                bits.append(f"{counts.get('w-toku', 0) + counts.get('review', 0)} w toku")
            bits.append(search.ago_text(item.get("lastActivityTs") or 0, self.clock()))
            text = " · ".join(p for p in ("Sesje", item.get("project"), item.get("title")) if p)
            out.append(_match(f"coordination:{item.get('id')}", text, SESSIONS_ICON, MODERATE, value,
                              " · ".join(b for b in bits if b), CATEGORIES["coordination"], []))
        return out

    def _report(self, query: str) -> list[dict]:
        """„raport” (albo jego początek od 3 liter, „raport poranny”) → najnowszy raport poranny."""
        text = " ".join(search.tokens(query))
        if not (REPORT_KEYWORD.startswith(text) or text.startswith(REPORT_KEYWORD.split()[0])):
            return []
        path = self.report()
        if not path or not os.path.isfile(path):
            return []
        day = os.path.basename(os.path.dirname(path))
        exact = text in (REPORT_KEYWORD, REPORT_KEYWORD.split()[0])
        return [_match(REPORT_MATCH, f"Raport poranny · {day}", REPORT_ICON, HIGH if exact else MODERATE,
                       1.0 if exact else 0.8, f"Otwórz w przeglądarce · {self._short(path)}", CATEGORIES["report"], [])]

    def _sessions(self, words, limit, recent_if_empty=False) -> list[dict]:
        now = self.clock()
        live = {s.get("id"): s for s in self.live() if s.get("canActivate") and s.get("state") != "ended"}
        actions = [a for a, _t, _i in self.actions()]
        app = self._app_ready()
        scored = []
        for entry in self.index.entries():
            if not self.exists(entry["cwd"]):
                continue  # katalogu już nie ma — nie da się wznowić sesji tam, gdzie była
            fresh = search.recency(entry["lastActivity"], now, HISTORY_AGE)
            if words:
                hit = search.score(words, search.session_fields(entry))
                if not hit:
                    continue
                value, best = hit
            elif recent_if_empty:
                value, best = 0.0, 0.0
            else:
                continue
            scored.append((0.75 * value + 0.25 * fresh, best, entry))
        scored.sort(key=lambda s: (-s[0], -s[2]["lastActivity"]))
        out = []
        for relevance, best, entry in scored[:limit]:
            tool = TOOL_NAMES.get(entry["tool"], entry["tool"])
            text = " · ".join(p for p in (tool, entry["project"], entry["title"]) if p)
            sub = [entry["branch"], search.ago_text(entry["lastActivity"], now)]
            if entry.get("desktop") and app:
                sub.append("otwórz w Codex")
            elif entry["id"] in live:
                sub.append("przejdź do okna")
            out.append(_match(f"session:{entry['id']}", text, TOOL_ICONS.get(entry["tool"], "utilities-terminal"),
                              _category_relevance(best), relevance, " · ".join(p for p in sub if p),
                              CATEGORIES["session"], actions))
        return out

    def _projects(self, words) -> list[dict]:
        items = [w for w in self.worktrees() if w.get("isMain") and not w.get("missing")]
        rider = [RIDER] if self.launchers.available().get(RIDER) else []
        scored = []
        for item in items:
            hit = search.score(words, search.project_fields(item))
            if hit:
                scored.append((hit[0], hit[1], item))
        scored.sort(key=lambda s: (-s[0], search.fold(s[2]["repo"])))
        return [_match(f"project:{item['path']}", item["repo"], PROJECT_ICON, _category_relevance(best), value,
                       f"Otwórz projekt · {self._short(item['path'])}", CATEGORIES["project"], rider)
                for value, best, item in scored[:LIMITS["project"]]]

    def _worktrees(self, words) -> list[dict]:
        items = [w for w in self.worktrees() if not w.get("isMain") and not w.get("missing")]
        rider = [RIDER] if self.launchers.available().get(RIDER) else []
        scored = []
        for item in items:
            hit = search.score(words, search.worktree_fields(item))
            if hit:
                scored.append((hit[0], hit[1], item))
        scored.sort(key=lambda s: (-s[0], -(s[2].get("lastChangeTs") or 0)))
        out = []
        for value, best, item in scored[:LIMITS["worktree"]]:
            text = item.get("branch") or f"{item.get('name')} (odłączony HEAD)"
            sub = f"Worktree · {item.get('repo')} · {self._short(item['path'])}"
            out.append(_match(f"worktree:{item['path']}", text, WORKTREE_ICON, _category_relevance(best), value,
                              sub, CATEGORIES["worktree"], rider))
        return out

    def _app_ready(self) -> bool:
        try:
            return bool(self.codex and self.codex.available())
        except Exception:  # sprawdzenie aplikacji nie może zepsuć wyników KRunnera
            return False

    def _short(self, path: str) -> str:
        if path == self.home or path.startswith(self.home + "/"):
            return "~" + path[len(self.home):]
        return path

    # --- uruchomienie ----------------------------------------------------------------------

    def run(self, match_id: str, action_id: str = "") -> bool:
        if match_id == REPORT_MATCH:
            path = self.report()
            if not path:
                return False
            if self.open_report is not None:
                return bool(self.open_report())
            return self.launchers.open_report(path)
        if match_id == SESSIONS_MATCH:
            return self.launchers.show_sessions("")
        if match_id.startswith("coordination:"):
            known = {c.get("id") for c in (self.coordination() if self.coordination else [])}
            key = match_id[len("coordination:"):]
            return key in known and self.launchers.show_sessions(key)
        kind, _, key = (match_id or "").partition(":")
        if kind == "session":
            return self._run_session(key, action_id)
        if kind in ("project", "worktree"):
            item = next((w for w in self.worktrees() if w.get("path") == key and not w.get("missing")
                         and bool(w.get("isMain")) == (kind == "project")), None)
            if item is None:
                return False  # tylko ścieżki z bieżącej listy — nic spoza niej nie jest otwierane
            return self.launchers.open(RIDER if action_id == RIDER else TERMINAL, item["path"])
        return False

    def _run_session(self, session_id: str, action_id: str) -> bool:
        entry = next((e for e in self.index.entries() if e["id"] == session_id), None)
        if entry is None or not self.exists(entry["cwd"]):
            return False
        cwd = entry["cwd"]
        if action_id == RIDER:
            return self.launchers.open(RIDER, cwd)
        if action_id == TERMINAL:
            return self.launchers.open(TERMINAL, cwd)
        live = any(s.get("id") == session_id and s.get("canActivate") and s.get("state") != "ended"
                   for s in self.live())
        if live and self.activate and self.activate(session_id):
            return True  # sesja jeszcze działa — zamiast drugiej kopii przechodzimy do jej okna
        if entry.get("desktop") and self._app_ready() and self.codex.open_thread(entry["sessionId"]):
            return True  # wątek aplikacji: rozmowa w Codex Desktop, nie `codex resume` w Konsoli
        # source: plik dziennika — sesję ze snapa wznawia Codex ze snapa (Launchers.codex_for).
        argv = self.launchers.resume_command(entry["tool"], entry["sessionId"], cwd, source=entry.get("source", ""))
        return self.launchers.spawn(argv, cwd)


def _category_relevance(best: float) -> int:
    if best >= search.EXACT:
        return HIGH
    if best >= search.PREFIX:
        return MODERATE
    return LOW


def _match(match_id, text, icon, category_relevance, relevance, subtext, category, actions) -> dict:
    return {
        "id": match_id,
        "text": text,
        "icon": icon,
        "categoryRelevance": category_relevance,
        "relevance": round(min(1.0, max(0.0, relevance)), 4),
        "properties": {"subtext": subtext, "category": category, "actions": list(actions)},
    }


class LazyIndex:
    """Leniwe i przyrostowe odświeżanie indeksu w wątku roboczym.

    request(callback) woła callback (w wątku głównym, przez post), gdy dane nadają się do użycia:
    - indeks jeszcze nie zbudowany → po pierwszym zbudowaniu (pierwsze zapytanie czeka),
    - zbudowany, ale starszy niż stale_after → od razu z obecnymi danymi, a odświeżenie idzie w tle
      (nowe sesje będą widoczne przy następnym naciśnięciu klawisza),
    - świeży → od razu.
    post(fn) przenosi wywołanie do wątku głównego (w usłudze: GLib.idle_add).
    """

    def __init__(self, index, post, stale_after: float = REFRESH_AFTER, monotonic=time.monotonic):
        self.index = index
        self.post = post
        self.stale_after = stale_after
        self.monotonic = monotonic
        self._running = False
        self._refreshed_at = None
        self._waiting = []

    def request(self, callback) -> None:
        if not self.index.built:
            self._waiting.append(callback)
            self._start()
            return
        if self._refreshed_at is None or self.monotonic() - self._refreshed_at > self.stale_after:
            self._start()
        callback()

    def _start(self) -> None:
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._work, name="tide-krunner-index", daemon=True).start()

    def _work(self) -> None:
        try:
            self.index.refresh()
        except Exception as exc:  # błąd odczytu nie może zostawić KRunnera bez odpowiedzi
            print(f"tide-daemon: krunner: indeks sesji: {exc!r}", flush=True)
        self.post(self._done)

    def _done(self) -> None:
        self._running = False
        self._refreshed_at = self.monotonic()
        waiting, self._waiting = self._waiting, []
        for callback in waiting:
            callback()
