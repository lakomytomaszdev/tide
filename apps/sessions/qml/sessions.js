// Logika okna „Tide Sesje” bez wyglądu: kolumny tablicy, kolejność ticketów, teksty i czasy po polsku.
// Dane z tide-daemon (/Coordination: Coordinators(), Board(id), RunDetail(id, run); opis pól w
// docs/tide-daemon.md, sekcja „Koordynacja”). Kolory są nazwami tokenów palety (Tokens.qml).
.pragma library

var COLUMNS = [
    { key: "todo", title: "Do zrobienia", tint: "textMuted", dot: false },
    { key: "w-toku", title: "W toku", tint: "teal", dot: true },
    { key: "czeka", title: "Czeka", tint: "peach", dot: true },
    { key: "review", title: "Review", tint: "lavender", dot: true },
    { key: "gotowe", title: "Gotowe", tint: "sky", dot: true, hollow: true }
]
var DONE_PREVIEW = 4   // tyle gotowych ticketów widać przed „+N więcej”
var STATE_ORDER = { "czeka": 0, "review": 1, "w-toku": 2, "todo": 3, "gotowe": 4, "odrzucone": 5 }

function plural(n, one, few, many) {
    var abs = Math.abs(n)
    if (abs === 1) return one
    var tens = abs % 100
    var ones = abs % 10
    return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
}

function count(n, one, few, many) {
    return n + " " + plural(n, one, few, many)
}

function parse(text, fallback) {
    try {
        var value = JSON.parse(text)
        return value === null || value === undefined ? fallback : value
    } catch (e) {
        return fallback
    }
}

function flowCardById(cards, id) {
    if (!id) return null
    for (var i = 0; i < (cards || []).length; i++) {
        if (cards[i].id === id) return cards[i]
    }
    return null
}

// Tickety jednej kolumny: w toku i czekające od najświeższych, do zrobienia w kolejności planu,
// gotowe od ostatnio skończonych.
function column(tickets, key) {
    var list = (tickets || []).filter(function (t) { return t.column === key })
    if (key === "todo") return list
    return list.slice().sort(function (a, b) {
        return (b.lastActivityTs || 0) - (a.lastActivityTs || 0)
    })
}

function columnCounts(tickets) {
    var out = {}
    COLUMNS.forEach(function (c) { out[c.key] = 0 })
    ;(tickets || []).forEach(function (t) { if (out[t.column] !== undefined) out[t.column]++ })
    return out
}

// Pasek postępu: części w kolejności gotowe, review, w toku, czeka (ułamki całości).
function progressParts(board) {
    if (!board || !board.counts) return []
    var c = board.counts
    var total = Math.max(1, c.total || 0)
    return [
        { tint: "mint", part: (c["gotowe"] || 0) / total },
        { tint: "lavender", part: (c["review"] || 0) / total },
        { tint: "teal", part: (c["w-toku"] || 0) / total },
        { tint: "peach", part: (c["czeka"] || 0) / total }
    ].filter(function (p) { return p.part > 0 })
}

function progressText(board) {
    if (!board || !board.counts) return ""
    var c = board.counts
    return c.done + " z " + count(c.total, "ticketu", "ticketów", "ticketów") + " gotowych"
}

function percent(board) {
    if (!board || !board.counts || !board.counts.total) return "0%"
    return Math.round(100 * board.counts.done / board.counts.total) + "%"
}

function clock(ts) {
    if (!ts) return ""
    var d = new Date(ts * 1000)
    var h = d.getHours(), m = d.getMinutes()
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m
}

// „od 20:21”, a dla starszych niż doba „od wt. 20:21”.
var DAYS = ["niedz.", "pon.", "wt.", "śr.", "czw.", "pt.", "sob."]
function since(ts, nowTs) {
    if (!ts) return ""
    if (nowTs - ts < 20 * 3600) return "od " + clock(ts)
    var d = new Date(ts * 1000)
    return "od " + DAYS[d.getDay()] + " " + d.getDate() + "." + (d.getMonth() + 1) + " " + clock(ts)
}

