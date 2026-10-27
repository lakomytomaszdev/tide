// Wiersz kontenera (mock „Tide Docker”): kropka stanu, nazwa, obraz i stan słowami (róż — padł),
// porty (HTTP jako link „:8080 ↗”, inne jako „5434→5432”), CPU i RAM ze Stats(), przyciski akcji:
// działający — Logi, Powłoka, Restart, Zatrzymaj; zatrzymany — Logi, Uruchom, Usuń (z potwierdzeniem
// w stronie). W trakcie akcji przyciski są wyłączone, a w drugiej linii widać, co się dzieje; wynik
// (albo błąd po polsku) zostaje w trzeciej linii. Sam nie rozmawia z usługą.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "docker.js" as Logic

Rectangle {
    id: row

    property var container: ({})
    property real nowTs: Date.now() / 1000
    property var stats: null                 // wpis ze Stats() albo null
    property bool statsKnown: false          // Stats() już odpowiedziało
    property bool fullName: false            // w „Zatrzymane” pełna nazwa (bez nagłówka projektu)
    property bool logsOpen: false
    property bool flash: false               // podświetlenie po showItem
    property string busyAction: ""           // start|stop|restart|remove albo ""
    property bool locked: false              // akcja na całym projekcie trwa
    property var note: null                  // {text, error} — wynik ostatniej akcji

    signal logsRequested()
    signal shellRequested()
    signal restartRequested()
    signal stopRequested()
    signal startRequested()
    signal removeRequested()
    signal linkRequested(string url)

    readonly property bool running: container.running === true
    readonly property var line: Logic.stateLine(container, nowTs)
    readonly property var portList: Logic.ports(container)
    readonly property bool idle: busyAction === "" && !locked

    implicitHeight: Math.max(48, body.implicitHeight + 16)
    radius: Tide.Tokens.radiusTile
    color: logsOpen ? Tide.Theme.alpha(Tide.Tokens.done, 0.07)
         : flash ? Tide.Theme.alpha(Tide.Tokens.accent, 0.10) : Tide.Tokens.backgroundView
    border.width: 1
    border.color: logsOpen ? Tide.Theme.alpha(Tide.Tokens.done, 0.30)
                : flash ? Tide.Theme.alpha(Tide.Tokens.accent, 0.55) : "transparent"
    Behavior on color { ColorAnimation { duration: 250 } }
    Behavior on border.color { ColorAnimation { duration: 250 } }

    Accessible.role: Accessible.ListItem
    Accessible.name: (container.name || "") + ", " + line.text

    RowLayout {
        id: body
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 12

        StateDot {
            Layout.preferredWidth: 10
            Layout.alignment: Qt.AlignVCenter
            kind: Logic.dotKind(row.container)
            opacity: row.running ? 1 : 0.8
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 80
            spacing: 3
            opacity: row.running || row.container.failed ? 1 : 0.8
            Text {
                Layout.fillWidth: true
                text: row.fullName ? (row.container.name || "") : (row.container.displayName || row.container.name || "")
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Text {
                objectName: "stateLine"
                Layout.fillWidth: true
                text: row.busyAction !== "" ? (Logic.BUSY_VERB[row.busyAction] || "…") : row.line.text
                textFormat: Text.PlainText
                color: row.busyAction !== "" ? Tide.Tokens.accent
                     : row.line.tone === "bad" ? Tide.Tokens.negative
                     : row.line.tone === "warn" ? Tide.Tokens.neutral : Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Text {
                objectName: "actionNote"
                Layout.fillWidth: true
                visible: row.note !== null && row.busyAction === ""
                text: row.note ? row.note.text : ""
                textFormat: Text.PlainText
                color: row.note && row.note.error ? Tide.Tokens.negative : Tide.Tokens.positive
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
            }
        }

        // Porty: linki do HTTP, reszta jako zwykłe znaczniki.
        Flow {
            Layout.preferredWidth: 130
            Layout.alignment: Qt.AlignVCenter
            spacing: 6
            Repeater {
                model: row.portList.slice(0, Logic.MAX_PORTS)
                Rectangle {
                    id: chip
                    required property var modelData
                    implicitWidth: chipText.implicitWidth + 12
                    implicitHeight: chipText.implicitHeight + 4
                    radius: 6
                    color: modelData.http ? Tide.Theme.alpha(Tide.Tokens.accent, chipArea.containsMouse ? 0.18 : 0.10)
                                          : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                    activeFocusOnTab: modelData.http
                    border.width: activeFocus ? 1 : 0
                    border.color: Tide.Tokens.accent
                    Accessible.role: modelData.http ? Accessible.Link : Accessible.StaticText
                    Accessible.name: modelData.http ? "Otwórz " + modelData.url : "port " + modelData.title
                    Accessible.onPressAction: if (modelData.http) row.linkRequested(modelData.url)
                    Keys.onReturnPressed: if (modelData.http) row.linkRequested(modelData.url)
                    Keys.onSpacePressed: if (modelData.http) row.linkRequested(modelData.url)
                    Text {
                        id: chipText
                        anchors.centerIn: parent
                        text: chip.modelData.label + (chip.modelData.http ? " ↗" : "")
                        textFormat: Text.PlainText
                        color: chip.modelData.http ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    MouseArea {
                        id: chipArea
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: chip.modelData.http
                        cursorShape: Qt.PointingHandCursor
                        onClicked: row.linkRequested(chip.modelData.url)
                    }
                    QQC2.ToolTip.visible: chipArea.containsMouse
                    QQC2.ToolTip.text: chip.modelData.http ? chip.modelData.url : chip.modelData.title
                    QQC2.ToolTip.delay: 400
                }
            }
            Text {
                visible: row.portList.length > Logic.MAX_PORTS
                text: "+" + (row.portList.length - Logic.MAX_PORTS)
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
            }
        }

        Text {
            objectName: "cpu"
            Layout.preferredWidth: 50
            horizontalAlignment: Text.AlignRight
            text: !row.running ? "" : row.stats ? Logic.percent(row.stats.cpuPercent) : row.statsKnown ? "—" : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
        }
        Text {
            objectName: "ram"
            Layout.preferredWidth: 64
            horizontalAlignment: Text.AlignRight
            text: !row.running ? "" : row.stats ? Logic.bytes(row.stats.memBytes) : row.statsKnown ? "—" : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
        }

        Row {
            Layout.preferredWidth: 124
            Layout.alignment: Qt.AlignVCenter
            layoutDirection: Qt.RightToLeft
            spacing: 5

            // Od prawej (RightToLeft): działający — Zatrzymaj, Restart, Powłoka, Logi;
            // zatrzymany — Usuń, Uruchom, Logi.
            IconButton {
                visible: row.running
                icon: "stop"
                name: "Zatrzymaj"
                enabled: row.idle
                onClicked: row.stopRequested()
            }
            IconButton {
                visible: row.running
                icon: "restart"
                name: "Restart"
                enabled: row.idle
                onClicked: row.restartRequested()
            }
            IconButton {
                visible: row.running
                icon: "shell"
                name: "Powłoka w Konsoli"
                enabled: row.container.state !== "paused"
                onClicked: row.shellRequested()
            }
            IconButton {
                visible: !row.running
                icon: "trash"
                name: "Usuń kontener"
                tint: Tide.Tokens.negative
                enabled: row.idle
                onClicked: row.removeRequested()
            }
            IconButton {
                visible: !row.running
                icon: "play"
                name: "Uruchom"
                tint: Tide.Tokens.positive
                tinted: true
                enabled: row.idle
                onClicked: row.startRequested()
            }
            IconButton {
                icon: "logs"
                name: row.logsOpen ? "Logi (otwarte)" : "Logi"
                tint: row.logsOpen ? Tide.Tokens.done : Tide.Tokens.textSecondary
                highlighted: row.logsOpen
                onClicked: row.logsRequested()
            }
        }
    }
}
