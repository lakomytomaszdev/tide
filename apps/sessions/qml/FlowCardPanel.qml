// Karta prowadzona przez usługę: tickety, ich kroki oraz oś zdarzeń z jednej migawki /Board.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

Item {
    id: panel
    property var card: null
    readonly property var derived: card && card.derived ? card.derived : ({})
    readonly property var flow: derived.flow || ({})

    function stepsFor(ticket) {
        return (flow.steps || []).filter(s => s.ticket === ticket)
    }

    function stateText(state) {
        switch (state) {
        case "w-toku": return "w toku"
        case "review": return "sprawdzanie"
        case "czeka": return "czeka"
        case "gotowe": return "gotowe"
        case "odrzucone": return "odrzucone"
        default: return "do zrobienia"
        }
    }

    function stepText(step) {
        const kinds = { planning: "planowanie", "plan-fix": "poprawa planu", "plan-review": "sprawdzenie planu",
                        "plan-revise": "poprawa planu", "plan-amend": "zmiana planu", ticket: "wykonanie",
                        fix: "poprawki", review: "sprawdzenie" }
        return (kinds[step.kind] || "krok") + " · " + (step.executor || "wykonawca") + " · "
                + (step.result || "pracuje")
    }

    function eventText(event) {
        const kinds = { "card.started": "Karta ruszyła", "step.started": "Krok ruszył",
                        "step.finished": "Krok zakończony", "step.waiting": "Krok czeka",
                        "plan.approved": "Plan zatwierdzony", "plan.finished": "Plan wykonany",
                        "card.paused": "Karta wstrzymana", "card.resumed": "Karta wznowiona",
                        "card.stalled": "Karta stoi" }
        return (kinds[event.type] || "Zmiana") + (event.ticket ? " · " + event.ticket : "")
                + (event.executor ? " · " + event.executor : "")
                + (event.reason ? " · " + event.reason : "")
    }

    Flickable {
        id: scroll
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight + 44
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

        ColumnLayout {
            id: content
            x: 22
            y: 20
            width: Math.max(0, scroll.width - 44)
            spacing: 15

            Text {
                Layout.fillWidth: true
                text: panel.card ? panel.card.id + " · " + panel.card.title : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 21
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                Accessible.role: Accessible.Heading
            }
            Text {
                Layout.fillWidth: true
                text: panel.derived.note || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                visible: panel.stepsFor("").length > 0
                text: "Kroki karty"
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Repeater {
                model: panel.stepsFor("")
                Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "↳ " + panel.stepText(modelData)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
            Text {
                Layout.fillWidth: true
                text: "Tickety · " + (panel.derived.tickets || []).length
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Repeater {
                model: panel.derived.tickets || []
                Rectangle {
                    id: ticketEntry
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: ticketContent.implicitHeight + 20
                    radius: Tide.Tokens.radiusCard
                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.035)
                    ColumnLayout {
                        id: ticketContent
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 10
                        spacing: 5
                        Text {
                            Layout.fillWidth: true
                            text: ticketEntry.modelData.id + " · " + ticketEntry.modelData.title
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: panel.stateText(ticketEntry.modelData.state)
                                  + (ticketEntry.modelData.wave ? " · fala " + ticketEntry.modelData.wave : "")
                                  + (ticketEntry.modelData.waitReason ? " · " + ticketEntry.modelData.waitReason : "")
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                        Repeater {
                            model: panel.stepsFor(ticketEntry.modelData.id)
                            Text {
                                required property var modelData
                                Layout.fillWidth: true
                                text: "↳ " + panel.stepText(modelData)
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                Layout.topMargin: 10
                text: "Oś zdarzeń"
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Repeater {
                model: panel.flow.timeline || []
                Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: panel.eventText(modelData)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
