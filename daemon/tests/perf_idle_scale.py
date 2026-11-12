#!/usr/bin/python3
"""Skala produkcji dzienników dla reproduktora CPU postoju (K-105, PT-410): dane, wybór wątku przepływu, ocena.

Moduł obsługuje tryb `--scale prod` skryptu `perf_idle_cpu.py` (PT-346); sam niczego nie uruchamia poza małym
poleceniem `evaluate`. Wyłącznie dane syntetyczne w katalogu tymczasowym reproduktora.

- `populate(service, ...)` — na Tablicy złożonej przez `perf_board_refresh.Service` (PT-342) zastępuje dzienniki kart
  dziennikami o skali produkcji: większość kart zakończona (`done`/`archived`), kilka aktywnych bez kroku w toku
  (czekająca decyzja „uruchom poza piaskownicą”, pytanie kroku, plan do zatwierdzenia). Dzienniki powstają
  równoważnym generatorem (nie przez `Journal.append`, które czyta cały plik przy każdym zdarzeniu), a każdy
  jest sprawdzany ścisłym odczytem (`Journal.read_complete`) i kontrolą tożsamości procesów usługi — inaczej usługa
  wstrzymałaby odzyskiwanie i pomiar byłby zaniżony. Rozmiar jest łączny dla kopii usługi i pliku w Tablicy.
- `select_flow_thread` — deterministyczny wybór wątku workera przepływu z mapy `threads` okna CPU.
- `evaluate` — ocena „przed”/„po”: progi dotyczą wątku workera przepływu i udziału `engine.tick`, nie CPU całego
  procesu. To świadome odstępstwo od progu produkcji (CPU całego `tide-daemon` ≤ 30 % rdzenia): reproduktor
  nie ma m.in. `coordination_monitor._scan`, `describe.facts_hash`, `boardplans` i `scanner` (~15 % próbek produkcji).
  Wynik jest wskaźnikiem; rozstrzyga pomiar produkcji przy domknięciu karty.

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
FLOW_CPU_LIMIT = 30.0                  # % jednego rdzenia, wątek workera przepływu („po”)
TICK_SHARE_LIMIT = 0.25                # udział `engine.tick` w próbkach okna próbnika („po”)
SNAPSHOT_CALLS = 60
SNAPSHOT_INTERVAL_S = 0.5
PROJECTS = ("plasma-tide", "alpha", "beta")
OUTSIDE_COMMANDS = ("make test-changed BASE=0123456789abcdef0123456789abcdef01234567",
                    "/usr/bin/python3 tools/test_runner.py --modules daemon/tests/test_cardflow.py",
                    "bash scripts/run-integration.sh ~/dane")
MIN_CARDS = 4


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


def evaluate(after, before=None):
    """Ocena wyniku „po” (progi) z opcjonalnym porównaniem z „przed”.

    Progi: CPU wątku workera przepływu ≤ 30 % jednego rdzenia i udział `engine.tick` ≤ 25 % próbek. CPU całego
    procesu i `Snapshot` p95 są tylko raportowane. Wzrost `VmRSS` (koniec okna) podajemy w MB i %, bez progu (próg
    ocenia #5). Werdykt: `invalid` (wysokie loadavg), `fail` (próg niespełniony albo brak pomiaru), `pass`."""
    flow, tick = _flow(after), after.get("tick") or {}
    flow_cpu, share = flow.get("cpuPercent"), tick.get("share")
    thresholds = {
        "flowThreadCpuPercent": {"value": flow_cpu, "limit": FLOW_CPU_LIMIT, "source": flow.get("source"),
                                 "thread": flow.get("name"), "ok": flow_cpu is not None and flow_cpu <= FLOW_CPU_LIMIT},
        "engineTickShare": {"value": share, "limit": TICK_SHARE_LIMIT,
                            "ok": share is not None and share <= TICK_SHARE_LIMIT},
    }
    ok_run, reasons = validity(after)
    if before is not None:
        ok_before, reasons_before = validity(before)
        ok_run = ok_run and ok_before
        reasons += [f"przed: {reason}" for reason in reasons_before]
    failed = [name for name, item in thresholds.items() if not item["ok"]]
    verdict = "invalid" if not ok_run else ("fail" if failed else "pass")
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
        "verdict": verdict, "valid": ok_run, "invalidReasons": reasons, "failedThresholds": failed,
        "thresholds": thresholds, "comparison": comparison, "vmRssGrowth": growth,
        "reported": {"processCpuPercent": (after.get("cpuWindow") or {}).get("cpuPercent"),
                     "snapshotP95Ms": (after.get("snapshot") or {}).get("p95Ms"),
                     "vmHwmMb": memory.get("vmHwmMb")},
        "note": ("Wskaźnik reproduktora: progi dotyczą wątku workera przepływu i udziału engine.tick, nie CPU całego "
                 "procesu; rozstrzyga pomiar produkcji przy domknięciu karty."),
    }


