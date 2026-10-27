// Dół paska widoków, który może podać widok (railInfo). Wszystkie pola są opcjonalne:
//   label, value, note — ramka, np. „Dane tablicy · ~/DevWorkspace/tide-board · lokalne repo · zapisano 12:04”
//                        (bez żadnego z nich ramki nie ma),
//   link: {text, view, item, activate} — link nad ramką w kolorze akcentu (mock: „Projekty i tagi”):
//         activate() — funkcja widoku, inaczej view (i item) — przejście do widoku okna.
// Teksty zawsze jako zwykły tekst (mogą pochodzić z usługi).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: footer

    property var info: null
    readonly property var link: info && info.link && info.link.text ? info.link : null
    readonly property bool hasBox: !!(info && (info.label || info.value || info.note))

    signal viewRequested(string view, string item)

    function activateLink() {
        if (!link) return
        if (typeof link.activate === "function") link.activate()
        else if (link.view) viewRequested(String(link.view), link.item ? String(link.item) : "")
    }

    spacing: 10
    visible: link !== null || hasBox

    Text {
        Layout.fillWidth: true
        Layout.leftMargin: 10
        visible: footer.link !== null
        text: footer.link ? String(footer.link.text) : ""
        textFormat: Text.PlainText
        color: linkMouse.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 13
        font.underline: activeFocus
        elide: Text.ElideRight
        activeFocusOnTab: visible
        Keys.onReturnPressed: footer.activateLink()
        Keys.onEnterPressed: footer.activateLink()
        Keys.onSpacePressed: footer.activateLink()
        Accessible.role: Accessible.Link
        Accessible.name: text
        Accessible.onPressAction: footer.activateLink()

        MouseArea {
            id: linkMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: footer.activateLink()
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: column.implicitHeight + 24
        visible: footer.hasBox
        radius: Tide.Tokens.radiusTile
        color: Tide.Tokens.backgroundWindow
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

        ColumnLayout {
            id: column
            anchors.fill: parent
            anchors.margins: 12
            spacing: 4

            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: footer.info && footer.info.label ? String(footer.info.label) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: footer.info && footer.info.value ? String(footer.info.value) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
                elide: Text.ElideMiddle
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: footer.info && footer.info.note ? String(footer.info.note) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
        }
    }
}
