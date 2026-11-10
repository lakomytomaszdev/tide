// Reguły przydziału ticketu (PT-334): wykonawca, recenzent i klasa. Czyste funkcje bez QML, wspólne dla okna
// ustawień ticketu i każdego przyszłego odbiorcy. Katalog wykonawców to `modelsStatus.executors` (lista albo
// `{list: [...]}`), wpis: `name`, `provider`, `model`, `effort`, `available`, `error`.
//
// Reguły odwzorowują usługę (`EditCardPlan`, action edit: `independent_reviewer`, zakres lokalny, klasa
// „lokalny”) oraz regułę karty K-97: przy klasie „mocny” recenzent musi być z innej rodziny modeli (dostawcy).
// Okno nie dopuszcza więc niczego, co usługa odrzuci. Dostępność wykonawcy (`available === false`) usługa przy
// edycji nie sprawdza — okno wyszarza niedostępnych z powodem z usługi, ale wartość, która już jest w planie,
// nie blokuje zapisu.
.pragma library
.import "decision.js" as Decision

var CLASSES = [
    { value: "lekki", label: "lekki" },
    { value: "standard", label: "standard" },
    { value: "mocny", label: "mocny" },
    { value: "lokalny", label: "lokalny" }
]

var REASON_SAME_MODEL = "ten sam model co autor"
var REASON_SCOPE = "inny zakres: lokalny i chmura"
var REASON_STRONG = "przy klasie mocny recenzent innej rodziny modeli"

function catalog(status) {
    var list = status && Array.isArray(status.executors) ? status.executors
             : status && status.executors && Array.isArray(status.executors.list) ? status.executors.list : []
    return list.filter(function (e) { return e && e.name })
}

function find(status, name) {
    return catalog(status).find(function (e) { return e.name === name }) || null
}

function isLocal(executor) {
    return !!executor && executor.provider === "local"
}

// Ten sam model (bez względu na wielkość liter) i ta sama siła myślenia — jak `independent_reviewer` w usłudze.
function sameModelAndEffort(author, reviewer) {
    return String(author.model || "").toLowerCase() === String(reviewer.model || "").toLowerCase()
        && String(author.effort || "") === String(reviewer.effort || "")
}

function unavailableReason(executor) {
    if (!executor || executor.available !== false) return ""
    return String(executor.error || "") || "wykonawca niedostępny"
}

// Powód, dla którego `reviewer` nie może recenzować pracy `author` przy klasie `cls` ("" — wolno).
// Twarde reguły przydziału bez dostępności.
function reviewerRuleReason(author, reviewer, cls) {
    if (!author || !reviewer) return ""
    if (isLocal(author) !== isLocal(reviewer)) return REASON_SCOPE
    if (sameModelAndEffort(author, reviewer)) return REASON_SAME_MODEL
    if (cls === "mocny" && author.provider === reviewer.provider) return REASON_STRONG
    return ""
}

// Powód niedostępności klasy dla wykonawcy: „lokalny” wymaga wykonawcy lokalnego.
function classReason(author, cls) {
    if (cls === "lokalny" && author && !isLocal(author)) return REASON_SCOPE
    return ""
}

// Powód wyszarzenia wykonawcy przy wybranej klasie.
function executorReason(executor, cls) {
    return unavailableReason(executor) || classReason(executor, cls)
}

function option(value, reason) {
    return { value: value, label: value, disabled: reason !== "", reason: reason }
}

// Trzy listy okna dla wyboru `sel` = {executor, reviewer, class}: wszystkie pozycje katalogu, niedozwolone
// z `disabled` i `reason`.
function options(status, sel) {
    var author = find(status, sel.executor)
    var cls = sel["class"]
    var all = catalog(status)
    return {
        executors: all.map(function (e) { return option(e.name, executorReason(e, cls)) }),
        reviewers: all.map(function (e) {
            return option(e.name, unavailableReason(e) || reviewerRuleReason(author, e, cls))
        }),
        classes: CLASSES.map(function (c) { return option(c.value, classReason(author, c.value)) })
    }
}

// Wybór po zmianie wykonawcy albo klasy: recenzent zostaje, jeśli nadal jest dozwolony, inaczej jest pusty.
function keepReviewer(status, sel) {
    if (!sel.reviewer) return sel
    var author = find(status, sel.executor)
    var reviewer = find(status, sel.reviewer)
    var ok = author && reviewer && reviewerRuleReason(author, reviewer, sel["class"]) === ""
    return { executor: sel.executor, reviewer: ok ? sel.reviewer : "", "class": sel["class"] }
}