function when(ts, nowTs) {
    if (!ts) return ""
    if (nowTs - ts < 20 * 3600) return clock(ts)
    var d = new Date(ts * 1000)
    return DAYS[d.getDay()] + " " + clock(ts)
}

function duration(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0))
    if (seconds < 60) return seconds + " s"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60) return minutes + " min"
    var hours = Math.floor(minutes / 60)
    minutes = minutes % 60
    if (hours < 24) return minutes ? hours + " h " + minutes + " min" : hours + " h"
    var days = Math.floor(hours / 24)
    return days + " d " + (hours % 24) + " h"
}

// Czas agenta na karcie: działający — od startu rundy, skończony — ile trwał.
function agentTime(agent, nowTs) {
    if (agent.state === "running") return nowTs - agent.startTs < 60 ? "teraz" : duration(nowTs - agent.startTs)
    if (agent.endTs && agent.startTs) return duration(agent.endTs - agent.startTs)
    return ""
}

// Kolor kropki agenta (nazwa tokenu): pracuje teal (recenzent lawenda), czeka brzoskwinia,
// skończył — puste kółko, przerwany róż.
function agentTint(agent, ticketState) {
    if (agent.state === "running") return ticketState === "czeka" ? "peach" : agent.role === "reviewer" ? "lavender" : "teal"
    if (agent.state === "failed" || agent.state === "killed" || agent.state === "interrupted") return "rose"
    return "sky"
}

function agentHollow(agent) {
    return agent.state === "completed"
}

function eventTint(event) {
    switch (event.state) {
    case "working": return "teal"
    case "waiting": return "peach"
    case "attention": return "peach"
    case "stopped": return "rose"
    case "done": return "sky"
    default: return "textMuted"
    }
}

function eventTime(event, nowTs) {
    if (event.now && event.kind === "running") {
        var running = nowTs - event.ts
        return "teraz · " + duration(running)
    }
    var out = when(event.ts, nowTs)
    if (event.durationS) out += " · " + duration(event.durationS)
    return out
}

// Krótki opis koordynatora w liście: „Release candidate TOM-20 · Claude · 2 h 14 min”.
function coordinatorLine(c, nowTs) {
    var parts = []
    if (c.title && c.title !== c.project) parts.push(c.title)
    parts.push(c.tool === "codex" ? "Codex" : "Claude")
    if (c.live) parts.push(duration(nowTs - (c.startedTs || nowTs)))
    else if (c.lastActivityTs) parts.push("skończony " + agoText(c.lastActivityTs, nowTs))
    return parts.join(" · ")
}

function agoText(ts, nowTs) {
    if (!ts) return "—"
    var s = Math.max(0, nowTs - ts)
    if (s < 60) return "teraz"
    if (s < 3600) return Math.floor(s / 60) + " min temu"
    if (s < 86400) return Math.floor(s / 3600) + " h temu"
    var days = Math.floor(s / 86400)
    return days === 1 ? "wczoraj" : days + " dni temu"
}

// Kolor stanu koordynatora (nazwa tokenu): czeka — brzoskwinia, pracuje — teal, inaczej puste kółko.
function coordinatorTint(c) {
    if (c.state === "waiting") return "peach"
    if (c.state === "working" || c.running > 0) return "teal"
    return "sky"
}

function coordinatorHollow(c) {
    return !(c.state === "waiting" || c.state === "working" || c.running > 0)
}

// Sesje bez podagentów do listy „Pojedyncze sesje”: żywe sesje z /Agents, które nie są koordynatorami.
function singleSessions(sessions, coordinators) {
    var ids = {}
    ;(coordinators || []).forEach(function (c) { ids[c.id] = true })
    return (sessions || []).filter(function (s) {
        return !ids[s.id] && s.state !== "ended" && s.state !== "idle"
    })
}

function ticketById(board, key) {
    if (!board || !board.tickets) return null
    for (var i = 0; i < board.tickets.length; i++) {
        if (board.tickets[i].key === key) return board.tickets[i]
    }
    return null
}

