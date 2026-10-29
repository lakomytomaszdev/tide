// Logika widoku Tablica bez QML: kolumny, filtry, kolejność przy przeciąganiu, teksty i liczby.
// Migawka to wynik /Board Snapshot() z tide-daemon (docs/tide-daemon.md#tablica). Funkcje są czyste,
// więc testy liczą je w QJSEngine (apps/board/tests).
//
// Stan z koordynatora karty (PT-5) przychodzi w polu `derived` karty. Widok czyta z niego (wszystko
// opcjonalne): column (progress, waiting, review, done), done i total (postęp ticketów), tool
// (claude, codex), note (jedno zdanie: co teraz albo pytanie), branch (gałąź do sprawdzenia),
// session (koordynator do „Otwórz w Sesjach”). Bez `derived` karta stoi w swojej kolumnie ręcznej.
.pragma library

// Kolumny ręczne: tylko tu da się przeciągnąć kartę. Archiwum (archived) — akcją z karty.
var MANUAL = ["backlog", "todo", "done"]
var DONE_PREVIEW = 6
var NO_PROJECT = "__none__"

// Kolumny tablicy w kolejności mocka: kropka w kolorze tokenu palety, puste kółko (hollow) dla
// Backlogu i Gotowych. Nazwy bierzemy z migawki, gdy je podaje.
var COLUMNS = [
    { id: "backlog", label: "Backlog", tint: "textMuted", hollow: true },
    { id: "todo", label: "Do zrobienia", tint: "textSecondary", hollow: false },
    { id: "progress", label: "W toku", tint: "teal", hollow: false },
    { id: "waiting", label: "Czeka na mnie", tint: "peach", hollow: false },
    { id: "review", label: "Do sprawdzenia", tint: "lavender", hollow: false },
    { id: "done", label: "Gotowe", tint: "sky", hollow: true }
]

function parse(text, fallback) {
    try {
        const value = JSON.parse(text)
        return value === null || value === undefined ? fallback : value
    } catch (e) {
        return fallback
    }
}

// Polska liczba mnoga: plural(5, "karta", "karty", "kart") → "kart".
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

function fold(text) {
    return String(text || "").toLocaleLowerCase()
}

function columns(snapshot) {
    const labels = {}
    for (const c of (snapshot && snapshot.columns) || []) labels[c.id] = c.label
    return COLUMNS.map(c => Object.assign({}, c, { label: labels[c.id] || c.label, manual: MANUAL.indexOf(c.id) >= 0 }))
}

function isManual(column) {
    return MANUAL.indexOf(column) >= 0
}

function displayColumn(card) {
    return card.boardColumn || card.column || "backlog"
}

// Karta w „Do sprawdzenia” albo w Gotowych z wyliczenia koordynatora: tak potwierdzasz sprawdzenie
// (do Gotowych) albo odsyłasz do poprawy (do Do zrobienia).
function confirmable(card) {
    if (!card || !card.derived) return false
    const shown = displayColumn(card)
    return shown === "review" || (shown === "done" && card.column !== "done")
}

// Pracująca karta przepływu karty (PT-58, stan z dziennika: `derived.flow`) w W toku albo Czeka na mnie.
function workingFlow(card) {
    const shown = card ? displayColumn(card) : ""
    return !!(card && card.derived && card.derived.flow) && (shown === "progress" || shown === "waiting")
}

// Karta w kolumnie ręcznej (bez stanu z koordynatora), do potwierdzenia albo pracująca karta z dziennikiem — tylko
// taką da się przeciągać.
function draggable(card) {
    return !!card && ((isManual(displayColumn(card)) && isManual(card.column)) || confirmable(card) || workingFlow(card))
}

// Dokąd wolno przenieść kartę: z kolumn ręcznych do każdej ręcznej, kartę do potwierdzenia — tylko do
// Gotowych albo z powrotem do Do zrobienia, pracującą kartę z dziennikiem — do Backlogu (wstrzymanie) albo do
// Gotowych (koniec, zmiany zapisane). Do kolumn wyliczanych nigdy.
function dropTargets(card) {
    if (confirmable(card)) return ["done", "todo"]
    if (workingFlow(card)) return ["backlog", "done"]
    return draggable(card) ? MANUAL.slice() : []
}

// Cel „Otwórz w Sesjach” / „Przejdź do agenta”: koordynator karty (/Coordination), inaczej jej sesja (/Agents).
function agentTarget(card) {
    const d = card && card.derived
    return d ? String(d.coordinator || d.session || "") : ""
}

// Sesja agenta karty do „Przejdź do agenta”: okno Konsoli albo wątek Codexa (/Agents ActivateSession).
// Koordynator to też sesja, więc bez osobnej sesji idzie jego id.
function agentSession(card) {
    const d = card && card.derived
    return d ? String(d.session || d.coordinator || "") : ""
}

// Koordynator karty do „Otwórz w Sesjach” (widok Sesje okna Tide); bez koordynatora — pusto.
function sessionsTarget(card) {
    const d = card && card.derived
    return d ? String(d.coordinator || "") : ""
}

// Czy cel z agentTarget to koordynator którejś karty (wtedy okno Tide na Sesjach), a nie zwykła sesja.
function isCoordinator(snapshot, target) {
    return !!target && ((snapshot && snapshot.cards) || []).some(c => c.derived && c.derived.coordinator === target)
}

// --- filtry --------------------------------------------------------------------------------