// Czy wybór można zapisać: {ok, reason}. `original` to wartości z planu — nie blokują zapisu niedostępnością.
function verdict(status, sel, original) {
    var author = find(status, sel.executor)
    var reviewer = find(status, sel.reviewer)
    if (!author) return { ok: false, reason: "Wybierz wykonawcę z katalogu." }
    var authorReason = classReason(author, sel["class"]) || (sel.executor !== original.executor ? unavailableReason(author) : "")
    if (authorReason) return { ok: false, reason: "Wykonawca „" + author.name + "”: " + authorReason + "." }
    if (!sel.reviewer) return { ok: false, reason: "Wybierz recenzenta — poprzedni nie pasuje do wybranego wykonawcy i klasy." }
    if (!reviewer) return { ok: false, reason: "Wybierz recenzenta z katalogu." }
    var rule = reviewerRuleReason(author, reviewer, sel["class"])
    if (rule) return { ok: false, reason: "Recenzent „" + reviewer.name + "”: " + rule + "." }
    var gone = sel.reviewer !== original.reviewer ? unavailableReason(reviewer) : ""
    if (gone) return { ok: false, reason: "Recenzent „" + reviewer.name + "”: " + gone + "." }
    if (CLASSES.every(function (c) { return c.value !== sel["class"] })) return { ok: false, reason: "Wybierz klasę." }
    return { ok: true, reason: "" }
}

// Żądanie `EditCardPlan` dla wiersza planu: kartka (`body`) idzie bez zmian.
function editRequest(row, sel) {
    return { action: "edit", ticket: row.id || row.workId, executor: sel.executor, reviewer: sel.reviewer,
             "class": sel["class"], body: row.body }
}

// --- ticket trwający (PT-335): przepięcie autora i recenzenta przez ResolveCardStep -------------------------
//
// Warunki odwzorowują usługę (engine.py, tylko odczyt) na migawce `derived.flow`:
// - `reassign-author` (`_reassign_fix_author`): ticket ma krok w `st.steps` i żaden jego krok `ticket`/`fix` nie
//   trwa → okno: krok ticketu w `flow.steps[]` i brak kroku `ticket`/`fix` tego ticketu w `flow.running[]`;
// - `reassign-reviewer` (`_reassign_waiting_reviewer`): `(ticket, "review")` w `st.waiting` i żaden krok ticketu
//   nie trwa → okno: `{ticket, kind: "review"}` w `flow.waiting[]` i brak kroku tego ticketu w `flow.running[]`.
// `flow.running[]` obejmuje wszystko, co usługa liczy jako trwające (`st.running()`: kroki bez wyniku i opiekun),
// oraz polecenia poza piaskownicą w toku — okno jest więc co najwyżej ostrożniejsze od usługi, nigdy łagodniejsze.

var LOCK_AUTHOR_WORKING = "Autor właśnie pracuje — zmienisz go, gdy skończy bieżący krok."
var LOCK_AUTHOR_NO_STEP = "Ticket nie ma jeszcze kroku autora."
var LOCK_REVIEW_RUNNING = "Review już biegnie."
var LOCK_REVIEW_NOT_WAITING = "Review jeszcze nie czeka na start."

function flowList(flow, key) {
    return flow && Array.isArray(flow[key]) ? flow[key] : []
}

function runningOf(flow, ticket) {
    return flowList(flow, "running").filter(function (s) { return s && s.ticket === ticket })
}

// Powód zablokowania pola „Autor kolejnych kroków” ("" — pole aktywne).
function authorLock(flow, ticket) {
    if (runningOf(flow, ticket).some(function (s) { return s.kind === "ticket" || s.kind === "fix" }))
        return LOCK_AUTHOR_WORKING
    if (!flowList(flow, "steps").some(function (s) { return s && s.ticket === ticket })) return LOCK_AUTHOR_NO_STEP
    return ""
}

// Powód zablokowania pola „Recenzent” ("" — pole aktywne).
function reviewerLock(flow, ticket) {
    var running = runningOf(flow, ticket)
    if (running.some(function (s) { return s.kind === "review" })) return LOCK_REVIEW_RUNNING
    if (running.length > 0) return LOCK_REVIEW_NOT_WAITING
    if (!flowList(flow, "waiting").some(function (w) { return w && w.ticket === ticket && w.kind === "review" }))
        return LOCK_REVIEW_NOT_WAITING
    return ""
}

// Dwie listy okna trwającego ticketu: autorzy (dostępność i klasa z wiersza planu, jak wykonawcy w #3) i recenzenci
// (reguły przydziału względem obecnego autora `current.author` i klasy `cls`).
function runningOptions(status, current, cls) {
    var author = find(status, current.author)
    var all = catalog(status)
    return {
        authors: all.map(function (e) { return option(e.name, executorReason(e, cls)) }),
        reviewers: all.map(function (e) {
            return option(e.name, unavailableReason(e) || reviewerRuleReason(author, e, cls))
        })
    }
}

