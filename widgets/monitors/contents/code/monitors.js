// Stan wejścia prawego monitora z tide-daemon (/Monitors Status, PT-38) na słowa kafelka i okna potwierdzenia.
// Wejście: {layout, target, available, error, interrupted, since, monitor, switching, result} (opis
// w daemon/tide_daemon/dbus_service.py) i to, czy usługa jest na magistrali. „Xbox” zmienia tylko wejście monitora
// przez DDC/CI, układ ekranów w KWin zostaje (decyzja 81). Kolory to nazwy ról z palety (Tokens[nazwa] w QML).
// Czyste funkcje — testy liczą je w qml6 (widgets/tests/test_widget_monitors.py).
.pragma library

var LABELS = { pc: "PC", xbox: "Xbox" }

function obj(value) {
    return value && typeof value === "object" ? value : {}
}

function label(layout) {
    return LABELS[layout] || ""
}

// Kafelek w docku Stream: {label, from, to, busy, muted, toolTip}. „PC → Xbox” jak w mocku: bieżące wejście
// i strzałka do drugiego w kolorze akcentu.
function tile(status, up) {
    const s = obj(status)
    const t = { label: "Monitory", from: "", to: "", busy: "", muted: false, toolTip: "" }
    if (!up) {
        t.busy = "usługa nie działa"
        t.muted = true
        t.toolTip = "tide-daemon nie działa — wejścia monitora nie da się przełączyć"
        return t
    }
    if (!s.layout) {
        t.busy = "czytam stan…"
        t.muted = true
        t.toolTip = "Czytam stan monitorów…"
        return t
    }
    if (s.switching) {
        t.busy = "przełączam na " + label(s.switching) + "…"
        t.muted = true
        t.toolTip = "Przełączam prawy monitor na " + label(s.switching) + "…"
        return t
    }
    if (s.available === false) {
        t.busy = "niedostępne"
        t.muted = true
        t.toolTip = "Wejścia monitora nie da się przełączyć: " + (s.error || "brak ddcutil")
        return t
    }
    const monitor = s.monitor || "DP-2"
    t.from = label(s.layout)
    t.to = "→ " + label(s.target)
    t.toolTip = "Teraz: " + t.from + " (" + monitor + (s.layout === "xbox" ? " na wejściu HDMI" : " na DisplayPort") + ")"
        + (s.interrupted ? "\nPoprzednie przełączenie zostało przerwane" : "")
        + "\nKliknij, aby przełączyć na " + label(s.target)
    return t
}

// Okno potwierdzenia (popup kafelka): {title, lines, button, enabled, note, error, target}.
function confirm(status, up) {
    const s = obj(status)
    const out = { title: "Monitory", lines: [], button: "", enabled: false, note: "", error: "", target: "" }
    if (!up) {
        out.lines = ["tide-daemon nie działa — przełączenie niedostępne."]
        return out
    }
    if (!s.layout) {
        out.lines = ["Czytam stan monitorów…"]
        return out
    }
    const monitor = s.monitor || "DP-2"
    out.target = s.target || ""
    if (s.target === "xbox") {
        out.title = "Przełączyć na Xbox?"
        out.lines = [monitor + " przejdzie na wejście HDMI z Xboxem (HD60 X).",
                     "Układ ekranów się nie zmienia: kursor i nowe okna mogą trafić na prawy ekran, którego wtedy nie widać.",
                     "Po wyjściu ze Stream monitor sam wróci na PC."]
        out.button = "Przełącz na Xbox"
    } else {
        out.title = "Wrócić do PC?"
        out.lines = [monitor + " wróci na DisplayPort."]
        if (s.interrupted)
            out.lines.push("Poprzednie przełączenie zostało przerwane — powrót do PC jest zawsze bezpieczny.")
        out.button = "Wróć do PC"
    }
    out.enabled = s.available !== false && !s.switching
    if (s.switching) out.button = "Przełączam…"
    if (s.available === false) out.error = "Niedostępne: " + (s.error || "brak ddcutil")
    const result = obj(s.result)
    if (result.ok === false && result.error) out.error = "Ostatnie przełączenie: " + result.error
    if (result.ok && result.auto && result.changed)
        out.note = "Po wyjściu ze Stream wejście wróciło na DisplayPort."
    else if (result.ok && result.note)
        out.note = "Ostatnie przełączenie: " + result.note + "."
    return out
}
