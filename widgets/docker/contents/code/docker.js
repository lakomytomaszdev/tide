// Logika kafelka Docker bez wyglądu: liczby i teksty po polsku z migawki tide-daemon (/Docker
// Snapshot(), docs/tide-daemon.md#docker). Kafelek, popup, pasek boczny i podpowiedź biorą słowa
// stąd, żeby mówiły to samo.
.pragma library

function plural(n, one, few, many) {
    var abs = Math.abs(n)
    if (abs === 1) return one
    var tens = abs % 100
    var ones = abs % 10
    return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
}

function count(n, one, few, many) {
    return n + " " + plural(n, one, few, many)
}

// „działa” przy 1 i 5+, „działają” przy 2–4; „padł”, „padły”, „padło”.
function runsWord(n) { return plural(n, "działa", "działają", "działa") }
function failedWord(n) { return plural(n, "padł", "padły", "padło") }

function parse(text) {
    if (typeof text !== "string") return null
    try { return JSON.parse(text) } catch (e) { return null }
}

// Stan kafelka: ok (Docker odpowiada), down (usługa tide-daemon nie odpowiada), starting, docker (Docker
// nie działa, brak uprawnień, nieobsługiwany — komunikat w snapshot.error).
function status(snapshot, serviceUp) {
    if (!serviceUp) return "down"
    if (!snapshot || snapshot.state === "starting" || snapshot.state === undefined) return "starting"
    return snapshot.available ? "ok" : "docker"
}

function summary(snapshot) {
    var s = snapshot && snapshot.summary ? snapshot.summary : {}
    return { running: s.running || 0, stopped: s.stopped || 0, failed: s.failed || 0, projects: s.projects || 0,
             testcontainers: s.testcontainers || 0 }
}

// Etykieta kafelka: „Docker · 6 projektów” (projekty compose z czymś działającym).
function tileLabel(snapshot, serviceUp) {
    var n = status(snapshot, serviceUp) === "ok" ? summary(snapshot).projects : 0
    return n > 0 ? "Docker · " + count(n, "projekt", "projekty", "projektów") : "Docker"
}

// Wartość słowami (dostępność i testy): „9 działa · 1 padł”, „nic nie działa”, „Docker nie działa”.
function tileValue(snapshot, serviceUp) {
    var st = status(snapshot, serviceUp)
    if (st === "down") return "usługa nie działa"
    if (st === "starting") return "łączę się…"
    if (st === "docker") return snapshot.state === "denied" ? "brak dostępu" : "Docker nie działa"
    var s = summary(snapshot)
    var parts = []
    if (s.running > 0) parts.push(s.running + " " + runsWord(s.running))
    if (s.failed > 0) parts.push(s.failed + " " + failedWord(s.failed))
    return parts.length ? parts.join(" · ") : "nic nie działa"
}

function toolTip(snapshot, serviceUp) {
    var st = status(snapshot, serviceUp)
    if (st === "down") return "Usługa tide-daemon nie działa"
    if (st === "starting") return "Tide łączy się z Dockerem"
    if (st === "docker") return snapshot.error || "Docker nie działa"
    var s = summary(snapshot)
    var text = "Kontenery Dockera: " + (s.running > 0
        ? s.running + " " + plural(s.running, "kontener", "kontenery", "kontenerów") + " " + runsWord(s.running)
          + (s.projects > 0 ? " w " + count(s.projects, "projekcie", "projektach", "projektach") : "")
        : "żaden nie działa")
    var failed = failedContainers(snapshot)
    if (failed.length > 0) text += ", " + (failed.length === 1 ? failed[0].name + " padł" : count(failed.length, "padł", "padły", "padło")
                                                                                           .replace(/^(\d+) (\S+)$/, "$1 $2: ") + failed.map(function (c) { return c.name }).join(", "))
    return text + "\nKliknij, aby zobaczyć projekty"
}

// Porty HTTP projektu: „:8080 :8000”.
function httpPorts(group) {
    var seen = {}
    var out = []
    var list = group.containers || []
    for (var i = 0; i < list.length; i++) {
        var ports = list[i].ports || []
        for (var j = 0; j < ports.length; j++) {
            var p = ports[j]
            if (p.likelyHttp && p.url && !seen[p.hostPort]) {
                seen[p.hostPort] = true
                out.push(":" + p.hostPort)
            }
        }
    }
    return out.join(" ")
}

