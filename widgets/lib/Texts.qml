// Teksty kafelków w docku i ich podpowiedzi, po polsku i z poprawną liczbą mnogą.
// Kafelek i podpowiedź (toolTipSubText) biorą słowa stąd, żeby mówiły to samo.
pragma Singleton
import QtQuick

QtObject {
    // „pracuje” przy 1, „pracują” przy 2–4 i 5+ (podmiotem są sesje agentów).
    function workingWord(n) {
        return Theme.plural(n, "pracuje", "pracują", "pracują")
    }

    // „czeka” przy 1, „czekają” przy pozostałych.
    function waitingWord(n) {
        return Theme.plural(n, "czeka", "czekają", "czekają")
    }

    // „1 pracuje”, „2 pracują”, „5 pracują”.
    function working(n) {
        return n + " " + workingWord(n)
    }

    // „1 czeka”, „3 czekają”.
    function waiting(n) {
        return n + " " + waitingWord(n)
    }

    // Linia wartości kafelka agentów: tylko niezerowe części, bez sesji — „brak sesji”.
    function agentsSummary(workingCount, waitingCount) {
        const parts = []
        if (workingCount > 0) parts.push(working(workingCount))
        if (waitingCount > 0) parts.push(waiting(waitingCount))
        return parts.length ? parts.join(" · ") : "brak sesji"
    }

    function agentsToolTip(available, workingCount, waitingCount) {
        if (!available) return "Usługa tide-daemon nie działa"
        const parts = []
        if (workingCount > 0) parts.push(working(workingCount))
        if (waitingCount > 0) parts.push(waiting(waitingCount) + " na ciebie")
        return "Sesje agentów AI: " + (parts.length ? parts.join(", ") : "żadna nie pracuje ani nie czeka")
    }

    // „15 do sprzątnięcia”, a gdy nie ma czego sprzątać — „nic do sprzątnięcia”.
    function cleanable(n) {
        return n > 0 ? n + " do sprzątnięcia" : "nic do sprzątnięcia"
    }

    function worktreesToolTip(available, cleanCount, cleanBytes, activeCount, totalCount) {
        if (!available) return "Usługa tide-daemon nie działa"
        const clean = cleanCount > 0 ? cleanable(cleanCount) + " (" + Theme.formatBytes(cleanBytes) + ")"
                                     : cleanable(cleanCount)
        return "Dodatkowe worktrees git: " + clean + ", " + activeCount + " w pracy, razem " + totalCount
            + "\nKliknij, aby otworzyć okno Worktrees"
    }

    // Procent całkowity albo „—” bez danych.
    function percent(value) {
        return typeof value === "number" && isFinite(value) ? Math.round(value) + "%" : "—"
    }

    // Pierwsza linia podpowiedzi Limitów AI: ile zostało, niższy z limitów sesji i tygodnia.
    function limitsToolTip(available, gpt, claude) {
        if (!available) return "Usługa Limity AI nie działa"
        return "Pozostały limit AI: GPT " + percent(gpt) + ", Claude " + percent(claude)
    }
}
