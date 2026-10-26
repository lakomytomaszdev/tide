// Komunikat o stanie usługi nad kartami: brak tokenu, usługa nie działa (róż),
// odczyt wstrzymany przy pełnym ekranie (masło). Bez problemu karta jest ukryta.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.Card {
    id: notice

    required property var source           // LimitsSource

    readonly property var snapshot: source.snapshot || {}
    readonly property bool problem: !source.configured || (source.checked && !source.available)
    readonly property string message: !source.configured ? "Brak tokenu usługi Limity AI"
        : source.checked && !source.available ? "Usługa Limity AI nie działa"
        : source.available && snapshot.fullscreenKnown === false ? "Czekam na stan pełnego ekranu z KDE"
        : source.available && snapshot.paused === true ? "Odczyt wstrzymany podczas pełnego ekranu"
        : ""
    readonly property string hint: !source.configured
        ? "Zainstaluj QuotaApp, a potem: make install-widget-limits"
        : problem ? "Uruchom: systemctl --user start limity-ai-widget"
        : ""

    visible: message !== ""
    highlighted: true
    accent: problem ? Tide.Tokens.negative : Tide.Tokens.neutral
    padding: 10
    spacing: 2

    Text {
        Layout.fillWidth: true
        text: notice.message
        wrapMode: Text.WordWrap
        color: notice.problem ? Tide.Tokens.negative : Tide.Tokens.neutral
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        font.weight: Font.DemiBold
    }
    Text {
        Layout.fillWidth: true
        visible: notice.hint !== ""
        text: notice.hint
        wrapMode: Text.WordWrap
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }
}
