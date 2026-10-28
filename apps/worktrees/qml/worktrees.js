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
    if (w.blocker === "nested" || w.forceBlocker === "nested") parts.push("zawiera inne worktrees")
    if (!w.mainBranch) parts.push("brak gałęzi głównej")
    return parts.join(" · ")
}

function isOld(w, nowTs) {
    return (w.lastActivityTs || 0) > 0 && nowTs - w.lastActivityTs > OLD_AFTER
}

// Czy worktree da się zaznaczyć do usunięcia. Sugerowane (removable) zawsze; pozostałe tylko wtedy,
// gdy usługa mówi, że nie mają twardej blokady (forceRemovable: nie główny katalog, nie gałąź
// główna, nie brakujący, nie zablokowany, bez działającego agenta i bez innych worktrees w środku). Usługa sprawdza to i tak sama —
// tu tylko nie pokazujemy pola wyboru tam, gdzie i tak by odmówiła. Starsza usługa bez pola
// forceRemovable — jak dawniej, tylko sugerowane.
function selectable(w) {
    if (!w || w.isMain || w.missing || w.group === "main") return false
    return w.removable === true || w.forceRemovable === true
}

// Zaznaczony nie-sugerowany: usunięcie wymaga mocniejszego potwierdzenia i RemoveForced.
function risky(w) {
    return selectable(w) && w.removable !== true
}

// Dlaczego wiersza nie da się zaznaczyć (podpowiedź); pusty napis, gdy się da.
function lockReason(w) {
    if (selectable(w)) return ""
    var reason = w.forceBlocker || w.blocker || (w.isMain ? "main" : w.group === "main" ? "protected" : "")
    var texts = {
        main: "główny katalog repozytorium — nigdy go nie usuwamy",
        protected: "gałąź główna (master, main, develop) — nigdy jej nie usuwamy",
        missing: "katalogu już nie ma — porządkuje go git worktree prune",
        locked: "zablokowany (git worktree lock) — najpierw git worktree unlock",
        agent: "pracuje w nim agent — zamknij sesję, żeby móc go usunąć",
        nested: "zawiera inne worktrees — najpierw usuń te w środku, inaczej zniknęłyby razem z nim"
    }
    return texts[reason] || ""
}

// Zaznaczenie spośród wszystkich worktrees (niezależnie od filtrów, żeby nic ukrytego filtrem
// nie zaskoczyło przy usuwaniu — potwierdzenie i tak wymienia wszystkie). risky: nie-sugerowane.
function selection(items, selected) {
    var chosen = items.filter(function (w) { return selectable(w) && selected[w.path] === true })
    return { items: chosen, bytes: bytesOf(chosen), risky: chosen.filter(risky) }
}

// Stan widziany w potwierdzeniu — usługa porówna go ze świeżym i nic nie usunie, jeśli od tej
// chwili przybyło zmian albo commitów (RemoveForced, argument confirmed).
function confirmedState(items) {
    var out = {}
    items.forEach(function (w) {
        out[w.path] = { dirty: w.dirty, unmerged: w.unmerged, unpushed: w.unpushed || 0, branch: w.branch || "" }
    })
    return out
}

// Gałęzie, które przepadną tylko z polem „usuń też gałąź”: nie-sugerowane z gałęzią, które nie są
// scalone (albo nie wiadomo, bo brak gałęzi głównej). commits: suma znanych niescalonych commitów.
function unmergedBranches(items) {
    var list = items.filter(function (w) { return risky(w) && w.branch && w.unmerged !== 0 })
    return {
        count: list.length,
        commits: list.reduce(function (s, w) { return s + Math.max(0, w.unmerged || 0) }, 0),
        unknown: list.filter(function (w) { return w.unmerged === null || w.unmerged === undefined }).length
    }
}

// Etykieta pola wyboru w potwierdzeniu: „Usuń też gałąź (4 niescalone commity przepadną)”.
function deleteBranchLabel(info) {
    var head = info.count === 1 ? "Usuń też gałąź" : "Usuń też gałęzie (" + info.count + ")"
    var parts = []
    if (info.commits > 0) {
        parts.push(count(info.commits, "niescalony commit", "niescalone commity", "niescalonych commitów") + " "
                   + plural(info.commits, "przepadnie", "przepadną", "przepadnie"))
    }
    if (info.unknown > 0) {
        parts.push((info.unknown === 1 && info.count === 1 ? "" : "w " + info.unknown + " ")
                   + "bez gałęzi głównej nie wiadomo, ile commitów przepadnie")
    }
    return head + " (" + parts.join("; ") + ")"
}

