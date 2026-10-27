// Kafelek Docker w docku (mock „Kafelki Tablica i Docker”, z lewej, zaraz po System): neutralna karta
// w text.secondary — kolor tylko w kropkach. „Docker · 6 projektów”, pod spodem „● 9 działa · ● 1 padł”
// (teal; róż tylko wtedy, gdy coś padło), liczby w Geist Mono. Bez Dockera albo usługi — wyciszone
// słowa („Docker nie działa”, „usługa nie działa”). Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import "../code/docker.js" as Logic
import "tide" as Tide

Tide.DockTile {
    id: tile

    property var snapshot: null
    property bool serviceUp: true

    readonly property string dockerStatus: Logic.status(snapshot, serviceUp)
    readonly property var counts: Logic.summary(snapshot)
    readonly property bool ok: dockerStatus === "ok"
    readonly property bool quiet: !ok || (counts.running === 0 && counts.failed === 0)
    // To samo słowami (dostępność i testy).
    readonly property string summary: Logic.tileValue(snapshot, serviceUp)

    label: Logic.tileLabel(snapshot, serviceUp)
    tint: Tide.Tokens.textSecondary
    tintStrength: 0.2
    Accessible.name: label + ": " + summary

    Row {
        spacing: 4

        Rectangle {
            visible: runningNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            width: 7
            height: 7
            radius: 3.5
            color: Tide.Tokens.working
        }
        Text {
            id: runningNumber
            visible: !tile.quiet && tile.counts.running > 0
            text: tile.counts.running
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: runningNumber.visible
            anchors.baseline: runningNumber.baseline
            text: Logic.runsWord(tile.counts.running)
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: runningNumber.visible && failedNumber.visible
            anchors.baseline: runningNumber.baseline
            text: "·"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Padnięte: jedyne miejsce, gdzie kafelek dostaje róż.
        Rectangle {
            visible: failedNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            width: 7
            height: 7
            radius: 3.5
            color: Tide.Tokens.negative
        }
        Text {
            id: failedNumber
            visible: tile.ok && tile.counts.failed > 0
            text: tile.counts.failed
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: failedNumber.visible
            anchors.baseline: failedNumber.baseline
            text: Logic.failedWord(tile.counts.failed)
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Nic nie działa, Docker nie działa albo usługa stoi.
        Text {
            visible: tile.quiet && !failedNumber.visible
            text: tile.summary
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}
