// Kafelek System w docku: etykieta „System”, pod nią trzy mierniki „CPU 34%  RAM 61%  Dysk 88%”
// z cienkim paskiem pod każdym — procesor (teal), pamięć (lawenda) i pełniejsza z partycji
// (brzoskwinia). Bez pierścieni: w 48 px kafelka miały ok. 26 px, a procent w środku był
// nieczytelny. Procent obok podpisu ma ten sam rozmiar co wartości innych kafelków.
// W pionowym panelu mierniki stoją jeden pod drugim. Bez danych pasek jest pusty, a procent to „—”.
import QtQuick
import "../code/system.js" as Logic
import "tide" as Tide

Tide.DockTile {
    id: compact

    required property var source            // SystemSource (albo obiekt z tymi samymi polami)
    property bool vertical: false
    property color textColor: Tide.Tokens.textPrimary

    readonly property var meters: [
        { label: "CPU", percent: source.cpuPercent, color: Tide.Tokens.teal },
        { label: "RAM", percent: source.memoryPercent, color: Tide.Tokens.lavender },
        { label: "Dysk", percent: source.fullerDisk ? source.fullerDisk.usedPercent : null, color: Tide.Tokens.peach }
    ]
    // Teksty mierników w kolejności wyświetlania, np. „CPU 34%” (dla testów i dostępności).
    readonly property var lines: meters.map(m => m.label + " " + Logic.percentText(m.percent))

    label: "System"
    tint: Tide.Tokens.sky
    Accessible.name: "System: " + lines.join(", ")

    Grid {
        columns: compact.vertical ? 1 : compact.meters.length
        columnSpacing: 10
        rowSpacing: 4

        Repeater {
            model: compact.meters

            delegate: Tide.TileMeter {
                required property var modelData
                label: modelData.label
                percent: modelData.percent
                color: modelData.color
                textColor: compact.textColor
                valuePointSize: compact.valuePointSize
            }
        }
    }
}
