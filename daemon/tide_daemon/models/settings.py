"""Zapis ustawień modeli lokalnych z D-Bus (widok „Modele” w oknie Tide): models.json, tokeny i tryb raportu.

Użytkownik konfiguruje modele w oknie Tide, nie w plikach; przepięcie na Maca to SetDefault("mac").
Ten moduł nie zna Qt — wywołuje go models_monitor.py w wątku głównym (zapis pliku trwa milisekundy).

- Walidacja jest jedna: punkt i rola przechodzą przez te same funkcje co odczyt pliku
  (config.check_endpoint, config.check_role), więc odmowa zapisu ma ten sam komunikat co ostrzeżenie
  przy odczycie. Na koniec cały wynik czytamy jeszcze raz (config.from_dict): zmiana, która dałaby
  nowe ostrzeżenie, nie jest zapisywana. Wcześniejsze ostrzeżenia z ręcznie edytowanego pliku nie
  blokują zapisu.
- Zapis atomowy: plik tymczasowy w tym samym katalogu i rename; models.json z prawami 0600.
  Nieznane klucze i pozostałe ustawienia zostają. Dowiązanie symboliczne do pliku zostaje dowiązaniem
  (piszemy w plik docelowy).
- Brak models.json: zaczynamy od wartości domyślnych (punkt `local`, role `summary` i `describe` bez
  punktu), więc pierwszy zapis niczego nie zmienia w działaniu poza samą zmianą. Zepsuty plik (nie JSON,
  nie obiekt, `endpoints` albo `roles` nie obiektem) — odmowa: nie nadpisujemy pliku, którego nie umiemy
  przeczytać.
- Token: tylko do pliku `tokens/<punkt>` obok models.json ($XDG_CONFIG_HOME/tide/tokens, katalog 0700,
  plik 0600), w models.json jako `tokenFile`. Nigdy w odpowiedzi, komunikacie ani dzienniku.
- Role zapisujemy bez punktu („qwen3:8b”), chyba że wywołujący wskaże punkt — wtedy idą za `default`,
  więc przełączenie domyślnego punktu przenosi je na Maca, a przypięte zostają.
- Zapisy są szeregowane blokadą (D-Bus i ewentualne wątki), a piszemy tylko na wyraźne wywołanie.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import tempfile
import threading

from . import config as config_mod
from .config import DEFAULT_ENDPOINT, DEFAULT_ROLES, DEFAULT_URL

LOCK = threading.Lock()
TOKENS_DIR = "tokens"
TEXT_MAX = 512             # adres, nazwa modelu — dłuższe to pomyłka albo śmieci
TOKEN_MAX = 4096
# Nazwa modelu do pobrania (Ollama): „qwen3:8b”, „hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M”.
PULL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


class SettingsError(Exception):
    """Odmowa albo nieudany zapis; str(exc) to gotowy komunikat po polsku dla użytkownika (bez tokenu)."""


def tokens_dir(path: str | None = None) -> str:
    """Katalog tokenów obok models.json: $XDG_CONFIG_HOME/tide/tokens (z $TIDE_MODELS_CONFIG — obok niego)."""
    return os.path.join(os.path.dirname(os.path.abspath(path or config_mod.config_path())), TOKENS_DIR)


def token_path(name: str, path: str | None = None) -> str:
    return os.path.join(tokens_dir(path), name)


def defaults() -> dict:
    """Plik odpowiadający działaniu bez models.json (config.defaults): od niego zaczyna pierwszy zapis."""
    return {"default": DEFAULT_ENDPOINT, "endpoints": {DEFAULT_ENDPOINT: {"url": DEFAULT_URL, "kind": "ollama"}},
            "roles": dict(DEFAULT_ROLES)}


# --- pliki ---------------------------------------------------------------------------------------

def atomic_write(path: str, data: bytes, mode: int) -> None:
    """Plik tymczasowy w tym samym katalogu, fsync, rename. Przy błędzie stary plik zostaje nietknięty."""
    path = os.path.realpath(path)
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    # Zmiana nazwy trafia na dysk dopiero z katalogiem — po awarii zasilania plik nie wróci do starej treści.
    with contextlib.suppress(OSError):
        dirfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)


def _read_object(path: str, label: str) -> dict | None:
    """Obiekt z pliku JSON; brak pliku → None; nieczytelny albo nie obiekt → SettingsError (nie nadpisujemy)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{label}: niepoprawny JSON (linia {exc.lineno}) — nie zapisuję, żeby nie nadpisać "
                            f"pliku; popraw go albo usuń ({path})") from None
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{label}: nie da się odczytać ({type(exc).__name__}) — nie zapisuję ({path})") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{label}: oczekiwano obiektu JSON — nie zapisuję, żeby nie nadpisać pliku; popraw go "
                            f"albo usuń ({path})")
    return data


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _file_mode(path: str, fallback: int) -> int:
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return fallback


