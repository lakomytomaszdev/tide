// Wiersze listy agentów z gotowymi tekstami — wspólne dla kafelka paska bocznego (SidebarAgents)
// i popupu kafelka w docku (SessionsView), żeby obie listy układały i opisywały sesje tak samo.
// Kolejność: najpierw ci, którzy czekają na ciebie, potem pracujący, skończeni, bezczynni
// i (na życzenie) zakończeni; w każdej grupie najświeżsi u góry. Podagentów Codexa zwija już
// tide-daemon (pozycja wątku głównego ma licznik `subagents` i stan najpilniejszego z grupy).
import QtQuick
import "tide" as Tide

QtObject {
    id: model

    property var sessions: []
    property bool showEnded: false
    property real nowTs: Date.now() / 1000

    readonly property var rank: ({ waiting: 0, working: 1, done: 2, idle: 3, ended: 4 })
    readonly property var live: sessions.filter(s => Tide.Theme.sessionState(s) !== "ended")
    readonly property int endedCount: sessions.length - live.length
    readonly property int workingCount: countOf("working")
    readonly property int waitingCount: countOf("waiting")
    readonly property int doneCount: countOf("done")
    readonly property var ordered: (showEnded ? sessions : live).slice().sort((a, b) => {
        const ra = rankOf(a), rb = rankOf(b)
        return ra !== rb ? ra - rb : sinceOf(b) - sinceOf(a)
    })
    // Z tych obiektów rysujemy wiersze (SessionRow) i to samo sprawdzają testy.
    readonly property var rows: ordered.map(s => rowOf(s))

    function countOf(state) {
        return live.filter(s => Tide.Theme.sessionState(s) === state).length
    }
    function rankOf(session) {
        const value = rank[Tide.Theme.sessionState(session)]
        return value === undefined ? 5 : value
    }
    // Czekający i skończeni liczą czas od zmiany stanu, pozostali od ostatniej aktywności.
    function sinceOf(session) {
        const state = Tide.Theme.sessionState(session)
        return (state === "waiting" || state === "done" ? session.sinceTs : session.lastActivityTs) || 0
    }
    function toolName(tool) {
        return tool === "claude" ? "Claude Code" : tool === "codex" ? "Codex" : (tool || "")
    }
    // Druga, wyciszona linia (tylko w popupie): czekający — na co czeka i przy czym; pozostali —
    // narzędzie, podagenci, gałąź i tytuł rozmowy.
    function detailOf(session, state) {
        // Podagenci zaraz po pierwszej części — przy długiej linii koniec znika pod wielokropkiem.
        const parts = [state === "waiting" ? Tide.Texts.reasonText(session.reason) : toolName(session.tool),
                       Tide.Texts.subagentsNote(session)]
        if (state === "waiting") {
            parts.push(session.lastAction)
        } else {
            if (session.reason === "background" && state === "working") parts.push("zadanie w tle")
            if (session.reason === "scheduled" && state !== "working") parts.push("zaplanowane")
            parts.push(session.branch, session.title)
        }
        return parts.filter(p => p).join(" · ")
    }
    function rowOf(s) {
        const state = Tide.Theme.sessionState(s)
        const deeplink = s.activation === "deeplink"
        return {
            id: s.id,
            state: state,
            project: s.project || "—",
            word: Tide.Theme.shortStateLabel(state),
            ago: Tide.Theme.shortAgo(sinceOf(s), nowTs),
            detail: detailOf(s, state),
            // Przycisk tylko u czekających — tylko oni naprawdę cię potrzebują (decyzja 36).
            button: state === "waiting" && s.canActivate === true,
            clickable: s.canActivate === true,
            deeplink: deeplink,
            // Sesja uruchomiona przed chwilą z okna „Nowe zadanie” (tide-daemon, przez 10 min).
            fresh: s.newTask === true,
            buttonText: deeplink ? Tide.Texts.activateButton(s) : "Przejdź",
            // Pełny tekst przejścia (np. „Przejdź do terminala”) dla czytnika ekranu.
            activateText: Tide.Texts.activateButton(s)
        }
    }
}
