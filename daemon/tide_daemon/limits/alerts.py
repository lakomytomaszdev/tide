"""Powiadomienia o limitach: raz na okno limitu, z pamięcią na dysku.

Reguły (progi z palety, sekcja „limits”):
- zużycie przekroczyło próg z `alertUsedPercent` (domyślnie 80 i 95) — jedno powiadomienie
  na próg i okno; skok przez kilka progów naraz daje jedno powiadomienie o najwyższym,
- prognoza mówi, że limit skończy się więcej niż `alertRunoutMarginMin` (30 min) przed
  resetem — jedno na okno. Gdy w tym samym odczycie przypada też próg zużycia, oba idą
  w jednym powiadomieniu.

Które powiadomienia już poszły, zapisujemy w $XDG_STATE_HOME/tide/limits-alerts.json
(okno rozpoznajemy po pierwszym odczycie albo po chwili resetu), więc restart usługi
niczego nie powtarza. Sprawdzamy tylko przy nowym odczycie usługi Limity AI.
"""
from __future__ import annotations

import json
import os

from .forecast import RUNS_OUT, when_text

SERVICE_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}
# (dopełniacz, mianownik): „zużyto 82% limitu sesji”, „limit sesji skończy się…”
WINDOW_WORDS = {"session": ("limitu sesji", "limit sesji"), "weekly": ("limitu tygodniowego", "limit tygodniowy")}
RUNOUT = "runout"


def default_path() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "tide", "limits-alerts.json")


def used_id(level: int) -> str:
    return f"used{level}"


class AlertBook:
    def __init__(self, config, path: str | None = None, tz=None):
        self.config = config
        self.path = path or default_path()
        self.tz = tz
        self.state: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.state = {k: v for k, v in data.items()
                          if isinstance(v, dict) and isinstance(v.get("fired"), list)}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def _record(self, key: str, window_name: str, start: int, reset) -> dict:
        """Wpis bieżącego okna: ten sam, jeśli zgadza się początek albo (w tolerancji) reset."""
        rec = self.state.get(key)
        jump = self.config.window(window_name).reset_jump_s
        same = rec is not None and (
            rec.get("start") == start
            or (rec.get("reset") is not None and reset is not None and abs(rec["reset"] - reset) <= jump))
        if not same:
            rec = {"start": start, "reset": reset, "fired": []}
            self.state[key] = rec
        else:
            rec["start"], rec["reset"] = start, reset
        return rec

    def check(self, service: str, window_name: str, forecast: dict, now: float) -> list[dict]:
        """Powiadomienia do wysłania dla nowego odczytu (z prognozą bieżącego okna)."""
        if forecast.get("used") is None or forecast.get("windowStartTs") is None:
            return []
        if forecast.get("reason") == "resetPassed":
            return []  # odczyt z okna, które już się odnowiło (np. stary lastRead po przerwie) — nieaktualny
        key = f"{service}/{window_name}"
        before = json.dumps(self.state.get(key), sort_keys=True)
        rec = self._record(key, window_name, forecast["windowStartTs"], forecast.get("resetTs"))
        fired = set(rec["fired"])
        used = forecast["used"]
        crossed = [level for level in self.config.alert_used if used >= level]
        new_level = max((lv for lv in crossed if used_id(lv) not in fired), default=None)
        runout = (forecast.get("state") == RUNS_OUT and RUNOUT not in fired
                  and (forecast.get("marginS") or 0) >= self.config.alert_runout_margin_s)
        alerts = []
        if new_level is not None:
            fired.update(used_id(lv) for lv in crossed)
            if runout:
                fired.add(RUNOUT)
            alerts.append(self._used_text(service, window_name, forecast, now, with_runout=runout))
        elif runout:
            fired.add(RUNOUT)
            alerts.append(self._runout_text(service, window_name, forecast, now))
        rec["fired"] = sorted(fired)
        if json.dumps(rec, sort_keys=True) != before:
            try:
                self._save()
            except OSError as exc:  # brak zapisu = najwyżej powtórka po restarcie
                print(f"tide-daemon: nie udało się zapisać stanu powiadomień limitów: {exc}", flush=True)
        return alerts

    # --- teksty ----------------------------------------------------------------------------

    def _reset_text(self, forecast: dict, now: float) -> str:
        reset = forecast.get("resetTs")
        return when_text(reset, now, self.tz) if reset else ""

    def _used_text(self, service, window_name, forecast, now, with_runout: bool) -> dict:
        name = SERVICE_NAMES.get(service, service)
        genitive, _ = WINDOW_WORDS[window_name]
        parts = [f"Zostało {forecast['remaining']}%"]
        reset = self._reset_text(forecast, now)
        if reset:
            parts.append(f"reset {reset}")
        body = " · ".join(parts)
        if with_runout:
            body += f"\nPrzy obecnym tempie {forecast['text']} — przed resetem"
        return {"id": f"{service}/{window_name}", "summary": f"{name}: zużyto {forecast['used']}% {genitive}",
                "body": body}

    def _runout_text(self, service, window_name, forecast, now) -> dict:
        name = SERVICE_NAMES.get(service, service)
        _, nominative = WINDOW_WORDS[window_name]
        reset = self._reset_text(forecast, now)
        body = f"Przy obecnym tempie {forecast['text']}"
        body += f", a reset dopiero {reset}" if reset else ""
        body += f" · zostało {forecast['remaining']}%"
        return {"id": f"{service}/{window_name}", "summary": f"{name}: {nominative} skończy się przed resetem",
                "body": body}