# --- models.json ---------------------------------------------------------------------------------

class _Draft:
    """Kopia models.json do zmiany: surowe dane, konfiguracja jak z odczytu i ostrzeżenia sprzed zmiany."""

    def __init__(self, path: str):
        self.path = path
        raw = _read_object(path, "models.json")
        self.data = defaults() if raw is None else copy.deepcopy(raw)
        self.before = set(config_mod.from_dict(self.data, path).warnings)
        for key in ("endpoints", "roles"):
            if key in self.data and self.data[key] is None:
                del self.data[key]  # odczyt traktuje null jak brak klucza — zapis też
            if key in self.data and not isinstance(self.data[key], dict):
                raise SettingsError(f"models.json: {key} nie jest obiektem — nie zapisuję, żeby nie nadpisać "
                                    f"pliku; popraw go albo usuń ({path})")
        self.endpoints = self.data.setdefault("endpoints", {})
        self.roles = self.data.setdefault("roles", {})
        cfg = config_mod.from_dict(self.data, path)
        # Bez żadnego poprawnego punktu odczyt dokłada `local` — zapis nie może go zgubić dodaniem innego punktu.
        if not set(self.endpoints) & set(cfg.endpoints) and DEFAULT_ENDPOINT not in self.endpoints:
            self.endpoints[DEFAULT_ENDPOINT] = {"url": DEFAULT_URL, "kind": "ollama"}
        # Bez `default` domyślny jest zapasowy (local albo pierwszy) — zapisany wprost, żeby się nie przesunął.
        if "default" not in self.data:
            self.data["default"] = cfg.default
        self.cfg = config_mod.from_dict(self.data, path)

    def pinned(self, name: str) -> list[str]:
        """Role przypięte do punktu (`"name:model"` albo {"model": "name:model"})."""
        out = []
        for role, value in self.roles.items():
            spec = value.get("model") if isinstance(value, dict) else value
            if isinstance(spec, str):
                head, sep, rest = spec.strip().partition(":")
                if sep and rest and head == name:
                    out.append(role)
        return sorted(out)

    def commit(self) -> None:
        cfg = config_mod.from_dict(self.data, self.path)
        new = [w for w in cfg.warnings if w not in self.before]
        if new:
            raise SettingsError(f"nie zapisuję: {re.sub(r' — pominięt[ya]$', '', new[0])}")
        try:
            atomic_write(self.path, _dump(self.data), 0o600)
        except OSError as exc:
            raise SettingsError(f"models.json: nie udało się zapisać ({type(exc).__name__})") from None


def _text(req: dict, key: str, label: str, default=None, limit: int = TEXT_MAX):
    value = req.get(key, default)
    if value is not None and not isinstance(value, str):
        raise SettingsError(f"{label}: oczekiwano napisu")
    if isinstance(value, str) and len(value.strip()) > limit:
        raise SettingsError(f"{label}: najwyżej {limit} znaków")
    return value.strip() if isinstance(value, str) else value


