"""Stała polityka prób K-38, również dla samodzielnego launchera Konsoli.

Bez importów pakietu i bez odczytów konfiguracji. Aktualizacja usługi obejmuje
ten sam plik dla silnika, runnerów i już wystawionych zadań Konsoli.
"""
import re

LEGACY = frozenset({"PT-173", "PT-176", "PT-184", "PT-185", "PT-186", "PT-191"})
EXCLUDED = frozenset({"PT-177", "PT-184", "PT-185", "PT-186"})
MESSAGE = "K-38 v1: dawne próby są wstrzymane; wymagane nowe przekazanie i chroniony odbiór PT-274"


class TrialDenied(RuntimeError):
    pass


def blocked(ticket: str) -> bool:
    return str(ticket).upper() in LEGACY | EXCLUDED


# K-113 PT-466: jedno wyrażenie skompilowane przy wczytaniu modułu zamiast osobnego wyszukiwania każdego numeru.
# Te same granice co dawna reguła (na numer): bez litery i cyfry przed numerem, bez cyfry po nim, bez względu
# na wielkość liter (ta sama flaga także dla granic). Alternatywa z cofaniem znajduje dopasowanie dokładnie wtedy,
# gdy pasuje któryś pojedynczy numer.
_TRIAL = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(t) for t in sorted(LEGACY | EXCLUDED))
                    + r")(?![0-9])", re.I)


def blocked_event(step) -> bool:
    return blocked(step.get("ticket") or "") or any(
        _TRIAL.search(str(step.get(key) or "")) for key in ("step", "key", "branch"))


def require_ticket(ticket: str) -> None:
    if blocked(ticket):
        raise TrialDenied(MESSAGE)


def require_step(step) -> None:
    if blocked_event({"step": getattr(step, "step_id", ""), "branch": getattr(step, "branch", "")}):
        raise TrialDenied(MESSAGE)


def require_public() -> None:
    raise TrialDenied(MESSAGE + "; publiczne run/box-check/boundary-trial pozostają zamknięte")


def console_allowed(task):
    if blocked_event({"ticket": task.get("flowTicket"), "step": task.get("flowStep")}):
        return False
    # Dawne, już wystawione zadania nie miały flowTicket. Tytuł i ścieżki
    # pozwalają je wstrzymać także bez ponownego otwarcia przez usługę.
    for key in ("title", "workdir", "cwd", "closedFile", "processFile", "resultFile"):
        if blocked_event({"step": task.get(key) or ""}):
            return False
    if task.get("flowCard") == "K-38" or re.search(r"\bK-38\b", str(task.get("title") or "")):
        return bool(task.get("flowTicket")) or task.get("flowKind") in (
            "planning", "plan-fix", "plan-amend", "plan-revise", "plan-review")
    return True
