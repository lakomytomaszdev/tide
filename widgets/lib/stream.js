// Stan streamu z rdzenia Streamer (PT-37) na słowa i kolory widżetów Stream: kafelek w docku, karta Stream,
// profil gracza, Observer, sceny i czat. Wejście to widok stanu z GetState() albo z sygnału Changed
// (org.streamer.Core1, opis w core/README.md repozytorium streamer): {seq, core, obs, profiles}.
// Kolory to nazwy ról z palety (Tokens[nazwa] w QML), więc widżety działają z każdym wariantem palety.
// Czyste funkcje — testy liczą je w qml6 (widgets/tests/test_widget_stream.py).
.pragma library

// Słowa z aplikacji webowej rdzenia (web/app.js) i ze słownika streamer (CONTEXT.md).
var MODES = { xbox: "Xbox", pc: "PC", observer: "Observer", talk: "Rozmowa" }
var MODULES = [
    { key: "camera", label: "Kamera" },
    { key: "chat", label: "Czat" },
    { key: "alerts", label: "Alerty" },
    { key: "opponent", label: "Przeciwnik" },
    { key: "motion", label: "Żubry" }
]
var TARGETS = { twitch: "Twitch", youtube: "YouTube", kick: "Kick", local: "tylko nagranie" }

// Wejście mikrofonu w OBS. Rdzeń ma je w konfiguracji (`obs.mic_input`), ale nie podaje go w stanie —
// tę samą nazwę wpisuje na sztywno aplikacja webowa. Gdy rdzeń zacznie podawać `obs.mic`, wygrywa ono.
var MIC_INPUT = "Mikrofon"

// Kolor ostrzegawczy: tylko wyciszony mikrofon na żywo, OBS rozłączony i rdzeń nie działa.
var WARNING_TINT = "neutral"

function obj(value) {
    return value && typeof value === "object" ? value : {}
}

function valueOf(field) {
    return field && typeof field === "object" && "value" in field ? field.value : field
}

function modeLabel(mode) {
    return MODES[mode] || (mode ? String(mode) : "")
}

// Wyciszenie mikrofonu: true, false albo null (OBS nie zna wejścia albo jest rozłączony).
function micMuted(view) {
    const obs = obj(obj(view).obs)
    if (obs.mic && typeof obs.mic.muted === "boolean") return obs.mic.muted
    const mutes = obj(obs.mutes)
    return typeof mutes[MIC_INPUT] === "boolean" ? mutes[MIC_INPUT] : null
}

// Wspólny, spłaszczony opis stanu. core: "down" (usługi nie ma na szynie), "loading" (jest, ale jeszcze bez
// odczytu) albo "up".
function status(view, coreUp) {
    if (!coreUp) return { core: "down" }
    if (!view || !view.core) return { core: "loading" }
    const core = view.core
    const obs = obj(view.obs)
    const mode = valueOf(core.mode) || ""
    return {
        core: "up",
        mode: mode,
        modeLabel: modeLabel(mode),
        modeOrigin: obj(core.mode).origin || "",
        obsConnected: obs.connected === true,
        obsError: obs.error || "",
        live: obs.connected === true && obs.streaming === true,
        reconnecting: obs.stream_reconnecting === true,
        recording: obs.connected === true && obs.recording === true,
        recordingPlanned: obj(core.recording)[mode] === true,
        scene: obs.connected === true && obs.scene ? String(obs.scene) : "",
        micMuted: obs.connected === true ? micMuted(view) : null,
        targets: (valueOf(core.broadcast_targets) || []).map(t => TARGETS[t] || String(t)),
        profile: valueOf(core.player_profile) || ""
    }
}

function part(word, tint, extra) {
    return Object.assign({ word: word, tint: tint, hollow: false, strong: false }, extra || {})
}

