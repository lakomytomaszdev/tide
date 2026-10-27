// Formularz tagu: nazwa, kolor (nazwa pastelu z palety — kropki z migawki) i opis. Służy do nowego
// tagu (popup „＋ Tag”, strona Projekty i tagi) i do zmiany istniejącego (nazwa zmienia się też
// w kartach i projektach — usługa robi to sama, newName w UpsertTag).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: editor

    property var tag: null              // istniejący tag z migawki albo null (nowy)
    property var pastels: []            // nazwy pasteli w kolejności palety
    property var taken: []              // kolory już użyte — nowy tag dostaje pierwszy wolny
    property string error: ""
    property bool busy: false
    property string name: ""
    property string color: ""
    property string description: ""
    signal saveRequested(var request)
    signal cancelRequested()

    function reset() {
        name = tag ? tag.name : ""
        color = tag ? tag.color : (pastels.find(c => taken.indexOf(c) < 0) || pastels[0] || "")
        description = tag ? tag.description || "" : ""
        error = ""
        nameField.text = name
        descriptionField.text = description
    }

    function focusName() {
        nameField.focusInput()
    }

    function save() {
        const trimmed = nameField.text.trim()
        if (!trimmed) {
            error = "Podaj nazwę tagu."
            return
        }
        const request = { name: tag ? tag.name : trimmed, color: color, description: descriptionField.text.trim() }
        if (tag && trimmed !== tag.name) request.newName = trimmed
        saveRequested(request)
    }

    onTagChanged: reset()
    Component.onCompleted: reset()
    spacing: 8

    LineInput {
        id: nameField
        objectName: "tagName"
        Layout.fillWidth: true
        placeholder: "Nazwa tagu, np. pilne"
        maximumLength: 40
        onAccepted: editor.save()
        onEscaped: editor.cancelRequested()
    }

    Flow {
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: editor.pastels
            Rectangle {
                id: swatch
                required property string modelData
                readonly property bool on: modelData === editor.color
                width: 24
                height: 24
                radius: 12
                color: "transparent"
                border.width: on || activeFocus ? 2 : 0
                border.color: activeFocus ? Tide.Tokens.accent : Tide.Tokens[modelData] || Tide.Tokens.textMuted
                activeFocusOnTab: true
                Accessible.role: Accessible.RadioButton
                Accessible.name: "kolor " + modelData
                Accessible.checked: on
                Accessible.onPressAction: editor.color = modelData
                Keys.onSpacePressed: editor.color = modelData
                Rectangle {
                    anchors.centerIn: parent
                    width: 14
                    height: 14
                    radius: 7
                    color: Tide.Tokens[swatch.modelData] || Tide.Tokens.textMuted
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: editor.color = swatch.modelData
                }
            }
        }
    }

    LineInput {
        id: descriptionField
        Layout.fillWidth: true
        placeholder: "Opis (opcjonalnie)"
        pixelSize: 12
        onAccepted: editor.save()
        onEscaped: editor.cancelRequested()
    }

    Text {
        objectName: "tagError"
        Layout.fillWidth: true
        visible: editor.error !== ""
        text: editor.error
        textFormat: Text.PlainText
        color: Tide.Tokens.rose
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        wrapMode: Text.Wrap
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Item { Layout.fillWidth: true }
        PillButton {
            implicitHeight: 30
            pixelSize: 12
            flat: true
            text: "Anuluj"
            onClicked: editor.cancelRequested()
        }
        PillButton {
            objectName: "tagSave"
            implicitHeight: 30
            pixelSize: 12
            strong: true
            enabled: !editor.busy
            text: editor.tag ? "Zapisz tag" : "Dodaj tag"
            fill: Tide.Tokens.accent
            textColor: Tide.Tokens.backgroundDesktop
            onClicked: editor.save()
        }
    }
}
