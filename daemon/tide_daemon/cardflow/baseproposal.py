"""Propozycja bazy jest danymi pytania, nigdy poleceniem zmiany gita."""
from __future__ import annotations

LIMITS = ("Korekta v1 jest możliwa tylko bez commitów wkładu i bez czynnych procesów, zwykle w Od razu "
          "przed pierwszą pracą. Commit planu po Lekkie/Mam plan/Pełne, WIP lub wcześniejsza praca po review "
          "(K-49) oznaczają odmowę korekty i zachowanie danych. Cel pkt 4 i historie 35–37 pozostają częściowe. "
          "Zachowaj bazę, aby kontynuować, albo pozostaw kartę w oczekiwaniu i osobno zaplanuj przeniesienie. "
          "Zakres następczy ma stały klucz K-50-replay-code (followups/replay-code.md przy planie); "
          "podgląd sprawdza dostępność szkicu. Nie oznacza to opublikowanej karty ani obietnicy replay "
          "w dalszym tickecie v1. Nie wykonuj ręcznego reset/rebase zarządzanych "
          "gałęzi ani nie usuwaj katalogów.")


def clean(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"ref": "", "evidence": "", "error": "baseProposal musi być obiektem"}
    ref, evidence = raw.get("ref"), raw.get("evidence")
    if not isinstance(ref, str) or not ref.startswith(("refs/heads/", "refs/remotes/")) or len(ref) > 1024 or \
            any(c.isspace() or ord(c) < 32 for c in ref) or not isinstance(evidence, str) or \
            not evidence.strip() or len(evidence) > 4000:
        return {"ref": ref[:1024] if isinstance(ref, str) else "", "evidence": evidence[:4000]
                if isinstance(evidence, str) else "", "error": "wymagany pełny ref i niepusty dowód tekstowy"}
    return {"ref": ref, "evidence": evidence.strip()}


def validate(raw, git, repo):
    proposal = clean(raw)
    if proposal is None:
        return None
    proposal["valid"] = False
    if proposal.get("error"):
        return proposal
    # Format sprawdzamy przed rozwiązywaniem rewizji; kandydat nie trafia do operacji mutujących.
    if not git.run(repo, "check-ref-format", proposal["ref"]).ok:
        return {**proposal, "error": "niepoprawny ref bazy"}
    from .gitops import FlowGitError
    try:
        base = git.resolve_base(repo, proposal["ref"], git.sha(repo, proposal["ref"]))
    except FlowGitError as exc:
        return {**proposal, "error": str(exc)}
    return {**proposal, "valid": True, "baseCommit": base["baseCommit"]}


def pending(st):
    return ([st.base_request["decision"]] if st.base_request else []) + [name for name, done in st.finished.items()
            if done.get("result") == "question" and (done.get("baseProposal") or {}).get("valid") is True
            and name in st.steps and name not in st.superseded and name not in st.step_decisions]


def blocked(st):
    """Jedna zapora kontekstu także dla starych API i recovery zwykłych operacji."""
    return bool(pending(st) or generation_blocked(st))


def generation_blocked(st):
    return bool(st.base_validation_required or st.base_generation_wait or
                any(i.get("action") == "change-base" for i in st.open_intents()))


def context(base):
    ref, sha = base.get("baseRef") or "unknown", base.get("baseCommit") or "unknown"
    return (f"\nBieżąca baza karty: {ref}\nPrzypięty SHA bazy: {sha}\n"
            "Podejrzenie złej bazy wymaga sprawdzenia i pytania. Sam brak pliku nie dowodzi błędnej bazy "
            "ani nie pozwala na checkout/rebase/reset lub tworzenie zastępczego pliku. Zakończ krok istniejącym "
            'wynikiem {"result":"question","question":"…","baseProposal":{"ref":"refs/heads/…",'
            '"evidence":"sprawdzone dowody tekstowe"}}. Opcje pytania pozostają opcjonalne.\n' + LIMITS + "\n")