// Kafelek Stream w docku: {label, parts: [{word, tint, hollow, strong}], summary, warning, tint, icon, toolTip}.
function tile(view, coreUp) {
    const s = status(view, coreUp)
    let label = "Stream"
    let parts = []
    let icon = ""
    if (s.core === "down") {
        parts = [part("rdzeń nie działa", WARNING_TINT)]
    } else if (s.core === "loading") {
        parts = [part("czytam stan…", "textMuted", { hollow: true })]
    } else if (!s.obsConnected) {
        label += " · " + s.modeLabel
        parts = [part("OBS rozłączony", WARNING_TINT)]
    } else if (s.live && s.micMuted === true) {
        label += " · na żywo"
        icon = "mic-off"
        parts = [part("mikrofon wyciszony", WARNING_TINT, { strong: true })]
    } else if (s.live) {
        label += " · " + s.modeLabel + (s.scene ? " · scena " + s.scene : "")
        parts = [part("na żywo", "negative", { strong: true })]
        if (s.recording) parts.push(part("nagrywa", "accent"))
    } else {
        label += " · " + s.modeLabel
        parts = [part("poza anteną", "textMuted")]
        if (s.recording) parts.push(part("nagrywa", "accent"))
        parts.push(part("OBS", "positive"))
    }
    const warning = parts.length > 0 && parts[0].tint === WARNING_TINT
    return {
        label: label,
        parts: parts,
        summary: parts.map(p => p.word).join(" · "),
        warning: warning,
        tint: warning ? WARNING_TINT : s.core === "up" ? "working" : "textMuted",
        icon: icon,
        toolTip: toolTip(s)
    }
}

function toolTip(s) {
    if (s.core === "down") return "Rdzeń Streamer nie działa (usługa streamer-core)"
    if (s.core === "loading") return "Czytam stan rdzenia Streamer…"
    const origin = originText(s.modeOrigin)
    const parts = [s.modeLabel + (origin ? " (" + origin + ")" : "")]
    if (!s.obsConnected) parts.push("OBS rozłączony" + (s.obsError ? ": " + s.obsError : ""))
    else {
        parts.push(s.live ? "na żywo" + (s.targets.length ? " · " + s.targets.join(", ") : "") : "poza anteną")
        if (s.recording) parts.push("nagrywa")
        if (s.scene) parts.push("scena " + s.scene)
    }
    const head = s.live && s.micMuted === true ? "Mikrofon wyciszony na żywo — Pause włącza go z powrotem\n" : ""
    return head + parts.join(" · ") + "\nKliknij, aby zobaczyć stan streamu"
}

// Panel streamera (aplikacja webowa rdzenia) na tym komputerze. Rdzeń nie podaje adresu w stanie.
var PANEL_URL = "http://127.0.0.1:8765/app/"
var CRITICAL_NOTE = "Start i koniec streamu: tylko w Panelu streamera (przytrzymanie)."

function originText(origin) {
    return origin === "user" ? "ustawiłeś ty" : origin === "auto" ? "automat" : ""
}

function modules(view) {
    const mods = obj(obj(obj(view).core).modules)
    return MODULES.filter(m => m.key in mods).map(m => ({
        key: m.key, label: m.label, on: valueOf(mods[m.key]) === true, origin: obj(mods[m.key]).origin || ""
    }))
}

function row(key, label, value, tint) {
    return { key: key, label: label, value: value, tint: tint || "" }
}

