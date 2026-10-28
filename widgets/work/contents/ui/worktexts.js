// Teksty i wiersze widżetu Praca (kafelek, podpowiedź, popup, pasek boczny) z listy tide-daemon
// /Board Work() — {exists, items, counts}, opis w docs/tide-daemon.md. Lista jest już policzona
// w usłudze (te same kolumny co na Tablicy, sesje bez karty), tu są tylko słowa i kolejność wierszy.
// Czyste funkcje — testy liczą je w qml6 (widgets/tests/test_widget_work.py).
.pragma library

var SIDEBAR_ROWS = 5

// Grupy popupu w kolejności: najpierw to, co czeka na ciebie. Skończone (sesje bez karty po turze
// albo bezczynne) są wyciszone, zwinięte i nie liczą się w nagłówku.
var GROUPS = [
    { key: "waiting", title: "Czeka na ciebie", tint: "waiting" },
    { key: "progress", title: "W toku", tint: "working" },
    { key: "review", title: "Do sprawdzenia", tint: "lavender" },
    { key: "todo", title: "Do zrobienia", tint: "textSecondary" },
    { key: "finished", title: "Skończone", tint: "textMuted" }
]

function plural(n, one, few, many) {
    const abs = Math.abs(n)
    if (abs === 1) return one
    const tens = abs % 100
    const ones = abs % 10
    return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
}

function waitingWord(n) {
    return plural(n, "czeka", "czekają", "czeka")
}

function items(work) {
    return work && Array.isArray(work.items) ? work.items : []
}

// Liczniki z usługi; starsza odpowiedź bez `counts` — policzone z pozycji.
function counts(work) {
    const out = { waiting: 0, progress: 0, review: 0, todo: 0, finished: 0, total: 0, sessions: 0 }
    if (work && work.counts && typeof work.counts.total === "number") {
        for (const key in out) out[key] = work.counts[key] || 0
        return out
    }
    for (const item of items(work)) {
        if (out[item.group] === undefined) continue
        out[item.group]++
        if (item.group !== "finished") {
            out.total++
            if (item.kind === "session") out.sessions++
        }
    }
    return out
}

// Części linii kafelka (tylko niezerowe): [{count, word, tint, hollow}]. „do spr.” — kafelek w docku
// liczy się w pikselach (mock); w popupie i podpowiedzi pełne „do sprawdzenia”.
function tileParts(n, short) {
    const parts = []
    if (n.progress) parts.push({ count: n.progress, word: "w toku", tint: "working", hollow: false })
    if (n.waiting) parts.push({ count: n.waiting, word: waitingWord(n.waiting), tint: "waiting", hollow: false })
    if (n.review) parts.push({ count: n.review, word: short === false ? "do sprawdzenia" : "do spr.", tint: "lavender",
                               hollow: false })
    if (!parts.length && n.todo) parts.push({ count: n.todo, word: "do zrobienia", tint: "textSecondary", hollow: true })
    return parts
}

// „3 w toku · 2 czeka · 2 do spr.”, „3 do zrobienia”, „nic w toku”.
function tileSummary(n, short) {
    const parts = tileParts(n, short)
    return parts.length ? parts.map(p => p.count + " " + p.word).join(" · ") : "nic w toku"
}

// Dopisek przy etykiecie: „Praca · 10” — wszystko w grze (bez Skończonych); zero — nic.
function tileDetail(n) {
    return n.total ? String(n.total) : ""
}

function toolTip(available, n) {
    if (!available) return "Usługa tide-daemon nie działa"
    if (!n.total) return "Nic w toku ani do zrobienia — „＋” zleca agentowi nowe zadanie"
    const parts = []
    if (n.progress) parts.push(n.progress + " w toku")
    if (n.waiting) parts.push(n.waiting + " " + waitingWord(n.waiting) + " na ciebie")
    if (n.review) parts.push(n.review + " do sprawdzenia")
    if (n.todo) parts.push(n.todo + " do zrobienia")
    const loose = n.sessions ? " (w tym " + n.sessions + " " + plural(n.sessions, "sesja", "sesje", "sesji")
                               + " bez karty)" : ""
    return "Praca: " + n.total + loose + " — " + parts.join(", ") + "\nKliknij, aby zobaczyć, co czeka"
}

// Podtytuł popupu: pełne słowa, także „do zrobienia”.
function subtitle(n) {
    if (!n.total) return "Nic w toku ani do zrobienia"
    const parts = tileParts(n, false).filter(p => !p.hollow)
    if (n.todo) parts.push({ count: n.todo, word: "do zrobienia" })
    return parts.map(p => p.count + " " + p.word).join(" · ")
}

// Krótki czas: „teraz”, „4 min”, „2 h”, „3 d” (jak Theme.shortAgo).
function shortAgo(ts, nowTs) {
    if (!ts) return ""
    const s = Math.max(0, nowTs - ts)
    if (s < 60) return "teraz"
    if (s < 3600) return Math.floor(s / 60) + " min"
    if (s < 86400) return Math.floor(s / 3600) + " h"
    return Math.floor(s / 86400) + " d"
}

// Słowo stanu agenta w wierszu W toku / Skończone: „pracuje”, „bezczynny 1 d”, „skończył 5 min temu”.
function agentWord(item, nowTs) {
    const ago = shortAgo(item.sinceTs, nowTs)
    switch (item.agentState) {
    case "working": return "pracuje"
    case "waiting": return "czeka"
    case "idle": return ago ? "bezczynny " + ago : "bezczynny"
    case "done": return !ago ? "skończył turę" : ago === "teraz" ? "skończył przed chwilą" : "skończył " + ago + " temu"
    case "ended": return "sesja zakończona"
    default: return ""
    }
}

