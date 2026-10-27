// Logika widoku Docker bez wyglądu: grupy do pokazania, szukanie, teksty stanów, porty, liczby
// i komunikaty po polsku. Dane to migawka, Stats() i Disk() z tide-daemon (/Docker, kształty
// w docs/tide-daemon.md#docker).
.pragma library

var STOPPED_COLLAPSE_AFTER = 6   // tyle zatrzymanych pokazujemy rozwiniętych; więcej — zwinięte (padnięte zawsze widać)
var MAX_PORTS = 3                // portów w wierszu; reszta jako „+N”

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

// „działa” przy 1 i 5+, „działają” przy 2–4 (podmiotem są kontenery).
function runsWord(n) {
    return plural(n, "działa", "działają", "działa")
}

function failedWord(n) {
    return plural(n, "padł", "padły", "padło")
}

function parse(text, fallback) {
    try {
        var value = JSON.parse(text)
        return value === null || value === undefined ? fallback : value
    } catch (e) {
        return fallback
    }
}

// Rozmiar: „840 MB”, „2,2 GB”, „86 KB”; ujemny albo brak — „…”.
function bytes(value) {
    if (value === undefined || value === null || value < 0) return "…"
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

// CPU jak w docker stats: „0,4%”, „12%”; bez danych „—”.
function percent(value) {
    if (typeof value !== "number" || !isFinite(value)) return "—"
    if (value < 10) return value.toFixed(1).replace(".", ",") + "%"
    return Math.round(value) + "%"
}

// Jak długo działa: „teraz”, „8 min”, „13 h”, „2 dni”, „3 tyg.”.
function uptime(seconds) {
    var s = Math.max(0, seconds)
    if (s < 60) return "teraz"
    if (s < 3600) return Math.floor(s / 60) + " min"
    if (s < 86400) return Math.floor(s / 3600) + " h"
    var days = Math.floor(s / 86400)
    if (days < 14) return count(days, "dzień", "dni", "dni")
    return Math.floor(days / 7) + " tyg."
}

// Kiedy: „5 min temu”, „21 h temu”, „wczoraj”, „2 dni temu”, „2 tyg. temu”.
function ago(ts, now) {
    if (!ts) return ""
    var s = Math.max(0, now - ts)
    if (s < 60) return "przed chwilą"
    if (s < 3600) return Math.floor(s / 60) + " min temu"
    if (s < 86400) return Math.floor(s / 3600) + " h temu"
    var days = Math.floor(s / 86400)
    if (days === 1) return "wczoraj"
    if (days < 14) return days + " dni temu"
    if (days < 60) return Math.floor(days / 7) + " tyg. temu"
    if (days < 365) return Math.floor(days / 30) + " mies. temu"
    var years = Math.floor(days / 365)
    return count(years, "rok", "lata", "lat") + " temu"
}

// --- kontenery ------------------------------------------------------------------------------

// Kropka stanu: run (teal), bad (róż — padł), off (puste kółko — zatrzymany).
function dotKind(c) {
    if (c.running) return "run"
    return c.failed ? "bad" : "off"
}

// Druga linia wiersza: obraz i stan słowami. tone: bad (róż), warn (masło) albo muted.
function stateLine(c, now) {
    var parts = []
    var tone = "muted"
    if (c.running) {
        if (c.state === "paused") parts.push("wstrzymany")
        else if (c.state === "restarting") {
            parts.push("restartuje się" + (c.restartCount > 0 ? " (" + c.restartCount + "×)" : ""))
            tone = "warn"
        }
        if (c.health === "healthy") parts.push("zdrowy")
        else if (c.health === "unhealthy") { parts.push("niezdrowy"); tone = "warn" }
        else if (c.health === "starting") parts.push("uruchamia się")
        if (c.startedTs) parts.push(uptime(now - c.startedTs))
    } else if (c.failed) {
        tone = "bad"
        parts.push((c.reason || c.stateText || "padł") + (c.finishedTs ? " · " + ago(c.finishedTs, now) : ""))
    } else if (c.state === "created") {
        parts.push(c.reason || "utworzony, jeszcze nie uruchomiony")
    } else {
        var when = c.finishedTs ? ago(c.finishedTs, now) : ""
        parts.push(c.reason ? c.reason + (when ? " · " + when : "") : (c.stateText || "zatrzymany") + (when ? " " + when : ""))
    }
    var text = [c.image].concat(parts).filter(function (p) { return p }).join(" · ")
    return { text: text, tone: tone }
}

// Porty wiersza: HTTP z linkiem („:8080 ↗”), inne jako „5434→5432”. Bez powtórek portu hosta.
function ports(c) {
    var seen = {}
    var out = []
    var list = c.ports || []
    for (var i = 0; i < list.length; i++) {
        var p = list[i]
        var key = p.hostPort + "/" + p.proto
        if (!p.hostPort || seen[key]) continue
        seen[key] = true
        var http = !!(p.likelyHttp && p.url)
        out.push({
            http: http,
            url: http ? p.url : "",
            label: http ? ":" + p.hostPort : p.hostPort + "→" + p.containerPort + (p.proto === "udp" ? "/udp" : ""),
            title: (p.hostIp && p.hostIp !== "0.0.0.0" && p.hostIp !== "::" ? p.hostIp + ":" : "") + p.hostPort
                   + " → " + p.containerPort + "/" + p.proto
        })
    }
    // Linki najpierw — to je się klika.
    out.sort(function (a, b) { return (b.http ? 1 : 0) - (a.http ? 1 : 0) })
    return out
}

function statsOf(stats, c) {
    var all = stats && stats.containers ? stats.containers : {}
    return all[c.id] || null
}

// Co szukać w kontenerze: nazwy, obraz, projekt, usługa, porty („8080”, „:8080”).
function haystack(c, group) {
    var parts = [c.name, c.displayName, c.image, c.project, c.service, c.shortId]
    if (group) parts.push(group.label, group.repo ? group.repo.name : "")
    var list = c.ports || []
    for (var i = 0; i < list.length; i++) parts.push(":" + list[i].hostPort, String(list[i].containerPort))
    return parts.filter(function (p) { return p }).join(" ").toLowerCase()
}

function matches(c, group, query) {
    var words = (query || "").toLowerCase().split(/\s+/).filter(function (w) { return w })
    if (words.length === 0) return true
    var hay = haystack(c, group)
    return words.every(function (w) { return hay.indexOf(w) >= 0 })
}

// --- grupy -----------------------------------------------------------------------------------

// Grupy do pokazania. Projekt compose z czymś działającym zostaje grupą (z zatrzymanymi kontenerami
// w środku), działające bez compose — grupą „bez compose”, a całe zatrzymane projekty i zatrzymane
// bez compose trafiają do jednej grupy „Zatrzymane” na końcu (padnięte pierwsze). Testcontainers
// tylko z showTc. Szukanie zostawia grupy z pasującymi kontenerami.
function displayGroups(snapshot, showTc, query) {
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    var out = []
    var stopped = []
    for (var i = 0; i < groups.length; i++) {
        var g = groups[i]
        var containers = g.containers || []
        if (g.kind === "testcontainers") {
            if (showTc) out.push(makeGroup(g, containers, query))
            continue
        }
        if (g.kind === "compose" && g.running > 0) {
            out.push(makeGroup(g, containers, query))
            continue
        }
        var running = containers.filter(function (c) { return c.running })
        if (g.kind === "standalone" && running.length > 0) out.push(makeGroup(g, running, query))
        for (var j = 0; j < containers.length; j++) {
            if (!containers[j].running) stopped.push({ container: containers[j], group: g })
        }
    }
    stopped.sort(function (a, b) {
        if (a.container.failed !== b.container.failed) return a.container.failed ? -1 : 1
        return (b.container.finishedTs || 0) - (a.container.finishedTs || 0)
    })
    var shownStopped = stopped.filter(function (s) { return matches(s.container, s.group, query) })
    if (stopped.length > 0) {
        var failed = stopped.filter(function (s) { return s.container.failed }).length
        out.push({
            id: "stopped", kind: "stopped", title: "Zatrzymane", project: "", repo: null,
            running: 0, total: stopped.length, failed: failed,
            containers: shownStopped.map(function (s) { return s.container }),
            projects: shownStopped.map(function (s) { return s.group.kind === "compose" ? s.group.label : "" }),
            matched: shownStopped.length > 0 || !query
        })
    }
    return out.filter(function (g) { return g.matched })
}

function makeGroup(g, containers, query) {
    var titleHit = query && matchesTitle(g, query)
    var shown = titleHit ? containers : containers.filter(function (c) { return matches(c, g, query) })
    return {
        id: g.id, kind: g.kind, title: g.label, project: g.project || "", repo: g.repo || null,
        running: g.running, total: g.total, failed: g.failed,
        containers: shown, projects: shown.map(function () { return "" }),
        matched: shown.length > 0
    }
}

function matchesTitle(g, query) {
    var hay = [g.label, g.repo ? g.repo.name : ""].join(" ").toLowerCase()
    var words = query.toLowerCase().split(/\s+/).filter(function (w) { return w })
    return words.length > 0 && words.every(function (w) { return hay.indexOf(w) >= 0 })
}

// „3 z 3 działa”, „2 z 3 działa · 1 padł”; „Zatrzymane”: „4 kontenery · 1 padł”.
function groupStatus(g) {
    var failed = g.failed > 0 ? " · " + g.failed + " " + failedWord(g.failed) : ""
    if (g.kind === "stopped") return count(g.total, "kontener", "kontenery", "kontenerów") + failed
    if (g.kind === "compose" || g.kind === "testcontainers") return g.running + " z " + g.total + " działa" + failed
    return ""
}

// Akcje nagłówka projektu compose: logi, restart, uruchom (gdy coś stoi), zatrzymaj (gdy coś działa).
function groupActions(g) {
    if (g.kind !== "compose") return []
    var out = [{ id: "logs", label: "compose logs" }]
    if (g.running > 0) out.push({ id: "restart", label: "Restart" })
    if (g.running < g.total) out.push({ id: "start", label: g.running > 0 ? "Uruchom resztę" : "Uruchom" })
    if (g.running > 0) out.push({ id: "stop", label: "Zatrzymaj" })
    return out
}

// Kontener do logów projektu: pierwszy działający, inaczej pierwszy w ogóle.
function logsTarget(g) {
    var list = g.containers || []
    for (var i = 0; i < list.length; i++) if (list[i].running) return list[i]
    return list.length ? list[0] : null
}

// Wszystkie kontenery migawki (z grupą), np. do wyszukania elementu z showItem.
function allContainers(snapshot) {
    var out = []
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    for (var i = 0; i < groups.length; i++) {
        var list = groups[i].containers || []
        for (var j = 0; j < list.length; j++) out.push({ container: list[j], group: groups[i] })
    }
    return out
}

function findContainer(snapshot, ident) {
    if (!ident) return null
    var all = allContainers(snapshot)
    for (var i = 0; i < all.length; i++) {
        var c = all[i].container
        if (c.id === ident || c.name === ident || c.shortId === ident) return all[i]
    }
    if (ident.length >= 12) {
        var hits = all.filter(function (x) { return x.container.id.indexOf(ident) === 0 })
        if (hits.length === 1) return hits[0]
    }
    return null
}

function findGroup(snapshot, ident) {
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    for (var i = 0; i < groups.length; i++) {
        if (groups[i].id === ident || (groups[i].kind === "compose" && groups[i].project === ident)) return groups[i]
    }
    return null
}

// Id grupy widoku, w której widać kontener (zatrzymany w całkiem zatrzymanym projekcie — „stopped”).
function shownGroupId(entry) {
    var g = entry.group
    var c = entry.container
    if (g.kind === "testcontainers") return g.id
    if (g.kind === "compose" && g.running > 0) return g.id
    if (g.kind === "standalone" && c.running) return g.id
    return "stopped"
}

// --- nagłówek --------------------------------------------------------------------------------

// „9 kontenerów działa w 6 projektach i 1 bez compose · 4 zatrzymane, 1 z nich padł · CPU 3% · RAM 2,2 GB”.
function summaryText(snapshot, stats) {
    var s = snapshot && snapshot.summary ? snapshot.summary : {}
    var running = s.running || 0
    var standalone = 0
    var groups = snapshot && snapshot.groups ? snapshot.groups : []
    for (var i = 0; i < groups.length; i++) if (groups[i].kind === "standalone") standalone += groups[i].running || 0
    var parts = []
    if (running === 0) {
        parts.push("Nic nie działa")
    } else {
        var text = running + " " + plural(running, "kontener", "kontenery", "kontenerów") + " " + runsWord(running)
        var projects = s.projects || 0
        if (projects > 0) text += " w " + count(projects, "projekcie", "projektach", "projektach")
        if (standalone > 0) text += (projects > 0 ? " i " : " — ") + standalone + " bez compose"
        parts.push(text)
    }
    var stopped = s.stopped || 0
    if (stopped > 0) {
        var failed = s.failed || 0
        parts.push(count(stopped, "zatrzymany", "zatrzymane", "zatrzymanych")
                   + (failed > 0 ? ", " + (stopped === 1 ? failedWord(1) : failed + " z nich " + failedWord(failed)) : ""))
    }
    if (stats && stats.ok && stats.total) {
        parts.push("CPU " + percent(stats.total.hostCpuPercent))
        parts.push("RAM " + bytes(stats.total.memBytes))
    }
    return parts.join(" · ")
}

// Przycisk testcontainers: „testcontainers: 2 ukryte” albo „Ukryj testcontainers”.
function tcLabel(n, shown) {
    return shown ? "Ukryj testcontainers" : "testcontainers: " + n + " " + plural(n, "ukryty", "ukryte", "ukrytych")
}

// Stan Dockera bez danych: tytuł i podpowiedź do pustego stanu.
function problem(snapshot, serviceUp) {
    if (!serviceUp) return { title: "Usługa tide-daemon nie odpowiada",
                             text: "Uruchom ją: systemctl --user start tide-daemon", busy: false }
    var state = snapshot ? snapshot.state : ""
    var error = snapshot && snapshot.error ? snapshot.error : ""
    if (!snapshot || state === "starting" || state === undefined)
        return { title: "Łączę się z Dockerem…", text: "", busy: true }
    if (state === "denied") return { title: "Brak dostępu do Dockera", text: error, busy: false }
    if (state === "unsupported") return { title: "Ten Docker nie jest obsługiwany", text: error, busy: false }
    if (state === "unavailable") return { title: "Docker nie działa", text: error + (error ? "\n" : "")
                                          + "Tide sprawdza co kilka sekund i pokaże kontenery, gdy Docker wstanie.", busy: false }
    if (state === "error") return { title: "Docker odpowiedział błędem", text: error, busy: false }
    return null
}

// --- zasoby i porządki ------------------------------------------------------------------------

// Pamięć całego komputera: limit kontenera bez własnego limitu to cała pamięć (największy z limitów).
function hostMemory(stats) {
    var all = stats && stats.containers ? stats.containers : {}
    var best = 0
    for (var id in all) best = Math.max(best, all[id].memLimitBytes || 0)
    return best
}

// „Najwięcej: youtrack 1,1 GB, web-1 640 MB” — dwa największe zużycia pamięci.
function topMemory(stats, snapshot, n) {
    var all = stats && stats.containers ? stats.containers : {}
    var names = {}
    var list = allContainers(snapshot)
    for (var i = 0; i < list.length; i++) {
        var c = list[i].container
        var g = list[i].group
        names[c.id] = g.kind === "compose" && c.displayName !== c.name ? g.label + " " + c.displayName : c.name
    }
    var entries = []
    for (var id in all) entries.push({ name: names[id] || all[id].name, mem: all[id].memBytes || 0 })
    entries.sort(function (a, b) { return b.mem - a.mem })
    return entries.slice(0, n || 2).filter(function (e) { return e.mem > 0 })
                  .map(function (e) { return e.name + " " + bytes(e.mem) }).join(", ")
}

function category(disk, id) {
    var list = disk && disk.categories ? disk.categories : []
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i]
    return null
}