// filters: {tag: "" | nazwa, project: "" | slug | NO_PROJECT, query: ""}
function matches(card, filters) {
    if (!filters) return true
    if (filters.tag) {
        const wanted = fold(filters.tag)
        if (!(card.resolvedTags || []).some(t => fold(t) === wanted)) return false
    }
    if (filters.project) {
        if (filters.project === NO_PROJECT ? !!card.project : card.project !== filters.project) return false
    }
    const query = fold(filters.query).trim()
    if (query) {
        const derived = card.derived || {}
        const haystack = [card.id, card.title, card.projectName || card.project, card.description, card.note,
                          card.youtrack, card.branch, derived.note, (card.resolvedTags || []).join(" ")].map(fold).join("\n")
        for (const word of query.split(/\s+/))
            if (haystack.indexOf(word) < 0) return false
    }
    return true
}

function active(card) {
    const column = displayColumn(card)
    return column !== "archived"
}

// Karty widoczne na tablicy (bez archiwum), po filtrach, z przesunięciami „w locie”:
// optimistic = [{id, column, before}, …] — przeniesienia wysłane do usługi, zanim przyszła nowa migawka
// (pojedynczy obiekt też działa).
function visible(snapshot, filters, optimistic) {
    let list = ((snapshot && snapshot.cards) || []).filter(c => active(c) && matches(c, filters))
    const moves = Array.isArray(optimistic) ? optimistic : optimistic && optimistic.id ? [optimistic] : []
    for (const move of moves) list = applyMove(list, move)
    return list
}

function applyMove(list, move) {
    const index = list.findIndex(c => c.id === move.id)
    if (index < 0) return list
    const moved = Object.assign({}, list[index], { column: move.column, boardColumn: move.column })
    const rest = list.slice(0, index).concat(list.slice(index + 1))
    if (move.column === "archived") return rest
    let at = move.before ? rest.findIndex(c => c.id === move.before) : -1
    if (at < 0) {
        // Na koniec kolumny: za ostatnią kartą tej kolumny.
        at = rest.length
        for (let i = rest.length - 1; i >= 0; i--) {
            if (displayColumn(rest[i]) === move.column) { at = i + 1; break }
        }
    }
    return rest.slice(0, at).concat([moved]).concat(rest.slice(at))
}

function column(list, columnId) {
    return list.filter(c => displayColumn(c) === columnId)
}

// Pozycja dla MoveCard: miejsce wśród kart zapisanych w kolumnie docelowej (bez przenoszonej),
// przed kartą `before` (pusty — na koniec, -1). Karta z inną kolumną zapisaną (stan z koordynatora)
// nie liczy się — usługa porządkuje tylko pliki tej kolumny.
function moveIndex(snapshot, cardId, columnId, before) {
    const all = ((snapshot && snapshot.cards) || []).filter(c => c.column === columnId && c.id !== cardId)
    if (!before) return -1
    const shown = ((snapshot && snapshot.cards) || []).filter(c => displayColumn(c) === columnId && c.id !== cardId)
    const start = shown.findIndex(c => c.id === before)
    if (start < 0) return -1
    for (let i = start; i < shown.length; i++) {
        const at = all.findIndex(c => c.id === shown[i].id)
        if (at >= 0) return at
    }
    return -1
}

// Pozycja karty w jej kolumnie zapisanej (do przesuwania klawiaturą o jedno miejsce).
function storedIndex(snapshot, cardId) {
    const card = ((snapshot && snapshot.cards) || []).find(c => c.id === cardId)
    if (!card) return -1
    return snapshot.cards.filter(c => c.column === card.column).findIndex(c => c.id === cardId)
}

// Sąsiednia kolumna ręczna (Ctrl+←/→ na karcie): backlog ↔ todo ↔ done.
function neighbourColumn(columnId, step) {
    const at = MANUAL.indexOf(columnId)
    if (at < 0) return ""
    const next = at + step
    return next >= 0 && next < MANUAL.length ? MANUAL[next] : ""
}

// Gotowe: kilka ostatnich, reszta zwinięta („+N starszych”).
function doneShown(list, expanded) {
    return expanded ? list : list.slice(0, DONE_PREVIEW)
}

// --- liczby i teksty -------------------------------------------------------------------------

function counts(list) {
    const out = { total: 0, backlog: 0, todo: 0, progress: 0, waiting: 0, review: 0, done: 0, projects: 0 }
    const projects = {}
    for (const card of list || []) {
        const col = displayColumn(card)
        if (col === "archived") continue
        if (out[col] !== undefined) out[col]++
        if (col !== "done") {
            out.total++
            if (card.project) projects[card.project] = true
        }
    }
    out.projects = Object.keys(projects).length
    return out
}

// „11 kart · 2 w toku · 1 czeka na ciebie · 1 do sprawdzenia” — karty poza Gotowymi; części
// z koordynatora tylko niezerowe, a bez nich — ile czeka do zrobienia.
function summary(list) {
    const n = counts(list)
    const parts = [cards(n.total)]
    if (n.progress) parts.push(n.progress + " w toku")
    if (n.waiting) parts.push(n.waiting + " " + plural(n.waiting, "czeka", "czekają", "czeka") + " na ciebie")
    if (n.review) parts.push(n.review + " do sprawdzenia")
    if (!n.progress && !n.waiting && !n.review && n.todo) parts.push(n.todo + " do zrobienia")
    return parts.join(" · ")
}

// Plakietka widoku w pasku okna: ile czeka na ciebie (brzoskwinia), inaczej ile kart w grze.
// Ta sama reguła co Badge() w usłudze (board_monitor.py) i badge() w backend.py.
function badge(snapshot) {
    const n = counts((snapshot && snapshot.cards) || [])
    if (n.waiting) return { text: "● " + n.waiting, tone: "waiting" }
    return { text: n.total ? String(n.total) : "", tone: "muted" }
}

