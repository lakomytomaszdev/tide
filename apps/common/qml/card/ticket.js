// Prezentacja ticketu karty (TicketTile.qml, PT-332): podpis stanu. Stan i jego etykietę liczy usługa
// (`derived.tickets[].status`, `statusLabel`); biblioteka podmienia tylko etykietę stanu `merged`, żeby kafelek nie
// sugerował scalenia karty do gałęzi głównej (K-70 pkt 8). Pozostałe teksty zostają z usługi.
.pragma library

var MERGED_LABEL = "scalony do gałęzi karty"

// Podpis stanu na kafelku: dla `merged` „scalony do gałęzi karty”, dla reszty etykieta z usługi.
function statusLabel(ticket) {
    if (!ticket) return ""
    if (ticket.status === "merged") return MERGED_LABEL
    return ticket.statusLabel || ""
}

var NOW_LABELS = {queued: "w kolejce", running: "pracuje", review: "review", fix: "poprawki", you: "czeka na Ciebie",
                  waiting: "czeka", merged: MERGED_LABEL}
var WORK_KINDS = ["ticket", "review", "fix", "conflict", "interactive"]

// Kafelek ticketu karty „Od razu” (`flow.entry === "now"`, bez `derived.tickets`): wyłącznie z pól migawki.
// Zwraca pozycję w kształcie `derived.tickets[]` (id, title, status, statusLabel, executor, reviewer) albo null dla
// karty bez przepływu, bez numeru ticketu albo z planem (wtedy kafelki idą z `derived.tickets`). Recenzent
// pochodzi tylko z kroku review (migawka nie ma `flow.reviewer`); dopóki review nie ruszył, jest pusty.
// `answeredIds` — decyzje już odpowiedziane w widoku, których nie liczymy.
function tileModel(card, answeredIds) {
    const derived = card && card.derived ? card.derived : null
    const flow = derived && derived.flow ? derived.flow : null
    // „ticket” to zastępczy napis usługi, gdy karta nie ma jeszcze numeru ticketu.
    if (!flow || !flow.ticket || flow.ticket === "ticket" || (Array.isArray(derived.tickets) && derived.tickets.length > 0)) return null
    const id = flow.ticket
    const answered = answeredIds || []
    const steps = (Array.isArray(flow.steps) ? flow.steps : []).filter(
        s => !s.historical && (!s.ticket || s.ticket === id))
    const last = list => list.length > 0 ? list[list.length - 1] : null
    const ticketStep = last(steps.filter(s => s.kind === "ticket"))
    const reviewStep = last(steps.filter(s => s.kind === "review"))
    const open = last(steps.filter(s => !s.result && WORK_KINDS.indexOf(s.kind) >= 0))
    const phases = Array.isArray(derived.phases) ? derived.phases : []
    const ticketsDone = phases.some(p => p.id === "tickets" && p.state === "done")
    const asked = (Array.isArray(flow.decisions) ? flow.decisions : []).some(
        d => d.ticket === id && answered.indexOf(d.id) < 0)

    let status
    if (flow.closed || flow.merged || ticketsDone) status = "merged"
    else if (asked) status = "you"
    else if (open) status = open.kind === "review" ? "review" : open.kind === "fix" ? "fix" : "running"
    else status = steps.length === 0 ? "queued" : "waiting"
    return {id: id, ticket: id, title: card.title || "", status: status, statusLabel: NOW_LABELS[status],
            executor: ticketStep && ticketStep.executor || flow.executor || "",
            reviewer: reviewStep && reviewStep.executor || ""}
}

// --- tabela planu (PT-503, K-128): wybór bieżącej fali i wiersze ---------------------------------------------
// Czyste funkcje, bez dostępu do QML: widok tylko je woła, a test tabelaryczny sprawdza je bez okna.

var FINISHED_STATES = ["gotowe", "odrzucone"]
var FINISHED_STATUSES = ["merged", "rejected"]

// Ticket zakończony: `state` z usługi (`gotowe`, `odrzucone`), a bez niego `status` (`merged`, `rejected`).
function isFinished(ticket) {
    if (!ticket) return false
    if (ticket.state) return FINISHED_STATES.indexOf(ticket.state) >= 0
    return FINISHED_STATUSES.indexOf(ticket.status) >= 0
}

// Indeks (od 0) najniższej fali z ticketem w stanie innym niż gotowe/odrzucone; -1, gdy wszystkie fale są
// zakończone albo planu nie ma. Ticket spoza karty (brak w `tickets`) nie czyni fali bieżącą.
function currentWave(waves, tickets) {
    const list = Array.isArray(tickets) ? tickets : []
    return (Array.isArray(waves) ? waves : []).findIndex(
        wave => (Array.isArray(wave) ? wave : []).some(id => {
            const ticket = list.find(t => t.id === id)
            return !!ticket && !isFinished(ticket)
        }))
}

// Wiersze tabeli planu w kolejności fal: {id, wave (od 1), item, title, note, question, current}.
// `item` — `derived.tickets[]` albo null (ticket spoza karty: wiersz przygaszony); `note` — uwagi z wiersza planu
// (`flow.plan.rows[].note`); `question` — zdanie nieodpowiedzianej decyzji tego ticketu (`flow.decisions[].happened`).
function planRows(waves, tickets, planRowList, decisions) {
    const list = Array.isArray(tickets) ? tickets : []
    const rows = Array.isArray(planRowList) ? planRowList : []
    const open = Array.isArray(decisions) ? decisions : []
    const now = currentWave(waves, list)
    const out = []
    const all = Array.isArray(waves) ? waves : []
    for (let index = 0; index < all.length; index++) {
        for (const id of Array.isArray(all[index]) ? all[index] : []) {
            const item = list.find(t => t.id === id) || null
            const row = rows.find(r => (r.id || r.workId) === id) || null
            const asked = open.find(d => d.ticket === id)
            out.push({id: id, wave: index + 1, item: item,
                      title: item && item.title || row && row.title || id,
                      note: row && row.note || "",
                      question: asked ? asked.happened || asked.question || "" : "",
                      current: index === now})
        }
    }
    return out
}