// „Na dysku: obrazy 10,4 GB, wolumeny 2,4 GB, pamięć podręczna budowania 6,8 GB”.
function diskLine(disk) {
    if (!disk || !disk.ok) return ""
    var parts = []
    var images = category(disk, "images")
    var volumes = category(disk, "volumes")
    var cache = category(disk, "cache")
    if (images) parts.push("obrazy " + bytes(images.totalBytes))
    if (volumes) parts.push("wolumeny " + bytes(volumes.totalBytes))
    if (cache) parts.push("pamięć podręczna budowania " + bytes(cache.totalBytes))
    return parts.length ? "Na dysku: " + parts.join(", ") : ""
}

function pickedIds(categories, picks) {
    return (categories || []).filter(function (k) { return picks[k.id] === true && k.count > 0 })
                             .map(function (k) { return k.id })
}

function pickedBytes(categories, picks) {
    return (categories || []).filter(function (k) { return picks[k.id] === true && k.count > 0 })
                             .reduce(function (sum, k) { return sum + Math.max(0, k.reclaimableBytes || 0) }, 0)
}

// Zatrzymane kontenery z migawki (także testcontainers — containers/prune usuwa wszystkie zatrzymane).
function stoppedNames(snapshot) {
    return allContainers(snapshot).filter(function (x) { return !x.container.running })
                                  .map(function (x) { return x.container.name })
}

