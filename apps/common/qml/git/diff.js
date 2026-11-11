// Logika widoku diffa (K-36, PT-354): z `DiffData` (odpowiedź `Git.Diff`) robi tytuł źródła, etykiety plików
// i płaską listę wierszy dla ListView. Niczego nie liczy poza układem — pliki, hunki i sumy przychodzą z usługi
// (daemon/tide_daemon/gitread/). Format „startu” daje wyłącznie start.js.
.pragma library
.import "start.js" as Start

// Nagłówek źródła. Karta bez targetRef: „K-N · zmiany od startu: <start> → <gałąź karty>”, z targetRef —
// „K-N · zmiany względem <ref> → <gałąź karty>”. Inne źródła dokładają przyszłe zadania; do tego czasu pokazujemy
// tytuł z usługi.
function header(data, request) {
    if (!data) return ""
    if (data.source === "card" && data.card) {
        const branch = Start.shortRef(data.to ? data.to.ref : "")
        const target = request && typeof request.targetRef === "string" ? request.targetRef : ""
        if (target) return data.card + " · zmiany względem " + Start.shortRef(target) + " → " + branch
        return data.card + " · zmiany od startu: " + Start.startLabel(data.from) + " → " + branch
    }
    return data.title || ""
}

// Litera statusu pokazywana przy pliku (A/M/D/R…) albo „bin” dla pliku binarnego.
function statusLetter(file) {
    return file.binary ? "bin" : (file.status || "M")
}

// Nazwa pliku w liście: dla zmiany nazwy „stara → nowa”.
function fileName(file) {
    if (file.oldPath && file.oldPath !== file.path) return file.oldPath + " → " + file.path
    return file.path
}

// Wiersze jednego pliku: nagłówek hunka (`kind: "hunk"`) i linie (`kind` " ", "+", "-"). `hunk` to numer
// hunka w pliku — z niego bierze się nawigacja `n`/`p`.
function rows(file) {
    const out = []
    if (!file || !file.hunks) return out
    for (let h = 0; h < file.hunks.length; h++) {
        const hunk = file.hunks[h]
        const head = "@@ -" + hunk.oldStart + "," + hunk.oldLines + " +" + hunk.newStart + "," + hunk.newLines
                   + " @@" + (hunk.header ? " " + hunk.header : "")
        out.push({kind: "hunk", hunk: h, oldNo: "", newNo: "", text: head})
        for (const line of hunk.lines) {
            out.push({kind: line.kind, hunk: h,
                      oldNo: line.oldNo === null || line.oldNo === undefined ? "" : String(line.oldNo),
                      newNo: line.newNo === null || line.newNo === undefined ? "" : String(line.newNo),
                      text: line.text})
        }
    }
    return out
}

// Indeksy wierszy, od których zaczynają się hunki.
function hunkStarts(rowList) {
    const starts = []
    for (let i = 0; i < rowList.length; i++) if (rowList[i].kind === "hunk") starts.push(i)
    return starts
}

// Indeks pliku o ścieżce (po `path`, a dla zmiany nazwy także po starej); -1, gdy go nie ma.
function indexOfPath(files, path) {
    if (!path) return -1
    for (let i = 0; i < files.length; i++) if (files[i].path === path || files[i].oldPath === path) return i
    return -1
}

// Opis sumy: „3 pliki · +12 −4”.
function totalsText(totals) {
    if (!totals) return ""
    const n = totals.files
    const noun = n === 1 ? "plik" : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "pliki" : "plików")
    return n + " " + noun + " · +" + totals.additions + " −" + totals.deletions
}

// Jedno zdanie dla błędu usługi i co dalej (kody z daemon/tide_daemon/gitread/diffdata.py i runnera).
function errorText(code, message) {
    const said = (fallback) => {
        const text = (message || fallback).trim()
        return /[.!?…]$/.test(text) ? text : text + "."
    }
    switch (code) {
    case "no-branch": return said("Karta nie ma gałęzi") + " Sprawdź, czy gałąź karty istnieje, i odśwież (r)."
    case "unknown-card": return said("Nie znam tej karty") + " Zamknij widok i otwórz go z karty, która ma dziennik."
    case "unknown-base": return said("Baza startu karty jest nieznana") + " Wybierz ref, z którym porównać."
    case "timeout": return "Git nie odpowiedział na czas. Spróbuj ponownie (r)."
    case "no-service": return "Usługa tide-daemon nie odpowiada. Spróbuj za chwilę (r)."
    default: return said("Nie udało się odczytać zmian") + " Spróbuj ponownie (r)."
    }
}
