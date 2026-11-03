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
    { key: "motion", label: "Animacje żubra",
      hint: "Otwórz ustawienia i testy przejścia, alertu, wychylenia i stada przy raidzie." }
]
var TARGETS = { twitch: "Twitch", youtube: "YouTube", kick: "Kick", local: "tylko nagranie" }

// Wejście mikrofonu w OBS. Rdzeń ma je w konfiguracji (`obs.mic_input`), ale nie podaje go w stanie —
// tę samą nazwę wpisuje na sztywno aplikacja webowa. Gdy rdzeń zacznie podawać `obs.mic`, wygrywa ono.
var MIC_INPUT = "Mikrofon"

// Kolor ostrzegawczy: tylko wyciszony mikrofon na żywo, OBS rozłączony w trakcie streamu (stan „lost”, Stream.obsKey)
// i rdzeń nie działa. OBS wyłączony bez streamu to zwykłe granie (PT-83, decyzja 82).
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
    // Nowy rdzeń podaje pole także jako null: to brak skonfigurowanego wejścia, nie sygnał do odczytu innego.
    if ("mic" in obs) return obs.mic && typeof obs.mic.muted === "boolean" ? obs.mic.muted : null
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

// Tryby, w których bez streamu po prostu się gra (kafelek „Granie · Xbox”); Observer i Rozmowa zostają „Stream”.
var GAME_MODES = ["xbox", "pc"]

// Kafelek Stream w docku: {label, parts: [{word, tint, hollow, strong}], summary, warning, tint, icon, toolTip}.
// memory — poprzedni stan OBS z pamięci widżetu (CoreSource.obsMemory): bez niego rozłączony OBS to granie.
function tile(view, coreUp, memory) {
    const s = status(view, coreUp)
    s.lost = obsKey(view, coreUp, memory) === "lost"
    let label = "Stream"
    let parts = []
    let icon = ""
    if (s.core === "down") {
        parts = [part("rdzeń nie działa", WARNING_TINT)]
    } else if (s.core === "loading") {
        parts = [part("czytam stan…", "textMuted", { hollow: true })]
    } else if (!s.obsConnected && !s.lost) {
        // Bez OBS i bez streamu to zwykłe granie (PT-83, decyzja 82), nie ostrzeżenie.
        label = (GAME_MODES.indexOf(s.mode) >= 0 ? "Granie · " : "Stream · ") + s.modeLabel
        parts = [part("bez streamu", "textMuted")]
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
        tint: warning ? WARNING_TINT : s.core === "up" && (s.obsConnected || s.lost) ? "working" : "textMuted",
        icon: icon,
        toolTip: toolTip(s)
    }
}

function toolTip(s) {
    if (s.core === "down") return "Rdzeń Streamer nie działa (usługa streamer-core)"
    if (s.core === "loading") return "Czytam stan rdzenia Streamer…"
    const origin = originText(s.modeOrigin)
    const parts = [s.modeLabel + (origin ? " (" + origin + ")" : "")]
    if (!s.obsConnected && !s.lost) parts.push("OBS wyłączony — bez streamu")
    else if (!s.obsConnected) parts.push("OBS rozłączony" + (s.obsError ? ": " + s.obsError : ""))
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
        key: m.key, label: m.label, hint: m.hint || "", on: valueOf(mods[m.key]) === true, origin: obj(mods[m.key]).origin || ""
    }))
}

function row(key, label, value, tint) {
    return { key: key, label: label, value: value, tint: tint || "" }
}

// Wiersz OBS bez połączenia: w trakcie streamu ostrzeżenie, bez streamu spokojne „wyłączony” (granie, PT-83).
function obsOffRow(s) {
    if (!s.lost) return row("obs", "OBS", "wyłączony · bez streamu", "textMuted")
    return row("obs", "OBS", "rozłączony" + (s.obsError ? " · " + s.obsError : ""), WARNING_TINT)
}

// Karta Stream (popup kafelka i konsola): {chip, rows, modules, mic, notice, note, panelUrl}. W konsoli (full)
// scena jest w karcie Sceny, nagrywanie dochodzi do wiersza OBS, a moduły są przełącznikami zamiast wiersza.
function card(view, coreUp, full, memory) {
    const s = status(view, coreUp)
    s.lost = obsKey(view, coreUp, memory) === "lost"
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
            : obsOffRow(s))
        return out
    }
    out.rows.push(row("recording", "Nagrywanie", s.recording ? "włączone"
                      : s.recordingPlanned ? "wyłączone · włączy się ze startem" : "wyłączone"))
    out.rows.push(row("scene", "Scena", s.scene || "—"))
    out.rows.push(s.obsConnected ? row("obs", "OBS", "połączony", "positive")
                  : obsOffRow(s))
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

