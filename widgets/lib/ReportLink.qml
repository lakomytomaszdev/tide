// Link do raportu porannego (tide-daemon /Reports): TextButton z małą brzoskwiniową kropką przed
// tekstem, dopóki najnowszy raport nie został otwarty. Ten sam w nagłówku paska bocznego
// („Raport · dziś 07:00”) i w nagłówku popupu agentów w docku („Raport”, odmiana outline).
// Kliknięcie to clicked() — widżet woła Open(), które otwiera raport i oznacza go jako przeczytany.
import QtQuick

TextButton {
    id: link

    property bool unread: false
    property real dotSize: 6
    readonly property real basePadding: variant === "link" ? 2 : 10

    // Nieprzeczytany jaśniej, przeczytany spokojnie wyciszony — kolor niesie kropka, nie tekst.
    accent: unread ? Tokens.textSecondary : Tokens.textMuted
    leftPadding: unread ? basePadding + dotSize + 5 : basePadding
    rightPadding: basePadding

    Rectangle {
        objectName: "reportDot"
        visible: link.unread
        x: link.basePadding
        anchors.verticalCenter: parent.verticalCenter
        width: link.dotSize
        height: link.dotSize
        radius: width / 2
        color: Tokens.peach
    }
}
