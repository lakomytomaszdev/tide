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
// Esc — zamknij. Wszystkie teksty z gita (ścieżki, linie, nazwy gałęzi) to Text.PlainText; linie diffu to
// ListView (wirtualizacja), nie Repeater.
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
    property var lineRows: []
    property var hunkStarts: []
    property int currentHunk: -1

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
        fileError = ""
        loading = true
        backend.gitDiff(pendingTag, JSON.stringify(effectiveRequest))
    }

    // Doczytuje treść jednego pliku (duży, przycięty) przez `Git.Diff` z `options.path`; odpowiedź zastępuje
    // wpis pliku w `diffData`.
    function loadFile(path) {
        if (!backend || !path) return
        sequence += 1
        fileTag = "file:" + sequence
        loadingPath = path
        fileError = ""
        const options = selectedGroup ? {path: path, group: selectedGroup} : {path: path}
        backend.gitDiff(fileTag, JSON.stringify(Object.assign({}, effectiveRequest, {options: options})))
    }

    function onFileReply(reply) {
        const path = loadingPath
        const group = selectedGroup
        fileTag = ""
        loadingPath = ""
        if (!reply || !reply.ok || !reply.files || reply.files.length === 0) {
            fileError = Diff.errorText(reply ? reply.error : "failed", reply ? reply.message : "")
            return
        }
        diffData = Diff.mergeFile(diffData, reply.files[0])
        if (selectedPath === path && selectedGroup === group) selectFile(Diff.indexOfPath(files, path, group))
    }

    function chooseTarget(ref) {
        targetRef = ref
        branches = []
        reload()
    }

    function selectFile(index) {
        if (index < 0 || index >= files.length) {
            fileIndex = -1
            selectedPath = ""
            selectedGroup = ""
            lineRows = []
            hunkStarts = []
            currentHunk = -1
            return
        }
        const group = files[index].group || ""
        if (files[index].path !== selectedPath || group !== selectedGroup) fileError = ""
        fileIndex = index
        selectedPath = files[index].path
        selectedGroup = group
        lineRows = Diff.rows(files[index])
        hunkStarts = Diff.hunkStarts(lineRows)
        currentHunk = hunkStarts.length ? 0 : -1
        lines.positionViewAtBeginning()
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
        lines.positionViewAtIndex(hunkStarts[currentHunk], ListView.Beginning)
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
    }

    onRequestChanged: if (completed) { targetRef = ""; reload() }
    onBackendChanged: if (completed) reload()
    property bool completed: false
    Component.onCompleted: {
        completed = true
        forceActiveFocus()
        reload()
    }

    Keys.onPressed: event => {
        const alt = (event.modifiers & Qt.AltModifier) !== 0
        if (event.key === Qt.Key_Escape) {
            view.closeRequested()
        } else if (event.text === "]" || (alt && event.key === Qt.Key_Down)) {
            view.step(1)
        } else if (event.text === "[" || (alt && event.key === Qt.Key_Up)) {
            view.step(-1)
        } else if (event.text === "n") {
            view.stepHunk(1)
        } else if (event.text === "p") {
            view.stepHunk(-1)
        } else if (event.text === "r") {
            view.reload()
        } else {
            return
        }
        event.accepted = true
    }

    component PillAction: Rectangle {
        id: pill
        property string label: ""
        signal clicked()
        implicitWidth: pillText.implicitWidth + 24
        implicitHeight: 30
        radius: Tide.Tokens.radiusSmall
        color: pillMouse.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
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
            anchors.right: refreshButton.left
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            visible: view.loading
            text: "Czytam…"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
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
                            onClicked: view.selectFile(entry.modelData.fileIndex)
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
                anchors.top: truncatedBar.bottom
                anchors.bottom: parent.bottom
                clip: true
                model: view.lineRows
                boundsBehavior: Flickable.StopAtBounds
                delegate: Rectangle {
                    id: lineRow
                    required property var modelData
                    objectName: "diffLine"
                    property string kind: modelData.kind
                    property string content: modelData.text
                    property string oldLabel: modelData.oldNo
                    property string newLabel: modelData.newNo
                    readonly property color tone: kind === "+" ? Tide.Tokens.added : kind === "-" ? Tide.Tokens.removed
                                                : kind === "hunk" ? Tide.Tokens.lavender : "transparent"
                    width: lines.width
                    height: Math.max(22, lineText.implicitHeight + 4)
                    color: kind === "+" || kind === "-" ? Tide.Theme.alpha(tone, 0.13)
                         : kind === "hunk" ? Tide.Theme.alpha(Tide.Tokens.lavender, 0.08) : "transparent"

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
                        text: lineRow.kind === "hunk" ? "" : lineRow.kind
                        textFormat: Text.PlainText
                        color: lineRow.tone
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                    }
                    Text {
                        id: lineText
                        objectName: "diffLineText"
                        x: 136
                        width: lineRow.width - 148
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.text
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: lineRow.kind === "hunk" ? Tide.Tokens.lavender : Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                    }
                }
            }
        }
    }
}
