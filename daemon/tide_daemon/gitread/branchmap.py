"""Mapa gałęzi jako dane (`Git.BranchMap`, K-99, PT-387): czysta projekcja dzienników kart i Tablicy — bez gita.

`request(journal, board, text)` odpowiada na dwa pytania:

- `{}` — które projekty Tablicy mają repozytorium (lista wyboru w Worktrees):
  `{ok, projects: [{slug, name, repo, cards}]}`; `cards` to liczba kart Tablicy z tym projektem głównym,
- `{project, limit?}` — jak gałęzie kart i ticketów projektu wiszą na sobie:
  `{ok, project, repo, nodes, edges, total, truncated, warnings}`.

Moduł nie uruchamia gita, nie zapisuje dziennika ani Tablicy i nie dostaje ścieżki z żądania: repozytorium pochodzi
z projektu Tablicy (`board_view`), a stan kart czytają istniejące funkcje odczytu stanu (`Journal.state` → `fold`,
`base_of`, `ticket_of`, `merge_of`). Metryki gita (istnienie gałęzi, worktree, upstream…) dokłada #11 jako nowe pola
węzła — istniejące pola się nie zmieniają.

Węzły (`nodes[]`, pola wspólne `id, kind, ref, branch, state`):

- `base` — ref użyty w dzienniku jako baza startu albo cel scalenia karty (`refs/heads/develop`,
  `refs/remotes/origin/main`…); mapa nie zakłada, że główna to `master`, i nie ma pola `mainRef`;
- `card` — gałąź karty z wyniku `card-branch` (`card`, `title`, `column`, `started`, `merged`);
- `ticket` — gałąź ticketu z wyniku `ticket-branch` (`card`, `ticket`, `title`, `started`, `merged`);
- `unknown-base` — jedna grupa kart, których baza w dzienniku jest nieznana.

`state`: `active`, `closed` (karta domknięta albo w Gotowe/Archiwum; ticket niescalony w takiej karcie), `merged`
(karta scalona do celu, ticket scalony do gałęzi karty), `rejected` (odrzucony ticket). `started` to
`{ref, sha, date}` (po skutecznej korekcie bazy — nowa baza, pierwotna w `started.initial: {ref, sha}`), `merged` —
`{targetRef, sha, date}`.

Krawędzie (`edges[]`, `{kind, from, to}`): `start` od rodzica do gałęzi (baza, karta, z której wystartowano —
dopasowanie po pełnym refie — albo `unknown-base`; ticket od gałęzi karty) i `merge` od scalonej gałęzi do celu
(ticket → karta z wyniku `merge`, karta → cel z `card.merged`/`card.main-merged`).

Limit: domyślnie 200 gałęzi kart i ticketów, najwyżej 500 (większy jest obcinany). Wybierane są najnowsze karty
(po ostatnim zdarzeniu karty albo jej ticketu) razem z ich najnowszymi ticketami, więc ticket nigdy nie zostaje
bez swojej karty; `total` liczy wszystkie gałęzie, `truncated` mówi, że część pominięto.
"""
import json
import os
import traceback
from datetime import datetime

from ..board.model import card_num
from ..cardflow.state import base_of, merge_of, ticket_of

DEFAULT_LIMIT = 200
MAX_LIMIT = 500
UNKNOWN_BASE = "unknown-base"
CLOSED_COLUMNS = ("done", "archived")


class MapError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def board_view(board) -> dict:
    """Lekka, niezmienna kopia Tablicy dla puli odczytu: projekty (`slug`, `name`, `repo`) i karty (`project`,
    `title`, `column`). Liczona w wątku głównym, jak lista repozytoriów `Git.Diff`."""
    return {"projects": [{"slug": p.slug, "name": p.name or p.slug, "repo": p.repo or ""}
                         for p in board.projects.values()],
            "cards": {c.id: {"project": c.project, "title": c.title, "column": c.column}
                      for c in board.cards.values()}}


def request(journal, board, text) -> dict:
    """Żądanie D-Bus (tekst JSON) → odpowiedź `{ok: true, …}` albo `{ok: false, error, message}`. Nigdy nie rzuca
    wyjątku, a treść nieoczekiwanego wyjątku nie trafia do odpowiedzi (tylko do dziennika usługi)."""
    try:
        try:
            data = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise MapError("bad-request", "żądanie musi być obiektem JSON")
        if board is None:
            raise MapError("unavailable", "Tablica jest wyłączona w usłudze")
        if data.get("project") is None:
            return {"ok": True, "projects": projects(board)}
        return {"ok": True, **branch_map(journal, board, data.get("project"), data.get("limit"))}
    except MapError as exc:
        return {"ok": False, "error": exc.code, "message": exc.message}
    except Exception:  # noqa: BLE001 — odczyt nie może wywrócić usługi ani pokazać wnętrza wyjątku
        print("tide-daemon: Git.BranchMap: " + traceback.format_exc(), flush=True)
        return {"ok": False, "error": "failed", "message": "Nie udało się zbudować mapy gałęzi (szczegóły w dzienniku "
                                                           "usługi)."}


