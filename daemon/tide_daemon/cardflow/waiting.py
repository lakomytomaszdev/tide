"""Graf blokerów i zapisanych oczekiwań; pusty ticket oznacza domknięcie całej karty."""


def completed(st):
    return st.imported_done | st.rejected | {
        ticket for (action, ticket), made in st.results.items() if action == "merge" and made.get("ok")}


def would_cycle(states, card, step, *, ticket="", other=""):
    """Czy nowa krawędź wraca do własnego ticketu przez plan, waitTicket lub waitCard?"""
    graph = {}

    def edge(source, target):
        graph.setdefault(source, set()).add(target)

    for ident, st in states.items():
        if st.closed or st.card_finished:
            continue
        ready = completed(st)
        rows = (st.plan_approved or {}).get("rows") or []
        for row in rows:
            own = row.get("id") or row.get("workId") or ""
            if own in ready:
                continue
            edge((ident, ""), (ident, own))
            for blocker in row.get("blockers") or []:
                if blocker not in ready:
                    edge((ident, own), (ident, blocker))
        # Wejście Od razu nie ma planu; czynny ticket także blokuje domknięcie karty.
        resumed = {s.get("parentStep") for s in st.steps.values() if s.get("parentStep")}
        for name, source in st.steps.items():
            own = source.get("ticket") or ""
            if name in st.superseded or name in resumed or own in ready:
                continue
            if own:
                edge((ident, ""), (ident, own))
            decision = st.step_decisions.get(name) or {}
            target = decision.get("waitTicket")
            if target and target not in ready:
                edge((ident, own), (ident, target))
            target = decision.get("waitCard")
            if target and not (target in states and (states[target].closed or states[target].card_finished)):
                edge((ident, own), (target, ""))

    source = (card, step.get("ticket") or "")
    target = (other, "") if other else (card, ticket)
    seen, pending = set(), [target]
    while pending:
        node = pending.pop()
        if node == source:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(graph.get(node, ()))
    return False