function progress(card) {
    const d = card && card.derived
    if (!d || !(d.total > 0)) return null
    const done = Math.max(0, Math.min(d.total, d.done || 0))
    return { done: done, total: d.total, ratio: done / d.total, text: done + " z " + d.total, short: done + "/" + d.total }
}

function toolName(tool) {
    return tool === "codex" ? "Codex" : tool === "claude" ? "Claude" : ""
}

// „Claude · koordynator” albo pusty napis bez stanu z koordynatora.
function who(card) {
    const d = card && card.derived
    if (!d) return ""
    const tool = toolName(d.tool)
    return tool ? tool + " · koordynator" : "koordynator"
}

// Kolor notatki: „✓ …” mięta (gotowa specyfikacja, review OK), pytanie czekające — brzoskwinia,
// notatka w kolejce (Do zrobienia) wyciszona, reszta text.secondary.
function noteTint(card, text) {
    const value = String(text || "")
    if (value.indexOf("✓") === 0) return "mint"
    const col = displayColumn(card)
    if (col === "waiting") return "peach"
    if (col === "todo" || col === "backlog") return "textMuted"
    return "textSecondary"
}

// Linia na karcie: notatka z koordynatora, inaczej notatka karty.
// Gotowa specyfikacja (`spec` karty, wejście „Tylko specyfikacja”) w Backlogu — „✓ specyfikacja gotowa”
// przed własną notatką karty, bo to wynik pracy agenta, na który czekasz.
function cardNote(card) {
    const d = card && card.derived
    if (d && d.note) return d.note
    if (card && card.spec && card.column === "backlog") return "✓ specyfikacja gotowa"
    return (card && card.note) || ""
}

// Kolor tagu: nazwa pastelu z migawki (tags[].color), gdy jej nie ma — nic (QML da text.muted).
function tagColor(snapshot, name) {
    const wanted = fold(name)
    const tag = ((snapshot && snapshot.tags) || []).find(t => fold(t.name) === wanted)
    return tag ? tag.color || "" : ""
}

function projectName(snapshot, slug) {
    if (!slug) return ""
    const project = ((snapshot && snapshot.projects) || []).find(p => p.slug === slug)
    return project ? project.name : slug
}

function projectBySlug(snapshot, slug) {
    return ((snapshot && snapshot.projects) || []).find(p => p.slug === slug) || null
}

// Tagi z projektu (dziedziczone) — w edycji karty pokazane jako stałe.
function inheritedTags(snapshot, slug) {
    const project = projectBySlug(snapshot, slug)
    return project ? project.tags || [] : []
}

function hasTag(list, name) {
    const wanted = fold(name)
    return (list || []).some(t => fold(t) === wanted)
}

function toggleTag(list, name) {
    return hasTag(list, name) ? list.filter(t => fold(t) !== fold(name)) : (list || []).concat([name])
}

// Ścieżka z „~” zamiast katalogu domowego.
function shortPath(path, home) {
    const value = String(path || "")
    if (home && (value === home || value.indexOf(home + "/") === 0)) return "~" + value.slice(home.length)
    return value
}

function two(n) {
    return (n < 10 ? "0" : "") + n
}

// „12:04” dziś, „wczoraj 18:20”, starsze „24.10 12:04”.
function when(ts, nowTs) {
    if (!ts) return ""
    const d = new Date(ts * 1000)
    const now = new Date((nowTs || Date.now() / 1000) * 1000)
    const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
    const days = Math.round((midnight(now) - midnight(d)) / 86400000)
    const time = two(d.getHours()) + ":" + two(d.getMinutes())
    if (days <= 0) return time
    if (days === 1) return "wczoraj " + time
    return d.getDate() + "." + two(d.getMonth() + 1) + " " + time
}

// Dopisek w ramce „Dane tablicy” na dole paska okna.
function railNote(snapshot, nowTs) {
    if (!snapshot) return ""
    if (!snapshot.exists) return "jeszcze nie założona — powstanie przy pierwszej karcie"
    const git = snapshot.git || {}
    if (git.busy) return "trwa git " + git.busy + " — zapis wstrzymany"
    if (git.pending) return "lokalne repo · " + git.pending + " " + plural(git.pending, "zmiana czeka", "zmiany czekają",
                                                                                  "zmian czeka") + " na commit"
    const last = snapshot.lastCommit
    return last && last.ts ? "lokalne repo · zapisano " + when(last.ts, nowTs) : "lokalne repo"
}

// Linie paska błędów plików (zły plik, trwający git revert): ścieżka względem tablicy i komunikat.
function errorLines(snapshot, limit) {
    const errors = (snapshot && snapshot.errors) || []
    const root = snapshot && snapshot.boardDir ? snapshot.boardDir + "/" : ""
    const max = limit || 3
    const lines = errors.slice(0, errors.length > max ? max - 1 : max).map(e => {
        const path = String(e.path || "")
        const rel = root && path.indexOf(root) === 0 ? path.slice(root.length) : ""
        return rel ? rel + ": " + e.message : String(e.message || "")
    })
    if (errors.length > max) lines.push("i " + (errors.length - max + 1) + " więcej — szczegóły: tide-daemon.py --print-board")
    return lines
}

// Komunikat z odpowiedzi usługi ({ok: false, error, message}) albo zapasowy po polsku.
function errorText(reply) {
    if (!reply) return "Usługa tide-daemon nie odpowiedziała — spróbuj jeszcze raz."
    if (reply.ok) return ""
    if (reply.message) return String(reply.message)
    switch (reply.error) {
    case "not-found": return "Nie ma już tej karty — tablica zmieniła się w międzyczasie."
    case "git-busy": return "W repozytorium tablicy trwa operacja gita — dokończ ją albo przerwij."
    case "broken-file": return "Pliku nie da się przeczytać — popraw go ręcznie."
    default: return "Nie udało się zapisać (" + (reply.error || "błąd") + ")."
    }
}

