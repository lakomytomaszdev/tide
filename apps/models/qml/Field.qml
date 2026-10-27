// Pole formularza: tło background.view, ramka tekstu ~0,08, w fokusie akcent (jak w oknie Nowe zadanie).
import QtQuick
import "tide" as Tide

Rectangle {
    property bool focused: false
    property bool invalid: false
    radius: Tide.Tokens.radiusFrame
    color: Tide.Tokens.backgroundView
    border.width: 1
    border.color: invalid ? Tide.Theme.alpha(Tide.Tokens.rose, 0.6)
                : focused ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
}
