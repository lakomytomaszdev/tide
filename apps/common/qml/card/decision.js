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

// --- Zgrupowane zgody na to samo polecenie (K-97, PT-338) -----------------------------------------------------
//
// Kilka kroków karty prosi o to samo polecenie poza piaskownicą (10.11, K-88: 7 próśb o `make test-changed`):
// okno pokazuje jedną pozycję „<polecenie> · N ticketów” zamiast N decyzji. Grupa obejmuje wyłącznie prośby,
// które użytkownik widzi jako jedno: `kind: "permission"` bez listy `commands[]` (prośba poza piaskownicą,
// możliwość „run”), ten sam tekst `technical.command` znak w znak (bez przycinania spacji i zmiany cudzysłowów),
// to samo zdanie ostrzeżenia (pierwsza linia `why`: ryzykowna prośba ma „Uwaga: …”) i te same możliwości w tej
// samej kolejności (`id`, `choice`, tytuł z rodziną polecenia, metoda). Ryzykowna prośba ma inne możliwości niż
// zwykła („Uruchom i zezwalaj do końca ticketu”, bez „Zawsze w projekcie”), więc się z nią nie łączy. Łańcuch
// (`commands[]`), decyzja z blokiem opiekuna i pojedyncza prośba zostają osobno. Grupa nie liczy niczego nowego:
// kolejność pozycji i możliwości jest kolejnością usługi, polecona możliwość jest tylko przekazana dalej.
//
// Odpowiedź grupy to istniejące żądanie każdej decyzji (`method` i `params` jej możliwości o tym samym `id`,
// z jej `step`) — usługa dostaje N zwykłych odpowiedzi, „Zawsze w projekcie” znaczy to samo co w pojedynczej zgodzie.

var GROUP_PREFIX = "consent-group:"
var RECEIPT_PREFIX = "consent-receipt:"
var SHORT_COMMAND = 70   // jak w usłudze (decisions.SHORT_COMMAND): dłuższe polecenie stoi tylko w polu pod spodem

// Opis możliwości grupy: `n` — liczba kroków (n ≥ 2). Pozostałe możliwości mają opis z usługi (jest w kluczu).
function groupEffect(choice, n) {
    if (choice === "run")
        return "Każdy z " + n + " kroków dostanie jedno uruchomienie; następne takie polecenie znowu zapyta."
    if (choice === "run-always")
        return "Polecenie ruszy teraz w " + n + " krokach, a ta rodzina poleceń w tym projekcie będzie uruchamiana "
               + "bez pytania."
    if (choice === "deny")
        return count(n, "krok wznowi", "kroki wznowią", "kroków wznowi") + " się bez polecenia."
    return ""
}

// „1 ticket”, „3 tickety”, „7 ticketów” (odmiana polska).
function count(n, one, few, many) {
    var mod10 = n % 10, mod100 = n % 100
    var word = n === 1 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many
    return n + " " + word
}

function firstLine(value) {
    return text(value).split("\n")[0]
}

// Klucz grupowania decyzji; pusty napis — decyzja zostaje osobno.
function consentKey(decision) {
    var d = decision
    if (!d || d.kind !== "permission" || !d.id || !d.step) return ""
    if (!Array.isArray(d.commands) || d.commands.length !== 0) return ""
    if (hasCaretaker(d)) return ""
    var command = d.technical && typeof d.technical.command === "string" ? d.technical.command : ""
    if (command === "") return ""
    var options = Array.isArray(d.options) ? d.options : []
    var outside = false
    var parts = []
    for (var i = 0; i < options.length; i++) {
        var o = options[i]
        // Każda możliwość musi odpowiadać za swój krok — inaczej odpowiedź grupy nie trafiłaby do właściwego kroku.
        if (!o || !o.id || !o.method || !o.params || o.params.step !== d.step) return ""
        if (o.id === "run") outside = true
        parts.push([o.id, o.choice || "", o.title || "", o.method, groupEffect(o.id, 2) === "" ? text(o.effect) : ""])
    }
    if (!outside) return ""
    return JSON.stringify([command, firstLine(d.why), parts])
}

