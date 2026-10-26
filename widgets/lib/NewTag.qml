// Znacznik „nowe” przy sesji, którą przed chwilą uruchomiło okno „Nowe zadanie” (pole newTask
// z tide-daemon, przez 10 min od startu). Teal jak akcent, mały i cichy — tylko podpowiedź „to ta”.
import QtQuick

Rectangle {
    id: tag

    property string text: "nowe"

    radius: 6
    color: Theme.alpha(Tokens.accent, 0.16)
    implicitWidth: label.implicitWidth + 12
    implicitHeight: label.implicitHeight + 2
    Accessible.role: Accessible.StaticText
    Accessible.name: "nowe zadanie"

    Text {
        id: label
        anchors.centerIn: parent
        text: tag.text
        color: Tokens.accent
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 2
        font.weight: Font.Medium
    }
}