// Kafelek „Kamera · ● widoczna / ○ ukryta” (PT-82, decyzja 83): `core.modules.camera` ({value, origin}).
// visible: true, false albo null (rdzeń nie działa, jeszcze bez odczytu albo bez modułu kamery). Kliknięcie to
// module.set z odwróconą wartością. Rdzeń zmienia stan także bez OBS — scene_sync ustawia OBS po połączeniu, więc
// przy rozłączonym OBS kafelek dalej przełącza. Bez koloru ostrzegawczego: o rdzeniu i OBS ostrzega kafelek Stream.
function camera(view, coreUp) {
    const s = status(view, coreUp)
    const out = { label: "Kamera", detail: "", value: "—", visible: null, enabled: false, origin: "",
                  obsConnected: s.obsConnected === true, dot: "textMuted", hollow: true, tint: "textMuted",
                  toolTip: "" }
    if (s.core === "down") {
        out.value = "rdzeń nie działa"
        out.toolTip = "Rdzeń Streamer nie działa (usługa streamer-core)"
        return out
    }
    if (s.core === "loading") {
        out.value = "czytam stan…"
        out.toolTip = "Czytam stan rdzenia Streamer…"
        return out
    }
    const mods = obj(view.core.modules)
    if (!("camera" in mods)) {
        out.toolTip = "Rdzeń nie podaje modułu kamery"
        return out
    }
    const on = valueOf(mods.camera) === true
    out.visible = on
    out.enabled = true
    out.origin = originText(obj(mods.camera).origin)
    out.value = on ? "widoczna" : "ukryta"
    out.dot = on ? "positive" : "textMuted"
    out.hollow = !on
    out.tint = "accent"
    out.detail = out.obsConnected ? out.origin : "bez OBS"
    out.toolTip = "Kamera " + out.value + (out.origin ? " (" + out.origin + ")" : "")
        + (out.obsConnected ? "" : "\nOBS rozłączony — zmiana trafi do OBS po połączeniu")
        + "\nKliknij, aby " + (on ? "ukryć" : "pokazać") + " kamerę"
    return out
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

// Stan źródła czatu: GetChat oznacza tylko dostępne API D-Bus, nie połączenie z Twitchem.
function twitchNote(twitch, available, coreUp) {
    if (!coreUp) return "Rdzeń Streamer nie działa"
    if (!available) return UNAVAILABLE.chat
    const source = obj(twitch)
    if (source.state === "connected") return "Czekam na wiadomości i alerty z Twitcha…"
    if (source.reason) return String(source.reason)
    switch (source.state) {
    case "setup_required": return "Twitch nie jest skonfigurowany w rdzeniu"
    case "authorization_required": return "Konto Twitch wymaga autoryzacji"
    case "reconnecting": return "Łączę ponownie z Twitchem…"
    case "partial": return "Część zdarzeń Twitch jest niedostępna"
    default: return "Twitch nie jest jeszcze połączony"
    }
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

// --- Konsola na lewym monitorze, wariant A (PT-105, K-29 decyzje 1, 2 i 4–7) ---
// Lewa kolumna: karta Zawsze nad Scenami w grupach. Prawa: rząd siedmiu kategorii i karty „Teraz” z rdzenia
// (situation.cards, auto/pinned/hidden/addable z PT-103). Rdzeń bez tych pól (przed wdrożeniem PT-103) daje to
// samo co wcześniej: płaskie sceny, karty z situation.cards i bez przypinania.

// Sceny w grupach z `situation.scene_groups` ([{id, label, locked, scenes: [{name, kind, locked}]}]). Rdzeń (od
// 7533a56) podaje sceny wszystkich trybów, sceny bieżącego trybu pierwsze, a tryb ustala kolejność grup. Przycisk
// BRB zostaje w karcie Zawsze (brb.toggle pamięta scenę powrotu), więc grupa Ekrany nie ma sceny BRB. Sceny,
// których nie ma w OBS (`obs.scenes`), pomijamy jak wcześniej. Bez grup w rdzeniu: jedna grupa bez nazwy.
// {available, grouped, groups: [{id, label, locked, items: [{name, kind, current, enabled, reason}]}], current,
//  live, note}
function sceneGroups(view, coreUp) {
    const flat = scenes(view, coreUp)
    const out = { available: flat.available, grouped: false, groups: [], current: flat.current, live: flat.live,
                  note: flat.note }
    if (status(view, coreUp).core !== "up") return out
    const groups = obj(view.situation).scene_groups
    if (!Array.isArray(groups)) {
        if (flat.available) out.groups = [{ id: "all", label: "", locked: false, items: flat.items }]
        return out
    }
    const inObs = obj(view.obs).scenes
    out.available = true
    out.grouped = true
    out.note = ""
    groups.forEach(g => {
        const items = (Array.isArray(obj(g).scenes) ? g.scenes : [])
            .map(e => typeof e === "string" ? { name: e, kind: CORE_SCENE_KINDS[e] || "", locked: false }
                                            : { name: String(obj(e).name || ""), kind: String(obj(e).kind || ""),
                                                locked: obj(e).locked === true })
            .filter(e => e.name !== "" && e.kind !== "brb" && (!Array.isArray(inObs) || inObs.indexOf(e.name) >= 0))
            .map(e => ({ name: e.name, kind: e.kind, current: e.name === flat.current, enabled: !e.locked,
                         reason: e.locked ? LIVE_LOCK_REASON : "" }))
        if (items.length === 0) return
        out.groups.push({ id: String(g.id || ""), label: String(g.label || ""),
                          locked: typeof g.locked === "boolean" ? g.locked : items.some(i => !i.enabled),
                          items: items })
    })
    // Bez dopisku pod scenami: kłódka przy grupie mówi to samo, a lewa kolumna mieści wtedy sceny wszystkich trybów.
    return out
}

// Siedem kategorii ustawień w kolejności cyklu streamu (decyzja 2). Rząd w prawej kolumnie otwiera popup
// kategorii z jej ustawieniami (PT-106, categoryContent niżej). `warning` to powód kropki ostrzeżenia przy ikonie,
// pusty, gdy wszystko w porządku.
var CATEGORIES = [
    { id: "prep", label: "Przygotowanie", short: "Przygot.", icon: "prep" },
    { id: "screens", label: "Sceny i ekrany", short: "Ekrany", icon: "scenes" },
    { id: "score", label: "Wynik i seria", short: "Wynik", icon: "score" },
    { id: "modules", label: "Na ekranie", short: "Na ekranie", icon: "layers" },
    { id: "alerts", label: "Alerty", short: "Alerty", icon: "bell" },
    { id: "motion", label: "Animacje żubra", short: "Animacje", icon: "spark" },
    { id: "devices", label: "Urządzenia", short: "Urządz.", icon: "device" }
]
var MOTION_KEYS = ["transition", "alert", "peek", "raid"]

// Kropki ostrzeżeń jak na tablecie (web/app.js, catWarning). Urządzenia: OBS rozłączony, Twitch wymaga zgody albo
// konfiguracji, albo kamera (iPhone) nie odpowiada — tylko przy widocznym module kamery i według ostatniej kontroli
// przed startem (preflight: wynik preflight.run z konsoli albo null). Sparowane urządzenie offline (np. iPhone
// z Pilotem) to zwykły stan, nie ostrzeżenie. Animacje żubra: moduł ruchu schowany albo wszystkie cztery wyłączone.
function categoryWarnings(view, coreUp, preflight) {
    const out = {}
    if (status(view, coreUp).core !== "up") return out
    const core = obj(view.core)
    const devices = []
    if (obj(view.obs).connected !== true) devices.push("OBS rozłączony")
    const twitch = obj(core.twitch).state
    if (twitch === "setup_required") devices.push("Twitch wymaga konfiguracji")
    if (twitch === "authorization_required") devices.push("Twitch wymaga zgody")
    if (cameraProblem(view, preflight)) devices.push("kamera nie odpowiada")
    if (devices.length) out.devices = devices.join(" · ")
    const motion = obj(core.motion)
    if (valueOf(obj(core.modules).motion) === false) out.motion = "animacje schowane w kategorii Na ekranie"
    else if (MOTION_KEYS.every(k => motion[k] === false)) out.motion = "wszystkie animacje wyłączone"
    return out
}

// Niezaliczone sprawdzenie kamery z ostatniej kontroli przed startem, gdy moduł kamery jest widoczny, albo null.
function cameraProblem(view, preflight) {
    if (valueOf(obj(obj(obj(view).core).modules).camera) !== true) return null
    const checks = preflight && Array.isArray(preflight.checks) ? preflight.checks.map(obj) : []
    return checks.filter(c => c.id === "camera" && c.status !== "ok")[0] || null
}

function categories(view, coreUp, preflight) {
    const warnings = categoryWarnings(view, coreUp, preflight)
    return CATEGORIES.map(c => Object.assign({}, c, { warning: warnings[c.id] || "" }))
}

// --- Treść kategorii ustawień (PT-106): te same komendy rdzenia i te same blokady co kategorie na tablecie
// (web/app.js: CARDS.setup, preflight, screens, observer, modules, alerts, motion, devices). Popup kategorii
// (CategoryPopup.qml) rysuje listę pozycji, a każda pozycja niesie gotową komendę {name, args} albo null.
// Rodzaje pozycji (kind):
// - heading {text} — podtytuł sekcji;
// - note {text, tone} — dopisek (tone: rola z palety);
// - row {label, detail, tint, on, button: {text, enabled, command, confirm, warning} | null,
//   test: {text, enabled, command} | null} — przełącznik (on: true/false) albo sam wiersz stanu (on: null); confirm —
//   napis przycisku po pierwszym kliknięciu, komenda idzie dopiero po drugim (odłączenie urządzenia), a warning
//   zastępuje wtedy dopisek wiersza;
// - choice {label, detail, options: [{key, text, tip, on, enabled, command}]} — przyciski wyboru;
// - actions {label, detail, buttons: [{key, text, tip, enabled, command}]} — rząd przycisków;
// - form {label, detail, fields: [{key, placeholder, value}], formats, format, save, empty, command} — pola tekstowe
//   zapisywane świadomie (Enter albo przycisk), komendę składa formCommand(); formats — wybór BO (nowa seria);
// - score {left, right, score, line, winner} — wynik serii;
// - checks {bad: [{id, label, message, fix}], ok: [nazwa], at} — wynik kontroli przed startem.
// Każda pozycja ma `key` (objectName „cat-<key>…” w popupie). Akcje krytyczne (start i koniec streamu, zmiana
// trybu na żywo, zerowanie wyniku, koniec nagrania) zostają w Panelu streamera (decyzja z PT-37).
var MODULE_LABELS = { camera: "Kamera", chat: "Czat", alerts: "Alerty", opponent: "Dane przeciwnika",
                      motion: "Animacje żubra" }
var SCREEN_NAMES = { starting: "Starting soon", brb: "Be right back", ended: "Thanks for watching" }
var TARGET_ORDER = ["twitch", "youtube", "kick", "local"]
var MOTION_KINDS = [
    { key: "transition", label: "Przejście scen", about: "żubr i złota linia przy każdej zmianie sceny" },
    { key: "alert", label: "Żubr przy alercie", about: "wbiega z follow, sub, bits i raid" },
    { key: "peek", label: "Wychylenie żubra", about: "" },
    { key: "raid", label: "Stado przy raidzie", about: "przebiega dołem ekranu" }
]
var PEEK_MINUTES = [5, 10, 20, 40, 60]
var PEEK_PLACES = [
    { key: "tab", text: "zza zakładki kanału" }, { key: "frame", text: "zza paska ramy" },
    { key: "edge", text: "zza boku ekranu" }, { key: "camera", text: "zza kamery" }
]
var TWITCH_STATES = { connected: "połączony", partial: "częściowo połączony", reconnecting: "łączy ponownie",
                      authorization_required: "wymaga zgody", setup_required: "wymaga konfiguracji" }
// Testowy alert jak na tablecie: te same pola dla każdego typu (rdzeń bierze te, które go dotyczą).
var ALERT_TEST = { user: "test_viewer", months: 3, amount: 100, viewers: 12 }

function command(name, args) {
    return { name: name, args: args || {} }
}

// Przełącznik: napis przycisku „Włączone”/„Wyłączone”, chyba że extra.text mówi inaczej.
function switchRow(key, label, detail, on, cmd, extra) {
    const ex = obj(extra)
    return { kind: "row", key: key, label: label, detail: detail || "", tint: "", on: on === true,
             button: { text: ex.text || (on === true ? "Włączone" : "Wyłączone"), enabled: true, command: cmd,
                       confirm: "" },
             test: ex.test || null }
}

function stateRow(key, label, detail, tint) {
    return { kind: "row", key: key, label: label, detail: detail || "", tint: tint || "", on: null, button: null,
             test: null }
}

function heading(key, text) {
    return { kind: "heading", key: key, text: text }
}

function note(key, text, tone) {
    return { kind: "note", key: key, text: text, tone: tone || "textMuted" }
}

function joinDetail(parts) {
    return parts.filter(p => p).join(" · ")
}

function button(key, text, cmd, enabled, tip) {
    return { key: key, text: text, tip: tip || "", enabled: enabled !== false, command: cmd }
}

// {items, panel}: panel — odesłanie do Panelu streamera pod treścią (rzeczy, które są tylko na tablecie).
// extra: {preflight: wynik preflight.run z chwilą `at` (ms) albo null, preflightRunning, now: czas w ms (koniec
// odliczania; domyślnie teraz)}.
function categoryContent(id, view, coreUp, extra) {
    const s = status(view, coreUp)
    if (s.core !== "up")
        return { items: [note("core", s.core === "down" ? "Rdzeń Streamer nie działa (usługa streamer-core)."
                                                        : "Czytam stan rdzenia…", "textSecondary")], panel: false }
    const ex = obj(extra)
    switch (id) {
    case "prep": return prepContent(view, s, ex)
    case "screens": return screensContent(view, typeof ex.now === "number" ? ex.now : Date.now())
    case "score": return scoreContent(view, coreUp, s)
    case "modules": return modulesContent(view)
    case "alerts": return alertsContent(view, s)
    case "motion": return motionContent(view, s)
    case "devices": return devicesContent(view, s, ex)
    }
    return { items: [], panel: false }
}

function prepContent(view, s, ex) {
    const core = obj(view.core)
    const items = []
    // Na żywo zmiana trybu to akcja krytyczna: na tablecie przytrzymanie, a rdzeń odrzuca ją spoza aplikacji webowej.
    items.push({ kind: "choice", key: "mode", label: "Tryb",
                 detail: joinDetail([originText(obj(core.mode).origin),
                                     s.live ? "na żywo zmienisz go tylko w Panelu streamera (przytrzymanie)" : ""]),
                 options: Object.keys(MODES).map(k => ({
                     key: k, text: MODES[k], tip: "", on: s.mode === k, enabled: s.mode === k || !s.live,
                     command: s.mode === k || s.live ? null : command("mode.set", { mode: k })
                 })) })
    const list = profiles(view)
    if (list.length)
        items.push({ kind: "choice", key: "profile", label: "Profil gracza",
                     detail: originText(obj(core.player_profile).origin),
                     options: list.map(p => ({ key: p.id, text: p.title, tip: p.detail, on: p.current, enabled: true,
                                               command: p.current ? null : command("profile.set", { profile: p.id }) })) })
    const targets = (valueOf(core.broadcast_targets) || []).map(String)
    items.push({ kind: "choice", key: "targets", label: "Cel transmisji",
                 detail: originText(obj(core.broadcast_targets).origin),
                 options: TARGET_ORDER.map(k => {
                     const on = targets.indexOf(k) >= 0
                     const next = on ? targets.filter(t => t !== k) : targets.concat([k])
                     // Ostatniego celu nie da się odznaczyć (rdzeń chce niepustej listy), jak na tablecie.
                     return { key: k, text: k === "local" ? "Lokalnie" : TARGETS[k], tip: k === "local" ? "Tylko nagranie" : "",
                              on: on, enabled: next.length > 0, command: command("target.set", { targets: next }) }
                 }) })
    const recording = obj(core.recording)[s.mode] === true
    // Tryb, który widziałeś przy kliknięciu — nagrywanie nie trafi do innego trybu, gdy tryb zmieni się w międzyczasie.
    items.push(switchRow("recording", "Nagrywanie", "zapamiętane dla trybu " + s.modeLabel, recording,
                         command("recording.set", { enabled: !recording, mode: s.mode })))
    const cocaster = valueOf(core.cocaster) === true
    items.push(switchRow("cocaster", "Współkomentator",
                         joinDetail(["suwak Rozmowa (Discord) na streamie", originText(obj(core.cocaster).origin)]),
                         cocaster, command("cocaster.set", { on: !cocaster })))
    items.push(heading("preflightHead", "Kontrola przed startem"))
    const result = ex.preflight && typeof ex.preflight === "object" ? ex.preflight : null
    items.push({ kind: "actions", key: "preflight", label: "",
                 detail: "Sprawdza OBS, obraz, dźwięk, kamerę, dysk i łącze. Ostrzega, nie blokuje.",
                 buttons: [button("run", ex.preflightRunning ? "Sprawdzam…" : result ? "Sprawdź ponownie"
                                                                                     : "Sprawdź przed startem",
                                  command("preflight.run"), !ex.preflightRunning)] })
    if (result) items.push(preflightChecks(result))
    items.push(note("start", CRITICAL_NOTE))
    return { items: items, panel: true }
}

function preflightChecks(result) {
    const checks = Array.isArray(result.checks) ? result.checks.map(obj) : []
    return { kind: "checks", key: "preflightResult",
             bad: checks.filter(c => c.status !== "ok").map(c => ({
                 id: String(c.id || ""), label: String(c.label || ""), message: String(c.message || ""),
                 fix: String(c.fix || "") })),
             ok: checks.filter(c => c.status === "ok").map(c => String(c.label || "")),
             at: typeof result.at === "number" ? Qt.formatTime(new Date(result.at), "HH:mm") : "" }
}

function screensContent(view, now) {
    const core = obj(view.core)
    const items = [heading("screensHead", "Teksty ekranów pełnych")]
    Object.keys(SCREEN_NAMES).forEach(k => {
        const sc = obj(obj(core.screens)[k])
        items.push({ kind: "form", key: "screen-" + k, label: SCREEN_NAMES[k], detail: "",
                     fields: [{ key: "title", placeholder: "Tytuł", value: String(sc.title || "") },
                              { key: "subtitle", placeholder: "Podtytuł", value: String(sc.subtitle || "") }],
                     formats: null, format: 0, save: "Zapisz", empty: true,
                     command: command("screen.set", { screen: k }) })
    })
    items.push(note("screensNote", "Pusty tytuł albo podtytuł wraca do domyślnego."))
    const ends = obj(core.countdown).ends_at
    // Odliczanie, które już minęło, nie ma czego wyłączać (rdzeń zostawia ends_at do następnej zmiany).
    const running = typeof ends === "number" && ends * 1000 > now
    items.push(heading("countdownHead", "Odliczanie"))
    items.push({ kind: "actions", key: "countdown", label: "",
                 detail: running ? "Ekrany pełne odliczają do " + clock(ends) + "."
                         : typeof ends === "number" ? "Odliczanie skończyło się o " + clock(ends) + "."
                         : "Odliczanie wyłączone.",
                 buttons: [button("5", "5 min", command("countdown.start", { minutes: 5 })),
                           button("10", "10 min", command("countdown.start", { minutes: 10 })),
                           button("clear", "Wyłącz", command("countdown.clear"), running)] })
    const topic = obj(core.topic)
    items.push(heading("topicHead", "Temat w scenie Browser"))
    items.push({ kind: "form", key: "topic", label: "",
                 detail: topic.text ? "" : "Bez tematu płytka w scenie Browser jest schowana.",
                 fields: [{ key: "text", placeholder: "Pusty chowa płytkę", value: String(topic.text || "") }],
                 formats: null, format: 0, save: "Zapisz", empty: true, command: command("topic.set", {}) })
    return { items: items, panel: false }
}

function scoreContent(view, coreUp, s) {
    const o = obj(obj(view.core).observer)
    const series = o.series && typeof o.series === "object" ? o.series : null
    const items = []
    if (series) {
        const score = Array.isArray(series.score) ? series.score : [0, 0]
        const left = String(series.left), right = String(series.right)
        items.push({ kind: "score", key: "series", left: left, right: right, score: score[0] + " : " + score[1],
                     line: "BO" + series.format + (series.finished ? " · koniec" : " · gra " + series.game),
                     winner: series.finished && series.winner ? String(series.winner) : "" })
        items.push({ kind: "actions", key: "points", label: "", detail: "", buttons: [
            button("left", "+1 " + left, command("observer.point", { side: "left" }), !series.finished, "+1 dla " + left),
            button("right", "+1 " + right, command("observer.point", { side: "right" }), !series.finished,
                   "+1 dla " + right)] })
        items.push({ kind: "actions", key: "fix", label: "", detail: "", buttons: [
            button("minusLeft", "−1 L", command("observer.point", { side: "left", delta: -1 }), score[0] > 0,
                   "−1 dla " + left),
            button("undo", "Cofnij", command("observer.undo")),
            button("swap", "Zamień", command("observer.swap"), true, "Zamień strony"),
            button("minusRight", "−1 P", command("observer.point", { side: "right", delta: -1 }), score[1] > 0,
                   "−1 dla " + right)] })
        items.push(note("reset", "Zerowanie wyniku: tylko w Panelu streamera (przytrzymanie)."))
    }
    // Nowa seria tylko bez serii albo po jej końcu — trwającej serii nie da się tu przypadkiem zastąpić.
    if (!series || series.finished) {
        const card = scoreCard(view, coreUp)
        items.push(heading("seriesHead", series ? "Następna seria" : "Nowa seria"))
        items.push({ kind: "form", key: "newSeries", label: "",
                     detail: series ? "" : "Brak serii. Wpisz graczy i wybierz BO.",
                     fields: [{ key: "left", placeholder: "Gracz lewy", value: card.left },
                              { key: "right", placeholder: "Gracz prawy", value: card.right }],
                     formats: SERIES_FORMATS, format: 3, save: "Nowa seria", empty: false,
                     command: command("observer.series.start", {}) })
    }
    items.push(heading("visibility", "Widoczność"))
    // Dwa niezależne przełączniki (decyzja 6): karta na panelu (przypięcie w bieżącym trybie) i nakładka u widzów.
    const sit = obj(view.situation)
    if (Array.isArray(sit.pinned) && Array.isArray(sit.hidden)) {
        const pinned = sit.pinned.map(String).indexOf("score") >= 0
        const auto = Array.isArray(sit.auto) && sit.auto.map(String).indexOf("score") >= 0
        items.push(switchRow("card", "Karta wyniku na panelu",
                             pinned ? "przypięta w trybie " + s.modeLabel : auto ? "auto — teraz pokazuje ją rdzeń" : "",
                             pinned, command("panel.pin", { card: "score", pinned: !pinned }),
                             { text: pinned ? "Przypięta" : "Nieprzypięta" }))
    }
    const overlay = scoreOverlay(view, coreUp)
    const word = overlay.label.replace("Wynik u widzów: ", "")
    items.push(switchRow("overlay", "Wynik u widzów", originText(obj(o.overlay).origin), overlay.shown,
                         command("observer.overlay.set", overlay.args),
                         { text: word.charAt(0).toUpperCase() + word.slice(1) }))
    items.push(heading("eventHead", "Wydarzenie"))
    const event = o.event && typeof o.event === "object" ? o.event : null
    items.push({ kind: "form", key: "event", label: "",
                 detail: event ? (event.logo ? "Z logo." : "Bez logo.") : "Castujesz bez wydarzenia.",
                 fields: [{ key: "name", placeholder: "Nazwa wydarzenia (opcjonalnie)",
                            value: event ? String(event.name || "") : "" }],
                 formats: null, format: 0, save: "Zapisz", empty: true,
                 command: command("observer.event.set", { logo: event && event.logo ? event.logo : null }) })
    if (event) {
        const buttons = []
        if (event.logo) buttons.push(button("dropLogo", "Bez logo",
                                            command("observer.event.set", { name: String(event.name || ""), logo: null })))
        buttons.push(button("clear", "Bez wydarzenia", command("observer.event.clear")))
        items.push({ kind: "actions", key: "eventActions", label: "", detail: "", buttons: buttons })
    }
    items.push(note("logo", "Logo wydarzenia (PNG, JPG albo WebP) wgrasz w Panelu streamera."))
    return { items: items, panel: true }
}

function modulesContent(view) {
    const core = obj(view.core)
    const mods = obj(core.modules)
    const items = Object.keys(MODULE_LABELS).filter(k => k in mods).map(k => {
        const on = valueOf(mods[k]) === true
        return switchRow("module-" + k, MODULE_LABELS[k],
                         joinDetail([k === "motion" ? "wspólny wyłącznik wszystkich animacji" : "",
                                     originText(obj(mods[k]).origin)]),
                         on, command("module.set", { module: k, visible: !on }))
    })
    const warn = valueOf(core.mic_warning) === true
    items.push(switchRow("micWarning", "Ostrzeżenie o wyciszonym mikrofonie",
                         joinDetail(["gdy mówisz przy wyciszonym mikrofonie", originText(obj(core.mic_warning).origin)]),
                         warn, command("mic.warning.set", { enabled: !warn })))
    items.push(note("modulesNote", "Zmiana trybu przywraca domyślne moduły trybu."))
    return { items: items, panel: false }
}

function alertsContent(view, s) {
    const a = obj(obj(view.core).alerts)
    const types = obj(a.types)
    const items = Object.keys(ALERTS).map(k => switchRow("alert-" + k, ALERTS[k], "", types[k] === true,
        command("alerts.set", { type: k, enabled: types[k] !== true }),
        { test: button("test", "Test", command("alerts.test", Object.assign({ type: k }, ALERT_TEST)), true,
                       "Testowy alert " + ALERTS[k]) }))
    items.push(switchRow("sound", "Dźwięk sub i raid", "", a.sound === true,
                         command("alerts.set", { type: "sound", enabled: a.sound !== true })))
    items.push(note("alertsNote", s.live ? "Test pojawia się na nakładce — na żywo zobaczą go też widzowie."
                                         : "Test pojawia się na nakładce w OBS."))
    return { items: items, panel: false }
}

function motionContent(view, s) {
    const core = obj(view.core)
    const m = obj(core.motion)
    const every = typeof m.peek_every_min === "number" ? m.peek_every_min : 20
    const items = []
    if (valueOf(obj(core.modules).motion) === false)
        items.push(note("motionOff", "Wszystkie wyłączone przełącznikiem „Animacje żubra” w kategorii „Na ekranie”.",
                        "neutral"))
    MOTION_KINDS.forEach(k => {
        items.push(switchRow("motion-" + k.key, k.label,
                             k.key === "peek" ? "co " + every + " min, nie w trakcie alertu" : k.about,
                             m[k.key] !== false, command("motion.set", { key: k.key, enabled: m[k.key] === false }),
                             { test: button("test", "Test", command("motion.test", { kind: k.key }), true,
                                            "Test: " + k.label) }))
    })
    items.push({ kind: "choice", key: "peek", label: "Wychylenie co", detail: "",
                 options: PEEK_MINUTES.map(n => ({
                     key: String(n), text: n + " min", tip: "", on: every === n, enabled: true,
                     command: every === n ? null : command("motion.set", { key: "peek_every_min", value: n }) })) })
    const chosen = Array.isArray(m.places) ? m.places.map(String) : PEEK_PLACES.map(p => p.key)
    items.push({ kind: "choice", key: "places", label: "Skąd się wychyla", detail: "",
                 options: PEEK_PLACES.map(p => {
                     const on = chosen.indexOf(p.key) >= 0
                     const next = on ? chosen.filter(x => x !== p.key) : chosen.concat([p.key])
                     return { key: p.key, text: p.text, tip: "", on: on, enabled: true,
                              command: command("motion.set", { key: "places", value: next }) }
                 }) })
    items.push(note("motionNote", s.live ? "Na żywo test zobaczysz tylko tutaj, na podglądzie sceny."
                                         : "Poza anteną test gra na nakładce w OBS."))
    return { items: items, panel: false }
}

function devicesContent(view, s, ex) {
    const core = obj(view.core)
    const items = []
    // Kamera (iPhone) jak na tablecie: ostatnia kontrola przed startem zgłosiła problem przy widocznym module kamery.
    const camera = cameraProblem(view, ex.preflight)
    if (camera)
        items.push(note("cameraProblem", "Kamera (iPhone) nie odpowiada" + (camera.message ? ": " + camera.message : "."),
                        "neutral"))
    items.push(heading("devicesHead", "Sparowane urządzenia"))
    // Odłączanie jest tutaj, bo konsola działa na tym komputerze (pairing.revoke działa tylko lokalnie).
    const list = Array.isArray(view.devices) ? view.devices.map(obj) : []
    list.forEach(d => {
        const id = String(d.id || "")
        // Na żywo odłączenie połączonego urządzenia od razu zamyka na nim Panel albo Pilota — mówi to potwierdzenie.
        items.push({ kind: "row", key: "device-" + id, label: String(d.name || id),
                     detail: d.online === true ? "połączony" : "offline", tint: d.online === true ? "positive" : "",
                     on: null, test: null,
                     button: { text: "Odłącz", confirm: "Na pewno odłączyć?", enabled: id !== "",
                               warning: d.online === true && s.live
                                        ? "Na żywo: Panel streamera albo Pilot na tym urządzeniu od razu się rozłączy." : "",
                               command: command("pairing.revoke", { device: id }) } })
    })
    if (!list.length) items.push(note("noDevices", "Brak sparowanych urządzeń."))
    items.push(note("pairNote", "Nowe urządzenie sparujesz kafelkiem „Tablet” w docku Stream (kod QR). Odłączone "
                                + "urządzenie musi sparować się od nowa."))
    items.push(heading("linksHead", "Połączenia"))
    const twitch = obj(core.twitch)
    items.push(stateRow("twitch", "Twitch", joinDetail([TWITCH_STATES[twitch.state] || "niepołączony",
                                                        twitch.reason ? String(twitch.reason) : ""]),
                        twitch.state === "connected" ? "positive" : WARNING_TINT))
    items.push(stateRow("obs", "OBS", s.obsConnected ? "połączony" : joinDetail(["rozłączony", s.obsError]),
                        s.obsConnected ? "positive" : WARNING_TINT))
    return { items: items, panel: false }
}

// Komenda formularza z wpisanych wartości ({klucz pola: tekst}) albo null, gdy formularz jest niepełny.
// format — wybrane BO (nowa seria). Wydarzenie bez nazwy to observer.event.clear, jak na tablecie.
function formCommand(item, values, format) {
    const it = obj(item)
    const vals = obj(values)
    const base = obj(it.command)
    if (!base.name) return null
    if (base.name === "observer.series.start") return seriesStart(vals.left, vals.right, format)
    const args = Object.assign({}, obj(base.args))
    const fields = Array.isArray(it.fields) ? it.fields : []
    for (let i = 0; i < fields.length; i++) {
        const raw = fields[i].key in vals ? vals[fields[i].key] : fields[i].value
        const text = String(raw === undefined || raw === null ? "" : raw).trim()
        if (text === "" && !it.empty) return null
        args[fields[i].key] = text
    }
    if (base.name === "observer.event.set" && args.name === "") return command("observer.event.clear")
    return command(base.name, args)
}

// Karty z rdzenia (core/src/kamp/situation.py): auto według etapu i trybu, z katalogu PINNABLE do przypięcia.
var NOW_CARDS = {
    score: "Wynik", match: "Mecz teraz", event: "Wydarzenie", alerts: "Ostatnie alerty",
    topic: "Temat w scenie Browser", countdown: "Odliczanie", camera: "Kamera", prep: "Przygotowanie",
    preflight: "Kontrola przed startem", start_screen: "Ekran startowy", go_live: "Start streamu",
    obs_offline: "OBS wyłączony", brb_screen: "Ekran przerwy", brb_return: "Powrót z przerwy",
    end_screen: "Ekran końcowy", go_off: "Koniec streamu", summary: "Podsumowanie"
}
var PINNABLE = ["score", "match", "event", "alerts", "topic", "countdown", "camera"]
var STAGES = { offline: "OBS wyłączony", before: "przed streamem", live: "na żywo", brb: "przerwa", ended: "koniec" }

function cardTitle(id) {
    return NOW_CARDS[id] || String(id)
}

// Kolumna „Teraz”: {controls, stage, items: [{id, title, state, pinnable, hideable}], hidden: [{id, title}],
// addable: [{id, title}]}. state: "auto", "pinned" albo "" (rdzeń bez przypinania: bez znacznika i przycisków).
// Karta auto i przypięta naraz to „przypięta” — zostanie po zmianie etapu.
function now(view, coreUp) {
    const out = { controls: false, stage: "", items: [], hidden: [], addable: [] }
    if (status(view, coreUp).core !== "up") return out
    const sit = obj(view.situation)
    const cards = Array.isArray(sit.cards) ? sit.cards.map(String) : []
    out.controls = Array.isArray(sit.pinned) && Array.isArray(sit.hidden) && Array.isArray(sit.addable)
    out.stage = [STAGES[sit.stage] || "", modeLabel(sit.mode)].filter(x => x).join(" · ")
    const pinned = out.controls ? sit.pinned.map(String) : []
    out.items = cards.map(id => ({
        id: id, title: cardTitle(id),
        state: !out.controls ? "" : pinned.indexOf(id) >= 0 ? "pinned" : "auto",
        pinnable: out.controls && PINNABLE.indexOf(id) >= 0,
        hideable: out.controls && id !== "obs_offline"
    }))
    if (!out.controls) return out
    out.hidden = sit.hidden.map(String).map(id => ({ id: id, title: cardTitle(id) }))
    // Schowana karta wraca chipem w nagłówku, nie z „Dodaj kartę” — jedna droga zamiast dwóch.
    const hidden = sit.hidden.map(String)
    out.addable = sit.addable.map(String).filter(id => PINNABLE.indexOf(id) >= 0 && hidden.indexOf(id) < 0)
        .map(id => ({ id: id, title: cardTitle(id) }))
    return out
}

// Komendy kart (core/README.md, „Komendy”): przypięcie w bieżącym trybie, schowanie do następnego streamu.
function pinCommand(item) {
    return { name: "panel.pin", args: { card: item.id, pinned: item.state !== "pinned" } }
}

function hideCommand(id, hidden) {
    return { name: "panel.hide", args: { card: id, hidden: hidden } }
}

// „Wynik u widzów” w karcie Zawsze (decyzja 6): nakładka wyniku, niezależna od karty wyniku na panelu.
function scoreOverlay(view, coreUp) {
    if (status(view, coreUp).core !== "up")
        return { on: false, series: false, enabled: false, label: "Wynik u widzów", args: { visible: true } }
    // Słowa jak na tablecie: „tak” tylko wtedy, gdy widzowie naprawdę widzą wynik (situation.score_shown,
    // u starszego rdzenia seria i włączona nakładka); bez serii nakładka nie ma czego pokazać.
    const o = obj(view.core.observer)
    const on = valueOf(o.overlay) !== false
    const series = !!(o.series && typeof o.series === "object")
    const sit = obj(view.situation)
    const shown = typeof sit.score_shown === "boolean" ? sit.score_shown : series && on
    const label = shown ? "tak" : series ? "nie" : on ? "po założeniu serii" : "nie · brak serii"
    return { on: on, shown: shown, series: series, enabled: true, label: "Wynik u widzów: " + label,
             args: { visible: !on } }
}

// Karta wyniku: seria albo formularz nowej serii (decyzja 6: bez serii pola „Nowa seria” na karcie).
// chip: kto widzi wynik — „u widzów” albo „tylko ty” (karta przypięta przy schowanej nakładce).
var SERIES_FORMATS = [1, 3, 5, 7]

function scoreCard(view, coreUp) {
    const o = obj(obj(obj(view).core).observer)
    const series = coreUp && o.series && typeof o.series === "object" ? o.series : null
    const history = Array.isArray(o.player_history) ? o.player_history.map(String) : []
    const chip = !series ? "" : valueOf(o.overlay) !== false ? "u widzów" : "tylko ty"
    return { series: series, chip: chip, left: history[0] || "", right: history[1] || "", format: 3 }
}

function seriesStart(left, right, format) {
    const l = String(left || "").trim()
    const r = String(right || "").trim()
    if (l === "" || r === "" || SERIES_FORMATS.indexOf(format) < 0) return null
    return { name: "observer.series.start", args: { left: l, right: r, format: format } }
}

function clock(seconds) {
    const d = new Date(seconds * 1000)
    return Qt.formatTime(d, "HH:mm")
}

function duration(seconds) {
    const min = Math.max(0, Math.round(seconds / 60))
    return Math.floor(min / 60) + ":" + String(min % 60).padStart(2, "0")
}

function screenLine(core, key) {
    const sc = obj(obj(core.screens)[key])
    return [sc.title, sc.subtitle].filter(x => x).map(String).join(" — ")
}

// Treść kart „Teraz” bez osobnego komponentu: {lines: [{text, tone}], action: {text, name, args} | null, panel,
// category}. tone: rola z palety (textPrimary, textSecondary, textMuted). panel: odesłanie do Panelu streamera
// (akcje krytyczne). category: id kategorii z ustawieniami tej karty (PT-106) — link w nagłówku otwiera jej popup.
// alerts — teksty ostatnich alertów z czatu.
function nowBody(view, coreUp, id, alerts) {
    const out = { lines: [], action: null, panel: false, category: "" }
    const s = status(view, coreUp)
    if (s.core !== "up") return out
    const core = obj(view.core)
    const line = (text, tone) => out.lines.push({ text: String(text), tone: tone || "textPrimary" })
    const screen = key => {
        const text = screenLine(core, key)
        if (text) line(text)
        else line("Bez tekstu — ustawisz go w kategorii Sceny i ekrany.", "textMuted")
        out.category = "screens"
    }
    switch (id) {
    case "match": {
        const m = matchNow(view, coreUp)
        if (m.me) {
            line(m.me.name + " · " + m.me.line)
            m.opponents.forEach(p => line("vs " + p.name + " · " + p.line, "textSecondary"))
            line(m.source, "textMuted")
        } else line(m.note, "textMuted")
        break
    }
    case "event": {
        const e = obj(obj(core.observer).event)
        if (e.name) line(e.name + (e.logo ? " · z logo" : ""))
        else line("Bez wydarzenia — castujesz bez nazwy.", "textMuted")
        out.category = "score"
        break
    }
    case "alerts":
        if (Array.isArray(alerts) && alerts.length) alerts.forEach(a => line(a))
        else line("Jeszcze bez alertów w tym streamie.", "textMuted")
        break
    case "topic": {
        const t = obj(core.topic)
        if (t.text) line((t.label ? t.label + ": " : "") + t.text)
        else line("Bez tematu — płytka w scenie Browser jest schowana.", "textMuted")
        out.category = "screens"
        break
    }
    case "countdown": {
        const ends = obj(core.countdown).ends_at
        if (typeof ends === "number") line("Ekrany pełne odliczają do " + clock(ends))
        else line("Odliczanie wyłączone.", "textMuted")
        out.category = "screens"
        break
    }
    case "camera": {
        const c = camera(view, coreUp)
        line("Kamera " + c.value + (c.origin ? " · " + c.origin : ""))
        if (c.enabled) out.action = { text: c.visible ? "Ukryj kamerę" : "Pokaż kamerę", name: "module.set",
                                      args: { module: "camera", visible: !c.visible } }
        break
    }
    case "prep": {
        const origin = originText(s.modeOrigin)
        line("Tryb " + s.modeLabel + (origin ? " (" + origin + ")" : "") + " · cel "
             + (s.targets.length ? s.targets.join(", ") : "—"))
        line("Nagrywanie " + (s.recordingPlanned ? "włączy się ze startem" : "wyłączone") + " · profil "
             + (s.profile || "—"), "textSecondary")
        out.category = "prep"
        break
    }
    case "preflight":
        line("Sprawdza OBS, obraz, dźwięk, kamerę, dysk i łącze — uruchomisz ją w kategorii Przygotowanie.",
             "textSecondary")
        out.category = "prep"
        break
    case "start_screen":
        screen("starting")
        if (typeof obj(core.countdown).ends_at === "number") line("Odliczanie do " + clock(core.countdown.ends_at),
                                                                   "textSecondary")
        break
    case "go_live":
    case "go_off":
        line((id === "go_live" ? "Start" : "Koniec") + " streamu tylko w Panelu streamera (przytrzymanie).",
             "textSecondary")
        out.panel = true
        break
    case "obs_offline":
        line("Uruchom OBS — bez niego sceny, mikrofon i podgląd są niedostępne.", "textSecondary")
        break
    case "brb_screen":
        screen("brb")
        break
    case "brb_return": {
        const back = valueOf(core.brb_return)
        if (back && typeof back === "string") line("Powrót do sceny " + back, "textSecondary")
        out.action = { text: "Wróć z przerwy", name: "brb.toggle", args: {} }
        break
    }
    case "end_screen":
        screen("ended")
        break
    case "summary": {
        const sum = core.last_summary
        if (sum && typeof sum === "object") {
            if (typeof sum.duration_s === "number") line("Czas streamu " + duration(sum.duration_s))
            if (typeof sum.new_followers === "number") line("Nowi obserwujący: " + sum.new_followers, "textSecondary")
            if (sum.recording) line("Nagranie zapisane", "textSecondary")
        } else line("Podsumowanie pojawi się po końcu streamu.", "textMuted")
        break
    }
    default:
        line("Ta karta jest na razie tylko w Panelu streamera.", "textMuted")
        out.panel = true
    }
    return out
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
                 user_id: String(m.user_id || ""),
                 text: String(m.text || ""), rich_text: chatBody(m) }
    }
    if (e.type === "alert" && e.alert)
        return { kind: "alert", id: "alert-" + lineCounter, user: String(e.alert.user || ""),
                 user_id: "", text: alertText(e.alert), rich_text: "" }
    return null
}

