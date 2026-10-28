// Model agenta: chmura albo model lokalny z roli `agent` w /Models (Status()), program agenta lokalnego
// (agentProgram) i teksty wokół nich. Wspólne dla okna „Nowe zadanie” (apps/newtask, przez newtask.js)
// i okna „Co ma zrobić agent?” na Tablicy (apps/board) — biblioteka „tide”, kopiowana przez widgets/stage.py.
.pragma library
.import "modelnames.js" as ModelNames

var TOOLS = [
    { id: "claude", label: "Claude Code" },
    { id: "codex", label: "Codex" }
]

// Model agenta: chmura narzędzia albo model lokalny z roli `agent` (models.json, przez /Models).
var MODELS = [
    { id: "cloud", label: "Chmura" },
    { id: "local", label: "Lokalny" }
]
var MODELS_HINT = "ustaw w Tide → Modele"
var PROGRAM_HINT = "zmień w Tide → Modele"
// Przy chmurze powód niedostępności „Lokalnego” stoi pod przełącznikiem agenta — przedrostek mówi, czego dotyczy.
var LOCAL_PREFIX = "⌂ Lokalny: "
// Program agenta na modelu lokalnym (Status().agentProgram, PT-39) — ustawiany raz w Tide → Modele; bez pola
// (starsza usługa) Codex, jak domyślnie w usłudze.
var DEFAULT_PROGRAM = "codex"
// Uczciwie, jednym zdaniem: na tej karcie graficznej agent lokalny jest słaby (PT-13).
var LOCAL_NOTE = "Na tym komputerze (8 GB) agent lokalny jest wolny i słaby — opcja głównie na Maca."
var CLAUDE_LOCAL_NOTE = "Konektory claude.ai będą wyłączone."

// Model Ollama Cloud (`…:cloud`, `…:120b-cloud`) — lokalna Ollama tylko przekazuje rozmowę na ollama.com.
// To samo co modelnames.is_cloud_model w usłudze.
function isCloudModel(name) {
    return ModelNames.isCloudModel(name)   // modelnames.js — ta sama reguła w widoku i kafelku Modele
}

function isLoopback(url) {
    var m = /^[a-z]+:\/\/(\[[^\]]*\]|[^\/:]*)/i.exec(url || "")
    var host = m ? m[1].replace(/^\[|\]$/g, "").toLowerCase() : ""
    return host === "localhost" || host === "::1" || /^127\./.test(host)
}

// Nazwa serwera jak w widoku Modele (server_label w apps/models/backend.py): „Ten komputer” dla adresu
// lokalnego (z nazwą, gdy lokalnych jest kilka), inaczej nazwa z ustawień bez przerabiania („Mac-Studio”).
function serverName(endpoint, name, endpoints) {
    if (!endpoint || !isLoopback(endpoint.url)) return name || ""
    var local = 0
    for (var key in endpoints || {}) if (endpoints[key] && isLoopback(endpoints[key].url)) local++
    return local <= 1 ? "Ten komputer" : "Ten komputer · " + name
}

function toolLabel(id) {
    for (var i = 0; i < TOOLS.length; i++) if (TOOLS[i].id === id) return TOOLS[i].label
    return id || ""
}

// Program agenta na modelu lokalnym ze stanu /Models; nieznana wartość albo brak pola — Codex.
function localProgram(status) {
    var value = status && typeof status === "object" ? status.agentProgram : undefined
    for (var i = 0; i < TOOLS.length; i++) if (TOOLS[i].id === value) return value
    return DEFAULT_PROGRAM
}

// Program, który uruchomi „Uruchom agenta”: w trybie „Lokalny” z ustawień modeli, w chmurze — z przełącznika.
function agentTool(model, tool, local) {
    return model === "local" ? (local && local.program ? local.program : DEFAULT_PROGRAM) : tool
}