// Domyślnie zaznaczony ticket: czekający, potem w toku/review (najświeższy), potem pierwszy.
function defaultTicket(board) {
    if (!board || !board.tickets || board.tickets.length === 0) return ""
    var list = board.tickets.slice().sort(function (a, b) {
        var ra = STATE_ORDER[a.state], rb = STATE_ORDER[b.state]
        if (ra !== rb) return ra - rb
        return (b.lastActivityTs || 0) - (a.lastActivityTs || 0)
    })
    return list[0].key
}

// Ostatni przebieg ticketu z wynikiem (dla „Pokaż wynik”): najpierw zakończony, potem dowolny.
function resultRun(board, ticket) {
    if (!board || !ticket || !ticket.runs || ticket.runs.length === 0) return ""
    var runs = {}
    ;(board.runs || []).forEach(function (r) { runs[r.id] = r })
    for (var i = ticket.runs.length - 1; i >= 0; i--) {
        var r = runs[ticket.runs[i]]
        if (r && r.state !== "running" && r.hasResult) return r.id
    }
    return ticket.runs[ticket.runs.length - 1]
}

function runMeta(board, ticket, nowTs) {
    if (!board || !ticket || !ticket.runs || ticket.runs.length === 0) return ""
    var runs = {}
    ;(board.runs || []).forEach(function (r) { runs[r.id] = r })
    var first = 0, last = 0
    ticket.runs.forEach(function (id) {
        var r = runs[id]
        if (!r) return
        if (!first || r.startTs < first) first = r.startTs
        last = Math.max(last, r.state === "running" ? nowTs : (r.endTs || r.startTs))
    })
    var n = ticket.runs.length
    return count(n, "subagent", "subagentów", "subagentów") + (first ? " · " + duration(last - first) : "")
}

// Linia pod tytułem karty: przy gotowym — co zrobiono, przy czekającym na review — też co zrobiono,
// a przy pracy w toku — co teraz (opisy z describe.py; bez nich nic).
function cardLine(ticket) {
    if (!ticket) return ""
    var done = ticket.done || "", now = ticket.now || ""
    if (ticket.state === "gotowe" || ticket.state === "odrzucone" || ticket.note === "gotowe, czeka na review")
        return done || now
    if (ticket.state === "todo") return ""
    return now || done
}

// Stan karty słowami: „✓ gotowe, review OK”, „gotowe, czeka na review”, „w trakcie”, „do poprawy”…
// Pusty, gdy coś działa (wtedy karta pokazuje wiersze podagentów) albo ticket czeka na odpowiedź.
// W kolumnie „Gotowe” słowo „gotowe” jest zbędne — zostaje to, jak się skończyło.
var DONE_WORDS = {
    "review OK": "✓ review OK",
    "bez review": "✓ bez review",
    "rozpoznanie skończone": "✓ rozpoznanie skończone",
    "poprawki po review, bez ponownego review": "✓ poprawione po review",
    "zmiany po review, bez ponownego review": "✓ zmienione po review"
}
var STOPPED_WORDS = {
    "przerwany — koordynator już nie działa": "przerwane — koordynator już nie działa",
    "podagent zakończył się błędem": "przerwane błędem podagenta",
    "podagent zatrzymany": "zatrzymane"
}
function stateLine(ticket) {
    if (!ticket) return ""
    var note = ticket.note || ""
    if (ticket.state === "todo") return ticket.inPlan ? "w planie · bez agenta" : "bez agenta"
    if (ticket.state === "odrzucone") return "odrzucone"
    if (ticket.state === "gotowe") {
        if (DONE_WORDS[note]) return DONE_WORDS[note]
        if (ticket.review && ticket.review.ok) return DONE_WORDS["review OK"]
        return ticket.source === "plan" ? "✓ gotowe według planu" : ticket.source === "tracker" ? "✓ zamknięte w YouTracku"
                                                                                                : "✓ gotowe"
    }
    if (ticket.state === "czeka") return ""
    if (runningAgents(ticket).length > 0) return ""
    if (note === "gotowe, czeka na review") return "gotowe, czeka na review"
    if (STOPPED_WORDS[note]) return STOPPED_WORDS[note]
    if (note.indexOf("po review:") === 0) return "po review — do poprawy"
    if (ticket.state === "review") return "w review"
    return "w trakcie"
}

