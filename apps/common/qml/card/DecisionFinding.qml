// Uwaga recenzenta w oknie decyzji (K-35, PT-127): pole wyboru, waga (blokująca / ważna / drobna; stary tekst bez
// wagi), treść i powiązane tickety. Zaznaczenie trzyma okno (`checked`); uwaga tylko zgłasza przełączenie.
// Domyślne zaznaczenie liczy usługa (`selected`), okno go nie wymyśla.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide
import "decision.js" as Decision

Rectangle {
    id: finding

    property string severity: ""
    property string text: ""
    property var tickets: []
    property bool checked: false
    signal toggled()

    readonly property color tone: severity === "blocker" ? Tide.Tokens.negative
                                : severity === "major" ? Tide.Tokens.waiting : Tide.Tokens.textMuted

    implicitHeight: column.implicitHeight + 20
    radius: Tide.Tokens.radiusTile
    color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.07) : Tide.Tokens.backgroundCard
    border.width: 1
    border.color: checked || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.5)
                                         : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
    activeFocusOnTab: true

    Accessible.role: Accessible.CheckBox
    Accessible.name: Decision.severityLabel(severity) + ": " + text
    Accessible.description: tickets && tickets.length ? "Tickety: " + tickets.join(", ") : ""
    Accessible.checked: checked
    Accessible.onToggleAction: toggled()
    Keys.onSpacePressed: toggled()

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            finding.forceActiveFocus()
            finding.toggled()
        }
    }

    RowLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 10
        anchors.leftMargin: 12
        spacing: 10

        Rectangle {
            objectName: "decisionFindingMark"
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 2
            implicitWidth: 14
            implicitHeight: 14
            radius: 4
            color: finding.checked ? Tide.Tokens.accent : "transparent"
            border.width: 1.5
            border.color: finding.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            RowLayout {
                spacing: 8
                Rectangle {
                    objectName: "decisionFindingSeverity"
                    implicitWidth: severityText.implicitWidth + 14
                    implicitHeight: severityText.implicitHeight + 6
                    radius: Tide.Tokens.radiusSmall
                    color: Tide.Theme.alpha(finding.tone, 0.14)
                    border.width: 1
                    border.color: Tide.Theme.alpha(finding.tone, 0.4)
                    Text {
                        id: severityText
                        anchors.centerIn: parent
                        text: Decision.severityLabel(finding.severity)
                        textFormat: Text.PlainText
                        color: finding.tone
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        font.weight: Font.Medium
                    }
                }
                Repeater {
                    model: finding.tickets || []
                    delegate: Text {
                        objectName: "decisionFindingTicket"
                        required property var modelData
                        text: modelData
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    }
                }
            }
            Text {
                objectName: "decisionFindingText"
                Layout.fillWidth: true
                text: finding.text
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
            }
        }
    }
}