// Ostrzeżenie po zapisie: plik zmieniony, commit czeka (commitError).
function commitNote(reply) {
    return reply && reply.ok && reply.commitError ? "Zapisane w pliku, commit czeka: " + reply.commitError : ""
}

// Opcje wyboru projektu w karcie: projekty z tablicy, potem repozytoria, które jeszcze nimi nie są
// (wybranie takiego zakłada projekt przy zapisie karty — CreateCard z `repo`).
function projectOptions(snapshot, query) {
    const q = fold(query).trim()
    const ok = text => !q || fold(text).indexOf(q) >= 0
    const out = [{ kind: "none", key: "", name: "Bez projektu", detail: "" }]
    for (const p of (snapshot && snapshot.projects) || [])
        if (ok(p.name) || ok(p.slug)) out.push({ kind: "project", key: p.slug, name: p.name, detail: (p.tags || []).join(", ") })
    for (const r of (snapshot && snapshot.repos) || [])
        if (ok(r.name)) out.push({ kind: "repo", key: r.path, name: r.name, detail: "nowy projekt z repozytorium" })
    return out
}

// Żądanie CreateCard z formularza: {title, description, note, tags, project | repo, column}.
function createRequest(form) {
    const request = { title: String(form.title || "").trim(), description: form.description || "",
                      note: String(form.note || "").trim(), tags: form.tags || [] }
    if (form.projectKind === "repo") request.repo = form.project
    else if (form.project) request.project = form.project
    if (form.column) request.column = form.column
    if (form.youtrack) request.youtrack = form.youtrack
    return request
}

// Żądanie UpdateCard: tylko pola, które się zmieniły (pusty obiekt — nic do zapisania).
function updateRequest(card, form) {
    const out = {}
    const title = String(form.title || "").trim()
    if (title !== card.title) out.title = title
    if ((form.description || "") !== (card.description || "")) out.description = form.description || ""
    const note = String(form.note || "").trim()
    if (note !== (card.note || "")) out.note = note
    const tags = form.tags || []
    const old = card.tags || []
    if (tags.length !== old.length || tags.some((t, i) => fold(t) !== fold(old[i]))) out.tags = tags
    if (form.projectKind === "repo") out.repo = form.project
    else if ((form.project || "") !== (card.project || "")) out.project = form.project || ""
    if (form.youtrack !== undefined && (form.youtrack || "") !== (card.youtrack || "")) out.youtrack = form.youtrack || ""
    return out
}

// --- YouTrack (PT-7) ------------------------------------------------------------------------------

// Znaczek stanu synchronizacji karty ze zgłoszeniem: ✓ zsynchronizowane, ⟳ czeka na zapis albo YouTrack
// niedostępny, ⚠ konflikt albo odmowa YouTracka; bez synchronizacji — nic.
function syncGlyph(sync) {
    if (!sync) return ""
    switch (sync.status) {
    case "ok": return "✓"
    case "pending": return "⟳"
    case "offline": return "⟳"
    case "conflict": return "⚠"
    case "error": return "⚠"
    default: return ""
    }
}

// Ton znaczka (nazwa tokenu palety): konflikt — rose, odmowa — butter, w toku — sky, zgodne — mint.
function syncTone(sync) {
    if (!sync) return "textMuted"
    return { ok: "mint", pending: "sky", offline: "textMuted", conflict: "rose", error: "butter" }[sync.status] || "textMuted"
}

// Podpowiedź na znaczniku YouTracka: klucz, stan w YouTracku i stan synchronizacji słowami.
function syncHint(card) {
    const sync = card ? card.youtrackSync : null
    if (!sync) return card && card.youtrack ? "YouTrack " + card.youtrack : ""
    const parts = ["YouTrack " + (sync.issue || card.youtrack)]
    if (sync.text) parts.push(sync.text)
    else if (sync.issueState) parts.push("w YouTracku „" + sync.issueState + "”")
    if (sync.via === "ticket" && sync.label) parts.push("stan według: " + sync.label)
    return parts.join(" · ")
}

// Adres zgłoszenia do „Otwórz w YouTracku” — tylko http(s).
function youtrackUrl(card) {
    const url = card && card.youtrackSync ? String(card.youtrackSync.url || "") : ""
    return /^https?:\/\/\S+$/i.test(url) ? url : ""
}

