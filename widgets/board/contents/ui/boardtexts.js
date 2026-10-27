// Teksty i wiersze widżetu Tablica (kafelek, podpowiedź, popup, pasek boczny) z migawki
// tide-daemon /Board Snapshot(). Czyste funkcje — testy liczą je w qml6 (widgets/tests).
// Stan z koordynatora karty (PT-5) to pole `derived` (column, done, total, note); bez niego
// karta stoi w swojej kolumnie ręcznej (backlog, todo, done).
.pragma library

var MAX_ROWS = 6

function plural(n, one, few, many) {
    const abs = Math.abs(n)
    if (abs === 1) return one
    const tens = abs % 100
    const ones = abs % 10
    return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
}

function cards(n) {
    return n + " " + plural(n, "karta", "karty", "kart")
}

function column(card) {
    return card.boardColumn || card.column || "backlog"
}

function counts(snapshot) {
    const out = { total: 0, backlog: 0, todo: 0, progress: 0, waiting: 0, review: 0, done: 0, projects: 0, tags: {} }
    const projects = {}
    for (const card of (snapshot && snapshot.cards) || []) {
        const col = column(card)
        if (col === "archived") continue
        if (out[col] !== undefined) out[col]++
        if (col === "done") continue
        out.total++
        if (card.project) projects[card.project] = true
        for (const tag of card.resolvedTags || []) out.tags[tag] = (out.tags[tag] || 0) + 1
    }
    out.projects = Object.keys(projects).length
    return out
}

// Części linii kafelka (tylko niezerowe): [{count, word, tint, hollow}]. Bez pracy agentów — ile
// do zrobienia; pusta lista — „pusto”.
function tileParts(n) {
    const parts = []
    if (n.progress) parts.push({ count: n.progress, word: "w toku", tint: "working", hollow: false })
    if (n.waiting) parts.push({ count: n.waiting, word: plural(n.waiting, "czeka", "czekają", "czeka"), tint: "waiting",
                                hollow: false })
    if (n.review) parts.push({ count: n.review, word: "do sprawdzenia", tint: "lavender", hollow: false })
    if (!parts.length && n.todo) parts.push({ count: n.todo, word: "do zrobienia", tint: "textSecondary", hollow: true })
    return parts
}

// „2 w toku · 1 czeka · 1 do sprawdzenia”, „3 do zrobienia”, „pusto”.
function tileSummary(n) {
    const parts = tileParts(n)
    return parts.length ? parts.map(p => p.count + " " + p.word).join(" · ") : "pusto"
}

// Dopisek przy etykiecie: „11 kart”; bez kart — nic.
function tileDetail(n) {
    return n.total ? cards(n.total) : ""
}

function toolTip(available, exists, n) {
    if (!available) return "Usługa tide-daemon nie działa"
    if (!exists || !n.total) return "Tablica pracy jest pusta — kliknij, aby dopisać pierwszą kartę"
    const parts = []
    if (n.progress) parts.push(n.progress + " w toku")
    if (n.waiting) parts.push(n.waiting + " " + plural(n.waiting, "czeka", "czekają", "czeka") + " na ciebie")
    if (n.review) parts.push(n.review + " do sprawdzenia")
    if (n.todo) parts.push(n.todo + " do zrobienia")
    if (n.backlog) parts.push(n.backlog + " w backlogu")
    return "Tablica pracy: " + cards(n.total) + " — " + parts.join(", ") + "\nKliknij, aby zobaczyć, co czeka"
}

// „11 kart w 7 projektach · Firma 5 · własne 6” (dwa najczęstsze tagi).
function subtitle(n) {
    if (!n.total) return "Nic nie ma na tablicy"
    let text = cards(n.total)
    if (n.projects) text += " w " + n.projects + " " + plural(n.projects, "projekcie", "projektach", "projektach")
    const tags = Object.keys(n.tags).sort((a, b) => n.tags[b] - n.tags[a] || a.localeCompare(b)).slice(0, 2)
    for (const tag of tags) text += " · " + tag + " " + n.tags[tag]
    return text
}