def _meaning_changes(before, data: dict, path: str) -> list:
    """Role, które po zmianie czytałyby się inaczej (inny punkt albo model) — np. nowy punkt `qwen3` sprawia,
    że „qwen3:8b” w roli bez punktu staje się punktem qwen3 i modelem 8b."""
    after = config_mod.from_dict(data, path).roles
    return sorted((name, role) for name, role in before.roles.items()
                  if name in after and (after[name].endpoint, after[name].model) != (role.endpoint, role.model))


def _write_token(path: str, token: str):
    """Zapisuje token (0600, katalog 0700); zwraca funkcję przywracającą poprzedni stan pliku."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    try:
        with open(path, "rb") as fh:
            previous = fh.read()
    except FileNotFoundError:
        previous = None
    atomic_write(path, (token + "\n").encode("ascii"), 0o600)

    def restore():
        with contextlib.suppress(OSError):
            if previous is None:
                os.unlink(path)
            else:
                atomic_write(path, previous, 0o600)

    return restore


def _remove_token(name: str, path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(token_path(name, path))


def set_endpoint(req: dict, path: str | None = None) -> None:
    """Nowy albo zmieniony punkt: {name, url, kind, token?, clearToken?}. Pusty albo brak `token` — token zostaje,
    `clearToken: true` — punkt bez tokenu (nasz plik tokenu znika). Nieznane klucze punktu zostają."""
    path = path or config_mod.config_path()
    if not isinstance(req, dict):
        raise SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
    name = req.get("name")
    url = _text(req, "url", "url")
    kind = _text(req, "kind", "kind")
    token = _text(req, "token", "token", limit=TOKEN_MAX) or ""
    clear = req.get("clearToken", False)
    if not isinstance(clear, bool):
        raise SettingsError("clearToken: oczekiwano true albo false")
    if token and clear:
        raise SettingsError("podaj nowy token albo clearToken, nie oba naraz")
    if token and not config_mod.valid_token(token):
        raise SettingsError("token: tylko drukowalne znaki ASCII, bez spacji")
    with LOCK:
        draft = _Draft(path)
        existing = draft.endpoints.get(name) if isinstance(name, str) else None
        value = dict(existing) if isinstance(existing, dict) else {}
        value["url"] = url
        value["kind"] = kind or value.get("kind") or "ollama"
        own = token_path(name, path) if isinstance(name, str) and config_mod.NAME_RE.fullmatch(name) else ""
        if clear:
            value.pop("tokenFile", None)
        elif token:
            value["tokenFile"] = own
        _endpoint, error = config_mod.check_endpoint(name, value)
        if error:
            raise SettingsError(error)
        draft.endpoints[name] = value
        changed = _meaning_changes(draft.cfg, draft.data, path)
        if changed:
            first = changed[0][1]
            raise SettingsError(f"nazwa „{name}” zmieniłaby role {', '.join(r for r, _ in changed)} (model "
                                f"„{first.model}” czytany jako serwer „{name}”) — wybierz inną nazwę serwera")
        restore = None
        if token:
            try:
                restore = _write_token(own, token)
            except OSError as exc:
                raise SettingsError(f"token: nie udało się zapisać pliku ({type(exc).__name__})") from None
        try:
            draft.commit()
        except BaseException:
            if restore:
                restore()
            raise
        if clear:
            _remove_token(name, path)


def remove_endpoint(name, path: str | None = None) -> None:
    """Usuwa punkt. Odmowa dla punktu domyślnego, ostatniego poprawnego i punktu z przypiętymi rolami
    (rola „mac:model” po usunięciu `mac` po cichu poszłaby na `default` — wolimy powiedzieć wprost)."""
    path = path or config_mod.config_path()
    with LOCK:
        draft = _Draft(path)
        if not isinstance(name, str) or name not in draft.endpoints:
            raise SettingsError(f"nieznany serwer „{str(name)[:40]}”")
        if name in draft.cfg.endpoints and len(draft.cfg.endpoints) == 1:
            raise SettingsError(f"serwer „{name}” jest jedynym serwerem — dodaj inny, zanim go usuniesz")
        if name == draft.data.get("default") or name == draft.cfg.default:
            raise SettingsError(f"serwer „{name}” jest domyślny — najpierw ustaw inny serwer jako domyślny")
        pinned = draft.pinned(name)
        if pinned:
            raise SettingsError(f"serwer „{name}” jest używany przez role: {', '.join(pinned)} — przepnij je na inny "
                                f"serwer albo na domyślny, zanim go usuniesz")
        del draft.endpoints[name]
        draft.commit()
        _remove_token(name, path)


def set_default(name, path: str | None = None) -> None:
    """Domyślny punkt — przełącznik „ten komputer / Mac”: role bez punktu idą za nim."""
    path = path or config_mod.config_path()
    with LOCK:
        draft = _Draft(path)
        if not isinstance(name, str):
            raise SettingsError("default: oczekiwano nazwy serwera")
        if name not in draft.cfg.endpoints:
            if name in draft.endpoints:
                raise SettingsError(f"default: serwer „{name[:40]}” jest pominięty w models.json — popraw go najpierw")
            raise SettingsError(f"default: nieznany serwer „{name[:40]}”")
        draft.data["default"] = name
        draft.commit()


_KEEP = object()


def set_role(req: dict, path: str | None = None) -> None:
    """Rola: {role, model, endpoint?, ctx?}. `endpoint` pusty albo brak — rola idzie za `default` (zapis bez
    punktu), nazwa punktu — rola przypięta. `ctx`: brak klucza — bez zmiany, null — domyślny (8192 dla
    summary i describe, inaczej kontekst serwera), liczba 0…1048576 (0 — kontekst serwera)."""
    path = path or config_mod.config_path()
    if not isinstance(req, dict):
        raise SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
    role = req.get("role")
    if not isinstance(role, str) or not config_mod.NAME_RE.fullmatch(role):
        raise SettingsError(config_mod.bad_name("rola", role))
    model = _text(req, "model", f"rola „{role}”: model")
    if not model:
        raise SettingsError(f"rola „{role}”: podaj nazwę modelu")
    endpoint = _text(req, "endpoint", f"rola „{role}”: endpoint") or ""
    ctx = req["ctx"] if "ctx" in req else _KEEP
    with LOCK:
        draft = _Draft(path)
        if endpoint:
            if endpoint not in draft.cfg.endpoints:
                state = "jest pominięty w models.json" if endpoint in draft.endpoints else "nie istnieje"
                raise SettingsError(f"rola „{role}”: serwer „{endpoint[:40]}” {state}")
            spec = f"{endpoint}:{model}"
        else:
            head, sep, rest = model.partition(":")
            if sep and rest and head in draft.endpoints:
                raise SettingsError(f"rola „{role}”: nazwa modelu zaczyna się od nazwy serwera „{head}:” — wybierz "
                                    f"serwer jawnie")
            spec = model
        existing = draft.roles.get(role)
        value = dict(existing) if isinstance(existing, dict) else {}
        value["model"] = spec
        if ctx is None:
            value.pop("ctx", None)
        elif ctx is not _KEEP:
            value["ctx"] = ctx
        stored = spec if set(value) == {"model"} else value
        _role, error = config_mod.check_role(role, stored, draft.cfg)
        if error:
            raise SettingsError(error)
        draft.roles[role] = stored
        draft.commit()


# --- report.json ---------------------------------------------------------------------------------

def set_report_summary(mode, path: str | None = None) -> None:
    """`summary` w $XDG_CONFIG_HOME/tide/report.json: claude, local albo rules; pozostałe klucze zostają."""
    from ..reports import schedule
    path = path or schedule.config_path()
    if mode not in schedule.SUMMARY_MODES:
        raise SettingsError(schedule.summary_problem(mode))
    with LOCK:
        data = _read_object(path, "report.json") or {}
        data["summary"] = mode
        try:
            atomic_write(path, _dump(data), _file_mode(os.path.realpath(path), 0o600))
        except OSError as exc:
            raise SettingsError(f"report.json: nie udało się zapisać ({type(exc).__name__})") from None


def report_state(path: str | None = None, live: str | None = None, pinned: bool = False) -> dict:
    """Tryb „Na daily” dla Status(): {summary, configured, summaryModel, raw, pinned, path, warnings}.

    `summary` — tryb, którego usługa używa teraz (`live`, gdy usługa go poda; inaczej z pliku), `configured` —
    z report.json po walidacji (nieznana wartość → rules), `raw` — wpisana nieznana wartość (inaczej null),
    `pinned` — tryb wymuszony opcją --report-summary (zapis zmienia plik, ale nie działającą usługę)."""
    from ..reports import schedule
    path = path or schedule.config_path()
    settings, warnings = schedule.load_settings(path)
    raw = None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "summary" in data and data["summary"] not in schedule.SUMMARY_MODES:
            raw = data["summary"] if isinstance(data["summary"], str) else json.dumps(data["summary"])
            raw = raw[:40]
    except (OSError, ValueError):
        pass
    return {"summary": live or settings.summary, "configured": settings.summary,
            "summaryModel": settings.summary_model, "raw": raw, "pinned": bool(pinned), "path": path,
            "warnings": list(warnings)}


# --- TestEndpoint i PullModel ----------------------------------------------------------------------

def endpoint_for_test(req: dict, path: str | None = None):
    """Punkt do próby połączenia, bez zapisu: {name} (zapisany punkt) albo {url, kind, token?}; `name` razem
    z `url`/`kind`/`token` — zapisany punkt z podmienionymi polami (edycja przed zapisem). → Endpoint."""
    if not isinstance(req, dict):
        raise SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
    name = _text(req, "name", "name") or ""
    url = _text(req, "url", "url")
    kind = _text(req, "kind", "kind")
    token = _text(req, "token", "token") or ""
    cfg = config_mod.load(path)
    saved = cfg.endpoints.get(name) if name else None
    if name and saved is None and url is None:
        raise SettingsError(f"nieznany serwer „{name[:40]}”")
    if url is None and saved is None:
        raise SettingsError("podaj nazwę zapisanego serwera albo adres (url)")
    if url is not None or kind is not None:
        value = {"url": url if url is not None else saved.url, "kind": kind or (saved.kind if saved else "ollama")}
        endpoint, error = config_mod.check_endpoint(name or "nowy", value)
        if error:
            raise SettingsError(error)
        if saved is not None and saved.token_file:
            endpoint = _replace(endpoint, token_file=saved.token_file)
    else:
        endpoint = saved
    if token:
        if not config_mod.valid_token(token):
            raise SettingsError("token: tylko drukowalne znaki ASCII, bez spacji")
        endpoint = _replace(endpoint, token=token, token_file="")
    return endpoint


def _replace(endpoint, **changes):
    import dataclasses
    return dataclasses.replace(endpoint, **changes)


def endpoint_for_pull(req: dict, path: str | None = None):
    """(Endpoint, model) do pobrania: punkt Ollamy z models.json i poprawna nazwa modelu."""
    if not isinstance(req, dict):
        raise SettingsError("niepoprawne żądanie: oczekiwano obiektu JSON")
    name = _text(req, "endpoint", "endpoint") or ""
    model = _text(req, "model", "model") or ""
    cfg = config_mod.load(path)
    if name not in cfg.endpoints:
        raise SettingsError(f"nieznany serwer „{name[:40]}”")
    endpoint = cfg.endpoints[name]
    if endpoint.kind != "ollama":
        raise SettingsError(f"serwer „{name}” jest zgodny z OpenAI — modele pobiera się na nim samym, "
                            f"pobieranie z Tide działa tylko z Ollamą")
    if not PULL_NAME_RE.fullmatch(model):
        raise SettingsError("nazwa modelu: litery, cyfry i znaki ._:/@+- (np. qwen3:8b)")
    return endpoint, model
