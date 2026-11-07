// Pomocnicze okna decyzji karty (DecisionDialog.qml). Okno niczego nie liczy: zdania, kolejność, polecona
// możliwość i miejsce odpowiedzi przychodzą z usługi w `derived.flow.decisions[]` (PT-123,
// daemon/tide_daemon/cardflow/decisions.py). Tu są tylko podpisy do wyświetlenia i złożenie żądania
// z gotowych pól `method` i `params` (tekst własnej odpowiedzi trafia do `params[reply.field]`).
.pragma library

// Podpis plakietki nad pytaniem: rodzaj decyzji (`kind`), dla błędów — czyja to usterka (`fault`).
var KIND_LABELS = {
    "caretaker": "Opiekun",
    "question": "Pytanie",
    "permission": "Prośba o zgodę",
    "error": "Błąd kroku",
    "limit": "Limit dostawcy",
    "transfer": "Przekazanie kroku",
    "stopped": "Rozmowa przerwana",
    "review": "Review bez zgody",
    "plan-approval": "Plan do zatwierdzenia",
    "plan-revision": "Poprawiony plan",
    "plan-revision-failed": "Poprawka planu odrzucona",
    "plan-reviewer": "Review planu czeka",
    "tracker": "Błąd YouTracka"
}
var FAULT_LABELS = {
    "tide": "Błąd Tide", "provider": "Błąd dostawcy", "model": "Błąd wykonawcy", "user": "Przerwane",
    "unknown": "Błąd kroku", "tracker": "Błąd YouTracka"
}

// Etykiety znanych pól `technical{}` w zwiniętych szczegółach; pozostałe klucze idą pod własną nazwą.
var TECH_LABELS = {
    "step": "Krok", "stepKind": "Rodzaj kroku", "ticket": "Ticket", "executor": "Wykonawca",
    "provider": "Dostawca", "model": "Model", "mode": "Tryb", "attempt": "Próba", "parentStep": "Poprzedni krok",
    "result": "Wynik kroku", "reason": "Powód", "reasonLabel": "Powód po ludzku", "question": "Pytanie",
    "command": "Polecenie", "canAllow": "Można dopisać do listy", "finishedAt": "Zakończony",
    "notes": "Notki", "stepDir": "Katalog kroku", "worktree": "Worktree", "branch": "Gałąź",
    "attempts": "Próby", "stalledSince": "Bez postępu od", "limitReset": "Odnowienie limitu",
    "limitWindow": "Okno limitu", "wipCommitted": "Commit WIP", "wipFiles": "Pliki w WIP",
    "wipError": "Błąd WIP", "planner": "Planujący", "reviewer": "Recenzent", "verdict": "Werdykt",
    "findings": "Uwagi recenzenta", "tickets": "Tickety", "errors": "Błędy formatu", "round": "Runda",
    "blockers": "Blokujące", "majors": "Ważne", "nits": "Drobne", "waitReason": "Powód czekania",
    "difference": "Różnica planu", "action": "Operacja", "key": "Klucz", "error": "Błąd",
    "fault": "Czyja wina (kod)", "faultCause": "Przyczyna (kod)", "faultLabel": "Czyja wina",
    "signature": "Podpis błędu", "base": "Bieżąca baza", "baseProposal": "Propozycja bazy"
}

// Podpis pola własnej odpowiedzi: z usługi (`reply.label`, np. „Opisz problem i ponów”) albo „Inna odpowiedź…”.
function replyLabel(decision) {
    var reply = decision && decision.reply
    return reply && reply.label ? String(reply.label) : "Inna odpowiedź…"
}

// Metoda podglądu przed zapisem odpowiedzi (`reply.preview`, PT-125); pusty napis — odpowiedź idzie od razu.
function previewMethod(decision) {
    var reply = decision && decision.reply
    return reply && reply.preview ? String(reply.preview) : ""
}