def evaluation_text(evaluation):
    t = evaluation["thresholds"]
    flow, tick = t["flowThreadCpuPercent"], t["engineTickShare"]
    lines = [f"werdykt: {evaluation['verdict']}"]
    if evaluation["invalidReasons"]:
        lines.append("nieważny: " + "; ".join(evaluation["invalidReasons"]))
    lines.append(f"CPU wątku przepływu: {flow['value']} % (próg ≤ {flow['limit']} %; wątek {flow['thread']}, "
                 f"źródło {flow['source']}) — {'ok' if flow['ok'] else 'NIE'}")
    lines.append(f"udział engine.tick: {tick['value']} (próg ≤ {tick['limit']}) — {'ok' if tick['ok'] else 'NIE'}")
    reported = evaluation["reported"]
    lines.append(f"bez progu: CPU procesu {reported['processCpuPercent']} %, Snapshot p95 {reported['snapshotP95Ms']} ms, "
                 f"VmHWM {reported['vmHwmMb']} MB")
    growth = evaluation["vmRssGrowth"]
    if growth:
        lines.append(f"VmRSS: {growth['beforeMb']} → {growth['afterMb']} MB ({growth['growthMb']:+} MB, "
                     f"{growth['growthPercent']:+} %), bez progu (ocenia #5)")
    comparison = evaluation["comparison"]
    if comparison:
        for name, item in comparison.items():
            lines.append(f"{name}: przed {item['before']} → po {item['after']} (zmiana {item['delta']})")
    lines.append(evaluation["note"])
    return "\n".join(lines)


# --- dzienniki o skali produkcji --------------------------------------------------------------------

