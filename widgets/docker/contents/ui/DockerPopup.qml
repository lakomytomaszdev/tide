// Popup kafelka Docker (mock „Kafelki Tablica i Docker”): nagłówek ze znakiem i linkiem „Otwórz w Tide ↗”,
// trzy małe liczby (CPU całego procesora, RAM kontenerów, do odzyskania na dysku), projekty compose
// z portami HTTP, liczbą działających i przyciskiem zatrzymania (GroupAction — zadanie w usłudze),
// kontenery, które padły (róż) z „Logi” (okno Tide na kontenerze) i „Uruchom”, stopka z zatrzymanymi.
// Sam nie rozmawia z usługą: prośby idą sygnałami, stan akcji (busy, notes) daje main.qml.
import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import "../code/docker.js" as Logic
import "tide" as Tide

ColumnLayout {
    id: popup

    property var snapshot: null
    property bool serviceUp: true
    property var stats: null
    property var disk: null
    property real nowTs: Date.now() / 1000
    property var busy: ({})                  // id grupy albo kontenera → akcja w toku
    property var notes: ({})                 // id grupy albo kontenera → {text, error}

    signal openRequested(string item)        // okno Tide na widoku Docker ("" — bez elementu)
    signal groupActionRequested(string project, string action)
    signal startRequested(string id)

    readonly property string dockerStatus: Logic.status(snapshot, serviceUp)
    readonly property bool ok: dockerStatus === "ok"
    readonly property var projects: ok ? Logic.projects(snapshot) : []
    readonly property var failed: ok ? Logic.failedContainers(snapshot) : []
    readonly property string footerText: ok ? Logic.footer(snapshot) : ""
    readonly property bool statsReady: !!(stats && stats.ok && stats.total)

    spacing: 12

    component MiniStat: Rectangle {
        id: mini
        property string label: ""
        property string value: ""
        property color valueColor: Tide.Tokens.textPrimary
        Layout.fillWidth: true
        implicitHeight: miniColumn.implicitHeight + 16
        radius: Tide.Tokens.radiusFrame
        color: Tide.Tokens.backgroundWindow
        Column {
            id: miniColumn
            x: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3
            Text {
                text: mini.label
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 2
            }
            Text {
                objectName: "miniValue"
                text: mini.value
                textFormat: Text.PlainText
                color: mini.valueColor
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt + 0.5
                font.weight: Font.DemiBold
            }
        }
    }

    // Mały kwadratowy przycisk z kwadratem „stop” albo trójkątem „start”.
    component SquareButton: Rectangle {
        id: square
        property bool play: false
        property string name: ""
        signal clicked()
        implicitWidth: 26
        implicitHeight: 26
        radius: 7
        color: squareArea.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.backgroundWindow, 1.25) : Tide.Tokens.backgroundWindow
        opacity: enabled ? 1 : 0.45
        border.width: activeFocus ? 1 : 0
        border.color: Tide.Tokens.accent
        activeFocusOnTab: enabled
        Accessible.role: Accessible.Button
        Accessible.name: name
        Accessible.onPressAction: if (enabled) clicked()
        Keys.onSpacePressed: if (enabled) clicked()
        Keys.onReturnPressed: if (enabled) clicked()
        Shape {
            anchors.centerIn: parent
            width: 11
            height: 11
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: square.play ? "transparent" : Tide.Tokens.textSecondary
                strokeWidth: 2
                fillColor: square.play ? Tide.Tokens.positive : "transparent"
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: square.play ? "M2 0.5l8.5 5 -8.5 5z" : "M2.5 1.5h6a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-6a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1z" }
            }
        }
        MouseArea {
            id: squareArea
            anchors.fill: parent
            hoverEnabled: true
            enabled: square.enabled
            cursorShape: Qt.PointingHandCursor
            onClicked: square.clicked()
        }
    }

    component Note: Text {
        property var note: null
        Layout.fillWidth: true
        Layout.leftMargin: 28
        visible: note !== null && note !== undefined
        text: note ? note.text : ""
        textFormat: Text.PlainText
        color: note && note.error ? Tide.Tokens.negative : Tide.Tokens.positive
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
        wrapMode: Text.WordWrap
    }

    // --- nagłówek ---------------------------------------------------------------------------
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Shape {
            implicitWidth: 18
            implicitHeight: 18
            preferredRendererType: Shape.CurveRenderer
            transform: Scale { xScale: 18 / 24; yScale: 18 / 24 }
            ShapePath {
                strokeColor: Tide.Tokens.sky
                strokeWidth: 2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: "M3 8l9 -5 9 5v8l-9 5 -9 -5z M3 8l9 5 9 -5 M12 13v8" }
            }
        }
        Text {
            Layout.fillWidth: true
            text: "Docker"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1.5
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Tide.TextButton {
            objectName: "openButton"
            text: "Otwórz w Tide ↗"
            Accessible.name: "Otwórz widok Docker w oknie Tide"
            onClicked: popup.openRequested("")
        }
    }

    // --- problem ------------------------------------------------------------------------------
    Text {
        objectName: "problem"
        Layout.fillWidth: true
        visible: !popup.ok
        text: popup.dockerStatus === "down" ? "Usługa tide-daemon nie odpowiada. Uruchom: systemctl --user start tide-daemon"
            : popup.dockerStatus === "starting" ? "Łączę się z Dockerem…"
            : (popup.snapshot && popup.snapshot.error ? popup.snapshot.error : "Docker nie działa")
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        wrapMode: Text.WordWrap
    }

    // --- liczby -------------------------------------------------------------------------------
    RowLayout {
        Layout.fillWidth: true
        visible: popup.ok
        spacing: 8
        MiniStat {
            label: "CPU"
            value: popup.statsReady ? Logic.percent(popup.stats.total.hostCpuPercent) : "—"
        }
        MiniStat {
            label: "RAM"
            value: popup.statsReady ? Logic.bytes(popup.stats.total.memBytes) : "—"
        }
        MiniStat {
            label: "Do odzyskania"
            value: popup.disk && popup.disk.ok ? Logic.bytes(popup.disk.totalReclaimableBytes || 0) : "—"
            valueColor: popup.disk && popup.disk.ok && popup.disk.totalReclaimableBytes > 0 ? Tide.Tokens.neutral : Tide.Tokens.textPrimary
        }
    }

    // --- projekty i padnięte --------------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        visible: popup.ok
        spacing: 2

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            visible: popup.projects.length === 0 && popup.failed.length === 0
            text: "Nic nie działa."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }

        Repeater {
            model: popup.projects
            ColumnLayout {
                id: projectRow
                required property var modelData
                objectName: "project"
                Layout.fillWidth: true
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 8
                    Layout.rightMargin: 8
                    Layout.topMargin: 3
                    Layout.bottomMargin: 3
                    spacing: 10
                    Rectangle {
                        implicitWidth: 8
                        implicitHeight: 8
                        radius: 4
                        color: Tide.Tokens.working
                    }
                    Row {
                        Layout.fillWidth: true
                        clip: true
                        spacing: 6
                        Text {
                            id: projectName
                            objectName: "projectName"
                            text: projectRow.modelData.label
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt
                        }
                        Text {
                            objectName: "projectPorts"
                            anchors.baseline: projectName.baseline
                            visible: text !== ""
                            text: projectRow.modelData.ports
                            textFormat: Text.PlainText
                            color: Tide.Tokens.accent
                            font.family: Tide.Theme.monoFamily
                            font.pointSize: Tide.Tokens.fontSizePt - 1.5
                        }
                    }
                    Text {
                        objectName: "projectCount"
                        text: popup.busy[projectRow.modelData.id] ? "…" : projectRow.modelData.count
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                    SquareButton {
                        objectName: "stopButton"
                        visible: projectRow.modelData.compose
                        name: "Zatrzymaj projekt " + projectRow.modelData.label
                        enabled: !popup.busy[projectRow.modelData.id]
                        onClicked: if (!popup.busy[projectRow.modelData.id]) popup.groupActionRequested(projectRow.modelData.project, "stop")
                    }
                    Item {
                        visible: !projectRow.modelData.compose
                        implicitWidth: 26
                        implicitHeight: 26
                    }
                }
                Note { note: popup.notes[projectRow.modelData.id] || null }
            }
        }

        Repeater {
            model: popup.failed
            ColumnLayout {
                id: failedRow
                required property var modelData
                objectName: "failed"
                Layout.fillWidth: true
                Layout.topMargin: 2
                spacing: 0
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: failedBody.implicitHeight + 14
                    radius: Tide.Tokens.radiusFrame
                    color: Tide.Theme.alpha(Tide.Tokens.negative, 0.09)
                    RowLayout {
                        id: failedBody
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 10
                        Rectangle {
                            implicitWidth: 8
                            implicitHeight: 8
                            radius: 4
                            color: Tide.Tokens.negative
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                text: failedRow.modelData.name
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt
                                elide: Text.ElideRight
                            }
                            Text {
                                objectName: "failedLine"
                                Layout.fillWidth: true
                                text: popup.busy[failedRow.modelData.id] ? "uruchamiam…" : Logic.failedLine(failedRow.modelData, popup.nowTs)
                                textFormat: Text.PlainText
                                color: popup.busy[failedRow.modelData.id] ? Tide.Tokens.accent : Tide.Tokens.negative
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 2
                                elide: Text.ElideRight
                            }
                        }
                        Tide.TextButton {
                            text: "Logi"
                            Accessible.name: "Logi " + failedRow.modelData.name + " w oknie Tide"
                            onClicked: popup.openRequested(failedRow.modelData.id)
                        }
                        Tide.TextButton {
                            objectName: "startButton"
                            variant: "filled"
                            accent: Tide.Theme.alpha(Tide.Tokens.positive, 0.16)
                            textColor: Tide.Tokens.positive
                            text: "Uruchom"
                            enabled: !popup.busy[failedRow.modelData.id]
                            Accessible.name: "Uruchom " + failedRow.modelData.name
                            onClicked: if (!popup.busy[failedRow.modelData.id]) popup.startRequested(failedRow.modelData.id)
                        }
                    }
                }
                Note { note: popup.notes[failedRow.modelData.id] || null }
            }
        }
    }

    Text {
        objectName: "footer"
        Layout.fillWidth: true
        visible: text !== ""
        text: popup.footerText
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1.5
    }
}
