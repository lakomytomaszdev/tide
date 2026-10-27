// Tag z kolorową kropką (mock: „● Firma” na karcie). selectable — przełącznik w formularzu
// karty i w filtrze (wybrany: tło akcentu albo obwódka); locked — tag z projektu (nie da się
// zdjąć z karty, tylko w projekcie). Nazwa tagu to dane: zawsze zwykły tekst.
import QtQuick
import "tide" as Tide

Rectangle {
    id: chip

    property string name: ""
    property color dotColor: Tide.Tokens.textMuted
    property bool selectable: false
    property bool selected: false
    property bool locked: false
    property bool large: false          // pastylka filtra (28 px, 12 px) zamiast znacznika na karcie
    property bool showDot: true
    property string suffix: ""          // np. „z projektu”
    signal clicked()

    implicitHeight: large ? 28 : label.implicitHeight + 4
    implicitWidth: row.implicitWidth + (large ? 24 : 16)
    radius: height / 2
    color: large ? (selected ? Tide.Tokens.accent : area.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.12)
                                                                       : Tide.Tokens.backgroundCard)
         : selectable && selected ? Tide.Theme.alpha(dotColor, 0.16)
         : Tide.Theme.alpha(Tide.Tokens.textPrimary, area.containsMouse && selectable ? 0.09 : 0.05)
    border.width: (selectable && !large && selected) || activeFocus ? 1 : 0
    border.color: activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(dotColor, 0.55)
    opacity: locked ? 0.75 : 1
    activeFocusOnTab: selectable
    Accessible.role: selectable ? Accessible.CheckBox : Accessible.StaticText
    Accessible.name: name + (suffix ? " (" + suffix + ")" : "")
    Accessible.checkable: selectable
    Accessible.checked: selected
    Accessible.onPressAction: if (selectable && !locked) clicked()
    Keys.onSpacePressed: if (selectable && !locked) clicked()
    Keys.onReturnPressed: if (selectable && !locked) clicked()

    Row {
        id: row
        anchors.centerIn: parent
        spacing: chip.large ? 6 : 5
        Rectangle {
            visible: chip.showDot
            anchors.verticalCenter: parent.verticalCenter
            width: chip.large ? 7 : 6
            height: width
            radius: width / 2
            color: chip.large && chip.selected ? Tide.Tokens.backgroundDesktop : chip.dotColor
        }
        Text {
            id: label
            text: chip.name
            textFormat: Text.PlainText
            color: chip.large ? (chip.selected ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textSecondary)
                              : Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: chip.large ? 12 : 11
            font.weight: chip.large && chip.selected ? Font.DemiBold : Font.Normal
        }
        Text {
            visible: chip.suffix !== ""
            text: chip.suffix
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 10
            anchors.baseline: label.baseline
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        enabled: chip.selectable && !chip.locked
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: chip.clicked()
    }
}