// Projekty z czymś działającym (compose) i działające bez compose — wiersze popupu.
function projects(snapshot) {
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    var out = []
    for (var i = 0; i < groups.length; i++) {
        var g = groups[i]
        if (g.kind === "testcontainers" || !(g.running > 0)) continue
        // Jeden kontener bez compose — jego nazwa mówi więcej niż „bez compose”.
        var single = g.kind !== "compose" && g.running === 1
            ? (g.containers || []).filter(function (c) { return c.running })[0] : null
        out.push({ id: g.id, project: g.project || "", label: single ? single.name : g.label, compose: g.kind === "compose",
                   ports: httpPorts(g), count: g.running + "/" + (g.kind === "compose" ? g.total : g.running),
                   running: g.running, total: g.total })
    }
    return out
}

function failedContainers(snapshot) {
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    var out = []
    for (var i = 0; i < groups.length; i++) {
        if (groups[i].kind === "testcontainers") continue
        var list = groups[i].containers || []
        for (var j = 0; j < list.length; j++) if (list[j].failed) out.push(list[j])
    }
    return out
}

function ago(ts, now) {
    if (!ts) return ""
    var s = Math.max(0, now - ts)
    if (s < 60) return "przed chwilą"
    if (s < 3600) return Math.floor(s / 60) + " min temu"
    if (s < 86400) return Math.floor(s / 3600) + " h temu"
    var days = Math.floor(s / 86400)
    if (days === 1) return "wczoraj"
    if (days < 14) return days + " dni temu"
    return Math.floor(days / 7) + " tyg. temu"
}

// „padł 21 h temu · kod 137”, „brak pamięci (OOM) 2 h temu”, „nie wystartował”.
function failedLine(c, now) {
    if (c.state === "created") return "nie wystartował" + (c.reason ? " · " + c.reason.replace(/^nie wystartował:\s*/, "") : "")
    var when = ago(c.finishedTs, now)
    var head = c.oomKilled ? "brak pamięci (OOM)" : c.state === "restarting" ? "restartuje się po błędzie" : "padł"
    return head + (when ? " " + when : "") + (c.exitCode ? " · kod " + c.exitCode : "")
}

// Stopka: „Zatrzymane: 3 · testcontainers ukryte: 1” (zatrzymane bez padniętych — te są wyżej).
function footer(snapshot) {
    var s = summary(snapshot)
    var parts = []
    var stopped = s.stopped - s.failed
    if (stopped > 0) parts.push("Zatrzymane: " + stopped)
    if (s.testcontainers > 0) parts.push("testcontainers ukryte: " + s.testcontainers)
    return parts.join(" · ")
}

function bytes(value) {
    if (value === undefined || value === null || value < 0) return "—"
    var units = ["B", "KB", "MB", "GB", "TB"]
    var v = value
    var unit = 0
    while (v >= 1024 && unit < units.length - 1) {
        v /= 1024
        unit++
    }
    var text = v >= 10 || unit === 0 ? Math.round(v).toString() : v.toFixed(1).replace(".", ",")
    return text + " " + units[unit]
}

// Udział w całym procesorze (hostCpuPercent): „3%”, „0,4%”.
function percent(value) {
    if (typeof value !== "number" || !isFinite(value)) return "—"
    return value < 10 && Math.round(value * 10) % 10 !== 0 ? value.toFixed(1).replace(".", ",") + "%" : Math.round(value) + "%"
}

// Wynik akcji projektu (ActionFinished) albo kontenera (Start) — {text, error}.
var GROUP_VERB = { start: "uruchomiony", stop: "zatrzymany", restart: "zrestartowany" }
function groupNote(action, result) {
    if (!result) return { text: "brak odpowiedzi usługi", error: true }
    if (result.ok) return { text: result.note || ("projekt " + (GROUP_VERB[action] || "gotowy")), error: false }
    var failed = result.failed || []
    return { text: failed.length ? failed.map(function (f) { return f.name + ": " + f.error }).join("; ")
                                 : (result.error || "nie udało się"), error: true }
}

function startNote(result) {
    if (!result) return { text: "brak odpowiedzi usługi", error: true }
    if (!result.ok) return { text: result.error || "nie udało się", error: true }
    return { text: "uruchomiony" + (result.note ? " — " + result.note : ""), error: false }
}
