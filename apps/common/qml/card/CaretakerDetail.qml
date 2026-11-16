// Rozwijany komentarz opiekuna karty (K-128, PT-506): makieta S-9 `.comment` przy opcji (tryby "agree" i "differ"
// reguły jednego zalecenia, decision.js `advice`) albo pod zdaniem opiekuna (tryb "note"). Pokazuje tylko pola
// z usługi: rekomendację, streszczenie (Tide.Prose — tekst opiekuna, escapowany) i zwijaną listę prób `tried[]`.
// Niczego nie wybiera i nie wysyła.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide
import "decision.js" as Decision

Rectangle {
    id: detail

    property var rule: null
    property var care: null
    property bool triedOpen: false

    readonly property var content: Decision.adviceDetail(rule, care)
    readonly property var triedRows: Decision.triedRows(care)

    implicitHeight: column.implicitHeight + 20
    radius: Tide.Tokens.radiusSmall
    color: Tide.Tokens.backgroundView
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
    Accessible.role: Accessible.Grouping
    Accessible.name: "Komentarz opiekuna"

    onCareChanged: triedOpen = false

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 10
        anchors.leftMargin: 12
        spacing: 6

        Text {
            objectName: "decisionCaretakerRecommendation"
            Layout.fillWidth: true
            visible: text !== ""
            text: detail.content.recommendation !== "" ? "Opiekun: " + detail.content.recommendation : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
        }
        Tide.Prose {
            objectName: "decisionCaretakerSummary"
            Layout.fillWidth: true
            visible: text !== ""
            text: detail.content.summary
            surface: Tide.Tokens.backgroundView
            pointSize: Tide.Tokens.fontSizePt
        }
        Tide.Button {
            objectName: "decisionTriedToggle"
            visible: detail.triedRows.length > 0
            text: Decision.triedToggleLabel(detail.care, detail.triedOpen)
            variant: "ghost"
            small: true
            onClicked: detail.triedOpen = !detail.triedOpen
        }
        ColumnLayout {
            objectName: "decisionTried"
            Layout.fillWidth: true
            visible: detail.triedOpen && detail.triedRows.length > 0
            spacing: 4
            Repeater {
                model: detail.triedOpen ? detail.triedRows : []
                delegate: ColumnLayout {
                    id: triedRow
                    objectName: "decisionTriedRow"
                    required property var modelData
                    readonly property string action: modelData.action
                    readonly property string reason: modelData.reason
                    readonly property string effect: modelData.effect
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                        objectName: "decisionTriedAction"
                        Layout.fillWidth: true
                        text: triedRow.action + " — " + triedRow.effect
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "decisionTriedReason"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: triedRow.reason
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
}
