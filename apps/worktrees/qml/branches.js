.pragma library
// Tryb „Gałęzie” okna Worktrees (K-99, PT-388): drzewo z odpowiedzi `Git.BranchMap({project})` i teksty stanu;
// dane okna scalania z mapy (PT-395).
// Czysta logika bez QML — testowana osobno i w sondzie okna.
// Skróty gałęzi i dat to wspólny formatter „startu” (git/start.js), bez kopii.
.import "git/start.js" as Start

// Etykiety kolumn Tablicy (daemon/tide_daemon/board/model.py, COLUMNS); mapa zwraca ID kolumny.
const COLUMN_LABELS = {
    backlog: "Backlog", todo: "Do zrobienia", progress: "W toku", waiting: "Czeka na mnie", review: "Do sprawdzenia",
    done: "Gotowe", archived: "Archiwum"
}

// Stan węzła jako tekst: karta — „w toku”, „domknięta”, „scalona do master (11.11)”; ticket — „scalony”
// (z celem i datą, gdy są), „w toku”, „zamknięty” (karta domknięta bez scalenia ticketu) albo „odrzucony”.
function stateText(node) {
    const merged = node.merged || null
    const suffix = merged
        ? [merged.targetRef ? "do " + Start.shortRef(merged.targetRef) : "", Start.shortDate(merged.date) ? "(" + Start.shortDate(merged.date) + ")" : ""]
            .filter(x => x !== "").join(" ")
        : ""
    if (node.kind === "card") {
        if (node.state === "merged") return "scalona" + (suffix ? " " + suffix : "")
        if (node.state === "closed") return "domknięta"
        return "w toku"
    }
    if (node.state === "merged") return "scalony"
    if (node.state === "rejected") return "odrzucony"
    if (node.state === "closed") return "zamknięty"
    return "w toku"
}

// Pierwszy wiersz węzła: karta — ID, tytuł; ticket — ID, tytuł; baza — krótka nazwa refu.
function titleText(node) {
    if (node.kind === "base") return Start.shortRef(node.ref) || node.branch || node.id
    if (node.kind === "unknown-base") return node.title || "Nieznana baza"
    const id = node.kind === "card" ? node.card : node.ticket
    return node.title ? id + " · " + node.title : String(id || "")
}

// Drugi wiersz karty: kolumna Tablicy i stan; ticketu — sam stan.
function detailText(node) {
    if (node.kind === "base" || node.kind === "unknown-base") return ""
    const parts = []
    if (node.kind === "card" && node.column) parts.push(COLUMN_LABELS[node.column] || node.column)
    parts.push(stateText(node))
    return parts.join(" · ")
}

// Płaska lista wierszy drzewa w kolejności wyświetlania: korzenie — bazy (kolejność z odpowiedzi), na końcu grupa
// „baza nieznana”; dzieci — po krawędziach `start` w kolejności węzłów odpowiedzi (karty od najnowszej, tickety
// pod swoją kartą). Węzeł osiągany dwa razy (zepsute dane) pojawia się raz; węzły bez rodzica nie znikają.
function rows(nodes, edges) {
    const list = Array.isArray(nodes) ? nodes : []
    const byId = {}
    const order = {}
    list.forEach((n, i) => { byId[n.id] = n; order[n.id] = i })
    const children = {}
    const hasParent = {}
    for (const e of (Array.isArray(edges) ? edges : [])) {
        if (e.kind !== "start" || !byId[e.from] || !byId[e.to]) continue
        (children[e.from] = children[e.from] || []).push(e.to)
        hasParent[e.to] = true
    }
    const roots = list.filter(n => n.kind === "base" && !hasParent[n.id])
        .concat(list.filter(n => n.kind === "unknown-base" && !hasParent[n.id]))
    const orphans = list.filter(n => !hasParent[n.id] && roots.indexOf(n) < 0)
    const out = []
    const seen = {}
    function walk(id, depth) {
        if (seen[id]) return
        seen[id] = true
        const node = byId[id]
        out.push({ id: id, depth: depth, node: node })
        const kids = (children[id] || []).slice().sort((a, b) => order[a] - order[b])
        for (const kid of kids) walk(kid, depth + 1)
    }
    for (const n of roots.concat(orphans)) walk(n.id, 0)
    return out
}

