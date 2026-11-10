"""Audyt wszystkich historii kart przed odbiorem partii 1 K-38 (PT-295, F7). Wyłącznie odczyt.

Co robi `audit()` (CLI niżej):
1. Odcisk źródeł przed odczytem: wpisy katalogu mirrora usługi i `flow/` Tablicy, katalogi kart, każdy dziennik
   (SHA-256 bajtów, rozmiar, mtime/ctime, i-węzeł, tryb). Ten sam odcisk po odczycie; każda różnica unieważnia
   przebieg (`unstable`).
2. Niezależna inwentaryzacja obu źródeł: suma rejestrów (karty aktywne, wstrzymane, zamknięte, tylko w mirrorze,
   tylko w Tablicy, katalog karty bez dziennika). Dla każdego źródła: karta, źródło, liczba i zakres seq, typy
   zdarzeń, SHA-256 pełnej treści. Błąd listowania/odczytu, ucięta linia, nieznany format, duplikat i luka seq są
   osobnymi błędami, a karta zostaje w mianowniku.
3. Porównanie Tablica ↔ mirror zdarzenie po zdarzeniu i pole po polu: brak karty, zdarzenia, pola, rozbieżna wartość.
   Mirror jest autorytatywny (z niego czyta usługa), ale rozjazd jest błędem — narzędzie niczego nie wybiera ani
   nie naprawia.
4. Odczyt kodem dokładnych SHA (`git archive` do katalogu wyjściowego, osobny proces `historyaudit_driver.py`
   ze strażnikiem zapisów): kandydat — `read_process_history` + `fold`, odniesienie — ścisły odczyt + `fold`.
   Odrzucenia są błędami. Porównanie pól stanu według `FIELD_SPEC`: każde pole eksportowanego stanu (obu wersji)
   ma kategorię i regułę; pole bez klasyfikacji jest błędem, nowe pole ma regułę oczekiwanej wartości z historii.
5. Raport kompletności: klucze zdarzeń (porównane Tablica ↔ mirror), pola stanu, klucze wpisów kroków/zamiarów.

Narzędzie nie wywołuje journal.check/append, migracji, napraw mirrora, D-Bus, Incusa ani instalacji. Pisze
wyłącznie do katalogu wyjściowego (pusty lub nowy, poza źródłami). Kod wyjścia 0 tylko przy zaliczonej bramce.
Procedura bramki partii 1: docs/box-decisions.md (PT-295).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

from . import historyaudit_driver as driver

CARD_RE = re.compile(r"^K-[1-9][0-9]{0,8}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historyaudit_driver.py")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PYTHON = "/usr/bin/python3"

# --- specyfikacja pól stanu -------------------------------------------------------------------------------------
# Kategorie z kryteriów F7. Reguły:
# - `equal` — znormalizowana wartość kandydata równa wartości odniesienia,
# - `steps-overlay` / `intents-overlay` — jak `equal`, ale kandydat dokłada do wpisu pola tożsamości z całej
#   historii (START_FIELDS z `process_identities`, PT-288) i `_identityHeld`; oczekiwanie: wpis odniesienia
#   nadpisany danymi tożsamości kandydata, `_identityHeld` == `held` tożsamości; inne różnice są niewyjaśnione,
# - `moved-to-rework` / `closed-rework` — `equal`, a różnica jest wyjaśniona tylko regułą „domknięcie po ręcznym
#   odesłaniu do poprawy” (kandydat: wynik kroku close przy moved_to=todo nie zeruje kolumny, card.closed przy
#   moved_to=todo nie domyka); sprawdzana przebiegiem samej kolumny po zdarzeniach,
# - `new-box-wait` / `new-box-env-deferred` — pole tylko w kandydacie (PT-172/PT-290); oczekiwanie: wartość
#   z ostatniego `box.wait` po ostatnim `card.started` (bez niego "" / False),
# - `new-lifted` — pole tylko w kandydacie (PT-313); oczekiwanie: ostatni ruch po `card.started` to zdjęcie z Backlogu
#   (`card.moved` backlog → todo) bez późniejszego `card.move-ignored`,
# - `new-process-identities` — pole tylko w kandydacie (PT-288); oczekiwanie: każda tożsamość `state=ok`
#   (inaczej read_process_history odrzuca historię) i nazwana krokiem obecnym w historii,
# - `new-merge-checks` — pola bramki testów po scaleniu (PT-318); oczekiwanie: wartość pusta, gdy historia nie ma
#   zdarzeń `merge.check-*`,
# - `setup-only` — nie z dziennika (`caretaker_setup` dokłada silnik); oczekiwanie: pusty słownik w obu wersjach.
PLAN, STATES, CLOSE, STEPS, RESULTS, DECISIONS, OUTSIDE, CONSOLES, BOX, PROJECT = (
    "plan/mapa ticketów", "stany", "pauza/domknięcie", "kroki/runner/tożsamość", "wyniki/werdykty",
    "decyzje/zgody", "outside", "konsole", "box.wait", "powiązania projektu")


def _spec(category, rule, *names):
    return {name: (category, rule) for name in names}


FIELD_SPEC = {
    **_spec(PLAN, "equal", "plan_checked", "imported", "imported_done", "plan_proposed", "approval_requested",
            "plan_approved", "plan_artifact_source", "plan_reviewed", "plan_reviewer", "plan_review_wait",
            "plan_revision", "plan_returns", "plan_user_seq", "plan_finished", "plan_change_seq", "plan_change_ts",
            "plan_resumes", "assignments", "reviewer_overrides", "author_overrides", "rejected"),
    **_spec(PROJECT, "equal", "started", "base", "initial_base", "base_version", "base_change",
            "base_validation_required", "base_generation_wait", "base_restart_ticket", "base_waits",
            "base_request"),
    **_spec(STATES, "equal", "store_column", "store_move_id", "last_seq", "timeline", "log", "last_step",
            "flow_errors", "flow_error_texts", "last_failed_action", "stalled_since", "last_activity_ts",
            "activity", "step_notices", "flow_notices", "tracker_retries", "waiting", "inputs", "limits",
            "wip_retries", "step_notes"),
    **_spec(STATES, "moved-to-rework", "moved_to"),
    **_spec(CLOSE, "equal", "paused", "card_finished", "main_merged", "merged", "merge_waiting",
            "abandoned_main_merges"),
    **_spec(CLOSE, "closed-rework", "closed"),
    **_spec(STEPS, "steps-overlay", "steps", "caretakers"),
    **_spec(STEPS, "intents-overlay", "intents"),
    **_spec(STEPS, "new-process-identities", "process_identities"),
    **_spec(STEPS, "equal", "superseded", "transfer_requests", "step_retries", "caretaker_chats",
            "caretaker_chat_actions", "caretaker_chat_files", "caretaker_runs", "caretaker_outcomes",
            "caretaker_escalations", "caretaker_decisions", "caretaking", "caretaker_limits", "caretaker_guards",
            "caretaker_wrote"),
    **_spec(STEPS, "setup-only", "caretaker_setup"),
    **_spec(RESULTS, "equal", "results", "finished", "verdicts", "review_questions"),
    **_spec(DECISIONS, "equal", "step_decisions", "review_decisions", "ticket_consents"),
    **_spec(OUTSIDE, "equal", "outside_started", "outside_launched"),
    **_spec(BOX, "new-box-wait", "box_wait"),
    **_spec(BOX, "new-box-env-deferred", "box_env_deferred"),
    **_spec(STATES, "new-lifted", "lifted"),
    **_spec(RESULTS, "new-merge-checks", "merge_checks", "merge_check_counts", "merge_check_merges",
            "merge_check_armed"),
}
NEW_RULES = ("new-box-wait", "new-box-env-deferred", "new-process-identities", "new-lifted", "new-merge-checks")
MERGE_CHECK_EVENTS = ("merge.check-armed", "merge.check-requested", "merge.check-finished")
DERIVED_SPEC = {"running": STEPS, "open_consoles": CONSOLES, "open_intents": STEPS, "merge_pending": RESULTS,
                "busy_reason": STATES, "flow_error": STATES, "tracker_failures": RESULTS}
# Pola wpisu kroku/zamiaru dokładane wyłącznie przez nakładkę tożsamości kandydata.
OVERLAY_MARK = "_identityHeld"


class AuditError(Exception):
    """Przebieg nie może się zacząć (zły katalog wyjściowy, SHA, repozytorium). Nie jest wynikiem bramki."""


def _err(errors, code, text, card="", source="", **extra):
    errors.append({"code": code, "card": card, "source": source, "text": text, **extra})


# --- odcisk źródeł ---------------------------------------------------------------------------------------------

def _meta(path):
    st = os.lstat(path)
    return {"mode": st.st_mode, "size": st.st_size, "mtime": st.st_mtime_ns, "ctime": st.st_ctime_ns,
            "ino": st.st_ino, "dev": st.st_dev}


def _file_sha(path):
    try:
        return hashlib.sha256(driver.read_bytes(path)).hexdigest()
    except (OSError, ValueError) as exc:
        return f"error:{type(exc).__name__}"


def fingerprint(board, mirror):
    """Metadane i treść wszystkiego, co składa się na rejestr i historie kart (bez wyjścia kroków)."""
    out = {}

    def entry(path, *, listing=False, content=False):
        try:
            item = _meta(path)
        except OSError as exc:
            out[path] = {"error": type(exc).__name__}
            return []
        if content and stat.S_ISREG(item["mode"]):
            item["sha256"] = _file_sha(path)
        names = []
        if listing and stat.S_ISDIR(item["mode"]):
            try:
                names = sorted(os.listdir(path))
            except OSError as exc:
                item["listing"] = f"error:{type(exc).__name__}"
            else:
                item["listing"] = names
        out[path] = item
        return names

    for name in entry(mirror, listing=True):
        entry(os.path.join(mirror, name), content=True)
    flow = os.path.join(board, "flow")
    for name in entry(flow, listing=True):
        card_dir = os.path.join(flow, name)
        names = entry(card_dir, listing=True)
        if "journal.jsonl" in names:
            entry(os.path.join(card_dir, "journal.jsonl"), content=True)
    return out


# --- inwentaryzacja i porównanie kopii ---------------------------------------------------------------------------

def _list_registry(path, source, errors):
    """Nazwy wpisów rejestru; brak katalogu jest pustym rejestrem z błędem, nie pominięciem."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        _err(errors, "registry-missing", f"brak katalogu rejestru {path}", source=source)
        return []
    except OSError as exc:
        _err(errors, "registry-unreadable", f"rejestr {path}: {exc}", source=source)
        return None
    if not stat.S_ISDIR(mode):
        _err(errors, "registry-not-dir", f"rejestr {path} nie jest katalogiem", source=source)
        return None
    try:
        return sorted(os.listdir(path))
    except OSError as exc:
        _err(errors, "registry-unreadable", f"rejestr {path}: {exc}", source=source)
        return None


