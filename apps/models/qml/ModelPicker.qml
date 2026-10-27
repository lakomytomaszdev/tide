// Wybór modelu roli: pole z nazwą i lista modeli zainstalowanych na serwerze roli (rozmiar, „w pamięci”),
// jak wybór projektu w oknie „Co ma zrobić agent?”. Bez listy (serwer nie odpowiada) pole pokazuje tylko
// bieżący model. Model spoza listy (nie ma go na serwerze) jest w maśle.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

Field {
    id: picker

    property string model: ""
    property var options: []            // [{name, detail, current}]
    property string emptyText: "wybierz model"
    property string accessibleName: "Model"
    property int current: 0
    readonly property bool missing: model !== "" && options.length > 0 && !options.some(o => o.name === model)
    readonly property alias opened: popup.opened
    signal chosen(string name)

    function open() {
        if (options.length === 0) return
        current = Math.max(0, options.findIndex(o => o.name === model))
        popup.open()
        list.forceActiveFocus()
    }

    function choose(index) {
        const option = options[index]
        popup.close()
        picker.forceActiveFocus()
        if (option && option.name !== model) chosen(option.name)
    }

    implicitHeight: 36
    focused: activeFocus || popup.opened
    // Modelu nie ma na serwerze — w maśle, jak ostrzeżenie pod polem (nie błąd).
    border.color: missing ? Tide.Theme.alpha(Tide.Tokens.neutral, 0.6)
                : focused ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
    activeFocusOnTab: enabled
    opacity: enabled ? 1 : 0.55
    Accessible.role: Accessible.ComboBox
    Accessible.name: accessibleName + ": " + (model || emptyText)
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                || event.key === Qt.Key_Space) {
            picker.open()
            event.accepted = true
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 11
        anchors.rightMargin: 11
        spacing: 8
        Text {
            Layout.fillWidth: true
            text: picker.model || picker.emptyText
            textFormat: Text.PlainText
            color: picker.model === "" ? Tide.Tokens.textMuted : picker.missing ? Tide.Tokens.neutral : Tide.Tokens.textPrimary
            font.family: picker.model === "" ? Tide.Theme.uiFamily : Tide.Theme.monoFamily
            font.pixelSize: 13
            elide: Text.ElideMiddle
        }
        Glyph {
            Layout.preferredWidth: 14
            Layout.preferredHeight: 14
            visible: picker.options.length > 0
            name: "down"
            color: Tide.Tokens.textMuted
        }
    }
    MouseArea {
        anchors.fill: parent
        enabled: picker.enabled && picker.options.length > 0
        cursorShape: Qt.PointingHandCursor
        onClicked: popup.opened ? popup.close() : picker.open()
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
        contentItem: ListView {
            id: list
            implicitHeight: Math.min(8, Math.max(1, count)) * 34
            clip: true
            model: picker.options
            currentIndex: picker.current
            boundsBehavior: Flickable.StopAtBounds
            keyNavigationEnabled: false
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            Keys.onDownPressed: picker.current = Math.min(picker.options.length - 1, picker.current + 1)
            Keys.onUpPressed: picker.current = Math.max(0, picker.current - 1)
            Keys.onReturnPressed: picker.choose(picker.current)
            Keys.onEnterPressed: picker.choose(picker.current)
            Keys.onSpacePressed: picker.choose(picker.current)
            Accessible.role: Accessible.List
            Accessible.name: picker.accessibleName
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
                    Glyph {
                        Layout.preferredWidth: 13
                        Layout.preferredHeight: 13
                        name: "check"
                        color: row.modelData.name === picker.model ? Tide.Tokens.accent : "transparent"
                    }
                    Text {
                        Layout.fillWidth: true
                        text: row.modelData.name
                        textFormat: Text.PlainText
                        color: row.current ? Tide.Tokens.accent : Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                        elide: Text.ElideMiddle
                    }
                    Text {
                        text: row.modelData.detail
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
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
