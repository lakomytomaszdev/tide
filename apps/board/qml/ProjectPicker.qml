// Wybór projektu karty: pole z nazwą i lista (projekty z tablicy, potem repozytoria, które jeszcze
// nie są projektami — wybranie takiego założy projekt przy zapisie karty, CreateCard z `repo`).
// Pisanie na zamkniętym polu otwiera listę i od razu szuka. Z `optionSource` — własna lista (dodatkowe
// projekty w oknie uruchomienia, PT-28), bez pozycji „Bez projektu”.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Field {
    id: picker

    property var snapshot: null
    property string kind: ""            // "", "project", "repo"
    property string key: ""             // slug projektu albo ścieżka repozytorium
    property string label: ""
    property string query: ""
    property int current: 0
    // Własna lista (np. dodatkowe projekty w oknie uruchomienia): funkcja(zapytanie) → opcje jak
    // projectOptions; null — projekt karty („Bez projektu”, projekty, repozytoria).
    property var optionSource: null
    property string placeholder: "Bez projektu"
    readonly property var options: optionSource ? optionSource(query) : Logic.projectOptions(snapshot, query)
    readonly property alias opened: popup.opened
    signal chosen(string kind, string key, string label)

    function open(text) {
        query = text || ""
        search.text = query
        current = Math.max(0, options.findIndex(o => o.key === key && (o.kind === kind || (o.kind === "none" && kind === ""))))
        popup.open()
        search.forceActiveFocus()
    }

    function choose(index) {
        const option = options[index]
        if (!option) return
        chosen(option.kind === "none" ? "" : option.kind, option.key, option.kind === "none" ? "" : option.name)
        popup.close()
        picker.forceActiveFocus()
    }

    implicitHeight: 36
    focused: activeFocus || popup.opened
    activeFocusOnTab: true
    Accessible.role: Accessible.ComboBox
    Accessible.name: optionSource ? placeholder : "Projekt: " + (label || "bez projektu")
    Keys.onPressed: event => {
        if (event.modifiers & Qt.ControlModifier) {
            event.accepted = false   // Ctrl+Enter należy do okna (np. „Uruchom agenta”)
        } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Space) {
            picker.open("")
            event.accepted = true
        } else if (event.text && event.text.trim() !== "" && !(event.modifiers & Qt.ControlModifier)) {
            picker.open(event.text)
            event.accepted = true
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 11
        anchors.rightMargin: 11
        spacing: 8
        Rectangle {
            implicitWidth: 8
            implicitHeight: 8
            rotation: 45
            radius: 2
            color: picker.kind === "repo" ? Tide.Tokens.butter : Tide.Tokens.accent
            opacity: picker.kind !== "" ? 1 : 0.35
        }
        Text {
            Layout.fillWidth: true
            text: picker.kind === "" ? picker.placeholder : picker.label
            textFormat: Text.PlainText
            color: picker.kind === "" ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            font.weight: Font.Medium
            elide: Text.ElideRight
        }
        Text {
            visible: picker.kind === "repo"
            text: "nowy projekt"
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
        }
        Text {
            text: "⌄"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.pixelSize: 14
        }
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: popup.opened ? popup.close() : picker.open("")
    }

    QQC2.Popup {
        id: popup
        y: picker.height + 4
        width: picker.width
        padding: 6
        focus: true
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
        background: Rectangle {
            radius: Tide.Tokens.radiusFrame + 2
            color: Tide.Tokens.backgroundCard
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
        }
        contentItem: ColumnLayout {
            spacing: 6
            Field {
                Layout.fillWidth: true
                implicitHeight: 32
                focused: search.activeFocus
                TextInput {
                    id: search
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    verticalAlignment: TextInput.AlignVCenter
                    color: Tide.Tokens.textPrimary
                    selectionColor: Tide.Tokens.accent
                    selectedTextColor: Tide.Tokens.backgroundDesktop
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    clip: true
                    onTextEdited: {
                        picker.query = text
                        picker.current = !picker.optionSource && picker.options.length > 1 ? 1 : 0
                    }
                    Keys.onDownPressed: picker.current = Math.min(picker.options.length - 1, picker.current + 1)
                    Keys.onUpPressed: picker.current = Math.max(0, picker.current - 1)
                    Keys.onReturnPressed: picker.choose(picker.current)
                    Keys.onEnterPressed: picker.choose(picker.current)
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "Szukaj projektu"
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: search.text === ""
                        text: "Szukaj projektu albo repozytorium…"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font: search.font
                    }
                }
            }
            ListView {
                id: list
                Layout.fillWidth: true
                implicitHeight: Math.min(7, Math.max(1, count)) * 34
                clip: true
                model: picker.options
                currentIndex: picker.current
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
                delegate: Rectangle {
                    id: row
                    required property var modelData
                    required property int index
                    readonly property bool current: index === picker.current
                    width: ListView.view.width
                    height: 34
                    radius: Tide.Tokens.radiusSmall - 2
                    color: current ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                         : rowArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.name + (modelData.detail ? ", " + modelData.detail : "")
                    Accessible.selected: current
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        spacing: 8
                        Text {
                            text: row.modelData.name
                            textFormat: Text.PlainText
                            color: row.modelData.kind === "none" ? Tide.Tokens.textMuted
                                 : row.current ? Tide.Tokens.accent : Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            font.weight: Font.Medium
                        }
                        Text {
                            Layout.fillWidth: true
                            text: row.modelData.detail
                            textFormat: Text.PlainText
                            color: row.modelData.kind === "repo" ? Tide.Tokens.butter : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                    MouseArea {
                        id: rowArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: picker.choose(row.index)
                    }
                }
            }
        }
    }
}
