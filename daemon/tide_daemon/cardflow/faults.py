"""Czyja wina błędu kroku i zgłoszenie błędu Tide jako karty (K-35, PT-125).

`fault` błędu kroku:
- `tide` — wina Tide, rozpoznana wyłącznie z twardych przesłanek: krok nie zapisał wyniku (`no-result`), a dziennik
  kroku (`out/step.log`) pokazuje odmowę zapisu w katalogu wyjściowym tego kroku (piaskownica, odmowa narzędzia);
  albo powód, który ustawia sam Tide (`start` — krok nie wystartował, `lost` — krok zniknął po restarcie usługi),
- `provider` — dostawca: limit (`limit`, kod 429 w dzienniku), serwer lokalny niedostępny, a z dziennika kroku jawny
  błąd API — autoryzacja (401, 403) albo awaria (5xx, przeciążenie),
- `model` — wykonawca oddał zły wynik, który Tide sprawdził (`bad-result`, `bad-review`, `review-changed-ticket`),
- `user` — krok przerwany przez użytkownika (porzucony, zamknięta rozmowa),
- `unknown` — wszystko inne: dziennik nie rozstrzyga, więc niczego nie zgadujemy.

Reguły nie czytają zwykłego tekstu agenta jako dowodu — tylko powód wyniku i zdarzenia błędów albo odmów z dziennika.

Zgłoszenie (`ReportStepFault`): karta w Backlogu projektu `plasma-tide` z opisem użytkownika i wycinkiem dziennika
kroku po zamaskowaniu sekretów. Podpis błędu (rodzaj kroku + `fault` + przyczyna) stoi w treści karty jako linia
`Podpis błędu: …`; po nim szukamy otwartej karty, do której dopisujemy kolejne wystąpienie.
"""
from __future__ import annotations

import json
import os
import re

from . import limits as flow_limits
from .labels import kind_label

FAULTS = ("tide", "model", "user", "provider", "unknown")
REPORT_PROJECT = "plasma-tide"
LOG_TAIL_BYTES = 1024 * 1024
EXCERPT_LINES = 30
EXCERPT_LINE_CHARS = 300
DESCRIPTION_LIMIT = 4000
SIGNATURE_PREFIX = "Podpis błędu: "

# Powód wyniku → (fault, przyczyna) bez czytania dziennika.
REASON_FAULTS = {
    "start": ("tide", "start"), "lost": ("tide", "lost"),
    "limit": ("provider", "limit"), "local-unavailable": ("provider", "local-unavailable"),
    "bad-result": ("model", "bad-result"), "bad-review": ("model", "bad-review"),
    "review-changed-ticket": ("model", "review-changed-ticket"),
    "abandoned": ("user", "abandoned"), "interactive-closed": ("user", "interactive-closed"),
}

CAUSE_LABELS = {
    "write-denied": "Tide nie dał prawa zapisu w katalogu wyniku kroku",
    "start": "krok nie wystartował",
    "lost": "krok zniknął po restarcie usługi",
    "limit": "skończył się limit dostawcy",
    "local-unavailable": "serwer lokalny jest niedostępny",
    "auth": "dostawca odrzucił autoryzację",
    "outage": "awaria po stronie dostawcy",
    "bad-result": "wykonawca zapisał niepoprawny plik wyniku",
    "bad-review": "recenzent zapisał niepoprawny werdykt",
    "review-changed-ticket": "recenzent zmienił gałąź albo pliki ticketu",
    "abandoned": "krok porzucono",
    "interactive-closed": "rozmowę w Konsoli zamknięto przed zapisaniem wyniku",
    "other": "wykonawca zgłosił błąd z własnym powodem",
}

# Powody, które ustawia sam Tide (skończony zbiór). Swobodny powód z `result.json` agenta nie jest przyczyną:
# przy `unknown` przyczyną jest wtedy `other`, żeby podpis był stały, a tekst agenta nie trafiał do tytułu i podpisu.
TIDE_REASONS = frozenset({"no-result", "bad-result", "no-progress", "exit", "start", "timeout", "local-unavailable",
                          "lost", "bad-review", "review-changed-ticket", "abandoned", "interactive-closed", "limit"})
KNOWN_CAUSES = TIDE_REASONS | frozenset(CAUSE_LABELS)

