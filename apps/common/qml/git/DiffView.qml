// Widok diffa na całe okno Tide (K-36, PT-354): nagłówek źródła, lista plików ze statusem i liczbami +/−, suma
// i diff jednolity wybranego pliku. Wspólny komponent nad `Git.Diff` (daemon/tide_daemon/gitread/diffdata.py)
// — gospodarz osadza go jako nakładkę (Tablica i pełna karta: #17, Worktrees: #29, okno scalania: K-99).
// Nie zna karty poza nagłówkiem źródła: żądanie, plik startowy i backend dostaje z zewnątrz.
//
// Umowa:
//  - `request` — żądanie `Git.Diff` (obiekt; dziś `{source: "card", card: "K-N", targetRef?}`),
//  - `initialPath` — plik, na którym widok ma się otworzyć (brak lub nieznany → pierwszy plik),
//  - `backend` — wstrzyknięty backend wywołań: `gitDiff(tag, żądanieJson)`, `listCardBranches(tag, idKarty,
//    żądanieJson)` i sygnał `replied(tag, odpowiedźJson)` (apps/board/backend.py); w testach atrapa,
//  - `closeRequested()` — Esc i „Zamknij”; gospodarz wraca tam, skąd użytkownik przyszedł,
//  - `reload()` — ponowny odczyt (przycisk „Odśwież” i `r`).
// Klawisze: `]`/`[` i Alt+↓/↑ — następny/poprzedni plik, `n`/`p` — następny/poprzedni fragment, `r` — odśwież,
// `u` — tryb jednolity/obok siebie (przełącznik w nagłówku; zapamiętuje go gospodarz: `backend.diffMode`,
// `backend.setDiffMode(tryb)`, sygnał `diffModeChanged`), `e` — rozwiń zwinięte niezmienione fragmenty pliku
// (kliknięcie rozwija jeden), Esc — zamknij (najpierw ściągawkę). Rozwinięcie to ponowne `Git.Diff` z `options.path` i większym
// `options.context` dla jednego pliku. Wszystkie teksty z gita (ścieżki, linie, nazwy gałęzi) to Text.PlainText;
// linie diffu to ListView (wirtualizacja), nie Repeater; pary „obok siebie” liczy diff.js raz na plik.
//
// Linie (PT-361): ↓/↑ i `j`/`k` przesuwają bieżącą linię, PgDn/PgUp o stronę, Shift+↓/↑ (i Shift+klik) rozszerzają
// zaznaczenie, Ctrl+C kopiuje zaznaczone linie bez numerów i znaczników `+`/`-`, `?` — ściągawka wszystkich klawiszy.
// Zaznaczenie to zakres indeksów wierszy aktywnej listy (`curRow`, `anchorRow`), a nie właściwość delegatów; w trybie
// obok siebie kopiuje się jedna strona (`selSide`: kliknięta albo ←/→). Menu pliku (prawy przycisk albo klawisz Menu):
// „Kopiuj ścieżkę” (dla zmiany nazwy — nowa) i „Otwórz w edytorze” — tylko dla źródła z katalogiem roboczym
// (`worktree`: `request.path`; `card`: `workDir` od gospodarza) i backendu z `open(pełnaŚcieżka, "rider")` oraz
// `launchersJson` (Worktrees); dla `commit` i `range` pozycji nie ma.
//
// Karta bez znanego startu (`unknown-base`): komunikat i wybór jawnego refu z `listCardBranches`; wybór ponawia
// `Git.Diff` z `targetRef`.
import QtQuick
import "../tide" as Tide
import "diff.js" as Diff

