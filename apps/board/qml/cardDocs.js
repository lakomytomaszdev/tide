// Logika zakładek „Plan” i „Specyfikacja” panelu karty bez QML. Dane to odpowiedź /Board CardDocs(id)
// z tide-daemon ({ok, card, plan, spec, others, warnings}; docs/tide-daemon.md, „Plan i specyfikacja na
// karcie”). Kolory są nazwami tokenów palety (Tokens.qml), jak w oknie Sesje. Funkcje są czyste —
// testy liczą je w QJSEngine (apps/board/tests).
.pragma library

// Stan ticketu słowem i kolorem — te same znaczenia co na tablicy Sesji (teal pracuje, brzoskwinia
// czeka, lawenda review, mięta gotowe).
var TICKET_STATES = {
    "todo": { word: "do zrobienia", tint: "textMuted" },
    "w-toku": { word: "w toku", tint: "teal" },
    "czeka": { word: "czeka", tint: "peach" },
    "review": { word: "review", tint: "lavender" },
    "gotowe": { word: "gotowe", tint: "mint" },
    "odrzucone": { word: "odrzucone", tint: "rose" }
}

function ticketState(state) {
    return TICKET_STATES[state] || { word: state ? String(state) : "bez stanu", tint: "textMuted" }
}

// Status planu (active, paused, done) i specyfikacji (szkic, gotowa) jako pastylka.
function planStatus(status) {
    if (status === "done") return { word: "zakończony", tint: "mint" }
    if (status === "paused") return { word: "wstrzymany", tint: "butter" }
    return { word: "aktywny", tint: "teal" }
}

function specStatus(spec) {
    if (!spec) return { word: "", tint: "textMuted" }
    if (spec.ready) return { word: "gotowa", tint: "mint" }
    const status = String(spec.status || "")
    if (status === "szkic" || status === "draft" || status === "wip") return { word: "szkic", tint: "butter" }
    return { word: status || "bez statusu", tint: "textMuted" }
}

// Postęp planu: gotowe / wszystkie bez odrzuconych („2/5”); bez ticketów — pusty.
function progress(plan) {
    const rows = (plan && plan.tickets) || []
    const counted = rows.filter(t => t.state !== "odrzucone")
    if (counted.length === 0) return ""
    return counted.filter(t => t.state === "gotowe").length + "/" + counted.length
}

// Zakładki panelu: Opis zawsze, Plan i Specyfikacja tylko wtedy, gdy są.
function tabs(docs) {
    const out = [{ value: "opis", label: "Opis" }]
    if (docs && docs.plan) {
        const p = progress(docs.plan)
        out.push({ value: "plan", label: p ? "Plan · " + p : "Plan" })
    }
    if (docs && docs.spec) out.push({ value: "spec", label: "Specyfikacja" })
    return out
}

// Zakładka do pokazania: wybrana, o ile jest; inaczej Opis.
function effectiveTab(docs, tab) {
    if (tab === "plan" && docs && docs.plan) return "plan"
    if (tab === "spec" && docs && docs.spec) return "spec"
    return "opis"
}

// Chwila zmiany dokumentu (sekundy): `updated` z frontmattera, inaczej czas pliku.
function docTime(doc) {
    if (!doc) return 0
    if (doc.updatedTs) return doc.updatedTs
    const parsed = doc.updated ? Date.parse(doc.updated) : NaN
    return isNaN(parsed) ? (doc.mtime || 0) : parsed / 1000
}

// Skąd dokument: „plan karty” (klucz card), „plan koordynatora karty”, „z pola spec karty”, „po ID karty”.
function source(doc, kind) {
    if (!doc) return ""
    const where = doc.where === "board" ? "w tablicy" : doc.where === "worktree" ? "w worktree" : "w repozytorium"
    let how = ""
    if (kind === "plan") how = doc.link === "coordinator" ? "plan koordynatora karty" : "plan karty"
    else how = doc.link === "field" ? "z pola spec karty" : "znaleziona po ID karty"
    return how + " · " + where
}

// Ticket poza kartą: koordynator prowadzi kilka kart, a ta obejmuje tylko część jego ticketów.
function outsideCard(plan, ticket) {
    const scope = (plan && plan.cardTickets) || []
    return scope.length > 0 && scope.indexOf(ticket.id) < 0
}

// Link z Markdownu: otwieramy wyłącznie http(s) — inne schematy (file:, javascript:, ścieżki) pomijamy.
function safeLink(url) {
    const value = String(url || "").trim()
    return /^https?:\/\/[^\s]+$/i.test(value) ? value : ""
}

function kindWord(kind) {
    return kind === "plan" ? "plan" : "specyfikacja"
}