// Zdanie nad podglądem: nowa karta w Backlogu projektu albo wystąpienie dopisane do otwartej karty.
function previewWhere(preview) {
    if (!preview) return ""
    var project = preview.project || "plasma-tide"
    if (preview.action === "append" && preview.card)
        return "Ten sam błąd jest już w otwartej karcie " + preview.card + " (" + project + ") — dopiszę to wystąpienie:"
    return "Nowa karta w Backlogu " + project + ":"
}

function kindLabel(decision) {
    if (!decision) return ""
    if (decision.kind === "error" && FAULT_LABELS[decision.fault]) return FAULT_LABELS[decision.fault]
    return KIND_LABELS[decision.kind] || "Decyzja"
}

// „K-33 · #3 · Sonnet” — karta, ticket i wykonawca z decyzji (puste pola pominięte).
function who(cardId, decision) {
    if (!decision) return cardId || ""
    return [cardId, decision.ticket, decision.executor].filter(function (x) { return !!x }).join(" · ")
}

function text(value) {
    if (value === null || value === undefined) return ""
    if (Array.isArray(value)) return value.map(text).filter(function (x) { return x !== "" }).join("\n")
    if (typeof value === "object") return JSON.stringify(value)
    if (typeof value === "boolean") return value ? "tak" : "nie"
    return String(value)
}

// Wiersze szczegółów technicznych [{label, value}] w kolejności kluczy z usługi, bez pustych wartości.
function techRows(technical) {
    var rows = []
    if (!technical) return rows
    for (var key in technical) {
        var value = text(technical[key])
        if (value === "") continue
        rows.push({ label: TECH_LABELS[key] || key, value: value })
    }
    return rows
}

// Możliwość wymaga jeszcze wyboru w oknie (np. `executor: ""` przy „Ponów innym wykonawcą…") — kontrakt PT-123:
// pole o pustej wartości uzupełnia okno. Wybór wykonawcy jest na razie w panelu karty.
function needsInput(option) {
    if (!option || !option.params) return false
    for (var key in option.params) {
        if (option.params[key] === "") return true
    }
    return false
}

// Podpis wagi uwagi recenzenta; stary tekst (bez wagi) dostaje „bez wagi”.
var SEVERITY_LABELS = { "blocker": "blokująca", "major": "ważna", "nit": "drobna" }

function severityLabel(severity) {
    return SEVERITY_LABELS[severity] || "bez wagi"
}

// Id uwag zaznaczonych z góry przez usługę (`findings[].selected`).
function defaultChecked(decision) {
    var ids = []
    var list = decision && decision.findings ? decision.findings : []
    for (var i = 0; i < list.length; i++) if (list[i].selected === true) ids.push(list[i].id)
    return ids
}

// Zaznaczone uwagi w kształcie żądania usługi `{severity, text, tickets}` (bez `id` i `selected`).
function checkedFindings(decision, ids) {
    var out = []
    var list = decision && decision.findings ? decision.findings : []
    for (var i = 0; i < list.length; i++) {
        if (ids.indexOf(list[i].id) >= 0)
            out.push({ severity: list[i].severity, text: list[i].text, tickets: list[i].tickets || [] })
    }
    return out
}

// Odesłanie planu niesie tylko zaznaczone uwagi (zamiast domyślnych z usługi); reszta żądań bez zmian.
function applyFindings(decision, request, ids) {
    if (!request || !request.params || request.params.action !== "return") return request
    if (!decision || !decision.findings || decision.findings.length === 0) return request
    request.params.findings = checkedFindings(decision, ids)
    return request
}

function copy(params) {
    var out = {}
    for (var key in (params || {})) out[key] = params[key]
    return out
}

// Zgoda na wybrane polecenia z łańcucha (PT-124): możliwość z `params.allow` (lista) dostaje indeksy
// zaznaczonych poleceń z `commands[]` decyzji.
function picksCommands(option) {
    return !!option && !!option.params && Array.isArray(option.params.allow)
}

// Lista poleceń w oknie: zawsze, gdy decyzja ma możliwość wyboru poleceń (także łańcuch, z którego po
// odjęciu własnego odczytu katalogu kroku zostało jedno polecenie — zgoda dotyczy tekstu widocznego na liście),
// oraz gdy jest więcej niż jedno polecenie albo jedyne nie może być dopuszczone (powód przy poleceniu).
function showsCommands(decision) {
    var list = (decision && decision.commands) || []
    if (list.length === 0) return false
    var options = (decision && decision.options) || []
    for (var i = 0; i < options.length; i++) {
        if (picksCommands(options[i])) return true
    }
    return list.length > 1 || list[0].canAllow !== true
}

