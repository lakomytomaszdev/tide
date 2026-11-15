#!/usr/bin/python3
"""Skala produkcji dzienników dla reproduktora CPU postoju (K-105, PT-410): dane, wybór wątku przepływu, ocena.

Moduł obsługuje tryb `--scale prod` skryptu `perf_idle_cpu.py` (PT-346); sam niczego nie uruchamia poza małym
poleceniem `evaluate`. Wyłącznie dane syntetyczne w katalogu tymczasowym reproduktora.

- `populate(service, ...)` — na Tablicy złożonej przez `perf_board_refresh.Service` (PT-342) zastępuje dzienniki kart
  dziennikami o skali produkcji: większość kart zakończona (`done`/`archived`), kilka aktywnych bez kroku w toku
  (czekająca decyzja „uruchom poza piaskownicą”, pytanie kroku, plan do zatwierdzenia) oraz — K-125 PT-486 — stany,
  które na produkcji dawały 62,7 % CPU: 4 karty wstrzymane z 7 prośbami outside bez decyzji, karta z trzymanym
  procesem K-38 i karta z zatwierdzonym planem w spoczynku (opis w `assign_roles`, kontrola w `check_roles`).
  Dzienniki powstają
  równoważnym generatorem (nie przez `Journal.append`, które czyta cały plik przy każdym zdarzeniu), a każdy
  jest sprawdzany ścisłym odczytem (`Journal.read_complete`) i kontrolą tożsamości procesów usługi — inaczej usługa
  wstrzymałaby odzyskiwanie i pomiar byłby zaniżony. Rozmiar jest łączny dla kopii usługi i pliku w Tablicy.
- `select_flow_thread` — deterministyczny wybór wątku workera przepływu z mapy `threads` okna CPU.
- `evaluate` — ocena jednookienkowa „przed”/„po” **bez progów i bez werdyktu** (K-105, PT-458): CPU wątku przepływu,
  udział `engine.tick` i wzrost `VmRSS` są raportowane („tryb jednego okna — bez oceny progu; rozstrzyga tryb
  10 okien”, `perf_cpu_windows.py`). Zostaje tylko ważność (loadavg, reguła PT-362). Kod wyjścia: 0 ważny, 2 nieważny.

Użycie oceny:
    /usr/bin/python3 daemon/tests/perf_idle_scale.py evaluate --after po.json [--before przed.json] [--out ocena.json]

Testy jednostkowe nie mierzą czasu; ten moduł jest narzędziem pomiaru.
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

FLOW_PREFIX = "tide-cardflow"          # prefiks puli workera przepływu (`cardflow_monitor`)
SAMPLER_NAME = "tide-sampler"          # wątek próbnika usługi
SNAPSHOT_CALLS = 60
SNAPSHOT_INTERVAL_S = 0.5
PROJECTS = ("plasma-tide", "alpha", "beta")
OUTSIDE_COMMANDS = ("make test-changed BASE=0123456789abcdef0123456789abcdef01234567",
                    "/usr/bin/python3 tools/test_runner.py --modules daemon/tests/test_cardflow.py",
                    "bash scripts/run-integration.sh ~/dane")
MIN_CARDS = 4
# K-125 PT-486: stany produkcji, które dawały 62,7 % CPU, a których dawny reproduktor (~10,6 %) nie miał.
# Wstrzymane karty z prośbami outside bez decyzji: 7 próśb w 4 kartach (jak K-104, K-107, K-44, K-100).
PAUSED_REQUESTS = (3, 2, 1, 1)
FULL_PAUSED_CARDS = 12                 # od tylu kart są wszystkie cztery wstrzymane; mniej kart — jedna wstrzymana
PROD_HELD_STEPS = 135                  # kroki chronionych prób w jedynej karcie z trzymanym procesem na produkcji (kształt)
HELD_TICKET = "PT-186"               # ticket chronionej próby K-38 (`trialpolicy.EXCLUDED`) — tożsamość procesu `held`
HANGING_COMMAND = OUTSIDE_COMMANDS[2]  # skrypt powłoki w worktree: `outside_risky` — zgoda zawsze od człowieka
# Atrapa `systemctl` (`perf_idle_cpu.isolated_env`) uznaje za aktywną jednostkę o tym fragmencie nazwy kroku.
HELD_UNIT_FRAGMENT = "-" + HELD_TICKET.lower() + "-"
# Wszystkie tickety chronionych prób K-38 (`trialpolicy.LEGACY | EXCLUDED`): karta z trzymanym procesem może mieć ich
# kilka (tryb `--shape`: liczba trzymanych kroków z produkcji); pierwszy to `HELD_TICKET`.
HELD_TICKETS = (HELD_TICKET, "PT-173", "PT-176", "PT-177", "PT-184", "PT-185", "PT-191")
HELD_UNIT_FRAGMENTS = tuple("-" + t.lower() + "-" for t in HELD_TICKETS)
PLAN_COMMIT_ROLES = ("held", "approved")   # karty z wynikami `card-branch` i `plan-commit` w dzienniku


def nearest_rank(samples, q=0.95):
    """Percentyl nearest-rank: k = ⌈q·n⌉ (1-indeksowane) na posortowanych próbkach."""
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


# --- pamięć i seria Snapshot ------------------------------------------------------------------------

def parse_status_mb(text):
    """`VmRSS` i `VmHWM` z treści `/proc/<pid>/status` w MB (kB / 1024); brak pola → None."""
    out = {"VmRSS": None, "VmHWM": None}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in out and rest.split():
            out[key] = round(int(rest.split()[0]) / 1024.0, 2)
    return out


def read_memory(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as fh:
            return parse_status_mb(fh.read())
    except OSError:
        return {"VmRSS": None, "VmHWM": None}


class SnapshotSeries:
    """Seria `calls` wywołań `Snapshot` (co `interval` s) wołana z pętli okna próbnika; `call()` zwraca ms albo None."""

    def __init__(self, call, calls=SNAPSHOT_CALLS, interval=SNAPSHOT_INTERVAL_S, clock=time.monotonic):
        self.call, self.calls, self.interval, self.clock = call, calls, interval, clock
        self.samples_ms, self.errors = [], 0
        self._next = None

    def step(self):
        """Jedno wywołanie pętli (wołaj często); wykonuje wywołanie, gdy minął odstęp."""
        if len(self.samples_ms) + self.errors >= self.calls:
            return
        now = self.clock()
        if self._next is not None and now < self._next:
            return
        self._next = now + self.interval if self._next is None else self._next + self.interval
        value = self.call()
        if value is None:
            self.errors += 1
        else:
            self.samples_ms.append(round(value, 3))

    def result(self):
        return {"calls": self.calls, "intervalS": self.interval, "samplesMs": self.samples_ms,
                "errors": self.errors, "p95Ms": nearest_rank(self.samples_ms)}


# --- wybór wątku workera przepływu ------------------------------------------------------------------

def select_flow_thread(threads, pid, window_s):
    """`threads`: tid → {name, cpuS} z okna CPU. Zwraca {tid, name, cpuPercent, source} albo None.

    1. jedyny wątek o nazwie zaczynającej się od `tide-cardflow` (source `name`);
    2. inaczej wątek o największym `cpuS` z pominięciem wątku głównego (tid == pid) i próbnika (source `max-cpu`),
       przy remisie — mniejszy tid."""
    items = sorted(((int(tid), value) for tid, value in (threads or {}).items()), key=lambda item: item[0])
    named = [item for item in items if str(item[1].get("name", "")).startswith(FLOW_PREFIX)]
    source = "name"
    if len(named) != 1:
        source = "max-cpu"
        pool = [item for item in items
                if item[0] != int(pid) and not str(item[1].get("name", "")).startswith(SAMPLER_NAME)]
        if not pool:
            return None
        best = max(item[1].get("cpuS", 0.0) for item in pool)
        named = [next(item for item in pool if item[1].get("cpuS", 0.0) == best)]    # pool jest po tid rosnąco
    tid, value = named[0]
    percent = round(100.0 * value.get("cpuS", 0.0) / window_s, 2) if window_s else None
    return {"tid": tid, "name": value.get("name", ""), "cpuPercent": percent, "source": source}


def tick_stats(split, window_s):
    """Udział `engine.tick` w próbkach okna próbnika i jego wartość bezwzględna (próbki/s)."""
    if not split:
        return {"share": None, "samples": None, "perSecond": None}
    samples = (split.get("samples") or {}).get("tick")
    share = (split.get("share") or {}).get("tick")
    return {"share": share, "samples": samples,
            "perSecond": round(samples / window_s, 3) if samples is not None and window_s else None}


# --- kryteria „przed” K-125 (progi ustalone przed pomiarem; po pomiarze się ich nie zmienia) -------------

RED_MEAN_PERCENT = 30.0                # „przed” jest czerwony: średnie CPU procesu > 30 % w co najmniej jednym trybie box
TICK_MIN_SHARE = 0.40                  # `engine.tick` ≥ 40 % próbek pracy
OUTSIDE_TICK_SHARE = (0.30, 0.50)      # `_consent_recheck` 30–50 % próbek ticku
PLAN_MIN_TICK_SHARE = 0.05             # `_plan_next` ≥ 5 % próbek ticku
INVENTORY_MIN_TICK_SHARE = 0.05        # `trialhold.inventory` ≥ 5 % próbek ticku
SYSTEMCTL_MIN_CALLS_PER_TICK = 1.0     # atrapa `systemctl` co najmniej raz na tick karty z trzymanym procesem


def profile_check(result):
    """Kryteria rzędu produkcji profilu „przed” jednego trybu box: lista {name, ok, value, limit}; `ok` None — brak
    pomiaru (traktowane jak niespełnione przez `profile_ok`). Czyta profil okna próbnika i licznik atrapy `systemctl`."""
    split = (result.get("profileWindow") or {}).get("split") or {}
    components = split.get("components") or {}
    fake = result.get("fakeSystemctl") or {}

    def item(name, value, ok, limit):
        return {"name": name, "value": value, "ok": None if value is None else bool(ok(value)), "limit": limit}
    low, high = OUTSIDE_TICK_SHARE
    return [
        # Próbki pracy bez bezczynnych ramek puli i pętli zdarzeń (`perf_idle_cpu.IDLE_FRAMES`); stare wyniki bez `work`
        # liczą się względem wszystkich próbek poza czekaniem.
        item("tick", (split.get("work") or {}).get("tickShare", (split.get("share") or {}).get("tick")),
             lambda v: v >= TICK_MIN_SHARE, f"≥ {TICK_MIN_SHARE}"),
        item("outside", (components.get("outside") or {}).get("shareOfTick"), lambda v: low <= v <= high,
             f"{low}–{high}"),
        item("plan", (components.get("plan") or {}).get("shareOfTick"), lambda v: v >= PLAN_MIN_TICK_SHARE,
             f"≥ {PLAN_MIN_TICK_SHARE}"),
        item("inventory", (components.get("inventory") or {}).get("shareOfTick"),
             lambda v: v >= INVENTORY_MIN_TICK_SHARE, f"≥ {INVENTORY_MIN_TICK_SHARE}"),
        item("systemctlPerTick", fake.get("callsPerTick"), lambda v: v >= SYSTEMCTL_MIN_CALLS_PER_TICK,
             f"≥ {SYSTEMCTL_MIN_CALLS_PER_TICK}"),
    ]


def profile_ok(criteria):
    return all(c["ok"] is True for c in criteria)


# --- ocena przed/po ---------------------------------------------------------------------------------

def load1(text):
    try:
        return float(str(text).split()[0])
    except (IndexError, ValueError):
        return None


def validity(result):
    """(ważny, powody): loadavg 1-min < rdzenie/2 przed pomiarem, w oknie i po nim (reguła PT-362)."""
    cores = result.get("cpuCount") or 1
    reasons = []
    for key in ("loadavgBefore", "loadavgWindowStart", "loadavgWindowEnd", "loadavgAfter"):
        value = load1(result.get(key))
        if value is None:
            reasons.append(f"brak odczytu {key}")
        elif value >= cores / 2:
            reasons.append(f"{key} {value} ≥ {cores / 2} (rdzenie/2)")
    return not reasons, reasons


def _flow(result):
    return (result.get("flowThread") or {})


def _delta(before, after):
    if before is None or after is None:
        return None
    return round(after - before, 3)


SINGLE_WINDOW_NOTE = "tryb jednego okna — bez oceny progu; rozstrzyga tryb 10 okien"


def evaluate(after, before=None):
    """Ocena jednookienkowa „po” z opcjonalnym porównaniem z „przed” — **bez progów i bez werdyktu** (K-105, PT-458).

    CPU wątku workera przepływu, udział `engine.tick`, `Snapshot` p95, CPU procesu i wzrost `VmRSS` są tylko
    raportowane. Rozstrzyga tryb 10 okien CPU całego procesu (`perf_cpu_windows.py`). Zostaje ważność: wynik jest
    nieważny (`valid` false), gdy loadavg 1-min ≥ rdzenie/2 przed pomiarem, w oknie albo po nim (reguła PT-362)."""
    flow, tick = _flow(after), after.get("tick") or {}
    flow_cpu, share = flow.get("cpuPercent"), tick.get("share")
    ok_run, reasons = validity(after)
    if before is not None:
        ok_before, reasons_before = validity(before)
        ok_run = ok_run and ok_before
        reasons += [f"przed: {reason}" for reason in reasons_before]
    memory = after.get("memory") or {}
    growth = None
    if before is not None:
        start, end = (before.get("memory") or {}).get("vmRssEndMb"), memory.get("vmRssEndMb")
        if start is not None and end is not None:
            growth = {"beforeMb": start, "afterMb": end, "growthMb": round(end - start, 2),
                      "growthPercent": round(100.0 * (end - start) / start, 2) if start else None,
                      "limit": None}
    comparison = None
    if before is not None:
        comparison = {
            "flowThreadCpuPercent": {"before": _flow(before).get("cpuPercent"), "after": flow_cpu,
                                     "delta": _delta(_flow(before).get("cpuPercent"), flow_cpu)},
            "engineTickShare": {"before": (before.get("tick") or {}).get("share"), "after": share,
                                "delta": _delta((before.get("tick") or {}).get("share"), share)},
            "engineTickPerSecond": {"before": (before.get("tick") or {}).get("perSecond"),
                                    "after": tick.get("perSecond"),
                                    "delta": _delta((before.get("tick") or {}).get("perSecond"),
                                                    tick.get("perSecond"))},
        }
    return {
        "mode": SINGLE_WINDOW_NOTE, "valid": ok_run, "invalidReasons": reasons,
        "reported": {"flowThreadCpuPercent": flow_cpu, "flowThread": flow.get("name"),
                     "flowThreadSource": flow.get("source"), "engineTickShare": share,
                     "engineTickPerSecond": tick.get("perSecond"),
                     "processCpuPercent": (after.get("cpuWindow") or {}).get("cpuPercent"),
                     "snapshotP95Ms": (after.get("snapshot") or {}).get("p95Ms"),
                     "vmHwmMb": memory.get("vmHwmMb")},
        "comparison": comparison, "vmRssGrowth": growth,
        "note": "Wskaźnik reproduktora (" + SINGLE_WINDOW_NOTE + "); rozstrzyga pomiar produkcji przy domknięciu karty.",
    }


def evaluation_text(evaluation):
    reported = evaluation["reported"]
    lines = [evaluation["mode"]]
    if evaluation["invalidReasons"]:
        lines.append("nieważny: " + "; ".join(evaluation["invalidReasons"]))
    lines.append(f"bez progu: CPU wątku przepływu {reported['flowThreadCpuPercent']} % (wątek {reported['flowThread']}, "
                 f"źródło {reported['flowThreadSource']}), udział engine.tick {reported['engineTickShare']} "
                 f"({reported['engineTickPerSecond']}/s)")
    lines.append(f"bez progu: CPU procesu {reported['processCpuPercent']} %, Snapshot p95 {reported['snapshotP95Ms']} ms, "
                 f"VmHWM {reported['vmHwmMb']} MB")
    growth = evaluation["vmRssGrowth"]
    if growth:
        lines.append(f"VmRSS: {growth['beforeMb']} → {growth['afterMb']} MB ({growth['growthMb']:+} MB, "
                     f"{growth['growthPercent']:+} %), bez progu")
    comparison = evaluation["comparison"]
    if comparison:
        for name, item in comparison.items():
            lines.append(f"{name}: przed {item['before']} → po {item['after']} (zmiana {item['delta']})")
    lines.append(evaluation["note"])
    return "\n".join(lines)


# --- dzienniki o skali produkcji --------------------------------------------------------------------

def assign_roles(count):
    """Role kart po indeksie: `outside`, `question`, `plan` (aktywne, bez kroku w toku), `done`, `archived` oraz stany
    produkcji K-125: `paused` (wstrzymana, prośby outside bez decyzji — `PAUSED_REQUESTS`), `held` (trzymany proces
    K-38) i `approved` (zatwierdzony plan w spoczynku, ticket czeka na bloker). Wszystkie są niezakończone, więc co tick
    przechodzą pełny przebieg karty (`rest.at_rest`, czynnik `card-unfinished`).

    Od `FULL_PAUSED_CARDS` kart są cztery wstrzymane; w mniejszej skali jedna, a przy najmniejszych — tyle rodzajów,
    ile mieści się obok co najmniej jednej karty zakończonej (kolejność: wstrzymana, trzymany proces, plan)."""
    if count < MIN_CARDS:
        raise ValueError(f"tryb skali wymaga co najmniej {MIN_CARDS} kart")
    active = max(3, round(count * 0.2))
    outside = min(3, max(1, active // 3))
    question = min(3, max(1, active // 3))
    plan = active - outside - question
    paused = len(PAUSED_REQUESTS) if count >= FULL_PAUSED_CARDS else 1
    extra = (["paused"] * paused + ["held", "approved"])[:max(0, count - 1 - active)]
    groups = [["outside"] * outside, ["question"] * question, ["plan"] * plan,
              ["paused"] * extra.count("paused"), ["held"] * extra.count("held"),
              ["approved"] * extra.count("approved")]
    kinds = []
    while any(groups):                                   # kolejno po jednej karcie z każdej grupy
        for group in groups:
            if group:
                kinds.append(group.pop())
    active = len(kinds)
    spots = {int(i * count / active): kind for i, kind in enumerate(kinds)}
    roles, finished = [], 0
    for index in range(count):
        if index in spots:
            roles.append(spots[index])
        else:
            finished += 1
            roles.append("archived" if finished % 6 == 0 else "done")
    return roles


def load_shape(path):
    """Kształt Tablicy produkcji z `perf_prod_shape.py` (same liczby); sprawdza minimum, którego używa generator."""
    with open(path, encoding="utf-8") as fh:
        shape = json.load(fh)
    cards = shape.get("cards") or []
    if shape.get("kind") != "tide-prod-shape" or len(cards) < MIN_CARDS:
        raise ValueError(f"plik kształtu {path} nie jest wynikiem perf_prod_shape.py albo ma za mało kart")
    return shape


def shape_plan(shape):
    """Role i rozmiary kart z kształtu produkcji: (role, prośby wstrzymanych, trzymane kroki, rozmiary dzienników w B).

    Karta zakończona → `done`/`archived`; wstrzymana z prośbami outside bez decyzji → `paused` (liczba próśb z produkcji);
    z trzymanym procesem → `held` (liczba trzymanych kroków); niezakończona z prośbą outside → `outside`; z pytaniem →
    `question`; z zatwierdzonym planem → `approved`; pozostałe → `plan` (dziennik z Tablicy reproduktora, bez rozmiaru).
    Rozmiary: bajty dziennika karty na produkcji (jedna kopia)."""
    roles, requests, held_steps, sizes = [], {}, {}, []
    for index, card in enumerate(shape["cards"]):
        sizes.append(int(card.get("bytes") or 0))
        if card.get("held"):                       # trzymany proces żyje także na karcie zakończonej (K-38 na produkcji)
            roles.append("held")
            held_steps[index] = max(1, int(card.get("heldSteps") or 1))
        elif card.get("finished"):
            roles.append("archived" if card.get("column") == "archived" else "done")
        elif card.get("paused") and card.get("undecidedOutside"):
            roles.append("paused")
            requests[index] = int(card["undecidedOutside"])
        elif card.get("undecidedOutside") or card.get("permission"):
            roles.append("outside")
        elif card.get("question"):
            roles.append("question")
        elif card.get("approved"):
            roles.append("approved")
        else:
            roles.append("plan")
    return roles, requests, held_steps, sizes


def paused_requests(roles):
    """Indeks karty wstrzymanej → liczba jej próśb outside bez decyzji (`PAUSED_REQUESTS` po kolei)."""
    return {index: PAUSED_REQUESTS[n] for n, index in enumerate(i for i, role in enumerate(roles) if role == "paused")}


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).astimezone().isoformat(timespec="microseconds")


class _Log:
    """Zdarzenia jednej karty z poprawnym `seq` i `ts` (przesunięcia czasu stemplowane na końcu)."""

    def __init__(self, card, rng):
        self.card, self.rng = card, rng
        self.events, self.offsets, self.clock = [], [], 0.0

    def add(self, type_, /, **data):
        self.clock += self.rng.uniform(0.5, 25.0)
        self.events.append({"v": 1, "seq": len(self.events) + 1, "ts": "", "card": self.card, "type": type_, **data})
        self.offsets.append(self.clock)

    def stamp(self, end):
        for event, offset in zip(self.events, self.offsets):
            event["ts"] = _iso(end - (self.clock - offset))


def _text(rng, words):
    pool = ("kontener", "indeks", "migawka", "krok", "ticket", "plan", "review", "scalenie", "dziennik", "karta",
            "projekt", "gałąź", "worktree", "wynik", "decyzja", "notka", "test", "sonda", "pudełko", "sieć")
    return " ".join(rng.choice(pool) for _ in range(words))


def _activity(step, ticket, rng):
    return {"step": step, "ticket": ticket, "files": [f"daemon/tide_daemon/{_text(rng, 1)}_{rng.randrange(99)}.py"
                                                      for _ in range(rng.randrange(1, 4))],
            "sessionBytes": rng.randrange(10_000, 900_000)}


def _identity(card, project, base, step, kind, ticket):
    low = card.lower()
    branch = f"{low}/{ticket.lower()}" if ticket else f"card/{low}"
    return {"ticket": ticket, "kind": kind, "branch": branch, "worktree": os.path.join(base, "wt", low, step),
            "stepDir": os.path.join(base, "board", "flow", card, "steps", step), "unit": f"tide-step-{step}"}


def _start(log, ident, step, project, repo, executor="Sonnet", **extra):
    fields = dict(ident)
    log.add("intent", action="step-start", key=step, **fields, attempt=0)
    log.add("result", action="step-start", key=step, ok=True, **fields)
    log.add("step.started", step=step, **fields, attempt=0, round=extra.get("round", 1), executor=executor,
            program="claude", model="sonnet", provider="anthropic", effort="medium", local=False,
            session=f"{step}-session", gitDir=os.path.join(ident["worktree"], ".git"), repo=repo,
            deliveredNoteIds=extra.get("notes", []), readDirsProfile=False, reviewTip="", parentStep=extra.get(
                "parent", ""))


def _finish(log, step, ticket, result="done", **extra):
    log.add("step.finished", step=step, ticket=ticket, result=result, **extra)


def _ticket_flow(log, card, project, repo, base, ticket, rng, marks, outside_done, last, note_id, command=None):
    """Ticket: krok autora, (outside), review (czasem z poprawką), scalenie. `last` — stan końcowy ticketu.
    `command` — polecenie prośby outside (`last == "outside"`); domyślnie losowe z `OUTSIDE_COMMANDS`."""
    step = f"{card.lower()}-{ticket.lower()}-ticket-1"
    ident = _identity(card, project, base, step, "ticket", ticket)
    if note_id:
        log.add("step.note-added", noteId=note_id, orderSeq=len(log.events), requestId=f"req-{note_id}", step=step,
                text=_text(rng, 8))
    _start(log, ident, step, project, repo, notes=[note_id] if note_id else [])
    marks.append((step, ticket, len(log.events)))
    if last == "outside":
        _finish(log, step, ticket, result="denied", outside=True, canAllow=True, command=command or OUTSIDE_COMMANDS[
            rng.randrange(len(OUTSIDE_COMMANDS))], pattern="make test-changed *", reason="pełne testy ticketu")
        return
    if last == "question":
        _finish(log, step, ticket, result="question", question="Czy przenieść wspólny fragment do pomocnika?",
                options=[{"title": "Tak, przenieś", "effect": "Krok przeniesie fragment i doda test.",
                          "recommended": True}, {"title": "Nie, zostaw", "effect": "Krok zostawi kod bez zmian."}])
        return
    if outside_done:
        command = OUTSIDE_COMMANDS[rng.randrange(2)]
        _finish(log, step, ticket, result="denied", outside=True, canAllow=True, command=command,
                pattern="make test-changed *", reason="pełne testy ticketu")
        log.add("step.decision", step=step, ticket=ticket, kind="ticket", choice="run", automatic=False)
        log.add("outside.started", step=step, ticket=ticket, worktree=ident["worktree"], unit=f"tide-outside-{step}",
                command=command, log=os.path.join(ident["stepDir"], "outside.log"), head="0123abc")
        log.add("outside.finished", step=step, ticket=ticket, command=command, exit=0, tail="OK")
        step2 = f"{card.lower()}-{ticket.lower()}-ticket-2"
        ident2 = _identity(card, project, base, step2, "ticket", ticket)
        _start(log, ident2, step2, project, repo, parent=step)
        marks.append((step2, ticket, len(log.events)))
        _finish(log, step2, ticket, notes=_text(rng, 12), exit=0)
    else:
        _finish(log, step, ticket, notes=_text(rng, 12), exit=0)
    rounds = 2 if rng.random() < 0.35 else 1
    for round_no in range(1, rounds + 1):
        review = f"{card.lower()}-{ticket.lower()}-review-{round_no}"
        rid = _identity(card, project, base, review, "review", ticket)
        _start(log, rid, review, project, repo, executor="Opus", round=round_no)
        _finish(log, review, ticket)
        approve = round_no == rounds
        log.add("review.verdict", step=review, ticket=ticket, round=round_no, reviewer="Opus", reviewTip="abc1234",
                verdict="APPROVE" if approve else "REQUEST_CHANGES",
                findings=[] if approve else [{"severity": "MAJOR", "text": _text(rng, 14), "file": "daemon/x.py"}])
        if not approve:
            fix = f"{card.lower()}-{ticket.lower()}-fix-{round_no}"
            fid = _identity(card, project, base, fix, "fix", ticket)
            _start(log, fid, fix, project, repo)
            marks.append((fix, ticket, len(log.events)))
            _finish(log, fix, ticket, notes=_text(rng, 10), exit=0)
    log.add("intent", action="merge", key=ticket, branch=ident["branch"], into=f"card/{card.lower()}",
            cardTip="abc1234", expectedTip="abc1234")
    log.add("result", action="merge", key=ticket, ok=True, commit="def5678", resolved=False)


def _tickets(number, count):
    """Numery ticketów karty. Offset 1000 omija `trialpolicy.LEGACY`/`EXCLUDED` (PT-173, PT-191…): przypadkowy ticket
    chronionej próby zrobiłby z karty kartę z trzymanym procesem — tę rolę nadaje tylko `HELD_TICKET`."""
    return [f"PT-{1000 + number * 10 + i}" for i in range(1, count + 1)]


def _held_flow(log, card, project, repo, base, tickets, rng, marks, steps=0):
    """Chronione próby K-38: kroki zakończone (bez kroku w toku), przekazanie i inwentaryzacja jak po pierwszym ticku —
    jednostki kroków są dla atrapy `systemctl` aktywne (`HELD_UNIT_FRAGMENTS`), procesy trzymane żyją; tick odczytuje
    stan każdej jednostki.

    Pierwsze `len(tickets)` kroków to po jednym kroku autora na ticket z `tickets`; gdy `steps` jest większe (na produkcji
    jedyna karta z trzymanym procesem ma 135 kroków chronionych prób: poprawki i kolejne podejścia), dalsze kroki
    `fix` wracają do tych samych ticketów po kolei — każdy to osobna jednostka, którą inwentaryzacja pyta o stan."""
    from tide_daemon import trialpolicy
    items = []
    for n in range(max(steps, len(tickets))):
        ticket, round_no = tickets[n % len(tickets)], n // len(tickets)
        kind, suffix = ("ticket", "ticket-1") if round_no == 0 else ("fix", f"fix-{round_no}")
        step = f"{card.lower()}-{ticket.lower()}-{suffix}"
        ident = _identity(card, project, base, step, kind, ticket)
        _start(log, ident, step, project, repo)
        marks.append((step, ticket, len(log.events)))
        _finish(log, step, ticket, notes=_text(rng, 12), exit=0)
        log.add("k38.handoff", step=step, ticket=ticket, version=1, instruction=trialpolicy.MESSAGE, preserved=True,
                excluded=ticket in trialpolicy.EXCLUDED)
        items.append({"step": step, "ticket": ticket, "state": "active", "unit": ident["unit"],
                      "worktree": ident["worktree"], "stepDir": ident["stepDir"], "branch": ident["branch"],
                      "wip": {}, "kind": "step"})
    log.add("k38.inventory", version=1, items=items,
            instruction="Zachowano procesy i WIP. Nie cofnięto wcześniejszych skutków.")


def build_card(card, number, role, project, repo, base, target_bytes, now, seed, requests=0, held_steps=1):
    """Zdarzenia jednej karty (lista słowników z `seq`, `ts`, `card`, `type`) i liczba zdarzeń wypełniających.

    `requests` — rola `paused`: liczba próśb outside bez decyzji (po jednym tickecie na prośbę);
    `held_steps` — rola `held`: liczba trzymanych kroków (co najmniej jeden na ticket chronionej próby; więcej — kroki
    `fix` tych samych ticketów)."""
    rng = random.Random(f"{seed}-{number}")
    log = _Log(card, rng)
    if role == "held":
        tickets = list(HELD_TICKETS[:max(1, min(held_steps, len(HELD_TICKETS)))])
    elif role == "paused":
        tickets = _tickets(number, max(1, requests))
    else:
        tickets = _tickets(number, 3)
    # Zatwierdzony plan w spoczynku: pierwszy ticket przechodzi całą drogę, pozostałe czekają na bloker z innej karty
    # (żaden krok w toku, `_plan_next` nie ma czego uruchomić).
    waiting = f"PT-{1000 + (number + 1) * 10 + 1}"
    blockers = {t: [waiting] for t in tickets[1:]} if role == "approved" else {}
    if role == "held":
        # Bez nazwanego powodu czekania (`guard_reason`) karta stałaby się „zatrzymana” i opiekun zapisywałby zdarzenia
        # co tick; bloker z innej karty to nazwany powód („blokery ticketów”).
        blockers = {t: [waiting] for t in tickets}
    log.add("card.started", project=project, repo=repo, entry="plan", executor="Sonnet", program="claude",
            provider="anthropic", model="sonnet", check="make test", column="todo", prefix="PT", mode="full",
            baseRef="refs/heads/master", baseBranch="master", baseCommit="0123abc")
    plan = f"{card.lower()}-planning-1"
    pid = _identity(card, project, base, plan, "planning", "")
    _start(log, pid, plan, project, repo)
    _finish(log, plan, "", notes=_text(rng, 10))
    rows = [{"id": t, "workId": t, "title": f"Ticket {t}", "blockers": blockers.get(t, []), "executor": "Sonnet",
             "reviewer": "Opus", "class": "standard", "note": ""} for t in tickets]
    log.add("plan.proposed", title=f"Plan {card}", prefix="PT", rows=rows, planner="Opus", box=None)
    log.add("plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
    log.add("plan.approved", rows=rows, map={}, planFile=f"docs/agents/plans/{card.lower()}.md")
    for ticket in tickets:
        log.add("ticket.assigned", ticket=ticket, executor="Sonnet", reviewer="Opus", program="claude",
                provider="anthropic", model="sonnet")
    if role in PLAN_COMMIT_ROLES:
        # Wynik `card-branch` (gałąź karty już jest) — bez niego pierwszy tick zakładałby worktree w Gicie; wynik
        # `plan-commit` dopisuje `expand` po ostatnim zdarzeniu (klucz zależy od `seq`).
        branch, worktree = f"card/{card.lower()}", os.path.join(base, "wt", card.lower(), "card")
        log.add("intent", action="card-branch", key=branch, repo=repo, branch=branch, worktree=worktree,
                base="refs/heads/master", baseRef="refs/heads/master", baseBranch="master")
        log.add("result", action="card-branch", key=branch, ok=True, branch=branch, worktree=worktree,
                base="0123abc", existing=False)
    marks = []
    outside_at = rng.randrange(len(tickets))
    pending = {"outside": "outside", "question": "question"}.get(role, "")
    finished_card = role in ("done", "archived")
    if role == "held":
        _held_flow(log, card, project, repo, base, tickets, rng, marks, steps=held_steps)
    elif role == "paused":
        for ticket in tickets:                 # każda prośba to osobny ticket: krok `denied` + `outside`, bez decyzji
            _ticket_flow(log, card, project, repo, base, ticket, rng, marks, outside_done=False, last="outside",
                         note_id="", command=HANGING_COMMAND)
        log.add("card.paused")
    elif role == "approved":
        _ticket_flow(log, card, project, repo, base, tickets[0], rng, marks, outside_done=False, last="",
                     note_id=f"n{number}")
    else:
        open_index = 2 if pending else -1
        for index, ticket in enumerate(tickets):
            if index > open_index >= 0:
                break
            last = pending if index == open_index else ""
            _ticket_flow(log, card, project, repo, base, ticket, rng, marks,
                         outside_done=(index == outside_at and not last), last=last,
                         note_id=f"n{number}" if index == 0 and not pending else "")
    if finished_card:
        close = f"{card.lower()}-close-1"
        cid = _identity(card, project, base, close, "close", "")
        _start(log, cid, close, project, repo)
        _finish(log, close, "", notes="Podsumowanie karty.")
        log.add("card.closed", step=close, summary=_text(rng, 20), check="make test", baseCommit="0123abc",
                baseVersion=0)
        # Kształt zdarzeń jak po udanej operacji usługi (_resume_main_merge): zamiar z polami operacji i checkpoint
        # `projected` — inaczej każdy tick wchodzi w odzyskiwanie scalenia zamiast być postojem.
        key = f"main-{card}"
        merge = {"resultCommit": "fed9876", "resultTree": "0123abc", "sourceCommit": "0123abc"}
        log.add("intent", action="main-merge", key=key, operationId=key, repo=repo, requestSeq=1, baseVersion=0,
                sourceWorktree=f"{repo}/{card.lower()}", sourceRef=f"refs/heads/card/{card.lower()}",
                targetRef="refs/heads/main", targetBranch="main", targetWorktree=repo,
                targetCommitBefore="0123abc", **merge)
        for stage in ("prepared", "materialized", "ref-moved"):
            log.add("main-merge.checkpoint", key=key, stage=stage)
        log.add("result", action="main-merge", key=key, ok=True, commit="fed9876", **merge)
        log.add("main-merge.checkpoint", key=key, stage="result-saved")
        log.add("card.merged", branch=f"card/{card.lower()}", commit="fed9876", operationId=key, **merge)
        log.add("card.finished", column="done")
        log.add("main-merge.checkpoint", key=key, stage="projected")
    base_bytes = sum(len(json.dumps(e, ensure_ascii=False)) + 1 for e in log.events)
    sample = len(json.dumps({"v": 1, "seq": 99999, "ts": "2026-11-12T12:00:00.000000+01:00", "card": card,
                             "type": "step.activity", **_activity("x" * 30, "PT-1", rng)}, ensure_ascii=False)) + 1
    extra = max(0, (target_bytes - base_bytes) // sample) if marks else 0
    return log, marks, extra


def expand(log, marks, extra, now, role):
    """Wstawia `extra` zdarzeń `step.activity` po startach kroków autora (po równo) i stempluje czas."""
    ordinal = {index: n for n, (_step, _ticket, index) in enumerate(marks)}
    per, rest = divmod(extra, len(marks)) if marks else (0, 0)
    out, offsets, shift = [], [], 0.0
    for index, (event, offset) in enumerate(zip(log.events, log.offsets)):
        out.append(event)
        offsets.append(offset + shift)
        n = ordinal.get(index + 1)
        if n is None:
            continue
        step, ticket, _index = marks[n]
        count = per + (1 if n < rest else 0)
        for k in range(count):
            out.append({"v": 1, "seq": 0, "ts": "", "card": log.card, "type": "step.activity",
                        **_activity(step, ticket, log.rng)})
            offsets.append(offset + shift + (k + 1) * 15.0)
        shift += count * 15.0
    for seq, event in enumerate(out, 1):
        event["seq"] = seq
    log.events, log.offsets, log.clock = out, offsets, offsets[-1]
    if role in PLAN_COMMIT_ROLES:
        _plan_commit_result(log)
    log.stamp(now - 120.0 if role in ("outside", "question") else now - 86400.0 * 3)
    return log.events


def plan_commit_key(st):
    """Klucz akcji `plan-commit` z `FlowEngine._plan_commit`: `<seq zatwierdzenia planu>:<seq ostatniej zmiany planu>`."""
    return f"{(st.plan_approved or {}).get('seq')}:{st.plan_change_seq}"


def _plan_commit_result(log):
    """Wynik `plan-commit` o kluczu, który silnik policzy z dziennika (ostatnia zmiana planu przed tym wpisem): bez niego
    każdy tick karty z zatwierdzonym planem zaczynałby od commita planu w Gicie. Para zamiar–wynik nie zmienia klucza."""
    from tide_daemon.cardflow import state
    st = state.fold(log.events)
    branch = f"card/{log.card.lower()}"
    worktree = (st.results.get(("card-branch", branch)) or {}).get("worktree", "")
    repo = next((e["repo"] for e in log.events if e["type"] == "intent" and e.get("action") == "card-branch"), "")
    key = plan_commit_key(st)
    log.add("intent", action="plan-commit", key=key, branch=branch, repo=repo, worktree=worktree)
    log.add("result", action="plan-commit", key=key, ok=True, commit="abc1234")


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def disk_stats(board_root, mirror_dir):
    """Rozmiar dzienników zmierzony na dysku: kopia usługi, plik w Tablicy i liczba kart z dziennikiem."""
    def sizes(paths):
        return sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
    flow = os.path.join(board_root, "flow")
    board = [os.path.join(flow, name, "journal.jsonl") for name in sorted(os.listdir(flow))] if os.path.isdir(flow) else []
    mirror = [os.path.join(mirror_dir, name) for name in sorted(os.listdir(mirror_dir))
              if name.endswith(".jsonl")] if os.path.isdir(mirror_dir) else []
    mirror_bytes, board_bytes = sizes(mirror), sizes(board)
    return {"cards": len(mirror), "mirrorBytes": mirror_bytes, "boardBytes": board_bytes,
            "totalBytes": mirror_bytes + board_bytes, "totalMb": round((mirror_bytes + board_bytes) / 1048576.0, 2)}


def verify(journal, cards):
    """Ścisły odczyt i kontrola tożsamości procesów każdego dziennika; zwraca listę problemów (pusta — ok)."""
    from tide_daemon.cardflow.processidentity import process_identities
    problems, events = [], 0
    for card in cards:
        try:
            read = journal.read_complete(card)
        except (OSError, ValueError) as exc:
            problems.append(f"{card}: ścisły odczyt: {exc}")
            continue
        events += len(read)
        for name, identity in process_identities(read).items():
            if identity.state != "ok":
                problems.append(f"{card}: tożsamość procesu {name!r}: {identity.state}")
    return problems, events


def check_roles(engine, journal, cards, roles, requests):
    """Kontrola stanu każdej roli na dziennikach z dysku (K-125 PT-486): zwraca (statystyki, lista problemów).

    Sprawdza to, co ma uruchamiać drogi ticku: liczbę decyzji `permission` w kartach wstrzymanych (prośby bez
    decyzji, polecenie zawsze od człowieka) i aktywnych, `paused`, tożsamość `held`, zatwierdzony plan z wynikami
    `card-branch` i `plan-commit` (ticket czeka na bloker) oraz brak kroków w toku. Karta bez swojej roli nie ma
    cudzych stanów (wstrzymania, trzymanego procesu). Dane, które stanu nie dają, są odrzucane."""
    from tide_daemon.board import commands
    from tide_daemon.cardflow import consent, decisions, state
    problems, found = [], {"pausedDecisions": 0, "pausedRequests": [], "outsideDecisions": 0, "questionDecisions": 0,
                           "heldCards": 0, "approvedCards": 0}
    for index, card in enumerate(cards):
        role = roles[index]
        st = state.fold(journal.read_complete(card))
        kinds = [d.get("kind") for d in decisions.build_decisions(st)]
        held = [name for name, identity in st.process_identities.items() if identity.held]
        if st.running():
            problems.append(f"{card}: krok w toku")
        if st.paused != (role == "paused"):
            problems.append(f"{card}: paused {st.paused}, rola {role}")
        if bool(held) != (role == "held"):
            problems.append(f"{card}: trzymany proces {held}, rola {role}")
        if role in ("done", "archived") and not st.card_finished:
            problems.append(f"{card}: karta zakończona bez card.finished")
        elif role == "outside":
            found["outsideDecisions"] += kinds.count("permission") == 1
        elif role == "question":
            found["questionDecisions"] += "question" in kinds
        elif role == "paused":
            found["pausedDecisions"] += kinds.count("permission")
            found["pausedRequests"].append(kinds.count("permission"))
            problems.extend(_paused_problems(engine, card, st, kinds, requests.get(index, 0), commands, consent))
        elif role == "held":
            found["heldCards"] += 1
            problems.extend(_held_problems(card, st, held))
        elif role == "approved":
            found["approvedCards"] += 1
            problems.extend(_approved_problems(card, st))
    if found["outsideDecisions"] != roles.count("outside") or found["questionDecisions"] != roles.count("question"):
        problems.append(f"czekające decyzje: outside {found['outsideDecisions']}/{roles.count('outside')}, "
                        f"pytania {found['questionDecisions']}/{roles.count('question')}")
    wanted = sum(requests.values())
    if found["pausedDecisions"] != wanted:
        problems.append(f"decyzje permission w kartach wstrzymanych: {found['pausedDecisions']}/{wanted}")
    return found, problems


def _paused_problems(engine, card, st, kinds, wanted, commands, consent):
    problems = []
    if kinds.count("permission") != wanted:
        problems.append(f"{card}: decyzje permission {kinds.count('permission')}/{wanted}")
    asked = [name for name, done in st.finished.items() if done.get("outside") and done.get("result") == "denied"]
    if len(asked) != wanted:
        problems.append(f"{card}: prośby outside {len(asked)}/{wanted}")
    project = engine._project(card)[1]
    for name in asked:
        step, done = st.steps.get(name) or {}, st.finished[name]
        command = done.get("command") or ""
        roots = (step.get("worktree") or "", project.repo, step.get("stepDir") or "")
        if name in st.step_decisions:
            problems.append(f"{card}: prośba {name} ma decyzję")
        if not commands.outside_allowed(command, project.denied_commands, roots) or not done.get("canAllow"):
            problems.append(f"{card}: polecenie {name} odrzucone przez reguły (prośba nie ma iść ścieżką zgody)")
        if not commands.outside_risky(command, roots, []):
            problems.append(f"{card}: polecenie {name} mogłoby dostać automatyczną zgodę")
        if consent.automatic(engine, card, step, command):
            problems.append(f"{card}: prośba {name} dostałaby automatyczną zgodę")
    return problems


def _held_problems(card, st, held):
    problems = []
    for name in held:
        identity = st.process_identities[name]
        if identity.state != "ok":
            problems.append(f"{card}: tożsamość {name!r} {identity.state}, ma być chroniona (ok)")
        if not (st.steps.get(name) or {}).get("unit"):
            problems.append(f"{card}: krok {name!r} bez jednostki — inwentaryzacja nie odczyta stanu")
    return problems


def _approved_problems(card, st):
    problems = []
    rows = (st.plan_approved or {}).get("rows") or []
    if not rows:
        return [f"{card}: brak zatwierdzonego planu"]
    key = plan_commit_key(st)
    if not (st.results.get(("plan-commit", key)) or {}).get("ok"):
        problems.append(f"{card}: brak wyniku plan-commit o kluczu {key}")
    if not any(res.get("ok") for (action, _key), res in st.results.items() if action == "card-branch"):
        problems.append(f"{card}: brak wyniku card-branch")
    merged = {t for (action, t), made in st.results.items() if action == "merge" and made.get("ok")}
    waiting = [row["id"] for row in rows if row["id"] not in merged and row["blockers"] and
               not set(row["blockers"]) <= merged and not any(s.get("ticket") == row["id"] for s in st.steps.values())]
    if not waiting:
        problems.append(f"{card}: żaden ticket nie czeka na bloker")
    if st.open_intents():
        problems.append(f"{card}: zamiar bez wyniku")
    return problems


FS_FILE_BYTES = 48          # rozmiar syntetycznego pliku w drzewie `flow/<karta>/` (produkcja: pliki wejścia/wyniku kroków)


def apply_fs_shape(root, cards, shape):
    """Kształt systemu plików Tablicy z produkcji (`perf_prod_shape.fs_shape`; same liczby), dane syntetyczne.

    Dla każdej karty: w `flow/<karta>/steps/` powstaje tyle katalogów i małych plików, ile ma karta produkcji (poza
    dziennikiem), a plik `cards/<karta>.md` rośnie do produkcyjnego rozmiaru (dopisany tekst po treści karty).
    Zwraca liczby faktycznie utworzone: {"files", "dirs", "cardBytes"}. Bez `shape["fs"]` niczego nie robi."""
    fs = shape.get("fs") or {}
    flow, sizes = fs.get("flow") or {}, fs.get("cardFiles") or {}
    made = {"files": 0, "dirs": 0, "cardBytes": 0}
    for card, prod in zip(cards, (c["card"] for c in shape["cards"])):
        want = flow.get(prod) or {}
        base = os.path.join(root, "flow", card, "steps")
        dirs = [os.path.join(base, f"s{n:04d}") for n in range(int(want.get("dirs") or 0))]
        for path in dirs:
            os.makedirs(path, exist_ok=True)
        made["dirs"] += len(dirs)
        if dirs:
            for n in range(int(want.get("files") or 0)):
                with open(os.path.join(dirs[n % len(dirs)], f"f{n:05d}.txt"), "w", encoding="utf-8") as fh:
                    fh.write("x" * FS_FILE_BYTES)
                made["files"] += 1
        path = os.path.join(root, "cards", card + ".md")
        target = int(sizes.get(prod) or 0)
        if target and os.path.isfile(path):
            size = os.path.getsize(path)
            if size < target:
                filler = "\n<!-- " + "synthetic filler text for production sized card files. " * 4 + "-->\n"
                text = (filler * (((target - size) // len(filler)) + 1))[:target - size]
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(text)
            made["cardBytes"] += os.path.getsize(path)
    return made


def populate(service, base, journal_mb, seed=105, now=None, shape=None):
    """Zastępuje dzienniki kart `service.cards` dziennikami o skali produkcji; zwraca opis danych (JSON).

    Karty zakończone dostają kolumnę `done`/`archived` w pliku karty, aktywne zostają w `todo`. Dzienniki są
    zapisywane w kopii usługi i w Tablicy (ten sam tekst), sprawdzane ścisłym odczytem i commitowane w Tablicy.

    `shape` (K-125 PT-486; wynik `perf_prod_shape.load_shape`): liczba kart (`service.cards` musi mieć tyle samo), role
    (w tym liczba próśb wstrzymanych i trzymanych kroków) i rozmiar dziennika każdej karty pochodzą z produkcji
    (`shape_plan`), a `journal_mb` jest ignorowane. Dane pozostają syntetyczne — z produkcji są tylko liczby."""
    from tide_daemon.cardflow.journal import Journal
    now = now or time.time()
    cards, store, journal = list(service.cards), service.store, service.engine.journal
    held_steps, sizes = {}, None
    # Skala produkcji (od `FULL_PAUSED_CARDS` kart) ma kartę z trzymanym procesem o produkcyjnej liczbie kroków: tick
    # pyta atrapę `systemctl` o każdą jednostkę (na produkcji `trialhold.inventory` to ~17 % ticku); mała skala — jeden.
    default_held = PROD_HELD_STEPS if len(cards) >= FULL_PAUSED_CARDS else 1
    if shape is not None:
        roles, requests, held_steps, sizes = shape_plan(shape)
        if len(roles) != len(cards):
            raise ValueError(f"kształt ma {len(roles)} kart, Tablica reproduktora {len(cards)}")
    else:
        roles = assign_roles(len(cards))
        requests = paused_requests(roles)
    weights = [random.Random(f"{seed}-w-{i}").uniform(0.6, 1.4) for i in range(len(cards))]
    generated = [i for i, role in enumerate(roles) if role != "plan"]
    kept = sum(os.path.getsize(journal.mirror_path(cards[i])) * 2 for i, role in enumerate(roles) if role == "plan")
    budget = max(0.0, journal_mb * 1048576.0 - kept)
    total_weight = sum(weights[i] for i in generated)
    for index, role in enumerate(roles):
        if role in ("done", "archived"):
            store.move_card(cards[index], role)
    for index in generated:
        card, role = cards[index], roles[index]
        project = PROJECTS[index % len(PROJECTS)]
        repo = os.path.join(base, "ws", project)
        target = sizes[index] if sizes is not None else int(budget * weights[index] / total_weight / 2)  # B jednej kopii
        log, marks, extra = build_card(card, index + 1, role, project, repo, base, target, now, seed,
                                       requests=requests.get(index, 0),
                                       held_steps=held_steps.get(index, default_held))
        events = expand(log, marks, extra, now, role)
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
        _write_text(journal.mirror_path(card), text)
        _write_text(journal.path(card), text)
    fs_stats = apply_fs_shape(service.root, cards, shape) if shape is not None and shape.get("fs") else None
    fresh = Journal(service.root)
    problems, events = verify(fresh, cards)
    stats = {"roles": {role: roles.count(role) for role in sorted(set(roles))}, "events": events, "problems": problems}
    found, role_problems = check_roles(service.engine, fresh, cards, roles, requests)
    stats.update(found)
    problems.extend(role_problems)
    if problems:
        raise RuntimeError("dzienniki skali nie przechodzą kontroli: " + "; ".join(problems[:5]))
    if fs_stats:
        stats["fsShape"] = fs_stats
    subprocess.run(["git", "-C", service.root, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", service.root, "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                    "journals: production scale"], check=True, capture_output=True)
    stats["disk"] = disk_stats(service.root, os.path.dirname(journal.mirror_path(cards[0])))
    return stats


# --- polecenie evaluate -----------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate", help="ocena wyniku „po” (opcjonalnie z porównaniem do „przed”)")
    ev.add_argument("--after", required=True)
    ev.add_argument("--before")
    ev.add_argument("--out")
    args = parser.parse_args(argv)
    with open(args.after, encoding="utf-8") as fh:
        after = json.load(fh)
    before = None
    if args.before:
        with open(args.before, encoding="utf-8") as fh:
            before = json.load(fh)
    evaluation = evaluate(after, before)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(evaluation, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print(evaluation_text(evaluation))
    return 0 if evaluation["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