FAULT_LABELS = {"tide": "błąd Tide", "model": "błąd wykonawcy", "user": "przerwane przez użytkownika",
                "provider": "błąd dostawcy", "unknown": "nie wiadomo, czyja to wina"}

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
DENIAL_WORDS = re.compile(r"read-only file system|permission denied|operation not permitted|EROFS|EACCES|"
                          r"brak dostępu|odmowa dostępu|system plików tylko do odczytu", re.I)
AUTH_STATUS, OUTAGE_STATUS = {401, 403}, {500, 502, 503, 504, 529}
AUTH_WORDS = re.compile(r"\b40[13]\b|unauthori[sz]ed|authentication|invalid (?:api|x-api)[ -]?key|"
                        r"not logged in|please run /login|forbidden", re.I)
OUTAGE_WORDS = re.compile(r"\b5(?:0[0234]|29)\b|overloaded|internal server error|service unavailable|"
                          r"bad gateway|gateway timeout", re.I)


# --- rozpoznanie ---------------------------------------------------------------------------------

def read_log(path: str) -> list[str]:
    """Końcówka dziennika kroku (najwyżej 1 MB, 2000 linii); brak pliku albo dowiązanie — pusta lista."""
    try:
        if not path or os.path.islink(path) or not os.path.isfile(path):
            return []
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_TAIL_BYTES))
            return fh.read().decode("utf-8", errors="replace").splitlines()[-2000:]
    except OSError:
        return []


def classify(reason: str, lines: list[str] | None = None, *, out_dir: str = "", tool: str = "",
             log_path: str = "") -> dict:
    """`{fault, cause}` błędu kroku z powodu wyniku i dziennika kroku; bez rozstrzygnięcia — `unknown`."""
    reason = str(reason or "")
    lines = lines or []
    if reason == "limit" or (log_path and flow_limits.exhausted_log(log_path, tool)):
        return {"fault": "provider", "cause": "limit"}
    if reason in REASON_FAULTS:
        fault, cause = REASON_FAULTS[reason]
        return {"fault": fault, "cause": cause}
    events = [event for event in (_json(line) for line in lines) if event is not None]
    provider = _provider_error(events)
    if provider:
        return {"fault": "provider", "cause": provider}
    if reason == "no-result" and out_dir and _write_denied(events, lines, out_dir):
        return {"fault": "tide", "cause": "write-denied"}
    return {"fault": "unknown", "cause": cause_code(reason)}


def of_finished(done: dict) -> dict:
    """`{fault, cause}` z zakończenia kroku: zapisane przy wyniku albo (stary dziennik) z samego powodu."""
    fault = done.get("fault")
    if fault in FAULTS:
        cause = str(done.get("faultCause") or "")
        return {"fault": fault, "cause": cause_code(cause) if cause else ""}
    return classify(done.get("reason") or "")


def cause_code(cause: str) -> str:
    """Przyczyna ze skończonego zbioru; swobodny tekst (powód pisany przez agenta, stary dziennik) → `other`."""
    cause = str(cause or "")
    return cause if cause in KNOWN_CAUSES else "other"


def cause_label(cause: str, reason_labels: dict | None = None) -> str:
    cause = str(cause or "")
    return CAUSE_LABELS.get(cause) or (reason_labels or {}).get(cause) or cause


def _json(line: str):
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    return event if isinstance(event, dict) else None


def _provider_error(events: list[dict]) -> str:
    """Jawny błąd API dostawcy inny niż limit: `auth` albo `outage`; pusty napis — nie ma takiego zdarzenia."""
    for event in events:
        kind = event.get("type") or ""
        if not (kind in ("error", "turn.failed") or kind == "result" and event.get("is_error") is True):
            continue
        detail = event.get("error")
        status = event.get("api_error_status") or (detail.get("status") if isinstance(detail, dict) else None)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 0
        if status in AUTH_STATUS:
            return "auth"
        if status in OUTAGE_STATUS:
            return "outage"
        message = event.get("message") or event.get("result") or detail or ""
        if isinstance(message, dict):
            message = message.get("message") or ""
        if isinstance(message, str):
            if AUTH_WORDS.search(message):
                return "auth"
            if OUTAGE_WORDS.search(message):
                return "outage"
    return ""


