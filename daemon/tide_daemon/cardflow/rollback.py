"""Kontrola przed wycofaniem K-38 do przypiętego mastera (PT-293, K-38 F5). Tylko odczyt.

Próba downgrade'u (daemon/tests/test_cardflow_rollback.py) pokazała, że stary silnik czyta historię K-38 foldem
bez wyjątku, a mimo to: sprawdza jednostkę kroku z pudełka na hoście (`systemctl --user is-active <j>.scope`),
uznaje żywy krok w pudełku za utracony i uruchamia opiekuna na hoście, ignoruje `box.wait` (environment, rebuild),
a odpowiedź na pytanie kroku z pudełka wznawia na hoście sesję z domu pudełka. Poprawny fold nie jest więc dowodem
bezpiecznego wznowienia. Stary silnik nic nie robi z kartą wstrzymaną bez czynnych procesów — na tym opiera się
procedura z docs/box-decisions.md (PT-293): pauza → dowód końca procesów i zwolnienia zasobów → ta kontrola →
zatrzymanie usługi → ponowna kontrola → odwrócenie integracji i instalacja (osobna zgoda właściciela) → stary
silnik → restart starego silnika → porównanie odcisku historii.

`check(engine)` zwraca raport: `ok` (żadnej przeszkody), `stops` (przeszkody: brak pauzy, czynny lub nieznany
proces, otwarta konsola, outside, rozpoczęta akcja, zajęte zasoby pudełka, nieczytelna historia), klasy kart
i odcisk historii. Klasy kart (co wolno po wycofaniu):
- `safe` — bez znaczeń pudełka (partia 1: `box.enabled: false`, bez kroków w pudełku); odroczona zgoda środowiska
  przy wyłączonym pudełku nic nie blokuje także w K-38,
- `hold-paused` — oczekujące/historyczne znaczenia pudełka bez kroku w pudełku (`box.wait` environment/start/rebuild,
  zgoda środowiska zapisana w historii): na starym kodzie karta zostaje wstrzymana do powrotu K-38,
- `decision` — krok w pudełku w historii (wznowienie, poprawka lub review poszłyby na hosta, także sesja z domu
  pudełka) albo krok objęty blokadą prób PT-261, której stary kod nie zna: nie wolno jej wznawiać na starym kodzie
  bez decyzji właściciela.
Kontrola nie migruje dziennika, niczego nie zapisuje i nie zatrzymuje procesów.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

SAFE, HOLD, DECISION = "safe", "hold-paused", "decision"
RESUMABLE = ("question", "denied", "stopped", "error")
BOX_RUNNER = "box"


def _stop(stops: list, code: str, text: str, card: str = "", step: str = "") -> None:
    stops.append({"code": code, "card": card, "step": step, "text": text})


def digest(events: list[dict]) -> str:
    """Odcisk zdarzeń w kolejności dziennika (pełne pola, kanoniczny JSON)."""
    h = hashlib.sha256()
    for e in events:
        h.update(json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def classify(events: list[dict], st) -> dict:
    """Klasa karty i znaczenia K-38, których przypięty master nie zna (lista zdań z kodem)."""
    meanings = []
    box_steps = [s for s in st.steps.values() if s.get("runner") == BOX_RUNNER]
    children = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
    for step in box_steps:
        name = step.get("step") or ""
        done = st.finished.get(name) or {}
        if done.get("result") in RESUMABLE and name not in children:
            meanings.append({"code": "box-resume-pending", "step": name,
                             "text": f"krok {name} z pudełka czeka na decyzję ({done.get('result')}); stary silnik "
                                     "wznowiłby go na hoście, także z sesją z domu pudełka"})
        else:
            meanings.append({"code": "box-step", "step": name,
                             "text": f"krok {name} biegł w pudełku; kolejne kroki karty stary silnik "
                                     "uruchomiłby na hoście"})
    from ..trialpolicy import blocked_event
    for name, step in [*st.steps.items(), *st.caretakers.items()]:
        if step.get("_identityHeld") is True or blocked_event(step):
            meanings.append({"code": "trial-hold", "step": name,
                             "text": f"krok {name} objęty blokadą prób PT-261 lub chronioną tożsamością procesu; "
                                     "przypięty master tej blokady nie zna"})
    if any(e.get("type") == "box.rebuild-requested" for e in events):
        meanings.append({"code": "box-rebuild-requested", "step": "",
                         "text": "zlecona przebudowa pudełka po decyzji o domenie: stary silnik jej nie wykona"})
    if st.box_wait:
        meanings.append({"code": f"box-wait-{st.box_wait}", "step": "",
                         "text": f"box.wait={st.box_wait}: stary silnik nie zna oczekiwania i uruchomiłby krok na hoście"})
    waits = [e for e in events if e.get("type") == "box.wait"]
    if any(e.get("value") == "environment" for e in waits) and not st.box_wait:
        meanings.append({"code": "environment-approved", "step": "",
                         "text": "zgoda środowiska zapisana w historii: stary silnik uruchomiłby dalsze kroki na hoście, "
                                 "nie w zatwierdzonym pudełku"})
    if st.box_env_deferred:
        meanings.append({"code": "environment-deferred", "step": "",
                         "text": "zgoda środowiska odroczona przy box.enabled=false: bez skutku, kroki i tak na hoście"})
    codes = {m["code"] for m in meanings}
    if codes & {"box-step", "box-resume-pending", "trial-hold"}:
        cls = DECISION
    elif codes - {"environment-deferred"}:
        cls = HOLD
    else:
        cls = SAFE
    return {"class": cls, "meanings": meanings}


def _unit_state(engine, step: dict) -> str:
    """Stan jednostki według wpisu kroku. Krok z pudełka: zatrzymana lub usunięta instancja nie ma procesów,
    więc to dowód końca także bez `incus exec`; działająca — `systemctl --user is-active` w pudełku."""
    try:
        if step.get("runner") == BOX_RUNNER:
            runner = engine._runner_for(step)
            inst = runner.incus.instance(step.get("box") or runner.box)
            if inst is None or str(inst.get("status", "")).lower() == "stopped":
                return "inactive"
        return engine._unit_state(step)
    except Exception:
        return "unknown"


def check(engine, *, unit_state=None, outside_state=None, console_state=None, process_alive=None,
          box_running=None, admission_load=None) -> dict:
    """Przeszkody wycofania i klasy kart na bieżącej historii. Argumenty — sondy (atrapy w testach).

    `unit_state(step)` — stan jednostki kroku według jego wpisu (krok z pudełka: `systemctl --user` w pudełku), tylko
    "inactive" jest dowodem końca. Systemd obejmuje cgroup jednostki, więc koniec jednostki obejmuje potomków."""
    from ..box import admission
    from ..box import state as bs
    from ..tasks import launch, stepstop
    from .state import fold
    unit_state = unit_state or (lambda step: _unit_state(engine, step))
    outside_state = outside_state or stepstop.unit_state
    console_state = console_state or stepstop.unit_state
    process_alive = process_alive or launch.process_alive
    box_running = box_running or bs.running_steps
    admission_load = admission_load or admission.load
    journal = engine.journal
    stops, cards, fingerprint = [], {}, {}
    try:
        names = journal.cards_complete()
    except (OSError, ValueError) as exc:
        _stop(stops, "unknown-registry", f"rejestr kart nieczytelny: {exc}")
        names = []
    boxes = set()
    for card in names:
        try:
            events = journal.read_complete(card)
        except (OSError, ValueError) as exc:
            _stop(stops, "unknown-history", f"historia nieczytelna lub niepełna: {exc}", card)
            continue
        st = fold(events)
        fingerprint[card] = {"events": len(events), "sha256": digest(events)}
        cards[card] = {**classify(events, st), "paused": bool(st.paused),
                       "active": st.started is not None and not st.card_finished}
        if cards[card]["active"] and not st.paused:
            _stop(stops, "not-paused", "karta ma trwający przepływ i nie jest wstrzymana", card)
        for step in st.running():
            _stop(stops, "step-running", "krok bez zapisanego końca", card, step.get("step") or "")
        for intent in st.open_intents(include_background=True):
            _stop(stops, "open-intent", f"rozpoczęta akcja {intent.get('action')} bez wyniku", card,
                  str(intent.get("key") or ""))
        for step in st.open_consoles():
            _stop(stops, "console-open", "otwarta karta Konsoli kroku", card, step.get("step") or "")
        for name in st.outside_started:
            _stop(stops, "outside-active", "polecenie poza piaskownicą bez zapisanego końca", card, name)
        for name, step in [*st.steps.items(), *st.caretakers.items()]:
            if step.get("unit"):
                state = unit_state(step)
                if state == "active":
                    _stop(stops, "process-alive", f"jednostka {step['unit']} działa", card, name)
                elif state != "inactive":
                    _stop(stops, "process-unknown", f"stan jednostki {step['unit']} nieznany", card, name)
            if name in st.outside_started or (st.finished.get(name) or {}).get("result") == "denied":
                state = outside_state(f"tide-outside-{name}")
                if state != "inactive":
                    _stop(stops, "outside-" + ("alive" if state == "active" else "unknown"),
                          f"jednostka tide-outside-{name}: {state}", card, name)
            if step.get("consoleUnit"):
                state = console_state(step["consoleUnit"])
                if state != "inactive":
                    _stop(stops, "console-" + ("alive" if state == "active" else "unknown"),
                          f"jednostka konsoli {step['consoleUnit']}: {state}", card, name)
            if step.get("processFile"):
                alive = process_alive(step["processFile"])
                if alive is not False:
                    _stop(stops, "process-alive" if alive else "process-unknown",
                          "proces kroku żyje" if alive else "brak wiarygodnego zapisu procesu kroku", card, name)
            if step.get("runner") == BOX_RUNNER:
                boxes.add((step.get("boxSlug") or "", step.get("box") or ""))
    for slug, box in sorted(boxes):
        try:
            held = box_running(slug, box)
        except (OSError, ValueError) as exc:
            _stop(stops, "box-unknown", f"dziennik pudełka {box} nieczytelny: {exc}")
            continue
        for step in held:
            _stop(stops, "box-use-held", f"pudełko {box}: box.use bez box.release", "", str(step))
    try:
        slot = admission_load()
        if slot.get("owner"):
            _stop(stops, "box-slot-held", f"globalny slot pudełka zajęty: {slot['owner'].get('name')}")
        if slot.get("queue"):
            _stop(stops, "box-queue", f"kolejka pudełek niepusta: {len(slot['queue'])}")
    except (OSError, ValueError) as exc:
        _stop(stops, "box-slot-unknown", f"stan globalnego slotu nieczytelny: {exc}")
    by_class = {c: sorted(k for k, v in cards.items() if v["class"] == c) for c in (SAFE, HOLD, DECISION)}
    return {"ok": not stops, "stops": stops, "cards": cards, "fingerprint": fingerprint, "classes": by_class,
            "batch1Safe": not stops and not by_class[HOLD] and not by_class[DECISION],
            "keepPaused": by_class[HOLD] + by_class[DECISION], "decisionRequired": by_class[DECISION]}


def verify_preserved(journal, fingerprint: dict) -> list[dict]:
    """Po wycofaniu: każda karta z odcisku ma te same pierwsze zdarzenia (komplet historii, bez kasowania)."""
    lost = []
    for card, want in sorted(fingerprint.items()):
        try:
            events = journal.read_complete(card)
        except (OSError, ValueError) as exc:
            lost.append({"card": card, "text": f"historia nieczytelna: {exc}"})
            continue
        if len(events) < want["events"] or digest(events[:want["events"]]) != want["sha256"]:
            lost.append({"card": card, "text": f"zmienione lub utracone zdarzenia (było {want['events']}, "
                                               f"jest {len(events)})"})
    return lost


def revert_plan(merged: dict | None, run) -> dict:
    """Polecenia odwrócenia integracji karty z główną według zapisanego wyniku scalenia (`card.merged`).

    `run(*args)` → (ok, stdout) gita w repozytorium. Commit scalenia Tide (`commit-tree -p before -p source`) ma
    dwóch rodziców: wtedy wyłącznie `git revert -m 1 <commit>`. Integracja bez commita scalenia: lista commitów
    `before..result` od najnowszego (bez commitów scalających). Brak danych lub niespójność: bez polecenia."""
    merged = merged or {}
    commit, before, source = (merged.get(k) or "" for k in ("resultCommit", "targetCommitBefore", "sourceCommit"))
    if not (commit and before):
        return {"kind": "unknown", "commands": [], "text": "brak zapisu wyniku scalenia z resultCommit/targetCommitBefore"}
    if commit == before:
        return {"kind": "no-commit", "commit": commit, "commands": [],
                "text": "scalenie nie utworzyło commita (źródło już było w gałęzi głównej): decyzja właściciela"}
    ok, out = run("rev-list", "--parents", "-n", "1", commit)
    parents = out.split()[1:] if ok else []
    if len(parents) == 2:
        if parents != [before, source]:
            return {"kind": "inconsistent", "commit": commit, "commands": [],
                    "text": f"rodzice {parents} różni od zapisu scalenia ({before}, {source})"}
        return {"kind": "merge", "commit": commit, "firstParent": before, "commands": [f"git revert -m 1 {commit}"],
                "text": "commit scalenia: odwrócenie względem pierwszego rodzica (stan głównej przed integracją)"}
    ok, out = run("rev-list", before + ".." + commit)
    commits = out.split() if ok else []
    ok_merges, merges = run("rev-list", "--merges", before + ".." + commit)
    if not commits or not ok_merges or merges.split():
        return {"kind": "inconsistent", "commit": commit, "commands": [],
                "text": "integracja bez commita scalenia z commitami scalającymi albo bez historii: decyzja właściciela"}
    return {"kind": "non-merge", "commit": commit, "commits": commits,
            "commands": ["git revert --no-edit " + " ".join(commits)],
            "text": "integracja bez commita scalenia: odwrócenie commitów od najnowszego"}


def _git_runner(repo: str):
    def run(*args):
        from ..gitcommand import git_argv
        done = subprocess.run(git_argv("-C", repo, *args), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        return done.returncode == 0, done.stdout
    return run


def main(argv=None, out=None) -> int:
    """Tylko odczyt; JSON na wyjściu, kod 0 tylko przy pełnym sukcesie:
    `check [--board DIR]` — kontrola przed wycofaniem (raport z odciskiem historii),
    `verify RAPORT.json [--board DIR]` — po wycofaniu: komplet zdarzeń z odcisku raportu `check`,
    `revert-plan K-N [--board DIR]` — polecenie odwrócenia integracji karty z gałęzią główną."""
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="tide_daemon.cardflow.rollback")
    parser.add_argument("command", choices=("check", "verify", "revert-plan"))
    parser.add_argument("target", nargs="?", default="")
    parser.add_argument("--board", default="")
    args = parser.parse_args(argv)
    from ..board.marker import default_board_dir
    from ..board.store import BoardStore
    from .engine import FlowEngine
    from .state import fold
    engine = FlowEngine(BoardStore(args.board or default_board_dir()))
    if args.command == "check":
        result = check(engine)
        code = 0 if result["ok"] else 1
    elif args.command == "verify":
        with open(args.target, encoding="utf-8") as fh:
            lost = verify_preserved(engine.journal, json.load(fh)["fingerprint"])
        result, code = {"ok": not lost, "lost": lost}, 0 if not lost else 1
    else:
        st = fold(engine.journal.read_complete(args.target))
        result = revert_plan(st.merged, _git_runner((st.started or {}).get("repo") or ""))
        code = 0 if result["commands"] else 1
    json.dump(result, out, ensure_ascii=False, indent=1)
    out.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