def inventory(board, mirror, errors):
    """{karta: {"mirror": opis, "board": opis}} z sumy rejestrów; opis None = brak w źródle."""
    cards = collections.defaultdict(dict)
    listed = {}
    names = _list_registry(mirror, "mirror", errors)
    listed["mirror"] = names is not None
    for name in names or []:
        card = name[:-6] if name.endswith(".jsonl") else ""
        if not CARD_RE.fullmatch(card):
            _err(errors, "unknown-entry", f"nieznany wpis rejestru mirrora: {name}", source="mirror")
            continue
        cards[card]["mirror"] = os.path.join(mirror, name)
    flow = os.path.join(board, "flow")
    names = _list_registry(flow, "board", errors)
    listed["board"] = names is not None
    for name in names or []:
        if not CARD_RE.fullmatch(name):
            _err(errors, "unknown-entry", f"nieznany wpis rejestru Tablicy: flow/{name}", source="board")
            continue
        cards[name]["board"] = os.path.join(flow, name, "journal.jsonl")
    out = {}
    for card in sorted(cards, key=lambda c: int(c[2:])):
        out[card] = {}
        for source in ("mirror", "board"):
            path = cards[card].get(source)
            if path is None:
                out[card][source] = None
                if listed[source]:
                    _err(errors, f"card-missing-in-{source}", f"karta {card} nie istnieje w kopii {source}",
                         card, source)
                continue
            out[card][source] = read_source(card, source, path, errors)
    return out


