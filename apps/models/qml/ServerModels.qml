// „Modele na serwerze”: modele zainstalowane na wybranym serwerze (rozmiar, który jest w pamięci i ile
// z niego na karcie, które role go używają) i „Pobierz model” — pole, podpowiedzi z naszych pomiarów
// (PT-13) i postęp pobierania z Status().pulls. Usuwania modeli nie ma (usługa go nie daje).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "models.js" as Logic

ColumnLayout {
    id: section

    property var status: null
    property var page: null
    property string selected: ""
    readonly property var all: status ? Logic.servers(status) : []
    readonly property var server: all.find(s => s.name === selected) || null
    readonly property var models: status ? Logic.serverModels(status, selected) : []
    readonly property var pulls: status ? Logic.pulls(status, selected) : []
    readonly property var hints: status ? Logic.suggestions(status, selected) : []
    readonly property bool canPull: !!server && server.online && server.kind !== "openai"
    readonly property string tag: "pull:" + selected
    readonly property alias pullText: pullField.text
    signal serverPicked(string name)

    function note() {
        return page ? page.notes[tag] || null : null
    }
    function pullModel() {
        const problem = Logic.modelProblem(pullField.text)
        if (problem) {
            page.setNote(tag, { text: problem, error: true })
            return
        }
        page.send("PullModel", Logic.pullRequest(selected, pullField.text), tag)
    }
    function fill(model) {
        pullField.text = model
        pullField.focusInput()
    }

    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        SectionTitle { text: "Modele na serwerze" }
        Item { Layout.fillWidth: true }
        Segmented {
            objectName: "serverSwitch"
            visible: section.all.length > 1
            options: section.all.map(s => ({ value: s.name, label: s.label }))
            current: section.selected
            itemHeight: 26
            onPicked: value => section.serverPicked(value)
        }
    }

    Note {
        objectName: "serverModelsState"
        Layout.fillWidth: true
        text: !section.server ? ""
            : section.server.state.kind === "offline" ? "Serwer „" + section.server.label + "” nie odpowiada — lista modeli będzie, gdy wstanie."
            : section.server.state.kind === "checking" ? "Sprawdzam serwer „" + section.server.label + "”…"
            : section.models.length === 0 ? "Na tym serwerze nie ma jeszcze modeli."
            : section.server.kind === "openai" ? "Serwer zgodny z OpenAI pobiera modele sam (w swoim programie) i nie mówi, co ma w pamięci."
            : ""
    }

    Repeater {
        model: section.models
        Rectangle {
            id: item
            required property var modelData
            objectName: "modelRow"
            Layout.fillWidth: true
            implicitHeight: rowLayout.implicitHeight + 18
            radius: Tide.Tokens.radiusTile
            color: modelData.loaded ? Tide.Theme.tinted(Tide.Tokens.backgroundView, modelData.slow ? Tide.Tokens.neutral : Tide.Tokens.accent, 0.05)
                                    : Tide.Tokens.backgroundView
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
            Accessible.role: Accessible.ListItem
            Accessible.name: modelData.name + ", " + modelData.size + (modelData.loaded ? ", " + modelData.loadedText : "")

            RowLayout {
                id: rowLayout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: 12
                Rectangle {
                    implicitWidth: 7
                    implicitHeight: 7
                    radius: 3.5
                    color: item.modelData.loaded ? (item.modelData.slow ? Tide.Tokens.neutral : Tide.Tokens.working) : "transparent"
                    border.width: item.modelData.loaded ? 0 : 1.2
                    border.color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.6)
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        Layout.fillWidth: true
                        text: item.modelData.name
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                        elide: Text.ElideMiddle
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: [item.modelData.detail, item.modelData.loadedText].filter(x => x).join(" · ")
                        textFormat: Text.PlainText
                        color: item.modelData.slow ? Tide.Tokens.neutral : item.modelData.loaded ? Tide.Tokens.accent : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }
                Repeater {
                    model: item.modelData.usedBy
                    Rectangle {
                        id: usedChip
                        required property var modelData
                        implicitHeight: 20
                        implicitWidth: usedLabel.implicitWidth + 14
                        radius: height / 2
                        color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.14)
                        Text {
                            id: usedLabel
                            anchors.centerIn: parent
                            text: usedChip.modelData
                            textFormat: Text.PlainText
                            color: Tide.Tokens.lavender
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                        }
                    }
                }
                Text {
                    Layout.preferredWidth: 64
                    horizontalAlignment: Text.AlignRight
                    text: item.modelData.size
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 12
                }
            }
        }
    }

    // --- pobieranie ---------------------------------------------------------------------------
    Repeater {
        model: section.pulls
        ColumnLayout {
            id: pullItem
            required property var modelData
            objectName: "pullRow"
            Layout.fillWidth: true
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Glyph {
                    Layout.preferredWidth: 14
                    Layout.preferredHeight: 14
                    name: pullItem.modelData.done ? "check" : pullItem.modelData.error ? "close" : "download"
                    color: pullItem.modelData.error ? Tide.Tokens.negative
                         : pullItem.modelData.done ? Tide.Tokens.positive : Tide.Tokens.accent
                }
                Text {
                    text: pullItem.modelData.model
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 13
                }
                Text {
                    Layout.fillWidth: true
                    text: pullItem.modelData.text
                    textFormat: Text.PlainText
                    color: pullItem.modelData.error ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
            Tide.MeterBar {
                Layout.fillWidth: true
                Layout.leftMargin: 24
                implicitHeight: 5
                visible: pullItem.modelData.running
                value: pullItem.modelData.fraction === null ? 0 : pullItem.modelData.fraction
                color: Tide.Tokens.accent
            }
        }
    }

    ColumnLayout {
        objectName: "pullForm"
        Layout.fillWidth: true
        Layout.topMargin: 6
        visible: section.canPull
        spacing: 8

        Text {
            text: "Pobierz model na „" + (section.server ? section.server.label : "") + "”"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            font.weight: Font.DemiBold
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            LineInput {
                id: pullField
                objectName: "pullField"
                Layout.fillWidth: true
                Layout.maximumWidth: 420
                mono: true
                placeholder: "nazwa modelu, np. qwen3:8b"
                accessibleName: "Model do pobrania"
                onAccepted: section.pullModel()
                onEdited: section.page.setNote(section.tag, null)
            }
            Tide.Button {
                objectName: "pullButton"
                text: section.page && section.page.busy[section.tag] ? "Zaczynam…" : "Pobierz"
                enabled: !(section.page && section.page.busy[section.tag])
                onClicked: section.pullModel()
            }
            Item { Layout.fillWidth: true }
        }
        Note {
            objectName: "pullCloudNote"
            Layout.fillWidth: true
            visible: text !== ""
            tone: "warn"
            text: Logic.pullWarning(pullField.text)
        }
        Note {
            objectName: "pullNote"
            Layout.fillWidth: true
            tone: section.note() && section.note().error ? "error" : "ok"
            text: section.note() ? section.note().text : ""
        }
        Note {
            Layout.fillWidth: true
            text: "Duży model to kilka–kilkadziesiąt GB; pobieranie idzie w tle, postęp jest tutaj."
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            Repeater {
                model: section.hints
                Rectangle {
                    id: hint
                    required property var modelData
                    objectName: "suggestion"
                    implicitHeight: 30
                    implicitWidth: hintRow.implicitWidth + 22
                    radius: Tide.Tokens.radiusFrame
                    color: hintArea.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.backgroundCard, 1.12) : Tide.Tokens.backgroundCard
                    border.width: activeFocus ? 1 : 0
                    border.color: Tide.Tokens.accent
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.model + " — " + modelData.hint
                    Accessible.onPressAction: section.fill(modelData.model)
                    Keys.onSpacePressed: section.fill(modelData.model)
                    Keys.onReturnPressed: section.fill(modelData.model)
                    Row {
                        id: hintRow
                        anchors.centerIn: parent
                        spacing: 8
                        Text {
                            text: hint.modelData.model
                            textFormat: Text.PlainText
                            color: hint.modelData.installed ? Tide.Tokens.textMuted : Tide.Tokens.accent
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 12
                        }
                        Text {
                            text: hint.modelData.installed ? "jest na serwerze" : hint.modelData.hint
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                    }
                    MouseArea {
                        id: hintArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: section.fill(hint.modelData.model)
                    }
                }
            }
        }
    }
}
