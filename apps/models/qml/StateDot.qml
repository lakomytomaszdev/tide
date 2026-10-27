// Kropka stanu serwera: online — teal (działa), offline — róż (nie odpowiada), checking — puste kółko
// (sprawdzam…).
import QtQuick
import "tide" as Tide

Rectangle {
    property string kind: "online"

    implicitWidth: 8
    implicitHeight: 8
    radius: 4
    color: kind === "online" ? Tide.Tokens.working : kind === "offline" ? Tide.Tokens.negative : "transparent"
    border.width: kind === "checking" ? 1.5 : 0
    border.color: Tide.Tokens.textMuted
}
