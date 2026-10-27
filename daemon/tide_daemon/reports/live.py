"""Żywy raport w ciągu dnia: kiedy złożyć raport bieżącej doby ponownie — bez Qt (report_monitor.py
dokłada zegar).

Raport doby powstaje o 07:00 (schedule.py), a potem odświeża się sam, gdy praca się zmienia:
- ticket na tablicy koordynacji zmienił stan (np. skończony po review — wtedy od razu idzie też notka
  „Skończone dziś”, reports/notes.py),
- agent w repozytorium skończył pracę (sesja przestała pracować).
Zmiana „czeka” nie wymaga składania raportu: Day()/Days()/Status() przeliczają ją na żywo
(data.apply_live), a usługa wysyła tylko sygnał Changed.

Tanio i zbiorczo: zdarzenia oznaczają raport jako nieaktualny, a składamy go najwyżej raz na
INTERVAL (12 min) i nie wcześniej niż SETTLE (90 s) po ostatnim zdarzeniu z serii — kilka ticketów
skończonych naraz to jedno złożenie. Przy złożeniu model dostaje tylko projekty, których fakty się
zmieniły (pamięć per projekt w summary.json, summarize.py); niezmienione biorą opis z pamięci.
Przed porannym raportem doby nic się nie składa (nie ma czego odświeżać).
"""
from dataclasses import dataclass, field

INTERVAL = 12 * 60      # s — najczęściej, jak składamy żywy raport (a z nim — najwyżej jedno wywołanie modelu)
SETTLE = 90             # s ciszy po zdarzeniu, zanim złożymy (seria zdarzeń = jedno złożenie)


@dataclass
class Live:
    interval: float = INTERVAL
    settle: float = SETTLE
    last_run: float = 0.0           # kiedy ostatnio złożono raport doby (poranny, Generate() albo żywy)
    dirty_since: float = 0.0        # pierwsze niezłożone zdarzenie (0 — raport aktualny)
    last_event: float = 0.0
    reasons: dict = field(default_factory=dict)   # powód → liczba (do dziennika usługi)

    def mark(self, reason: str, now: float) -> None:
        if not self.dirty_since:
            self.dirty_since = now
        self.last_event = now
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def ran(self, now: float) -> None:
        """Raport doby właśnie powstał — zdarzenia sprzed jego startu są w nim."""
        self.last_run = now
        if self.last_event <= now:
            self.dirty_since, self.last_event, self.reasons = 0.0, 0.0, {}

    def delay(self, now: float) -> float | None:
        """Za ile sekund złożyć raport (0 — teraz), None — nie trzeba."""
        if not self.dirty_since:
            return None
        # Cisza po serii, ale nie dłużej niż INTERVAL od pierwszego zdarzenia — ciągła praca też dostaje odświeżenie.
        settled = min(self.last_event + self.settle, self.dirty_since + max(self.settle, self.interval))
        due = max(settled, self.last_run + self.interval)
        return max(0.0, due - now)

    def describe(self) -> str:
        return ", ".join(f"{k} ×{v}" for k, v in sorted(self.reasons.items())) or "zmiany"


def transitions(previous: dict, boards: list) -> tuple[dict, list[tuple[str, str, str]]]:
    """(nowy stan, zmiany): stan ticketów {board|ticket: stan} i lista (klucz, było, jest) — tylko dla
    ticketów, które były już widziane (pierwszy odczyt nie jest zmianą)."""
    current = {}
    for board in boards or []:
        for ticket in board.get("tickets") or []:
            key = f"{board.get('id') or ''}|{ticket.get('key') or ticket.get('id') or ''}"
            current[key] = ticket.get("state") or ""
    changes = [(k, previous[k], v) for k, v in current.items() if k in previous and previous[k] != v]
    return current, changes


def finished_turns(previous: dict, sessions: list) -> tuple[dict, list[str]]:
    """(nowy stan, id sesji, które przestały pracować) — agent skończył kawałek pracy."""
    current = {s.get("id"): s.get("state") or "" for s in sessions or [] if isinstance(s, dict) and s.get("id")}
    done = [sid for sid, state in current.items()
            if previous.get(sid) == "working" and state in ("done", "idle", "ended")]
    return current, done


def waiting_signature(sessions: list) -> tuple:
    """Co czeka teraz (id, od kiedy, na co) — zmiana to sygnał Changed dla widoku, bez składania raportu."""
    return tuple(sorted((s.get("id") or "", round(float(s.get("sinceTs") or 0)), s.get("waitingText") or "")
                        for s in sessions or [] if isinstance(s, dict) and s.get("state") == "waiting"))
