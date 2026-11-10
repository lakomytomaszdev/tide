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
