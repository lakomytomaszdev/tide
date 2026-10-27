// Logika kafelka Modele bez wyglądu: stan i teksty po polsku z Status() tide-daemon (/Models,
// docs/tide-daemon.md#modele-lokalne). Kafelek, popup i podpowiedź biorą słowa stąd, żeby mówiły to samo.
// Pola dokładane przez równoległe zmiany usługi są opcjonalne: `report` i `pulls` (PT-25: tryb raportu,
// pobieranie modeli po kluczu "<punkt>/<model>"), `queue` (PT-16: kolejka na punkt) — bez nich kafelek po
// prostu ich nie pokazuje.
.pragma library

// Role z models.json słowami: nazwa w popupie i to, co model robi („pracuje: opis kart”).
var ROLE_NAMES = { summary: "Raport", describe: "Opisy kart", agent: "Agent", embed: "Wyszukiwanie", polish: "Polszczyzna" }
var ROLE_WORK = { summary: "raport", describe: "opis kart", agent: "agent", embed: "wyszukiwanie", polish: "polszczyzna" }
var ROLE_ORDER = ["summary", "describe", "agent", "embed"]
// Role pracy w tle (raport, opisy kart): ich brakujący model to ostrzeżenie na kafelku. Opcjonalne role
// (agent, wyszukiwanie…) widać tylko w popupie.
var BACKGROUND_ROLES = ["summary", "describe"]
// Tryb raportu (report.json, PT-25): kto pisze „Na daily”.
var REPORT_MODES = { claude: "Claude", local: "lokalnie", rules: "bez modelu" }

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

// Obiekt JSON (nie tablica ani lista Qt).
function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value) && typeof value.length !== "number"
}

// --- punkty dostępu ---------------------------------------------------------------------------

// Serwer na tym komputerze: localhost, 127.x.x.x albo [::1].
function isLocalUrl(url) {
    return /^https?:\/\/(localhost|127(\.\d+){3}|\[::1\])(:\d+)?(\/|$)/i.test(String(url || ""))
}

// „Ten komputer” dla serwera lokalnego, inaczej nazwa punktu z ustawień („mac”).
function endpointTitle(ep) {
    if (!ep) return ""
    return isLocalUrl(ep.url) ? "Ten komputer" : String(ep.name || "")
}

