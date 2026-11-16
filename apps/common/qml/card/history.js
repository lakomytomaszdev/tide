// Czas wiersza historii karty (HistoryBlock.qml, PT-503): „dziś 14:20”, „wczoraj 14:20”, starsze „24.10 14:20”,
// z „5 min temu” obok dla świeżych zdarzeń. Wydzielone z CardFullView.qml, żeby ten sam blok działał też w panelu
// karty na Tablicy (K-128 #5). `ts` to sekundy epoki z `flow.history[]`.
.pragma library

function absoluteTime(ts) {
    if (!ts) return ""
    const d = new Date(ts * 1000)
    if (isNaN(d.getTime())) return ""
    const now = new Date()
    const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
    const days = Math.round((midnight(now) - midnight(d)) / 86400000)
    const time = Qt.formatDateTime(d, "HH:mm")
    return days <= 0 ? "dziś " + time : days === 1 ? "wczoraj " + time : Qt.formatDateTime(d, "d.MM HH:mm")
}

function relativeTime(ts) {
    if (!ts) return ""
    const minutes = Math.floor((Date.now() / 1000 - ts) / 60)
    if (minutes < 1) return "przed chwilą"
    if (minutes < 60) return minutes + " min temu"
    const hours = Math.floor(minutes / 60)
    return hours < 24 ? hours + " godz. temu" : ""
}

function when(ts) {
    return [absoluteTime(ts), relativeTime(ts)].filter(x => !!x).join(" · ")
}
