// Kafelek Limitów AI w docku: etykieta „Limity AI”, pod nią „GPT 91%  Claude 86%” z cienkim
// paskiem pod każdym — ile zostało, niższy z limitów (sesja, tydzień). Kolor paska według
// poziomu (mięta, masło, róż), brak danych = „—”. Kafelek zabarwia poziom niższego z limitów.
import QtQuick
import "tide" as Tide

Tide.DockTile {
    id: tile

    required property var source            // LimitsSource (albo obiekt z panelValue i available)
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

    label: "Limity AI"
    tint: Tide.Theme.levelColor(lowest)
    Accessible.name: "Limity AI: " + lines.join(", ")

    Grid {
        columns: tile.vertical ? 1 : tile.services.length
        columnSpacing: 10
        rowSpacing: 4

        Repeater {
            model: tile.services

            delegate: Tide.TileMeter {
                required property var modelData
                required property int index
                label: modelData.label
                percent: tile.values[index]
                color: Tide.Theme.levelColor(percent)
                textColor: tile.textColor
                valuePointSize: tile.valuePointSize
            }
        }
    }
}