// Karta Stream (popup kafelka i konsola): {chip, rows, modules, mic, notice, note, panelUrl}. W konsoli (full)
// scena jest w karcie Sceny, nagrywanie dochodzi do wiersza OBS, a moduły są przełącznikami zamiast wiersza.
function card(view, coreUp, full) {
    const s = status(view, coreUp)
    const out = { chip: "", rows: [], modules: [], notice: "", note: CRITICAL_NOTE, panelUrl: PANEL_URL,
                  obsConnected: s.obsConnected === true,
                  mic: { label: "Wycisz mikrofon · Pause", enabled: false, muted: null, warning: false } }
    if (s.core === "down") {
        out.notice = "Rdzeń Streamer nie działa (usługa streamer-core)."
        return out
    }
    if (s.core === "loading") {
        out.notice = "Czytam stan rdzenia…"
        return out
    }
    const origin = originText(s.modeOrigin)
    out.chip = s.modeLabel + (origin ? " · " + origin : "")
    const targets = s.targets.join(", ")
    let antenna = s.live ? "na żywo" + (targets ? " · " + targets : "") : "poza anteną" + (targets ? " · cel " + targets : "")
    if (s.live && s.reconnecting) antenna += " · łączy ponownie"
    out.rows.push(row("antenna", "Antena", antenna, s.live ? "negative" : "textMuted"))
    out.modules = modules(view)
    out.mic = {
        label: s.micMuted === true ? "Włącz mikrofon · Pause" : "Wycisz mikrofon · Pause",
        enabled: s.obsConnected && s.micMuted !== null,
        muted: s.micMuted,
        warning: s.live && s.micMuted === true
    }
    if (full) {
        out.rows.push(s.obsConnected
            ? row("obs", "OBS", "połączony · " + (s.recording ? "nagrywa" : "bez nagrania"), "positive")
            : row("obs", "OBS", "rozłączony" + (s.obsError ? " · " + s.obsError : ""), WARNING_TINT))
        return out
    }
    out.rows.push(row("recording", "Nagrywanie", s.recording ? "włączone"
                      : s.recordingPlanned ? "wyłączone · włączy się ze startem" : "wyłączone"))
    out.rows.push(row("scene", "Scena", s.scene || "—"))
    out.rows.push(s.obsConnected ? row("obs", "OBS", "połączony", "positive")
                  : row("obs", "OBS", "rozłączony" + (s.obsError ? " · " + s.obsError : ""), WARNING_TINT))
    const visible = out.modules.filter(m => m.on).map(m => m.label.toLowerCase())
    out.rows.push(row("modules", "Moduły", visible.length ? visible.join(", ") : "wszystkie ukryte"))
    return out
}

// Profile graczy z rdzenia (`profiles`): tytuł to gamertag (tak jest w Panelu), dopisek — nazwa.
function profiles(view) {
    const current = valueOf(obj(obj(view).core).player_profile)
    const list = Array.isArray(obj(view).profiles) ? view.profiles : []
    return list.map(p => ({
        id: String(p.id), title: String(p.gamertag || p.id), detail: p.name && p.name !== (p.gamertag || p.id)
            ? String(p.name) : "", current: p.id === current
    }))
}

// Kafelek „Profil gracza · streamer ▾”.
function profileTile(view, coreUp) {
    const s = status(view, coreUp)
    const current = profiles(view).filter(p => p.current)[0]
    return {
        label: "Profil gracza",
        value: s.core === "up" ? (current ? current.title : s.profile || "—") : "—",
        enabled: s.core === "up" && profiles(view).length > 0,
        origin: originText(obj(obj(obj(view).core).player_profile).origin)
    }
}

// Kafelek Observer: wydarzenie i wynik serii; widoczny tylko w trybie Observer.
function observer(view, coreUp) {
    const s = status(view, coreUp)
    const out = { visible: s.core === "up" && s.mode === "observer", label: "Observer", left: "", right: "",
                  score: "", empty: "", finishedText: "", event: "" }
    if (!out.visible) return out
    const o = obj(view.core.observer)
    const event = o.event && o.event.name ? String(o.event.name) : ""
    const series = o.series && typeof o.series === "object" ? o.series : null
    out.event = event
    if (!series) {
        out.label = event || "Observer"
        out.empty = "brak serii"
        return out
    }
    const score = Array.isArray(series.score) ? series.score : [0, 0]
    const parts = []
    if (event) parts.push(event)
    parts.push("BO" + series.format)
    parts.push(series.finished ? "koniec" : "gra " + series.game)
    out.label = parts.join(" · ")
    out.left = String(series.left)
    out.right = String(series.right)
    out.score = score[0] + " : " + score[1]
    out.finishedText = series.finished && series.winner ? "wygrał " + series.winner : ""
    return out
}