// Adres bez schematu: „127.0.0.1:11434”.
function hostPort(url) {
    return String(url || "").replace(/^https?:\/\//i, "").replace(/\/+$/, "")
}

// Krótka nazwa modelu: bez „:latest” i bez przedrostka repozytorium („hf.co/speakleash/…”).
function shortModel(name) {
    var text = String(name || "")
    var slash = text.lastIndexOf("/")
    if (slash >= 0) text = text.slice(slash + 1)
    return text.replace(/:latest$/, "")
}

function endpointMap(snap) {
    return snap && isObject(snap.endpoints) ? snap.endpoints : {}
}

function defaultName(snap) {
    var eps = endpointMap(snap)
    if (snap && eps[snap["default"]]) return snap["default"]
    var names = Object.keys(eps).sort()
    return names.length ? names[0] : ""
}

function defaultEndpoint(snap) {
    var name = defaultName(snap)
    return name ? endpointMap(snap)[name] : null
}

// Punkty do popupu: domyślny pierwszy, reszta alfabetycznie; name zawsze ustawione.
function endpoints(snap) {
    var eps = endpointMap(snap)
    var first = defaultName(snap)
    var names = Object.keys(eps).sort(function (a, b) {
        if (a === first) return -1
        if (b === first) return 1
        return a < b ? -1 : a > b ? 1 : 0
    })
    var out = []
    for (var i = 0; i < names.length; i++) {
        var ep = eps[names[i]]
        if (!isObject(ep)) continue
        var copy = {}
        for (var key in ep) copy[key] = ep[key]
        copy.name = names[i]
        copy.isDefault = names[i] === first
        out.push(copy)
    }
    return out
}

// Tablica JS z tablicy albo listy Qt (QVariantList po drodze przez właściwość QML); inaczej pusta.
function asList(value) {
    if (Array.isArray(value)) return value
    if (value && typeof value === "object" && typeof value.length === "number") return Array.prototype.slice.call(value)
    return []
}

function loaded(ep) {
    return ep ? asList(ep.loaded).filter(isObject) : []
}

function installed(ep) {
    return ep ? asList(ep.models).filter(isObject) : []
}

// Stan całości: down (usługa nie odpowiada), starting (przed pierwszym odczytem), offline (domyślny serwer
// nie odpowiada), ok.
function status(snap, serviceUp) {
    if (!serviceUp) return "down"
    if (!isObject(snap) || snap.checked === false || !isObject(snap.endpoints)) return "starting"
    var ep = defaultEndpoint(snap)
    return ep && ep.online ? "ok" : "offline"
}

// --- czas -------------------------------------------------------------------------------------

// Chwila jako ms: ISO 8601 albo liczba (sekundy albo milisekundy). null — nieczytelna.
function toMs(value) {
    if (typeof value === "number" && isFinite(value) && value > 0) return value > 1e12 ? value : value * 1000
    if (typeof value === "string" && value !== "") {
        var ms = Date.parse(value)
        return isNaN(ms) ? null : ms
    }
    return null
}

function pad(n) { return n < 10 ? "0" + n : "" + n }

// Kiedy model zwolni pamięć: „za 4 min”, „za chwilę”, „o 17:05”, „zostaje w pamięci” (bez terminu).
function unloadText(expiresAt, nowMs) {
    var ms = toMs(expiresAt)
    if (ms === null) return ""
    var s = (ms - nowMs) / 1000
    if (s > 86400 || new Date(ms).getFullYear() < 2000) return "zostaje w pamięci"
    if (s <= 0) return "zwalnia pamięć"
    if (s < 60) return "zwolni pamięć za chwilę"
    if (s < 3600) return "zwolni pamięć za " + Math.round(s / 60) + " min"
    var d = new Date(ms)
    return "zwolni pamięć o " + pad(d.getHours()) + ":" + pad(d.getMinutes())
}

// Od kiedy coś trwa: „od chwili”, „od 2 min”, „od 1 h”.
function sinceText(value, nowMs) {
    var ms = toMs(value)
    if (ms === null) return ""
    var s = Math.max(0, (nowMs - ms) / 1000)
    if (s < 60) return "od chwili"
    if (s < 3600) return "od " + Math.floor(s / 60) + " min"
    return "od " + Math.floor(s / 3600) + " h"
}

function bytes(value) {
    if (typeof value !== "number" || !isFinite(value) || value < 0) return ""
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

// --- model w pamięci ----------------------------------------------------------------------------

// Model chmurowy Ollamy (`…:cloud`, `…:120b-cloud`): serwer tylko przekazuje go na ollama.com. Status() daje
// `cloud: true`; nazwę sprawdzamy też sami (starsza usługa, /api/ps). Ta sama zasada co models.config.is_cloud_model.
function isCloudModel(m) {
    if (isObject(m) && m.cloud === true) return true
    var name = isObject(m) ? m.name : m
    if (typeof name !== "string") return false
    var i = name.lastIndexOf(":")
    return i > 0 && /cloud$/i.test(name.slice(i + 1))
}

function gpuText(m) {
    if (isCloudModel(m)) return "w chmurze Ollamy"
    if (typeof m.gpuPercent !== "number") return "w pamięci"
    return m.gpuPercent >= 100 ? "na karcie 100%" : "na karcie " + Math.round(m.gpuPercent) + "%"
}

// Model w całości na karcie pisze szybko; część na procesorze — kilkanaście razy wolniej (PT-13).
function onCard(m) {
    return typeof m.gpuPercent === "number" && m.gpuPercent >= 100
}

// Druga linia wiersza modelu: „5,6 GB · kontekst 8192 · zwolni pamięć za 4 min”.
function loadedDetail(m, nowMs) {
    if (isCloudModel(m)) return "Tide go nie używa — fakty wyszłyby z komputera"
    var parts = []
    var size = bytes(m.size)
    if (size) parts.push(size)
    if (typeof m.gpuPercent === "number" && m.gpuPercent < 100) parts.push("reszta na procesorze — wolniej")
    if (typeof m.contextLength === "number" && m.contextLength > 0) parts.push("kontekst " + m.contextLength)
    var unload = unloadText(m.expiresAt, nowMs)
    if (unload) parts.push(unload)
    return parts.join(" · ")
}

// --- kolejka (PT-16) i pobieranie (PT-25) ----------------------------------------------------------

function roleWord(role) {
    return ROLE_WORK[role] || String(role || "zadanie")
}

function roleName(role) {
    return ROLE_NAMES[role] || String(role || "")
}

// Kolejka punktu: {running: {role, model, since} | null, waiting: n, waitingRoles: [...]}; null — brak pola.
function queueFor(snap, name) {
    var all = snap && isObject(snap.queue) ? snap.queue : null
    var q = all && isObject(all[name]) ? all[name] : null
    return q ? queueOf(q) : null
}

// Wywołania, które trwają albo czekają jeszcze na STARYM adresie punktu (adres zmieniony w trakcie, PT-16):
// Status().queue.<punkt>.other = [{lane, running, waiting}]. Linie do popupu, bezczynne i zepsute pomijane:
// „Na starym adresie 127.0.0.1:11434: pracuje: raport (qwen3:8b) od 3 min”.
function queueOther(snap, name, nowMs) {
    var all = snap && isObject(snap.queue) ? snap.queue : null
    var list = all && isObject(all[name]) ? asList(all[name].other) : []
    var out = []
    for (var i = 0; i < list.length; i++) {
        if (!isObject(list[i])) continue
        var line = queueLine(queueOf(list[i]), nowMs)
        if (!line) continue
        var lane = typeof list[i].lane === "string" ? list[i].lane.replace(/^[a-z]+:\/\//, "") : ""
        out.push("Na starym adresie" + (lane ? " " + lane : "") + ": " + line.charAt(0).toLowerCase() + line.slice(1))
    }
    return out
}

function queueOf(q) {
    var waiting = q.waiting
    var list = asList(waiting)
    var n = typeof waiting === "number" ? waiting : list.length
    var roles = []
    for (var i = 0; i < list.length; i++) {
        var r = isObject(list[i]) ? list[i].role : list[i]
        if (typeof r === "string" && r && roles.indexOf(roleWord(r)) < 0) roles.push(roleWord(r))
    }
    return { running: isObject(q.running) ? q.running : null, waiting: n, waitingRoles: roles }
}

function queueBusy(q) {
    return !!q && (q.running !== null || q.waiting > 0)
}

// Oczekujące jedną formą wszędzie: „2 w kolejce”, w popupie z rolami: „2 w kolejce: raport, opis kart”.
function waitingText(q, withRoles) {
    return q.waiting + " w kolejce" + (withRoles && q.waitingRoles.length ? ": " + q.waitingRoles.slice(0, 3).join(", ") : "")
}

// Krótko do kafelka: „pracuje: opis kart · 2 w kolejce”, „3 w kolejce”.
function queueShort(q) {
    if (!queueBusy(q)) return ""
    if (!q.running) return waitingText(q, false)
    return "pracuje: " + roleWord(q.running.role) + (q.waiting > 0 ? " · " + waitingText(q, false) : "")
}

// Pełniej do popupu: „Pracuje: opis kart (qwen3:8b) od 2 min · 2 w kolejce: raport”.
function queueLine(q, nowMs) {
    if (!queueBusy(q)) return ""
    var parts = []
    if (q.running) {
        var model = q.running.model ? " (" + shortModel(q.running.model) + ")" : ""
        var since = sinceText(q.running.since, nowMs)
        parts.push("Pracuje: " + roleWord(q.running.role) + model + (since ? " " + since : ""))
    }
    if (q.waiting > 0) parts.push(waitingText(q, true))
    return parts.join(" · ")
}

function pullPercent(p) {
    if (typeof p.total === "number" && p.total > 0 && typeof p.completed === "number")
        return Math.max(0, Math.min(100, 100 * p.completed / p.total))
    return null
}

// Pobieranie modeli na punkt (PT-25, PullModel): Status().pulls to obiekt po kluczu "<punkt>/<model>",
// wpis {endpoint, model, status, completed, total, running, error}; zakończony znika po 15 s, nieudany po
// 60 s, pusty obiekt — nic się nie pobiera. Wpis należy do punktu z pola endpoint, a bez niego — z klucza.
// Wynik: [{model, percent, state: running|done|error, error}].
function pullsFor(snap, name) {
    var raw = snap && isObject(snap.pulls) ? snap.pulls : null
    if (!raw) return []
    var out = []
    var keys = Object.keys(raw)
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i]
        var p = raw[key]
        if (!isObject(p)) continue
        var own = typeof p.endpoint === "string" && p.endpoint ? p.endpoint === name : key.indexOf(name + "/") === 0
        if (!own) continue
        var model = typeof p.model === "string" && p.model ? p.model : key.slice(key.indexOf("/") + 1)
        var error = typeof p.error === "string" ? p.error : ""
        var state = p.status === "error" || error ? "error" : p.status === "success" ? "done" : "running"
        out.push({ model: shortModel(model), percent: pullPercent(p), state: state, error: error })
    }
    return out
}

// „Pobieram bge-m3 · 42%”, „Pobieram bge-m3…”, „Pobrano bge-m3”, „Nie udało się pobrać bge-m3: <błąd>”.
function pullLine(p) {
    if (p.state === "error") return "Nie udało się pobrać " + p.model + (p.error ? ": " + p.error : "")
    if (p.state === "done") return "Pobrano " + p.model
    return "Pobieram " + p.model + (p.percent !== null ? " · " + Math.floor(p.percent) + "%" : "…")
}

// --- role ------------------------------------------------------------------------------------------

// Wiersze ról: znane w stałej kolejności, inne alfabetycznie. problem — po polsku, czemu rola nie działa.
function roles(snap) {
    var all = snap && isObject(snap.roles) ? snap.roles : {}
    var keys = Object.keys(all).filter(function (k) { return isObject(all[k]) })
    keys.sort(function (a, b) {
        var ia = ROLE_ORDER.indexOf(a), ib = ROLE_ORDER.indexOf(b)
        if (ia < 0) ia = 99
        if (ib < 0) ib = 99
        return ia !== ib ? ia - ib : (a < b ? -1 : a > b ? 1 : 0)
    })
    var eps = endpointMap(snap)
    var out = []
    for (var i = 0; i < keys.length; i++) {
        var r = all[keys[i]]
        var ep = eps[r.endpoint]
        out.push({ key: keys[i], name: roleName(keys[i]), model: shortModel(r.model),
                   where: ep && isLocalUrl(ep.url) ? "Ten komputer" : String(r.endpoint || ""),
                   available: r.available === true, problem: roleProblem(r, ep) })
    }
    return out
}

function roleProblem(r, ep) {
    if (r.available === true) return ""
    if (typeof r.error === "string" && r.error) return r.error
    if (!ep) return "serwer „" + (r.endpoint || "?") + "” nie jest ustawiony"
    if (!ep.online) return "serwer nie odpowiada"
    if (r.installed === false) return "model nie jest pobrany na ten serwer"
    return "niedostępna"
}

// Rola pracy w tle (raport, opisy kart) na domyślnym punkcie, której model nie jest pobrany — do kafelka.
// Opcjonalne role (agent, wyszukiwanie…) nie ostrzegają: ich brak widać tylko w popupie.
function missingModel(snap) {
    var name = defaultName(snap)
    var all = snap && isObject(snap.roles) ? snap.roles : {}
    for (var i = 0; i < BACKGROUND_ROLES.length; i++) {
        var r = all[BACKGROUND_ROLES[i]]
        if (isObject(r) && r.endpoint === name && r.installed === false) return shortModel(r.model)
    }
    return ""
}

// --- tryb raportu (PT-25) -----------------------------------------------------------------------------

// „Raport: lokalnie”, „Raport: Claude”, „Raport: bez modelu”; pusty — usługa nie podaje trybu.
function reportLine(snap) {
    var mode = snap && isObject(snap.report) ? snap.report.summary : ""
    if (typeof mode !== "string" || !mode) return ""
    var text = "Raport: " + (REPORT_MODES[mode] || mode)
    if (mode === "local") {
        var role = snap.roles && isObject(snap.roles.summary) ? snap.roles.summary : null
        if (role && role.available !== true) text += " · model niedostępny, opis bez modelu"
    }
    return text
}

// Problemy z plikiem ustawień: błąd (wszystko domyślne) i pominięte punkty albo role.
function configLine(snap) {
    var cfg = snap && isObject(snap.config) ? snap.config : null
    if (!cfg) return ""
    if (typeof cfg.error === "string" && cfg.error) return "Ustawienia: " + cfg.error
    var warnings = asList(cfg.warnings).filter(function (w) { return typeof w === "string" && w })
    return warnings.length ? "Ustawienia: " + warnings.join("; ") : ""
}

// --- kafelek ----------------------------------------------------------------------------------------

// Stan kafelka: {label, value, tone, pulse}. tone: muted, ok, working, partial, warning, offline —
// kolor kropki daje QML (Tokens). Kolejność: brak usługi, start, domyślny serwer nie odpowiada, praca
// w kolejce, pobieranie, model w pamięci, brak modelu roli, gotowe.
function tile(snap, serviceUp) {
    var st = status(snap, serviceUp)
    var ep = defaultEndpoint(snap)
    var label = "Modele" + (ep && !isLocalUrl(ep.url) ? " · " + defaultName(snap) : "")
    if (st === "down") return { label: "Modele", value: "usługa nie działa", tone: "muted", pulse: false }
    if (st === "starting") return { label: "Modele", value: "łączę się…", tone: "muted", pulse: false }
    if (st === "offline") return { label: label, value: ep ? "serwer nie odpowiada" : NO_SERVER_SHORT, tone: "offline", pulse: false }
    var name = defaultName(snap)
    var q = queueFor(snap, name)
    if (queueBusy(q)) return { label: label, value: queueShort(q), tone: "working", pulse: q.running !== null }
    var pulls = pullsFor(snap, name).filter(function (p) { return p.state === "running" })
    if (pulls.length) {
        var p = pulls[0]
        return { label: label, value: "pobieram " + p.model + (p.percent !== null ? " " + Math.floor(p.percent) + "%" : "…"),
                 tone: "working", pulse: true }
    }
    var mem = loaded(ep)
    if (mem.length) {
        var m = mem[0]
        return { label: label, value: shortModel(m.name) + " · " + gpuText(m), tone: onCard(m) ? "ok" : "partial", pulse: false }
    }
    var missing = missingModel(snap)
    if (missing) return { label: label, value: "brak modelu " + missing, tone: "warning", pulse: false }
    return { label: label, value: "gotowe", tone: "ok", pulse: false }
}

var NO_SERVER_SHORT = "brak serwera"
var CLICK_HINT = "Kliknij, aby zobaczyć serwery i role"

// Podpowiedź kafelka: jedno zdanie i „Kliknij…” — w każdym stanie.
function toolTip(snap, serviceUp, nowMs) {
    var head = toolTipHead(snap, serviceUp, nowMs)
    return head + "\n" + CLICK_HINT
}

function toolTipHead(snap, serviceUp, nowMs) {
    var st = status(snap, serviceUp)
    if (st === "down") return "Usługa tide-daemon nie działa"
    if (st === "starting") return "Tide sprawdza serwery modeli"
    var ep = defaultEndpoint(snap)
    if (st === "offline") return ep ? (ep.error || "Serwer modeli nie odpowiada") : "Brak serwera modeli w ustawieniach"
    var n = installed(ep).length
    var text = "Modele lokalne: " + endpointTitle(ep) + " odpowiada" + (ep.version ? " (Ollama " + ep.version + ")" : "")
        + ", " + count(n, "model pobrany", "modele pobrane", "modeli pobranych")
    var mem = loaded(ep)
    if (mem.length) {
        var unload = unloadText(mem[0].expiresAt, nowMs)
        text += "; w pamięci " + shortModel(mem[0].name) + " (" + gpuText(mem[0]) + (unload ? ", " + unload : "") + ")"
    }
    var q = queueLine(queueFor(snap, defaultName(snap)), nowMs)
    if (q) text += "\n" + q
    return text
}

// --- popup ------------------------------------------------------------------------------------------

// Stan serwera w karcie: „działa”, „nie odpowiada” (popup pokazuje karty dopiero po pierwszym odczycie).
function endpointState(ep) {
    return ep.online ? "działa" : "nie odpowiada"
}

// „127.0.0.1:11434 · Ollama 0.40.11” albo „… · serwer zgodny z OpenAI”.
function endpointMeta(ep) {
    var kind = ep.kind === "openai" ? "serwer zgodny z OpenAI" : ep.version ? "Ollama " + ep.version : ep.kind === "ollama" ? "Ollama" : ""
    var parts = [hostPort(ep.url)]
    if (kind) parts.push(kind)
    if (ep.auth) parts.push("z tokenem")
    return parts.join(" · ")
}

// „Pobrane modele: 5”, a gdy serwer ich nie zgłasza — pusty.
function installedLine(ep) {
    if (!ep.online) return ""
    var all = installed(ep)
    var n = all.length
    var cloud = all.filter(isCloudModel).length
    if (n === 0) return "Brak pobranych modeli"
    return "Pobrane: " + count(n, "model", "modele", "modeli") + (cloud > 0 ? ", w tym " + cloud + " w chmurze Ollamy" : "")
}

// Gdy nic nie jest w pamięci: „Żaden model nie jest w pamięci” (Ollama), dla OpenAI — pusty (nie wiadomo).
function idleLine(ep) {
    if (!ep.online || ep.loaded === null || ep.loaded === undefined || loaded(ep).length > 0) return ""
    return "Żaden model nie jest teraz w pamięci"
}

// Co mówi popup, gdy nie ma czego pokazać: usługa stoi albo pierwszy odczyt. Polecenie osobno (Geist Mono,
// w jednej linii).
function problem(snap, serviceUp) {
    var st = status(snap, serviceUp)
    if (st === "down") return "Usługa tide-daemon nie odpowiada. Uruchom:"
    if (st === "starting") return "Sprawdzam serwery modeli…"
    return ""
}

function problemCommand(snap, serviceUp) {
    return status(snap, serviceUp) === "down" ? "systemctl --user start tide-daemon" : ""
}

// Notatka po „Otwórz w Tide”, gdy okno nie przeszło na widok Modele.
function windowNote(kind) {
    if (kind === "noview") return "Okno Tide nie ma jeszcze widoku „Modele” — pojawi się z nową wersją okna."
    if (kind === "timeout") return "Okno Tide się nie otworzyło. Czy jest zainstalowane (make install-tide-window)?"
    return ""
}
