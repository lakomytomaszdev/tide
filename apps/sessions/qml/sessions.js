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

function cardFooter(ticket) {
    if (ticket.state === "todo") return ticket.inPlan ? "w planie · bez agenta" : "bez agenta"
    if (ticket.state === "odrzucone") return "odrzucone"
    if (ticket.state === "gotowe") {
        if (ticket.review && ticket.review.ok) return "✓ review OK"
        return "✓ " + (ticket.note || "gotowe")
    }
    return ""
}

function sourceText(ticket) {
    var parts = []
    if (ticket.source === "plan") parts.push("stan z planu")
    else if (ticket.source === "tracker") parts.push("stan z YouTracka")
    else if (ticket.source === "runs") parts.push("stan z pracy podagentów")
    if (ticket.planState && ticket.planState !== ticket.state) parts.push("w planie: " + ticket.planState)
    if (ticket.trackerState) parts.push("YouTrack: " + ticket.trackerState)
    return parts.join(" · ")
}
