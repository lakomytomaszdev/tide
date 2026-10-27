// Karta projektu w raporcie: nazwa, tagi z tablicy (jeśli są), 1–2 zdania „co się działo”,
// liczniki (zrobione / w toku / czeka) i „Czeka na ciebie: …” w brzoskwini. Wybrana — obwódka akcentu.
// Teksty z raportu zawsze jako zwykły tekst.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "reports.js" as Logic

Rectangle {
    id: card

    property var project: ({})
    property bool selected: false
    property bool legacy: false
    readonly property var n: Logic.counts(project)
    readonly property string waits: Logic.waitText(project, 2)
    readonly property var tags: Logic.tags(project, 3)
    signal clicked()

    implicitHeight: column.implicitHeight + 28
    radius: Tide.Tokens.radiusBar
    color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.07)
         : area.containsMouse ? Qt.lighter(Tide.Tokens.backgroundView, 1.08) : Tide.Tokens.backgroundView
    border.width: 1
    border.color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.45)
                : activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: (project.name || "") + (waits ? ", czeka na ciebie" : "")
    Accessible.onPressAction: clicked()
    Keys.onReturnPressed: clicked()
    Keys.onSpacePressed: clicked()

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: card.clicked()
    }

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                objectName: "projectName"
                Layout.maximumWidth: card.width * 0.4
                text: card.project.name || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 15
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }

            Repeater {
                model: card.tags
                delegate: Rectangle {
                    required property var modelData
                    readonly property color tone: Tide.Tokens[modelData.color] !== undefined
                                                  ? Tide.Tokens[modelData.color] : Tide.Tokens.textMuted
                    implicitHeight: tagText.implicitHeight + 4
                    implicitWidth: tagText.implicitWidth + 24
                    radius: height / 2
                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                    Rectangle {
                        anchors.left: parent.left
                        anchors.leftMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        width: 6; height: 6; radius: 3
                        color: parent.tone
                    }
                    Text {
                        id: tagText
                        objectName: "tagChip"
                        anchors.left: parent.left
                        anchors.leftMargin: 18
                        anchors.verticalCenter: parent.verticalCenter
                        text: String(modelData.name || "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Row {
                visible: !card.legacy
                spacing: 0
                Text {
                    text: card.n.done + " zrobione"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.mint
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                Text {
                    text: " · "
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                Text {
                    text: card.n.doing + " w toku"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.teal
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                Text {
                    text: " · "
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                Text {
                    objectName: "waitCount"
                    text: card.n.waiting + " czeka"
                    textFormat: Text.PlainText
                    color: card.n.waiting ? Tide.Tokens.waiting : Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                Text {
                    visible: card.n.chores > 0
                    text: " · " + card.n.chores + " porządki"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
            }
        }

        Text {
            objectName: "projectSummary"
            Layout.fillWidth: true
            text: card.legacy ? "Raport sprzed widoku w oknie Tide — „Na daily” na pełnej stronie w przeglądarce."
                : (card.project.headline || "Ruch bez opisu — szczegóły na pełnej stronie.")
            textFormat: Text.PlainText
            color: card.legacy || !card.project.headline ? Tide.Tokens.textMuted : Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            lineHeight: 1.25
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
        }

        Rectangle {
            Layout.fillWidth: true
            visible: card.waits !== ""
            implicitHeight: waitLabel.implicitHeight + 12
            radius: Tide.Tokens.radiusSmall - 1
            color: Tide.Theme.alpha(Tide.Tokens.waiting, 0.08)
            Text {
                id: waitLabel
                objectName: "waitText"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                text: "Czeka na ciebie: " + card.waits
                textFormat: Text.PlainText
                color: Tide.Tokens.waiting
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                lineHeight: 1.2
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
            }
        }
    }
}