// Treść widza do Text.StyledText: wszystko escapowane, więc wiadomość nie wstawi obrazka ani znacznika.
function escapeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

// Fragmenty z EventSub: identyfikator emotki jest walidowany, URL powstaje wyłącznie z domeny Twitch CDN.
function chatBody(message) {
    const fragments = obj(message).fragments
    if (!Array.isArray(fragments)) return escapeHtml(obj(message).text || "")
    return fragments.map(f => {
        const part = obj(f)
        const fallback = escapeHtml(part.text || "")
        const id = String(part.emote_id || "")
        if (part.type !== "emote" || !/^[A-Za-z0-9_-]+$/.test(id)) return fallback
        return '<img src="https://static-cdn.jtvnw.net/emoticons/v2/' + id
            + '/static/light/1.0" width="28" height="28" align="middle" alt="' + fallback + '">'
    }).join("")
}

// Wiersz czatu: pogrubiony nick w kolorze (z palety) i wiadomość w jednym akapicie, który się zawija.
function chatHtml(line, nickColor) {
    return "<b><font color=\"" + escapeHtml(nickColor) + "\">" + escapeHtml(line.user) + "</font></b>&nbsp; "
        + (line.rich_text || escapeHtml(line.text))
}

// Komendy, które widżety mogą wysłać do rdzenia. Akcji krytycznych (start i koniec streamu, koniec nagrania,
// zmiana trybu na żywo, reset serii) nie ma w widżetach — są tylko w Panelu streamera (przytrzymanie).
// Mikrofon przez mic.set {muted} (nie mic.toggle): podwójne kliknięcie albo spóźniony stan nie odwróci wyniku.
// Wynik serii na lewym monitorze (PT-91, decyzja 29.10): +1, cofnij, zamiana i ukrycie; zerowanie tylko w Panelu
// (observer.reset jest w rdzeniu krytyczna, więc i tak odrzuciłby ją z D-Bus).
// PT-105: nowa seria z karty wyniku bez serii (decyzja 6 K-29) oraz przypinanie i chowanie kart „Teraz”.
// PT-106: ustawienia siedmiu kategorii (K-29 decyzja 2) — te same komendy co kategorie na tablecie. mode.set
// tylko poza anteną: na żywo to akcja krytyczna, przyciski trybu są wtedy wyłączone, allowedNow() jej nie
// przepuszcza, a rdzeń i tak odrzuca ją spoza aplikacji webowej.
var ALLOWED = ["mic.set", "profile.set", "module.set", "motion.set", "motion.test", "scene.set", "brb.toggle", "pairing.invite", "pairing.revoke",
               "privacy.toggle", "overlays.toggle", "observer.point", "observer.undo", "observer.swap", "observer.overlay.set",
               "observer.series.start", "panel.pin", "panel.hide",
               "mode.set", "target.set", "recording.set", "cocaster.set", "preflight.run", "screen.set",
               "countdown.start", "countdown.clear", "topic.set", "observer.event.set", "observer.event.clear",
               "mic.warning.set", "alerts.set", "alerts.test"]

