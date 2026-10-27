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

// Karta w kolumnie ręcznej (bez stanu z koordynatora) albo do potwierdzenia — tylko taką da się przeciągać.
function draggable(card) {
    return !!card && ((isManual(displayColumn(card)) && isManual(card.column)) || confirmable(card))
}

// Dokąd wolno przenieść kartę: z kolumn ręcznych do każdej ręcznej, kartę do potwierdzenia — tylko do
// Gotowych albo z powrotem do Do zrobienia. Do kolumn wyliczanych nigdy.
function dropTargets(card) {
    if (confirmable(card)) return ["done", "todo"]
    return draggable(card) ? MANUAL.slice() : []
}

// Cel „Otwórz w Sesjach” / „Przejdź do agenta”: koordynator karty (/Coordination), inaczej jej sesja (/Agents).
function agentTarget(card) {
    const d = card && card.derived
    return d ? String(d.coordinator || d.session || "") : ""
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
// Gotowa specyfikacja (`spec` karty, tryb „tylko spec”) w Backlogu — „✓ specyfikacja gotowa”
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
    return out
}

function columnLabel(snapshot, id) {
    const found = columns(snapshot).find(c => c.id === id)
    return found ? found.label : id === "archived" ? "Archiwum" : id
}

// --- „Co ma zrobić agent?” (uruchomienie z karty, PT-4) ------------------------------------------

// Tryby z mocka: tytuł, opis, uwaga pod opisem i kroki paska pod kartami trybów.
var LAUNCH_MODES = [
    { id: "full", title: "Pełny flow", desc: "Grilling, specyfikacja, tickety w planie, implementacja przez TDD i review.",
      meta: "na start zada ci pytania", steps: ["Grilling", "Specyfikacja", "Tickety w planie", "TDD", "Review"] },
    { id: "light", title: "Lekki", desc: "Krótka specyfikacja i 1–3 tickety w planie, potem od razu praca.",
      meta: "pyta tylko przy wątpliwościach", steps: ["Krótka specyfikacja", "1–3 tickety w planie", "Praca", "Review"] },
    { id: "spec", title: "Tylko specyfikacja", desc: "Agent dopytuje i pisze specyfikację. Bez kodu i bez worktree.",
      meta: "karta wraca do Backlogu", steps: ["Pytania", "Specyfikacja", "„specyfikacja gotowa” na karcie"] }
]
var LAUNCH_TOOLS = [{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]

function launchMode(id) {
    return LAUNCH_MODES.find(m => m.id === id) || LAUNCH_MODES[0]
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
    if (state.mode === "spec") return "Specyfikacja nie potrzebuje worktree: powstaje w " + specDir(state) + " w katalogu projektu."
    if (!state.newWorktree) return "Agent pracuje w głównym katalogu repozytorium, na tym, co tam jest teraz."
    const name = String(state.branch || "").replace(/^(claude|codex)\//, "")
    return "Od lokalnej gałęzi głównej, katalog " + name + " obok repozytorium (konwencja projektu). Gałąź główna zostaje nietknięta."
}

function specDir(state) {
    return state.aiDocs === "repo" ? "docs/agents/specs/" : "tide-board/docs/" + (state.slug || "…") + "/specs/"
}

// Zdanie „co się stanie” w stopce okna.
function launchFooter(state) {
    const tool = launchToolName(state.tool)
    const where = state.repoName ? "„" + state.repoName + " · " + (state.newWorktree ? state.branch : "bieżąca gałąź") + "”" : ""
    const check = state.check === "manual" ? " Na koniec zostawi listę „Co przetestować ręcznie i jak” i poczeka na twój test." : ""
    if (state.mode === "spec")
        return tool + " w katalogu projektu, bez worktree: dopyta i zapisze specyfikację w " + specDir(state)
            + ". Potem karta wróci do Backlogu ze znacznikiem „specyfikacja gotowa”."
    const room = state.newWorktree ? "praca w nowym worktree" : "praca w głównym katalogu"
    if (state.mode === "light")
        return "Nowa karta Konsoli " + where + ", " + tool + " z szablonem „Lekki”: krótka specyfikacja i plan z 1–3 ticketami, potem "
            + room + "." + check + " Karta przejdzie do „W toku”, gdy agent ruszy."
    return "Nowa karta Konsoli " + where + ", " + tool + " z szablonem „Pełny flow”. Agent najpierw zada ci pytania, potem założy plan z kluczem card: "
        + state.cardId + "." + check + " Karta przejdzie do „W toku”, gdy agent ruszy."
}

// Prośba /Tasks Start z polecenia RenderLaunch: worktree tylko poza trybem spec, katalog dokumentów
// w tablicy przez addDirs (ai-docs: tide).
function launchStartRequest(flow, state) {
    const worktree = state.mode !== "spec" && state.newWorktree
    return { repoPath: flow.repo, tool: flow.tool || state.tool, prompt: flow.text, newWorktree: worktree,
             branch: worktree ? (flow.branch || state.branch) : "", images: [],
             addDirs: flow.aiDocs === "tide" && flow.docsDir ? [flow.docsDir] : [] }
}

// UpdateCard po udanym starcie: gałąź i worktree (tylko nowy worktree), tryb, narzędzie, sprawdzenie, chwila.
function launchMarkRequest(result, state, nowIso) {
    return { branch: result.worktree ? result.branch || "" : "", worktree: result.worktree || "", mode: state.mode,
             tool: result.tool || state.tool, check: state.check === "manual" ? "manual" : "auto", launched: nowIso }
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
