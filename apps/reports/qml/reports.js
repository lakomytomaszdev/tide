// Logika widoku „Raporty” bez QML: etykiety dni, liczby z odmianą, sekcje „Na daily”.
// Dane przychodzą z /Reports Days() i Day(doba) (report.json z usługi) — wszystko jako zwykły tekst.
.pragma library

const DAYS_SHORT = ["niedz", "pon", "wt", "śr", "czw", "pt", "sob"]
const SECTIONS = [
    { key: "done", title: "Zrobione", tint: "mint", hollow: false },
    { key: "doing", title: "W toku", tint: "teal", hollow: false },
    { key: "waiting", title: "Czeka na mnie", tint: "peach", hollow: false },
    { key: "next", title: "Dalej", tint: "textMuted", hollow: true }
]

function parse(text, fallback) {
    try {
        const value = JSON.parse(text)
        if (value === null || typeof value !== typeof fallback) return fallback
        if (Array.isArray(fallback) !== Array.isArray(value)) return fallback
        return value
    } catch (e) {
        return fallback
    }
}

// Lista z danych: tablica JS albo lista z modelu Repeatera (modelData — Array.isArray jej nie rozpoznaje).
function list(value) {
    if (!value || typeof value === "string" || typeof value.length !== "number") return []
    return Array.prototype.slice.call(value)
}

function plural(n, one, few, many) {
    if (n === 1) return one
    const d = n % 10, h = n % 100
    if (d >= 2 && d <= 4 && !(h >= 12 && h <= 14)) return few
    return many
}

function count(n, one, few, many) {
    return n + " " + plural(n, one, few, many)
}

// „2026-10-27” → Date o północy czasu lokalnego (bez przesunięcia strefy jak w new Date("…")).
function dateOf(day) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""))
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}

function pad(n) {
    return (n < 10 ? "0" : "") + n
}

// „Dziś · wt 27.10” dla doby raportu bieżącej, inaczej „pon 26.10”.
function dayLabel(day, today) {
    const d = dateOf(day)
    if (!d) return String(day || "")
    const text = DAYS_SHORT[d.getDay()] + " " + pad(d.getDate()) + "." + pad(d.getMonth() + 1)
    return day === today ? "Dziś · " + text : text
}

function clock(ts) {
    if (!ts) return ""
    const d = new Date(ts * 1000)
    return pad(d.getHours()) + ":" + pad(d.getMinutes())
}

// „07:00 · 5 projektów” (liczba nieznana — sam czas).
function daySub(entry) {
    if (!entry) return ""
    const parts = []
    const time = clock(entry.generatedAt)
    if (time) parts.push(time)
    if (typeof entry.projects === "number") parts.push(count(entry.projects, "projekt", "projekty", "projektów"))
    return parts.join(" · ")
}

// Tytuł doby bez roku, gdy to bieżący rok: „Wtorek, 27 października”.
function dayTitle(report, today) {
    const title = report && report.title ? String(report.title) : ""
    const year = String(today || "").slice(0, 4)
    if (year && title.endsWith(" " + year)) return title.slice(0, -(year.length + 1))
    return title
}

// Linia pod tytułem: [{text, tone}] — tone "waiting" dla tego, co czeka na ciebie.
function summaryParts(report) {
    if (!report || !report.ok) return []
    const counts = report.counts || {}
    const parts = []
    if (report.since) parts.push({ text: String(report.since), tone: "" })
    if (typeof counts.projects === "number")
        parts.push({ text: count(counts.projects, "projekt", "projekty", "projektów") + " z ruchem", tone: "" })
    if (report.legacy) {
        parts.push({ text: "raport sprzed widoku w oknie — szczegóły w przeglądarce", tone: "" })
        return parts
    }
    const waiting = typeof counts.waiting === "number" ? counts.waiting : 0
    if (waiting > 0) {
        const verb = plural(waiting, "czeka", "czekają", "czeka")
        parts.push({ text: count(waiting, "rzecz", "rzeczy", "rzeczy") + " " + verb + " na ciebie", tone: "waiting" })
    } else {
        parts.push({ text: "nic na ciebie nie czeka", tone: "" })
    }
    return parts
}

function counts(project) {
    const c = project && project.counts ? project.counts : {}
    return { done: c.done || 0, doing: c.doing || 0, waiting: c.waiting || 0, next: c.next || 0, chores: c.chores || 0 }
}

// Tekst pozycji: „TOM-146 …” (ref przed tekstem) — do „Czeka na ciebie” na karcie.
function itemText(item) {
    if (!item) return ""
    const text = String(item.text || "")
    return item.ref ? String(item.ref) + ": " + text : text
}

// „Czeka na ciebie: …” na karcie projektu — pierwsze pozycje „Czeka na mnie”, bez „i N więcej”.
function waitText(project, limit) {
    const items = list(project ? project.waiting : null).filter(i => i && !i.more)
    if (items.length === 0) return ""
    const shown = items.slice(0, limit || 2).map(itemText)
    const rest = items.length - shown.length
    return shown.join(" · ") + (rest > 0 ? " · i " + rest + " więcej" : "")
}

// Sekcje prawego panelu w kolejności strony projektu (Zrobione, W toku, Czeka na mnie, Dalej).
function sections(project) {
    return SECTIONS.map(s => ({
        key: s.key, title: s.title, tint: s.tint, hollow: s.hollow,
        items: list(project ? project[s.key] : null)
    }))
}

function chores(project) {
    return list(project ? project.chores : null)
}

// Projekt do pokazania: po slugu albo nazwie (showItem, poprzedni wybór), inaczej pierwszy.
function findProject(projects, key) {
    projects = list(projects)
    if (projects.length === 0) return null
    if (key) {
        const hit = projects.find(p => p.slug === key || p.name === key)
        if (hit) return hit
    }
    return projects[0]
}

// Czy na liście dni pokazać brzoskwiniową kropkę: najnowszy, jeszcze nieotwarty raport.
function unreadDay(day, days) {
    return !!days && !!days.unread && day === days.latest
}

// „~/…” zamiast katalogu domowego w ścieżkach.
function tildePath(path, home) {
    const p = String(path || "")
    if (home && (p === home || p.indexOf(home + "/") === 0)) return "~" + p.slice(home.length)
    return p
}

// showItem: „2026-10-27” albo „2026-10-27/slug-projektu”.
function splitItem(item) {
    const text = String(item || "")
    const m = /^(\d{4}-\d{2}-\d{2})(?:\/(.+))?$/.exec(text)
    return m ? { day: m[1], project: m[2] || "" } : { day: "", project: "" }
}

// Sesje „Poza projektami” — krótko do zwiniętego wiersza: „2 sesje · 1 czeka”.
function outsideLabel(outside) {
    const sessions = list(outside)
    const waiting = sessions.filter(s => s && s.state === "waiting").length
    return count(sessions.length, "sesja", "sesje", "sesji") + (waiting ? " · " + waiting + " czeka" : "")
}

function stateTint(state) {
    switch (state) {
    case "waiting": return "waiting"
    case "working": return "working"
    case "done": return "done"
    default: return "textMuted"
    }
}

// Tagi projektu z tablicy (dokłada backend z /Board Snapshot): [{name, color}] — najwyżej limit.
function tags(project, limit) {
    return list(project ? project.tags : null).filter(t => t && t.name).slice(0, limit || 3)
}