function allowed(name) {
    return ALLOWED.indexOf(name) >= 0
}

// Czy komendę wolno wysłać teraz: z listy ALLOWED, a zmiana trybu tylko poza anteną.
function allowedNow(name, view, coreUp) {
    if (!allowed(name)) return false
    return !(name === "mode.set" && status(view, coreUp).live === true)
}

// Ściąga ze skrótami aktywności Stream (plan PT-38). Skróty Stream działają tylko w aktywności Stream.
var SHORTCUTS = [
    { keys: "Pause", action: "mikrofon" },
    { keys: "Meta+Pause", action: "ekran prywatności" },
    { keys: "Meta+B", action: "BRB i powrót" },
    { keys: "Meta+F9", action: "plan gry: start/pauza" },
    { keys: "Meta+F10", action: "plan gry: −5 s" },
    { keys: "Meta+F11", action: "plan gry: +5 s" },
    { keys: "Meta+Shift+A", action: "Praca ⇄ Stream" },
    { keys: "Meta+A", action: "czekający agent" }
]

// --- Lewy monitor w aktywności Stream: konsola streamu albo Granie (PT-83, decyzja 82) ---
// Decyzja użytkownika 28.10: „Czasem po prostu chcę pograć i mieć mniej nawalonych rzeczy na lewym ekranie”.
// Widok wybiera automat według OBS: połączony → konsola, wyłączony → Granie, na żywo zawsze konsola. Ręczne
// nadpisanie ({view, key}) obowiązuje do następnej zmiany stanu OBS (klucz `key`), na żywo nie działa wcale —
// zasada streamer: każdy automat ma ręczne nadpisanie i widać, kto ustawił wartość.
var DESK_VIEWS = { console: "Konsola streamu", gaming: "Granie" }