// Kolor linii stanu (nazwa tokenu).
function stateTint(ticket) {
    if (!ticket) return "textMuted"
    if (ticket.state === "gotowe") return "mint"
    if (ticket.note === "gotowe, czeka na review") return "lavender"
    if (STOPPED_WORDS[ticket.note || ""]) return "rose"
    if ((ticket.note || "").indexOf("po review:") === 0) return "peach"
    return "textMuted"
}

// Działający podagenci ticketu (do wierszy na karcie).
function runningAgents(ticket) {
    return ((ticket && ticket.agents) || []).filter(function (a) { return a.state === "running" })
}

// Rola podagenta słowem: implementacja, poprawki, review, ponowne review, rozpoznanie.
function agentWord(agent) {
    if (!agent) return ""
    if (agent.word) return agent.word
    return agent.role === "reviewer" ? "review" : agent.role === "helper" ? "rozpoznanie" : "implementacja"
}

// Zgodność ze starszą usługą (bez pól opisu): stan karty jak wcześniej.
function cardFooter(ticket) {
    return stateLine(ticket)
}

function sourceText(ticket) {
    var parts = []
    if (ticket.source === "plan") parts.push("stan z planu")
    else if (ticket.source === "tracker") parts.push("stan z YouTracka")
    else if (ticket.source === "runs") parts.push("stan z pracy podagentów")
    if (ticket.planState && ticket.planState !== ticket.state) parts.push("w planie: " + ticket.planState)
    if (ticket.trackerState) parts.push("YouTrack: " + ticket.trackerState)
    // Tryb lokalny, gdy model zawiódł: „Model lokalny niedostępny — opis bez modelu (…)” zamiast „opis z reguł”.
    if (ticket.describeNote) parts.push(ticket.describeNote)
    else if (ticket.describedBy === "model") parts.push("opis: model")
    else if (ticket.describedBy === "model-stale") parts.push("opis: model (sprzed ostatniej zmiany)")
    else if (ticket.describedBy === "rules") parts.push("opis z reguł")
    return parts.join(" · ")
}

// Skąd tytuł i jak brzmiało zlecenie — drobny dopisek pod „Co to jest”.
function titleSources(ticket) {
    if (!ticket) return ""
    var parts = []
    var from = { "plan": "w planie", "tracker": "w YouTracku", "backlog": "w backlogu" }[ticket.officialFrom || ""]
    if (from && ticket.officialTitle && ticket.officialTitle !== ticket.title) parts.push(from + ": „" + ticket.officialTitle + "”")
    if (ticket.taskTitle && ticket.taskTitle !== ticket.title && ticket.taskTitle !== ticket.officialTitle)
        parts.push("zlecenie: „" + ticket.taskTitle + "”")
    return parts.join(" · ")
}

// Pasek ostrzeżeń nad kolumnami: kontrola numerów ticketów w planach repozytorium (board.warnings,
// coordination/numbering.py). Kolizje twarde, najwyżej `limit` linii, reszta jako „i N więcej”. Łagodne
// uwagi (np. numer zarezerwowany pod tytułem w innym języku) tylko jedną linią z liczbą — 28.10 piętnaście
// takich linii zasłaniało tablicę.
var WARNINGS_SHOWN = 3

function warningLines(board, limit) {
    var list = (board && board.warnings) || []
    limit = limit || WARNINGS_SHOWN
    var hard = list.filter(function (w) { return w.severity === "hard" })
    var soft = list.length - hard.length
    var out = hard.slice(0, limit).map(function (w) { return "Konflikt numerów: " + (w.text || "") })
    if (hard.length > limit)
        out.push("i " + (hard.length - limit) + " więcej — wszystkie pokaże make check-plans")
    if (soft > 0)
        out.push("Numeracja: " + soft + (soft === 1 ? " uwaga" : soft % 10 >= 2 && soft % 10 <= 4 && (soft % 100 < 12 || soft % 100 > 14) ? " uwagi" : " uwag")
                 + " — make check-plans")
    return out
}
