// Logika widoku „Modele” bez QML: serwery, role, tryb raportu, podpowiedzi modeli i żądania do usługi
// (/Models, docs/tide-daemon.md#modele-lokalne). Stan to Status() z dopisanymi przez backend.py polami
// endpoints.*.label (nazwa dla użytkownika: „Ten komputer”, „Mac Studio”), endpoints.*.local i problems.
// Słowa dla użytkownika: „serwer”, nie „punkt”; „kontekst” tylko w „Zaawansowanych”.
.pragma library

const ROLE_ORDER = ["summary", "describe", "agent"]
const ROLE_INFO = {
    summary: { title: "Raport", hint: "„Na daily” w raporcie porannym" },
    describe: { title: "Opisy kart", hint: "Krótkie opisy kart na tablicy i w Sesjach" },
    agent: { title: "Agent", hint: "Sesje z opcją „Lokalny” w oknie Nowe zadanie (wkrótce)" }
}
// Domyślny kontekst ról raportu (decyzja 60: z 16k model 8B nie mieści się na karcie 8 GB).
const ROLE_CTX = { summary: 8192, describe: 8192 }
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$/

// Lista z danych: dane z delegata (modelData) bywają sekwencją Qt, dla której Array.isArray daje false.
function list(value) {
    if (value === null || value === undefined || typeof value !== "object" || typeof value.length !== "number") return null
    return Array.prototype.slice.call(value)
}

function parse(text, fallback) {
    if (typeof text !== "string" || text === "") return fallback
    try { return JSON.parse(text) } catch (e) { return fallback }
}

function plural(n, one, few, many) {
    const abs = Math.abs(n)
    if (abs === 1) return one
    const tens = abs % 100
    const ones = abs % 10
    return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? few : many
}

function count(n, one, few, many) {
    return n + " " + plural(n, one, few, many)
}

// Rozmiar modelu w GB jak w `ollama list` (1000, nie 1024): „5,2 GB”, „566 MB”.
function bytes(value) {
    if (value === undefined || value === null || value < 0) return ""
    const units = ["B", "KB", "MB", "GB", "TB"]
    let v = value
    let unit = 0
    while (v >= 1000 && unit < units.length - 1) {
        v /= 1000
        unit++
    }
    const text = v >= 100 || unit === 0 ? Math.round(v).toString() : v.toFixed(1).replace(".", ",").replace(/,0$/, "")
    return text + " " + units[unit]
}

