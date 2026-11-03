// Rzadkie ustawienia pełnej karty (CardFullView.qml, PT-134): lista akcji wspólna dla sekcji „Ustawienia karty”
// i menu „Karta ▾” oraz klucz zgłoszenia YouTracka. Widok niczego nie liczy poza podpisami przycisków: kolumnę,
// wstrzymanie i gałąź podaje usługa w migawce.
.pragma library

var COLUMN_NAMES = { "backlog": "Backlog", "todo": "Do zrobienia", "done": "Gotowe" }

function columnName(id) {
    return COLUMN_NAMES[id] || id
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

// Akcje karty: {id, label, effect, danger, input}. `label` nazywa skutek, `effect` to zdanie pod przyciskiem;
// `input` — akcja z polem (YouTrack, gałąź), `danger` — archiwizacja. Kolumny: bez bieżącej; karta „Do sprawdzenia”
// dostaje „Odeślij do poprawy” i „Oznacz jako sprawdzoną”.
function actions(cardId, column, youtrack, canPause, paused) {
    var out = []
    var review = column === "review"
    if (column !== "backlog")
        out.push({ id: "move:backlog", label: "Przenieś do Backlogu",
                   effect: "Karta wraca do Backlogu; agent nie rusza jej, dopóki jej nie zlecisz." })
    if (column !== "todo")
        out.push({ id: "move:todo", label: review ? "Odeślij do poprawy" : "Przenieś do Do zrobienia",
                   effect: review ? "Karta wraca do Do zrobienia jako odesłana do poprawy." : "Karta trafia do Do zrobienia." })
    if (column !== "done")
        out.push({ id: "move:done", label: review ? "Oznacz jako sprawdzoną" : "Przenieś do Gotowych",
                   effect: "Karta trafia do Gotowych." })
    out.push({ id: "youtrack", input: true,
               label: youtrack ? "Zmień zgłoszenie w YouTracku" : "Powiąż ze zgłoszeniem w YouTracku",
               effect: "Stan zgłoszenia pójdzie za kartą. Puste pole odłącza kartę od zgłoszenia." })
    out.push({ id: "branch", input: true, label: "Zmień gałąź karty",
               effect: "Zapyta o potwierdzenie. Gałęzie w repozytorium zostają bez zmian." })
    if (canPause)
        out.push({ id: "pause", label: paused ? "Wznów kartę" : "Wstrzymaj kartę",
                   effect: paused ? "Karta rusza dalej." : "Trwające kroki kończą się, nowe nie startują." })
    out.push({ id: "archive", danger: true, label: "Archiwizuj kartę",
               effect: "Zapyta o potwierdzenie. Plik karty i historia zostają." })
    return out
}