def read_source(card, source, path, errors):
    """Opis jednego źródła: liczba i zakres seq, typy, SHA-256 treści; zdarzenia do porównania (bez zapisu)."""
    item = {"path": path, "events": 0, "seqMin": None, "seqMax": None, "types": {}, "sha256": "",
            "readable": False, "_events": {}}
    if source == "board":
        card_dir = os.path.dirname(path)
        try:
            if not stat.S_ISDIR(os.lstat(card_dir).st_mode):
                _err(errors, "card-dir-not-dir", f"flow/{card} nie jest katalogiem", card, source)
                return item
        except OSError as exc:
            _err(errors, "unreadable", f"flow/{card}: {exc}", card, source)
            return item
        if not os.path.lexists(path):
            _err(errors, "journal-missing", f"katalog karty {card} bez dziennika journal.jsonl", card, source)
            return item
    try:
        data = driver.read_bytes(path)
    except (OSError, ValueError) as exc:
        _err(errors, "unreadable", f"{path}: {exc}", card, source)
        return item
    item["readable"] = True
    item["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        _err(errors, "bad-encoding", f"{path}: {exc}", card, source)
        return item
    seqs = []
    types = collections.Counter()
    by_seq = {}
    lines = text.splitlines(keepends=True)
    for number, line in enumerate(lines, 1):
        if not line.endswith("\n"):
            _err(errors, "truncated-line", f"ucięta ostatnia linia {number} (bez końca wiersza)", card, source,
                 line=number)
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            _err(errors, "bad-json", f"linia {number}: {exc}", card, source, line=number)
            continue
        if (not isinstance(event, dict) or type(event.get("v")) is not int or event["v"] != driver.SCHEMA
                or type(event.get("seq")) is not int or event.get("card") != card
                or not isinstance(event.get("type"), str) or not event["type"]):
            _err(errors, "unknown-format", f"linia {number}: nieznany format zdarzenia (v, seq, card, type)", card,
                 source, line=number)
            continue
        seq = event["seq"]
        if seq in by_seq:
            _err(errors, "duplicate-seq", f"seq {seq} występuje ponownie w linii {number}", card, source, seq=seq)
            continue
        by_seq[seq] = event
        seqs.append(seq)
        types[event["type"]] += 1
    if seqs:
        item.update(seqMin=min(seqs), seqMax=max(seqs))
        expected = set(range(1, max(seqs) + 1))
        missing = sorted(expected - set(seqs))
        if missing:
            _err(errors, "seq-gap", f"brak seq {_ranges(missing)}", card, source, seqs=missing[:50])
        if seqs != sorted(seqs):
            _err(errors, "seq-order", "seq nie rośnie w kolejności pliku", card, source)
    elif not lines:
        _err(errors, "empty-history", f"pusty dziennik {path}", card, source)
    item["events"], item["types"], item["_events"] = len(seqs), dict(sorted(types.items())), by_seq
    return item


def _ranges(values):
    out, start, prev = [], None, None
    for v in values:
        if start is None:
            start = prev = v
        elif v == prev + 1:
            prev = v
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = v
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out[:20]) + (" …" if len(out) > 20 else "")


