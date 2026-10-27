// Kafelek tablicy w pasku bocznym: „● 2 w toku · ● 1 czeka · ● 1 do sprawdzenia” (albo „○ 3 do
// zrobienia”, „pusto”) tym samym językiem co kafelek w docku i pod spodem do trzech wierszy — najpierw
// to, co czeka na ciebie. W nagłówku „Otwórz w Tide”; kliknięcie wiersza otwiera okno Tide na karcie.
// Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "boardtexts.js" as Texts

Tide.SidebarTile {
    id: tile

    property var snapshot: null
    property bool available: true
    property string problem: ""          // "not-installed", "no-service" albo ""
    signal openRequested(string cardId)

    readonly property var counts: Texts.counts(snapshot)
    readonly property var parts: Texts.tileParts(counts)
    readonly property var rows: available ? Texts.rows(snapshot, 3).rows.filter(r => r.kind !== "backlog") : []
    readonly property string summary: available ? Texts.tileSummary(counts) : "usługa nie działa"
    readonly property bool showProblem: !available || problem !== ""
    readonly property string problemText: !available || problem === "no-service"
        ? "Usługa tide-daemon nie odpowiada" : "Okno Tide nie jest zainstalowane"

    title: "Tablica" + (available && counts.total ? " · " + Texts.cards(counts.total) : "")
    spacing: 8
    Accessible.name: "Tablica: " + (available ? summary : problemText)

    headerItems: Tide.TextButton {
        text: tile.showProblem ? "Spróbuj ponownie" : "Otwórz w Tide"
        Accessible.name: "Otwórz tablicę w oknie Tide"
        onClicked: tile.openRequested("")
    }

    Row {
        visible: tile.available
        spacing: 6
        Repeater {
            model: tile.parts
            Row {
                id: part
                required property var modelData
                required property int index
                spacing: 6
                Text {
                    visible: part.index > 0
                    anchors.verticalCenter: parent.verticalCenter
                    text: "·"
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 7
                    radius: 3.5
                    color: part.modelData.hollow ? "transparent" : Tide.Tokens[part.modelData.tint]
                    border.width: part.modelData.hollow ? 1.5 : 0
                    border.color: Tide.Tokens[part.modelData.tint]
                }
                Text {
                    id: number
                    text: part.modelData.count
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    font.weight: Font.DemiBold
                }
                Text {
                    anchors.baseline: number.baseline
                    text: part.modelData.word
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
            }
        }
        Text {
            visible: tile.parts.length === 0
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Repeater {
        model: tile.rows
        Rectangle {
            id: row
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: 26
            radius: 8
            color: modelData.kind === "waiting" ? Tide.Theme.alpha(Tide.Tokens.peach, rowArea.containsMouse ? 0.16 : 0.10)
                 : rowArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
            Accessible.role: Accessible.Button
            Accessible.name: modelData.id + " " + modelData.line + ", " + modelData.word
            Accessible.onPressAction: tile.openRequested(modelData.id)
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 6
                spacing: 8
                Rectangle {
                    implicitWidth: 7
                    implicitHeight: 7
                    radius: 3.5
                    color: row.modelData.hollow ? "transparent" : Tide.Tokens[row.modelData.tint]
                    border.width: row.modelData.hollow ? 1.5 : 0
                    border.color: Tide.Tokens[row.modelData.tint]
                }
                Text {
                    text: row.modelData.id
                    textFormat: Text.PlainText
                    color: Tide.Tokens.sky
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1.5
                }
                Text {
                    Layout.fillWidth: true
                    text: row.modelData.line
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                    elide: Text.ElideRight
                }
                Text {
                    text: row.modelData.progress || row.modelData.word
                    textFormat: Text.PlainText
                    color: row.modelData.tint === "textSecondary" ? Tide.Tokens.textMuted : Tide.Tokens[row.modelData.tint]
                    font.family: row.modelData.progress ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1.5
                }
            }
            MouseArea {
                id: rowArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: tile.openRequested(row.modelData.id)
            }
        }
    }

    Text {
        Layout.fillWidth: true
        visible: tile.showProblem
        text: tile.problemText
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }
}