// Czego rdzeń jeszcze nie daje (PT-37, zgłoszone w repozytorium streamer). Widżety mają już miejsce na te dane.
var UNAVAILABLE = {
    scenes: "Lista scen trybu: jeszcze niedostępna w rdzeniu",
    preview: "Podgląd sceny: jeszcze niedostępny w rdzeniu",
    chat: "Czat Twitcha: jeszcze niedostępny w rdzeniu"
}

// Sceny pełnoekranowe z generatora scen streamer (scenes/scenes.json, moduł `screen` w stylu starting, brb
// i ended; start_scene i brb_scene w core.py mają te same nazwy) — rodzaj sceny, gdy `core.mode_scenes` poda
// same nazwy. `kind` z rdzenia ma pierwszeństwo.
var CORE_SCENE_KINDS = { "Starting soon": "starting", "Be right back": "brb", "Thanks for watching": "ended" }
var FULL_SCREEN_KINDS = ["starting", "ended"]
var LIVE_LOCK_REASON = "na żywo — tylko poza anteną"

// Sceny trybu do przycisków konsoli: {available, items: [{name, kind, current, enabled, reason}], current, live, note}.
// Dane, których rdzeń jeszcze nie ma: `core.mode_scenes` ({tryb: [nazwa albo {name, kind}]}, kind: starting, brb,
// ended) i opcjonalnie `obs.scenes` (sceny w OBS) — wtedy tylko sceny trybu, które OBS naprawdę ma. Bez scen trybu
// nie ma przycisków (same sceny OBS to też sceny innych trybów). BRB nie jest przyciskiem sceny, tylko osobnym
// przyciskiem (brb.toggle pamięta scenę powrotu). Sceny pełnoekranowe (start, koniec) przełączamy tylko poza anteną.
function scenes(view, coreUp) {
    const s = status(view, coreUp)
    const out = { available: false, items: [], current: s.scene || "", live: s.live === true, note: "" }
    if (s.core !== "up") {
        out.note = s.core === "down" ? "Rdzeń nie działa" : "Czytam stan rdzenia…"
        return out
    }
    const byMode = obj(view.core.mode_scenes)[s.mode]
    if (!Array.isArray(byMode)) {
        out.note = UNAVAILABLE.scenes
        return out
    }
    const inObs = obj(view.obs).scenes
    let entries = byMode.map(e => typeof e === "string" ? { name: e, kind: CORE_SCENE_KINDS[e] || "" }
                                  : { name: String(obj(e).name || ""), kind: String(obj(e).kind || "") })
    entries = entries.filter(e => e.name !== "" && e.kind !== "brb")
    if (Array.isArray(inObs)) entries = entries.filter(e => inObs.indexOf(e.name) >= 0)
    out.available = true
    out.items = entries.map(e => {
        const locked = s.live && FULL_SCREEN_KINDS.indexOf(e.kind) >= 0
        return { name: e.name, kind: e.kind, current: e.name === s.scene, enabled: !locked,
                 reason: locked ? LIVE_LOCK_REASON : "" }
    })
    if (out.items.some(i => !i.enabled))
        out.note = "Na żywo sceny startu i końca są wyłączone — przełączysz je poza anteną."
    return out
}

// Tekst w miejscu podglądu sceny (pusty, gdy jest obraz). supported: rdzeń ma GetSceneScreenshot (do pierwszej
// odpowiedzi UnknownMethod zakładamy, że ma).
function previewNote(view, coreUp, supported, hasImage) {
    if (!coreUp) return "Rdzeń nie działa"
    if (!supported) return UNAVAILABLE.preview
    if (hasImage) return ""
    const s = status(view, coreUp)
    if (s.core === "up" && !s.obsConnected) return "OBS rozłączony — brak podglądu"
    return "Czekam na zrzut sceny…"
}

var ALERTS = { follow: "Follow", sub: "Sub", bits: "Bits", raid: "Raid" }