def compare_copies(card, sources, errors, keys):
    """Zdarzenia i pola Tablicy względem mirrora. `keys` zbiera (typ, klucz) → liczba porównań."""
    mirror, board = sources.get("mirror"), sources.get("board")
    if not (mirror and board and mirror["readable"] and board["readable"]):
        return
    if mirror["sha256"] == board["sha256"]:
        for event in mirror["_events"].values():
            for key in event:
                keys[(event["type"], key)] += 1
        return
    m, b = mirror["_events"], board["_events"]
    for seq in sorted(set(m) | set(b)):
        if seq not in b:
            _err(errors, "event-missing-in-board", f"zdarzenie seq {seq} ({m[seq]['type']}) tylko w mirrorze",
                 card, "board", seq=seq)
            continue
        if seq not in m:
            _err(errors, "event-missing-in-mirror", f"zdarzenie seq {seq} ({b[seq]['type']}) tylko w Tablicy",
                 card, "mirror", seq=seq)
            continue
        for key in sorted(set(m[seq]) | set(b[seq])):
            keys[(m[seq].get("type") or b[seq].get("type"), key)] += 1
            if key not in b[seq]:
                _err(errors, "field-missing-in-board", f"seq {seq}: brak pola {key} w Tablicy", card, "board",
                     seq=seq, key=key)
            elif key not in m[seq]:
                _err(errors, "field-missing-in-mirror", f"seq {seq}: pole {key} tylko w Tablicy", card, "mirror",
                     seq=seq, key=key)
            elif m[seq][key] != b[seq][key] or type(m[seq][key]) is not type(b[seq][key]):
                _err(errors, "value-diff", f"seq {seq}: różna wartość pola {key}", card, "board", seq=seq, key=key,
                     mirror=_short(m[seq][key]), board=_short(b[seq][key]))
    if not any(e["card"] == card and e["code"].startswith(("event-", "field-", "value-")) for e in errors):
        # Ta sama lista zdarzeń, inne bajty (np. kolejność kluczy, odstępy): także rozjazd kopii.
        _err(errors, "content-diff", "różna treść plików przy zgodnych zdarzeniach", card, "board")


