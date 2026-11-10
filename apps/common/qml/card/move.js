// Zdanie bloku „Twój ruch” pełnej karty (PT-337, K-97; K-70 pkt 9 i 11). Usługa liczy `derived.flow.headline`
// i tej wartości nie zmieniamy. Gdy nic nie czeka na użytkownika, a usługa mówi tylko ogólnie („1 krok pracuje
// w tle”, „Teraz: PT-322.”), biblioteka nazywa trwający krok z pól migawki: `flow.running[]` (`kind`, `kindLabel`,
// `ticket`, `executor`), `derived.phases[]` i `derived.tickets[]` (`statusLabel`).
//
// Zasada nadrzędna: ważne zdanie usługi (decyzja, wstrzymanie, opiekun, limit, blokery, baza, karta domknięta
// lub scalona) nigdy nie jest zastępowane. Zdanie okna wchodzi wyłącznie na miejsce dwóch ogólnych zdań usługi,
// rozpoznanych po ich początku; każde inne zdanie przechodzi bez zmian.
.pragma library

var OUTSIDE_LABEL = "polecenie poza piaskownicą"
var RETRY_LABEL = "ponawia po błędzie"
var MAX_NAMED = 2

var GENERIC_RUNNING = /^\d+ kro(k|ki|ków) (pracuje|pracują) w tle/
var GENERIC_NOW = /^Teraz: /

var TAIL = "; następna decyzja będzie Twoja przy domknięciu karty, chyba że któryś krok o coś zapyta."

// „+ 1 inny”, „+ 2 inne”, „+ 5 innych”.
function otherForm(n) {
    if (n === 1) return "inny"
    return n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "inne" : "innych"
}

function executorSuffix(executor) {
    return executor ? " (" + executor + ")" : ""
}

function closingSentence(outside, executor) {
    if (outside)
        return "Pełne testy domknięcia biegną poza piaskownicą; następna decyzja będzie Twoja przy scaleniu karty, "
               + "chyba że testy padną."
    return "Domknięcie pracuje w tle" + executorSuffix(executor)
           + "; następna decyzja będzie Twoja przy scaleniu karty, chyba że domknięcie o coś zapyta."
}

function named(step) {
    const prefix = step.ticket ? step.ticket + ": " : ""
    if (step.kind === "fix") return prefix + "poprawki po review" + executorSuffix(step.executor)
    if (step.kind === "review") return prefix + "review" + executorSuffix(step.executor)
    return ""
}

// Zdanie do bloku „Twój ruch”; `tickets` jest opcjonalne (ponowienie po błędzie czyta stan ticketu).
function moveHeadline(flow, phases, tickets) {
    if (!flow) return ""
    const service = flow.headline || ""
    if (Array.isArray(flow.decisions) && flow.decisions.length > 0) return service
    const generic = GENERIC_RUNNING.test(service)
    if (!generic && !GENERIC_NOW.test(service)) return service

    const running = Array.isArray(flow.running) ? flow.running : []
    const close = running.filter(s => s.kind === "close")
    if (close.length > 0)
        return closingSentence(close.some(s => s.kindLabel === OUTSIDE_LABEL), close[0].executor || "")

    const names = running.map(named).filter(text => text !== "")
    const runningTickets = running.map(s => s.ticket)
    const retries = (Array.isArray(tickets) ? tickets : [])
        .filter(t => t && t.statusLabel === RETRY_LABEL && runningTickets.indexOf(t.id) < 0)
        .map(t => t.id + ": ponowienie po błędzie")
    const all = names.concat(retries)
    if (all.length === 0) {
        // „Teraz: <ticket>.” przy etapie domknięcia w toku to domknięcie; zwykłe kroki ticketów zostają zdaniem usługi.
        const closing = generic ? null : (Array.isArray(phases) ? phases : []).find(p => p.id === "closing")
        return closing && closing.state === "now" ? closingSentence(false, closing.executor || "") : service
    }
    const shown = all.slice(0, MAX_NAMED)
    const rest = running.length + retries.length - shown.length
    return shown.join(", ") + (rest > 0 ? " + " + rest + " " + otherForm(rest) : "") + TAIL
}