def assign_roles(count):
    """Role kart po indeksie: `outside`, `question`, `plan` (aktywne, bez kroku w toku), `done`, `archived`."""
    if count < MIN_CARDS:
        raise ValueError(f"tryb skali wymaga co najmniej {MIN_CARDS} kart")
    active = max(3, round(count * 0.2))
    outside = min(3, max(1, active // 3))
    question = min(3, max(1, active // 3))
    plan = active - outside - question
    kinds = ["outside"] * outside + ["question"] * question + ["plan"] * plan
    spots = {int(i * count / active): kind for i, kind in enumerate(kinds)}
    roles, finished = [], 0
    for index in range(count):
        if index in spots:
            roles.append(spots[index])
        else:
            finished += 1
            roles.append("archived" if finished % 6 == 0 else "done")
    return roles


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


def _ticket_flow(log, card, project, repo, base, ticket, rng, marks, outside_done, last, note_id):
    """Ticket: krok autora, (outside), review (czasem z poprawką), scalenie. `last` — stan końcowy ticketu."""
    step = f"{card.lower()}-{ticket.lower()}-ticket-1"
    ident = _identity(card, project, base, step, "ticket", ticket)
    if note_id:
        log.add("step.note-added", noteId=note_id, orderSeq=len(log.events), requestId=f"req-{note_id}", step=step,
                text=_text(rng, 8))
    _start(log, ident, step, project, repo, notes=[note_id] if note_id else [])
    marks.append((step, ticket, len(log.events)))
    if last == "outside":
        _finish(log, step, ticket, result="denied", outside=True, canAllow=True, command=OUTSIDE_COMMANDS[
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


def build_card(card, number, role, project, repo, base, target_bytes, now, seed):
    """Zdarzenia jednej karty (lista słowników z `seq`, `ts`, `card`, `type`) i liczba zdarzeń wypełniających."""
    rng = random.Random(f"{seed}-{number}")
    log = _Log(card, rng)
    tickets = [f"PT-{number * 10 + i}" for i in range(1, 4)]
    log.add("card.started", project=project, repo=repo, entry="plan", executor="Sonnet", program="claude",
            provider="anthropic", model="sonnet", check="make test", column="todo", prefix="PT", mode="full",
            baseRef="refs/heads/master", baseBranch="master", baseCommit="0123abc")
    plan = f"{card.lower()}-planning-1"
    pid = _identity(card, project, base, plan, "planning", "")
    _start(log, pid, plan, project, repo)
    _finish(log, plan, "", notes=_text(rng, 10))
    rows = [{"id": t, "workId": t, "title": f"Ticket {t}", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
             "class": "standard", "note": ""} for t in tickets]
    log.add("plan.proposed", title=f"Plan {card}", prefix="PT", rows=rows, planner="Opus", box=None)
    log.add("plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
    log.add("plan.approved", rows=rows, map={}, planFile=f"docs/agents/plans/{card.lower()}.md")
    for ticket in tickets:
        log.add("ticket.assigned", ticket=ticket, executor="Sonnet", reviewer="Opus", program="claude",
                provider="anthropic", model="sonnet")
    marks = []
    outside_at = rng.randrange(len(tickets))
    pending = {"outside": "outside", "question": "question"}.get(role, "")
    open_index = 2 if pending else -1
    for index, ticket in enumerate(tickets):
        if index > open_index >= 0:
            break
        last = pending if index == open_index else ""
        _ticket_flow(log, card, project, repo, base, ticket, rng, marks, outside_done=(index == outside_at and not last),
                     last=last, note_id=f"n{number}" if index == 0 and not pending else "")
    if not pending:
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
    log.stamp(now - 120.0 if role in ("outside", "question") else now - 86400.0 * 3)
    return log.events


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


def populate(service, base, journal_mb, seed=105, now=None):
    """Zastępuje dzienniki kart `service.cards` dziennikami o skali produkcji; zwraca opis danych (JSON).

    Karty zakończone dostają kolumnę `done`/`archived` w pliku karty, aktywne zostają w `todo`. Dzienniki są
    zapisywane w kopii usługi i w Tablicy (ten sam tekst), sprawdzane ścisłym odczytem i commitowane w Tablicy."""
    from tide_daemon.cardflow import decisions, state
    from tide_daemon.cardflow.journal import Journal
    now = now or time.time()
    cards, store, journal = list(service.cards), service.store, service.engine.journal
    roles = assign_roles(len(cards))
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
        target = int(budget * weights[index] / total_weight / 2)         # bajty jednej kopii
        log, marks, extra = build_card(card, index + 1, role, project, repo, base, target, now, seed)
        events = expand(log, marks, extra, now, role)
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
        _write_text(journal.mirror_path(card), text)
        _write_text(journal.path(card), text)
    fresh = Journal(service.root)
    problems, events = verify(fresh, cards)
    stats = {"roles": {role: roles.count(role) for role in sorted(set(roles))}, "events": events, "problems": problems}
    outside = question = 0
    for index in generated:
        card, role = cards[index], roles[index]
        st = state.fold(fresh.read_complete(card))
        if st.running():
            problems.append(f"{card}: krok w toku")
        kinds = [d.get("kind") for d in decisions.build_decisions(st)]
        if role == "outside":
            outside += "permission" in kinds
        elif role == "question":
            question += "question" in kinds
        elif not st.card_finished:
            problems.append(f"{card}: karta zakończona bez card.finished")
    stats["outsideDecisions"], stats["questionDecisions"] = outside, question
    if outside != roles.count("outside") or question != roles.count("question"):
        problems.append(f"czekające decyzje: outside {outside}/{roles.count('outside')}, "
                        f"pytania {question}/{roles.count('question')}")
    if problems:
        raise RuntimeError("dzienniki skali nie przechodzą kontroli: " + "; ".join(problems[:5]))
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
    return {"pass": 0, "fail": 1, "invalid": 2}[evaluation["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