def _short(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 300 else text[:300] + "…"


# --- kod dokładnych SHA ------------------------------------------------------------------------------------------

def resolve(repo, rev, run):
    done = run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    sha = (done.stdout or "").strip()
    if done.returncode != 0 or not SHA_RE.fullmatch(sha):
        raise AuditError(f"nieznany commit {rev} w {repo}")
    return sha


def extract(repo, sha, dest, run):
    """Kod `daemon/` dokładnego SHA (git archive czyta wyłącznie obiekty repozytorium)."""
    done = run(["git", "-C", repo, "archive", "--format=tar", sha, "daemon/tide_daemon"], capture_output=True,
               stdin=subprocess.DEVNULL, timeout=300)
    if done.returncode != 0:
        raise AuditError(f"git archive {sha}: {done.stderr[-500:]!r}")
    os.makedirs(dest)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def run_driver(mode, tree, board, mirror, cards, run, python=PYTHON):
    cfg = {"mode": mode, "tree": tree, "board": board, "mirror": mirror, "cards": cards}
    done = run([python, "-I", "-B", DRIVER], input=json.dumps(cfg), capture_output=True, text=True,
               timeout=1800, cwd=tree)
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return {"failed": f"kod {done.returncode}: {(done.stderr or '')[-2000:]}"}
    if done.returncode != 0:
        report["failed"] = f"kod {done.returncode}: {(done.stderr or '')[-2000:]}"
    return report


# --- porównanie stanu ----------------------------------------------------------------------------------------

def _last_card_started_index(events):
    return max((i for i, e in enumerate(events) if e.get("type") == "card.started"), default=-1)


def expected_lifted(events):
    """Reguła nowego pola `lifted` (PT-313): ostatnie zdarzenie ruchu po ostatnim `card.started` to zdjęcie z Backlogu
    (`card.moved` backlog → todo), a nie inny ruch ani `card.move-ignored`."""
    lifted = False
    for e in events[_last_card_started_index(events) + 1:]:
        if e.get("type") == "card.moved":
            lifted = e.get("from") == "backlog" and e.get("to") == "todo"
        elif e.get("type") == "card.move-ignored":
            lifted = False
    return lifted


def expected_box(events):
    """Reguła nowych pól pudełka: ostatnie box.wait po ostatnim card.started."""
    start = _last_card_started_index(events)
    last = next((e for e in reversed(events[start + 1:]) if e.get("type") == "box.wait"), None)
    if last is None:
        return "", False
    value = str(last.get("value") or "")
    return value, not value and last.get("deferred") == "environment"


def moved_to_trace(events, rework):
    """Sama kolumna `moved_to` po zdarzeniach; `rework` — reguła kandydata (wynik close przy todo nie zeruje).

    Zwraca (końcowa wartość, {seq: wartość przed zdarzeniem}). Zmiana bazy z generacją także zeruje kolumnę."""
    moved, steps, before = "", {}, {}
    for e in events:
        kind = e.get("type")
        before[e.get("seq")] = moved
        if kind == "card.started":
            moved = ""
        elif kind == "step.started" and e.get("kind") not in ("caretaker", "caretaker-chat"):
            steps[e.get("step")] = e
            moved = ""
        elif kind == "step.finished":
            closing = rework and moved == "todo" and (steps.get(e.get("step")) or {}).get("kind") == "close"
            if moved not in ("done", "archived") and not closing:
                moved = ""
        elif kind == "card.moved" and e.get("from") not in ("backlog", "todo"):
            moved = e.get("to") or ""
        elif kind == "card.base-changed" and "generation" in e:
            return None, before   # walidacja generacji poza tą regułą: różnica pozostaje niewyjaśniona
    return moved, before


def _overlay_diffs(ref, cand, identities, start_fields):
    """Różnice wpisów kroków/zamiarów po odjęciu nakładki tożsamości: (niewyjaśnione, wyjaśnione, klucze nowe)."""
    unexplained, explained, new_keys = [], [], set()
    for name in sorted(set(ref) | set(cand)):
        if name not in cand or name not in ref:
            unexplained.append(f"{name}: wpis tylko w {'odniesieniu' if name in ref else 'kandydacie'}")
            continue
        r, c = ref[name], dict(cand[name])
        step = name
        if name.startswith("json:"):
            try:
                key = json.loads(name[5:])
                step = key[1] if isinstance(key, list) and len(key) == 2 and key[0] == "step-start" else None
            except ValueError:
                step = None
        identity = identities.get(step) if step else None
        expected = dict(r)
        if identity is not None:
            data = identity.get("data") or {}
            for k in start_fields:
                if k in data:
                    if k in r and r[k] != data[k]:
                        explained.append(f"{name}.{k}: tożsamość z całej historii {_short(data[k])} "
                                         f"zamiast {_short(r[k])}")
                    elif k not in r:
                        new_keys.add(k)
                    expected[k] = data[k]
            if c.get(OVERLAY_MARK) != identity.get("held"):
                unexplained.append(f"{name}.{OVERLAY_MARK}: {c.get(OVERLAY_MARK)!r} ≠ held {identity.get('held')!r}")
            c.pop(OVERLAY_MARK, None)
            new_keys.add(OVERLAY_MARK)
        elif OVERLAY_MARK in c:
            unexplained.append(f"{name}.{OVERLAY_MARK} bez tożsamości procesu")
            c.pop(OVERLAY_MARK)
        for k in sorted(set(expected) | set(c)):
            if expected.get(k, "<brak>") != c.get(k, "<brak>"):
                unexplained.append(f"{name}.{k}: odniesienie {_short(expected.get(k, '<brak>'))}, "
                                   f"kandydat {_short(c.get(k, '<brak>'))}")
    return unexplained, explained, new_keys


def compare_state(card, events, ref, cand, start_fields, errors, coverage):
    """Pola stanu jednej karty według FIELD_SPEC; zapisuje wynik każdego pola w `coverage`."""
    ref_state, cand_state = ref["state"], cand["state"]
    identities = cand_state.get("process_identities") or {}
    for name in sorted(set(ref_state) | set(cand_state)):
        spec = FIELD_SPEC.get(name)
        item = coverage.setdefault(name, {"category": spec[0] if spec else "", "rule": spec[1] if spec else "",
                                          "cards": 0, "equal": 0, "explained": [], "unexplained": 0,
                                          "inReference": False, "inCandidate": False})
        item["inReference"] |= name in ref_state
        item["inCandidate"] |= name in cand_state
        item["cards"] += 1
        if spec is None:
            _err(errors, "field-unclassified", f"pole stanu {name} bez klasyfikacji w FIELD_SPEC", card)
            item["unexplained"] += 1
            continue
        rule = spec[1]
        r, c = ref_state.get(name, "<brak>"), cand_state.get(name, "<brak>")
        problems, explained = [], []
        if rule in NEW_RULES:
            if name in ref_state:
                problems.append("pole oznaczone jako nowe istnieje w odniesieniu — reguła nieaktualna")
            if rule == "new-box-wait" and c != expected_box(events)[0]:
                problems.append(f"box_wait {c!r}, oczekiwane {expected_box(events)[0]!r} z ostatniego box.wait")
            elif rule == "new-box-env-deferred" and c != expected_box(events)[1]:
                problems.append(f"box_env_deferred {c!r}, oczekiwane {expected_box(events)[1]!r}")
            elif rule == "new-lifted":
                if c != expected_lifted(events):
                    problems.append(f"lifted {c!r}, oczekiwane {expected_lifted(events)!r} z ruchów po card.started")
            elif rule == "new-merge-checks" and c not in (0, {}, [], {"__set__": []}) and \
                    not any(e.get("type") in MERGE_CHECK_EVENTS for e in events):
                problems.append(f"{name} {_short(c)} bez zdarzeń merge.check-* w historii")
            elif rule == "new-process-identities":
                named = {e.get("step") for e in events} | {e.get("key") for e in events}
                for ident, data in sorted((c or {}).items()):
                    if data.get("state") != "ok":
                        problems.append(f"tożsamość {ident}: state={data.get('state')}")
                    if ident not in named:
                        problems.append(f"tożsamość {ident} bez kroku w historii")
        elif name not in cand_state or name not in ref_state:
            problems.append(f"pole tylko w {'odniesieniu' if name in ref_state else 'kandydacie'} bez reguły nowego pola")
        elif rule == "setup-only":
            if r != {} or c != {}:
                problems.append("pole spoza dziennika ma wartość po samym fold")
        elif rule in ("steps-overlay", "intents-overlay"):
            unexplained, explained, new_keys = _overlay_diffs(r, c, identities, start_fields)
            problems += unexplained
            coverage.setdefault("__overlayKeys__", set()).update(new_keys)
        elif r != c:
            if rule == "moved-to-rework":
                old, _ = moved_to_trace(events, False)
                new, _ = moved_to_trace(events, True)
                if (old, new) == (r, c) and old is not None:
                    explained.append(f"moved_to {c!r} zamiast {r!r}: wynik domknięcia po odesłaniu do poprawy")
                else:
                    problems.append(f"moved_to: odniesienie {r!r}, kandydat {c!r}")
            elif rule == "closed-rework":
                seq = (r or {}).get("seq") if isinstance(r, dict) else None
                _, before = moved_to_trace(events, True)
                if seq is not None and before.get(seq) == "todo" and (c is None or (c.get("seq") or 0) < seq):
                    explained.append(f"closed: card.closed seq {seq} przy moved_to=todo nie domyka karty")
                else:
                    problems.append(f"closed: odniesienie {_short(r)}, kandydat {_short(c)}")
            else:
                problems.append(f"{name}: odniesienie {_short(r)}, kandydat {_short(c)}")
        for text in problems:
            _err(errors, "state-diff", text, card, field=name, category=spec[0])
        item["unexplained"] += len(problems)
        item["explained"] += [f"{card}: {t}" for t in explained]
        if not problems:
            item["equal"] += 1
    for name, category in DERIVED_SPEC.items():
        r, c = ref["derived"].get(name), cand["derived"].get(name)
        item = coverage.setdefault("derived:" + name, {"category": category, "rule": "equal", "cards": 0,
                                                       "equal": 0, "explained": [], "unexplained": 0,
                                                       "inReference": True, "inCandidate": True})
        item["cards"] += 1
        if r != c:
            _err(errors, "state-diff", f"{name}: odniesienie {_short(r)}, kandydat {_short(c)}", card,
                 field="derived:" + name, category=category)
            item["unexplained"] += 1
        else:
            item["equal"] += 1


def entry_keys(states):
    """Klucze wpisów kroków, opiekunów i zamiarów obu wersji (raport kompletności)."""
    keys = collections.defaultdict(lambda: {"reference": 0, "candidate": 0})
    for side, state in states:
        for field in ("steps", "caretakers", "intents"):
            for entry in (state.get(field) or {}).values():
                for key in entry:
                    keys[f"{field}.{key}"][side] += 1
    return keys


# --- przebieg ----------------------------------------------------------------------------------------------------

def _check_out(out, board, mirror, repo):
    real = os.path.realpath(out)
    for root in (board, mirror):
        root = os.path.realpath(root)
        if real == root or real.startswith(root + os.sep) or root.startswith(real + os.sep):
            raise AuditError(f"katalog wyjściowy {out} nachodzi na źródło {root}")
    if os.path.lexists(out):
        if not os.path.isdir(out) or os.path.islink(out) or os.listdir(out):
            raise AuditError(f"katalog wyjściowy {out} musi być nowy albo pusty")
    else:
        os.makedirs(out)
    return real


def audit(board, out, *, mirror=None, repo=REPO_ROOT, candidate, reference, run=subprocess.run, python=PYTHON,
          between=None, now=None):
    """Pełny przebieg bramki; zwraca raport (zapisany też w `out/report.json` i `out/report.md`)."""
    board = os.path.abspath(board)
    mirror = os.path.abspath(mirror or default_mirror(board))
    out = _check_out(out, board, mirror, repo)
    candidate_sha, reference_sha = resolve(repo, candidate, run), resolve(repo, reference, run)
    started = (now or _now)()
    before = fingerprint(board, mirror)
    errors = []
    sources = inventory(board, mirror, errors)
    cards = list(sources)
    keys = collections.Counter()
    for card in cards:
        compare_copies(card, sources[card], errors, keys)
    trees = {"candidate": os.path.join(out, "trees", "candidate-" + candidate_sha),
             "reference": os.path.join(out, "trees", "reference-" + reference_sha)}
    extract(repo, candidate_sha, trees["candidate"], run)
    extract(repo, reference_sha, trees["reference"], run)
    if between is not None:
        between()   # tylko testy: zmiana źródła w trakcie odczytu
    reports = {mode: run_driver(mode, trees[mode], board, mirror, cards, run, python) for mode in trees}
    after = fingerprint(board, mirror)
    finished = (now or _now)()
    for mode, report in reports.items():
        if report.get("failed"):
            _err(errors, "driver-failed", f"sterownik {mode}: {report['failed']}", source=mode)
        for reason in report.get("forbidden") or []:
            _err(errors, "forbidden-operation", f"sterownik {mode}: {reason}", source=mode)
        module = report.get("module") or ""
        if not module.startswith(os.path.realpath(trees[mode]) + os.sep):
            _err(errors, "wrong-code", f"sterownik {mode} czytał kod spoza drzewa SHA: {module}", source=mode)
    if before != after:
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        _err(errors, "unstable", f"źródła zmieniły się podczas odczytu: {', '.join(changed[:20])}",
             paths=changed[:200])
    cand, ref = reports["candidate"], reports["reference"]
    registry = cand.get("registry")
    if isinstance(registry, list) and registry != cards:
        _err(errors, "registry-mismatch", f"Journal.cards_complete {registry} ≠ inwentaryzacja {cards}",
             source="candidate")
    elif not isinstance(registry, list):
        _err(errors, "registry-rejected", f"Journal.cards_complete odmówił: {registry}", source="candidate")
    coverage, read_ok, rows = {}, 0, []
    states = []
    for card in cards:
        c, r = (cand.get("cards") or {}).get(card) or {}, (ref.get("cards") or {}).get(card) or {}
        auth = sources[card]["mirror"] if sources[card]["mirror"] is not None else sources[card]["board"]
        events = [auth["_events"][s] for s in sorted(auth["_events"])] if auth and auth["readable"] else []
        row = {"card": card, "class": card_class(sources[card], c), "sources": {
            s: _public(v) for s, v in sources[card].items()}, "candidate": _read_result(c),
            "reference": _read_result(r)}
        rows.append(row)
        if not c.get("ok"):
            _err(errors, "read-rejected", f"kandydat (read_process_history/fold) odrzucił historię: "
                                          f"{c.get('read') or c.get('fold') or c.get('forbidden') or 'brak wyniku'}",
                 card, "candidate")
        if not r.get("ok"):
            _err(errors, "reference-rejected", f"odniesienie odrzuciło historię: "
                                               f"{r.get('read') or r.get('fold') or 'brak wyniku'}", card, "reference")
        if c.get("ok") and r.get("ok"):
            read_ok += 1
            want = driver.digest(events)
            for label, item in (("candidate", c), ("reference", r)):
                if item.get("digest") != want:
                    _err(errors, "digest-mismatch", f"{label} przeczytał inne zdarzenia niż inwentaryzacja",
                         card, label)
            compare_state(card, events, r, c, cand.get("startFields") or [], errors, coverage)
            states += [("reference", r["state"]), ("candidate", c["state"])]
    for name in sorted(set(cand.get("fields") or []) | set(ref.get("fields") or [])):
        if name not in FIELD_SPEC:
            if not any(e["code"] == "field-unclassified" and f" {name} " in e["text"] for e in errors):
                _err(errors, "field-unclassified", f"pole stanu {name} bez klasyfikacji w FIELD_SPEC")
            coverage.setdefault(name, {"category": "", "rule": "", "cards": 0, "equal": 0, "explained": [],
                                       "unexplained": 1, "inReference": name in (ref.get("fields") or []),
                                       "inCandidate": name in (cand.get("fields") or [])})
    overlay = sorted(coverage.pop("__overlayKeys__", set()))
    report = {
        "version": 1, "startedAt": started, "finishedAt": finished,
        "board": board, "mirror": mirror, "out": out, "repo": os.path.realpath(repo),
        "candidate": {"sha": candidate_sha, "module": cand.get("module"), "python": cand.get("python")},
        "reference": {"sha": reference_sha, "module": ref.get("module"), "python": ref.get("python")},
        "tool": tool_version(repo, run),
        "counts": {"cards": len(cards), "read": read_ok, "label": f"{read_ok}/{len(cards)}",
                   "rejected": sum(1 for e in errors if e["code"] in ("read-rejected", "reference-rejected")),
                   "errors": len(errors),
                   "unexplained": sum(1 for e in errors if e["code"] == "state-diff"),
                   "explained": sum(len(v["explained"]) for v in coverage.values())},
        "stable": before == after,
        "cards": rows,
        "errors": errors,
        "completeness": {
            "eventKeys": [{"type": t, "key": k, "compared": n, "status": "porównane Tablica ↔ mirror"}
                          for (t, k), n in sorted(keys.items())],
            "stateFields": {k: {**v, "status": _field_status(v)} for k, v in sorted(coverage.items())},
            "entryKeys": {k: {**v, "status": ("porównane" if v["reference"] and v["candidate"] else
                                              "nakładka tożsamości kandydata (reguła steps/intents-overlay)"
                                              if k.split(".", 1)[1] in overlay else "niesklasyfikowane")}
                          for k, v in sorted(entry_keys(states).items())},
        },
    }
    for key, item in report["completeness"]["entryKeys"].items():
        if item["status"] == "niesklasyfikowane":
            _err(errors, "entry-key-unclassified", f"klucz wpisu {key} tylko w jednej wersji bez reguły")
    report["counts"]["errors"] = len(errors)
    report["ok"] = not errors and bool(cards) and read_ok == len(cards)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True, default=sorted)
        fh.write("\n")
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(summary(report))
    return report


