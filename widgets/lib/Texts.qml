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

    // Przycisk przejścia do sesji: Codex Desktop otwiera wątek w aplikacji, terminal Konsole
    // przełącza kartę, inna aplikacja — samo okno.
    function activateButton(session) {
        if (session.activation === "deeplink") return "Otwórz w " + (session.terminal || "Codex")
        return session.terminal && session.terminal !== "Konsole"
               ? "Przejdź do okna " + session.terminal : "Przejdź do terminala"
    }

    // Na co czeka agent (pole reason przy stanie „waiting”).
    function reasonText(reason) {
        switch (reason) {
        case "question": return "pyta o decyzję"
        case "approval":
        case "permission": return "czeka na zgodę"
        default: return reason ? "czeka: " + reason : "czeka na ciebie"
        }
    }

    // Podagenci Codexa w linii stanu: „+1 podagent”, „+2 podagentów” u wątku głównego,
    // „podagent” (z przydomkiem, jeśli jest) u podagenta bez znanego rodzica.
    function subagentsNote(session) {
        const n = session.subagents || 0
        if (n > 0) return "+" + n + " " + (n === 1 ? "podagent" : "podagentów")
        if (session.subagent === true)
            return session.agentNickname ? "podagent " + session.agentNickname : "podagent"
        return ""
    }

    // Sesja na modelu lokalnym (tide-daemon: localModel z dziennika sesji): „lokalny · qwen3-coder:30b”.
    function localNote(session) {
        if (!session || session.localModel !== true) return ""
        return session.model ? "lokalny · " + session.model : "lokalny"
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

    // Prognoza limitu z tide-daemon (/Limits, docs/tide-daemon.md): „wyczerpie się ok. 17:40”,
    // „starczy do resetu” albo nic (za mało danych, brak usługi). Krótka wersja do wąskich wierszy:
    // „koniec ok. 17:40”.
    function forecastText(forecast) {
        return forecast && (forecast.state === "runs-out" || forecast.state === "lasts") ? forecast.text || "" : ""
    }

    function forecastShort(forecast) {
        return forecast && (forecast.state === "runs-out" || forecast.state === "lasts") ? forecast.short || "" : ""
    }

    // Dopisek kafelka w docku, tylko gdy limit skończy się przed resetem: „Claude do 17:40”,
    // „Claude do jutra 14:30”, „GPT do czwartku 11:00” — tide-daemon pisze dzień w mianowniku
    // („jutro 14:30”, „czwartek 11:00”), a po „do” idzie dopełniacz.
    function limitsRunoutDetail(label, forecast) {
        return forecast && forecast.state === "runs-out" && forecast.whenText
            ? label + " do " + untilWhen(forecast.whenText) : ""
    }

    readonly property var whenGenitive: ({ "jutro": "jutra", "poniedziałek": "poniedziałku", "wtorek": "wtorku",
                                           "środa": "środy", "czwartek": "czwartku", "piątek": "piątku",
                                           "sobota": "soboty", "niedziela": "niedzieli" })

    function untilWhen(when) {
        const text = String(when)
        const space = text.indexOf(" ")
        const head = space < 0 ? text : text.slice(0, space)
        return whenGenitive.hasOwnProperty(head) ? whenGenitive[head] + text.slice(head.length) : text
    }

    // Pierwsza linia podpowiedzi Limitów AI: ile zostało, niższy z limitów sesji i tygodnia.
    function limitsToolTip(available, gpt, claude) {
        if (!available) return "Usługa Limity AI nie działa"
        return "Pozostały limit AI: GPT " + percent(gpt) + ", Claude " + percent(claude)
    }

    // Raport poranny (tide-daemon /Reports Status()): kiedy powstał, względem „teraz” w czasie
    // lokalnym — „dziś 07:00”, „wczoraj”, starszy „24.10”. Pusty napis bez czasu.
    function reportWhen(generatedAt, nowTs) {
        if (typeof generatedAt !== "number" || !isFinite(generatedAt)) return ""
        const made = new Date(generatedAt * 1000)
        const now = new Date(nowTs * 1000)
        const midnight = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
        const days = Math.round((midnight(now) - midnight(made)) / 86400000)
        const two = n => (n < 10 ? "0" : "") + n
        if (days <= 0) return "dziś " + two(made.getHours()) + ":" + two(made.getMinutes())
        if (days === 1) return "wczoraj"
        return made.getDate() + "." + two(made.getMonth() + 1)
    }

    // Link w nagłówku paska bocznego: „Raport · dziś 07:00”; bez raportu pusty (link znika).
    function reportLink(status, nowTs) {
        if (!status || !status.path) return ""
        const when = reportWhen(status.generatedAt, nowTs)
        return when ? "Raport · " + when : "Raport"
    }

    // Nazwa dla czytnika ekranu: „Raport poranny · dziś 07:00 · 1 czeka na ciebie · nowy, nieotwarty”.
    function reportAccessible(status, nowTs) {
        if (!status || !status.path) return ""
        const parts = ["Raport poranny"]
        const when = reportWhen(status.generatedAt, nowTs)
        if (when) parts.push(when)
        if (typeof status.waiting === "number" && status.waiting > 0) parts.push(status.waiting + " czeka na ciebie")
        if (status.unread) parts.push("nowy, nieotwarty")
        return parts.join(" · ")
    }
}