// Pamięć stanu OBS widżetu: napis (klucz) albo {key, since} z CoreSource.obsMemory — since to chwila wejścia w „lost”.
function memoryKey(memory) {
    return memory && typeof memory === "object" ? String(memory.key || "") : String(memory || "")
}

// Stan OBS, którego zmiana kończy nadpisanie: down i loading (rdzeń), live (na żywo), on (połączony poza anteną),
// lost (rozłączony zaraz po antenie albo po lost — awaria w trakcie streamu), off (wyłączony, bez streamu).
// „lost” wynika z pamięci widżetu (memory: poprzedni stan bez przerw rdzenia, obsMemory), nie z core.session — rdzeń
// czyści je tylko w stream.stop, więc po zatrzymaniu streamu w OBS albo po awarii OBS zostaje na zawsze, a stream
// włączony w samym OBS go nie ma (review PT-83). Bez historii (start powłoki przy rozłączonym OBS) to „off”.
function obsKey(view, coreUp, memory) {
    const s = status(view, coreUp)
    if (s.core !== "up") return s.core
    if (s.live) return "live"
    if (s.obsConnected) return "on"
    return streamMemory(memory) ? "lost" : "off"
}

// Nowa pamięć widżetu {key, since} po zmianie surowego klucza (obsKey bez pamięci: live, on, off, down, loading):
// przerwa w rdzeniu jej nie zmienia, a rozłączony OBS po antenie zostaje „lost” (since — chwila utraty, now w ms)
// aż do ponownego połączenia z OBS albo do wygaśnięcia (obsExpire).
function obsMemory(memory, key, now) {
    const prev = memoryKey(memory)
    const since = memory && typeof memory === "object" ? Number(memory.since) || 0 : 0
    if (key === "down" || key === "loading") return { key: prev, since: since }
    if (key === "off" || key === "lost") {
        if (prev === "lost") return { key: "lost", since: since }
        if (prev === "live") return { key: "lost", since: now }
    }
    return { key: key, since: 0 }
}

