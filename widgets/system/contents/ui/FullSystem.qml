// Widok szczegółowy (popup, później pasek boczny na lewym monitorze): procesor z ostatniej
// minuty, pamięć z największymi programami, karta graficzna z VRAM, partycje z wolnym miejscem
// i podpowiedzią przy zajętości powyżej 85%, przyciski do Monitora systemu i analizy dysku.
// Bez ksystemstats zamiast zer jest wyciszony komunikat, a dyski mają liczby ze statvfs.
import QtQuick
import QtQuick.Layouts
import "../code/system.js" as Logic
import "tide" as Tide

ColumnLayout {
    id: full

    required property var source            // SystemSource
    // Gęsty układ dla paska bocznego (ok. 1000 px wysokości na cztery widżety): bez podpisów
    // pod wykresem, pamięcią i kartą graficzną, mniejsze odstępy i niższy wykres.
    property bool dense: false
    // Czy widok jest naprawdę na ekranie (popup otwarty albo widżet rozwinięty w panelu bocznym).
    readonly property bool shown: visible && Window.window !== null && Window.window.visible

    readonly property bool ready: source.sensorsReady
    readonly property var gpu: source.gpu
    readonly property string diskApp: source.apps.diskUsage || ""
    readonly property string monitorApp: source.apps.systemMonitor || ""

    // Co widać — osobno, żeby testy mogły to sprawdzić bez szukania elementów.
    readonly property bool showNotice: source.problem !== ""
    readonly property string noticeText: source.problem === "missing" ? "Zainstaluj pakiet ksystemstats"
        : source.problem === "stalled" ? "Brak danych z ksystemstats" : ""
    readonly property bool showCpu: ready
    readonly property bool showMemory: ready
    readonly property bool showGpu: ready && gpu.available
    readonly property bool showDisks: source.disks.length > 0
    readonly property bool showMonitorButton: monitorApp !== ""
    readonly property bool showDiskButton: diskApp !== ""

    spacing: dense ? 10 : 16

    component Caption: Text {
        Layout.fillWidth: true
        visible: text !== ""
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    // Nagłówek sekcji: nazwa, opcjonalny opis (np. „22 GiB z 78 GiB”) i duży procent w kolorze.
    component MetricHeader: RowLayout {
        id: header
        property string title: ""
        property string detail: ""
        property var percent: null
        property color accent: Tide.Tokens.accent

        Layout.fillWidth: true
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: header.title
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            elide: Text.ElideRight
        }
        Text {
            visible: header.detail !== ""
            text: header.detail
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        Text {
            text: Logic.percentText(header.percent)
            color: header.percent === null ? Tide.Tokens.textMuted : header.accent
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: "System"
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Text {
            visible: text !== ""
            text: full.source.info && full.source.info.hostname ? full.source.info.hostname : ""
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    // Brak ksystemstats: wyciszony komunikat zamiast pierścieni i wykresów z zerami.
    Tide.Card {
        id: notice
        Layout.fillWidth: true
        visible: full.showNotice
        padding: 10
        spacing: 2

        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: full.noticeText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        Caption {
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            text: full.source.problem === "missing"
                ? "Procesor, pamięć i karta graficzna biorą dane z jego czujników, tak jak Monitor systemu."
                : "Usługa nie odpowiada. Sprawdź: systemctl --user status plasma-ksystemstats"
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: full.showCpu
        spacing: 8

        MetricHeader {
            title: "Procesor"
            percent: full.source.cpuPercent
            accent: Tide.Tokens.teal
        }
        Tide.BarChart {
            Layout.fillWidth: true
            Layout.preferredHeight: full.dense ? 32 : 44
            slots: full.source.history.capacity
            values: full.source.history.samples.map(v => v === null ? null : v / 100)
            color: Tide.Tokens.teal
        }
        Caption {
            visible: !full.dense && text !== ""
            text: Logic.historyCaption(full.source.history.average, full.source.history.peak, full.source.historySeconds)
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: full.showMemory
        spacing: 8

        MetricHeader {
            title: "Pamięć"
            detail: full.source.memoryUsed === null ? ""
                : full.source.formatBytes(full.source.memoryUsed) + " z " + full.source.formatBytes(full.source.memoryTotal)
            percent: full.source.memoryPercent
            accent: Tide.Tokens.lavender
        }
        Tide.MeterBar {
            Layout.fillWidth: true
            value: Logic.fraction(full.source.memoryPercent)
            color: Tide.Tokens.lavender
        }
        Caption {
            visible: !full.dense && text !== ""
            text: Logic.processesLine(full.source.processes, full.source.formatBytes)
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: full.showGpu
        spacing: 8

        MetricHeader {
            title: "Karta graficzna"
            detail: full.gpu.usedVram === null ? ""
                : "VRAM " + full.source.formatBytes(full.gpu.usedVram) + " z " + full.source.formatBytes(full.gpu.totalVram)
            percent: full.gpu.usage
            accent: Tide.Tokens.mint
        }
        Tide.MeterBar {
            Layout.fillWidth: true
            value: Logic.fraction(full.gpu.usage)
            color: Tide.Tokens.mint
        }
        Caption {
            visible: !full.dense && text !== ""
            text: full.gpu.name
        }
    }

    Tide.Card {
        Layout.fillWidth: true
        visible: full.showDisks
        padding: 14
        spacing: 10

        Text {
            text: "Dyski"
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }

        Repeater {
            model: full.source.disks

            delegate: ColumnLayout {
                id: diskRow
                required property var modelData
                readonly property string hint: full.source.diskHint(modelData)

                Layout.fillWidth: true
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Text {
                        Layout.preferredWidth: Math.max(Math.ceil(mountWidth.width), implicitWidth)
                        text: diskRow.modelData.target
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                    Tide.MeterBar {
                        Layout.fillWidth: true
                        value: Logic.fraction(diskRow.modelData.usedPercent)
                        color: Tide.Tokens.peach
                    }
                    Text {
                        text: diskRow.modelData.free === null ? "—" : full.source.formatBytes(diskRow.modelData.free) + " wolne"
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: diskRow.hint !== ""
                    text: diskRow.hint
                    wrapMode: Text.WordWrap
                    color: Tide.Tokens.peach
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: full.showMonitorButton || full.showDiskButton
        spacing: 8

        ActionButton {
            Layout.fillWidth: true
            visible: full.showMonitorButton
            text: "Monitor systemu"
            onClicked: full.source.launch(full.monitorApp, "")
        }
        ActionButton {
            Layout.fillWidth: true
            visible: full.showDiskButton
            text: "Analiza dysku"
            // Filelight od razu na najpełniejszej partycji.
            onClicked: full.source.launch(full.diskApp, full.source.fullerDisk ? "file://" + full.source.fullerDisk.target : "")
        }
    }

    // Kolumna punktów montowania ma szerokość najdłuższego, żeby paski zaczynały się w jednym miejscu.
    TextMetrics {
        id: mountWidth
        text: full.source.disks.map(d => d.target).reduce((a, b) => b.length > a.length ? b : a, "")
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }
}
