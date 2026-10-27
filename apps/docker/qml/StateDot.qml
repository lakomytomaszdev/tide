// Kropka stanu kontenera: run — teal, bad — róż (padł), off — puste kółko (zatrzymany).
import QtQuick
import "tide" as Tide

Rectangle {
    property string kind: "run"

    implicitWidth: 8
    implicitHeight: 8
    radius: 4
    color: kind === "run" ? Tide.Tokens.working : kind === "bad" ? Tide.Tokens.negative : "transparent"
    border.width: kind === "off" ? 1.5 : 0
    border.color: Tide.Tokens.textMuted
}
