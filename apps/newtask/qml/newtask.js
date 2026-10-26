// Logika okna „Nowe zadanie” bez QML: filtrowanie repozytoriów, teksty i prośba dla usługi.
// Osobny plik, żeby testy (qml6) sprawdzały ją bez rysowania okna.
.pragma library

var TOOLS = [
    { id: "claude", label: "Claude Code" },
    { id: "codex", label: "Codex" }
]

// Bez wielkości liter i polskich znaków: „zolc” znajdzie „Żółć”.
function fold(text) {
    var map = { "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z" }
    return (text || "").toLowerCase().replace(/[ąćęłńóśźż]/g, function (ch) { return map[ch] })
}

// Każde słowo zapytania musi trafić w nazwę repozytorium albo jego ścieżkę. Kolejność z usługi
// (od ostatnio aktywnych) zostaje; przy zapytaniu nazwy zaczynające się od niego idą wyżej.
function filterRepos(repos, query) {
    var words = fold(query).split(/\s+/).filter(function (w) { return w !== "" })
    if (!words.length) return repos.slice()
    var hits = repos.filter(function (r) {
        var fields = [fold(r.name), fold(r.path)]
        return words.every(function (w) { return fields.some(function (f) { return f.indexOf(w) >= 0 }) })
    })
    var first = words[0]
    return hits.filter(function (r) { return fold(r.name).indexOf(first) === 0 })
        .concat(hits.filter(function (r) { return fold(r.name).indexOf(first) !== 0 }))
}

function findRepo(repos, path) {
    for (var i = 0; i < repos.length; i++) if (repos[i].path === path) return repos[i]
    return null
}

function tildePath(path, home) {
    if (home && (path === home || (path || "").indexOf(home + "/") === 0)) return "~" + path.slice(home.length)
    return path || ""
}

function shortAgo(ts, nowTs) {
    if (!ts) return "—"
    var s = Math.max(0, nowTs - ts)
    if (s < 3600) return Math.max(1, Math.floor(s / 60)) + " min"
    if (s < 86400) return Math.floor(s / 3600) + " h"
    var days = Math.floor(s / 86400)
    if (days < 60) return days + " d"
    return Math.floor(days / 30) + " mies."
}

function wordCount(text) {
    return (text || "").trim().split(/\s+/).filter(function (w) { return w !== "" }).length
}

// Co jeszcze blokuje start (pusty napis, gdy nic). Usługa sprawdza to samo jeszcze raz.
function blocker(state) {
    if (!state.available) return "Usługa tide-daemon nie działa"
    if (!state.repo) return "Wybierz repozytorium"
    if (state.tools && state.tools[state.tool] === false) return "Nie znaleziono programu " + state.tool
    if (wordCount(state.prompt) === 0) return "Napisz, co agent ma zrobić"
    if (wordCount(state.prompt) < 2) return "Opisz zadanie co najmniej dwoma słowami"
    if (state.newWorktree && !state.repo.mainBranch) return "Repozytorium nie ma master, main ani develop — wyłącz „Nowy worktree”"
    if (state.newWorktree && (state.branch || "").trim() === "") return "Podaj nazwę gałęzi"
    return ""
}

// Podpis karty „Nowy worktree” (przed startem; usługa sprawdza to samo jeszcze raz).
function worktreeHint(repo, newWorktree) {
    if (!repo) return { text: "", warn: false }
    if (!newWorktree) {
        return { text: "Agent pracuje w głównym katalogu" + (repo.branch ? " na " + repo.branch : "")
                       + (repo.dirty > 0 ? " · " + repo.dirty + " niezacommitowanych zmian" : ""),
                 warn: repo.dirty > 0 }
    }
    if (!repo.mainBranch) return { text: "Brak lokalnej gałęzi master, main ani develop", warn: true }
    if (repo.dirty > 0 && repo.branch === repo.mainBranch) {
        return { text: "Na " + repo.mainBranch + " są niezacommitowane zmiany — nowy worktree ich nie zawiera",
                 warn: true }
    }
    return { text: "od " + repo.mainBranch + " · " + repo.mainBranch + " zostaje nietknięty", warn: false }
}

// Linia nad przyciskami: co się stanie po „Uruchom agenta”.
function outcome(repo, tool, newWorktree) {
    var label = tool === "codex" ? "Codexem" : "Claude Code"
    if (!repo) return ""
    return newWorktree ? "Otworzy się nowa karta Konsoli z " + label + " w tym worktree."
                       : "Otworzy się nowa karta Konsoli z " + label + " w " + repo.name + "."
}

// „238 znaków”, „1 znak”, „3 znaki”.
function charCount(text) {
    var n = (text || "").length
    var ones = n % 10, tens = n % 100
    var word = n === 1 ? "znak" : (ones >= 2 && ones <= 4 && !(tens >= 12 && tens <= 14)) ? "znaki" : "znaków"
    return n + " " + word
}

// „zmiany 12 min temu” przy wybranym repozytorium.
function changesAgo(ts, nowTs) {
    if (!ts) return ""
    var s = Math.max(0, nowTs - ts)
    var ago
    if (s < 60) ago = "przed chwilą"
    else if (s < 3600) ago = Math.floor(s / 60) + " min temu"
    else if (s < 86400) ago = Math.floor(s / 3600) + " h temu"
    else if (s < 2 * 86400) ago = "wczoraj"
    else ago = Math.floor(s / 86400) + " dni temu"
    return "zmiany " + ago
}

// Katalog worktree dla wpisanej ręcznie gałęzi (jak w usłudze: bez prefiksu narzędzia).
function pathForBranch(repo, branch) {
    if (!repo) return ""
    var parts = (branch || "").split("/")
    var name = (parts.length > 1 && (parts[0] === "claude" || parts[0] === "codex")) ? parts.slice(1).join("/") : branch
    name = (name || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "zadanie"
    return repo.worktreeDir + "/" + name
}

function request(state) {
    return JSON.stringify({
        repoPath: state.repo ? state.repo.path : "",
        tool: state.tool,
        prompt: state.prompt,
        newWorktree: state.newWorktree,
        branch: state.newWorktree ? (state.branch || "").trim() : "",
        images: state.images || []
    })
}

function imageName(path) {
    var parts = (path || "").split("/")
    return parts[parts.length - 1]
}