function alertText(alert) {
    const parts = [ALERTS[alert.type] || String(alert.type || "Alert"), String(alert.user || "")]
    if (alert.months) parts.push(alert.months + " mies.")
    if (alert.amount) parts.push(String(alert.amount))
    if (alert.viewers) parts.push(alert.viewers + " " + (alert.viewers === 1 ? "widz" : "widzów"))
    return parts.filter(p => p !== "").join(" · ")
}

// Kolor nicku: pastel palety wybrany po nicku (ten sam widz — ten sam kolor). Kolor z Twitcha pomijamy,
// bo bywa nieczytelny na ciemnym tle. Kandydaci to role pasteli palety; nickTints odrzuca te, które paleta
// daje kolorom ostrzeżenia i błędu (semantic neutral i negative), żeby nick nie wyglądał jak alarm.
var NICK_TINTS = ["sky", "butter", "mint", "rose", "lavender", "peach", "teal"]

// colors: {rola: kolor} z Tokens, avoid: kolory do pominięcia (Tokens.neutral, Tokens.negative).
function nickTints(colors, avoid) {
    const skip = (avoid || []).map(c => String(c).toLowerCase())
    const out = NICK_TINTS.filter(n => n in obj(colors) && skip.indexOf(String(colors[n]).toLowerCase()) < 0)
    return out.length ? out : ["accent"]
}

function nickTint(user, tints) {
    const list = tints && tints.length ? tints : NICK_TINTS
    let h = 0
    for (let i = 0; i < user.length; i++) h = (h * 31 + user.charCodeAt(i)) % 1000003
    return list[h % list.length]
}

// Wiersz czatu z komunikatu w kształcie WebSocketu rdzenia ({type: "chat", message} i {type: "alert", alert}):
// płaski obiekt {kind, id, user, text} do ListModel; inny komunikat — null.
var lineCounter = 0

function chatLine(e) {
    if (!e || typeof e !== "object") return null
    lineCounter++
    if (e.type === "chat" && e.message) {
        const m = e.message
        return { kind: "chat", id: String(m.id || "chat-" + lineCounter), user: String(m.user || ""),
                 text: String(m.text || "") }
    }
    if (e.type === "alert" && e.alert)
        return { kind: "alert", id: "alert-" + lineCounter, user: String(e.alert.user || ""), text: alertText(e.alert) }
    return null
}

// Treść widza do Text.StyledText: wszystko escapowane, więc wiadomość nie wstawi obrazka ani znacznika.
function escapeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

// Wiersz czatu: pogrubiony nick w kolorze (z palety) i wiadomość w jednym akapicie, który się zawija.
function chatHtml(line, nickColor) {
    return "<b><font color=\"" + escapeHtml(nickColor) + "\">" + escapeHtml(line.user) + "</font></b>&nbsp; "
        + escapeHtml(line.text)
}

// Komendy, które widżety mogą wysłać do rdzenia. Akcji krytycznych (start i koniec streamu, koniec nagrania,
// zmiana trybu na żywo, reset serii) nie ma w widżetach — są tylko w Panelu streamera (przytrzymanie).
// mode.set zostaje poza listą w całości: rdzeń odrzuca ją na żywo, a poza anteną tryb wybiera Panel.
// Mikrofon przez mic.set {muted} (nie mic.toggle): podwójne kliknięcie albo spóźniony stan nie odwróci wyniku.
var ALLOWED = ["mic.set", "profile.set", "module.set", "scene.set", "brb.toggle"]

function allowed(name) {
    return ALLOWED.indexOf(name) >= 0
}

// Ściąga ze skrótami aktywności Stream (plan PT-38). Skróty Stream działają tylko w aktywności Stream.
var SHORTCUTS = [
    { keys: "Pause", action: "mikrofon" },
    { keys: "Meta+Pause", action: "ekran prywatności" },
    { keys: "Meta+B", action: "BRB i powrót" },
    { keys: "Meta+Shift+A", action: "Praca ⇄ Stream" },
    { keys: "Meta+A", action: "czekający agent" }
]
