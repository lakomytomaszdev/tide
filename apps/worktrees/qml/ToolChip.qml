// Znacznik narzędzia, które pracowało w worktree: Codex (niebieski), Claude (brzoskwinia) albo „—”.
import QtQuick
import "tide" as Tide
import "worktrees.js" as Logic

Rectangle {
    id: chip

    property string tool: ""

    implicitHeight: label.implicitHeight + 6
    implicitWidth: label.implicitWidth + 16
    radius: height / 2
    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)

    Text {
        id: label
        anchors.centerIn: parent
        text: Logic.toolLabel(chip.tool)
        color: chip.tool === "codex" ? Tide.Tokens.sky : chip.tool === "claude" ? Tide.Tokens.peach : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
}
