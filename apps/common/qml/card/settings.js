// Rzadkie ustawienia pełnej karty (CardFullView.qml, PT-134): lista akcji wspólna dla sekcji „Ustawienia karty”
// i menu „Karta ▾” oraz klucz zgłoszenia YouTracka. Widok niczego nie liczy poza podpisami przycisków: kolumnę,
// wstrzymanie i gałąź podaje usługa w migawce.
.pragma library

// Nazwa kolumny z etykiet migawki Tablicy (`labels`: id → nazwa), bez własnej kopii nazw.
function columnName(labels, id) {
    return (labels && labels[id]) || id
}

// Klucz zgłoszenia z klucza albo adresu (jak Logic.issueKey w Tablicy): "" — bez zgłoszenia, null — błędny zapis.
function issueKey(text) {
    var value = String(text || "").trim()
    if (!value) return ""
    var at = value.lastIndexOf("/issue/")
    if (at >= 0) value = value.slice(at + 7).split(/[/?#]/)[0]
    value = value.toUpperCase()
    return /^[A-Z][A-Z0-9_]{0,19}-[1-9][0-9]{0,8}$/.test(value) ? value : null
}

// „Tickety naraz” (PT-375): "" — puste pole (ustawienie domyślne), 1..6 — liczba, null — błędny zapis.
// Zakres sprawdza też usługa (UpdateCard `maxTickets`); tu tylko podpowiedź przed wysłaniem.
function maxTickets(text) {
    var value = String(text || "").trim()
    if (!value) return ""
    if (!/^[0-9]+$/.test(value)) return null
    var number = parseInt(value, 10)
    return number >= 1 && number <= 6 ? number : null
}

// Priorytet karty (PT-377): wartości klucza `priority` (UpdateCard) w kolejności od najwyższego i ich etykiety.
var PRIORITIES = ["high", "normal", "low"]
var PRIORITY_LABELS = { high: "Wysoki", normal: "Normalny", low: "Niski" }

// Etykieta priorytetu albo "" dla wartości spoza listy.
function priorityLabel(value) {
    return PRIORITY_LABELS.hasOwnProperty(value) ? PRIORITY_LABELS[value] : ""
}

// Akcje karty: {id, label, effect, danger, input}. `label` nazywa skutek, `effect` to zdanie pod przyciskiem;
// `input` — akcja z polem (YouTrack, gałąź), `danger` — archiwizacja. Reguły przenoszenia podaje Tablica:
// `targets` — kolumny, do których wolno przenieść kartę (Logic.settingTargets), `canArchive` — czy wolno ją
// zarchiwizować, `branchLocked` — gałąź karty z przepływem ustala Tide, więc zmiany nie ma. Karta „Do sprawdzenia”
// dostaje „Odeślij do poprawy” i „Oznacz jako sprawdzoną”.
// `finish` — skutek zakończenia karty z przepływem z usługi (`flow.finish`: {steps, tickets}) albo null.
function finishEffect(finish) {
    if (!finish) return ""
    var parts = []
    if (finish.steps > 0) parts.push("zatrzyma " + finish.steps + " " + (finish.steps === 1 ? "trwający krok" : "trwające kroki"))
    if (finish.tickets > 0) parts.push("odrzuci " + finish.tickets + " " + (finish.tickets === 1 ? "niescalony ticket" : "niescalone tickety"))
    return parts.join(" i ")
}

function actions(cardId, column, youtrack, canPause, paused, targets, canArchive, branchLocked, finish) {
    var out = []
    var ending = finishEffect(finish)
    var review = column === "review"
    var allowed = targets || []
    if (column !== "backlog" && allowed.indexOf("backlog") >= 0)
        out.push({ id: "move:backlog", label: "Przenieś do Backlogu",
                   effect: finish ? "Karta wraca do Backlogu i zostaje wstrzymana; trwające kroki się kończą."
                                  : "Karta wraca do Backlogu; agent nie rusza jej, dopóki jej nie zlecisz." })
    if (column !== "todo" && allowed.indexOf("todo") >= 0)
        out.push({ id: "move:todo", label: review ? "Odeślij do poprawy" : "Przenieś do Do zrobienia",
                   effect: review ? "Karta wraca do Do zrobienia jako odesłana do poprawy." : "Karta trafia do Do zrobienia." })
    if (column !== "done" && allowed.indexOf("done") >= 0)
        out.push({ id: "move:done", label: review ? "Oznacz jako sprawdzoną" : "Przenieś do Gotowych",
                   effect: ending ? "Kończy kartę: " + ending + ". Zapyta o potwierdzenie." : "Karta trafia do Gotowych." })
    out.push({ id: "youtrack", input: true,
               label: youtrack ? "Zmień zgłoszenie w YouTracku" : "Powiąż ze zgłoszeniem w YouTracku",
               effect: "Stan zgłoszenia pójdzie za kartą." })
    if (youtrack)
        out.push({ id: "youtrack-clear", label: "Odłącz od zgłoszenia " + youtrack,
                   effect: "Stan zgłoszenia przestaje iść za kartą." })
    if (!branchLocked)
        out.push({ id: "branch", input: true, label: "Zmień gałąź karty",
                   effect: "Zapyta o potwierdzenie. Gałęzie w repozytorium zostają bez zmian." })
    if (canPause)
        out.push({ id: "pause", label: paused ? "Wznów kartę" : "Wstrzymaj kartę",
                   effect: paused ? "Karta rusza dalej." : "Trwające kroki kończą się, nowe nie startują." })
    if (canArchive)
        out.push({ id: "archive", danger: true, label: "Archiwizuj kartę",
                   effect: "Zapyta o potwierdzenie. Plik karty i historia zostają." })
    return out
}