def _field_status(item):
    if not item["rule"]:
        return "niesklasyfikowane"
    if item["unexplained"]:
        return "różnice niewyjaśnione"
    if item["rule"] in NEW_RULES:
        return "nowe pole: reguła oczekiwania spełniona"
    return "porównane" + (" (różnice wyjaśnione regułą)" if item["explained"] else "")


def card_class(sources, candidate):
    """Klasa karty do raportu: źródła i stan (aktywna, wstrzymana, zamknięta)."""
    mirror, board = sources.get("mirror"), sources.get("board")
    if mirror is None:
        where = "tylko Tablica"
    elif board is None:
        where = "tylko mirror"
    elif not board.get("readable") and board.get("events") == 0 and not os.path.lexists(board["path"]):
        where = "katalog karty bez dziennika"
    else:
        where = "obie kopie"
    state = (candidate or {}).get("state")
    if not state:
        return f"{where}, odczyt odrzucony"
    if state.get("paused"):
        status = "wstrzymana"
    elif state.get("card_finished") or state.get("closed") or state.get("merged"):
        status = "zamknięta"
    elif state.get("started"):
        status = "aktywna"
    else:
        status = "bez zlecenia"
    return f"{where}, {status}"


def _public(item):
    return None if item is None else {k: v for k, v in item.items() if not k.startswith("_")}


