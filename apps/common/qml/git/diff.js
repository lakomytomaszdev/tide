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

// Kontekst, który usługa dokłada domyślnie (`options.context` bez wartości), i jej górna granica.
var DEFAULT_CONTEXT = 3
var MAX_CONTEXT = 100000

// Pierwsza linia hunka po stronie nowej; hunk bez linii nowych (same usunięcia) zaczyna się „po” linii `newStart`.
function newFirst(hunk) {
    return hunk.newLines > 0 ? hunk.newStart : hunk.newStart + 1
}

// Ile niezmienionych linii ukrywa przerwa przed hunkiem `h` (przed pierwszym — od początku pliku).
function hiddenBefore(hunks, h) {
    const from = h === 0 ? 1 : newFirst(hunks[h - 1]) + hunks[h - 1].newLines
    return Math.max(0, newFirst(hunks[h]) - from)
}

// „… 42 niezmienione linie” — podpis zwiniętego fragmentu.
function gapText(n) {
    return "… " + n + " " + plural(n, "niezmieniona", "niezmienione", "niezmienionych") + " "
           + plural(n, "linia", "linie", "linii")
}

// Wiersze jednego pliku: nagłówek hunka (`kind: "hunk"`), linie (`kind` " ", "+", "-") i zwinięte fragmenty między
// hunkami (`kind: "gap"`, `lines` — ile niezmienionych linii ukrywa; stoi tuż przed nagłówkiem następnego hunka).
// `hunk` to numer hunka w pliku — z niego bierze się nawigacja `n`/`p`.
function rows(file) {
    const out = []
    if (!file || !file.hunks) return out
    for (let h = 0; h < file.hunks.length; h++) {
        const hunk = file.hunks[h]
        const hidden = hiddenBefore(file.hunks, h)
        if (hidden > 0) out.push({kind: "gap", hunk: h, oldNo: "", newNo: "", lines: hidden, text: gapText(hidden)})
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

// Wiersze trybu „obok siebie” z wierszy jednolitych (liczone raz na plik). Nagłówki hunków i zwinięte fragmenty
// przechodzą bez zmian; kontekst staje po obu stronach; ciąg linii `-`/`+` paruje się kolejno (i-ta usunięta
// z i-tą dodaną), nadmiar ma pustą (`null`) drugą stronę. `src` to indeks wiersza jednolitego — po nim widok
// zachowuje miejsce przy przełączeniu trybu.
function sideRows(rowList) {
    const out = []
    let i = 0
    while (i < rowList.length) {
        const row = rowList[i]
        if (row.kind === "hunk" || row.kind === "gap") {
            out.push(Object.assign({}, row, {src: i}))
            i++
        } else if (row.kind === " ") {
            out.push({kind: "pair", hunk: row.hunk, src: i,
                      left: {kind: " ", no: row.oldNo, text: row.text},
                      right: {kind: " ", no: row.newNo, text: row.text}})
            i++
        } else {
            const removed = []
            const added = []
            while (i < rowList.length && (rowList[i].kind === "-" || rowList[i].kind === "+")) {
                (rowList[i].kind === "-" ? removed : added).push(i)
                i++
            }
            const count = Math.max(removed.length, added.length)
            for (let k = 0; k < count; k++) {
                const l = k < removed.length ? rowList[removed[k]] : null
                const r = k < added.length ? rowList[added[k]] : null
                out.push({kind: "pair", hunk: (l || r).hunk,
                          src: k < removed.length ? removed[k] : added[k],
                          left: l ? {kind: "-", no: l.oldNo, text: l.text} : null,
                          right: r ? {kind: "+", no: r.newNo, text: r.text} : null})
            }
        }
    }
    return out
}

// Indeks pierwszego wiersza trybu „obok siebie” o `src` nie mniejszym niż podany (miejsce po przełączeniu trybu).
function rowOfSrc(sideList, src) {
    for (let i = 0; i < sideList.length; i++) if (sideList[i].src >= src) return i
    return Math.max(0, sideList.length - 1)
}

// Największy zwinięty fragment pliku (0 — nic nie jest zwinięte).
function largestGap(rowList) {
    let best = 0
    for (const row of rowList) if (row.kind === "gap" && row.lines > best) best = row.lines
    return best
}

// `options.context` rozwijający fragment o `hidden` liniach: dotychczasowy kontekst plus cały fragment, więc hunki
// po obu jego stronach na pewno się scalą (usługa ogranicza wartość do MAX_CONTEXT).
function expandedContext(current, hidden) {
    return Math.min(MAX_CONTEXT, (current || DEFAULT_CONTEXT) + hidden)
}

// Indeksy wierszy, od których zaczynają się hunki.
function hunkStarts(rowList) {
    const starts = []
    for (let i = 0; i < rowList.length; i++) if (rowList[i].kind === "hunk") starts.push(i)
    return starts
}

// Indeks pliku o ścieżce (po `path`, a dla zmiany nazwy także po starej); -1, gdy go nie ma. Źródło z grupami
// (`worktree`, PT-360) może mieć ten sam plik w dwóch grupach — wtedy `group` wskazuje właściwy wpis.
function indexOfPath(files, path, group) {
    if (!path) return -1
    for (let i = 0; i < files.length; i++) {
        if (group && files[i].group !== group) continue
        if (files[i].path === path || files[i].oldPath === path) return i
    }
    return -1
}

// Wiersze listy plików (PT-360). Bez `groups[]` i `untracked[]` to same pliki — dokładnie jak dotąd. Źródło
// z grupami dokłada przed plikami każdej niepustej grupy nagłówek (`kind: "group"`: nazwa, liczba, sumy), a na końcu
// listę nieśledzonych plików (`kind: "untrackedHeader"` i `"untracked"`, bez treści). Wiersz pliku zna swój indeks
// w `data.files` (`fileIndex`), bo to on wybiera plik, nie pozycja na liście.
function listRows(data) {
    const files = data && Array.isArray(data.files) ? data.files : []
    const groups = data && Array.isArray(data.groups) ? data.groups : []
    const untracked = data && Array.isArray(data.untracked) ? data.untracked : []
    const rows = []
    if (groups.length === 0) {
        for (let i = 0; i < files.length; i++) rows.push({kind: "file", file: files[i], fileIndex: i})
    } else {
        for (const group of groups) {
            const members = []
            for (let i = 0; i < files.length; i++) if (files[i].group === group.id) members.push(i)
            if (members.length === 0) continue
            rows.push({kind: "group", id: group.id, name: group.name, count: members.length,
                       text: group.name + " · " + totalsText(group.totals)})
            for (const i of members) rows.push({kind: "file", file: files[i], fileIndex: i})
        }
    }
    if (untracked.length > 0) {
        rows.push({kind: "untrackedHeader", count: untracked.length,
                   text: "Nieśledzone · " + untracked.length + " " + plural(untracked.length, "plik", "pliki", "plików")
                         + " (bez treści)"})
        for (const path of untracked) rows.push({kind: "untracked", path: path})
    }
    return rows
}

// Indeks wiersza listy (`listRows`) pliku o indeksie `fileIndex` w `data.files`; -1, gdy go nie ma.
function rowOfFile(rows, fileIndex) {
    for (let i = 0; i < rows.length; i++) if (rows[i].kind === "file" && rows[i].fileIndex === fileIndex) return i
    return -1
}

function plural(n, one, few, many) {
    return n === 1 ? one : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many)
}

// Opis sumy: „3 pliki · +12 −4”.
function totalsText(totals) {
    if (!totals) return ""
    const n = totals.files
    return n + " " + plural(n, "plik", "pliki", "plików") + " · +" + totals.additions + " −" + totals.deletions
}

// Duże diffy (PT-355). Usługa dokłada pola tylko, gdy dotyczą pliku: `large` + `lines` (bez hunków), `truncated`
// (treść poza limitem całości), `oldSize`/`newSize` (binarny). Doczytany plik dostaje `loaded: true`.
var TRUNCATED_BAR ="Pokazano część zmian; wybierz plik, żeby doczytać"

function isLarge(file) {
    return !!file && file.large === true && file.loaded !== true && !(file.hunks && file.hunks.length)
}

// Plik bez treści tylko z powodu limitu całości — wybór pliku doczytuje go przez `options.path`.
function needsLoad(file) {
    return !!file && file.truncated === true && file.loaded !== true && !file.binary
        && !(file.hunks && file.hunks.length)
}

function largeText(file) {
    return "duży plik — " + file.lines + " linii, pokaż"
}

// 30 → „30 B”, 1536 → „1,5 KB”, 3 MB → „3,0 MB”; brak strony (null) → „—”.
function sizeText(bytes) {
    if (bytes === null || bytes === undefined) return "—"
    if (bytes < 1024) return bytes + " B"
    const units = ["KB", "MB", "GB"]
    let value = bytes / 1024
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++ }
    return value.toFixed(1).replace(".", ",") + " " + units[unit]
}

// Bez rozmiarów w danych (`oldSize`/`newSize` nieobecne) zostaje dawny komunikat bez liczb.
function binaryText(file) {
    if (file.oldSize === undefined && file.newSize === undefined) return "Plik binarny — bez podglądu zmian."
    return "plik binarny (" + sizeText(file.oldSize) + " → " + sizeText(file.newSize) + ")"
}

// `DiffData` z plikiem `file` (odpowiedź `options.path`) wstawionym w miejsce wpisu o tej samej ścieżce.
function mergeFile(data, file) {
    const files = data.files.map(f => f.path === file.path && f.group === file.group
                                      ? Object.assign({}, file, {loaded: true}) : f)
    return Object.assign({}, data, {files: files})
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
    case "no-worktree": return said("Katalog worktree nie istnieje") + " Odśwież listę Worktrees (r)."
    case "timeout": return "Git nie odpowiedział na czas. Spróbuj ponownie (r)."
    case "no-service": return "Usługa tide-daemon nie odpowiada. Spróbuj za chwilę (r)."
    default: return said("Nie udało się odczytać zmian") + " Spróbuj ponownie (r)."
    }
}
