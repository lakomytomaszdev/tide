// Mały przycisk z liniową ikoną (PT-105): pinezka i × na kartach „Teraz”, ikony rzędu kategorii.
// Prawdziwy przycisk (rola Button, fokus z klawiatury, spacja i Enter) z nazwą dla czytnika ekranu
// i podpowiedzią; label — opcjonalny podpis obok ikony (aktywna kategoria).
import QtQuick
import QtQuick.Templates as T

T.AbstractButton {
    id: button

    property string iconName: ""
    property string label: ""
    property string tip: ""
    property bool on: false          // aktywny stan: złota ikona (przypięta karta, otwarta kategoria)
    property bool framed: false      // obwódka jak w rzędzie kategorii
    property bool dot: false         // kropka ostrzeżenia (kategoria wymaga uwagi)
    property real iconSize: 15
    readonly property bool lit: hovered || visualFocus || down

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    padding: framed ? 7 : 5
    implicitWidth: Math.ceil(implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.ceil(implicitContentHeight + topPadding + bottomPadding)

    Accessible.role: Accessible.Button
    Accessible.name: button.tip !== "" ? button.tip : button.label
    Accessible.onPressAction: button.clicked()
    Keys.onReturnPressed: button.clicked()
    Keys.onEnterPressed: button.clicked()

    contentItem: Row {
        spacing: 5
        LineIcon {
            anchors.verticalCenter: parent.verticalCenter
            name: button.iconName
            size: button.iconSize
            color: button.on ? Tokens.accent : button.lit ? Tokens.textPrimary : Tokens.textSecondary
        }
        Text {
            visible: button.label !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: button.label
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
            font.weight: Font.Medium
        }
    }

    background: Rectangle {
        radius: Tokens.radiusSmall
        color: button.on && button.framed ? Theme.alpha(Tokens.accent, 0.16)
             : Theme.alpha(Tokens.textPrimary, button.lit ? 0.10 : button.framed ? 0.04 : 0)
        border.width: button.framed || button.visualFocus ? 1 : 0
        border.color: button.visualFocus || (button.on && button.framed) ? Tokens.accent
                    : Theme.alpha(Tokens.textPrimary, 0.14)
        opacity: button.enabled ? 1 : 0.45
        Rectangle {
            visible: button.dot
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 3
            width: 7
            height: 7
            radius: width / 2
            color: Tokens.neutral
        }
    }

    PlainToolTip {
        visible: button.hovered && button.tip !== ""
        text: button.tip
    }

    HoverHandler {
        cursorShape: Qt.PointingHandCursor
    }
}
