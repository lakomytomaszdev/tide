// Kafelek Limitów AI w pasku bocznym: dwa zwarte wiersze (GPT, Claude) — niższy z pozostałych
// limitów (sesja albo tydzień) z cienkim paskiem w kolorze poziomu (mięta, masło, róż), a pod
// paskiem wyciszone „tydzień · reset poniedziałek 20:00”. Problemy z usługą to jedna wyciszona
// linia zamiast kart. Pełne karty usług (oba limity, logowanie, otwieranie stron) są w popupie
// kafelka w docku. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    required property var source            // LimitsSource (albo obiekt z tymi samymi funkcjami)

    readonly property var services: [
        { key: "chatgpt", label: "GPT", name: "ChatGPT" },
        { key: "claude", label: "Claude", name: "Claude" }
    ]
    readonly property var snapshot: source.snapshot || {}
    readonly property string notice: !source.configured ? "Brak tokenu usługi Limity AI"
        : source.checked && !source.available ? "Usługa Limity AI nie działa"
        : source.available && snapshot.paused === true ? "Odczyt wstrzymany podczas pełnego ekranu"
        : ""
    // Wiersze z gotowymi tekstami — z nich rysujemy i to samo sprawdzają testy.
    readonly property var rows: services.map(s => {
        const value = source.panelValue(s.key)
        const metric = lowerMetric(s.key)
        const state = source.available ? (source.service(s.key).state || "checking") : "offline"
        return {
            key: s.key,
            label: s.label,
            name: s.name,
            value: value,
            valueText: Tide.Texts.percent(value),
            note: noteFor(s.key, metric, state),
            login: state === "login"
        }
    })
    readonly property string readTime: {
        const times = services.map(s => source.lastRead(s.key)).filter(t => !!t).sort()
        return times.length ? times[times.length - 1] : ""
    }

    // Który limit jest niższy: "session", "weekly" albo "" bez danych.
    function lowerMetric(key) {
        const session = source.remaining(key, "session")
        const weekly = source.remaining(key, "weekly")
        if (session === null && weekly === null) return ""
        if (weekly === null) return "session"
        if (session === null) return "weekly"
        return session < weekly ? "session" : "weekly"
    }
    function noteFor(key, metric, state) {
        if (state === "login") return "nie zalogowano"
        if (state === "unavailable") return "nie udało się odczytać"
        if (metric === "") return state === "checking" ? "sprawdzanie…" : "brak danych"
        const reset = source.reset(key, metric)
        return (metric === "session" ? "sesja" : "tydzień") + (reset ? " · reset " + reset : "")
    }

    title: "Limity AI"
    spacing: 8
    Accessible.name: "Limity AI: " + rows.map(r => r.label + " " + r.valueText).join(", ")

    headerItems: Text {
        visible: tile.readTime !== "" && tile.notice === ""
        text: "odczyt " + tile.readTime
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
    }

    Text {
        Layout.fillWidth: true
        visible: tile.notice !== ""
        text: tile.notice
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    Repeater {
        model: tile.source.available ? tile.rows : []

        delegate: RowLayout {
            id: row
            required property var modelData
            Layout.fillWidth: true
            spacing: 8

            Tide.SidebarMeter {
                Layout.fillWidth: true
                label: row.modelData.label
                value: row.modelData.value === null ? null : row.modelData.value / 100
                valueText: row.modelData.valueText
                color: Tide.Theme.levelColor(row.modelData.value)
                valueColor: Tide.Theme.levelColor(row.modelData.value)
                note: row.modelData.note
                labelWidth: labelMetrics.advanceWidth
            }
            // Logowanie odbywa się w zwykłym Firefoksie (POST /open), usługa sama pobierze ciasteczka.
            Tide.TextButton {
                visible: row.modelData.login
                Layout.alignment: Qt.AlignTop
                text: "Zaloguj"
                Accessible.name: "Zaloguj się do " + row.modelData.name + " w Firefoksie"
                onClicked: tile.source.open(row.modelData.key)
            }
        }
    }

    TextMetrics {
        id: labelMetrics
        text: "Claude"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        font.weight: Font.Medium
    }
}
