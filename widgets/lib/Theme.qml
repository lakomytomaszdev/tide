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

    // Wyciszenie bezczynnych i zakończonych elementów.
    function stateOpacity(state) {
        return state === "idle" ? 0.62 : state === "ended" ? 0.45 : 1.0
    }

    // Worktrees: teal — pracuje agent, masło — zmiany albo niescalone commity, mięta — scalony.
    function worktreeColor(w) {
        if (w.hasAgent) return Tokens.working
        if (w.dirty !== 0 || (w.unmerged || 0) > 0) return Tokens.neutral
        if (w.isMain || w.missing || !w.mainBranch) return Tokens.textMuted
        return w.merged ? Tokens.positive : Tokens.textMuted
    }

    // Polska liczba mnoga: plural(5, "zmiana", "zmiany", "zmian") → "zmian".
    function plural(n, one, few, many) {
        const abs = Math.abs(n)
        if (abs === 1) return one
        const tens = abs % 100
        const ones = abs % 10
        return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
    }

    // Rozmiar na dysku: „840 MB”, „1,2 GB”; ujemny = jeszcze nie policzony.
    function formatBytes(bytes) {
        if (bytes === undefined || bytes === null || bytes < 0) return "…"
        const units = ["B", "KB", "MB", "GB", "TB"]
        let value = bytes
        let unit = 0
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024
            unit++
        }
        const text = value >= 10 || unit === 0 ? Math.round(value).toString() : value.toFixed(1).replace(".", ",")
        return text + " " + units[unit]
    }

    // Czas względny po polsku: „teraz”, „5 min temu”, „wczoraj”, „3 tyg. temu”.
    function agoText(ts, nowTs) {
        if (!ts) return "—"
        const s = Math.max(0, nowTs - ts)
        if (s < 60) return "teraz"
        if (s < 3600) return Math.floor(s / 60) + " min temu"
        if (s < 86400) return Math.floor(s / 3600) + " h temu"
        const days = Math.floor(s / 86400)
        if (days === 1) return "wczoraj"
        if (days < 14) return days + " dni temu"
        if (days < 60) return Math.floor(days / 7) + " tyg. temu"
        if (days < 365) return Math.floor(days / 30) + " mies. temu"
        const years = Math.floor(days / 365)
        return years + " " + plural(years, "rok", "lata", "lat") + " temu"
    }

    function alpha(color, a) {
        return Qt.rgba(color.r, color.g, color.b, a)
    }

    // Tło lekko zabarwione kolorem (np. karta czekającej sesji w brzoskwini).
    function tinted(base, tint, strength) {
        return Qt.tint(base, alpha(tint, strength))
    }
}
