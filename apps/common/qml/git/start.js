// Jedyny formatter „startu” w Tide (K-36, PT-354): krótka nazwa gałęzi, data d.MM i 7 znaków SHA.
// Słownictwo „Start od…” na karcie, w oknie scalania i na mapie (K-99) woła tę funkcję — bez kopii.
//
// Wejście to surowe pola z `Git.Diff` (`from`/`to`: {ref, sha, date}); data to ISO 8601 (UTC) z demona.
// Wynik to zwykły tekst („main · 5.11 · abc1234”); pokazuj go jako Text.PlainText — nazwy gałęzi bywają
// dowolne („feature/a<b>”).
.pragma library

// refs/heads/x → x, refs/remotes/o/x → o/x; inne napisy bez zmian (nazwa z „/” i znakami specjalnymi zostaje).
function shortRef(ref) {
    if (typeof ref !== "string") return ""
    if (ref.indexOf("refs/heads/") === 0) return ref.substring(11)
    if (ref.indexOf("refs/remotes/") === 0) return ref.substring(13)
    return ref
}

// d.MM w czasie lokalnym (dzień bez zera wiodącego, miesiąc dwucyfrowy); brak albo zła data → "".
function shortDate(date) {
    if (typeof date !== "string" || date === "") return ""
    const d = new Date(date)
    if (isNaN(d.getTime())) return ""
    const month = d.getMonth() + 1
    return d.getDate() + "." + (month < 10 ? "0" : "") + month
}

// Każda część jest opcjonalna; bez żadnej danych → „nieznany”.
function startLabel(from) {
    const point = from && typeof from === "object" ? from : {}
    const parts = [shortRef(point.ref), shortDate(point.date),
                   typeof point.sha === "string" ? point.sha.substring(0, 7) : ""].filter(p => p !== "")
    return parts.length ? parts.join(" · ") : "nieznany"
}

// --- Start karty (K-99, PT-389) -------------------------------------------------------------------------
// Zdanie „master (3.11, 58a3f26)” na tych samych pomocnikach (`shortRef`, `shortDate`, 7 znaków SHA).
// Dane są z migawki `derived.flow`: baza z `flow.base`, data z ostatniego `card.started` w `flow.timeline`
// (nie data commita). Skuteczna korekta bazy (patrz `_corrected`) zmienia bazę, a `timeline` nie niesie
// czasu korekty — wtedy zdanie jest bez daty, a pierwotną bazę (`flow.initialBase`) pokazuje `cardStartHint`.
// Follow-up (osobna zmiana silnika): `card.base-changed` w `timeline` pozwoli pokazać datę po korekcie.

function _startEvent(flow) {
    const timeline = flow && Array.isArray(flow.timeline) ? flow.timeline : []
    for (let i = timeline.length - 1; i >= 0; i--)
        if (timeline[i] && timeline[i].type === "card.started") return timeline[i]
    return null
}

// Zlecenie wystartowało: w `timeline` jest `card.started`.
function hasStarted(flow) {
    return _startEvent(flow) !== null
}

function _phrase(base, date) {
    if (!base || typeof base !== "object" || base.status !== "known") return "nieznany"
    const ref = shortRef(base.baseRef)
    if (ref === "") return "nieznany"
    const inner = [shortDate(date), typeof base.baseCommit === "string" ? base.baseCommit.substring(0, 7) : ""]
        .filter(p => p !== "")
    return inner.length ? ref + " (" + inner.join(", ") + ")" : ref
}

// Silnik zapisuje `card.started` z `baseVersion=1`, więc skuteczna korekta to wersja > 1 (1→2) albo baza
// różna od pierwotnej (`flow.initialBase`).
function _corrected(flow) {
    if (!flow) return false
    if (typeof flow.baseVersion === "number" && flow.baseVersion > 1) return true
    const a = flow.base, b = flow.initialBase
    if (!a || !b || typeof a !== "object" || typeof b !== "object") return false
    return a.baseRef !== b.baseRef || a.baseCommit !== b.baseCommit
}

// „master (3.11, 58a3f26)”, „origin/develop (5.11, abc1234)”, „master (58a3f26)” bez daty, „nieznany”.
function cardStartLabel(flow) {
    if (!flow) return "nieznany"
    const started = _startEvent(flow)
    return _phrase(flow.base, started && !_corrected(flow) ? started.ts : "")
}

// Linia na karcie; "" dla karty bez wystartowanego zlecenia.
function cardStartLine(flow) {
    return hasStarted(flow) ? "Start od: " + cardStartLabel(flow) : ""
}

// Podpowiedź po korekcie bazy: pierwotna baza z datą startu; inaczej "".
function cardStartHint(flow) {
    const started = _startEvent(flow)
    if (!started || !_corrected(flow)) return ""
    return "Pierwotna baza: " + _phrase(flow.initialBase, started.ts)
}

// „Zmień gałąź startową…” tylko na karcie bez pracy: zlecenie wystartowało, brak domknięcia, żaden wiersz planu
// poza `todo`, brak scalonych ticketów. Karta „Od razu” (bez wierszy planu) nie ma kroku poza historią.
function canChangeStart(derived) {
    const flow = derived && derived.flow
    if (!flow || !hasStarted(flow) || flow.closed || flow.merged || flow.mainMerged) return false
    const tickets = Array.isArray(derived.tickets) ? derived.tickets : []
    if (tickets.some(t => t && t.state !== "todo")) return false
    if (tickets.length > 0) return true
    return !(Array.isArray(flow.steps) ? flow.steps : []).some(s => !s.historical)
}

// --- Scalenie karty (K-99, PT-401) ----------------------------------------------------------------------
// „Scalono do master (11.11 19:02)” z `flow.merge` (cel zapisany w zdarzeniu scalenia) i czasu tego zdarzenia (`ts`,
// czas lokalny, ta sama data d.MM co w „Start od”). Brak celu → „Scalono (cel niezapisany)”; brak czasu → bez nawiasu.
// Karta niescalona → "".
function shortTime(date) {
    if (typeof date !== "string" || date === "") return ""
    const d = new Date(date)
    if (isNaN(d.getTime())) return ""
    const pad = n => (n < 10 ? "0" : "") + n
    return pad(d.getHours()) + ":" + pad(d.getMinutes())
}

function mergedLine(flow) {
    if (!flow || !flow.merged) return ""
    const merge = flow.merge && typeof flow.merge === "object" ? flow.merge : {}
    const target = shortRef(merge.targetRef || merge.targetBranch || "")
    const when = [shortDate(merge.ts), shortTime(merge.ts)].filter(p => p !== "").join(" ")
    const suffix = when ? " (" + when + ")" : ""
    return target === "" ? "Scalono (cel niezapisany)" + suffix : "Scalono do " + target + suffix
}
