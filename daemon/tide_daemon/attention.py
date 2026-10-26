"""Sesje, które czekają na użytkownika: wybór najdłużej czekającej i kiedy powiadamiać.

Czysta logika na liście sesji z AgentScanner.sessions() — bez D-Bus i bez Qt, żeby dało się
ją sprawdzić na fixtures. Wiązanie z magistralą jest w notifications.py i monitor.py.

Liczy się wyłącznie stan „waiting” — agent stoi, dopóki użytkownik nie odpowie (zgoda, pytanie).
Sesja, która tylko skończyła turę („done”), nie dostaje powiadomienia i nie jest celem Meta+A:
nic nie blokuje, a skrót, który czasem prowadzi do skończonej rozmowy, przestałby znaczyć
„ktoś cię potrzebuje”. Do skończonych sesji prowadzi lista w widżecie i KRunner.
"""
WAITING = "waiting"

REASONS = {
    "question": "Pyta o decyzję",
    "approval": "Czeka na zgodę",
    "permission": "Czeka na zgodę",
}
TOOLS = {"claude": "Claude Code", "codex": "Codex"}


def longest_waiting(sessions: list[dict]) -> dict | None:
    """Sesja czekająca najdłużej, do której okna da się przejść, albo None.

    Czekające sesje bez ustalonego okna (np. w tmux) pomijamy — skrót i tak by nic nie zrobił,
    a następna w kolejce może mieć okno.
    """
    candidates = [s for s in sessions if s.get("state") == WAITING and s.get("canActivate")]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (s.get("sinceTs") or 0, s.get("id", "")))


class WaitingTracker:
    """Pamięta, które sesje już czekają, i zgłasza tylko wejścia w stan „czeka” i wyjścia z niego.

    Ta sama sesja dostaje powiadomienie ponownie dopiero wtedy, gdy przestanie czekać (pracuje,
    bezczynna, zakończona, zniknęła z listy) i znowu zacznie. Pierwsza lista tylko zapamiętuje
    stan: po starcie usługi nie zasypujemy użytkownika powiadomieniami o sesjach, które czekały
    już wcześniej.
    """

    def __init__(self):
        self._waiting: set[str] | None = None

    @property
    def primed(self) -> bool:
        return self._waiting is not None

    def update(self, sessions: list[dict]) -> tuple[list[dict], list[str]]:
        """Zwraca (sesje, które właśnie zaczęły czekać — od najdłużej czekającej,
        identyfikatory sesji, które przestały czekać)."""
        now = {s["id"]: s for s in sessions if s.get("state") == WAITING and s.get("id")}
        if self._waiting is None:
            self._waiting = set(now)
            return [], []
        started = sorted((s for sid, s in now.items() if sid not in self._waiting),
                         key=lambda s: (s.get("sinceTs") or 0, s["id"]))
        stopped = sorted(sid for sid in self._waiting if sid not in now)
        self._waiting = set(now)
        return started, stopped


def notification_text(session: dict) -> tuple[str, str]:
    """Tytuł i treść powiadomienia: „Agent czeka: <projekt>” i powód z kontekstem."""
    project = session.get("project") or session.get("cwd") or "?"
    reason = session.get("reason", "")
    why = REASONS.get(reason) or (f"Czeka: {reason}" if reason else "Czeka na ciebie")
    if reason in ("approval", "permission", "question") and session.get("lastAction"):
        why += f": {session['lastAction']}"
    where = [TOOLS.get(session.get("tool", ""), session.get("tool", ""))]
    if session.get("branch"):
        where.append(session["branch"])
    if session.get("title"):
        where.append(session["title"])
    body = "\n".join([why, " · ".join(p for p in where if p)])
    return f"Agent czeka: {project}", body
