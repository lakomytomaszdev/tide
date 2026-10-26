"""Stan agentów według katalogu roboczego — mały plik dla promptu powłoki (#26).

Prompt Tide (theme/terminal/prompt.bash) pokazuje chip „claude pracuje” dla katalogu, w którym
stoi powłoka. Nie może pytać usługi przez D-Bus przy każdym znaku zachęty (budżet 30 ms), więc
usługa przy każdej zmianie listy sesji zapisuje plik `$XDG_CACHE_HOME/tide/agents-by-cwd.tsv`,
który bash czyta samym `read`:

    <stan>\\t<narzędzie>\\t<katalog>

Jeden wiersz na katalog, najważniejszy stan wygrywa (czeka > pracuje > skończył). Bezczynne
i zakończone sesje nie trafiają do pliku. Katalog jest ostatni, więc może zawierać spacje;
katalogi z tabulatorem albo nową linią są pomijane. Zapis atomowy (plik tymczasowy + rename),
tylko gdy treść się zmieniła; przy zamknięciu usługi plik znika, żeby prompt nie pokazywał
nieaktualnego stanu.
"""
import os

STATES = {"waiting": 3, "working": 2, "done": 1}


def default_path() -> str:
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "tide", "agents-by-cwd.tsv")


def by_cwd(sessions: list[dict]) -> dict[str, tuple[str, str]]:
    """Katalog → (stan, narzędzie) dla sesji czekających, pracujących i skończonych."""
    out: dict[str, tuple[str, str]] = {}
    for session in sessions:
        state = session.get("state")
        cwd = (session.get("cwd") or "").rstrip("/") or ("/" if session.get("cwd") else "")
        if state not in STATES or not cwd or "\t" in cwd or "\n" in cwd:
            continue
        current = out.get(cwd)
        if current is None or STATES[state] > STATES[current[0]]:
            out[cwd] = (state, session.get("tool") or "agent")
    return out


def render(entries: dict[str, tuple[str, str]]) -> str:
    return "".join(f"{state}\t{tool}\t{cwd}\n" for cwd, (state, tool) in sorted(entries.items()))


class CwdStateFile:
    """Zapisuje plik tylko przy zmianie treści; `clear()` usuwa go przy zamknięciu usługi."""

    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        self._last: str | None = None

    def update(self, sessions: list[dict]) -> bool:
        text = render(by_cwd(sessions))
        if text == self._last:
            return False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"tide-daemon: nie udało się zapisać stanu agentów dla promptu: {exc}", flush=True)
            return False
        self._last = text
        return True

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass
        self._last = None
