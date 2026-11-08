"""Wybór bieżącej próby ticketu z dziennika, bez odczytu logów i ścieżek."""
import hashlib
import json

from .baseproposal import blocked


def view(st):
    order = (st.started or {}).get("seq", 0)
    current = {name: step for name, step in st.steps.items()
               if name not in st.superseded and step.get("seq", 0) > order
               and step.get("baseVersion", 0) == st.base_version and step.get("ticket")
               and step.get("kind") in ("ticket", "fix", "review", "interactive")}
    attempts = []
    for name, step in st.steps.items():
        # Zakończony autor ustępuje kolejnemu review/poprawce/wznowieniu tego ticketu.
        # Dwie nadal czynne próby pozostają niejednoznaczne, niezależnie od dat logów.
        latest = name in current and not (name in st.finished and any(
            other.get("ticket") == step.get("ticket") and other.get("seq", 0) > step.get("seq", 0)
            for other in current.values()))
        reason = ""
        if not latest or st.card_finished or step.get("ticket") in st.rejected or \
                (st.results.get(("merge", step.get("ticket"))) or {}).get("ok"):
            reason = "no-step"
        elif blocked(st) or st.base_validation_required or st.base_generation_wait:
            reason = "base-decision"
        elif step.get("mode") == "interactive" and name not in st.finished:
            reason = "already-entered"
        elif any(other.get("worktree") == step.get("worktree") and other.get("step") != name
                 for other in st.running()):
            reason = "worktree-busy"
        elif any(st.steps.get(outside, {}).get("worktree") == step.get("worktree")
                 for outside in st.outside_started):
            # Polecenie outside zajmuje katalog także po zakończeniu własnego kroku.
            reason = "worktree-busy"
        elif any(other.get("worktree") == step.get("worktree") for other in st.open_consoles()):
            reason = "console-open"
        attempts.append({"id": name, "ticket": step.get("ticket") or "", "kind": step.get("kind") or "",
                         "attempt": step.get("seq", 0), "round": step.get("round", 0),
                         "currentAttempt": latest, "available": not reason, "error": reason})
    # Odcisk nie obejmuje postępu ani dat aktywności; obejmuje zmianę próby, zlecenia,
    # bazy, wyniku i zajętości. Hash nie daje prawa do odczytu ścieżek zawartych w stanie.
    context = {"order": st.started, "base": st.base, "baseVersion": st.base_version,
               "steps": st.steps, "caretakers": st.caretakers, "finished": st.finished,
               "superseded": sorted(st.superseded), "rejected": sorted(st.rejected),
               "attempts": attempts, "decisions": st.step_decisions,
               "outside": st.outside_started, "paused": st.paused}
    revision = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"revision": revision, "attempts": attempts}


def enrich(st, out):
    projected = view(st)
    flow = out["flow"]
    flow["ticketEntryVersion"] = 1
    flow["entryRevision"] = projected["revision"]
    attempts = {item["id"]: item for item in projected["attempts"]}
    for step in flow.get("steps", []):
        item = attempts[step["id"]]
        step.update({key: item[key] for key in ("attempt", "round", "currentAttempt", "available", "error")})