def _inside(path: str, folder: str) -> bool:
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    path, folder = os.path.normpath(path), os.path.normpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _write_denied(events: list[dict], lines: list[str], out_dir: str) -> bool:
    """Odmowa zapisu w katalogu wyjściowym tego kroku: narzędzie zapisu odrzucone albo błąd systemu plików
    wskazujący ścieżkę w tym katalogu. Sama wzmianka o katalogu albo odmowa gdzie indziej nie wystarczą."""
    folders = {os.path.normpath(out_dir), os.path.realpath(out_dir)}
    for event in events:
        for denial in event.get("permission_denials") or []:
            if not isinstance(denial, dict) or denial.get("tool_name") not in WRITE_TOOLS:
                continue
            tool_input = denial.get("tool_input") or {}
            target = tool_input.get("file_path") or tool_input.get("notebook_path") if isinstance(tool_input, dict) else ""
            if any(_inside(target, folder) for folder in folders):
                return True
    for line in _tool_outputs(lines):
        if not DENIAL_WORDS.search(line):
            continue
        for folder in folders:
            for match in re.finditer(re.escape(folder) + r"(?:/[^\s\"'\\]*)?", line):
                # odmowa musi stać przy tej ścieżce, nie gdzieś dalej w tej samej linii
                near = line[max(0, match.start() - 160):match.end() + 160]
                if _inside(match.group(0), folder) and DENIAL_WORDS.search(near):
                    return True
    return False


def _tool_outputs(lines: list[str]) -> list[str]:
    """Teksty, którym wierzymy przy odmowie: wyniki narzędzi, wyjście poleceń i zdarzenia błędów z dziennika
    (JSON), a także surowe linie spoza JSON (stderr programu). Tekst pisany przez agenta nie jest dowodem."""
    out = []
    for line in lines:
        event = _json(line)
        if event is None:
            out.append(line)
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                value = item.get("content")
                if isinstance(value, list):
                    value = " ".join(str(c.get("text") or "") for c in value if isinstance(c, dict))
                out.append(str(value or ""))
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if isinstance(item.get("aggregated_output"), str):
            out.append(item["aggregated_output"])
        if event.get("type") in ("error", "turn.failed") and isinstance(event.get("message"), str):
            out.append(event["message"])
    return out


# --- podpis, wycinek i maskowanie ----------------------------------------------------------------

def signature(step_kind: str, fault: str, cause: str) -> str:
    """Podpis błędu: rodzaj kroku, fault i przyczyna jako stały, krótki napis (`plan-review/tide/write-denied`)."""
    return "/".join(_slug(part) or "unknown" for part in (step_kind or "krok", fault, cause))


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return text[:60].rstrip("-")


SECRET_NAME = r"[A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS|API_?KEY|KEY|AUTH|CREDENTIALS?|COOKIE|SESSION)[A-Za-z0-9_]*"
TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bperm:[A-Za-z0-9+/=._\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9+/=._\-]{12,}"),
    # długi ciąg liter z cyframi i wielkimi literami (klucz, token); skróty commitów (sam hex) zostają
    re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])(?=[A-Za-z0-9_\-]*[0-9])[A-Za-z0-9_\-]{32,}\b"),
]
ENV_ASSIGN = re.compile(r"\b((?:export\s+)?)([A-Z_][A-Z0-9_]{2,})=(\"[^\"]*\"|'[^']*'|\S*)")
SECRET_KEY_VALUE = re.compile(r"(?i)(\"?\b(?:" + SECRET_NAME + r"|password|passwd|secret|token|api[_-]?key|"
                              r"authorization|cookie)\b\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)")
MASK = "•••"


def mask(text: str, env: dict | None = None) -> str:
    """Wycinek bez sekretów: wartości zmiennych środowiskowych (`NAZWA=…`), znane tokeny, pary klucz–sekret
    i wartości sekretnych zmiennych z bieżącego środowiska."""
    text = str(text or "")
    environ = os.environ if env is None else env
    for name, value in environ.items():
        if isinstance(value, str) and len(value) >= 8 and re.fullmatch(SECRET_NAME, name, re.I):
            text = text.replace(value, MASK)
    for pattern in TOKEN_PATTERNS:   # najpierw tokeny: „Authorization: Bearer <token>” traci token, nie tylko słowo
        text = pattern.sub(lambda m: (m.group(1) + " " + MASK) if m.groups() else MASK, text)
    text = ENV_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}={MASK}", text)
    text = SECRET_KEY_VALUE.sub(lambda m: m.group(1) + MASK, text)
    return text