// Potwierdzenie porządków: dokładnie to, co zniknie, w kolejności usuwania (obrazy → wolumeny →
// pamięć podręczna → kontenery). Linia: {title, size, lines: [{text, warn}]}.
function pruneSummary(categories, picks, snapshot) {
    var order = ["images", "volumes", "cache", "stopped"]
    var out = []
    for (var i = 0; i < order.length; i++) {
        var k = null
        for (var j = 0; j < (categories || []).length; j++) if (categories[j].id === order[i]) k = categories[j]
        if (!k || picks[k.id] !== true || !(k.count > 0)) continue
        var lines = []
        if (k.id === "images") {
            lines.push({ text: count(k.count, "obraz", "obrazy", "obrazów") + " bez żadnego kontenera, także zatrzymanego", warn: false })
        } else if (k.id === "volumes") {
            lines.push({ text: count(k.count, "wolumen", "wolumeny", "wolumenów") + " bez kontenera (także nazwane)", warn: false })
            lines.push({ text: "mogą trzymać dane baz — usuniętych nie da się odzyskać", warn: true })
        } else if (k.id === "cache") {
            lines.push({ text: count(k.count, "wpis", "wpisy", "wpisów") + " nieużywanej pamięci podręcznej budowania — następne budowanie potrwa dłużej", warn: false })
        } else if (k.id === "stopped") {
            var names = stoppedNames(snapshot)
            lines.push({ text: count(k.count, "kontener", "kontenery", "kontenerów")
                               + (names.length ? ": " + names.slice(0, 8).join(", ") + (names.length > 8 ? " i " + (names.length - 8) + " więcej" : "") : ""),
                         warn: false })
            lines.push({ text: "ich nazwane wolumeny zostają", warn: false })
            if (k.anonymousVolumes > 0)
                lines.push({ text: count(k.anonymousVolumes, "kontener ma", "kontenery mają", "kontenerów ma")
                                   + " wolumeny anonimowe — po usunięciu kontenera ich dane (np. bazy) zostaną bez nazwy", warn: true })
        }
        out.push({ id: k.id, title: k.label, size: bytes(k.reclaimableBytes), lines: lines })
    }
    return out
}

