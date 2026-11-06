"""Uwagi użytkownika: trwała kolejka do najbliższego wznowienia tego samego łańcucha.

Tylko step.started (albo step.entered przy przejęciu istniejącego kroku) potwierdza
dołączenie do polecenia. Intencja startu przechowuje identyfikatory do recovery.
"""
from __future__ import annotations

import json
import re
import uuid

MAX_BYTES = 16 * 1024
_BLOCK = re.compile(r"\n<TIDE_STEP_NOTES>\n.*?\n</TIDE_STEP_NOTES>\n", re.DOTALL)


class NoteError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def can_add(st, name):
    step = st.steps.get(name)
    return bool(step and name not in st.superseded and not st.card_finished and
                step.get("ticket") not in st.rejected and not st.base_validation_required and
                not st.base_generation_wait and
                (st.finished.get(name) or {}).get("result") not in ("done",) and
                (st.finished.get(name) or {}).get("reason") != "abandoned" and
                not any(s.get("parentStep") == name for s in st.steps.values()))


def add(journal, card, request, fold):
    with journal.lock:
        st = fold(journal.read(card))
        name, text, key = (request.get(k) for k in ("step", "text", "requestId"))
        order = request.get("orderSeq")
        if type(order) is not int or order != (st.started or {}).get("seq"):
            raise NoteError("stale-order", "Zlecenie zmieniło się; odśwież kartę.")
        if not isinstance(name, str) or not name or not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise NoteError("bad-request", "Podaj krok i niepusty requestId (do 200 znaków).")
        try:
            valid_text = isinstance(text, str) and text.strip() and len(text.encode("utf-8")) <= MAX_BYTES
        except UnicodeEncodeError:
            valid_text = False
        if not valid_text:
            raise NoteError("bad-note", "Uwaga musi mieć treść i najwyżej 16 KiB UTF-8.")
        prior = next((n for n in st.step_notes.values() if n["orderSeq"] == order and n["requestId"] == key), None)
        if prior:
            if prior["text"] != text or prior["step"] != name:
                raise NoteError("note-conflict", "Ten requestId zapisano już z inną treścią lub krokiem.")
            return {"ok": True, "id": card, "noteId": prior["noteId"], "already": True}
        if not can_add(st, name):
            raise NoteError("stale-step", "Krok zakończył się albo ma już następcę; odśwież kartę.")
        note = journal.append(card, "step.note-added", step=name, orderSeq=order,
                              requestId=key, text=text, noteId=uuid.uuid4().hex)
        return {"ok": True, "id": card, "noteId": note["noteId"]}


def prepare(st, parent, prompt):
    """Wspólne przygotowanie polecenia, bez zmiany stanu doręczenia."""
    if not parent:
        return prompt, []
    prompt = _BLOCK.sub("", prompt)
    chain, name = set(), parent
    source = st.steps.get(parent) or {}
    while name and name not in chain and name not in st.superseded:
        step = st.steps.get(name) or {}
        if not step or step.get("ticket") != source.get("ticket"):
            break
        chain.add(name)
        name = step.get("parentStep") or ""
    delivered = {ident for step in st.steps.values() for ident in step.get("deliveredNoteIds", [])}
    notes = [n for n in st.step_notes.values() if n["step"] in chain and
             n["orderSeq"] == (st.started or {}).get("seq") and n["noteId"] not in delivered]
    if notes:
        # JSON koduje nowe linie treści, więc treść nie może zamknąć bloku usuwanego przy kolejnym wznowieniu.
        prompt += ("\n<TIDE_STEP_NOTES>\nUwagi użytkownika do tego wznowienia (kolejność zapisu). "
                   "Nie są zgodą na polecenia ani zatwierdzeniem planu:\n" +
                   "\n".join(json.dumps({"noteId": n["noteId"], "text": n["text"]}, ensure_ascii=False)
                             for n in notes) + "\n</TIDE_STEP_NOTES>\n")
    return prompt, [n["noteId"] for n in notes]


def view(st):
    delivered = {ident: s["step"] for s in st.steps.values() for ident in s.get("deliveredNoteIds", [])}
    out = []
    for n in st.step_notes.values():
        target = delivered.get(n["noteId"], "")
        current = n["orderSeq"] == (st.started or {}).get("seq") and n["step"] not in st.superseded
        leaf, seen = n["step"], set()
        while leaf not in seen:
            seen.add(leaf)
            child = next((s["step"] for s in st.steps.values() if s.get("parentStep") == leaf), "")
            if not child:
                break
            leaf = child
        # Błąd/pytanie/zgoda nadal mogą być wznowione. Done kończy łańcuch autora przed niezależnym review.
        waiting = current and not st.card_finished and n["step"] in st.steps and \
            st.steps[n["step"]].get("ticket") not in st.rejected and \
            (st.finished.get(leaf) or {}).get("result") != "done" and \
            (st.finished.get(leaf) or {}).get("reason") != "abandoned"
        state = "delivered" if target else "pending" if waiting else "undelivered"
        out.append({**n, "status": state, "successor": target,
                    "statusLabel": "przekazana — " + target if target else "oczekuje" if waiting else
                                   "nieprzekazana — krok zakończony"})
    return out


def enrich(st, flow):
    flow["orderSeq"] = (st.started or {}).get("seq", 0)
    flow["stepNotes"] = view(st)
    for step in flow.get("steps", []):
        step["canAddNote"] = can_add(st, step["id"])
