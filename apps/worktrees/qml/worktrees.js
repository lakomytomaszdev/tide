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

// Stan względem zdalnej i głównej (PT-352): „↑3 ↓2 · origin/master” albo „tylko lokalnie”.
function arrows(ahead, behind) {
    return "↑" + ahead + " ↓" + behind
}

function upstreamLabel(w) {
    if (!w || !w.branch) return ""
    if (!w.upstream) return "tylko lokalnie"
    if (w.upstreamAhead === null || w.upstreamAhead === undefined) return w.upstream + " (brak odniesienia)"
    return arrows(w.upstreamAhead, w.upstreamBehind) + " · " + w.upstream
}

// „↑5 ↓1 względem master”; pusty dla samej gałęzi głównej i gdy nie ma głównej.
function mainLabel(w) {
    if (!w || !w.branch || !w.mainBranch || w.branch === w.mainBranch) return ""
    if (w.mainAhead === null || w.mainAhead === undefined) return ""
    return arrows(w.mainAhead, w.mainBehind) + " względem " + w.mainBranch
}

function syncLabel(w) {
    return [upstreamLabel(w), mainLabel(w)].filter(function (s) { return s !== "" }).join(" · ")
}

function agoText(seconds) {
    if (seconds < 60) return "przed chwilą"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60) return minutes + " min temu"
    var hours = Math.floor(minutes / 60)
    if (hours < 48) return hours + " h temu"
    return Math.floor(hours / 24) + " dni temu"
}

// Repozytorium: lista zdalnych i czas ostatniego fetch („origin · pobrano 2 h temu”).
function repoLine(w, nowTs) {
    if (!w) return ""
    if (!w.remotes || w.remotes.length === 0) return "bez zdalnego"
    var fetched = w.lastFetch ? Date.parse(w.lastFetch) / 1000 : NaN
    return w.remotes.join(", ") + " · "
           + (isNaN(fetched) ? "nigdy nie pobrano" : "pobrano " + agoText(Math.max(0, nowTs - fetched)))
}

// Repozytoria do paska „Pobierz” (PT-358): po jednym wierszu na repozytorium z bieżącego filtra,
// alfabetycznie po nazwie; canFetch tylko dla repozytorium ze zdalnym.
function repoRows(items, repo, chips) {
    var seen = {}
    var rows = []
    items.forEach(function (w) {
        if (seen[w.repoPath] || !inRepo(w, repo, chips)) return
        seen[w.repoPath] = true
        // Główny katalog repozytorium (PT-353: „Zaciągnij” gałęzi głównej przy pasku — sam główny katalog
        // nie ma wiersza na liście).
        var main = items.filter(function (x) { return x.repoPath === w.repoPath && x.isMain })[0] || null
        rows.push({ repoPath: w.repoPath, repo: w.repo, remotes: w.remotes || [], lastFetch: w.lastFetch || "",
                    canFetch: !!w.remotes && w.remotes.length > 0, sample: w, main: main })
    })
    return rows.sort(function (a, b) { return a.repo.toLowerCase().localeCompare(b.repo.toLowerCase()) })
}

function fetchButtonText(busy) {
    return busy ? "Pobieram…" : "Pobierz"
}

// Wynik Fetch() przy repozytorium: jedno zdanie z usługi albo komunikat o braku odpowiedzi.
function fetchNote(result) {
    if (!result || typeof result.message !== "string" || result.message === "")
        return { ok: false, text: "Nie udało się pobrać: brak odpowiedzi usługi" }
    return { ok: result.ok === true, text: result.ok === true ? result.message : "Nie udało się pobrać — " + result.message }
}

// „Zaciągnij” (PT-353): przycisk przy worktree, którego gałąź ma upstream (także główny katalog — w pasku
// repozytoriów). Usługa sprawdza stan na nowo pod blokadą; tu tylko wyłączamy przycisk z powodem z danych listy.
function canOfferPull(w) {
    return !!w && !w.missing && !!w.branch && !!w.upstream
}

// Powód wyłączenia „Zaciągnij” z danych listy ({code, text}, kody jak w usłudze) albo null, gdy wolno.
function pullBlocker(w) {
    if (!w || w.missing) return { code: "bad-request", text: "katalogu nie ma" }
    if (w.hasCard) return { code: "agent", text: "trwa krok karty" }
    if (w.hasAgent) return { code: "agent", text: "pracuje agent" }
    if (w.flowBranch) return { code: "card-step", text: "tą gałęzią zarządza Tide" }
    if (!w.branch || !w.upstream || w.upstreamBehind === null || w.upstreamBehind === undefined)
        return { code: "no-upstream", text: "brak upstreamu" }
    if (w.dirty !== 0) return { code: "dirty", text: "zmiany w katalogu" }
    if ((w.upstreamAhead || 0) > 0 && w.upstreamBehind > 0)
        return { code: "diverged", text: "rozjechana z " + w.upstream + " — scal albo zrób rebase sam" }
    if (w.upstreamBehind === 0) return { code: "up-to-date", text: "gałąź aktualna" }
    return null
}

function pullButtonText(busy, w) {
    if (busy) return "Zaciągam…"
    return w && w.upstreamBehind > 0 ? "Zaciągnij ↓" + w.upstreamBehind : "Zaciągnij"
}

// Wynik PullFastForward(): jedno zdanie z usługi albo komunikat o braku odpowiedzi.
function pullNote(result) {
    if (!result || typeof result.message !== "string" || result.message === "")
        return { ok: false, text: "Nie udało się zaciągnąć: brak odpowiedzi usługi" }
    return { ok: result.ok === true, text: result.ok === true ? result.message : "Nie zaciągnięto — " + result.message }
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

// Zmiany worktree w widoku diffa (K-36, PT-360). „Zmiany w katalogu” ma worktree z niezacommitowaną pracą
// (`dirty` ≠ 0; −1 to stan nieznany, więc też — widok pokaże, co git powie), „Zmiany względem głównej” — worktree
// z gałęzią inną niż lokalna główna projektu. Brakujący katalog nie ma żadnej z akcji.
function canShowChanges(w) {
    return !!w && !w.missing && w.dirty !== 0 && w.dirty !== undefined && w.dirty !== null
}

function canShowBranchChanges(w) {
    return !!w && !w.missing && !!w.branch && !!w.mainBranch && w.branch !== w.mainBranch && !!w.repoPath
}

// Żądanie `Git.Diff` dla widoku diffa albo null, gdy akcja nie dotyczy worktree. `worktree` czyta niezacommitowaną
// pracę katalogu (usługa sprawdza `path` na liście Worktrees), `range` — gałąź worktree od lokalnej głównej projektu.
function diffRequest(w, kind) {
    if (kind === "worktree") return canShowChanges(w) ? {source: "worktree", path: w.path} : null
    if (kind === "range") {
        return canShowBranchChanges(w)
            ? {source: "range", repo: w.repoPath, from: "refs/heads/" + w.mainBranch, to: "refs/heads/" + w.branch}
            : null
    }
    return null
}
