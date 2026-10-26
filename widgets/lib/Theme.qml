// Wspólne reguły wyglądu widżetów Tide: kolory stanów, czcionki, przezroczystości.
// Kolory pochodzą wyłącznie z Tokens (generowany przez widgets/stage.py z theme/palette/<wariant>.json).
pragma Singleton
import QtQuick

QtObject {
    // Geist i Geist Mono, jeśli są zainstalowane, inaczej czcionka motywu.
    readonly property string uiFamily: hasFont(Tokens.fontUi) ? Tokens.fontUi : Qt.application.font.family
    readonly property string monoFamily: hasFont(Tokens.fontMono) ? Tokens.fontMono : "monospace"

    function hasFont(family) {
        return family !== "" && Qt.fontFamilies().indexOf(family) >= 0
    }

    // Stany sesji agentów: working, waiting, idle, ended.
    function stateColor(state) {
        switch (state) {
        case "working": return Tokens.working
        case "waiting": return Tokens.waiting
        default: return Tokens.textMuted
        }
    }

    function stateLabel(state) {
        switch (state) {
        case "working": return "pracuje"
        case "waiting": return "czeka na ciebie"
        case "idle": return "bezczynna"
        case "ended": return "zakończona"
        default: return state
        }
    }

    // Poziom pozostałego limitu w procentach: powyżej 40 dobrze, 16–40 uwaga, do 15 mało,
    // null = brak danych (wyciszony).
    function levelColor(percent) {
        if (percent === null || percent === undefined) return Tokens.textMuted
        if (percent <= 15) return Tokens.negative
        if (percent <= 40) return Tokens.neutral
        return Tokens.positive
    }

    // Wyciszenie bezczynnych i zakończonych elementów.
    function stateOpacity(state) {
        return state === "idle" ? 0.62 : state === "ended" ? 0.45 : 1.0
    }

    function alpha(color, a) {
        return Qt.rgba(color.r, color.g, color.b, a)
    }

    // Tło lekko zabarwione kolorem (np. karta czekającej sesji w brzoskwini).
    function tinted(base, tint, strength) {
        return Qt.tint(base, alpha(tint, strength))
    }
}
