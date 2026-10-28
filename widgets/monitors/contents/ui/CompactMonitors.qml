// Kafelek „Monitory” w docku Stream (PT-38): bieżące wejście prawego monitora i strzałka do drugiego w kolorze akcentu
// („PC → Xbox”, jak w mocku). Kliknięcie otwiera potwierdzenie (MonitorsPopup), samo nic nie przełącza.
// Bez PlasmoidItem (podgląd: widgets/preview_monitors.py).
import QtQuick
import "tide" as Tide
import "../code/monitors.js" as Monitors

Tide.DockTile {
    id: tile

    property var status: null
    property bool up: false
    readonly property var model: Monitors.tile(status, up)

    label: model.label
    tint: model.muted ? Tide.Tokens.textMuted : Tide.Tokens.accent

    Row {
        spacing: 6
        Text {
            visible: tile.model.busy !== ""
            text: tile.model.busy
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: tile.model.from !== ""
            text: tile.model.from
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: tile.model.to !== ""
            text: tile.model.to
            textFormat: Text.PlainText
            color: Tide.Tokens.accent
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}
