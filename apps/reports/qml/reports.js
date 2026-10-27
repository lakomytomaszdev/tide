// Logika widoku „Raporty” bez QML: etykiety dni, liczby z odmianą, sekcje „Na daily”, „Dziś w skrócie”,
// „Czeka teraz” (na żywo), „Skończone dziś” i linia projektów z samymi porządkami.
// Dane przychodzą z /Reports Days() i Day(doba) (report.json z usługi) — wszystko jako zwykły tekst.
.pragma library

const DAYS_SHORT = ["niedz", "pon", "wt", "śr", "czw", "pt", "sob"]
const SECTIONS = [
    { key: "done", title: "Zrobione", tint: "mint", hollow: false },
    { key: "doing", title: "W toku", tint: "teal", hollow: false },
    { key: "waiting", title: "Czeka na mnie", tint: "peach", hollow: false },
    // „Uwaga”: testy, które nie przechodzą, i konflikty numerów — nie „czeka”, pokazywane tylko, gdy są.
    { key: "attention", title: "Uwaga", tint: "butter", hollow: false, optional: true },
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
    return { done: c.done || 0, doing: c.doing || 0, waiting: c.waiting || 0, next: c.next || 0, chores: c.chores || 0,
             attention: c.attention || 0 }
}

// Tekst pozycji: „TOM-146 …” (ref przed tekstem) — do „Czeka na ciebie” na karcie.
function itemText(item) {
    if (!item) return ""
    const text = String(item.text || "")
    return item.ref ? String(item.ref) + ": " + text : text
}

// „Czeka na ciebie: …” na karcie projektu — pierwsze pozycje „Czeka na mnie”, bez „i N więcej”
// i bez nieaktualnych (stale: agent już na to nie czeka — usługa przelicza to na żywo).
function waitText(project, limit) {
    const items = list(project ? project.waiting : null).filter(i => i && !i.more && !i.stale)
    if (items.length === 0) return ""
    const shown = items.slice(0, limit || 2).map(itemText)
    const rest = items.length - shown.length
    return shown.join(" · ") + (rest > 0 ? " · i " + rest + " więcej" : "")
}

// Sekcje prawego panelu w kolejności strony projektu (Zrobione, W toku, Czeka na mnie, Uwaga, Dalej).
function sections(project) {
    return SECTIONS.map(s => ({
        key: s.key, title: s.title, tint: s.tint, hollow: s.hollow, optional: !!s.optional,
        items: list(project ? project[s.key] : null)
    }))
}

function chores(project) {
    return list(project ? project.chores : null)
}

// „Skończone dziś” — notki po tickecie (reports/notes.py w usłudze).
function finished(project) {
    return list(project ? project.finished : null).filter(n => n && n.text)
}

// Pierwsze zdanie notki na karcie projektu: „Skończone dziś: Eksport logów do CSV” (+ „i N więcej”).
function finishedText(project) {
    const notes = finished(project)
    if (notes.length === 0) return ""
    const first = String(notes[notes.length - 1].summary || notes[notes.length - 1].title || "")
    return first + (notes.length > 1 ? " · i " + (notes.length - 1) + " więcej" : "")
}

// Dopisek o ręcznym sprawdzeniu pod notką: „Sprawdź ręcznie: a; b” albo „Wymaga sprawdzenia ręcznego”.
function checkText(note) {
    if (!note) return ""
    const items = list(note.checklist).filter(c => c)
    if (items.length) return "Sprawdź ręcznie: " + items.join("; ")
    return note.manual ? "Wymaga sprawdzenia ręcznego" : ""
}

// „Dziś w skrócie”: zdania z raportu (model albo reguły), bez pustych.
function briefLines(report) {
    return list(report ? report.brief : null).filter(b => b).map(String)
}

// „Czeka teraz” (na żywo, jak widżet agentów): [{project, text}] — tekst bez projektu.
function nowItems(report) {
    return list(report ? report.now : null).filter(w => w).map(w => {
        const asks = w.reason === "question" || (w.reason !== "approval" && String(w.text || "").endsWith("?"))
        const what = asks ? (w.text ? "pyta: „" + w.text + "”" : "czeka na odpowiedź")
                          : "czeka na zgodę" + (w.text ? " (" + w.text + ")" : "")
        return { project: String(w.project || w.title || "agent"), text: what, id: String(w.id || "") }
    })
}

// Projekty z samymi porządkami — jedna zwinięta linia „Porządki: QuotaApp, BO, dev-dashboard…”.
function choreProjects(report) {
    return list(report ? report.choreProjects : null).filter(p => p && p.name)
}

function choreLine(report, limit) {
    const names = choreProjects(report).map(p => String(p.name))
    if (names.length === 0) return ""
    const shown = names.slice(0, limit || 4)
    return "Porządki: " + shown.join(", ") + (names.length > shown.length ? "…" : "")
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