// Po 10 min bez połączenia z OBS stream na pewno już nie idzie: „lost” przechodzi w „off” (spokojny kafelek,
// automat Granie). CoreSource sprawdza to rzadkim zegarem tylko wtedy, gdy pamięć to „lost”.
var OBS_LOST_LIMIT = 10 * 60 * 1000

function obsExpire(memory, now, limit) {
    const lim = typeof limit === "number" ? limit : OBS_LOST_LIMIT
    if (memoryKey(memory) !== "lost" || now - (Number(memory.since) || 0) < lim) return memory
    return { key: "off", since: 0 }
}

function streamMemory(memory) {
    const key = memoryKey(memory)
    return key === "live" || key === "lost"
}

var DESK_AUTO = {
    down: { view: "gaming", reason: "rdzeń nie działa" },
    loading: { view: "gaming", reason: "czytam stan rdzenia…" },
    live: { view: "console", reason: "na żywo — konsola zawsze widoczna" },
    on: { view: "console", reason: "OBS połączony" },
    lost: { view: "console", reason: "OBS rozłączony w trakcie streamu" },
    off: { view: "gaming", reason: "OBS wyłączony" }
}

// Nadpisanie, które jeszcze obowiązuje (ten sam stan OBS, nie na żywo), albo null.
function deskOverride(override, view, coreUp, memory) {
    const key = obsKey(view, coreUp, memory)
    if (!override || key === "live" || override.key !== key || !(override.view in DESK_VIEWS)) return null
    return override
}

