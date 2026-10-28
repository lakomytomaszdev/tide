// Stan układu monitorów z tide-daemon (/Monitors Status, PT-38) na słowa kafelka i okna potwierdzenia.
// Wejście: {layout, target, available, error, saved, savedAt, ddc, notices, monitor, primary, disableMonitor,
// switching, result} (opis w daemon/tide_daemon/dbus_service.py) i to, czy usługa jest na magistrali.
// Kolory to nazwy ról z palety (Tokens[nazwa] w QML). Czyste funkcje — testy liczą je w qml6
// (widgets/tests/test_widget_monitors.py).
.pragma library

var LABELS = { pc: "PC", xbox: "Xbox" }

function obj(value) {
    return value && typeof value === "object" ? value : {}
}

function label(layout) {
    return LABELS[layout] || ""
}

// „2026-10-28T21:14:05+01:00” → „28.10 21:14” (czas lokalny zapisany przez usługę, bez przeliczania strefy).
function shortTime(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ""))
    return m ? m[3] + "." + m[2] + " " + m[4] + ":" + m[5] : ""
}

// Kafelek w docku Stream: {label, from, to, busy, muted, toolTip}. „PC → Xbox” jak w mocku: bieżący układ
// i strzałka do drugiego w kolorze akcentu.
function tile(status, up) {
    const s = obj(status)
    const t = { label: "Monitory", from: "", to: "", busy: "", muted: false, toolTip: "" }
    if (!up) {
        t.busy = "usługa nie działa"
        t.muted = true
        t.toolTip = "tide-daemon nie działa — układu monitorów nie da się przełączyć"
        return t
    }
    if (!s.layout) {
        t.busy = "czytam układ…"
        t.muted = true
        t.toolTip = "Czytam układ monitorów…"
        return t
    }
    if (s.switching) {
        t.busy = "przełączam na " + label(s.switching) + "…"
        t.muted = true
        t.toolTip = "Przełączam monitory na " + label(s.switching) + "…"
        return t
    }
    if (s.available === false) {
        t.busy = "niedostępne"
        t.muted = true
        t.toolTip = "Układu monitorów nie da się przełączyć: " + (s.error || "brak kscreen-doctor")
        return t
    }
    t.from = label(s.layout)
    t.to = "→ " + label(s.target)
    const primary = s.layout === "xbox" ? s.primary : s.monitor
    t.toolTip = "Teraz: " + t.from + (primary ? " (główny " + primary + ")" : "")
        + "\nKliknij, aby przełączyć na " + label(s.target)
    return t
}

// Okno potwierdzenia (popup kafelka): {title, lines, button, enabled, notices, error, target}.
function confirm(status, up) {
    const s = obj(status)
    const out = { title: "Monitory", lines: [], button: "", enabled: false, notices: [], error: "", target: "" }
    if (!up) {
        out.lines = ["tide-daemon nie działa — przełączenie niedostępne."]
        return out
    }
    if (!s.layout) {
        out.lines = ["Czytam układ monitorów…"]
        return out
    }
    const monitor = s.monitor || "DP-2"
    const primary = s.primary || "HDMI-A-1"
    out.target = s.target || ""
    if (s.target === "xbox") {
        out.title = "Przełączyć na Xbox?"
        out.lines = [monitor + " przejdzie na wejście HDMI z Xboxem (HD60 X).", primary + " zostanie monitorem głównym."]
        if (s.disableMonitor !== false)
            out.lines.push(monitor + " zniknie z pulpitu, a jego okna przejdą na monitor główny.")
        out.button = "Przełącz na Xbox"
    } else {
        out.title = "Wrócić do PC?"
        out.lines = [monitor + " wróci na DisplayPort."]
        const when = shortTime(s.savedAt)
        out.lines.push(s.saved ? "Układ ekranów wróci do zapisanego" + (when ? " " + when : "") + "."
                               : "Brak zapisanego układu — " + monitor + " wróci jako główny w preferowanym trybie.")
        out.button = "Wróć do PC"
    }
    out.enabled = s.available !== false && !s.switching
    if (s.switching) out.button = "Przełączam…"
    if (s.available === false) out.error = "Niedostępne: " + (s.error || "brak kscreen-doctor")
    const result = obj(s.result)
    if (result.ok === false && result.error) out.error = "Ostatnie przełączenie: " + result.error
    out.notices = (result.ok ? result.notices || [] : []).filter(n => n.indexOf("już jest") < 0)
    return out
}
