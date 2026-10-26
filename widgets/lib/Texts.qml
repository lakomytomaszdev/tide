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

    // Sesje, w których agent skończył turę i niczego nie blokuje (stan „done”). Przymiotnik
    // zamiast czasownika: „skończył/skończyli” wymusza rodzaj agenta (męskoosobowy, który bez
    // rzeczownika brzmi obco: „2 skończyli”), a podmiotem kafelka są sesje — „1 gotowa”,
    // „2 gotowe”, „5 gotowych”. Krótsze też od „skończyły”, a kafelek w docku liczy się w pikselach.
    function doneWord(n) {
        return Theme.plural(n, "gotowa", "gotowe", "gotowych")
    }

    // „1 pracuje”, „2 pracują”, „5 pracują”.
    function working(n) {
        return n + " " + workingWord(n)
    }

    // „1 czeka”, „3 czekają”.
    function waiting(n) {
        return n + " " + waitingWord(n)
    }

    // „1 gotowa”, „2 gotowe”, „5 gotowych”.
    function done(n) {
        return n + " " + doneWord(n)
    }

    // Linia wartości kafelka agentów: tylko niezerowe części, bez sesji — „brak sesji”.
    // doneCount jest opcjonalny (starsze wywołania podają tylko dwie liczby).
    function agentsSummary(workingCount, waitingCount, doneCount) {
        const parts = []
        if (workingCount > 0) parts.push(working(workingCount))
        if (waitingCount > 0) parts.push(waiting(waitingCount))
        if (doneCount > 0) parts.push(done(doneCount))
        return parts.length ? parts.join(" · ") : "brak sesji"
    }

    function agentsToolTip(available, workingCount, waitingCount, doneCount) {
        if (!available) return "Usługa tide-daemon nie działa"
        const parts = []
        if (workingCount > 0) parts.push(working(workingCount))
        if (waitingCount > 0) parts.push(waiting(waitingCount) + " na ciebie")
        if (doneCount > 0) parts.push(done(doneCount))
        return "Sesje agentów AI: " + (parts.length ? parts.join(", ") : "żadna nie pracuje ani nie czeka")
    }

    // „15 do sprzątnięcia”, a gdy nie ma czego sprzątać — „nic do sprzątnięcia”.
    function cleanable(n) {
        return n > 0 ? n + " do sprzątnięcia" : "nic do sprzątnięcia"
    }

    // Linia wartości kafelka worktrees, w tym samym układzie co agenci: tylko niezerowe części.
    function worktreesSummary(activeCount, cleanCount) {
        const parts = []
        if (activeCount > 0) parts.push(activeCount + " w pracy")
        if (cleanCount > 0) parts.push(cleanable(cleanCount))
        return parts.length ? parts.join(" · ") : cleanable(0)
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