// Zaznaczenie poleceń po otwarciu decyzji: wprost z pola `preselected` usługi (to zaznaczenie poleceń,
// nie możliwości decyzji — D10 dotyczy możliwości).
function preselectedIndexes(decision) {
    var out = []
    var list = (decision && decision.commands) || []
    for (var i = 0; i < list.length; i++) {
        if (list[i].preselected === true && list[i].canAllow === true) out.push(i)
    }
    return out
}

// Zaznaczenie po kliknięciu polecenia `index`, w kolejności listy; polecenia bez zgody nie da się zaznaczyć.
function toggled(commands, checks, index) {
    var out = []
    for (var i = 0; i < (commands || []).length; i++) {
        var on = checks.indexOf(i) >= 0
        if (i === index) on = !on && commands[i].canAllow === true
        if (on) out.push(i)
    }
    return out
}

// Plakietki polecenia: flagi z usługi.
function commandTags(command) {
    var tags = []
    if (!command) return tags
    if (command.forbidden === true) tags.push("zabronione")
    if (command.readOnly === true) tags.push("tylko czyta")
    if (command.stepDir === true) tags.push("katalog kroku")
    return tags
}

// Żądanie do usługi po wyborze możliwości: {method, params} wprost z decyzji (z indeksami zaznaczonych poleceń,
// gdy możliwość ich wymaga).
function optionRequest(option, checks) {
    var params = copy(option.params)
    if (picksCommands(option)) params.allow = (checks || []).slice()
    return { method: option.method, params: params }
}

// Wybór korekty oczekuje na podgląd; pytanie pozostaje dostępne, w tym możliwość Zachowaj.
function keepsPending(params) {
    return !!params && params.baseDecision === "change"
}

// Blok „Opiekun poleca” (K-37, PT-157): pole `caretaker{summary, recommendation, tried[], optionId, failed}` liczy
// usługa (caretaker.annotate). Okno tylko je formatuje: nie przestawia możliwości i niczego nie wybiera (D10).
function hasCaretaker(decision) {
    var care = decision && decision.caretaker
    return !!care && typeof care === "object" && !!(care.recommendation || care.summary)
}

// Nagłówek bloku: „Opiekun poleca: <rekomendacja>”; bez rekomendacji (opiekun nie poradził sobie) — samo oddanie decyzji.
function caretakerTitle(care) {
    var recommendation = care && care.recommendation ? String(care.recommendation) : ""
    return recommendation ? "Opiekun poleca: " + recommendation : "Opiekun oddaje decyzję"
}

var TRIED_ACTIONS = { "escalate": "Oddanie decyzji" }

// Próby opiekuna w kolejności z usługi: [{action, reason, effect}] — akcja, powód (zdanie z usługi) i skutek.
function triedRows(care) {
    var rows = []
    var list = care && Array.isArray(care.tried) ? care.tried : []
    for (var i = 0; i < list.length; i++) {
        var item = list[i] || {}
        rows.push({ action: TRIED_ACTIONS[item.action] || item.action || "Działanie",
                    reason: text(item.text),
                    effect: item.ok === false ? "nie udało się" : "udało się" })
    }
    return rows
}

// Podpis przełącznika „Co próbowano”; pusta lista `tried[]` ukrywa sekcję (przycisk i listę).
function triedToggleLabel(care, open) {
    return (open ? "▾ " : "▸ ") + "Co próbowano (" + triedRows(care).length + ")"
}

// Żądanie z własną odpowiedzią: `reply` decyzji z tekstem w `params[reply.field]`.
function replyRequest(decision, answer) {
    var reply = decision && decision.reply
    if (!reply) return null
    var params = copy(reply.params)
    params[reply.field || "answer"] = answer
    return { method: reply.method, params: params }
}