// Słowo przy karcie do sprawdzenia: co musi zrobić człowiek (notatka z derive: „do scalenia: feat/x”,
// „do sprawdzenia ręcznie”).
function reviewWord(item) {
    const note = item.note || ""
    if (note.indexOf("do scalenia") >= 0) return "do scalenia"
    if (note.indexOf("ręcznie") >= 0) return "do testu"
    return "do sprawdzenia"
}

function quoted(text) {
    return "„" + text + "”"
}

// Wiersz do narysowania: {key, group, kind, label, loose, title, line, word, tint, hollow, progress,
// session, card, go, addCard, launch, request}. go — „Przejdź” (do agenta: wypełniony przy tym, co czeka,
// zwykły link przy karcie w toku albo do sprawdzenia z żywą sesją, jak dawniej u Agentów), addCard —
// „+ karta” (sesja bez karty; nie, gdy zakładanie już trwa — pending), launch — „Uruchom” (karta do zrobienia).
// Sesja, która czeka, a jej karta nie stoi w „Czeka” (np. karta w Gotowych), ma numer karty i notatkę o niej.
function row(item, nowTs, pending) {
    const group = GROUPS.find(g => g.key === item.group) || GROUPS[1]
    const session = item.kind === "session"
    const loose = session && !item.card
    const out = {
        key: item.kind + ":" + item.id, group: item.group, kind: item.kind, loose: loose,
        label: loose ? "bez karty" : item.card || item.id,
        title: session ? (item.projectName || item.title || "sesja") : item.title || "",
        line: "", word: "", tint: group.tint, hollow: session || item.group === "todo" || item.group === "finished",
        progress: item.total > 0 ? Math.min(item.total, item.done || 0) + "/" + item.total : "",
        session: item.session || "", card: item.card || "",
        go: false, addCard: loose && !!item.cardRequest && !(pending && pending[item.session]), launch: false,
        request: item.cardRequest || null
    }
    if (item.group === "waiting") {
        // Bez słowa „czeka” — mówi to nagłówek grupy i brzoskwiniowe tło, a miejsce bierze pytanie.
        out.word = ""
        const asked = item.question ? quoted(item.question) : ""
        out.line = session && item.card ? [asked, item.note].filter(t => t).join(" · ")
                                        : asked || item.note || "czeka na ciebie"
        out.go = true
    } else if (item.group === "progress") {
        out.word = agentWord(item, nowTs) || "w toku"
        out.line = session ? (item.note || item.title || "") : item.note || ""
        if (session && item.title && out.title !== item.title) out.line = item.title + (item.note ? " · " + item.note : "")
        out.go = session ? !!item.session : !!item.canActivate
    } else if (item.group === "review") {
        out.word = reviewWord(item)
        out.line = item.note || ""
        out.progress = ""
        out.go = !!item.canActivate
    } else if (item.group === "todo") {
        out.word = ""   // nagłówek grupy mówi „do zrobienia”, a obok jest „Uruchom”
        out.line = item.note || ""
        out.launch = !session
    } else {
        out.word = agentWord(item, nowTs) || "skończone"
        out.line = item.title && out.title !== item.title ? item.title : item.note || ""
        out.go = !!item.session
    }
    return out
}

// Grupy popupu: [{key, title, tint, count, rows, muted}]; puste grupy pomijamy. Skończone mają wiersze
// tylko po rozwinięciu (showFinished), ale zawsze licznik w nagłówku grupy. pending — sesje, dla których
// „+ karta” już poszło (bez przycisku, dopóki lista ich nie zgubi).
function groups(work, nowTs, showFinished, pending) {
    const all = items(work)
    const out = []
    for (const g of GROUPS) {
        const members = all.filter(i => i.group === g.key)
        if (!members.length) continue
        const muted = g.key === "finished"
        out.push({ key: g.key, title: g.title, tint: g.tint, count: members.length, muted: muted,
                   rows: muted && !showFinished ? [] : members.map(i => row(i, nowTs, pending)) })
    }
    return out
}

// Sesje bez karty, które jeszcze są na liście — po nich kafelek czyści „zakładanie w toku” (pending).
function looseSessions(work) {
    const out = {}
    for (const item of items(work)) if (item.kind === "session" && !item.card) out[item.session] = true
    return out
}

// Wiersze kafelka w pasku bocznym: czeka, w toku, do sprawdzenia, do zrobienia — najwyżej limit.
function sidebarRows(work, nowTs, limit) {
    const max = limit || SIDEBAR_ROWS
    const all = items(work).filter(i => i.group !== "finished").map(i => row(i, nowTs))
    return { rows: all.slice(0, max), more: Math.max(0, all.length - max) }
}

// Nazwa wiersza dla czytnika ekranu: „K-20 Twitch, czeka na ciebie: „Czy taki podział?””,
// „sesja bez karty pricingapp, pracuje: Edit · main.py”.
function accessibleRow(r) {
    const who = r.loose ? "sesja bez karty " + r.title : r.label + " " + r.title
    const word = r.group === "waiting" ? "czeka na ciebie" : r.word
    return who + ", " + word + (r.line ? ": " + r.line : "")
}

// Odpowiedź CreateCard („+ karta”): "" — założona, inaczej komunikat po polsku.
function replyError(text) {
    let reply = null
    try { reply = JSON.parse(text) } catch (e) {}
    if (!reply) return "Usługa tide-daemon nie odpowiedziała — spróbuj jeszcze raz."
    return reply.ok ? "" : String(reply.message || reply.error || "nie udało się założyć karty")
}