// Automat dla klucza; przerwa w rdzeniu po antenie zostawia konsolę (na żywo nie miga na Granie).
function deskAuto(key, memory) {
    const auto = DESK_AUTO[key]
    return (key === "down" || key === "loading") && streamMemory(memory) ? { view: "console", reason: auto.reason } : auto
}

// Widok lewego monitora: {view, label, origin ("auto" | "user"), originText, forced, reason, key}.
function desk(view, coreUp, override, memory) {
    const key = obsKey(view, coreUp, memory)
    const auto = deskAuto(key, memory)
    const mine = deskOverride(override, view, coreUp, memory)
    const chosen = mine && mine.view !== auto.view ? mine.view : auto.view
    const origin = chosen === auto.view ? "auto" : "user"
    return {
        view: chosen,
        label: DESK_VIEWS[chosen],
        origin: origin,
        originText: origin === "user" ? "ustawiłeś ty · do zmiany OBS" : "automat",
        forced: key === "live",
        reason: auto.reason,
        key: key
    }
}

// Nowe nadpisanie po wyborze widoku `target`: null, gdy to widok automatu (wraca „automat”) albo na żywo.
function deskToggle(view, coreUp, override, target, memory) {
    const key = obsKey(view, coreUp, memory)
    if (key === "live" || !(target in DESK_VIEWS) || deskAuto(key, memory).view === target) return null
    return { view: target, key: key }
}

// --- Widok Granie: profile graczy, mecz teraz, ostatnie mecze, bilans dnia (PT-83) ---
// Rdzeń daje dziś tylko `core.match` (bieżący mecz aktywnego profilu, tryb Xbox z modułem Przeciwnik; gracze
// w kształcie core/src/kamp/match.py). Reszta to proponowane API (decyzja 82), na które widok już czeka:
// - `core.player_stats` {profil: {rank, rank_name, rating, win_rate, form, last_played (s)}} — przegląd kont;
// - `core.recent_matches` {profile, games: [{game_id, result: win|loss, civ_name, opponent, opponent_civ_name, map,
//   rating_change}]} — ostatnie mecze aktywnego profilu;
// - `core.today` {date, profile, games, wins, losses, rating_change: {profil: liczba}} — bilans dnia.
var GAMING_UNAVAILABLE = {
    stats: "Ranga, rating i forma profili graczy: jeszcze niedostępne w rdzeniu",
    recent: "Ostatnie mecze: jeszcze niedostępne w rdzeniu",
    today: "Bilans dnia: jeszcze niedostępny w rdzeniu",
    matchPc: "Tryb PC służy do castowania — tu nie śledzimy meczu gracza"
}

function coreNote(s) {
    return s.core === "down" ? "Rdzeń nie działa" : "Czytam stan rdzenia…"
}

function gamingSource(view, key) {
    const core = obj(obj(view).core)
    if (!("gaming_data_error" in core)) return null
    const errors = obj(core.gaming_data_error)
    if (key === "profiles" && Array.isArray(errors.profiles) && errors.profiles.length)
        return "Brak danych AoE4 World dla: " + errors.profiles.join(", ")
    if (key === "history" && errors.history) return String(errors.history)
    return "Pobieram dane z AoE4 World…"
}

function rankName(p) {
    const name = p && p.rank_name ? String(p.rank_name) : ""
    return name === "" || name === "Unranked" ? "bez rangi" : name
}

// Znaki W i L (forma z match.py: od najstarszej).
function formOf(p) {
    return Array.isArray(obj(p).form) ? p.form.filter(r => r === "W" || r === "L") : []
}

