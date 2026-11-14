"""Stan kolejki poleceń poza piaskownicą do migawki Tablicy (K-39 #4, PT-374): pole `queue` w `/Board Snapshot`.

Moduł jest czysty: dostaje stany kart (`FlowState` z fazy odczytu ticku, K-98 #3), ustawienia i wywołanie silnika
(`heavy_of`) i zwraca gotowy słownik. Projekt karty to `card.started.project` ze stanu (bez wczytania Tablicy). Nie
czyta dzienników, plików kroków ani ustawień z dysku, więc
`snapshot_json` tylko dołącza wynik policzony raz na tick (`FlowEngine._queue_view`). Zasady kolejki (kto jest ciężki,
kto ruszy) pozostają w `heavyqueue`; tu tylko widok.

Kształt (dodatek do migawki, istniejące pola bez zmian):

    queue = {
      "heavy":   {"limit": 2,
                  "running": [{"step", "card", "ticket", "command", "since", "kind": "outside"|"merge-check",
                               "exclusive": bool}],                      # wymaga ciszy (#6)
                  "waiting": [{… to samo …, "reason": "zdanie z usługi"}],   # w kolejności startu
                  "load": {"avg1", "cores", "threshold", "error"}},        # obciążenie maszyny (#6)
      "outside": {"perProject": 6, "total": 7,                    # limity poleceń poza piaskownicą
                  "occupied": {"total": n, "projects": {slug: n}}, # trwające razem z ciężkimi (liczą się do limitów)
                  "running": [{… "project": slug}], "waiting": [{… "project": slug}]},   # tylko lekkie
      "limits":  {"heavyLimit": 2, "providerSlots": {dostawca: n}, "providerSlotsDefault": 3},
    }

Obciążenie maszyny (`heavy.load`, #6) to jeden odczyt na tick (wstrzykiwany `load`, domyślnie `/proc/loadavg`) —
w fazie ticku, nie w `snapshot_json`. Powody czekania (limit, cisza) to zdania `heavyqueue.admit`. Część `steps` w #10.

Źródła w `FlowState`: trwające — `outside_started` (krok → `outside.started`: polecenie i `since`; ticket z `steps` albo
`merge_check_steps`), czekające — `state.outside_queued` (decyzja „uruchom” bez startu; polecenie z `finished`, czas zgody
z `step_decisions`) oraz próby testów po scaleniu w etapie `queued` (`mergecheck.phase`).
"""
from __future__ import annotations

import time
from typing import Callable, Mapping

from . import heavyqueue, mergecheck
from .state import FlowState, _epoch, approval_live, outside_queued

KIND_OUTSIDE = "outside"
KIND_MERGE_CHECK = mergecheck.KIND

WAITING_HELD = "czeka na wolne miejsce: ciężkie polecenia {busy} z {limit} zajęte"
WAITING_NEXT = "ruszy przy najbliższym sprawdzeniu kolejki"


def _entry(card: str, name: str, ticket: str, command: str, since: str, kind: str, project: str,
           exclusive: bool = False) -> dict:
    return {"step": name, "card": card, "ticket": ticket or "", "command": command or "", "since": since or "",
            "kind": kind, "exclusive": bool(exclusive), "project": project}


def _exclusive(card: str, st: FlowState, name: str, step: dict, command: str, quiet_of) -> bool:
    """Czy polecenie wymaga ciszy (#6): klasa z silnika (`quiet_of`: znacznik kroku i `quietCommands`), bez niej sam
    znacznik `exclusive` z `step.finished`. Próba testów po scaleniu nie wymaga ciszy."""
    if mergecheck.is_step(name):
        return False
    if quiet_of is not None:
        return bool(quiet_of(card, name, step, command))
    return heavyqueue.exclusive_of((st.finished.get(name) or {}).get("exclusive"))