// „http://mac-studio:11434/” → „mac-studio:11434”.
function hostText(url) {
    return String(url || "").replace(/^[a-z]+:\/\//i, "").replace(/\/+$/, "")
}

function kindLabel(kind) {
    return kind === "openai" ? "zgodny z OpenAI" : "Ollama"
}

// --- serwery -----------------------------------------------------------------------------------

// Stan serwera słowami: online — działa, offline z błędem — nie odpowiada, bez błędu (przed pierwszym
// odczytem, nowy albo zmieniony adres) — sprawdzam….
function serverState(endpoint, checked) {
    if (!endpoint) return { kind: "checking", text: "sprawdzam…" }
    if (endpoint.online) return { kind: "online", text: "działa" }
    if (!checked || !endpoint.error) return { kind: "checking", text: "sprawdzam…" }
    return { kind: "offline", text: "nie odpowiada" }
}

// Serwery do kart: lokalne najpierw, potem w kolejności z ustawień.
function servers(status) {
    const endpoints = status && status.endpoints ? status.endpoints : {}
    const out = []
    for (const name in endpoints) {
        const e = endpoints[name]
        if (!e || typeof e !== "object") continue
        out.push(Object.assign({}, e, {
            name: name,
            label: e.label || name,
            isDefault: name === status.default,
            state: serverState(e, status.checked !== false)
        }))
    }
    return out.filter(s => s.local).concat(out.filter(s => !s.local))
}

function server(status, name) {
    return servers(status).find(s => s.name === name) || null
}

function labelOf(status, name) {
    const e = status && status.endpoints ? status.endpoints[name] : null
    return e ? (e.label || name) : name
}

// „Ollama 0.40.11 · 5 modeli”, serwer zgodny z OpenAI bez wersji.
function serverFacts(s) {
    const parts = [s.kind === "openai" ? "zgodny z OpenAI" : "Ollama" + (s.version ? " " + s.version : "")]
    if (s.online) {
        const n = (list(s.models) || []).length
        parts.push(n === 0 ? "bez modeli" : count(n, "model", "modele", "modeli"))
    }
    return parts.join(" · ")
}

// Modele w pamięci: „qwen3:8b · 100% na karcie”; część na procesorze — w maśle, z wyjaśnieniem.
function loadedLines(s) {
    const loaded = s && s.online ? list(s.loaded) : null
    if (!loaded) return []
    return loaded.map(m => {
        const gpu = typeof m.gpuPercent === "number" ? m.gpuPercent : null
        return {
            name: m.name,
            gpu: gpu,
            slow: gpu !== null && gpu < 100,
            text: gpu === null ? "w pamięci"
                : gpu >= 100 ? gpu + "% na karcie"
                : gpu + "% na karcie — reszta na procesorze, pisze wolniej"
        }
    })
}

function loadedSummary(s) {
    if (!s || !s.online) return ""
    const loaded = list(s.loaded)
    if (!loaded) return "serwer nie mówi, co ma w pamięci"
    if (loaded.length === 0) return "nic nie jest teraz załadowane"
    return ""
}

function defaultServer(status) {
    return status ? server(status, status.default) : null
}

// Linia pod nagłówkiem: „Używany serwer: Ten komputer · działa · Ollama 0.40.11 · 5 modeli”.
function summaryText(status) {
    const s = defaultServer(status)
    if (!s) return "Brak serwera modeli w ustawieniach"
    return "Używany serwer: " + s.label + " · " + s.state.text + (s.online ? " · " + serverFacts(s) : "")
}

// Wynik TestEndpoint słowami.
function testText(result) {
    if (!result) return { text: "brak odpowiedzi usługi", error: true }
    if (!result.ok) return { text: result.error || "nie udało się połączyć", error: true }
    const parts = ["Połączenie działa"]
    if (result.version) parts.push("Ollama " + result.version)
    if (typeof result.models === "number") parts.push(count(result.models, "model", "modele", "modeli"))
    return { text: parts.join(" · "), error: false }
}

// Nazwa nowego serwera do zapisu: „Mac Studio” → „Mac-Studio” (litery, cyfry, _.-; bez polskich znaków).
function slug(name) {
    const map = { "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
                  "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z" }
    let text = String(name || "").trim().replace(/[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]/g, c => map[c])
    text = text.normalize("NFD").replace(/[̀-ͯ]/g, "")
    text = text.replace(/\s+/g, "-").replace(/[^A-Za-z0-9_.-]/g, "").replace(/-{2,}/g, "-")
    text = text.replace(/^[^A-Za-z0-9]+/, "").replace(/-+$/, "")
    return text.slice(0, 40)
}

// Sprawdzenie formularza przed wysłaniem — tylko to, czego usługa nie powie lepiej; resztę mówi ona.
function formProblem(form, status, adding) {
    const name = adding ? slug(form.name) : form.name
    if (adding) {
        if (!String(form.name || "").trim()) return { field: "name", text: "podaj nazwę, np. Mac Studio" }
        if (!NAME_RE.test(name)) return { field: "name", text: "nazwa musi zaczynać się literą albo cyfrą" }
        if (status && status.endpoints && status.endpoints[name]) return { field: "name", text: "serwer o tej nazwie już jest" }
    }
    const url = String(form.url || "").trim()
    if (!url) return { field: "url", text: "podaj adres, np. http://mac-studio:11434" }
    if (!/^https?:\/\/[^\s/]+/i.test(url)) return { field: "url", text: "adres zaczyna się od http:// albo https://" }
    if (/\s/.test(form.token || "")) return { field: "token", text: "token bez spacji" }
    return null
}

// SetEndpoint: {name, url, kind, token?, clearToken?} — pusty token = bez zmiany.
function endpointRequest(form, adding) {
    const req = { name: adding ? slug(form.name) : form.name, url: String(form.url || "").trim(), kind: form.kind || "ollama" }
    if (form.clearToken) req.clearToken = true
    else if (form.token) req.token = form.token
    return JSON.stringify(req)
}

// TestEndpoint przed zapisem: nowy adres ({url, kind, token?}) albo zapisany serwer z podmienionymi polami
// ({name, url, kind, token?} — bez nowego tokenu idzie zapisany).
function testRequest(form, adding) {
    const req = { url: String(form.url || "").trim(), kind: form.kind || "ollama" }
    if (!adding) req.name = form.name
    if (form.token && !form.clearToken) req.token = form.token
    return JSON.stringify(req)
}

// --- role --------------------------------------------------------------------------------------

// Czy rola jest przypięta do serwera. Status() mówi to wprost (roles.*.pinned), jeśli usługa to podaje;
// inaczej wiemy na pewno tylko tyle, że rola na innym serwerze niż domyślny jest przypięta.
function pinned(status, role) {
    if (!role) return false
    if (typeof role.pinned === "boolean") return role.pinned
    return !!role.endpoint && role.endpoint !== status.default
}

function modelNames(status, endpointName) {
    const e = status && status.endpoints ? status.endpoints[endpointName] : null
    return e && e.online ? (e.models || []).map(m => m.name) : []
}

// Wiersze ról w kolejności widoku. Rola „agent” może nie istnieć — wtedy wiersz bez modelu.
function roleRows(status) {
    const roles = status && status.roles ? status.roles : {}
    return ROLE_ORDER.map(id => {
        const role = roles[id] || null
        const endpointName = role ? role.endpoint : (status ? status.default : "")
        const e = status && status.endpoints ? status.endpoints[endpointName] : null
        const state = serverState(e, status ? status.checked !== false : false)
        return {
            id: id,
            title: ROLE_INFO[id].title,
            hint: ROLE_INFO[id].hint,
            exists: role !== null,
            model: role ? role.model : "",
            endpoint: endpointName,
            serverLabel: labelOf(status, endpointName),
            serverState: state,
            pinned: pinned(status, role),
            installed: role ? role.installed : null,
            available: role ? role.available === true : false,
            error: role && role.error ? role.error : "",
            ctx: role ? role.ctx : null,
            models: modelOptions(status, endpointName, role ? role.model : "")
        }
    })
}

// Modele do wyboru: zainstalowane na serwerze roli, z rozmiarem i znacznikiem „w pamięci”.
function modelOptions(status, endpointName, current) {
    const e = status && status.endpoints ? status.endpoints[endpointName] : null
    if (!e || !e.online) return []
    const loaded = (list(e.loaded) || []).map(m => m.name)
    return (e.models || []).map(m => ({
        name: m.name,
        detail: [m.parameterSize || "", bytes(m.size), loaded.indexOf(m.name) >= 0 ? "w pamięci" : ""]
                .filter(x => x).join(" · "),
        current: m.name === current
    }))
}

// Ostrzeżenie pod wyborem modelu (albo "").
function roleWarning(row) {
    if (!row.exists) return ""
    if (row.error) return row.error
    if (row.serverState.kind === "offline") return "Serwer „" + row.serverLabel + "” nie odpowiada — rola niedostępna"
    if (row.installed === false) return "Modelu " + row.model + " nie ma na serwerze „" + row.serverLabel + "”"
    return ""
}

function ctxDefault(id) {
    return ROLE_CTX[id] || 0
}

// Pole kontekstu: puste = domyślny. Status nie odróżnia jawnego 8192 od domyślnego, więc 8192 przy rolach
// raportu pokazujemy jako domyślny.
function ctxText(id, ctx) {
    if (ctx === null || ctx === undefined) return ""
    if (ROLE_CTX[id] && ctx === ROLE_CTX[id]) return ""
    return String(ctx)
}

function ctxPlaceholder(id) {
    return ROLE_CTX[id] ? "domyślny (" + ROLE_CTX[id] + ")" : "domyślny (kontekst serwera)"
}

// Kontekst z pola: "" → null (domyślny), liczba 0…1048576, inaczej undefined (błąd).
function ctxValue(text) {
    const t = String(text || "").trim().replace(/\s/g, "")
    if (t === "") return null
    if (!/^[0-9]+$/.test(t)) return undefined
    const n = parseInt(t, 10)
    return n >= 0 && n <= 1048576 ? n : undefined
}

// SetRole: model bez przedrostka, endpoint "" — za serwerem domyślnym; ctx tylko, gdy zmieniony.
function roleRequest(id, model, endpoint, ctx) {
    const req = { role: id, model: model, endpoint: endpoint || "" }
    if (ctx !== undefined) req.ctx = ctx
    return JSON.stringify(req)
}

// Przypięcie roli: opcje przełącznika „za serwerem domyślnym” + każdy serwer.
function pinOptions(status) {
    return [{ value: "", label: "Za domyślnym" }].concat(servers(status).map(s => ({ value: s.name, label: s.label })))
}

// Które role używają modelu na serwerze (znaczniki przy liście modeli).
function usedBy(status, endpointName, model) {
    const roles = status && status.roles ? status.roles : {}
    return ROLE_ORDER.filter(id => roles[id] && roles[id].endpoint === endpointName && roles[id].model === model)
                     .map(id => ROLE_INFO[id].title)
}

// --- raport ------------------------------------------------------------------------------------

const REPORT_MODES = [
    { value: "claude", label: "Claude" },
    { value: "local", label: "Lokalnie" },
    { value: "rules", label: "Bez modelu" }
]

function reportModes() {
    return REPORT_MODES
}

// Jedno uczciwe zdanie o trybie.
function reportText(status, mode) {
    if (mode === "claude")
        return "Najlepszy tekst. Liczy się do limitów Claude, a fakty z twojej pracy idą do Anthropic."
    if (mode === "rules")
        return "Same reguły: listy zrobionego i czekającego, bez streszczeń. Nic nie jest wysyłane."
    if (mode === "local") {
        const role = status && status.roles ? status.roles.summary : null
        const e = role && status.endpoints ? status.endpoints[role.endpoint] : null
        if (!role || !e) return "Prywatnie i bez limitów — fakty zostają na twoich serwerach."
        if (e.local)
            return "Prywatnie i bez limitów, na tym komputerze. Tekst jest tu słabszy niż z Claude (" + role.model + ")."
        return "Prywatnie i bez limitów, na serwerze „" + (e.label || role.endpoint) + "” (" + role.model + ")."
    }
    return ""
}

// Uwagi do trybu raportu: wymuszony opcją usługi, nieznana wartość w pliku, model lokalny niedostępny.
function reportNotes(status) {
    const report = status ? status.report : null
    if (!report) return [{ text: "Usługa nie podała trybu raportu (starsza wersja albo dane z --print-models).", tone: "muted" }]
    const out = []
    if (report.pinned)
        out.push({ text: "Usługa działa z opcją --report-summary " + report.summary + ": wybór zapisze się w report.json, "
                         + "ale zadziała dopiero po uruchomieniu usługi bez tej opcji.", tone: "warn" })
    if (report.raw !== null && report.raw !== undefined)
        out.push({ text: "W report.json jest nieznany tryb „" + report.raw + "” — działa „Bez modelu”.", tone: "warn" })
    const role = status.roles ? status.roles.summary : null
    if (report.summary === "local" && role && role.available === false && status.checked !== false) {
        const e = status.endpoints ? status.endpoints[role.endpoint] : null
        if (!e || e.online || e.error)
            out.push({ text: "Model lokalny niedostępny — raport i karty dostaną opis z reguł, bez Claude.", tone: "warn" })
    }
    for (const w of report.warnings || []) out.push({ text: w, tone: "warn" })
    return out
}

// --- modele na serwerze ----------------------------------------------------------------------------

function serverModels(status, endpointName) {
    const e = status && status.endpoints ? status.endpoints[endpointName] : null
    if (!e || !e.online) return []
    const loaded = {}
    for (const m of list(e.loaded) || []) loaded[m.name] = m
    return (e.models || []).map(m => {
        const l = loaded[m.name]
        const gpu = l && typeof l.gpuPercent === "number" ? l.gpuPercent : null
        return {
            name: m.name,
            detail: [m.parameterSize || "", m.quantization || ""].filter(x => x).join(" · "),
            size: bytes(m.size),
            loaded: !!l,
            loadedText: !l ? "" : gpu === null ? "w pamięci" : "w pamięci · " + gpu + "% na karcie",
            slow: gpu !== null && gpu < 100,
            usedBy: usedBy(status, endpointName, m.name)
        }
    })
}

// Podpowiedzi „Pobierz model” z naszych pomiarów (PT-13, karta 8 GB) — inne dla tego komputera, inne
// dla serwera w sieci (Mac Studio z dużą pamięcią).
function suggestions(status, endpointName) {
    const e = status && status.endpoints ? status.endpoints[endpointName] : null
    const installed = e && e.online ? (e.models || []).map(m => m.name) : []
    const list = e && !e.local ? [
        { model: "gpt-oss:120b", hint: "na dużym Macu: najlepszy tekst z lokalnych, ok. 65 GB" },
        { model: "qwen3:32b", hint: "na Macu: lepszy tekst niż 8b, ok. 20 GB" },
        { model: "qwen3-coder:30b", hint: "dla agenta (kod), ok. 19 GB" },
        { model: "qwen3:8b", hint: "mały i szybki — opisy kart" }
    ] : [
        { model: "qwen3:8b", hint: "szybki, mieści się na karcie 8 GB — opis w kilka sekund" },
        { model: "gpt-oss:20b", hint: "lepszy tekst, ale tutaj wolniejszy (część na procesorze)" },
        { model: "bge-m3", hint: "do wyszukiwania po znaczeniu, ok. 1,2 GB" }
    ]
    return list.map(s => Object.assign({}, s, { installed: installed.indexOf(s.model) >= 0 }))
}

function modelProblem(name) {
    const t = String(name || "").trim()
    if (!t) return "wpisz nazwę modelu, np. qwen3:8b"
    if (!MODEL_RE.test(t)) return "nazwa modelu: litery, cyfry i ._:/@+-"
    return ""
}

function pullRequest(endpointName, model) {
    return JSON.stringify({ endpoint: endpointName, model: String(model || "").trim() })
}

// Pobierania na serwerze: trwające najpierw. {model, text, fraction (null — nieznany rozmiar), running, error}.
function pulls(status, endpointName) {
    const all = status && status.pulls ? status.pulls : {}
    const out = []
    for (const key in all) {
        const p = all[key]
        if (!p || p.endpoint !== endpointName) continue
        const fraction = p.total > 0 ? Math.max(0, Math.min(1, p.completed / p.total)) : null
        let text
        if (p.error) text = p.error
        else if (p.status === "success") text = "pobrany"
        else if (fraction !== null) text = bytes(p.completed) + " z " + bytes(p.total) + " · " + Math.floor(fraction * 100) + "%"
        else text = pullStage(p.status)
        out.push({ key: key, model: p.model, text: text, fraction: fraction, running: !!p.running, error: p.error || "",
                   done: p.status === "success" })
    }
    out.sort((a, b) => (b.running ? 1 : 0) - (a.running ? 1 : 0))
    return out
}

function pullStage(status) {
    if (!status || status === "start") return "zaczynam…"
    if (status.indexOf("pulling manifest") === 0) return "pobieram opis modelu…"
    if (status.indexOf("verifying") === 0) return "sprawdzam sumę kontrolną…"
    if (status.indexOf("writing") === 0) return "zapisuję…"
    return "pobieram…"
}

// --- pusty stan ---------------------------------------------------------------------------------

function problem(status, serviceUp) {
    if (!serviceUp) return { title: "Usługa tide-daemon nie odpowiada",
                             text: "Stan serwerów i ustawienia modeli daje usługa Tide. Uruchom ją: "
                                   + "systemctl --user start tide-daemon (bez opcji --no-models)." }
    if (!status || !status.endpoints || Object.keys(status.endpoints).length === 0)
        return { title: "Brak serwerów modeli", text: "Usługa nie podała żadnego serwera — sprawdź ~/.config/tide/models.json." }
    return null
}