// Czy wybór pola trwającego ticketu można zapisać: {ok, reason}. `field` — "author" albo "reviewer"; `chosen` —
// wybrana nazwa; `current` — {author, reviewer} z kafelka (obecna wartość nie blokuje zapisu niedostępnością).
function runningVerdict(status, field, chosen, current, cls) {
    var picked = find(status, chosen)
    if (!picked) return { ok: false, reason: field === "author" ? "Wybierz autora z katalogu." : "Wybierz recenzenta z katalogu." }
    var gone = chosen !== current[field] ? unavailableReason(picked) : ""
    if (field === "author") {
        var why = classReason(picked, cls) || gone
        return why ? { ok: false, reason: "Autor „" + picked.name + "”: " + why + "." } : { ok: true, reason: "" }
    }
    var rule = reviewerRuleReason(find(status, current.author), picked, cls) || gone
    return rule ? { ok: false, reason: "Recenzent „" + picked.name + "”: " + rule + "." } : { ok: true, reason: "" }
}

// Żądanie `ResolveCardStep` dla pola trwającego ticketu.
function reassignRequest(field, ticket, executor) {
    return { choice: field === "author" ? "reassign-author" : "reassign-reviewer", ticket: ticket, executor: executor }
}

// --- „Ponów innym wykonawcą” przy błędzie kroku ticketu (PT-341) -------------------------------------------
//
// Pole nie ma własnej logiki wysyłania: możliwość `change-executor` bierze się z decyzji `error` tego ticketu
// (`flow.decisions[]`), a żądanie buduje `decision.js` (`optionRequest`) z `params` możliwości (`choice`, `step`),
// do których okno dokłada wybranego wykonawcę — tak samo jak okno decyzji. Warunki odwzorowują
// `FlowEngine.resolve_step` (engine.py, tylko odczyt):
// - decyzja `error` kroku ticketu z `change-executor` na liście możliwości (usługa buduje ją tylko dla kroku
//   zakończonego błędem po drugiej próbie, bez następcy i bez wcześniejszej decyzji) — bez niej pola nie ma;
// - żaden inny krok tego ticketu nie trwa (`busy` → `no-step-question`; kroki opiekuna się nie liczą);
// - `_resume_executor` (decyzja użytkownika, nie opiekuna): wykonawca z katalogu i dostępny, a praca lokalna
//   nie miesza się z chmurową — względem recenzenta (krok autora i poprawek), autora (krok review) albo
//   wykonawcy nieudanego kroku (pozostałe rodzaje). Niezależność autora i recenzenta to tylko rekomendacja.
var LOCK_RETRY_WORKING = "Krok tego ticketu właśnie pracuje — ponowisz go, gdy skończy."
var REASON_RETRY_SCOPE = "praca lokalna i chmurowa się nie mieszają"

// Decyzja błędu ticketu z możliwością „Ponów innym wykonawcą”: {decision, option} albo null.
function retryDecision(flow, ticket) {
    var list = flowList(flow, "decisions")
    for (var i = 0; i < list.length; i++) {
        var d = list[i]
        if (!d || d.kind !== "error" || d.ticket !== ticket || !Array.isArray(d.options)) continue
        for (var j = 0; j < d.options.length; j++) {
            var o = d.options[j]
            if (o && o.id === "change-executor" && o.method === "ResolveCardStep" && o.params
                    && o.params.choice === "change-executor" && o.params.step)
                return { decision: d, option: o }
        }
    }
    return null
}

// Powód zablokowania pola „Ponów innym wykonawcą” ("" — pole aktywne).
function retryLock(flow, ticket) {
    var busy = runningOf(flow, ticket).some(function (s) { return s.kind !== "caretaker" && s.kind !== "caretaker-chat" })
    return busy ? LOCK_RETRY_WORKING : ""
}

// Wykonawca po drugiej stronie pary autor/recenzent, względem którego usługa pilnuje zakresu lokalny/chmura.
function retryScopeRef(status, decision, current) {
    var kind = decision.stepKind
    var name = kind === "ticket" || kind === "fix" ? current.reviewer : kind === "review" ? current.author : decision.executor
    return find(status, name)
}

function retryReason(status, executor, decision, current) {
    var gone = unavailableReason(executor)
    if (gone) return gone
    var other = retryScopeRef(status, decision, current)
    return other && isLocal(other) !== isLocal(executor) ? REASON_RETRY_SCOPE : ""
}

// Lista okna: wszystkie pozycje katalogu, niedozwolone z powodem. `current` = {author, reviewer} z kafelka.
function retryOptions(status, decision, current) {
    return catalog(status).map(function (e) { return option(e.name, retryReason(status, e, decision, current)) })
}

function retryVerdict(status, chosen, decision, current) {
    var picked = find(status, chosen)
    if (!picked) return { ok: false, reason: "Wybierz wykonawcę z katalogu." }
    var why = retryReason(status, picked, decision, current)
    return why ? { ok: false, reason: "Wykonawca „" + picked.name + "”: " + why + "." } : { ok: true, reason: "" }
}

// `ResolveCardStep` dla możliwości decyzji z wybranym wykonawcą: {method, params}.
function retryRequest(choice, executor) {
    var request = Decision.optionRequest(choice)
    request.params.executor = executor
    return request
}