var KIND = {
    waiting: { word: "czeka", tint: "waiting", hollow: false },
    progress: { word: "w toku", tint: "working", hollow: false },
    review: { word: "do sprawdzenia", tint: "lavender", hollow: false },
    todo: { word: "do zrobienia", tint: "textSecondary", hollow: true },
    backlog: { word: "backlog", tint: "textMuted", hollow: true }
}

// Wiersze popupu: najpierw to, co czeka na ciebie, potem w toku, do sprawdzenia, do zrobienia;
// gdy tego mało — kilka kart z góry Backlogu. [{id, project, line, kind, word, tint, hollow, progress}].
function rows(snapshot, limit) {
    const max = limit || MAX_ROWS
    const all = (snapshot && snapshot.cards) || []
    const out = []
    for (const kind of ["waiting", "progress", "review", "todo", "backlog"]) {
        if (kind === "backlog" && out.length >= 3) break
        for (const card of all) {
            if (column(card) !== kind) continue
            const d = card.derived || {}
            const progress = d.total > 0 ? Math.min(d.total, d.done || 0) + "/" + d.total : ""
            const line = kind === "waiting" && d.note ? d.note : d.note ? card.title + " · " + d.note : card.title
            out.push(Object.assign({ id: card.id, project: card.projectName || card.project || "", line: line,
                                     kind: kind, progress: progress }, KIND[kind]))
        }
    }
    return { rows: out.slice(0, max), more: Math.max(0, out.length - max) }
}

// Projekty do wyboru przy szybkim dopisaniu: [{slug, name}], pierwszy „bez projektu”.
// Wybór projektu szybkiego dopisania, jak w oknie: projekty z tablicy, potem repozytoria z katalogu
// projektów, które jeszcze nie są projektami — wartość "repo:<ścieżka>"; CreateCard z `repo` zakłada
// projekt razem z kartą.
const REPO = "repo:"

function projects(snapshot) {
    return [{ slug: "", name: "bez projektu", kind: "none" }]
        .concat(((snapshot && snapshot.projects) || []).map(p => ({ slug: p.slug, name: p.name, kind: "project" })))
        .concat(((snapshot && snapshot.repos) || []).map(r => ({ slug: REPO + r.path, name: r.name, kind: "repo" })))
}

// Zapamiętana wartość → bieżąca: slug projektu, "repo:<ścieżka>" albo "" (bez projektu). Repozytorium,
// z którego powstał już projekt, wskazuje ten projekt.
function resolveProject(snapshot, value) {
    if (!value) return ""
    const projectList = (snapshot && snapshot.projects) || []
    if (value.indexOf(REPO) === 0) {
        const path = value.slice(REPO.length)
        const made = projectList.find(p => p.repo === path)
        if (made) return made.slug
        return ((snapshot && snapshot.repos) || []).some(r => r.path === path) ? value : ""
    }
    return projectList.some(p => p.slug === value) ? value : ""
}

function projectName(snapshot, value) {
    if (!value) return "bez projektu"
    const found = projects(snapshot).find(p => p.slug === value)
    return found ? found.name : "bez projektu"
}

// Pola CreateCard dla wybranej wartości: {project} albo {repo}.
function projectRequest(value) {
    if (!value) return {}
    return value.indexOf(REPO) === 0 ? { repo: value.slice(REPO.length) } : { project: value }
}

// Odpowiedź CreateCard: "" — dopisane, inaczej komunikat po polsku.
function replyError(text) {
    let reply = null
    try { reply = JSON.parse(text) } catch (e) {}
    if (!reply) return "Usługa tide-daemon nie odpowiedziała — spróbuj jeszcze raz."
    return reply.ok ? "" : String(reply.message || reply.error || "nie udało się dopisać karty")
}
