// Kafelek „AI” w docku (PT-41: Limity AI i model lokalny w jednym kafelku): etykieta „AI”, pod nią
// „GPT 91%  Claude 86%” z cienkim paskiem pod każdym — ile zostało, niższy z limitów (sesja, tydzień) —
// i na końcu „● lokalny”: kropka modelu lokalnego z tide-daemon (/Models) w tej samej linii co procenty.
// Kolor paska według poziomu (mięta, masło, róż), brak danych = „—”. Kafelek zabarwia poziom niższego
// z limitów. Model lokalny jest spokojny, gdy działa (wyciszone słowo, kropka mięta albo teal przy pracy);
// kolor ostrzegawczy i dłuższe słowo tylko wtedy, gdy domyślny serwer nie odpowiada („lokalny nie działa”,
// róż), model nie mieści się na karcie („lokalny na CPU”, masło) albo brakuje modelu roli („lokalny bez
// modelu”, masło). Bez usługi, przed pierwszym odczytem i bez serwera w ustawieniach kropki nie ma.
// Gdy tide-daemon prognozuje wyczerpanie przed resetem, przy etykiecie pojawia się krótko
// „· Claude do 17:40” w brzoskwini (najwcześniejsze z limitów); „starczy” nic nie dokłada,
// żeby kafelek nie rósł — pełne prognozy i stan modeli są w popupie i podpowiedzi.
import QtQuick
import "tide/models.js" as ModelsLogic
import "tide" as Tide

Tide.DockTile {
    id: tile

    required property var source            // LimitsSource (albo obiekt z panelValue i available)
    // ModelsState z biblioteki (albo obiekt z snapshot i serviceUp); null — bez modelu lokalnego.
    property var models: null
    property bool vertical: false
    property color textColor: Tide.Tokens.textPrimary

    readonly property var services: [
        { key: "chatgpt", label: "GPT" },
        { key: "claude", label: "Claude" }
    ]
    readonly property var values: services.map(s => source.panelValue(s.key))
    readonly property var lowest: {
        const known = values.filter(v => v !== null && v !== undefined)
        return known.length ? Math.min.apply(null, known) : null
    }
    // Teksty mierników, np. „GPT 91%” (dla testów i dostępności).
    readonly property var lines: services.map((s, i) => s.label + " " + Tide.Texts.percent(values[i]))
    // Najwcześniejsze wyczerpanie przed resetem spośród usług i okien: {label, forecast} albo null.
    readonly property var runout: {
        let best = null
        if (typeof source.forecastFor !== "function") return null
        for (const s of services) {
            for (const metric of ["session", "weekly"]) {
                const f = source.forecastFor(s.key, metric)
                if (f && f.state === "runs-out" && (best === null || f.exhaustTs < best.forecast.exhaustTs))
                    best = { label: s.label, forecast: f }
            }
        }
        return best
    }
    // Model lokalny: {visible, word, tone, warning, text} (tide/models.js, indicator()).
    readonly property var local: models ? ModelsLogic.indicator(models.snapshot, models.serviceUp)
                                        : ModelsLogic.indicator(null, false)

    label: "AI"
    detail: runout ? Tide.Texts.limitsRunoutDetail(runout.label, runout.forecast) : ""
    detailColor: Tide.Tokens.runout
    tint: Tide.Theme.levelColor(lowest)
    Accessible.name: "AI: " + lines.join(", ")
        + (runout ? ", " + runout.label + " " + runout.forecast.text : "")
        + (local.visible ? ", model lokalny: " + local.text : "")

    Grid {
        columns: tile.vertical ? 1 : tile.services.length + (tile.local.visible ? 1 : 0)
        columnSpacing: 10
        rowSpacing: 4

        Repeater {
            model: tile.services

            delegate: Tide.TileMeter {
                required property var modelData
                required property int index
                objectName: "meter"
                // Bez usługi Limity AI przygasają same mierniki, gdy obok żyje model lokalny (main.qml
                // przygasza wtedy cały kafelek tylko bez kropki).
                opacity: tile.source.available === false && tile.local.visible ? 0.45 : 1
                label: modelData.label
                percent: tile.values[index]
                color: Tide.Theme.levelColor(percent)
                textColor: tile.textColor
                valuePointSize: tile.valuePointSize
            }
        }

        // „● lokalny” w linii procentów: słowo krojem podpisów mierników („GPT”) i na tej samej wysokości
        // (TileMeter stawia podpis u góry, a procent na jego linii bazowej).
        Item {
            id: localItem
            objectName: "localIndicator"
            visible: tile.local.visible
            implicitWidth: localWord.x + localWord.implicitWidth
            implicitHeight: localWord.implicitHeight

            LocalDot {
                id: localDot
                objectName: "localDot"
                anchors.verticalCenter: localWord.verticalCenter
                tone: tile.local.tone
            }
            Text {
                id: localWord
                objectName: "localWord"
                x: localDot.width + 5
                text: tile.local.word
                textFormat: Text.PlainText
                color: tile.local.warning ? localDot.toneColor : Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
                font.weight: Font.Medium
            }
        }
    }
}