// Klucz zgłoszenia z pola karty: „legal-35” albo adres …/issue/LEGAL-35 → „LEGAL-35”; pusty — brak; null — zły.
function issueKey(text) {
    let value = String(text || "").trim()
    if (!value) return ""
    const at = value.lastIndexOf("/issue/")
    if (at >= 0) value = value.slice(at + 7).split(/[/?#]/)[0]
    value = value.toUpperCase()
    return /^[A-Z][A-Z0-9_]{0,19}-[1-9][0-9]{0,8}$/.test(value) ? value : null
}

// Pasek okna: „YouTrack niedostępny”, „N nowych zgłoszeń w YouTracku”, konflikty — albo pusty.
function youtrackNotes(snapshot) {
    const yt = snapshot && snapshot.youtrack
    if (!yt) return []
    const out = []
    if (yt.conflicts > 0)
        out.push({ kind: "conflict", text: yt.conflicts + " " + plural(yt.conflicts, "konflikt", "konflikty", "konfliktów")
                                             + " z YouTrackiem" })
    if (yt.enabled && !yt.available)
        out.push({ kind: "offline", text: "YouTrack niedostępny" + (yt.pending > 0 ? " · " + yt.pending + " czeka" : "") })
    if (yt.newIssues > 0)
        out.push({ kind: "new", text: yt.newIssues + " " + plural(yt.newIssues, "nowe zgłoszenie", "nowe zgłoszenia",
                                                                  "nowych zgłoszeń") + " w YouTracku" })
    return out
}

// Linia stanu w sekcji YouTrack strony „Projekty i tagi”.
function youtrackSummary(yt, projects) {
    if (!yt) return ""
    const synced = (projects || []).filter(p => p.youtrack && p.youtrackSync).map(p => p.youtrack + " → " + p.name)
    if (!yt.sync) return "Synchronizacja wyłączona w ustawieniach (youtrack.json: \"sync\": false)."
    if (!synced.length) return "Żaden projekt nie ma klucza YouTracka — ustaw go w projekcie (np. LOG), żeby stan kart szedł do YouTracka."
    const states = yt.states || {}
    const map = ["Do zrobienia → " + (states.open || "Open"), "W toku → " + (states.progress || "In Progress"),
                 "Czeka → " + (states.waiting || "To be discussed"), "Do sprawdzenia → " + (states.review || "Fixed"),
                 "Gotowe → " + (states.done || "Verified")].join(", ")
    const parts = ["Synchronizacja: " + synced.join(", ") + "."]
    if (!yt.available) parts.push("YouTrack niedostępny" + (yt.error ? " (" + yt.error + ")" : "") + " — zmiany czekają.")
    else if (yt.pending > 0) parts.push(yt.pending + " " + plural(yt.pending, "zmiana czeka", "zmiany czekają", "zmian czeka") + " na zapis.")
    parts.push(map + ".")
    return parts.join(" ")
}

// Lista do importu: filtr po kluczu, tytule i projekcie (oraz wybrany projekt YouTracka); zgłoszenia z kartą na końcu.
function importRows(issues, query, project) {
    const q = fold(String(query || "").trim())
    const rows = (issues || []).filter(i => (!project || i.project === project)
                                            && (!q || fold([i.id, i.summary, i.project, i.boardProjectName].join(" ")).indexOf(q) >= 0))
    return rows.slice().sort((a, b) => (a.card ? 1 : 0) - (b.card ? 1 : 0))
}

// Przełącznik projektów nad listą importu: „Wszystkie” i projekty YouTracka z liczbą zgłoszeń.
function importProjects(issues) {
    const counts = {}
    for (const issue of issues || []) counts[issue.project || "?"] = (counts[issue.project || "?"] || 0) + 1
    return [{ value: "", label: "Wszystkie" }].concat(Object.keys(counts).sort()
        .map(key => ({ value: key, label: key + " · " + counts[key] })))
}

function columnLabel(snapshot, id) {
    const found = columns(snapshot).find(c => c.id === id)
    return found ? found.label : id === "archived" ? "Archiwum" : id
}

// --- „Co ma zrobić agent?” (uruchomienie z karty, PT-4) ------------------------------------------

// Wejścia („Jak zacząć?”): sposób rozpoczęcia zlecenia. Kolejne (Mam plan, Od razu) dochodzą tu jako nowe pozycje;
// `modes: true` — wejście ma wybór trybu (Pełny, Lekki), a bez niego kroki są własne wejścia (`steps`).
var LAUNCH_ENTRIES = [
    { id: "planning", title: "Planowanie", desc: "Agent planuje pracę: specyfikacja, tickety w planie, potem implementacja i review.",
      meta: "wybierasz tryb: Pełny albo Lekki", modes: true },
    { id: "plan", title: "Mam plan", desc: "Przejmij istniejący plan projektu, sprawdź go i kontynuuj niedokończone tickety.",
      meta: "wybierz plan z projektu lub tablicy", modes: false, flow: true,
      steps: ["Przejęcie planu", "Uzupełnienie i review", "Zatwierdzenie", "Tickety w tle"] },
    { id: "spec", title: "Tylko specyfikacja", desc: "Agent dopytuje i pisze specyfikację. Bez kodu i bez worktree.",
      meta: "karta wraca do Backlogu", modes: false, steps: ["Pytania", "Specyfikacja", "„specyfikacja gotowa” na karcie"] },
    // Przepływ karty (PT-58): jeden ticket w tle od razu, zlecenie przez /Board StartCard, bez Konsoli.
    { id: "now", title: "Od razu", desc: "Jeden ticket w tle, bez planowania i bez terminala. Wynik na gałęzi karty.",
      meta: "wybierasz wykonawcę i recenzenta", modes: false, flow: true,
      steps: ["Ticket w tle", "Review", "Gałąź karty", "Do sprawdzenia"] }
]

// Wykonawcy kroku „Od razu” z katalogu Status() /Models. Zanim przyjdzie stan usługi, pokazujemy
// domyślną grupę Anthropic; po odczycie wybór może obejmować Codex, lokalny i innych dostawców.
var NOW_EXECUTORS = ["Haiku", "Sonnet", "Opus"]
var NOW_DEFAULT = "Sonnet"

function launchExecutorList(status) {
    if (status && Array.isArray(status.executors)) return status.executors
    if (status && status.executors && Array.isArray(status.executors.list)) return status.executors.list
    return NOW_EXECUTORS.map(name => ({ name: name, program: "claude", provider: "anthropic", model: name.toLowerCase() }))
}

function preferredProviders(status) {
    const known = status && status.providers && typeof status.providers === "object" ? status.providers : ({})
    const base = [
        { value: "anthropic", label: "Anthropic" }, { value: "openai", label: "OpenAI" },
        { value: "local", label: "Tylko lokalnie" }, { value: "mixed", label: "Mieszany" }
    ]
    return base.concat(Object.keys(known).filter(name => !["anthropic", "openai", "local", "mixed"].includes(name))
        .map(name => ({ value: name, label: String(known[name].label || name) })))
}

function launchExecutors(status, provider) {
    const wanted = provider || "anthropic"
    return launchExecutorList(status).filter(e => e && e.name && e.program && e.model &&
        (wanted === "mixed" || e.provider === wanted))
        .map(e => ({ value: String(e.name), label: String(e.name), disabled: e.available === false,
                      hint: e.error || "" }))
}

function planningExecutors(status, provider) {
    return launchExecutors(status, provider === "local" ? "local" : "mixed")
        .filter(option => provider === "local" || launchExecutor(status, option.value).provider !== "local")
}

function retryExecutors(status, flow) {
    if (!flow || !flow.provider) return []
    if (flow.stepKind === "planning" || flow.stepKind === "plan-fix")
        return planningExecutors(status, flow.provider === "local" ? "local" : "mixed")
            .filter(option => option.value !== flow.executor && !option.disabled)
    const review = flow.stepKind === "review"
    const otherModel = String(review ? flow.authorModel || "" : flow.reviewerModel || "").toLowerCase()
    const otherProvider = String(review ? flow.authorProvider || "" : flow.reviewerProvider || "")
    const mixed = flow.preferredProvider === "mixed"
    return launchExecutorList(status).filter(e => e && e.name && e.program && e.model &&
        e.available !== false && e.name !== flow.executor &&
        String(e.model).toLowerCase() !== otherModel &&
        (e.provider === "local") === (otherProvider === "local") &&
        (mixed ? e.provider !== otherProvider : e.provider === otherProvider))
        .map(e => ({ value: String(e.name), label: String(e.name) }))
}

function launchExecutor(status, name) {
    return launchExecutorList(status).find(e => e && e.name === name) || null
}

function defaultExecutor(options) {
    const names = (options || []).map(o => o.value)
    return names.indexOf(NOW_DEFAULT) >= 0 ? NOW_DEFAULT : (names[0] || "")
}

function reviewExecutors(status, author, provider) {
    const current = launchExecutor(status, author)
    if (!current) return []
    return launchExecutorList(status).filter(e => e && e.name && e.model && e.program &&
        e.name !== author && e.model.toLowerCase() !== String(current.model || "").toLowerCase() &&
        (current.provider === "local" ? e.provider === "local" : e.provider !== "local") &&
        (provider === "mixed" ? e.provider !== current.provider : e.provider === current.provider))
        .map(e => ({ value: String(e.name), label: String(e.name), disabled: e.available === false,
                     hint: e.error || "" }))
}

function defaultReviewer(author, options) {
    const names = (options || []).map(o => o.value)
    const preferred = author === "Opus" ? "Sonnet" : author === "Codex mocny" ? "Codex Astra" : "Opus"
    return names.indexOf(preferred) >= 0 ? preferred : (names[0] || "")
}

function isFlowEntry(id, mode) {
    return launchEntry(id).flow === true || id === "planning"
}

// Prośba /Board StartCard z okna („Od razu”): wejście, wykonawca i zrzuty z katalogu zadań.
function startCardRequest(state, images) {
    const request = { entry: state.entry === "plan" ? "import" : state.entry, executor: state.executor,
                      check: state.check, images: (images || []).slice() }
    if (state.entry === "planning") request.mode = state.mode
    if (state.entry === "plan") request.planPath = state.planPath
    if (state.reviewer && state.entry === "now") request.reviewer = state.reviewer
    if (state.preferredProvider) request.preferredProvider = state.preferredProvider
    return request
}

function planCandidates(card) {
    const rows = card && Array.isArray(card.planCandidates) ? card.planCandidates.slice() : []
    return rows.sort((a, b) => Number(b.card === card.id) - Number(a.card === card.id)
        || Number(b.updatedTs || 0) - Number(a.updatedTs || 0))
}

// Tryby Planowania z mocka: tytuł, opis, uwaga pod opisem i kroki paska pod kartami trybów.
var LAUNCH_MODES = [
    { id: "full", title: "Pełny", desc: "Rozmowa w Konsoli: grilling z dokumentacją, specyfikacja i podział na tickety. Potem review i zatwierdzenie planu.",
      meta: "na start zada ci pytania", steps: ["Grilling", "Specyfikacja", "Tickety w planie", "Review planu", "Zatwierdzenie planu"] },
    { id: "light", title: "Lekki", desc: "Specyfikacja i pionowe tickety w tle; plan czeka na Twoje zatwierdzenie.",
      meta: "bez pytań i bez Konsoli", steps: ["Specyfikacja", "Tickety w planie", "Zatwierdzenie planu"] }
]
var LAUNCH_TOOLS = [{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]

function launchMode(id) {
    return LAUNCH_MODES.find(m => m.id === id) || LAUNCH_MODES[0]
}

function launchEntry(id) {
    return LAUNCH_ENTRIES.find(e => e.id === id) || LAUNCH_ENTRIES[0]
}

// Czy wejście ma wybór trybu (dziś tylko Planowanie).
function entryHasModes(id) {
    return launchEntry(id).modes === true
}

// Kroki paska pod wyborem: własne wejścia albo kroki wybranego trybu Planowania.
function launchSteps(entry, mode) {
    const e = launchEntry(entry)
    return e.modes ? launchMode(mode).steps : e.steps
}

// Wejście i tryb z projektu przy otwarciu okna. Dawne `mode: spec` (stary zapis albo usługa sprzed PT-77) to wejście
// Tylko specyfikacja; tryb zawsze Pełny albo Lekki — zapamiętany także wtedy, gdy projekt ma wejście spec.
function launchDefaults(project) {
    const p = project || {}
    const legacySpec = p.mode === "spec"
    const mode = p.mode === "light" ? "light" : "full"
    const entry = p.entry && LAUNCH_ENTRIES.some(e => e.id === p.entry) ? p.entry : (legacySpec ? "spec" : "planning")
    return { entry: entry, mode: mode }
}

function launchToolName(tool) {
    return tool === "codex" ? "Codex" : "Claude Code"
}

// Projekt karty, w którym agent może pracować: z repozytorium, które istnieje. null — trzeba wybrać.
function launchProject(snapshot, card) {
    const project = card && card.project ? projectBySlug(snapshot, card.project) : null
    return project && project.repo && !project.repoMissing ? project : null
}

// Dlaczego nie da się uruchomić (pusty napis — można).
function launchBlocker(snapshot, card) {
    if (!card) return "Nie ma już tej karty."
    const project = card.project ? projectBySlug(snapshot, card.project) : null
    if (!project) return "Karta nie ma projektu — agent potrzebuje repozytorium, w którym ma pracować. Wybierz projekt albo repozytorium."
    if (!project.repo) return "Projekt „" + project.name + "” nie ma repozytorium — agent potrzebuje katalogu z kodem. Wybierz projekt z repozytorium."
    if (project.repoMissing) return "Repozytorium projektu „" + project.name + "” nie istnieje (" + project.repo + "). Wybierz inny projekt albo popraw go w ustawieniach."
    return ""
}

// Proponowana gałąź: zapisana w karcie albo `<narzędzie>/k-12-<opis>` (branchSlug z migawki).
function launchBranch(card, tool) {
    if (card && card.branch) return card.branch
    return (tool || "claude") + "/" + ((card && card.branchSlug) || (card ? String(card.id).toLowerCase() : "zadanie"))
}

// Zmiana narzędzia podmienia jego prefiks w gałęzi (claude/… ↔ codex/…), resztę zostawia.
function retoolBranch(branch, tool) {
    const match = /^(claude|codex)\//.exec(branch || "")
    return match ? tool + "/" + branch.slice(match[0].length) : branch
}

// „w repozytorium · docs/agents/” albo „w Tide · tide-board/docs/<projekt>/”.
function docsLabel(project) {
    if (!project) return ""
    if (project.aiDocs === "repo") return "w repozytorium · docs/agents/"
    const parts = String(project.docsDir || "").split("/").filter(x => x)
    const tail = parts.slice(-3).join("/")
    return "w Tide · " + (tail || "docs/" + project.slug) + "/"
}

// Katalog worktree jak w usłudze (tasks/core.py: worktree_path): `<repo>-worktrees/<nazwa>`, gdy taki
// katalog już jest — tego okno nie wie, więc mówimy ogólnie o konwencji repozytorium.
function worktreeNote(state) {
    if (state.entry === "spec") return "Specyfikacja nie potrzebuje worktree: powstaje w " + specDir(state) + " w katalogu projektu."
    if (!state.newWorktree) return "Agent pracuje w głównym katalogu repozytorium, na tym, co tam jest teraz."
    const name = String(state.branch || "").replace(/^(claude|codex)\//, "")
    return "Od lokalnej gałęzi głównej, katalog " + name + " obok repozytorium (konwencja projektu). Gałąź główna zostaje nietknięta."
}

function specDir(state) {
    return state.aiDocs === "repo" ? "docs/agents/specs/" : "tide-board/docs/" + (state.slug || "…") + "/specs/"
}

// Zdanie „co się stanie” w stopce okna (z modelem lokalnym: „Codex na qwen3-coder:30b”).
function launchFooter(state) {
    if (state.entry === "plan")
        return "Przejmę wybrany plan bez zmieniania go w projekcie. Brakujące przydziały i kartki uzupełni krok w tle, potem plan przejdzie niezależny przegląd i poczeka na zatwierdzenie."
    if (state.entry === "now")
        return "Krok w tle bez Konsoli: " + (state.executor || "wykonawca") + " robi jeden ticket we własnym worktree "
            + "odgałęzionym od gałęzi karty. Potem ticket trafia na gałąź karty, a karta do „Do sprawdzenia”."
    const tool = launchToolName(state.tool) + (state.localModel ? " na " + state.localModel : "")
    if (state.entry === "spec")
        return tool + " w katalogu projektu, bez worktree: dopyta i zapisze specyfikację w " + specDir(state)
            + ". Potem karta wróci do Backlogu ze znacznikiem „specyfikacja gotowa”."
    const room = state.newWorktree ? "praca w nowym worktree" : "praca w głównym katalogu"
    if (state.mode === "light")
        return "Planowanie w tle bez Konsoli: " + (state.executor || "planujący")
            + " zapisze specyfikację i tickety. Plan poczeka w „Czeka na mnie” na Twoje zatwierdzenie."
            + ((state.extras || []).length ? " Dodatkowe projekty do odczytu: " + state.extras.map(p => p.name).join(", ") + "." : "")
    return "Planowanie Pełne w jednej karcie Konsoli: " + (state.executor || "planujący")
        + " dopyta, zapisze specyfikację i pokaże podział na tickety. Po review plan poczeka w „Czeka na mnie” na Twoje zatwierdzenie."
        + ((state.extras || []).length ? " Dodatkowe projekty jako kontekst: " + state.extras.map(p => p.name).join(", ") + "." : "")
}

// Zdanie stopki o dodatkowych projektach (PT-28): „ Pracuje też w mobileapp, infra (gałąź … w każdym, które zmieni).”
function extrasNote(state) {
    const names = (state.extras || []).map(p => p.name)
    if (!names.length) return ""
    const branch = state.newWorktree ? state.branch : "bieżąca gałąź"
    return " Pracuje też w: " + names.join(", ") + " (gałąź " + branch + " w każdym, które zmieni)."
}

// Prośba /Tasks Start z polecenia RenderLaunch: worktree tylko poza wejściem Tylko specyfikacja, katalogi dla --add-dir
// z RenderLaunch (`addDirs`: dokumenty w tablicy przy ai-docs: tide i repozytoria dodatkowych projektów,
// PT-28); usługa sprzed PT-28 — sam katalog dokumentów. Zrzuty ekranu i model jak z okna „Nowe zadanie”
// (PT-43): `images` — pliki z katalogu zadań (imagesDir), `model` — cloud albo local (przy local usługa bierze
// program z agentProgram w models.json; `tool` z okna to ten sam program).
function launchStartRequest(flow, state) {
    const worktree = state.entry !== "spec" && state.newWorktree
    const dirs = Array.isArray(flow.addDirs) ? flow.addDirs.slice()
               : (flow.aiDocs === "tide" && flow.docsDir ? [flow.docsDir] : [])
    return { repoPath: flow.repo, tool: flow.tool || state.tool, prompt: flow.text, newWorktree: worktree,
             branch: worktree ? (flow.branch || state.branch) : "", images: (state.images || []).slice(), addDirs: dirs,
             model: state.model === "local" ? "local" : "cloud" }
}

// --- dodatkowe projekty karty (PT-28) -------------------------------------------------------------

// Dodatkowe projekty zapisane w karcie jako pozycje okna: [{kind: "project", key: slug, name}].
function cardExtras(snapshot, card) {
    const out = []
    for (const slug of (card && card.projects) || []) {
        const project = projectBySlug(snapshot, slug)
        out.push({ kind: "project", key: slug, name: project ? project.name : slug })
    }
    return out
}

// Do wyboru jako dodatkowy projekt: projekty tablicy z istniejącym repozytorium (bez głównego i już
// wybranych), potem repozytoria z katalogu projektów, które nie są jeszcze projektami (wybranie założy
// projekt — UpdateCard {projects: [ścieżka]}, jak `repo` w CreateCard).
function extraOptions(snapshot, card, picked, query) {
    const q = fold(query).trim()
    const ok = text => !q || fold(text).indexOf(q) >= 0
    const taken = (picked || []).map(p => p.key)
    const primary = card ? card.project || "" : ""
    const out = []
    for (const p of (snapshot && snapshot.projects) || [])
        if (p.repo && !p.repoMissing && p.slug !== primary && taken.indexOf(p.slug) < 0 && (ok(p.name) || ok(p.slug)))
            out.push({ kind: "project", key: p.slug, name: p.name, detail: pathTail(p.repo) })
    for (const r of (snapshot && snapshot.repos) || [])
        if (taken.indexOf(r.path) < 0 && ok(r.name))
            out.push({ kind: "repo", key: r.path, name: r.name, detail: "nowy projekt z repozytorium" })
    return out
}

function addExtra(picked, kind, key, name) {
    const list = (picked || []).slice()
    if (!key || list.some(p => p.key === key) || list.length >= EXTRA_MAX) return list
    list.push({ kind: kind || "project", key: key, name: name || key })
    return list
}

function removeExtra(picked, key) {
    return (picked || []).filter(p => p.key !== key)
}

// Czy wybór w oknie różni się od `projects` karty (wtedy UpdateCard {projects} przed uruchomieniem).
function extrasChanged(card, picked) {
    const saved = ((card && card.projects) || []).join("\n")
    return (picked || []).map(p => p.key).join("\n") !== saved
}

var EXTRA_MAX = 5

// „…/DevWorkspace/mobileapp” — ostatnie dwa człony ścieżki repozytorium (lista w oknie jest wąska).
function pathTail(path) {
    const text = String(path || "")
    const parts = text.split("/").filter(x => x)
    return parts.length > 2 ? "…/" + parts.slice(-2).join("/") : text
}

// UpdateCard po udanym starcie: gałąź i worktree (tylko nowy worktree), wejście i tryb (przy Tylko specyfikacja pusty), narzędzie, sprawdzenie, chwila,
// a `session` pusty — ponowne uruchomienie odpina poprzednią sesję i usługa przypnie nową (PT-28).
function launchMarkRequest(result, state, nowIso) {
    return { branch: result.worktree ? result.branch || "" : "", worktree: result.worktree || "", entry: state.entry || "planning",
             mode: state.entry === "spec" ? "" : state.mode, tool: result.tool || state.tool, check: state.check === "manual" ? "manual" : "auto", launched: nowIso,
             session: "" }
}

// Czas ISO 8601 z przesunięciem strefy (2026-10-27T12:04:00+01:00) — jak w plikach tablicy.
function isoNow(ms) {
    const d = new Date(ms)
    const offset = -d.getTimezoneOffset()
    const sign = offset >= 0 ? "+" : "-"
    const abs = Math.abs(offset)
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + "T" + two(d.getHours()) + ":"
        + two(d.getMinutes()) + ":" + two(d.getSeconds()) + sign + two(Math.floor(abs / 60)) + ":" + two(abs % 60)
}