function known(value) {
    return value !== undefined && value !== null && String(value) !== "" && String(value) !== "—"
}

// „Platinum II · rating 1234 · 54% wygranych”; bez rangi i ratingu tylko „bez rangi”. W meczu przed rangą
// cywilizacja, a procent wygranych tylko u przeciwnika (jak w mocku).
function statsLine(p, withCiv, withWinRate) {
    const parts = []
    if (withCiv && obj(p).civ_name) parts.push(String(p.civ_name))
    parts.push(rankName(p))
    if (known(obj(p).rating)) parts.push("rating " + p.rating)
    if (withWinRate && known(obj(p).win_rate)) parts.push(p.win_rate + " wygranych")
    return parts.join(" · ")
}

function ago(seconds, now) {
    const min = Math.max(0, Math.floor((now / 1000 - seconds) / 60))
    if (min < 60) return min < 2 ? "przed chwilą" : min + " min temu"
    const hours = Math.floor(min / 60)
    if (hours < 24) return hours + " godz. temu"
    const days = Math.floor(hours / 24)
    if (days === 1) return "wczoraj"
    if (days < 14) return days + " dni temu"
    return Math.floor(days / 7) + " tyg. temu"
}

function myPlayer(match) {
    const m = obj(match)
    const all = (Array.isArray(m.players) ? m.players : []).concat(obj(m.teams).us || [])
    return all.filter(p => obj(p).me)[0] || null
}

// Konta: {items: [{id, title, detail, current, line, form}], note}. now — czas w ms (ostatnia gra).
function accounts(view, coreUp, now) {
    const s = status(view, coreUp)
    if (s.core !== "up") return { items: [], note: coreNote(s) }
    const stats = obj(view.core).player_stats
    const match = obj(view.core.match)
    const me = match.state === "data" && match.profile === s.profile ? myPlayer(match) : null
    const items = profiles(view).map(p => {
        const st = stats && typeof stats === "object" ? stats[p.id] : null
        let line = ""
        let form = []
        if (p.current && me) {
            line = statsLine(me, false, true)
            // Migawka bieżącego meczu bywa krótsza od historii profilu; pokaż pełniejsze pięć wyników.
            const matchForm = formOf(me)
            const profileForm = formOf(st)
            form = profileForm.length >= matchForm.length ? profileForm : matchForm
        } else if (st) {
            line = statsLine(st, false, true)
            if (!p.current && typeof st.last_played === "number") line += " · ostatnio " + ago(st.last_played, now)
            form = formOf(st)
        }
        // Nazwa profilu różna od gamertagu tylko wielkością liter niczego nie dodaje (Streamer i streamer).
        const detail = p.detail.toLowerCase() === p.title.toLowerCase() ? "" : p.detail
        return Object.assign({}, p, { detail: detail, line: line, form: form })
    })
    const source = gamingSource(view, "profiles")
    const note = items.length === 0 ? "Rdzeń nie podał profili graczy"
        : source && source.indexOf("Brak danych") === 0 ? source
        : stats && typeof stats === "object" ? "" : source || GAMING_UNAVAILABLE.stats
    return { items: items, note: note }
}

function matchPlayer(p, opponent) {
    return { name: String(obj(p).name || "?"), line: statsLine(p, true, opponent), form: formOf(p) }
}

// Mecz teraz: {state: data|searching|none|off|down|loading, source, me, opponents, allies, note}.
function matchNow(view, coreUp) {
    const s = status(view, coreUp)
    const out = { state: s.core === "up" ? "off" : s.core, source: "AoE4 World", me: null, opponents: [], allies: [],
                  note: "" }
    if (s.core !== "up") {
        out.note = coreNote(s)
        return out
    }
    const m = view.core.match
    if (!m || typeof m !== "object") {
        const opponent = valueOf(obj(obj(view.core.modules).opponent))
        out.note = s.mode === "pc" ? GAMING_UNAVAILABLE.matchPc
            : s.mode !== "xbox" ? "Tryb " + s.modeLabel + ": bez danych meczu"
            : opponent === false ? "Moduł Przeciwnik wyłączony — rdzeń nie szuka meczu" : "Szukam meczu w AoE4 World…"
        return out
    }
    out.state = m.state === "data" || m.state === "searching" ? m.state : "none"
    // Mecz poprzedniego profilu (rdzeń jeszcze nie przeszukał nowego) to szukanie, nie cudze dane.
    if (out.state === "data" && m.profile !== s.profile) out.state = "searching"
    if (out.state === "searching") {
        out.note = "Szukam meczu w AoE4 World…"
        return out
    }
    if (out.state === "none") {
        out.note = m.reason ? String(m.reason) : "Brak trwającego meczu"
        return out
    }
    out.source = ["AoE4 World", m.mode, m.map].filter(x => x).join(" · ")
    const me = myPlayer(m)
    const teams = obj(m.teams)
    const them = Array.isArray(teams.them) ? teams.them : (m.players || []).filter(p => !obj(p).me)
    out.me = me ? matchPlayer(me, false) : null
    out.opponents = them.map(p => matchPlayer(p, true))
    out.allies = (Array.isArray(teams.us) ? teams.us : []).filter(p => !obj(p).me).map(p => String(p.name || "?"))
    return out
}

function signed(n) {
    return n > 0 ? "+" + n : n < 0 ? "−" + Math.abs(n) : "±0"
}

// Ostatnie mecze aktywnego profilu: {available, title, items: [{result, tint, civs, opponent, map, rating}], note}.
function recentMatches(view, coreUp) {
    const s = status(view, coreUp)
    const out = { available: false, title: "Ostatnie mecze", items: [], note: "" }
    if (s.core !== "up") {
        out.note = coreNote(s)
        return out
    }
    if (s.profile) out.title += " · " + s.profile
    const recent = view.core.recent_matches
    if (!recent || typeof recent !== "object") {
        out.note = gamingSource(view, "history") || GAMING_UNAVAILABLE.recent
        return out
    }
    if (recent.profile !== s.profile) {
        out.note = "Odświeżam ostatnie mecze aktywnego profilu…"
        return out
    }
    const games = Array.isArray(recent.games) ? recent.games : []
    out.items = games.map(g => ({
        result: g.result === "win" ? "W" : g.result === "loss" ? "L" : "—",
        tint: g.result === "win" ? "positive" : g.result === "loss" ? "negative" : "textMuted",
        civs: [g.civ_name, g.opponent_civ_name].filter(x => x).join(" vs "),
        opponent: String(g.opponent || ""),
        map: String(g.map || ""),
        rating: typeof g.rating_change === "number" ? signed(g.rating_change) : ""
    }))
    out.available = out.items.length > 0
    if (!out.available) out.note = "Brak ostatnich meczów tego profilu"
    return out
}

function plural(n, one, few, many) {
    if (n === 1) return one
    const d = n % 10, h = n % 100
    return d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many
}

// Bilans dnia: {available, games, wins, losses, rating, note}; date — dzisiejsza data w Polsce do testów.
function today(view, coreUp, date) {
    const s = status(view, coreUp)
    const out = { available: false, games: "", wins: "", losses: "", rating: "", note: "" }
    if (s.core !== "up") {
        out.note = coreNote(s)
        return out
    }
    const t = view.core.today
    if (!t || typeof t !== "object") {
        out.note = gamingSource(view, "history") || GAMING_UNAVAILABLE.today
        return out
    }
    if (t.profile !== s.profile) {
        out.note = "Odświeżam bilans aktywnego profilu…"
        return out
    }
    if (t.date !== (date || Qt.formatDate(new Date(), "yyyy-MM-dd"))) {
        out.note = "Odświeżam bilans dzisiejszego dnia…"
        return out
    }
    if (typeof t.games !== "number" || typeof t.wins !== "number" || typeof t.losses !== "number") {
        out.note = "Bilans dnia chwilowo niedostępny"
        return out
    }
    const games = Number(t.games) || 0
    out.available = true
    out.games = games + " " + plural(games, "mecz", "mecze", "meczów")
    out.wins = (Number(t.wins) || 0) + " W"
    out.losses = (Number(t.losses) || 0) + " L"
    const changes = obj(t.rating_change)
    const keys = Object.keys(changes).filter(k => typeof changes[k] === "number")
    out.rating = keys.length === 1 ? "rating " + signed(changes[keys[0]])
        : keys.length > 1 ? "rating " + keys.map(k => k + " " + signed(changes[k])).join(", ") : ""
    if (games === 0) out.note = "Dziś jeszcze bez meczów"
    return out
}