// „pokazano N z M” (N — gałęzie kart i ticketów w odpowiedzi), tylko gdy usługa obcięła mapę; inaczej "".
function shownText(reply) {
    if (!reply || reply.truncated !== true) return ""
    const nodes = Array.isArray(reply.nodes) ? reply.nodes : []
    const shown = nodes.filter(n => n.kind === "card" || n.kind === "ticket").length
    return "pokazano " + shown + " z " + reply.total
}

function hasBranches(reply) {
    return !!reply && Array.isArray(reply.nodes) && reply.nodes.some(n => n.kind === "card" || n.kind === "ticket")
}

function errorText(reply) {
    const message = reply && typeof reply.message === "string" ? reply.message : ""
    return message !== "" ? message : "Nie udało się odczytać mapy gałęzi."
}

function mapRequest(project) {
    return {project: project}
}

function cardDiffRequest(card) {
    return {source: "card", card: card}
}

// --- Okno scalania z mapy (K-99, PT-395) -----------------------------------------------------------------

// „Scal…” tylko przy gałęzi karty domkniętej i niescalonej (`state: closed`); karta w toku, scalona, ticket
// i baza go nie mają. O tym, czy scalenie jest naprawdę możliwe, decyduje podgląd usługi w oknie.
function canMerge(node) {
    return !!node && node.kind === "card" && node.state === "closed" && typeof node.card === "string" && node.card !== ""
}

// Znana baza karty (pełny ref) dla okna scalania: `started.ref` węzła; pusta — baza nieznana (cel to propozycja).
function cardBaseRef(node) {
    const started = node && node.started
    return started && typeof started.ref === "string" ? started.ref : ""
}

// „Start od: master (3.11, 58a3f26)” z pól węzła mapy przez wspólny formatter (git/start.js, `cardStartLine`):
// `started` to baza z datą startu, a po korekcie bazy `started.initial` (wtedy zdanie bez daty, jak w Tablicy).
function cardStartLine(node) {
    const started = node && node.started
    if (!started || typeof started !== "object") return ""
    const point = (ref, sha) => typeof ref === "string" && ref !== ""
        ? { status: "known", baseRef: ref, baseCommit: typeof sha === "string" ? sha : "" } : { status: "unknown" }
    const flow = { base: point(started.ref, started.sha),
                   timeline: [{ type: "card.started", ts: typeof started.date === "string" ? started.date : "" }] }
    if (started.initial && typeof started.initial === "object")
        flow.initialBase = point(started.initial.ref, started.initial.sha)
    return Start.cardStartLine(flow)
}

// Powód odmowy usługi dla okna scalania; "" przy sukcesie.
function mergeErrorText(reply) {
    if (reply && reply.ok === true) return ""
    const message = reply && typeof reply.message === "string" ? reply.message : ""
    if (message !== "") return message
    const code = reply && typeof reply.error === "string" ? reply.error : ""
    return code !== "" ? "Usługa odmówiła (" + code + ")." : "Usługa tide-daemon nie odpowiedziała — spróbuj jeszcze raz."
}

// Komunikat po udanym `MergeCard` (z okna: „Scalono K-5 do main”); `already` — źródło było już w celu.
function mergedText(message, reply) {
    if (!message) return ""
    return message + (reply && reply.already === true ? " — gałąź karty była już w celu, bez nowego commita." : ".")
}

// Projekt do pokazania: zapamiętany, gdy nadal jest na liście; inaczej pierwszy z listy; bez listy — "".
function pickProject(projects, remembered) {
    const list = Array.isArray(projects) ? projects : []
    if (list.some(p => p.slug === remembered)) return remembered
    return list.length > 0 ? list[0].slug : ""
}