def _secret_field(key: str, env: dict | None) -> bool:
    """Czy pole jest sekretne — rozstrzyga `mask` na próbce `nazwa=x`, bez drugiej listy nazw."""
    probe = f"{key}=x"
    return bool(key) and mask(probe, env) != probe


def mask_json(value, key: str = "", env: dict | None = None):
    """Maskuje odkodowane klucze i wartości przed serializacją JSON. Sekretne pole traci całą wartość napisu
    lub liczby także w potomnych obiektach i listach; kolizje zamaskowanych kluczy dostają unikalny dopisek
    liczbowy, bez nadpisywania pól."""
    if isinstance(value, dict):
        secret_parent = _secret_field(key, env)
        entries = [(mask(k, env) if isinstance(k, str) else k, k, v) for k, v in value.items()]
        reserved = {masked for masked, _, _ in entries}
        result, suffixes = {}, {}
        for masked, original, item in entries:
            target = masked
            if target in result:
                number = suffixes.get(masked, 2)
                while f"{masked} [{number}]" in reserved or f"{masked} [{number}]" in result:
                    number += 1
                target = f"{masked} [{number}]"
                suffixes[masked] = number + 1
            # Rezerwujemy także nazwy późniejszych pól. Sekretny rodzic zachowuje sekretność całego poddrzewa;
            # poza nim wartości oceniamy po oryginalnym kluczu (przed maskowaniem i rozstrzyganiem kolizji).
            result[target] = mask_json(item, key if secret_parent else str(original), env)
        return result
    if isinstance(value, list):
        return [mask_json(v, key, env) for v in value]
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return value
    if value != "" and _secret_field(key, env):
        return MASK
    if isinstance(value, str):
        return mask(value, env)
    return value if mask(str(value), env) == str(value) else MASK


def excerpt(lines: list[str] | str, env: dict | None = None, limit: int = EXCERPT_LINES) -> str:
    """Wycinek dziennika kroku do karty: ostatnie `limit` zdarzeń po ludzku (typ i tekst), sekrety zamaskowane.
    Uszkodzony obiekt JSON daje dopisek bez treści. Kontekst opiekuna (PT-153) przekazuje surowy tekst,
    zachowując białe znaki wielowierszowych sekretów, i bierze tak samo ostatnie 300 zdarzeń.
    """
    picked = []
    plain = []

    def add(text):
        if text:
            # najpierw maskowanie, potem skracanie — obcięty w pół token nie przeszedłby przez wzorce
            text = mask(text, env)
            picked.append(text if len(text) <= EXCERPT_LINE_CHARS else text[:EXCERPT_LINE_CHARS - 1] + "…")

    def flush_plain():
        # Surowy stderr maskujemy całym blokiem, zanim podział na linie lub `strip` zgubi granice sekretu.
        # JSON pozostaje osobno: maskowanie jego serializacji mogłoby uszkodzić sekwencje ucieczki.
        for line in mask("".join(plain), env).splitlines():
            if line.lstrip().startswith("{"):
                # Niepełna serializacja nie jest zwykłym tekstem; nie ujawniamy odkodowanych fragmentów.
                add("(pominięto: niepoprawne lub niepełne zdarzenie JSON)")
            else:
                add(line.strip())
        plain.clear()

    raw_lines = lines.splitlines(keepends=True) if isinstance(lines, str) else [line + "\n" for line in lines]
    for line in raw_lines:
        event = _json(line)
        if event is None:
            plain.append(line)
        else:
            flush_plain()
            add(_event_text(event, env))
    flush_plain()
    return "\n".join(picked[-limit:]).replace("```", "'''")