// Linia zamiast przełącznika Claude Code / Codex w trybie „Lokalny”: {program: "Codex", detail: " na qwen3-coder:30b
// · Ten komputer"}. Bez modelu (rola nieustawiona, usługa nie podaje stanu) — sam program; powód jest w uwadze.
// link: „ustaw w Tide → Modele”, gdy kłopot jest z modelem (rola nieustawiona, serwer nie odpowiada, modelu nie
// ma), inaczej „zmień w Tide → Modele” (program).
function localLine(option) {
    var detail = option && option.model ? " na " + option.model + (option.server ? " · " + option.server : "") : ""
    return { program: toolLabel(option && option.program ? option.program : DEFAULT_PROGRAM), detail: detail,
             link: option && !option.enabled && option.hint ? MODELS_HINT : PROGRAM_HINT }
}

// Opcja „Lokalny” z Status() obiektu /Models: {enabled, label, reason, hint, model, server}.
// Wyłączona z krótkim powodem, gdy usługa nie podaje stanu, rola `agent` nie jest ustawiona,
// serwer nie odpowiada albo modelu na nim nie ma. hint: czy pokazać „ustaw w Tide → Modele”.
function localOption(status) {
    var program = localProgram(status)
    function off(reason, hint, pending) {
        return { enabled: false, label: "Lokalny", reason: reason, hint: hint !== false, model: "", server: "",
                 thisComputer: false, pending: pending === true, program: program }
    }
    if (status && status.pending === true) return off("Sprawdzam serwer modeli…", false, true)
    if (!status || typeof status !== "object" || !status.roles) return off("Usługa nie podaje stanu modeli lokalnych", false)
    var role = status.roles.agent
    if (!role) return off("Model agenta nie jest ustawiony")
    var endpoint = (status.endpoints || {})[role.endpoint] || null
    var server = serverName(endpoint, role.endpoint, status.endpoints)
    var thisComputer = !!endpoint && isLoopback(endpoint.url)
    var label = "Lokalny: " + role.model + (server ? " · " + server : "")
    var result = function (reason, hint, pending) {
        return { enabled: false, label: label, reason: reason, hint: hint !== false, model: role.model, server: server,
                 thisComputer: thisComputer, pending: pending === true, program: program }
    }
    // Najpierw chmura: usługa (krok 3, decyzja 67) daje takiej roli też `error` z pełnym zdaniem i podpowiedzią,
    // a tu mówimy krótko, z własną podpowiedzią „ustaw w Tide → Modele”.
    if (isCloudModel(role.model)) return result("Model " + role.model + " działa w chmurze Ollamy")
    if (role.error) return result(role.error)
    if (status.checked === false) return result("Sprawdzam serwer modeli…", false, true)
    if (!endpoint || !endpoint.online) return result("Serwer " + (server || role.endpoint) + " nie odpowiada")
    if (role.installed === false) return result("Modelu " + role.model + " nie ma na serwerze")
    if (!role.available) return result("Model niedostępny")
    return { enabled: true, label: label, reason: "", hint: false, model: role.model, server: server,
             thisComputer: thisComputer, pending: false, program: program }
}

// Linia pod agentem: powód wyłączenia „Lokalnego” albo uczciwa uwaga o jakości (i konektorach Claude Code, gdy program
// agenta lokalnego to Claude Code). Przy chmurze i dostępnym modelu lokalnym — nic (okno zostaje ciche).
function modelNote(option, model) {
    // Przy „Lokalny” odnośnik do Tide → Modele jest w linii agenta, w chmurze — w tej uwadze (z przedrostkiem).
    if (!option.enabled) return model === "local" ? option.reason
                                                 : LOCAL_PREFIX + option.reason + (option.hint ? " — " + MODELS_HINT : "")
    if (model !== "local") return ""
    var note = option.thisComputer ? LOCAL_NOTE : "Agent pracuje na serwerze " + option.server + "."
    return note + (option.program === "claude" ? " " + CLAUDE_LOCAL_NOTE : "")
}
