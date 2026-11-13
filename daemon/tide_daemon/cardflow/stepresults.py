"""Odczyt wyniku kroku (PT-462, K-112): granica zaufania wyników agenta (ADR-5).

Czyste funkcje z jawnym wejściem (ścieżka pliku) i wynikiem jako wartością: bez `self`, bez zapisu, bez importu
silnika. `FlowEngine._result`, `_review_result`, `_raw_result` i `_claude_denied_command` tylko tu delegują.
Składanie wyniku kroku po jego zakończeniu (kod wyjścia, `login`, pośrednik pudełka) zostaje w `FlowEngine._finish`.
"""
from __future__ import annotations

import json
import os

from . import baseproposal
from .decisions import clean_options
from .heavyqueue import exclusive_of

RESULTS = ("done", "question", "denied", "error", "outside")

# Limit rozmiaru odczytywanego wyniku; tyle samo co engine.OUTPUT_MAX (silnik zachowuje własną stałą dla innych odczytów).
RESULT_MAX = 256 * 1024


def raw_result(path: str):
    """Plik wyniku opiekuna: obiekt JSON, None (brak pliku) albo "" (za duży, dowiązanie albo nie JSON)."""
    try:
        if os.path.getsize(path) > RESULT_MAX or os.path.islink(path):
            return ""
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError):
        return ""


def step_result(path: str) -> dict:
    """`result.json` kroku → znormalizowany słownik albo `error` z `no-result`/`bad-result`."""
    try:
        if os.path.getsize(path) > RESULT_MAX:
            return {"result": "error", "reason": "bad-result"}
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"result": "error", "reason": "no-result"}
    except (OSError, ValueError):
        return {"result": "error", "reason": "bad-result"}
    if not isinstance(data, dict) or data.get("result") not in RESULTS:
        return {"result": "error", "reason": "bad-result"}
    if (data["result"] == "question" and
            (not isinstance(data.get("question"), str) or not data["question"].strip())) or \
            (data["result"] in ("denied", "outside") and
             (not isinstance(data.get("command"), str) or not data["command"].strip() or
              any(c in data["command"] for c in "\n\r\0"))):
        return {"result": "error", "reason": "bad-result"}
    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    if data["result"] == "outside":
        # Prośba o uruchomienie poza piaskownicą idzie ścieżką zgody na polecenie (dziennik, decyzja,
        # wznowienie, restart), z flagą `outside` — wynik polecenia dostaje wznowiony krok (03.11).
        # `exclusive` (K-39 #6, PT-376): polecenie wymaga ciszy; zły typ to `false`, nie błąd wyniku.
        return {"result": "denied", "outside": True, "reason": str(data.get("reason") or "")[:600],
                "question": "", "command": data["command"].strip(),
                "exclusive": exclusive_of(data.get("exclusive")),
                "notes": [str(n) for n in notes if isinstance(n, str)][:20]}
    return {"result": data["result"], "reason": str(data.get("reason") or "")[:300],
            "exclusive": exclusive_of(data.get("exclusive")) if data["result"] == "denied" else False,
            "question": str(data.get("question") or "")[:2000], "command": str(data.get("command") or ""),
            "options": clean_options(data.get("options")) if data["result"] == "question" else [],
            "baseProposal": baseproposal.clean(data.get("baseProposal")) if data["result"] == "question" else None,
            "waitFor": str(data.get("waitFor") or "").strip()[:32]
            if data["result"] in ("question", "error") and isinstance(data.get("waitFor"), str) else "",
            "notes": [str(n) for n in notes if isinstance(n, str)][:20]}


def review_result(path: str) -> dict | None:
    """Wynik review: werdykt i liczniki albo None (brak, zły, niespójny)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("result") != "done":
        return None
    verdict = data.get("verdict")
    counts = [data.get(key) for key in ("blockers", "majors", "nits")]
    if verdict not in ("APPROVE", "REQUEST_CHANGES") or any(
            isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 999 for n in counts):
        return None
    if (verdict == "APPROVE") != (counts[0] == 0 and counts[1] == 0):
        return None
    return dict(verdict=verdict, blockers=counts[0], majors=counts[1], nits=counts[2])


def claude_denied_command(path: str) -> str:
    """Końcowa odmowa Bash w dzienniku CLI Claude: polecenie albo "" (agent nie zapisał własnego JSON)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(line) > RESULT_MAX or '"permission_denials"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "result":
                    continue
                for denial in event.get("permission_denials") or []:
                    if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
                        continue
                    tool_input = denial.get("tool_input") or {}
                    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
                    if isinstance(command, str) and command.strip() and not any(c in command for c in "\n\r\0"):
                        return command.strip()
    except OSError:
        pass
    return ""
