import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

QQC2.TextField {
    color: Tide.Tokens.textPrimary
    placeholderTextColor: Tide.Tokens.textMuted
    selectionColor: Tide.Tokens.accent
    selectedTextColor: Tide.Tokens.backgroundDesktop
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 14
    padding: 10
    background: Rectangle {
        color: Tide.Tokens.backgroundCard
        radius: Tide.Tokens.radiusFrame
        border.width: 1
        border.color: parent.activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)
    }
}