Rectangle {
    id: view

    property var request: ({})
    property string initialPath: ""
    property var backend: null
    property string workDir: ""   // katalog roboczy źródła `card` (worktree karty), jeśli gospodarz go zna

    signal closeRequested()

    property var diffData: null
    property bool loading: false
    property string errorCode: ""
    property string errorMessage: ""
    property string targetRef: ""
    property var branches: []
    property int fileIndex: -1
    property string selectedPath: ""
    property string selectedGroup: ""   // grupa wybranego pliku (źródło `worktree`); pusta bez grup
    property var lineRows: []          // wiersze jednolite wybranego pliku (diff.js, `rows`)
    property var sideRows: []          // te same wiersze sparowane „obok siebie” — liczone raz na plik, gdy trzeba
    property var hunkStarts: []
    property int currentHunk: -1
    // Tryb: "unified" albo "split". Wybór zapamiętuje gospodarz w ustawieniach okna (`backend.diffMode` i
    // `backend.setDiffMode`); backend bez nich daje tryb jednolity na czas życia widoku.
    property string mode: "unified"
    readonly property var activeRows: mode === "split" ? sideRows : lineRows
    readonly property Item activeList: mode === "split" ? sideLines : lines
    property var contextByFile: ({})   // plik → `options.context` ostatniego rozwinięcia (domyślnie DEFAULT_CONTEXT)
    property int loadContext: 0        // `options.context` trwającego doczytania; 0 — zwykłe doczytanie pliku
    // Bieżąca linia i zaznaczenie (PT-361): indeksy wierszy aktywnej listy. `anchorRow` < 0 — brak zaznaczenia.
    property int curRow: -1
    property int anchorRow: -1
    property string selSide: "right"   // tryb obok siebie: kopiowana strona, "left" (stara) albo "right" (nowa)
    readonly property int selFrom: anchorRow < 0 ? -1 : Math.min(anchorRow, curRow)
    readonly property int selTo: anchorRow < 0 ? -2 : Math.max(anchorRow, curRow)
    property bool helpOpen: false
    readonly property var keyHelp: Diff.KEY_HELP
    property var menuFile: null        // plik, którego menu jest otwarte (null — zamknięte)
    property real menuX: 0
    property real menuY: 0

    readonly property var files: diffData ? diffData.files || [] : []
    // Wiersze listy: pliki, a dla źródła z grupami także nagłówki grup i nieśledzone (diff.js, `listRows`).
    readonly property var listRows: Diff.listRows(diffData)
    readonly property bool hasEntries: listRows.length > 0
    readonly property var effectiveRequest: {
        const copy = Object.assign({}, request || {})
        if (targetRef) copy.targetRef = targetRef
        return copy
    }
    readonly property string headerText: Diff.header(diffData, effectiveRequest)
    readonly property var selectedFile: fileIndex >= 0 && fileIndex < files.length ? files[fileIndex] : null
    // Katalog roboczy źródła: `worktree` — katalog z żądania (lista Worktrees), `card` — `workDir` gospodarza;
    // `commit` i `range` nie mają katalogu roboczego.
    readonly property string editorDir: {
        const source = request ? request.source : ""
        if (source === "worktree") return typeof request.path === "string" ? request.path : ""
        if (source === "card") return workDir
        return ""
    }
    readonly property bool riderAvailable: {
        if (!backend || typeof backend.open !== "function" || backend.launchersJson === undefined) return false
        try { return JSON.parse(backend.launchersJson).rider === true } catch (e) { return false }
    }

    property string wantedPath: initialPath
    property string pendingTag: ""
    property string branchTag: ""
    property string fileTag: ""
    property string loadingPath: ""
    property string fileError: ""
    property int sequence: 0

    color: Tide.Tokens.backgroundWindow
    focus: true
    objectName: "diffView"

    function reload() {
        if (!backend) return
        sequence += 1
        pendingTag = "diff:" + sequence
        fileTag = ""
        loadingPath = ""
        loadContext = 0
        contextByFile = ({})
        fileError = ""
        loading = true
        backend.gitDiff(pendingTag, JSON.stringify(effectiveRequest))
    }

    // Doczytuje treść jednego pliku (duży, przycięty) przez `Git.Diff` z `options.path`; odpowiedź zastępuje
    // wpis pliku w `diffData`. Z `context` (> 0) to rozwinięcie zwiniętych fragmentów: ten sam plik z większym
    // `options.context`, a widok zostaje w tym samym miejscu.
    function loadFile(path, context) {
        if (!backend || !path) return
        sequence += 1
        fileTag = "file:" + sequence
        loadingPath = path
        loadContext = context || 0
        fileError = ""
        const options = selectedGroup ? {path: path, group: selectedGroup} : {path: path}
        if (loadContext > 0) options.context = loadContext
        backend.gitDiff(fileTag, JSON.stringify(Object.assign({}, effectiveRequest, {options: options})))
    }

    function onFileReply(reply) {
        const path = loadingPath
        const group = selectedGroup
        const context = loadContext
        fileTag = ""
        loadingPath = ""
        loadContext = 0
        if (!reply || !reply.ok || !reply.files || reply.files.length === 0) {
            fileError = Diff.errorText(reply ? reply.error : "failed", reply ? reply.message : "")
            return
        }
        if (context > 0 && !(reply.files[0].hunks && reply.files[0].hunks.length)) {
            fileError = "Nie udało się rozwinąć kontekstu tego pliku."
            return
        }
        diffData = Diff.mergeFile(diffData, reply.files[0])
        if (context > 0) {
            const copy = Object.assign({}, contextByFile)
            copy[fileKey(reply.files[0])] = context
            contextByFile = copy
        }
        if (selectedPath === path && selectedGroup === group) selectFile(Diff.indexOfPath(files, path, group), context > 0)
    }

    function fileKey(file) {
        return (file.group || "") + "\u0000" + file.path
    }

    // Rozwija jeden zwinięty fragment (klik) albo wszystkie fragmenty pliku (`e`; `hidden` = największy z nich).
    function expand(hidden) {
        const file = selectedFile
        if (!file || hidden <= 0 || loadingPath !== "") return
        loadFile(selectedPath, Diff.expandedContext(contextByFile[fileKey(file)], hidden))
    }

    function expandAll() {
        expand(Diff.largestGap(lineRows))
    }

    // Wiersze wybranego pliku w obecnym trybie; pary „obok siebie” powstają tylko, gdy ktoś ich potrzebuje.
    function rebuildRows() {
        lineRows = Diff.rows(selectedFile)
        sideRows = mode === "split" ? Diff.sideRows(lineRows) : []
        hunkStarts = Diff.hunkStarts(activeRows)
        anchorRow = -1
        curRow = activeRows.length ? Math.min(Math.max(0, curRow), activeRows.length - 1) : -1
    }

    // --- bieżąca linia, zaznaczenie i kopiowanie (PT-361) ---

    // Ile wierszy mieści się w oknie listy (PgDn/PgUp przesuwają o tyle, ale co najmniej o jeden).
    function pageRows() {
        const list = activeList
        if (list.count === 0) return 1
        const top = list.indexAt(0, list.contentY + 1)
        const bottom = list.indexAt(0, list.contentY + list.height - 1)
        if (top >= 0 && bottom > top) return bottom - top
        return Math.max(1, Math.floor(list.height / 22) - 1)
    }

    // Przesuwa bieżącą linię o `delta`; z `extend` rozszerza zaznaczenie, bez niego je zdejmuje.
    function moveCurrent(delta, extend) {
        const count = activeRows.length
        if (count === 0) return
        if (extend && anchorRow < 0) anchorRow = Math.max(0, curRow)
        else if (!extend) anchorRow = -1
        curRow = Math.max(0, Math.min(count - 1, (curRow < 0 ? 0 : curRow) + delta))
        activeList.positionViewAtIndex(curRow, ListView.Contain)
    }

    // Klik w linię zaznacza ją, Shift+klik rozszerza zaznaczenie od kotwicy.
    function selectRow(index, extend, side) {
        if (index < 0 || index >= activeRows.length) return
        if (extend && anchorRow < 0) anchorRow = Math.max(0, curRow)
        else if (!extend) anchorRow = index
        curRow = index
        if (side) selSide = side
        forceActiveFocus()
    }

    // Tekst zaznaczonych linii (bez zaznaczenia — bieżącej linii): sama treść, bez numerów i znaczników `+`/`-`.
    // Nagłówki hunków i zwinięte fragmenty nie wchodzą do tekstu. Tryb obok siebie: jedna strona.
    function selectionText() {
        const from = anchorRow < 0 ? curRow : selFrom
        const to = anchorRow < 0 ? curRow : selTo
        if (from < 0) return ""
        const pick = side => {
            const out = []
            for (let i = from; i <= to && i < activeRows.length; i++) {
                const row = activeRows[i]
                if (mode === "split") {
                    const half = row.kind === "pair" ? row[side] : null
                    if (half) out.push(half.text)
                } else if (row.kind === " " || row.kind === "+" || row.kind === "-") {
                    out.push(row.text)
                }
            }
            return out
        }
        let picked = pick(selSide)
        if (picked.length === 0 && mode === "split") picked = pick(selSide === "left" ? "right" : "left")
        return picked.join("\n")
    }

    function copyToClipboard(text) {
        clipboard.text = text
        clipboard.selectAll()
        clipboard.copy()
        clipboard.text = ""
    }

    function copySelection() {
        const text = selectionText()
        if (text !== "") copyToClipboard(text)
    }

    // --- menu pliku ---

    function openFileMenu(file, x, y) {
        if (!file) return
        menuFile = file
        menuX = x
        menuY = y
    }

    function closeFileMenu() {
        menuFile = null
    }

    function copyFilePath(file) {
        if (file) copyToClipboard(file.path)
        closeFileMenu()
    }

    // Pełna ścieżka pliku w katalogu roboczym źródła; pusta, gdy źródło go nie ma, plik jest usunięty albo ścieżka
    // z gita wychodziłaby poza katalog.
    function editorPath(file) {
        if (!file || file.status === "D" || editorDir === "" || !riderAvailable) return ""
        const path = file.path
        if (typeof path !== "string" || path === "" || path.charAt(0) === "/" || path.split("/").indexOf("..") >= 0)
            return ""
        return editorDir.replace(/\/+$/, "") + "/" + path
    }

    function openInEditor(file) {
        const full = editorPath(file)
        closeFileMenu()
        if (full !== "") backend.open(full, "rider")
    }

    // Indeks wiersza jednolitego u góry widocznego okna listy (do zachowania miejsca przy zmianie trybu).
    function topSource() {
        const list = activeList
        const at = list.count > 0 ? list.indexAt(0, list.contentY + 1) : -1
        if (at < 0) return 0
        const row = activeRows[at]
        return row && row.src !== undefined ? row.src : at
    }

    function setMode(value, persist) {
        if ((value !== "unified" && value !== "split") || value === mode) return
        const src = topSource()
        const here = curRow >= 0 ? activeRows[curRow] : null
        const curSrc = here && here.src !== undefined ? here.src : curRow
        mode = value
        if (value === "split" && sideRows.length === 0 && lineRows.length > 0) sideRows = Diff.sideRows(lineRows)
        hunkStarts = Diff.hunkStarts(activeRows)
        anchorRow = -1
        if (curSrc >= 0) curRow = value === "split" ? Diff.rowOfSrc(sideRows, curSrc)
                                                    : Math.min(curSrc, Math.max(0, lineRows.length - 1))
        const target = value === "split" ? Diff.rowOfSrc(sideRows, src) : Math.min(src, Math.max(0, lineRows.length - 1))
        if (lineRows.length > 0) activeList.positionViewAtIndex(target, ListView.Beginning)
        if (persist && backend && typeof backend.setDiffMode === "function") backend.setDiffMode(value)
    }

    function toggleMode() {
        setMode(mode === "split" ? "unified" : "split", true)
    }

    // Tryb zapamiętany przez gospodarza (przy otwarciu widoku i po zmianie w ustawieniach okna).
    function syncMode() {
        const saved = backend ? backend.diffMode : undefined
        if (saved === "unified" || saved === "split") setMode(saved)
    }

    function chooseTarget(ref) {
        targetRef = ref
        branches = []
        reload()
    }

    // `keepView` — ten sam plik z rozwiniętym kontekstem: widok zostaje na tym samym miejscu, a numer fragmentu
    // tylko mieści się w nowej liczbie hunków (po scaleniu jest ich mniej).
    function selectFile(index, keepView) {
        if (index < 0 || index >= files.length) {
            fileIndex = -1
            selectedPath = ""
            selectedGroup = ""
            lineRows = []
            sideRows = []
            hunkStarts = []
            currentHunk = -1
            curRow = -1
            anchorRow = -1
            return
        }
        const group = files[index].group || ""
        if (files[index].path !== selectedPath || group !== selectedGroup) fileError = ""
        const scrollY = activeList.contentY
        fileIndex = index
        selectedPath = files[index].path
        selectedGroup = group
        rebuildRows()
        if (keepView === true) {
            currentHunk = hunkStarts.length ? Math.min(Math.max(0, currentHunk), hunkStarts.length - 1) : -1
            Qt.callLater(() => { activeList.contentY = scrollY })
            return
        }
        currentHunk = hunkStarts.length ? 0 : -1
        curRow = activeRows.length ? 0 : -1
        anchorRow = -1
        activeList.positionViewAtBeginning()
        filesList.positionViewAtIndex(Math.max(0, Diff.rowOfFile(listRows, index)), ListView.Contain)
        // Plik bez treści z powodu limitu całości doczytuje się sam; `large` czeka na „pokaż”.
        if (Diff.needsLoad(files[index]) && loadingPath !== selectedPath) loadFile(selectedPath)
    }

    function step(delta) {
        if (!files.length) return
        selectFile(Math.max(0, Math.min(files.length - 1, (fileIndex < 0 ? 0 : fileIndex) + delta)))
    }

    function stepHunk(delta) {
        if (!hunkStarts.length) return
        currentHunk = Math.max(0, Math.min(hunkStarts.length - 1, currentHunk + delta))
        activeList.positionViewAtIndex(hunkStarts[currentHunk], ListView.Beginning)
        curRow = hunkStarts[currentHunk]
        anchorRow = -1
    }

    function applyData(reply) {
        errorCode = ""
        errorMessage = ""
        diffData = reply
        let index = Diff.indexOfPath(files, wantedPath)
        if (index < 0) index = Diff.indexOfPath(files, selectedPath, selectedGroup)
        if (index < 0 && files.length) index = 0
        wantedPath = ""
        selectFile(index)
    }

    function applyError(code, message) {
        diffData = null
        errorCode = code || "failed"
        errorMessage = message || ""
        selectFile(-1)
        if (errorCode === "unknown-base" && request && request.card && backend) {
            sequence += 1
            branchTag = "branches:" + sequence
            backend.listCardBranches(branchTag, request.card, JSON.stringify({purpose: "base", limit: 100}))
        }
    }

    function onReply(tag, text) {
        if (tag !== pendingTag && tag !== branchTag && tag !== fileTag) return
        let reply = null
        try { reply = JSON.parse(text) } catch (e) { reply = null }
        if (tag === fileTag) {
            onFileReply(reply)
            return
        }
        if (tag === branchTag) {
            branchTag = ""
            branches = reply && reply.ok && Array.isArray(reply.branches) ? reply.branches : []
            return
        }
        pendingTag = ""
        loading = false
        if (reply && reply.ok) applyData(reply)
        else applyError(reply ? reply.error : "failed", reply ? reply.message : "")
    }

    Connections {
        target: view.backend
        ignoreUnknownSignals: true
        function onReplied(tag, text) { view.onReply(tag, text) }
        function onDiffModeChanged() { view.syncMode() }
    }

    onRequestChanged: if (completed) { targetRef = ""; reload() }
    onBackendChanged: if (completed) { syncMode(); reload() }
    property bool completed: false
    Component.onCompleted: {
        completed = true
        forceActiveFocus()
        syncMode()
        reload()
    }

    Keys.onPressed: event => {
        const alt = (event.modifiers & Qt.AltModifier) !== 0
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
        const shift = (event.modifiers & Qt.ShiftModifier) !== 0
        if (event.key === Qt.Key_Escape) {
            if (view.helpOpen) view.helpOpen = false
            else if (view.menuFile !== null) view.closeFileMenu()
            else view.closeRequested()
        } else if (event.text === "?") {
            view.helpOpen = !view.helpOpen
        } else if (view.helpOpen) {
            // ściągawka przechwytuje resztę klawiszy, żeby nie ruszać widoku pod spodem
        } else if (ctrl && event.key === Qt.Key_C) {
            view.copySelection()
        } else if (event.key === Qt.Key_Menu) {
            view.openFileMenu(view.selectedFile, 24, 80)
        } else if (event.text === "]" || (alt && event.key === Qt.Key_Down)) {
            view.step(1)
        } else if (event.text === "[" || (alt && event.key === Qt.Key_Up)) {
            view.step(-1)
        } else if (alt || ctrl) {
            return
        } else if (event.key === Qt.Key_Down || event.text === "j") {
            view.moveCurrent(1, shift && event.key === Qt.Key_Down)
        } else if (event.key === Qt.Key_Up || event.text === "k") {
            view.moveCurrent(-1, shift && event.key === Qt.Key_Up)
        } else if (event.key === Qt.Key_PageDown) {
            view.moveCurrent(view.pageRows(), shift)
        } else if (event.key === Qt.Key_PageUp) {
            view.moveCurrent(-view.pageRows(), shift)
        } else if (event.key === Qt.Key_Left && view.mode === "split") {
            view.selSide = "left"
        } else if (event.key === Qt.Key_Right && view.mode === "split") {
            view.selSide = "right"
        } else if (event.text === "n") {
            view.stepHunk(1)
        } else if (event.text === "p") {
            view.stepHunk(-1)
        } else if (event.text === "r") {
            view.reload()
        } else if (event.text === "u") {
            view.toggleMode()
        } else if (event.text === "e") {
            view.expandAll()
        } else {
            return
        }
        event.accepted = true
    }

    // Schowek: QML nie ma własnego API, więc tekst przechodzi przez niewidoczny TextEdit (jego format to zwykły
    // tekst, także domyślnie).
    TextEdit {
        id: clipboard
        visible: false
    }

    component PillAction: Rectangle {
        id: pill
        property string label: ""
        property bool active: false   // wybrana opcja przełącznika (tryb diffa)
        signal clicked()
        implicitWidth: pillText.implicitWidth + 24
        implicitHeight: 30
        radius: Tide.Tokens.radiusSmall
        color: active ? Tide.Theme.alpha(Tide.Tokens.accent, 0.22)
             : pillMouse.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                                       : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
        Accessible.role: Accessible.Button
        Accessible.name: label
        Text {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
        }
        MouseArea {
            id: pillMouse
            anchors.fill: parent
            hoverEnabled: true
            onClicked: pill.clicked()
        }
    }

    Rectangle {
        id: bar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 52
        color: Tide.Tokens.backgroundTitlebar

        Text {
            id: headerLabel
            objectName: "diffHeader"
            anchors.left: parent.left
            anchors.leftMargin: 18
            anchors.right: statusLabel.left
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            text: view.headerText
            textFormat: Text.PlainText
            elide: Text.ElideMiddle
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 15
            font.weight: Font.DemiBold
        }
        Text {
            id: statusLabel
            objectName: "diffLoading"
            anchors.right: modeSwitch.left
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            visible: view.loading
            text: "Czytam…"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
        }
        // Przełącznik trybu (`u`): dwie opcje, wybrana podświetlona.
        Row {
            id: modeSwitch
            objectName: "diffModeSwitch"
            anchors.right: refreshButton.left
            anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            Text {
                anchors.verticalCenter: parent.verticalCenter
                rightPadding: 4
                text: "u"
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
            }
            PillAction {
                objectName: "diffModeUnified"
                label: "Jednolity"
                active: view.mode === "unified"
                onClicked: view.setMode("unified", true)
            }
            PillAction {
                objectName: "diffModeSplit"
                label: "Obok siebie"
                active: view.mode === "split"
                onClicked: view.setMode("split", true)
            }
        }
        PillAction {
            id: refreshButton
            objectName: "diffRefresh"
            anchors.right: closeButton.left
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            label: "Odśwież  r"
            onClicked: view.reload()
        }
        PillAction {
            id: closeButton
            objectName: "diffClose"
            anchors.right: parent.right
            anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            label: "Zamknij  Esc"
            onClicked: view.closeRequested()
        }
    }

    Item {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: bar.bottom
        anchors.bottom: parent.bottom

        // Błąd, brak zmian i stan „Czytam…” przy pierwszym odczycie zastępują treść.
        Column {
            id: message
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 640)
            spacing: 12
            visible: view.errorCode !== "" || (!view.diffData && view.loading) || (view.diffData !== null && !view.hasEntries)

            Text {
                objectName: "diffError"
                width: parent.width
                visible: view.errorCode !== ""
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: Diff.errorText(view.errorCode, view.errorMessage)
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 14
            }
            Text {
                objectName: "diffEmpty"
                width: parent.width
                visible: view.diffData !== null && !view.hasEntries
                horizontalAlignment: Text.AlignHCenter
                text: view.diffData && view.diffData.source === "worktree" ? "Brak niezacommitowanych zmian."
                                                                           : "Brak zmian w tym zakresie."
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 14
            }
            Text {
                objectName: "diffReading"
                width: parent.width
                visible: !view.diffData && view.errorCode === "" && view.loading
                horizontalAlignment: Text.AlignHCenter
                text: "Czytam…"
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 14
            }
            Column {
                objectName: "diffBasePicker"
                width: parent.width
                spacing: 6
                visible: view.errorCode === "unknown-base" && view.branches.length > 0

                Repeater {
                    model: view.branches.filter(b => b.selectable !== false).slice(0, 12)
                    delegate: PillAction {
                        required property var modelData
                        objectName: "diffBaseOption"
                        property string ref: modelData.ref
                        width: parent.width
                        label: modelData.name || modelData.ref
                        onClicked: view.chooseTarget(modelData.ref)
                    }
                }
            }
        }

        Rectangle {
            id: filePane
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 340
            visible: view.hasEntries
            color: Tide.Tokens.backgroundPanel

            ListView {
                id: filesList
                objectName: "diffFiles"
                anchors.fill: parent
                anchors.bottomMargin: 40
                clip: true
                model: view.listRows
                delegate: Item {
                    id: entry
                    required property var modelData
                    readonly property string kind: modelData.kind
                    width: filesList.width
                    height: kind === "file" ? 34 : kind === "untracked" ? 26 : 32

                    // Nagłówek grupy plików („W indeksie · 2 pliki · +3 −1”) i nieśledzonych (PT-360).
                    Text {
                        objectName: entry.kind === "group" ? "diffGroupHeader" : "diffUntrackedHeader"
                        visible: entry.kind === "group" || entry.kind === "untrackedHeader"
                        anchors.left: parent.left
                        anchors.leftMargin: 14
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 5
                        text: visible ? entry.modelData.text : ""
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "diffUntracked"
                        visible: entry.kind === "untracked"
                        anchors.left: parent.left
                        anchors.leftMargin: 14
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: visible ? entry.modelData.path : ""
                        textFormat: Text.PlainText
                        elide: Text.ElideMiddle
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Rectangle {
                        id: fileRow
                        readonly property var file: entry.modelData.file || ({})
                        objectName: "diffFile"
                        visible: entry.kind === "file"
                        anchors.fill: parent
                        property string path: file.path || ""
                        property string group: file.group || ""
                        property string status: Diff.statusLetter(file)
                        property string name: entry.kind === "file" ? Diff.fileName(file) : ""
                        property bool selected: entry.modelData.fileIndex === view.fileIndex
                        color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.14) : "transparent"

                        Text {
                            id: letter
                            objectName: "diffFileStatus"
                            anchors.left: parent.left
                            anchors.leftMargin: 14
                            anchors.verticalCenter: parent.verticalCenter
                            width: 28
                            text: fileRow.status
                            textFormat: Text.PlainText
                            color: fileRow.file.status === "A" ? Tide.Tokens.added
                                 : fileRow.file.status === "D" ? Tide.Tokens.removed : Tide.Tokens.textMuted
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 12
                        }
                        Text {
                            id: stats
                            objectName: "diffFileStats"
                            anchors.right: parent.right
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            textFormat: Text.PlainText
                            text: fileRow.file.binary ? "" : "+" + fileRow.file.additions + " −" + fileRow.file.deletions
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 12
                        }
                        Text {
                            id: fileLabel
                            objectName: "diffFileName"
                            anchors.left: letter.right
                            anchors.right: stats.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: fileRow.name
                            textFormat: Text.PlainText
                            elide: Text.ElideMiddle
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                        }
                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: mouse => {
                                if (mouse.button === Qt.RightButton) {
                                    const at = mapToItem(view, mouse.x, mouse.y)
                                    view.openFileMenu(fileRow.file, at.x, at.y)
                                } else {
                                    view.closeFileMenu()
                                    view.selectFile(entry.modelData.fileIndex)
                                    view.forceActiveFocus()
                                }
                            }
                        }
                    }
                }
            }
            Text {
                objectName: "diffTotals"
                anchors.left: parent.left
                anchors.leftMargin: 14
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 12
                text: Diff.totalsText(view.diffData ? view.diffData.totals : null)
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
            }
        }

        Item {
            id: diffPane
            anchors.left: filePane.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            visible: view.hasEntries

            // Diff ucięty limitem całości (PT-355): lista plików jest pełna, treść tylko części plików.
            Rectangle {
                id: truncatedBar
                objectName: "diffTruncatedBar"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: visible ? 34 : 0
                visible: view.diffData !== null && view.diffData.truncated === true
                color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.12)

                Text {
                    objectName: "diffTruncatedText"
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: Diff.TRUNCATED_BAR
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }

            // Nieudane rozwinięcie kontekstu: treść pliku zostaje, pasek mówi dlaczego.
            Rectangle {
                id: expandErrorBar
                objectName: "diffExpandError"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: truncatedBar.bottom
                height: visible ? 30 : 0
                visible: view.fileError !== "" && view.lineRows.length > 0
                color: Tide.Theme.alpha(Tide.Tokens.removed, 0.12)

                Text {
                    objectName: "diffExpandErrorText"
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: view.fileError
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }

            // Tylko nieśledzone pliki (PT-360): lista bez treści, nie ma czego pokazać w diffie.
            Text {
                objectName: "diffUntrackedNote"
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 520)
                visible: view.selectedFile === null && view.hasEntries
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: "Pliki nieśledzone są tylko wymienione — ich treści git jeszcze nie śledzi."
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 14
            }

            Column {
                id: fileNote
                z: 1  // nad ListView linii, żeby przyciski przyjmowały kliknięcia
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 520)
                spacing: 12
                visible: view.selectedFile !== null && view.lineRows.length === 0

                Text {
                    objectName: "diffFileNote"
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: !view.selectedFile ? ""
                        : view.selectedFile.binary ? Diff.binaryText(view.selectedFile)
                        : view.loadingPath === view.selectedPath ? "Doczytuję zmiany pliku…"
                        : view.fileError !== "" ? view.fileError
                        : Diff.isLarge(view.selectedFile) ? "Plik jest duży — treść nie została wczytana."
                        : Diff.needsLoad(view.selectedFile) ? "Treść pliku nie mieści się w limicie podglądu."
                        : "Brak zmian w treści pliku."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 14
                }
                PillAction {
                    objectName: "diffLargeShow"
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: Diff.isLarge(view.selectedFile) && view.loadingPath !== view.selectedPath
                    label: view.selectedFile && Diff.isLarge(view.selectedFile) ? Diff.largeText(view.selectedFile) : ""
                    onClicked: view.loadFile(view.selectedPath)
                }
                PillAction {
                    objectName: "diffRetryLoad"
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: view.fileError !== "" && Diff.needsLoad(view.selectedFile)
                             && view.loadingPath !== view.selectedPath
                    label: "Spróbuj ponownie"
                    onClicked: view.loadFile(view.selectedPath)
                }
            }

            ListView {
                id: lines
                objectName: "diffLines"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: expandErrorBar.bottom
                anchors.bottom: parent.bottom
                clip: true
                visible: view.mode === "unified"
                model: view.mode === "unified" ? view.lineRows : []
                boundsBehavior: Flickable.StopAtBounds
                delegate: Rectangle {
                    id: lineRow
                    required property var modelData
                    required property int index
                    // Zaznaczenie to zakres w widoku (selFrom..selTo); delegat tylko go czyta.
                    readonly property bool selected: index >= view.selFrom && index <= view.selTo
                    readonly property bool current: index === view.curRow
                    objectName: kind === "gap" ? "diffGap" : "diffLine"
                    property string kind: modelData.kind
                    property string content: modelData.text
                    property string oldLabel: modelData.oldNo
                    property string newLabel: modelData.newNo
                    readonly property color tone: kind === "+" ? Tide.Tokens.added : kind === "-" ? Tide.Tokens.removed
                                                : kind === "hunk" ? Tide.Tokens.lavender : "transparent"
                    width: lines.width
                    height: kind === "gap" ? 22 : Math.max(22, lineText.implicitHeight + 4)
                    color: kind === "+" || kind === "-" ? Tide.Theme.alpha(tone, 0.13)
                         : kind === "hunk" ? Tide.Theme.alpha(Tide.Tokens.lavender, 0.08)
                         : kind === "gap" ? Tide.Theme.alpha(Tide.Tokens.textPrimary, gapMouse.containsMouse ? 0.1 : 0.04)
                         : "transparent"

                    Rectangle {
                        objectName: "diffSelection"
                        anchors.fill: parent
                        visible: lineRow.selected
                        color: Tide.Theme.alpha(Tide.Tokens.accent, 0.22)
                    }
                    Rectangle {
                        objectName: "diffCurrentMark"
                        visible: lineRow.current
                        width: 3
                        height: parent.height
                        color: Tide.Tokens.accent
                    }

                    // Zwinięty fragment: kliknięcie rozwija ten jeden (PT-350); inna linia — zaznacza (PT-361).
                    MouseArea {
                        id: gapMouse
                        anchors.fill: parent
                        hoverEnabled: lineRow.kind === "gap"
                        onClicked: mouse => {
                            if (lineRow.kind === "gap") view.expand(lineRow.modelData.lines)
                            else view.selectRow(lineRow.index, (mouse.modifiers & Qt.ShiftModifier) !== 0)
                        }
                    }

                    Text {
                        id: oldNo
                        objectName: "diffOldNo"
                        x: 8
                        width: 48
                        anchors.verticalCenter: parent.verticalCenter
                        horizontalAlignment: Text.AlignRight
                        text: modelData.oldNo
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Text {
                        id: newNo
                        objectName: "diffNewNo"
                        x: 62
                        width: 48
                        anchors.verticalCenter: parent.verticalCenter
                        horizontalAlignment: Text.AlignRight
                        text: modelData.newNo
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Text {
                        id: marker
                        x: 118
                        anchors.verticalCenter: parent.verticalCenter
                        text: lineRow.kind === "hunk" || lineRow.kind === "gap" ? "" : lineRow.kind
                        textFormat: Text.PlainText
                        color: lineRow.tone
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                    }
                    Text {
                        id: lineText
                        objectName: "diffLineText"
                        x: lineRow.kind === "gap" ? 62 : 136
                        width: lineRow.width - x - 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.text
                        textFormat: Text.PlainText
                        wrapMode: lineRow.kind === "gap" ? Text.NoWrap : Text.WrapAnywhere
                        color: lineRow.kind === "hunk" ? Tide.Tokens.lavender
                             : lineRow.kind === "gap" ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
                        font.family: lineRow.kind === "gap" ? Tide.Theme.uiFamily : Tide.Theme.monoFamily
                        font.pixelSize: lineRow.kind === "gap" ? 12 : 13
                    }
                }
            }

            // Tryb „obok siebie”: pary linii z diff.js (`sideRows`); kontekst po obu stronach, usunięte po lewej,
            // dodane po prawej w tym samym wierszu, nadmiar ma pustą drugą stronę.
            ListView {
                id: sideLines
                objectName: "diffSideLines"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: expandErrorBar.bottom
                anchors.bottom: parent.bottom
                clip: true
                visible: view.mode === "split"
                model: view.mode === "split" ? view.sideRows : []
                boundsBehavior: Flickable.StopAtBounds
                delegate: Rectangle {
                    id: pairRow
                    required property var modelData
                    required property int index
                    readonly property bool selected: index >= view.selFrom && index <= view.selTo
                    readonly property bool current: index === view.curRow
                    readonly property string kind: modelData.kind
                    readonly property var leftSide: modelData.left || null
                    readonly property var rightSide: modelData.right || null
                    readonly property real half: Math.floor((width - 1) / 2)
                    objectName: kind === "gap" ? "diffGap" : kind === "hunk" ? "diffSideHunk" : "diffPair"
                    property string content: modelData.text || ""
                    property string leftKind: leftSide ? leftSide.kind : ""
                    property string leftNo: leftSide ? leftSide.no : ""
                    property string leftContent: leftSide ? leftSide.text : ""
                    property string rightKind: rightSide ? rightSide.kind : ""
                    property string rightNo: rightSide ? rightSide.no : ""
                    property string rightContent: rightSide ? rightSide.text : ""
                    width: sideLines.width
                    height: kind === "pair" ? Math.max(22, leftText.implicitHeight + 4, rightText.implicitHeight + 4)
                          : kind === "gap" ? 22 : Math.max(22, headText.implicitHeight + 4)
                    color: kind === "hunk" ? Tide.Theme.alpha(Tide.Tokens.lavender, 0.08)
                         : kind === "gap" ? Tide.Theme.alpha(Tide.Tokens.textPrimary, sideGapMouse.containsMouse ? 0.1 : 0.04)
                         : "transparent"

                    // Zaznaczona strona (kopiowana): lewa albo prawa połowa wiersza.
                    Rectangle {
                        objectName: "diffSelection"
                        x: view.selSide === "left" ? 0 : pairRow.half + 1
                        width: view.selSide === "left" ? pairRow.half : pairRow.width - x
                        height: parent.height
                        visible: pairRow.selected && pairRow.kind === "pair"
                        color: Tide.Theme.alpha(Tide.Tokens.accent, 0.22)
                    }
                    Rectangle {
                        objectName: "diffCurrentMark"
                        visible: pairRow.current
                        width: 3
                        height: parent.height
                        color: Tide.Tokens.accent
                    }

                    MouseArea {
                        id: sideGapMouse
                        anchors.fill: parent
                        hoverEnabled: pairRow.kind === "gap"
                        onClicked: mouse => {
                            if (pairRow.kind === "gap") view.expand(pairRow.modelData.lines)
                            else view.selectRow(pairRow.index, (mouse.modifiers & Qt.ShiftModifier) !== 0,
                                                mouse.x < pairRow.half ? "left" : "right")
                        }
                    }

                    // Nagłówek hunka i zwinięty fragment rozciągają się na całą szerokość.
                    Text {
                        id: headText
                        objectName: "diffSideHeadText"
                        visible: pairRow.kind !== "pair"
                        x: pairRow.kind === "gap" ? 62 : 8
                        width: pairRow.width - x - 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: pairRow.kind === "pair" ? "" : pairRow.content
                        textFormat: Text.PlainText
                        wrapMode: pairRow.kind === "gap" ? Text.NoWrap : Text.WrapAnywhere
                        color: pairRow.kind === "gap" ? Tide.Tokens.textMuted : Tide.Tokens.lavender
                        font.family: pairRow.kind === "gap" ? Tide.Theme.uiFamily : Tide.Theme.monoFamily
                        font.pixelSize: pairRow.kind === "gap" ? 12 : 13
                    }

                    // Lewa połowa (stary plik).
                    Rectangle {
                        visible: pairRow.kind === "pair"
                        x: 0
                        width: pairRow.half
                        height: parent.height
                        color: pairRow.leftKind === "-" ? Tide.Theme.alpha(Tide.Tokens.removed, 0.13)
                             : pairRow.leftSide === null ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.03) : "transparent"
                    }
                    Text {
                        objectName: "diffSideOldNo"
                        visible: pairRow.kind === "pair"
                        x: 4
                        width: 44
                        anchors.verticalCenter: parent.verticalCenter
                        horizontalAlignment: Text.AlignRight
                        text: pairRow.leftNo
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Text {
                        id: leftText
                        objectName: "diffSideOldText"
                        visible: pairRow.kind === "pair"
                        x: 56
                        width: pairRow.half - x - 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: pairRow.leftContent
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                    }

                    // Prawa połowa (nowy plik).
                    Rectangle {
                        visible: pairRow.kind === "pair"
                        x: pairRow.half + 1
                        width: pairRow.width - x
                        height: parent.height
                        color: pairRow.rightKind === "+" ? Tide.Theme.alpha(Tide.Tokens.added, 0.13)
                             : pairRow.rightSide === null ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.03) : "transparent"
                    }
                    Text {
                        objectName: "diffSideNewNo"
                        visible: pairRow.kind === "pair"
                        x: pairRow.half + 5
                        width: 44
                        anchors.verticalCenter: parent.verticalCenter
                        horizontalAlignment: Text.AlignRight
                        text: pairRow.rightNo
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Text {
                        id: rightText
                        objectName: "diffSideNewText"
                        visible: pairRow.kind === "pair"
                        x: pairRow.half + 57
                        width: pairRow.width - x - 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: pairRow.rightContent
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                    }
                    Rectangle {
                        visible: pairRow.kind === "pair"
                        x: pairRow.half
                        width: 1
                        height: parent.height
                        color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                    }
                }
            }
        }
    }

    // Menu pliku (PT-361): tło łapie kliknięcie poza menu i je zamyka. „Otwórz w edytorze” tylko wtedy, gdy źródło ma
    // katalog roboczy, jest wyrzutnia, a plik istnieje (`editorPath`).
    MouseArea {
        id: menuShade
        objectName: "diffFileMenuShade"
        anchors.fill: parent
        z: 50
        visible: view.menuFile !== null
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: view.closeFileMenu()

        Rectangle {
            id: fileMenu
            objectName: "diffFileMenu"
            x: Math.max(4, Math.min(view.menuX, view.width - width - 4))
            y: Math.max(4, Math.min(view.menuY, view.height - height - 4))
            width: 220
            height: menuColumn.implicitHeight + 12
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.backgroundPanel
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)

            Column {
                id: menuColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 6
                spacing: 2
                PillAction {
                    objectName: "diffCopyPath"
                    width: parent.width
                    label: "Kopiuj ścieżkę"
                    onClicked: view.copyFilePath(view.menuFile)
                }
                PillAction {
                    objectName: "diffOpenEditor"
                    width: parent.width
                    visible: view.menuFile !== null && view.editorPath(view.menuFile) !== ""
                    height: visible ? implicitHeight : 0
                    label: "Otwórz w edytorze"
                    onClicked: view.openInEditor(view.menuFile)
                }
            }
        }
    }

    // Ściągawka klawiszy (`?`): wszystkie klawisze widoku; Esc zamyka najpierw ją, potem widok.
    Rectangle {
        id: cheatSheet
        objectName: "diffCheatSheet"
        anchors.fill: parent
        z: 60
        visible: view.helpOpen
        color: Tide.Theme.alpha(Tide.Tokens.backgroundWindow, 0.94)

        MouseArea {
            anchors.fill: parent
            onClicked: view.helpOpen = false
        }
        Text {
            id: sheetTitle
            anchors.left: parent.left
            anchors.leftMargin: 40
            anchors.top: parent.top
            anchors.topMargin: 28
            text: "Klawisze widoku diffa  ·  ? lub Esc zamyka"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        ListView {
            id: sheetList
            objectName: "diffCheatSheetKeys"
            anchors.left: parent.left
            anchors.leftMargin: 40
            anchors.right: parent.right
            anchors.rightMargin: 40
            anchors.top: sheetTitle.bottom
            anchors.topMargin: 16
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 20
            clip: true
            interactive: contentHeight > height
            model: view.keyHelp
            delegate: Item {
                required property var modelData
                width: sheetList.width
                height: 26
                Text {
                    objectName: "diffHelpKeys"
                    x: 0
                    width: 220
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.keys
                    textFormat: Text.PlainText
                    color: Tide.Tokens.accent
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 13
                }
                Text {
                    objectName: "diffHelpText"
                    x: 232
                    width: parent.width - x
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.text
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }
        }
    }
}
