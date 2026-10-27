// „Poza projektami” na końcu listy: sesje spoza repozytoriów git (korzeń przestrzeni roboczej,
// katalogi tymczasowe) — zwinięte w jeden wiersz, po rozwinięciu stan, tytuł, zadanie i wynik albo
// pytanie. Bez „Na daily” i bez własnych stron, jak w indeksie raportu. Wszystko jako zwykły tekst.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "reports.js" as Logic

Rectangle {
    id: section

    property var sessions: []
    property bool expanded: false
    property string homeDir: ""

    implicitHeight: column.implicitHeight + 20
    radius: Tide.Tokens.radiusTile
    color: "transparent"
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.14)

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 10
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                objectName: "outsideLabel"
                Layout.fillWidth: true
                text: "Poza projektami: " + Logic.outsideLabel(section.sessions)
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Text {
                objectName: "outsideToggle"
                text: section.expanded ? "Zwiń" : "Rozwiń"
                textFormat: Text.PlainText
                color: toggleArea.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8)
                                                               : Tide.Tokens.accent
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                font.underline: activeFocus
                activeFocusOnTab: true
                Keys.onReturnPressed: section.expanded = !section.expanded
                Keys.onSpacePressed: section.expanded = !section.expanded
                Accessible.role: Accessible.Button
                Accessible.name: (section.expanded ? "Zwiń" : "Rozwiń") + " poza projektami"
                MouseArea {
                    id: toggleArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: section.expanded = !section.expanded
                }
            }
        }

        Repeater {
            model: section.expanded ? section.sessions : []
            delegate: ColumnLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true
                spacing: 3

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 7
                    Dot { tint: Logic.stateTint(row.modelData.state); size: 7 }
                    Text {
                        text: String(row.modelData.word || "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens[Logic.stateTint(row.modelData.state)]
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    Text {
                        objectName: "outsideTitle"
                        Layout.fillWidth: true
                        text: String(row.modelData.title || "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                }
                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    text: Logic.tildePath(row.modelData.path, section.homeDir)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                }
                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    visible: !!row.modelData.task
                    text: "Zadanie: " + String(row.modelData.task || "")
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    visible: !!row.modelData.said
                    text: (row.modelData.state === "waiting" ? "Pyta: " : "Wynik: ") + String(row.modelData.said || "")
                    textFormat: Text.PlainText
                    color: row.modelData.state === "waiting" ? Tide.Tokens.waiting : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    maximumLineCount: 4
                    elide: Text.ElideRight
                }
            }
        }
    }
}