def _running(card: str, st: FlowState, project: str, heavy_of, quiet_of=None) -> list[tuple[bool, dict]]:
    out = []
    for name, started in st.outside_started.items():
        if not isinstance(name, str):
            continue
        step = st.steps.get(name) or st.merge_check_steps.get(name) or {}
        command = started.get("command") or step.get("command") or ""
        merge = mergecheck.is_step(name)
        exclusive = _exclusive(card, st, name, step, command, quiet_of)
        heavy = merge or exclusive or bool(started.get("rerunOf")) or bool(heavy_of(card, name, step, command))
        out.append((heavy, _entry(card, name, step.get("ticket") or started.get("ticket") or "", command,
                                  started.get("ts") or "", KIND_MERGE_CHECK if merge else KIND_OUTSIDE, project,
                                  exclusive)))
    return out


def _waiting(card: str, st: FlowState, project: str, heavy_of, now: float, quiet_of=None) -> list[tuple[bool, dict]]:
    if st.paused or st.card_finished:
        return []        # wstrzymana albo zakończona karta nie zajmuje miejsca w kolejce
    out = []
    for name in outside_queued(st):
        if not approval_live(st, name, now):
            continue     # zgoda sprzed ponownego startu karty albo starsza niż doba: silnik jej nie uruchomi
        step = st.steps.get(name) or {}
        command = (st.finished.get(name) or {}).get("command") or ""
        since = (st.step_decisions.get(name) or {}).get("ts") or ""
        exclusive = _exclusive(card, st, name, step, command, quiet_of)
        out.append((exclusive or bool(heavy_of(card, name, step, command)),
                    _entry(card, name, step.get("ticket") or "", command, since, KIND_OUTSIDE, project, exclusive)))
    for item in mergecheck.gate(st):
        name = mergecheck.step_name(item.get("check"))
        if item.get("status") != mergecheck.PENDING or name in st.outside_started or \
                mergecheck.phase(st, item) != "queued":
            continue
        step = st.merge_check_steps.get(name) or {}
        command = step.get("command") or (item.get("requested") or {}).get("command") or ""
        since = (st.step_decisions.get(name) or {}).get("ts") or ""
        out.append((True, _entry(card, name, item.get("ticket") or "", command, since, KIND_MERGE_CHECK, project)))
    return out


def operations(states: Mapping[str, FlowState], *, now: float | None = None) -> list[dict]:
    """Wszystkie trwające i czekające wpisy kolejki (ciężkie i lekkie, z próbami testów po scaleniu) z tych samych
    źródeł co `build`, bez klasyfikacji ciężkości i bez odczytu obciążenia — dla punktu wejścia „karta ma operację
    w kolejce” warunku spoczynku (K-105 PT-412, `rest.has_queued_operation`)."""
    now = time.time() if now is None else now
    unclassified = lambda *_args: False     # noqa: E731 — klasa polecenia nie zmienia obecności wpisu
    found = []
    for card in sorted(states):
        st = states[card]
        project = str((st.started or {}).get("project") or "")
        found += [entry for _, entry in _running(card, st, project, unclassified, unclassified)]
        found += [entry for _, entry in _waiting(card, st, project, unclassified, now, unclassified)]
    return found


def mark_decisions(card: str, st: FlowState, decisions: list, heavy_of: Callable) -> None:
    """Część techniczna decyzji zgody na polecenie poza piaskownicą dostaje `queue`: `heavy` albo `outside` (ta sama
    klasyfikacja co start polecenia; próba testów po scaleniu jest zawsze ciężka). Okno liczy z tego prognozę na polach
    `queue` migawki (apps/common/qml/card/decision.js). Decyzje bez polecenia poza piaskownicą zostają bez zmian."""
    for decision in decisions:
        tech = decision.get("technical")
        name = decision.get("step") or ""
        if decision.get("kind") != "permission" or not isinstance(tech, dict) or not tech.get("command"):
            continue
        merge = mergecheck.is_step(name)
        if not merge and not (st.finished.get(name) or {}).get("outside"):
            continue
        step = st.steps.get(name) or st.merge_check_steps.get(name) or {}
        tech["queue"] = "heavy" if merge or heavy_of(card, name, step, tech["command"]) else "outside"


