// Logika widżetu System bez QML: wybór pełniejszej partycji, podpowiedź o worktrees, podpisy.
// Czyste funkcje — testy wołają je w qml6 bez Plasmy (widgets/tests/test_widget_system.py).
.pragma library

var HINT_THRESHOLD = 85   // procent zajętości partycji, od którego pokazujemy podpowiedź

// Liczba albo null (czujnik bez danych, brak pola w JSON).
function number(value) {
    return typeof value === "number" && isFinite(value) ? value : null
}

// Procent → ułamek 0–1 dla pierścieni i mierników; null zostaje null.
function fraction(percent) {
    var value = number(percent)
    return value === null ? null : Math.max(0, Math.min(1, value / 100))
}

function percentText(percent) {
    var value = number(percent)
    return value === null ? "—" : Math.round(value) + "%"
}

// Partycja z największą zajętością (na kafelek „Dysk”); null bez danych.
function fullerDisk(disks) {
    var best = null
    for (var i = 0; i < (disks || []).length; i++) {
        var disk = disks[i]
        if (number(disk.usedPercent) === null) continue
        if (best === null || disk.usedPercent > best.usedPercent) best = disk
    }
    return best
}

// Punkt montowania, na którym leży ścieżka: najdłuższy pasujący prefiks z listy targets.
function mountFor(path, targets) {
    var best = null
    for (var i = 0; i < (targets || []).length; i++) {
        var target = targets[i]
        var prefix = target === "/" ? "/" : target.replace(/\/+$/, "") + "/"
        var inside = path === target || path.indexOf(prefix) === 0
        if (inside && (best === null || target.length > best.length)) best = target
    }
    return best
}

// Worktrees (lista z tide-daemon /Worktrees) leżące na danej partycji, bez głównych katalogów
// repozytoriów: ile zajmują i ile z tego zwolni sprzątanie (removable). Rozmiar -1 = jeszcze
// niepoliczony — pomijamy go w sumach.
function worktreeUsage(worktrees, target, targets) {
    var usage = { count: 0, total: 0, freeable: 0 }
    for (var i = 0; i < (worktrees || []).length; i++) {
        var tree = worktrees[i]
        if (!tree || tree.isMain || typeof tree.path !== "string") continue
        if (mountFor(tree.path, targets) !== target) continue
        usage.count++
        var size = number(tree.size)
        if (size === null || size < 0) continue
        usage.total += size
        if (tree.removable === true) usage.freeable += size
    }
    return usage
}

// Podpowiedź pod partycją zajętą powyżej progu. worktrees === null oznacza, że tide-daemon
// nie wystawia obszaru /Worktrees (albo nie działa) — wtedy tylko krótkie ostrzeżenie.
function diskHint(disk, worktrees, targets, formatBytes) {
    if (!disk || number(disk.usedPercent) === null || disk.usedPercent <= HINT_THRESHOLD) return ""
    if (!Array.isArray(worktrees)) return "Dysk prawie pełny"
    var usage = worktreeUsage(worktrees, disk.target, targets)
    if (usage.total <= 0) return "Dysk prawie pełny"
    return "Powyżej " + HINT_THRESHOLD + "%: worktrees zajmują " + formatBytes(usage.total)
        + ", z czego " + formatBytes(usage.freeable) + " można zwolnić"
}

// „najwięcej: dotnet 3,2 GiB · node 1,1 GiB · firefox 1,0 GiB”; pusty napis bez danych.
function processesLine(processes, formatBytes) {
    var parts = []
    for (var i = 0; i < (processes || []).length; i++) {
        var item = processes[i]
        if (!item || number(item.bytes) === null) continue
        parts.push(item.name + " " + formatBytes(item.bytes))
    }
    return parts.length ? "najwięcej: " + parts.join(" · ") : ""
}

function historyCaption(average, peak, seconds) {
    var span = seconds < 60 ? "ostatnie " + Math.round(seconds) + " s"
        : seconds < 120 ? "ostatnia minuta"
        : "ostatnie " + Math.round(seconds / 60) + " min"
    if (number(average) === null) return span + " · zbieram dane"
    return span + " · średnio " + percentText(average) + " · szczyt " + percentText(peak)
}

// Ścieżka lokalna z adresu file:// (Qt.resolvedUrl) i bezpieczne cytowanie dla powłoki.
function localPath(url) {
    return decodeURIComponent(String(url).replace(/^file:\/\//, ""))
}

function shellQuote(text) {
    return "'" + String(text).replace(/'/g, "'\\''") + "'"
}

// JSON z wyjścia pomocnika; null przy błędzie albo pustym wyjściu.
function parseOutput(data) {
    if (!data || data["exit code"] !== 0) return null
    try {
        return JSON.parse(data.stdout)
    } catch (e) {
        return null
    }
}
