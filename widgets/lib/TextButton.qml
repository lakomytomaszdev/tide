// Mały przycisk Tide do pasków i kafelków: prawdziwy przycisk (AbstractButton z Qt Quick
// Templates — rola Button dla czytników ekranu, fokus z klawiatury, spacja i Enter), ale bez
// stylu Plasmy, więc wygląda tak samo w panelu i w podglądzie offscreen.
// Odmiany: "link" — sam tekst („+3 więcej”, „Otwórz okno”), "filled" — pełne tło w kolorze
// accent (np. „Przejdź” przy czekającym agencie), "outline" — przezroczysty z obwódką.
import QtQuick
import QtQuick.Templates as T

T.AbstractButton {
    id: button

    property string variant: "link"
    property color accent: Tokens.accent
    property color textColor: variant === "filled" ? Tokens.backgroundWindow
                            : variant === "link" ? accent : Tokens.textSecondary
    property real pointSize: Tokens.fontSizePt - 1
    property bool wrapText: false
    readonly property bool lit: hovered || visualFocus || down

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    leftPadding: variant === "link" ? 2 : 10
    rightPadding: leftPadding
    topPadding: variant === "link" ? 1 : 3
    bottomPadding: topPadding
    implicitWidth: Math.ceil(implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.ceil(implicitContentHeight + topPadding + bottomPadding)

    // Rola i nazwa wprost (AbstractButton ustawia je dopiero przy aktywnej dostępności), a akcja
    // „naciśnij” z czytnika ekranu klika. Spację obsługuje AbstractButton; Enter dokładamy, jak
    // w ActionButton.
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: button.clicked()
    Keys.onReturnPressed: button.clicked()
    Keys.onEnterPressed: button.clicked()

    contentItem: Text {
        text: button.text
        textFormat: Text.PlainText
        color: button.textColor
        font.family: Theme.uiFamily
        font.pointSize: button.pointSize
        font.weight: button.variant === "filled" ? Font.DemiBold : Font.Medium
        font.underline: button.variant === "link" && button.hovered
        elide: button.wrapText ? Text.ElideNone : Text.ElideRight
        wrapMode: button.wrapText ? Text.WrapAnywhere : Text.NoWrap
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignHCenter
    }

    background: Rectangle {
        radius: Tokens.radiusSmall
        color: button.variant === "filled"
            ? (button.lit ? Qt.lighter(button.accent, 1.08) : button.accent)
            : button.variant === "outline"
                ? Theme.alpha(Tokens.textPrimary, button.lit ? 0.10 : 0.04)
                : "transparent"
        border.width: button.visualFocus ? 1 : button.variant === "outline" ? 1 : 0
        border.color: button.visualFocus ? Tokens.accent : Theme.alpha(Tokens.textPrimary, 0.14)
    }

    HoverHandler {
        cursorShape: Qt.PointingHandCursor
    }
}
