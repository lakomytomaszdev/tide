// Jednoliniowe pole tekstowe w ramce Field, z podpowiedzią (placeholder) jak w oknie Nowe zadanie.
import QtQuick
import "tide" as Tide

Field {
    id: field

    property alias text: input.text
    property alias input: input
    property string placeholder: ""
    property bool mono: false
    property int pixelSize: 13
    property int maximumLength: 32767
    property string accessibleName: placeholder
    signal accepted()
    signal escaped()
    signal edited()

    function focusInput() {
        input.forceActiveFocus()
        input.selectAll()
    }

    implicitHeight: 36
    implicitWidth: 200
    focused: input.activeFocus

    TextInput {
        id: input
        anchors.fill: parent
        anchors.leftMargin: 11
        anchors.rightMargin: 11
        verticalAlignment: TextInput.AlignVCenter
        color: Tide.Tokens.textPrimary
        selectionColor: Tide.Tokens.accent
        selectedTextColor: Tide.Tokens.backgroundDesktop
        font.family: field.mono ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
        font.pixelSize: field.pixelSize
        maximumLength: field.maximumLength
        clip: true
        activeFocusOnTab: true
        onAccepted: field.accepted()
        onTextEdited: field.edited()
        Keys.onEscapePressed: event => {
            field.escaped()
            event.accepted = false
        }
        Accessible.role: Accessible.EditableText
        Accessible.name: field.accessibleName

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
