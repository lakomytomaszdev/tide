// Kafelek System w pasku bocznym: trzy mierniki (CPU, RAM, Dysk) z procentem i cienkim paskiem,
// pod procesorem mała historia z ostatniej minuty (28 px zamiast wysokiego wykresu z popupu),
// karta graficzna w jednej linii, partycje po jednej linii z paskiem i wolnym miejscem,
// a przy zajętości powyżej 85% jedna krótka, wyciszona linia ostrzeżenia. Pełne szczegóły
// (procesy, podpisy, nazwa karty) zostają w popupie kafelka w docku (FullSystem).
import QtQuick
import QtQuick.Layouts
import "../code/system.js" as Logic
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    required property var source            // SystemSource
    // Czy kafelek jest naprawdę na ekranie — od tego zależą czujniki karty graficznej.
    readonly property bool shown: visible && Window.window !== null && Window.window.visible

    readonly property bool ready: source.sensorsReady
    readonly property var gpu: source.gpu
    readonly property string diskApp: source.apps.diskUsage || ""
    readonly property string monitorApp: source.apps.systemMonitor || ""

    // Co widać i jakie teksty — osobno, żeby testy mogły to sprawdzić bez szukania elementów.
    readonly property string noticeText: source.problem === "missing" ? "Zainstaluj pakiet ksystemstats"
        : source.problem === "stalled" ? "Brak danych z ksystemstats" : ""
    readonly property bool showGpu: ready && gpu.available
    readonly property string gpuText: Logic.gpuLine(gpu.usage, gpu.usedVram, gpu.totalVram)
    readonly property var diskLines: source.disks.map(d => d.target + " " + freeText(d))
    readonly property string diskNote: Logic.diskNote(source.disks, source.worktrees, source.targets,
                                                      Tide.Theme.formatBytes)
    // Długie punkty montowania nie zjadają paska: najwyżej 96 px, dalej wielokropek.
    readonly property real labelWidth: Math.ceil(Math.min(96, Math.max(labelMetrics.advanceWidth,
                                                                       mountMetrics.advanceWidth)))
    readonly property real valueWidth: Math.ceil(valueMetrics.advanceWidth)

    function freeText(disk) {
        return disk.free === null || disk.free === undefined ? "—" : Tide.Theme.formatBytes(disk.free) + " wolne"
    }

    title: "System"
    spacing: 10
    Accessible.name: "System: " + Logic.toolTip(source.problem === "missing", source.cpuPercent,
                                                source.memoryPercent, source.fullerDisk)

    headerItems: Tide.TextButton {
        visible: tile.monitorApp !== ""
        text: "Monitor systemu"
        onClicked: tile.source.launch(tile.monitorApp, "")
    }

    // Brak ksystemstats: jedna wyciszona linia zamiast mierników z zerami.
    Text {
        Layout.fillWidth: true
        visible: tile.noticeText !== ""
        text: tile.noticeText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: tile.ready
        spacing: 6

        Tide.SidebarMeter {
            id: cpuMeter
            Layout.fillWidth: true
            label: "CPU"
            value: Logic.fraction(tile.source.cpuPercent)
            valueText: Logic.percentText(tile.source.cpuPercent)
            color: Tide.Tokens.teal
            labelWidth: tile.labelWidth
            valueWidth: tile.valueWidth
        }
        Tide.BarChart {
            Layout.fillWidth: true
            Layout.leftMargin: cpuMeter.barX
            Layout.rightMargin: tile.valueWidth + 10
            Layout.preferredHeight: 28
            slots: tile.source.history.capacity
            values: tile.source.history.samples.map(v => v === null ? null : v / 100)
            color: Tide.Tokens.teal
            spacing: 1.5
            minimumBar: 1
            olderOpacity: 0.35
        }
        Tide.SidebarMeter {
            Layout.fillWidth: true
            Layout.topMargin: 2
            label: "RAM"
            value: Logic.fraction(tile.source.memoryPercent)
            valueText: Logic.percentText(tile.source.memoryPercent)
            color: Tide.Tokens.lavender
            labelWidth: tile.labelWidth
            valueWidth: tile.valueWidth
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: cpuMeter.barX
            visible: tile.showGpu
            text: tile.gpuText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 2
            elide: Text.ElideRight
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: tile.source.disks.length > 0
        spacing: 6

        Tide.SidebarMeter {
            Layout.fillWidth: true
            label: "Dysk"
            value: tile.source.fullerDisk ? Logic.fraction(tile.source.fullerDisk.usedPercent) : null
            valueText: tile.source.fullerDisk ? Logic.percentText(tile.source.fullerDisk.usedPercent) : "—"
            color: Tide.Tokens.peach
            labelWidth: tile.labelWidth
            valueWidth: tile.valueWidth
            // Jedna partycja: jej wolne miejsce pod paskiem zamiast drugiego, takiego samego paska.
            note: tile.source.disks.length === 1 ? tile.diskLines[0] : ""
        }
        Repeater {
            model: tile.source.disks.length > 1 ? tile.source.disks : []

            delegate: Tide.SidebarMeter {
                required property var modelData
                Layout.fillWidth: true
                label: modelData.target
                value: Logic.fraction(modelData.usedPercent)
                valueText: tile.freeText(modelData)
                valueColor: Tide.Tokens.textSecondary
                monoValue: false
                color: Tide.Theme.alpha(Tide.Tokens.peach, 0.7)
                labelWidth: tile.labelWidth
                valueWidth: tile.valueWidth
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: tile.diskNote !== ""
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: tile.diskNote
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 2
                elide: Text.ElideRight
            }
            Tide.TextButton {
                visible: tile.diskApp !== ""
                text: "Analiza dysku"
                pointSize: Tide.Tokens.fontSizePt - 2
                // Filelight od razu na najpełniejszej partycji.
                onClicked: tile.source.launch(tile.diskApp, tile.source.fullerDisk
                                              ? "file://" + tile.source.fullerDisk.target : "")
            }
        }
    }

    // Wspólna szerokość podpisów (najdłuższy z „Dysk” i punktów montowania) i wartości
    // („999 GB wolne”), żeby wszystkie paski zaczynały się i kończyły w jednym miejscu.
    TextMetrics {
        id: labelMetrics
        text: "Dysk"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        font.weight: Font.Medium
    }
    TextMetrics {
        id: mountMetrics
        text: tile.source.disks.map(d => d.target).reduce((a, b) => b.length > a.length ? b : a, "")
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        font.weight: Font.Medium
    }
    TextMetrics {
        id: valueMetrics
        text: "999 GB wolne"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }
}
