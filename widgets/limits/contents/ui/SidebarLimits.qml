// Kafelek „AI” w pasku bocznym (do PT-41 „Limity AI”): dwa zwarte wiersze (GPT, Claude) — niższy z pozostałych
// limitów (sesja albo tydzień) z cienkim paskiem w kolorze poziomu (mięta, masło, róż), a pod
// paskiem wyciszone „tydzień · reset poniedziałek 20:00”, a za nim prognoza tego limitu
// z tide-daemon: „ · starczy do resetu” (wyciszone) albo „ · koniec ok. 17:40” (brzoskwinia,
// wyczerpie się przed resetem). Bez prognozy (za mało danych, brak usługi) nic nie dochodzi.
// Problemy z usługą to jedna wyciszona
// linia zamiast kart. Pełne karty usług (oba limity, logowanie, otwieranie stron) są w popupie
// kafelka w docku. Pod limitami wiersz modelu lokalnego (PT-41): „Lokalny ● qwen3:8b · na karcie 100%”
// słowami kafelka Modele (tide/models.js) i „Modele ↗” do widoku Modele w oknie Tide; kolor ostrzegawczy
// tylko przy niedziałającym serwerze, modelu częściowo na procesorze albo braku modelu roli. Bez usługi
// tide-daemon, przed pierwszym odczytem i bez serwera w ustawieniach wiersza nie ma.
// Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide/models.js" as ModelsLogic
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    required property var source            // LimitsSource (albo obiekt z tymi samymi funkcjami)
    // ModelsState z biblioteki (albo obiekt z snapshot i serviceUp); null — bez modelu lokalnego.
    property var models: null
    property string windowNote: ""          // notatka po nieudanym „Modele ↗” (okno bez widoku, limit czasu)

    signal openModelsRequested()            // okno Tide na widoku Modele

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
        const forecast = metric !== "" && typeof source.forecastFor === "function"
            ? source.forecastFor(s.key, metric) : null
        const short = state === "login" || state === "unavailable" ? "" : Tide.Texts.forecastShort(forecast)
        return {
            key: s.key,
            label: s.label,
            name: s.name,
            value: value,
            valueText: Tide.Texts.percent(value),
            note: noteFor(s.key, metric, state),
            tail: short !== "" ? " · " + short : "",
            runsOut: short !== "" && forecast.state === "runs-out",
            login: state === "login"
        }
    })
    // Model lokalny: {visible, word, tone, warning, text} (tide/models.js, indicator()).
    readonly property var local: models ? ModelsLogic.indicator(models.snapshot, models.serviceUp)
                                        : ModelsLogic.indicator(null, false)
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

    title: "AI"
    spacing: 8
    Accessible.name: "AI: " + rows.map(r => r.label + " " + r.valueText + r.tail).join(", ")
        + (local.visible ? ", model lokalny: " + local.text : "")

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
                noteTail: row.modelData.tail
                noteTailColor: row.modelData.runsOut ? Tide.Tokens.runout : Tide.Tokens.textMuted
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

    // Model lokalny: podpis w kolumnie podpisów mierników, kropka i stan słowami kafelka Modele.
    RowLayout {
        id: localRow
        objectName: "localRow"
        Layout.fillWidth: true
        visible: tile.local.visible
        spacing: 10

        Text {
            Layout.preferredWidth: labelMetrics.advanceWidth
            text: "Lokalny"
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            font.weight: Font.Medium
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            LocalDot {
                id: localDot
                objectName: "localDot"
                Layout.alignment: Qt.AlignVCenter
                tone: tile.local.tone
            }
            Text {
                objectName: "localText"
                Layout.fillWidth: true
                text: tile.local.text
                textFormat: Text.PlainText
                color: tile.local.warning ? localDot.toneColor : Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
                elide: Text.ElideMiddle
            }
        }
        Tide.TextButton {
            objectName: "openModels"
            text: "Modele ↗"
            Accessible.name: "Otwórz widok Modele w oknie Tide"
            onClicked: tile.openModelsRequested()
        }
    }

    Text {
        objectName: "windowNote"
        Layout.fillWidth: true
        visible: tile.local.visible && tile.windowNote !== ""
        text: tile.windowNote
        textFormat: Text.PlainText
        color: Tide.Tokens.neutral
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
        wrapMode: Text.WordWrap
    }

    TextMetrics {
        id: labelMetrics
        // Wspólna szerokość podpisów: „Lokalny” tylko wtedy, gdy wiersz modelu jest widoczny.
        text: tile.local.visible ? "Lokalny" : "Claude"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        font.weight: Font.Medium
    }
}
