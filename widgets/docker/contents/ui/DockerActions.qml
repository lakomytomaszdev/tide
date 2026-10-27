// Akcje kafelka Docker i ich stan, bez PlasmoidItem (da się je sprawdzić w teście z atrapą źródła):
// zatrzymanie projektu (GroupAction — zadanie w usłudze, wynik sygnałem ActionFinished) i uruchomienie
// padniętego kontenera (Start). busy i notes czyta popup; wynik gaśnie po noteMs, zadanie bez wyniku
// (usługa zniknęła) zwalnia przycisk po jobTimeoutMs. Wynik, który przyszedł przed odpowiedzią z `job`,
// czeka w early (najwyżej earlyMs) — cudze zadania (okno Tide) też tam trafiają i po chwili znikają.
// source: Tide.DaemonSource /Docker (albo atrapa z call(member, args, done) i sygnałem actionFinished).
import QtQuick
import "../code/docker.js" as Logic

Item {
    id: actions

    property var source: null
    property var busy: ({})                  // id grupy albo kontenera → akcja w toku
    property var notes: ({})                 // id grupy albo kontenera → {text, error}
    property var jobs: ({})                  // job → {key, action, ts}
    property var early: ({})                 // job → {text, ts}
    property int noteMs: 12000
    property int jobTimeoutMs: 600000
    property int earlyMs: 60000

    function setIn(map, key, value) {
        const next = Object.assign({}, map)
        if (value === undefined || value === null || value === "") delete next[key]
        else next[key] = value
        return next
    }

    function groupAction(project, action) {
        const key = "compose:" + project
        if (busy[key] || !source) return
        busy = setIn(busy, key, action)
        notes = setIn(notes, key, null)
        source.call("GroupAction", [project, action], function (value) {
            const started = Logic.parse(value)
            if (!started || !started.ok || !started.job) {
                actions.finish(key, Logic.groupNote(action, started || { ok: false, error: "usługa tide-daemon nie odpowiada" }))
                return
            }
            const waiting = actions.early[started.job]
            if (waiting) {
                actions.early = actions.setIn(actions.early, started.job, null)
                actions.finish(key, Logic.groupNote(action, Logic.parse(waiting.text)))
                return
            }
            actions.jobs = actions.setIn(actions.jobs, started.job, { key: key, action: action, ts: Date.now() })
        })
    }

    function startContainer(id) {
        if (busy[id] || !source) return
        busy = setIn(busy, id, "start")
        notes = setIn(notes, id, null)
        source.call("Start", [id], value => actions.finish(id, Logic.startNote(Logic.parse(value))))
    }

    function finish(key, note) {
        busy = setIn(busy, key, null)
        notes = setIn(notes, key, note)
        noteTimer.restart()
    }

    // ActionFinished(job, json) z usługi.
    function jobDone(job, text) {
        const entry = jobs[job]
        if (!entry) {
            // Odpowiedź z `job` jeszcze w drodze albo cudze zadanie — czeka chwilę, potem znika.
            const now = Date.now()
            const kept = {}
            for (const j in early) if (now - early[j].ts < earlyMs) kept[j] = early[j]
            kept[job] = { text: text, ts: now }
            early = kept
            return
        }
        jobs = setIn(jobs, job, null)
        finish(entry.key, Logic.groupNote(entry.action, Logic.parse(text)))
    }

    function expire() {
        const now = Date.now()
        for (const job in jobs) {
            if (now - jobs[job].ts > jobTimeoutMs)
                jobDone(job, JSON.stringify({ ok: false, error: "brak wyniku z usługi — sprawdź stan w oknie Tide" }))
        }
        const kept = {}
        for (const j in early) if (now - early[j].ts < earlyMs) kept[j] = early[j]
        early = kept
    }

    Connections {
        target: actions.source
        ignoreUnknownSignals: true
        function onActionFinished(job, text) { actions.jobDone(job, text) }
    }
    Timer {
        id: noteTimer
        interval: actions.noteMs
        onTriggered: actions.notes = ({})
    }
    Timer {
        interval: Math.max(50, Math.min(30000, actions.jobTimeoutMs / 2))
        repeat: true
        running: Object.keys(actions.jobs).length > 0 || Object.keys(actions.early).length > 0
        onTriggered: actions.expire()
    }
}