// Podpis liczebności: „7 ticketów” albo — gdy kroki nie mają osobnych ticketów — „7 kroków”.
function membersLabel(members) {
    var seen = {}
    var distinct = 0
    for (var i = 0; i < members.length; i++) {
        var t = members[i].ticket || ""
        if (t !== "" && !seen[t]) {
            seen[t] = true
            distinct++
        }
    }
    return distinct === members.length ? count(members.length, "ticket", "tickety", "ticketów")
                                       : count(members.length, "krok", "kroki", "kroków")
}

// Wiersz rozwijanej listy grupy: ticket, krok, wykonawca i powód kroku z usługi.
function memberRow(decision) {
    var tech = decision.technical || {}
    return { id: decision.id, ticket: decision.ticket || "", step: decision.step || "",
             executor: decision.executor || "", reason: text(tech.reason) }
}

function groupHead(command, label) {
    return (command.length <= SHORT_COMMAND ? command : "To samo polecenie poza piaskownicą") + " · " + label
}

// --- Prognoza „ruszy od razu / poczeka” (K-97, PT-340) -----------------------------------------------------------
// Obciążenie liczy strona Tablicy z migawki wszystkich kart. Usługa nie daje pól strukturalnych, tylko napisy
// wyświetlane — rozpoznawane tu, w jednym miejscu; test okna buduje migawkę prawdziwym kodem usługi (`fold` →
// `derived`) i łamie się, gdy napis się zmieni (zamiast po cichu fałszować licznik).
var OUTSIDE_RUNNING_LABEL = "polecenie poza piaskownicą"   // `kindLabel` wpisu `flow.running[]` (fullcard.running_steps)
var OUTSIDE_QUEUED_PREFIX = "w kolejce: "                    // `statusLabel` ticketu ze zgodą w kolejce (fullcard.ticket_status)
// Lustro limitów usługi (OUTSIDE_PER_PROJECT, OUTSIDE_TOTAL w tide_daemon.cardflow.engine) — test okna je porównuje.
var OUTSIDE_PER_PROJECT = 6
var OUTSIDE_TOTAL = 7

// Obciążenie poleceniami poza piaskownicą z migawki kart: {projectRunning, totalRunning, projectQueued, totalQueued}
// albo null, gdy migawki nie da się rozpoznać (brak listy kart, wpis bez `kindLabel`, ticket bez `statusLabel`).
function outsideLoad(cards, projectSlug) {
    if (!Array.isArray(cards)) return null
    var load = { projectRunning: 0, totalRunning: 0, projectQueued: 0, totalQueued: 0 }
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i]
        var flow = card && card.derived && card.derived.flow
        if (!flow) continue
        var mine = !!projectSlug && card.project === projectSlug
        var running = flow.running
        if (running !== undefined && !Array.isArray(running)) return null
        for (var r = 0; running && r < running.length; r++) {
            var label = running[r] && running[r].kindLabel
            if (typeof label !== "string" || label === "") return null
            if (label === OUTSIDE_RUNNING_LABEL) {
                load.totalRunning++
                if (mine) load.projectRunning++
            }
        }
        var tickets = card.derived.tickets
        if (tickets !== undefined && !Array.isArray(tickets)) return null
        for (var t = 0; tickets && t < tickets.length; t++) {
            var status = tickets[t] && tickets[t].statusLabel
            if (typeof status !== "string") return null
            if (status.indexOf(OUTSIDE_QUEUED_PREFIX) === 0) {
                load.totalQueued++
                if (mine) load.projectQueued++
            }
        }
    }
    return load
}

