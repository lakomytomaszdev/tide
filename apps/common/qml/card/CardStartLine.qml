// Jedna linia „Start od: master (3.11, 58a3f26)” z przyciskiem „Zmień gałąź startową…” (K-99, PT-389).
// Wspólna dla panelu karty i pełnej karty, więc oba widoki pokazują ten sam tekst. Tekst składa wyłącznie
// git/start.js; przycisk tylko emituje `changeRequested()` — niczego nie wysyła do usługi, a decyzję
// o zmianie bazy daje podgląd korekty (BaseChangePreview).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "../git/start.js" as Start

RowLayout {
    id: line

    property var derived: null
    readonly property var flow: derived && derived.flow ? derived.flow : null
    readonly property string text: Start.cardStartLine(flow)
    readonly property string hint: Start.cardStartHint(flow)
    // Widok, który nie umie otworzyć podglądu korekty (Sesje), wyłącza przycisk, a linia zostaje.
    property bool changeAllowed: true
    readonly property bool changeVisible: changeAllowed && Start.canChangeStart(derived)
    signal changeRequested()

    objectName: "cardStartLine"
    visible: text !== ""
    spacing: 8

    Text {
        objectName: line.objectName + "Text"
        Layout.fillWidth: true
        text: line.text
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        HoverHandler { id: hover }
        QQC2.ToolTip.visible: hover.hovered && line.hint !== ""
        QQC2.ToolTip.text: line.hint
        QQC2.ToolTip.delay: 400
    }
    Tide.TextButton {
        objectName: line.objectName + "Change"
        visible: line.changeVisible
        text: "Zmień gałąź startową…"
        onClicked: line.changeRequested()
    }
}
