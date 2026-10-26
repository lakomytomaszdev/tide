// Logika okna Worktrees bez wyglądu: filtry, grupy, liczniki i teksty po polsku.
// Dane to lista z tide-daemon (List() na /Worktrees, opis pól w docs/tide-daemon.md).
.pragma library

var DAY = 86400
var DECIDE_PREVIEW = 10      // ile pozycji „Do decyzji” pokazujemy przed „pokaż wszystkie”
var OLD_AFTER = 30 * DAY     // starsze są wyciszone
var MAX_CHIPS = 5            // tyle repozytoriów ma własny filtr, reszta to „pozostałe”

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

// Worktrees bez głównych katalogów repozytoriów — to je liczymy i pokazujemy w grupach.
function linked(items) {
    return items.filter(function (w) { return !w.isMain })
}

function bytesOf(items) {
    return items.reduce(function (sum, w) { return sum + Math.max(0, w.size || 0) }, 0)
}

function byActivity(a, b) {
    return (b.lastActivityTs || 0) - (a.lastActivityTs || 0)
}

function inGroup(items, group) {
    return items.filter(function (w) { return w.group === group }).sort(byActivity)
}

function summary(items) {
    var list = linked(items)
    var repos = {}
    list.forEach(function (w) { repos[w.repoPath] = true })
    var clean = list.filter(function (w) { return w.group === "clean" })
    return {
        count: list.length,
        repos: Object.keys(repos).length,
        bytes: bytesOf(list),
        cleanCount: clean.length,
        cleanBytes: bytesOf(clean),
        activeCount: list.filter(function (w) { return w.group === "active" }).length,
        sizesPending: list.some(function (w) { return w.size < 0 && !w.missing })
    }
}

// Filtry repozytoriów: „Wszystkie”, do MAX_CHIPS największych i „pozostałe”.
function repoChips(items) {
    var list = linked(items)
    var counts = {}
    var names = {}
    list.forEach(function (w) {
        counts[w.repoPath] = (counts[w.repoPath] || 0) + 1
        names[w.repoPath] = w.repo
    })
    var paths = Object.keys(counts).sort(function (a, b) {
        return counts[b] - counts[a] || names[a].toLowerCase().localeCompare(names[b].toLowerCase())
    })
    var chips = [{ id: "all", label: "Wszystkie", count: list.length }]
    paths.slice(0, MAX_CHIPS).forEach(function (p) { chips.push({ id: p, label: names[p], count: counts[p] }) })
    var rest = paths.slice(MAX_CHIPS)
    if (rest.length > 0) {
        chips.push({ id: "other", label: "pozostałe", count: rest.reduce(function (s, p) { return s + counts[p] }, 0),
                     paths: rest })
    }
    return chips
}

function inRepo(w, repo, chips) {
    if (!repo || repo === "all") return true
    if (repo === "other") {
        var other = chips.filter(function (c) { return c.id === "other" })[0]
        return !!other && other.paths.indexOf(w.repoPath) >= 0
    }
    return w.repoPath === repo
}

// Szukanie: gałąź, tytuł, temat commita, tytuł sesji, repozytorium, katalog.
function matches(w, query) {
    var q = (query || "").trim().toLowerCase()
    if (q === "") return true
    var fields = [w.title, w.branch, w.subject, w.sessionTitle, w.repo, w.name, w.tag]
    return q.split(/\s+/).every(function (word) {
        return fields.some(function (f) { return (f || "").toLowerCase().indexOf(word) >= 0 })
    })
}

function filtered(items, query, repo, chips) {
    return items.filter(function (w) { return inRepo(w, repo, chips) && matches(w, query) })
}

// Druga linia wiersza: tytuł powiązanej sesji, inaczej temat ostatniego commita.
function detail(w) {
    if (w.sessionTitle) return w.sessionTitle
    if (w.subject) return w.subject
    return w.detached ? "odłączony HEAD " + w.head : ""
}

function toolLabel(tool) {
    return tool === "codex" ? "Codex" : tool === "claude" ? "Claude" : "—"
}

// Dlaczego worktree jest „do decyzji”: „4 commity · 3 zmiany”.
function whyText(w) {
    var parts = []
    if ((w.unmerged || 0) > 0) parts.push(count(w.unmerged, "commit", "commity", "commitów"))
    if (w.dirty > 0) parts.push(count(w.dirty, "zmiana", "zmiany", "zmian"))
    if (w.dirty < 0) parts.push("stan nieznany")
    if (w.locked) parts.push("zablokowany")
    if (!w.mainBranch) parts.push("brak gałęzi głównej")
    return parts.join(" · ")
}

function isOld(w, nowTs) {
    return (w.lastActivityTs || 0) > 0 && nowTs - w.lastActivityTs > OLD_AFTER
}

// Zaznaczenie: tylko worktrees z grupy „Do sprzątnięcia” (niezależnie od filtrów, żeby nic
// ukrytego filtrem nie zaskoczyło przy usuwaniu — potwierdzenie i tak wymienia wszystkie).
function selection(cleanItems, selected) {
    var chosen = cleanItems.filter(function (w) { return selected[w.path] === true })
    return { items: chosen, bytes: bytesOf(chosen) }
}

function tildePath(path, home) {
    if (home && (path === home || path.indexOf(home + "/") === 0)) return "~" + path.slice(home.length)
    return path
}

// Co stanie się z gałęzią, w ludzkich słowach (do potwierdzenia).
function branchFate(w) {
    if (!w.branch) return "bez gałęzi (odłączony HEAD " + w.head + ") — zostaje tylko katalog do usunięcia"
    return "gałąź " + w.branch + " zniknie, jeśli git potwierdzi scalenie (git branch -d); inaczej zostaje"
}

// Brakujące katalogi pogrupowane według repozytoriów.
function missingByRepo(items) {
    var out = []
    items.filter(function (w) { return w.group === "missing" }).forEach(function (w) {
        var entry = out.filter(function (e) { return e.repoPath === w.repoPath })[0]
        if (!entry) {
            entry = { repo: w.repo, repoPath: w.repoPath, count: 0 }
            out.push(entry)
        }
        entry.count++
    })
    return out
}

// Podsumowanie wyniku Remove(): co usunięto, co pominięto i dlaczego.
function resultText(results, titles) {
    var removed = results.filter(function (r) { return r.removed })
    var skipped = results.filter(function (r) { return !r.removed })
    var lines = []
    skipped.forEach(function (r) { lines.push((titles[r.path] || r.path) + ": " + r.message) })
    removed.filter(function (r) { return r.branch && !r.branchDeleted }).forEach(function (r) {
        lines.push((titles[r.path] || r.path) + ": " + r.branchMessage)
    })
    return {
        removed: removed.length,
        skipped: skipped.length,
        freed: removed.reduce(function (s, r) { return s + (r.freed || 0) }, 0),
        details: lines
    }
}