def _slots(settings: Mapping) -> dict:
    raw = settings.get("providerSlots") if isinstance(settings, Mapping) else None
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            and v >= 1}


def _describe(entries: list[dict]) -> Callable[[str], str]:
    """Opis polecenia do zdań o ciszy: „make test (K-36)” (polecenie skrócone jak na kartce ticketu)."""
    by_step = {e["step"]: e for e in entries}

    def describe(name: str) -> str:
        entry = by_step.get(name) or {}
        text = " ".join(str(entry.get("command") or name).split())
        text = text if len(text) <= 40 else text[:39] + "…"
        return f"{text} ({entry['card']})" if entry.get("card") else text
    return describe


def build(states: Mapping[str, FlowState], settings: Mapping | None, *, heavy_of: Callable,
          outside_limits: tuple[int, int], provider_slots_default: int = 3, now: float | None = None,
          quiet_of: Callable | None = None, load: Callable[[], "heavyqueue.Load"] = heavyqueue.read_load) -> dict:
    """`states` — karta → `FlowState`; `settings` — ustawienia przepływu (`heavyLimit`, `providerSlots`);
    `heavy_of(card, krok, wpis_kroku, polecenie)` — klasa polecenia z silnika (`True` — ciężkie; próby testów po
    scaleniu i ponowienia modułów są ciężkie bez pytania); `quiet_of` (ten sam podpis) — czy wymaga ciszy (#6;
    bez niego sam znacznik `exclusive` kroku); `load` — odczyt obciążenia maszyny (raz na wywołanie);
    `outside_limits` — (na projekt, łącznie)."""
    settings = settings if isinstance(settings, Mapping) else {}
    now = time.time() if now is None else now
    limit = heavyqueue.limit_of(settings.get("heavyLimit"))
    heavy_running, light_running, heavy_waiting, light_waiting = [], [], [], []
    for card in sorted(states):
        st = states[card]
        project = str((st.started or {}).get("project") or "")
        for heavy, entry in _running(card, st, project, heavy_of, quiet_of):
            (heavy_running if heavy else light_running).append(entry)
        for heavy, entry in _waiting(card, st, project, heavy_of, now, quiet_of):
            (heavy_waiting if heavy else light_waiting).append(entry)
    measured = heavyqueue.measure(load)      # jeden odczyt na tick (błąd odczytu jako `error`)
    queued = {e["step"]: heavyqueue.Waiting(e["step"], _epoch(e["since"]), e["exclusive"], e["card"])
              for e in heavy_waiting}
    admission = heavyqueue.admit(queued.values(), [e["step"] for e in heavy_running], limit,
                                 exclusive_running=[e["step"] for e in heavy_running if e["exclusive"]],
                                 describe=_describe([*heavy_running, *heavy_waiting]), load=lambda: measured)
    waiting = []
    # Kolejność startu to kolejność `heavyqueue` (dziś czas zgody, potem nazwa) — druga kopia porządku nie powstaje.
    for entry in sorted(heavy_waiting, key=lambda e: heavyqueue.default_order(queued[e["step"]])):
        held = admission.held.get(entry["step"])
        reason = held if heavyqueue.quiet_hold(held) else \
            WAITING_HELD.format(busy=admission.busy, limit=admission.limit) if held else WAITING_NEXT
        waiting.append({**{k: v for k, v in entry.items() if k != "project"}, "reason": reason})
    occupied: dict = {}
    for entry in (*heavy_running, *light_running):
        occupied[entry["project"]] = occupied.get(entry["project"], 0) + 1
    per_project, total = outside_limits
    return {
        "heavy": {"limit": limit,
                  "running": [{k: v for k, v in e.items() if k != "project"} for e in heavy_running],
                  "waiting": waiting, "load": measured.as_dict()},
        "outside": {"perProject": per_project, "total": total,
                    "occupied": {"total": len(heavy_running) + len(light_running), "projects": occupied},
                    "running": light_running, "waiting": light_waiting},
        "limits": {"heavyLimit": limit, "providerSlots": _slots(settings),
                   "providerSlotsDefault": provider_slots_default},
    }