def _read_result(item):
    return {"ok": bool(item.get("ok")), "events": item.get("events"), "digest": item.get("digest"),
            "error": item.get("read") or item.get("fold") or ""}


def tool_version(repo, run):
    head = run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
               stdin=subprocess.DEVNULL, timeout=60)
    status = run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                 text=True, stdin=subprocess.DEVNULL, timeout=60)
    return {"head": (head.stdout or "").strip(), "clean": status.returncode == 0 and not (status.stdout or "").strip()}


def summary(report):
    c = report["counts"]
    lines = [f"# Audyt historii kart (PT-295, F7)", "",
             f"- Wynik: **{'ZALICZONY' if report['ok'] else 'NIEZALICZONY'}**",
             f"- Kandydat: `{report['candidate']['sha']}`, odniesienie: `{report['reference']['sha']}`",
             f"- Narzędzie: `{report['tool']['head']}` (czyste drzewo: {report['tool']['clean']})",
             f"- Odczyt: {c['label']}, odrzucenia: {c['rejected']}, błędy: {c['errors']}, "
             f"różnice niewyjaśnione: {c['unexplained']}, wyjaśnione: {c['explained']}",
             f"- Stabilne okno odczytu: {report['stable']} ({report['startedAt']} → {report['finishedAt']})",
             f"- Tablica: `{report['board']}`, mirror: `{report['mirror']}`", "",
             "## Karty", "", "| Karta | Klasa | mirror: zdarzenia, seq, SHA-256 | Tablica: zdarzenia, seq, SHA-256 |"
             " Kandydat | Odniesienie |", "| --- | --- | --- | --- | --- | --- |"]
    for row in report["cards"]:
        def src(item):
            if item is None:
                return "brak"
            return f"{item['events']}, {item['seqMin']}–{item['seqMax']}, `{item['sha256'][:12]}`"
        lines.append(f"| {row['card']} | {row['class']} | {src(row['sources']['mirror'])} | "
                     f"{src(row['sources']['board'])} | {'ok' if row['candidate']['ok'] else 'odrzucona'} | "
                     f"{'ok' if row['reference']['ok'] else 'odrzucona'} |")
    lines += ["", "## Błędy", ""]
    lines += [f"- `{e['code']}` {e['card']} {e['source']}: {e['text']}" for e in report["errors"][:500]] or ["- brak"]
    lines += ["", "## Pola stanu", "", "| Pole | Kategoria | Reguła | Karty | Zgodne | Status |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, item in report["completeness"]["stateFields"].items():
        lines.append(f"| {name} | {item['category']} | {item['rule']} | {item['cards']} | {item['equal']} | "
                     f"{item['status']} |")
    explained = [t for item in report["completeness"]["stateFields"].values() for t in item["explained"]]
    lines += ["", "## Różnice wyjaśnione regułą", ""] + ([f"- {t}" for t in explained[:200]] or ["- brak"])
    lines += ["", "## Klucze zdarzeń", "", f"{len(report['completeness']['eventKeys'])} par (typ, klucz), "
              "wszystkie porównane Tablica ↔ mirror; lista w report.json.", ""]
    return "\n".join(lines) + "\n"


def default_mirror(board, env=None):
    """Ta sama ścieżka co journal.mirror_root (bez importu dziennika)."""
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    key = hashlib.sha1(os.path.realpath(board).encode("utf-8")).hexdigest()[:12]
    return os.path.join(state, "tide", "cardflow", key)


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="tide_daemon.cardflow.historyaudit",
                                     description="Audyt wszystkich historii kart (tylko odczyt, PT-295).")
    parser.add_argument("--board", required=True, help="katalog Tablicy (repozytorium tablicy)")
    parser.add_argument("--mirror", default="", help="katalog kopii usługi; domyślnie jak journal.mirror_root")
    parser.add_argument("--out", required=True, help="nowy albo pusty katalog raportu, poza źródłami")
    parser.add_argument("--candidate", required=True, help="dokładny SHA kandydata")
    parser.add_argument("--reference", required=True, help="przypięty SHA odniesienia")
    parser.add_argument("--repo", default=REPO_ROOT, help="repozytorium z obiektami obu SHA")
    args = parser.parse_args(argv)
    try:
        report = audit(args.board, args.out, mirror=args.mirror or None, repo=args.repo,
                       candidate=args.candidate, reference=args.reference)
    except AuditError as exc:
        out.write(f"audyt nie wystartował: {exc}\n")
        return 2
    c = report["counts"]
    out.write(f"{'ZALICZONY' if report['ok'] else 'NIEZALICZONY'}: {c['label']} kart, odrzucenia {c['rejected']}, "
              f"błędy {c['errors']}, niewyjaśnione {c['unexplained']}; raport {os.path.join(args.out, 'report.md')}\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
