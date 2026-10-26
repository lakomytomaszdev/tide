// Karta jednej sesji agenta w popupie. Czekająca jest wyróżniona brzoskwinią i ma przycisk
// przejścia do terminala; pracująca ma pulsującą kropkę i linię ostatniej czynności;
// bezczynna i zakończona są przygaszone.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.Card {
    id: card

    required property var session
    property real nowTs: Date.now() / 1000
    signal activateRequested(string sessionId)

    readonly property bool waiting: session.state === "waiting"
    readonly property bool clickable: session.canActivate === true

    highlighted: waiting
    accent: Tide.Tokens.waiting
    opacity: Tide.Theme.stateOpacity(session.state)
    spacing: 4

    function ago(ts) {
        const s = Math.max(0, nowTs - ts)
        if (s < 60) return "teraz"
        if (s < 3600) return Math.floor(s / 60) + " min"
        const h = Math.floor(s / 3600)
        const m = Math.floor((s % 3600) / 60)
        return m > 0 && h < 10 ? h + " h " + m + " min" : h + " h"
    }

    function reasonText(reason) {
        switch (reason) {
        case "reply": return "czeka na odpowiedź"
        case "question": return "pyta o decyzję"
        case "approval":
        case "permission": return "czeka na zgodę"
        default: return reason ? "czeka: " + reason : "czeka na ciebie"
        }
    }

    readonly property string toolName: session.tool === "claude" ? "Claude Code" : session.tool === "codex" ? "Codex" : session.tool
    readonly property string actionLine: {
        if (waiting) {
            const why = reasonText(session.reason)
            return session.lastAction && session.reason !== "reply" ? why + " · " + session.lastAction : why
        }
        return session.lastAction || ""
    }

    // Cała karta przenosi do okna sesji, jeśli da się je ustalić.
    MouseArea {
        parent: card
        anchors.fill: parent
        enabled: card.clickable
        hoverEnabled: true
        cursorShape: card.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: card.activateRequested(card.session.id)
        z: -1

        Rectangle {
            anchors.fill: parent
            radius: card.radius
            color: Tide.Tokens.textPrimary
            opacity: parent.containsMouse ? 0.04 : 0
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Tide.StateDot {
            sessionState: card.session.state
            size: 9
        }
        Text {
            text: card.session.project || "—"
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1
            font.weight: Font.DemiBold
            elide: Text.ElideRight
            Layout.maximumWidth: card.width * 0.45
        }
        Rectangle {
            visible: !!card.session.branch
            radius: height / 2
            color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.16)
            implicitHeight: branchLabel.implicitHeight + 4
            implicitWidth: branchLabel.implicitWidth + 14
            Layout.maximumWidth: card.width * 0.38
            Text {
                id: branchLabel
                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - 14)
                text: card.session.branch
                color: Tide.Tokens.lavender
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
                elide: Text.ElideMiddle
            }
        }
        Item { Layout.fillWidth: true }
        Text {
            text: card.ago(card.waiting ? card.session.sinceTs : card.session.lastActivityTs)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Text {
        Layout.fillWidth: true
        text: {
            const parts = [Tide.Theme.stateLabel(card.session.state), card.toolName]
            if (card.session.worktree) parts.push("worktree " + card.session.worktree)
            if (card.session.title) parts.push(card.session.title)
            return parts.join("  ·  ")
        }
        color: card.waiting ? Tide.Tokens.waiting : Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    Text {
        Layout.fillWidth: true
        visible: text !== ""
        text: card.actionLine
        color: card.session.state === "working" ? Tide.Tokens.working : Tide.Tokens.textMuted
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    // Przycisk tylko dla czekających sesji z ustalonym oknem.
    Rectangle {
        visible: card.waiting && card.clickable
        Layout.topMargin: 4
        implicitHeight: buttonLabel.implicitHeight + 12
        implicitWidth: buttonLabel.implicitWidth + 28
        radius: Tide.Tokens.radiusSmall
        color: buttonArea.containsMouse ? Qt.lighter(Tide.Tokens.waiting, 1.08) : Tide.Tokens.waiting

        Text {
            id: buttonLabel
            anchors.centerIn: parent
            text: card.session.terminal && card.session.terminal !== "Konsole"
                  ? "Przejdź do okna " + card.session.terminal : "Przejdź do terminala"
            color: Tide.Tokens.backgroundWindow
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: buttonArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: card.activateRequested(card.session.id)
        }
    }
}