// Co przepadnie przy wymuszonym usunięciu — linie dla potwierdzenia. danger: przepada bez kopii.
// ago: gotowy tekst „3 godz. temu” dla ostatniej zmiany (liczy go QML, Tide.Theme.agoText).
function lossLines(w, deleteBranches, ago) {
    var lines = []
    if (!risky(w)) {
        lines.push({ text: "bez ryzyka — scalony z " + (w.mainBranch || "gałęzią główną") + ", bez zmian", danger: false })
        lines.push({ text: branchFate(w), danger: false })
        return lines
    }
    if (w.dirty > 0) {
        lines.push({ text: count(w.dirty, "niezacommitowana zmiana", "niezacommitowane zmiany", "niezacommitowanych zmian")
                           + " " + plural(w.dirty, "przepadnie", "przepadną", "przepadnie")
                           + " (także pliki nieśledzone)", danger: true })
    } else if (w.dirty < 0) {
        lines.push({ text: "stanu plików nie dało się odczytać — wszystko niezacommitowane przepadnie", danger: true })
    }
    var unpushed = w.unpushed || 0
    var onlyHere = unpushed > 0 ? ", z tego " + unpushed + " bez kopii na serwerze ani w innej gałęzi"
                 : unpushed < 0 ? ", nie wiadomo, ile ma kopię na serwerze"
                 : (w.unmerged || 0) > 0 ? ", wszystkie mają kopię na serwerze albo w innej gałęzi" : ""
    if ((w.unmerged || 0) > 0) {
        lines.push({ text: count(w.unmerged, "commit", "commity", "commitów") + " poza " + w.mainBranch + onlyHere,
                     danger: unpushed !== 0 && (!w.branch || deleteBranches) })
    } else if (w.unmerged === null || w.unmerged === undefined) {
        lines.push({ text: "brak lokalnej gałęzi głównej — nie wiadomo, co jest scalone"
                           + (unpushed > 0 ? "; " + count(unpushed, "commit", "commity", "commitów") + " tylko tutaj" : ""),
                     danger: false })
    }
    if (!w.branch) {
        if ((w.unmerged || 0) > 0 || unpushed > 0) {
            lines.push({ text: "odłączony HEAD " + w.head + ": te commity zostaną bez gałęzi (odzyskasz je tylko z refloga)",
                         danger: true })
        }
    } else if (w.unmerged === 0) {
        lines.push({ text: "gałąź " + w.branch + " jest scalona — zniknie (git branch -d)", danger: false })
    } else if (deleteBranches) {
        lines.push({ text: "gałąź " + w.branch + " zostanie usunięta (git branch -D)", danger: true })
    } else {
        lines.push({ text: "gałąź " + w.branch + " zostaje (usuniesz ją polem niżej)", danger: false })
    }
    if (w.sessionTitle) {
        lines.push({ text: "sesja " + toolLabel(w.tool) + " „" + w.sessionTitle + "” — agent już w nim nie działa, "
                           + "rozmowa zostaje w historii", danger: false })
    }
    if (ago) lines.push({ text: "ostatnia zmiana " + ago, danger: false })
    return lines
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

// Podsumowanie wyniku Remove() / RemoveForced(): co usunięto, przy których została gałąź, czego
// usługa odmówiła i dlaczego.
function resultText(results, titles) {
    var removed = results.filter(function (r) { return r.removed })
    var skipped = results.filter(function (r) { return !r.removed })
    var kept = removed.filter(function (r) { return r.branch && !r.branchDeleted })
    var lines = []
    skipped.forEach(function (r) { lines.push((titles[r.path] || r.path) + ": " + r.message) })
    kept.forEach(function (r) {
        lines.push((titles[r.path] || r.path) + ": " + (r.forced ? "usunięty, " : "") + r.branchMessage)
    })
    return {
        removed: removed.length,
        skipped: skipped.length,
        branchKept: kept.length,
        freed: removed.reduce(function (s, r) { return s + (r.freed || 0) }, 0),
        details: lines
    }
}