// Wynik porządków (ActionFinished): {title, ok, lines: [{text, error}]}.
function pruneResult(result) {
    if (!result) return { ok: false, title: "Porządki: brak wyniku z usługi", lines: [] }
    var details = result.details || []
    var lines = details.map(function (d) {
        return d.ok ? { text: d.label + ": odzyskano " + bytes(d.reclaimedBytes || 0)
                              + (d.removed ? " (" + count(d.removed, "element", "elementy", "elementów") + ")" : ""), error: false }
                    : { text: d.label + ": " + (d.error || "nie udało się"), error: true }
    })
    if (!details.length && result.error) lines.push({ text: result.error, error: true })
    var failed = lines.filter(function (l) { return l.error }).length
    var title = (result.ok ? "Odzyskano " : (failed < details.length ? "Częściowo — odzyskano " : "Nie udało się — odzyskano "))
                + bytes(result.reclaimedBytes || 0)
    return { ok: !!result.ok, title: title, lines: lines }
}

// --- wyniki akcji ------------------------------------------------------------------------------

var DONE_VERB = { start: "uruchomiony", stop: "zatrzymany", restart: "zrestartowany", remove: "usunięty" }
var BUSY_VERB = { start: "uruchamiam…", stop: "zatrzymuję… (Docker czeka do 10 s)", restart: "restartuję…", remove: "usuwam…" }
var GROUP_VERB = { start: "Uruchomiono", stop: "Zatrzymano", restart: "Zrestartowano" }
var GROUP_BUSY = { start: "uruchamiam projekt…", stop: "zatrzymuję projekt…", restart: "restartuję projekt…" }

// Wynik pojedynczej akcji: {text, error}.
function actionNote(action, result) {
    if (!result) return { text: "brak odpowiedzi usługi", error: true }
    if (!result.ok) return { text: result.error || "nie udało się", error: true }
    return { text: (DONE_VERB[action] || "gotowe") + (result.note ? " — " + result.note : ""), error: false }
}

// Wynik akcji na projekcie: {text, error}.
function groupNote(action, result) {
    if (!result) return { text: "brak odpowiedzi usługi", error: true }
    if (result.note && result.ok) return { text: result.note, error: false }
    var done = (result.done || []).length
    var failed = result.failed || []
    if (result.ok) return { text: (GROUP_VERB[action] || "Gotowe") + ": " + count(done, "kontener", "kontenery", "kontenerów"),
                            error: false }
    if (failed.length) return { text: (done ? (GROUP_VERB[action] || "Gotowe") + ": " + done + ", " : "") + "nie udało się: "
                                      + failed.map(function (f) { return f.name + " — " + f.error }).join("; "), error: true }
    return { text: result.error || "nie udało się", error: true }
}