// Ile z `n` zgód ruszy od razu, a ile poczeka: wolne miejsca to mniejsza z reszt limitu projektu i limitu łącznego,
// po odjęciu trwających i już czekających w kolejce. To szacunek ze stanu migawki, nie kolejność startu.
function outsideForecast(load, n) {
    if (!load) return null
    var free = Math.min(OUTSIDE_PER_PROJECT - load.projectRunning - load.projectQueued,
                        OUTSIDE_TOTAL - load.totalRunning - load.totalQueued)
    var now = Math.max(0, Math.min(n, free))
    return { now: now, wait: n - now }
}

function forecastText(load, n) {
    var forecast = outsideForecast(load, n)
    if (!forecast) return ""
    return "Ruszy od razu: " + forecast.now + ", poczeka w kolejce: " + forecast.wait + " (w projekcie biegnie teraz "
           + load.projectRunning + " z " + OUTSIDE_PER_PROJECT + ", łącznie " + load.totalRunning + " z "
           + OUTSIDE_TOTAL + ") — według stanu sprzed chwili. To szacunek, nie obietnica kolejności startu."
}

function makeGroup(members, load) {
    var first = members[0]
    var command = first.technical.command
    var label = membersLabel(members)
    var options = []
    var source = first.options || []
    for (var i = 0; i < source.length; i++) {
        var option = copy(source[i])
        var effect = groupEffect(option.id, members.length)
        if (effect !== "") option.effect = effect
        option.params = { choice: source[i].params.choice }   // żądania składa groupRequests z decyzji kroków
        options.push(option)
    }
    return { id: GROUP_PREFIX + first.id, group: true, receipt: false, kind: "permission", fault: "",
             ticket: label, executor: "", step: "", happened: groupHead(command, label), why: firstLine(first.why),
             technical: { command: command }, options: options, freeText: false, reply: null, commands: [],
             findings: [], copyCommands: [], members: members.map(memberRow), sources: members,
             count: members.length, receiptText: "", forecast: forecastText(load, members.length) }
}

// Pozycje okna z listy decyzji usługi: pojedyncze decyzje i grupy zgód (grupa stoi w miejscu swojej pierwszej
// decyzji). `loose` — id decyzji trzymanych osobno (np. po odmowie usługi dla jednego kroku grupy).
function consentGroups(decisions, loose, load) {
    var list = decisions || []
    var apart = loose || []
    var keys = []
    var byKey = {}
    for (var i = 0; i < list.length; i++) {
        var key = apart.indexOf(list[i].id) >= 0 ? "" : consentKey(list[i])
        keys.push(key)
        if (key !== "") byKey[key] = (byKey[key] || []).concat([list[i]])
    }
    var out = []
    var placed = {}
    for (var j = 0; j < list.length; j++) {
        var k = keys[j]
        if (k === "" || byKey[k].length < 2) out.push(list[j])
        else if (!placed[k]) {
            placed[k] = true
            out.push(makeGroup(byKey[k], load))
        }
    }
    return out
}

// Żądania grupy po wyborze możliwości `optionId`: [{id, method, params}] — po jednym na decyzję, z jej `step`.
function groupRequests(group, optionId) {
    var out = []
    var sources = group && group.sources ? group.sources : []
    for (var i = 0; i < sources.length; i++) {
        var option = (sources[i].options || []).find(function (o) { return o.id === optionId })
        if (option) out.push({ id: sources[i].id, method: option.method, params: copy(option.params) })
    }
    return out
}

// Stan odpowiedzi grupowych właściciela formularza (okno decyzji, „Twój ruch”): `sent` — id decyzji → znacznik
// wysyłki, `counts` — ile odpowiedzi wysłała grupa, `failed` — ile z nich usługa odrzuciła, `loose` — decyzje
// odrzucone, które wracają osobno, `reasons` — powód odmowy przy takiej decyzji.
function consentStart() {
    return { sent: {}, counts: {}, failed: {}, loose: [], reasons: {} }
}