def _event_text(event: dict, env: dict | None = None) -> str:
    # Maskujemy odkodowane wartości przed łączeniem i normalizacją białych znaków. Inaczej wielowierszowy
    # sekret środowiska przestaje pasować do dokładnej wartości rozpoznawanej przez `mask`.
    event = mask_json(event, env=env)
    kind = str(event.get("type") or "")
    parts = []
    for key in ("subtype", "api_error_status"):
        if event.get(key) not in (None, ""):
            parts.append(f"{key}={event[key]}")
    for key in ("result", "message", "error"):
        value = event.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("content") or ""
        if isinstance(value, str) and value.strip():
            parts.append(" ".join(value.split()))
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for item in message.get("content") or [] if isinstance(message.get("content"), list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text"):
            parts.append(" ".join(str(item["text"]).split()))
        elif item.get("type") == "tool_use":
            parts.append(f"{item.get('name') or 'narzędzie'}: {_short(item.get('input'), env)}")
        elif item.get("type") == "tool_result":
            content = item.get("content")
            if isinstance(content, list):
                content = " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
            parts.append(("błąd narzędzia: " if item.get("is_error") else "wynik narzędzia: ") +
                         " ".join(str(content or "").split()))
    for denial in event.get("permission_denials") or []:
        if isinstance(denial, dict):
            parts.append(f"odmowa {denial.get('tool_name') or '?'}: {_short(denial.get('tool_input'), env)}")
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    for key in ("command", "aggregated_output", "text"):
        if isinstance(item.get(key), str) and item[key].strip():
            parts.append(" ".join(item[key].split()))
    if not parts:
        return ""
    return f"[{kind}] " + " · ".join(parts) if kind else " · ".join(parts)


def _short(value, env: dict | None = None) -> str:
    if isinstance(value, dict):
        for key in ("command", "file_path", "notebook_path", "path", "pattern"):
            if isinstance(value.get(key), str):
                return value[key]
        # Maskujemy odkodowane wartości przed serializacją i skróceniem: sekwencje ucieczki JSON
        # zmieniają granice cytowanego sekretu, a obcięty token nie przeszedłby przez wzorce.
        return json.dumps(mask_json(value, env=env), ensure_ascii=False)[:200]
    return str(value or "")


# --- treść karty ---------------------------------------------------------------------------------

def card_title(step_kind: str, fault: str, cause: str, reason_labels: dict | None = None,
               env: dict | None = None) -> str:
    what = mask(cause_label(cause, reason_labels), env) or "błąd"
    if fault == "tide":
        title = f"Błąd Tide w kroku {kind_label(step_kind)}: {what}"
    else:
        title = f"Błąd kroku {kind_label(step_kind)} ({FAULT_LABELS.get(fault, fault)}): {what}"
    return title if len(title) <= 200 else title[:199] + "…"


def occurrence(*, when: str, card: str, step: str, step_kind: str, ticket: str, executor: str, attempt: int,
               result: str, reason: str, fault: str, cause: str, description: str, log_excerpt: str,
               reason_labels: dict | None = None, env: dict | None = None, with_excerpt: bool = True) -> str:
    """Jedno wystąpienie błędu: kiedy, gdzie, czyja wina, opis użytkownika i wycinek dziennika kroku."""
    where = " ".join(part for part in (kind_label(step_kind), ticket) if part)
    head = f"### {when} · {card} · {where}" + (f" ({executor})" if executor else "")
    # Powód wyniku może pisać agent (`result.json`) — przechodzi przez maskowanie jak opis i wycinek.
    reason = " ".join(mask(reason, env).split())
    reason = reason if len(reason) <= EXCERPT_LINE_CHARS else reason[:EXCERPT_LINE_CHARS - 1] + "…"
    lines = [head, "",
             f"- Krok: {step}" + (f" (próba {attempt + 1})" if attempt else "") + f", wynik: {result} / {reason or '—'}",
             f"- Czyja wina: {FAULT_LABELS.get(fault, fault)} — "
             f"{mask(cause_label(cause, reason_labels), env) or 'brak szczegółów'}",
             "", "Opis użytkownika:", ""]
    lines += ["> " + line if line else ">" for line in mask(description, env).strip().splitlines()] or ["> —"]
    if with_excerpt:
        lines += ["", "Wycinek dziennika kroku (sekrety zamaskowane):", "", "```text",
                  log_excerpt or "(dziennik kroku jest pusty albo niedostępny)", "```"]
    return "\n".join(lines) + "\n"


def card_body(*, card: str, sig: str, happened: str, first: str, env: dict | None = None) -> str:
    happened = mask(happened, env)   # „co się stało” przy `unknown` cytuje powód wyniku pisany przez agenta
    return "\n".join([
        f"Błąd zgłoszony z karty {card} przyciskiem „Opisz problem i ponów”.", "",
        f"{SIGNATURE_PREFIX}`{sig}`", "",
        "## Co się stało", "", happened, "",
        "## Wystąpienia", "", first,
    ])


def has_signature(body: str, sig: str) -> bool:
    return f"{SIGNATURE_PREFIX}`{sig}`" in str(body or "").splitlines()
