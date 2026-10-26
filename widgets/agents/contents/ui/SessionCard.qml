// Karta jednej sesji agenta w popupie. Czekająca (agent stoi bez ciebie) jest wyróżniona
// brzoskwinią i ma pełny przycisk przejścia do terminala; skończona (done) jest spokojna — puste
// błękitne kółko i ten sam przycisk bez wyróżnienia; pracująca ma pulsującą kropkę i linię
// ostatniej czynności; bezczynna i zakończona są przygaszone.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.Card {
    id: card

    required property var session
    property real nowTs: Date.now() / 1000
    signal activateRequested(string sessionId)

    // Starsza usługa zgłaszała koniec tury jako waiting/reply — Theme.sessionState robi z tego done.
    readonly property string shownState: Tide.Theme.sessionState(session)
    readonly property bool waiting: shownState === "waiting"
    readonly property bool done: shownState === "done"
    readonly property bool clickable: session.canActivate === true
    readonly property bool scheduled: session.reason === "scheduled"
    // Przycisk przejścia: dla czekających wyróżniony, dla skończonych spokojny.
    readonly property bool showButton: (waiting || done) && clickable
    readonly property string buttonText: session.terminal && session.terminal !== "Konsole"
                                         ? "Przejdź do okna " + session.terminal : "Przejdź do terminala"

    highlighted: waiting
    accent: Tide.Tokens.waiting
    opacity: Tide.Theme.stateOpacity(shownState)
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
        case "question": return "pyta o decyzję"
        case "approval":
        case "permission": return "czeka na zgodę"
        default: return reason ? "czeka: " + reason : "czeka na ciebie"
        }
    }

    readonly property string toolName: session.tool === "claude" ? "Claude Code" : session.tool === "codex" ? "Codex" : session.tool
    // Linia stanu: „skończył · Codex · zaplanowane · worktree x · tytuł”.
    readonly property string stateLine: {
        const parts = [Tide.Theme.stateLabel(shownState), toolName]
        if (scheduled && shownState !== "working") parts.push("zaplanowane")
        if (session.worktree) parts.push("worktree " + session.worktree)
        if (session.title) parts.push(session.title)
        return parts.join("  ·  ")
    }
    readonly property string actionLine: {
        if (waiting) {
            const why = reasonText(session.reason)
            return session.lastAction ? why + " · " + session.lastAction : why
        }
        if (session.reason === "background")
            return "zadanie w tle" + (session.lastAction ? " · " + session.lastAction : "")
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
            sessionState: card.shownState
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
            text: card.ago(card.waiting || card.done ? card.session.sinceTs : card.session.lastActivityTs)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Text {
        Layout.fillWidth: true
        text: card.stateLine
        color: card.waiting ? Tide.Tokens.waiting : Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    Text {
        Layout.fillWidth: true
        visible: text !== ""
        text: card.actionLine
        color: card.shownState === "working" ? Tide.Tokens.working : Tide.Tokens.textMuted
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    // Przycisk dla czekających i skończonych sesji z ustalonym oknem: czekająca — pełna
    // brzoskwinia, skończona — przezroczysty z obwódką, żeby nie konkurował z tymi, które czekają.
    Rectangle {
        visible: card.showButton
        Layout.topMargin: 4
        implicitHeight: buttonLabel.implicitHeight + 12
        implicitWidth: buttonLabel.implicitWidth + 28
        radius: Tide.Tokens.radiusSmall
        color: card.waiting ? (buttonArea.containsMouse ? Qt.lighter(Tide.Tokens.waiting, 1.08) : Tide.Tokens.waiting)
                            : Tide.Theme.alpha(Tide.Tokens.textPrimary, buttonArea.containsMouse ? 0.10 : 0.05)
        border.width: card.waiting ? 0 : 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.14)

        Text {
            id: buttonLabel
            anchors.centerIn: parent
            text: card.buttonText
            color: card.waiting ? Tide.Tokens.backgroundWindow : Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: card.waiting ? Font.DemiBold : Font.Medium
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
