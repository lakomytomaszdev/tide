// Jednoliniowe pole wybieraka gałęzi (BranchPicker): ramka z palety, podpowiedź, sygnały `edited` i `accepted`.
// Samodzielne, bo wspólny katalog git nie zna pól formularzy Tablicy (Field/LineInput).
import QtQuick
import "../tide" as Tide

Rectangle {
    id: field

    property alias text: input.text
    property string placeholder: ""
    signal accepted()
    signal edited()

    function focusInput() {
        input.forceActiveFocus()
        input.selectAll()
    }

    implicitHeight: 36
    implicitWidth: 200
    radius: Tide.Tokens.radiusFrame
    color: Tide.Tokens.backgroundView
    border.width: 1
    border.color: input.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                    : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)

    TextInput {
        id: input
        anchors.fill: parent
        anchors.leftMargin: 11
        anchors.rightMargin: 11
        verticalAlignment: TextInput.AlignVCenter
        color: Tide.Tokens.textPrimary
        selectionColor: Tide.Tokens.accent
        selectedTextColor: Tide.Tokens.backgroundDesktop
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 13
        clip: true
        activeFocusOnTab: true
        onAccepted: field.accepted()
        onTextEdited: field.edited()
        Accessible.role: Accessible.EditableText
        Accessible.name: field.placeholder

        Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            visible: input.text === "" && !input.inputMethodComposing
            text: field.placeholder
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font: input.font
            elide: Text.ElideRight
        }
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.IBeamCursor
        acceptedButtons: Qt.NoButton
    }
}