def projects(board) -> list:
    """Projekty Tablicy z repozytorium, po nazwie; `cards` — liczba kart z tym projektem głównym."""
    counts = {}
    for info in board["cards"].values():
        counts[info["project"]] = counts.get(info["project"], 0) + 1
    found = [{"slug": p["slug"], "name": p["name"], "repo": p["repo"], "cards": counts.get(p["slug"], 0)}
             for p in board["projects"] if p["repo"]]
    return sorted(found, key=lambda p: (p["name"].casefold(), p["slug"]))


def _limit(value) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MapError("bad-request", f"limit musi być dodatnią liczbą całkowitą (najwyżej {MAX_LIMIT})")
    return min(value, MAX_LIMIT)


def branch_map(journal, board, slug, limit=None) -> dict:
    if not isinstance(slug, str) or not slug.strip():
        raise MapError("bad-request", "project musi być niepustym slugiem projektu Tablicy")
    limit = _limit(limit)
    project = next((p for p in board["projects"] if p["slug"] == slug), None)
    if project is None:
        raise MapError("unknown-project", f"nieznany projekt Tablicy: {slug}")
    if not project["repo"]:
        raise MapError("no-repo", f"projekt {slug} nie ma repozytorium")
    if journal is None:
        raise MapError("unavailable", "przepływ karty jest wyłączony w usłudze")
    repo = project["repo"]
    real_repo = os.path.realpath(repo)
    warnings, groups = [], []
    for card in journal.cards():
        try:
            st = journal.state(card)
        except Exception:  # noqa: BLE001 — jeden zepsuty dziennik nie zabiera mapy pozostałych kart
            warnings.append(f"{card}: nie udało się odczytać dziennika karty")
            continue
        if not _belongs(st, slug, real_repo):
            continue
        group = _card_group(card, st, board["cards"].get(card) or {}, warnings)
        if group is not None:
            groups.append(group)
    total = sum(1 + len(g["tickets"]) for g in groups)
    chosen = _choose(groups, limit)
    nodes, edges = _graph(chosen, warnings)
    return {"project": slug, "repo": repo, "nodes": nodes, "edges": edges, "total": total,
            "truncated": total > limit, "warnings": warnings}


def _belongs(st, slug: str, real_repo: str) -> bool:
    """Karta należy do mapy projektu, gdy jej ostatnie zlecenie (`card.started`) dotyczy tego projektu albo jego
    repozytorium (po ścieżce kanonicznej)."""
    started = st.started or {}
    if not started:
        return False
    if started.get("project") == slug:
        return True
    repo = started.get("repo")
    return isinstance(repo, str) and os.path.isabs(repo) and os.path.realpath(repo) == real_repo


def _epoch(ts) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp() if isinstance(ts, str) and ts else 0.0
    except ValueError:
        return 0.0


def _ts(event) -> str:
    value = (event or {}).get("ts")
    return value if isinstance(value, str) else ""


def _card_group(card: str, st, info: dict, warnings: list):
    """Węzeł karty i jej ticketów z jednego dziennika; None — karta bez gałęzi (nie ma czego pokazać)."""
    branch = ticket_of(st)["cardBranch"]
    if not branch:
        return None
    if st.merged:
        state = "merged"
    elif st.card_finished or st.closed or info.get("column") in CLOSED_COLUMNS:
        state = "closed"
    else:
        state = "active"
    base = base_of(st)
    known = base.get("status") == "known"
    started = {"ref": base["baseRef"] if known else "", "sha": base["baseCommit"] if known else "",
               "date": _ts(st.base_change or st.started)}
    if st.base_change:
        initial = st.initial_base or {}
        started["initial"] = {"ref": initial.get("baseRef") or "", "sha": initial.get("baseCommit") or ""}
    node = {"id": f"card:{card}", "kind": "card", "ref": f"refs/heads/{branch}", "branch": branch, "card": card}
    for key in ("title", "column"):
        if info.get(key):
            node[key] = info[key]
    node.update(state=state, started=started)
    if st.merged:
        done = merge_of(st.merged)
        node["merged"] = {"targetRef": done["targetRef"], "sha": done["resultCommit"], "date": _ts(done)}
        if not done["targetRef"]:
            warnings.append(f"{card}: dziennik nie zapisał celu scalenia karty")
    tickets = _tickets(card, st, node)
    activity = max([st.last_activity_ts or 0.0, _epoch(_ts(st.started))] + [t["activity"] for t in tickets])
    return {"card": card, "node": node, "tickets": tickets, "activity": activity}