function consentSent(state, ids) {
    var next = { sent: copy(state.sent), counts: copy(state.counts), failed: copy(state.failed),
                 loose: state.loose.filter(function (id) { return ids.indexOf(id) < 0 }), reasons: copy(state.reasons) }
    if (ids.length === 0) return next
    var token = ids[0]
    for (var i = 0; i < ids.length; i++) {
        next.sent[ids[i]] = token
        delete next.reasons[ids[i]]
    }
    next.counts[token] = ids.length
    next.failed[token] = 0
    return next
}

function isSent(state, id) {
    return !!state && Object.prototype.hasOwnProperty.call(state.sent, id)
}

// Usługa odrzuciła odpowiedź dla jednego kroku grupy: tylko ta decyzja wraca, osobno i z powodem.
function consentFailed(state, id, message) {
    if (!isSent(state, id)) return state
    var next = { sent: copy(state.sent), counts: copy(state.counts), failed: copy(state.failed),
                 loose: state.loose.concat([id]), reasons: copy(state.reasons) }
    var token = next.sent[id]
    delete next.sent[id]
    next.failed[token] = (next.failed[token] || 0) + 1
    next.reasons[id] = message
    return next
}

// Napis znaku przyjęcia: „Przyjęto · wysyłam N odpowiedzi” (i ile odrzuciła usługa).
function receiptText(sentCount, failedCount) {
    var line = "Przyjęto · wysyłam " + count(sentCount, "odpowiedź", "odpowiedzi", "odpowiedzi")
    if (failedCount > 0)
        line += " · usługa odrzuciła " + count(failedCount, "odpowiedź", "odpowiedzi", "odpowiedzi")
                + " — " + (failedCount === 1 ? "wraca osobno" : "wracają osobno") + " z powodem"
    return line
}

// Pozycje do pokazania: decyzje bez odpowiedzi (bez `hidden`), zgrupowane, oraz znak przyjęcia każdej wysłanej
// grupy w miejscu jej pierwszej decyzji, dopóki usługa jeszcze pokazuje te decyzje w migawce.
function consentItems(decisions, hidden, state, load) {
    var list = decisions || []
    var skip = hidden || []
    var st = state || consentStart()
    var open = list.filter(function (d) { return skip.indexOf(d.id) < 0 && !isSent(st, d.id) })
    var items = consentGroups(open, st.loose, load)
    var receipts = {}
    var order = []
    for (var i = 0; i < list.length; i++) {
        if (skip.indexOf(list[i].id) >= 0 || !isSent(st, list[i].id)) continue
        var token = st.sent[list[i].id]
        if (!receipts[token]) {
            receipts[token] = [list[i]]
            order.push({ token: token, at: i })
        } else receipts[token].push(list[i])
    }
    // Wstaw znak przyjęcia przed pierwszą pozycją, której pierwsza decyzja stoi u usługi dalej niż jego.
    for (var r = 0; r < order.length; r++) {
        var members = receipts[order[r].token]
        var head = members[0]
        var receipt = { id: RECEIPT_PREFIX + order[r].token, group: true, receipt: true, kind: "permission", fault: "",
                        ticket: membersLabel(members), executor: "", step: "",
                        happened: groupHead(head.technical && head.technical.command ? String(head.technical.command) : "",
                                            membersLabel(members)),
                        why: "",
                        receiptText: receiptText(st.counts[order[r].token] || members.length,
                                                 st.failed[order[r].token] || 0),
                        technical: { command: head.technical ? head.technical.command : "" }, options: [],
                        freeText: false, reply: null, commands: [], findings: [], copyCommands: [],
                        members: members.map(memberRow), count: members.length, forecast: "" }
        var at = items.length
        for (var p = 0; p < items.length; p++) {
            if (position(list, items[p]) > order[r].at) {
                at = p
                break
            }
        }
        items.splice(at, 0, receipt)
    }
    return items
}

// Miejsce pozycji w liście usługi: decyzja — jej indeks, grupa — indeks pierwszej decyzji.
function position(list, item) {
    var id = item.group && item.members && item.members.length ? item.members[0].id : item.id
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return i
    return list.length
}