def _tickets(card: str, st, card_node: dict) -> list:
    titles = {row.get("id"): row.get("title") or "" for row in ((st.plan_approved or {}).get("rows") or [])
              if isinstance(row, dict)}
    out, seen = [], set()
    for (action, key), res in st.results.items():
        if action != "ticket-branch" or not res.get("ok"):
            continue
        ticket = res.get("ticket") or key
        if not isinstance(ticket, str) or not ticket or ticket in seen:
            continue
        seen.add(ticket)
        info = ticket_of(st, ticket)
        if not info["branch"]:
            continue
        intent = st.intents.get(("ticket-branch", key)) or {}
        start_ref = intent.get("base") if isinstance(intent.get("base"), str) and \
            intent["base"].startswith("refs/") else card_node["ref"]
        merged = st.results.get(("merge", ticket)) or {}
        if merged.get("ok"):
            state = "merged"
        elif ticket in st.rejected:
            state = "rejected"
        elif card_node["state"] != "active":
            state = "closed"
        else:
            state = "active"
        node = {"id": f"ticket:{card}:{ticket}", "kind": "ticket", "ref": f"refs/heads/{info['branch']}",
                "branch": info["branch"], "card": card, "ticket": ticket}
        title = titles.get(ticket) or info["title"]
        if title:
            node["title"] = title
        node.update(state=state, started={"ref": start_ref, "sha": info["base"] or "", "date": _ts(res)})
        stamps = [_ts(res)]
        if merged.get("ok"):
            into = (st.intents.get(("merge", ticket)) or {}).get("into") or card_node["branch"]
            node["merged"] = {"targetRef": f"refs/heads/{into}", "sha": merged.get("commit") or "",
                              "date": _ts(merged)}
            stamps.append(_ts(merged))
        for name, step in st.steps.items():
            if step.get("ticket") == ticket:
                stamps += [_ts(step), _ts(st.finished.get(name))]
        out.append({"node": node, "activity": max(_epoch(s) for s in stamps)})
    return out


def _choose(groups: list, limit: int) -> list:
    """Najnowsze karty z ich najnowszymi ticketami, łącznie najwyżej `limit` gałęzi."""
    # Remis czasu (np. dziennik bez znaczników): nowsza karta (większy numer) pierwsza.
    ordered = sorted(groups, key=lambda g: (-g["activity"], -card_num(g["card"])))
    chosen, budget = [], limit
    for group in ordered:
        if budget <= 0:
            break
        tickets = sorted(group["tickets"], key=lambda t: -t["activity"])[:budget - 1]
        chosen.append((group, tickets))
        budget -= 1 + len(tickets)
    return chosen


def _short(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _graph(chosen: list, warnings: list) -> tuple[list, list]:
    by_ref = {group["node"]["ref"]: group["node"]["id"] for group, _ in chosen}
    bases, unknown, branches, edges = set(), False, [], []

    def target(ref: str, own: str) -> str:
        """Węzeł o pełnym refie `ref`: gałąź innej karty z mapy albo węzeł `base`."""
        if by_ref.get(ref) and by_ref[ref] != own:
            return by_ref[ref]
        bases.add(ref)
        return f"base:{ref}"

    for group, tickets in chosen:
        node = group["node"]
        branches.append(node)
        ref = node["started"]["ref"]
        if ref:
            parent = target(ref, node["id"])
        else:
            parent, unknown = UNKNOWN_BASE, True
        edges.append({"kind": "start", "from": parent, "to": node["id"]})
        for ticket in tickets:
            branches.append(ticket["node"])
            edges.append({"kind": "start", "from": node["id"], "to": ticket["node"]["id"]})
            if ticket["node"]["state"] == "merged":
                edges.append({"kind": "merge", "from": ticket["node"]["id"], "to": node["id"]})
        goal = (node.get("merged") or {}).get("targetRef")
        if goal:
            edges.append({"kind": "merge", "from": node["id"], "to": target(goal, node["id"])})
    nodes = [{"id": f"base:{ref}", "kind": "base", "ref": ref, "branch": _short(ref), "state": "active"}
             for ref in sorted(bases)]
    if unknown:
        nodes.append({"id": UNKNOWN_BASE, "kind": UNKNOWN_BASE, "ref": "", "branch": "", "title": "Nieznana baza",
                      "state": "active"})
    return nodes + branches, edges
