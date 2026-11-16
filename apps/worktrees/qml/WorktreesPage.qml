// Zawartość okna Worktrees: nagłówek z podsumowaniem, szukanie, filtry repozytoriów, trzy grupy
// (W pracy, Do sprzątnięcia, Do decyzji), brakujące katalogi, gałęzie główne i stopka z usuwaniem
// zaznaczonych. Sam nie rozmawia z usługą — dostaje dane i zgłasza prośby sygnałami, dzięki czemu
// da się go załadować w teście z przykładowymi danymi.
//
// Usuwanie: sugerowane („Do sprzątnięcia”) idą zwykłym potwierdzeniem i Remove(). Gdy wśród
// zaznaczonych jest choć jeden nie-sugerowany (zmiany, niescalone commity, świeża praca), okno
// pokazuje mocniejsze potwierdzenie: dla każdego worktree, co dokładnie przepadnie, pole „usuń też
// gałąź” i czerwony przycisk „Usuń mimo to” — a potem woła RemoveForced() ze stanem z potwierdzenia.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "git" as Git
import "worktrees.js" as Logic

Rectangle {
    id: page

    property var worktrees: []
    property bool available: true
    property var launchers: ({ terminal: true, rider: false })
    property real nowTs: Date.now() / 1000
    property string homeDir: ""

    signal removeRequested(var paths)
    // confirmed: JSON {ścieżka: {dirty, unmerged, unpushed, branch}} — stan z potwierdzenia.
    signal removeForcedRequested(var paths, bool deleteBranches, string confirmed)
    signal openRequested(string path, string app)
    signal refreshRequested()
    signal pruneRequested(string repoPath)
    signal fetchRequested(string repoPath)   // „Pobierz”: git fetch ze wszystkich zdalnych repozytorium
    signal pullRequested(string path)        // „Zaciągnij”: merge --ff-only do upstreamu w worktree (PT-353)

    // Widok diffa (PT-360): żądanie `Git.Diff` otwartego widoku (null — lista) i backend z gitDiff/replied.
    property var diffRequest: null
    property var diffBackend: null
    readonly property bool diffOpen: diffRequest !== null

    function openDiff(path, kind) {
        const w = worktrees.find(x => x.path === path)
        const request = w ? Logic.diffRequest(w, kind) : null
        if (request !== null) diffRequest = request
    }

    function closeDiff() {
        diffRequest = null
        page.forceActiveFocus()
    }

    // Stan widoku.
    property var fetching: ({})        // repoPath → true, gdy trwa pobieranie
    property var fetchNotes: ({})        // repoPath → {ok, text}: wynik ostatniego pobierania
    property var pulling: ({})           // ścieżka worktree → true, gdy trwa zaciąganie
    property var pullNotes: ({})         // ścieżka worktree → {ok, text}: wynik ostatniego zaciągania
    property string query: ""
    property string repo: "all"
    property var selected: ({})          // ścieżka → true
    property bool showAllDecide: false
    property bool showMain: false
    property bool confirming: false
    property bool deleteBranches: false  // pole „usuń też gałąź” w mocniejszym potwierdzeniu
    property bool removing: false
    property var results: null           // podsumowanie ostatniego usuwania (Logic.resultText)
    property string notice: ""           // komunikat po porządkowaniu brakujących katalogów
    property var titles: ({})            // ścieżka → tytuł, do opisu wyniku po usunięciu

    readonly property var chips: Logic.repoChips(worktrees)
    readonly property var sum: Logic.summary(worktrees)
    readonly property var shown: Logic.filtered(worktrees, query, repo, chips)
    readonly property var active: Logic.inGroup(shown, "active")
    readonly property var clean: Logic.inGroup(shown, "clean")
    readonly property var decide: Logic.inGroup(shown, "decide")
    // Worktrees na gałęzi głównej; same główne katalogi repozytoriów nie są tu pokazywane.
    readonly property var mains: Logic.inGroup(shown, "main").filter(w => !w.isMain)
    readonly property var missing: Logic.missingByRepo(shown)
    readonly property var repoRows: Logic.repoRows(worktrees, repo, chips)
    readonly property var picked: Logic.selection(worktrees, selected)
    readonly property bool forcing: picked.risky.length > 0
    readonly property var unmergedInfo: Logic.unmergedBranches(picked.items)
    readonly property bool allShownPicked: clean.length > 0 && clean.every(w => selected[w.path] === true)
    readonly property bool canTerminal: launchers && launchers.terminal === true
    readonly property bool canRider: launchers && launchers.rider === true

    readonly property alias searchText: search.text  // to, co widać w polu szukania (testy)

    onQueryChanged: if (search.text !== query) search.text = query

    function focusSearch() {
        search.forceActiveFocus()
        search.selectAll()
    }

    function toggle(path) {
        const w = worktrees.find(x => x.path === path)
        if (!Logic.selectable(w) && !selected[path]) return  // główny katalog, gałąź główna, agent…
        const next = Object.assign({}, selected)
        if (next[path]) delete next[path]
        else next[path] = true
        selected = next
    }

    function toggleAllShown() {
        const next = Object.assign({}, selected)
        const pick = !allShownPicked
        for (const w of clean) {
            if (pick) next[w.path] = true
            else delete next[w.path]
        }
        selected = next
    }

    function askRemove() {
        if (picked.items.length > 0 && !removing) {
            deleteBranches = false  // zawsze świadomie, od nowa przy każdym potwierdzeniu
            confirming = true
        }
    }

    function confirmRemove() {
        const names = {}
        for (const w of picked.items) names[w.path] = w.title + " · " + w.repo
        titles = names
        const items = picked.items
        const forced = forcing
        confirming = false
        removing = true
        results = null
        if (forced) {
            removeForcedRequested(items.map(w => w.path), deleteBranches && unmergedInfo.count > 0,
                                  JSON.stringify(Logic.confirmedState(items)))
        } else {
            removeRequested(items.map(w => w.path))
        }
    }

    // Wynik Remove() z usługi (tablica wyników dla ścieżek) albo null przy błędzie.
    function finishRemove(list) {
        removing = false
        const items = Array.isArray(list) ? list
            : [{ path: "", removed: false, message: "brak odpowiedzi usługi — lista odświeży się sama" }]
        results = Logic.resultText(items, titles)
        const next = Object.assign({}, selected)
        for (const r of items) if (r.removed) delete next[r.path]
        selected = next
    }

    function startFetch(repoPath) {
        if (fetching[repoPath] === true) return
        const busy = Object.assign({}, fetching)
        busy[repoPath] = true
        fetching = busy
        const notes = Object.assign({}, fetchNotes)
        delete notes[repoPath]
        fetchNotes = notes
        fetchRequested(repoPath)
    }

    // Wynik Fetch() dla repozytorium (result: obiekt z usługi albo null przy braku odpowiedzi).
    function finishFetch(repoPath, result) {
        const busy = Object.assign({}, fetching)
        delete busy[repoPath]
        fetching = busy
        const notes = Object.assign({}, fetchNotes)
        notes[repoPath] = Logic.fetchNote(result)
        fetchNotes = notes
    }

    function startPull(path) {
        if (pulling[path] === true) return
        const w = worktrees.find(x => x.path === path)
        if (!w || Logic.pullBlocker(w) !== null) return   // przycisk i tak jest wtedy wyłączony
        const busy = Object.assign({}, pulling)
        busy[path] = true
        pulling = busy
        const notes = Object.assign({}, pullNotes)
        delete notes[path]
        pullNotes = notes
        pullRequested(path)
    }

    // Wynik PullFastForward() dla worktree (result: obiekt z usługi albo null przy braku odpowiedzi).
    // Lista odświeża się sama (usługa po zaciągnięciu przegląda repozytoria i wysyła Changed).
    function finishPull(path, result) {
        const busy = Object.assign({}, pulling)
        delete busy[path]
        pulling = busy
        const notes = Object.assign({}, pullNotes)
        notes[path] = Logic.pullNote(result)
        pullNotes = notes
    }

    function finishPrune(result) {
        notice = result && result.ok
            ? (result.pruned.length > 0 ? "Usunięto wpisy bez katalogu: " + result.pruned.length
                                        : "Nie było wpisów do usunięcia.")
            : "Nie udało się: " + (result ? result.message : "brak odpowiedzi usługi")
    }

    color: Tide.Tokens.backgroundWindow
    focus: true
    Keys.onEscapePressed: if (confirming) confirming = false

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // --- nagłówek ---------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.topMargin: 18
            Layout.bottomMargin: 14
            spacing: 16

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    Layout.fillWidth: true
                    text: Logic.count(page.sum.count, "worktree", "worktrees", "worktrees") + " w "
                          + Logic.count(page.sum.repos, "repozytorium", "repozytoriach", "repozytoriach")
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Accessible.role: Accessible.Heading
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Zajmują " + (page.sum.sizesPending ? "liczę…" : Tide.Theme.formatBytes(page.sum.bytes))
                          + ". Do sprzątnięcia bez ryzyka: " + page.sum.cleanCount
                          + " (" + Tide.Theme.formatBytes(page.sum.cleanBytes) + ")."
                          + " W pracy: " + page.sum.activeCount + ". Nic nie jest usuwane bez zaznaczenia."
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }

            Rectangle {
                id: searchBox
                Layout.preferredWidth: 260
                Layout.preferredHeight: 36
                radius: Tide.Tokens.radiusFrame
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: search.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                 : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)

                // Lupa narysowana z prostokątów — bez ikon z zewnątrz.
                Item {
                    id: glass
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: 14
                    height: 14
                    Rectangle {
                        width: 10; height: 10; radius: 5
                        color: "transparent"
                        border.width: 1.6
                        border.color: Tide.Tokens.textMuted
                    }
                    Rectangle {
                        x: 8.5; y: 10; width: 5; height: 1.8; radius: 1
                        rotation: 45
                        color: Tide.Tokens.textMuted
                    }
                }
                TextInput {
                    id: search
                    anchors.left: glass.right
                    anchors.leftMargin: 8
                    anchors.right: clear.left
                    anchors.rightMargin: 6
                    anchors.verticalCenter: parent.verticalCenter
                    color: Tide.Tokens.textPrimary
                    selectionColor: Tide.Tokens.accent
                    selectedTextColor: Tide.Tokens.backgroundDesktop
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    clip: true
                    // Bez wiązania text: page.query — przypisanie w Esc albo × by je zerwało i zapytanie
                    // ustawione z zewnątrz (showItem okna Tide) filtrowałoby przy pustym polu.
                    // Pole idzie za page.query przez onQueryChanged strony.
                    onTextEdited: page.query = text
                    Keys.onEscapePressed: page.query = ""
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "Szukaj gałęzi, commita, sesji"
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: search.text === ""
                        text: "Szukaj gałęzi, commita, sesji…"
                        color: Tide.Tokens.textMuted
                        font: search.font
                    }
                }
                Text {
                    id: clear
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    visible: page.query !== ""
                    text: "×"
                    color: Tide.Tokens.textMuted
                    font.pixelSize: 16
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -6
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.query = ""
                    }
                }
            }
        }

        // --- filtry repozytoriów ------------------------------------------------------------
        Flow {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.bottomMargin: 14
            spacing: 6
            visible: page.chips.length > 2

            Repeater {
                model: page.chips
                Rectangle {
                    required property var modelData
                    readonly property bool current: page.repo === modelData.id
                    implicitHeight: 28
                    implicitWidth: chipLabel.implicitWidth + 24
                    radius: height / 2
                    color: current ? Tide.Tokens.accent
                         : chipArea.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.12) : Tide.Tokens.backgroundCard
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: chipLabel.text
                    Accessible.checked: current
                    Accessible.onPressAction: page.repo = modelData.id
                    Text {
                        id: chipLabel
                        anchors.centerIn: parent
                        text: modelData.label + " · " + modelData.count
                        color: current ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: current ? Font.DemiBold : Font.Normal
                    }
                    MouseArea {
                        id: chipArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.repo = modelData.id
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
        }

        // --- grupy ----------------------------------------------------------------------------
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: groups.implicitHeight + 24
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}

            ColumnLayout {
                id: groups
                x: 24
                y: 8
                width: flick.width - 48
                spacing: 18

                // Repozytoria: zdalne, czas ostatniego pobrania i „Pobierz” (tylko ze zdalnym).
                ColumnLayout {
                    objectName: "repoStrip"
                    Layout.fillWidth: true
                    Layout.topMargin: 10
                    visible: page.repoRows.length > 0
                    spacing: 6
                    Repeater {
                        model: page.repoRows
                        ColumnLayout {
                            id: repoRow
                            required property var modelData
                            readonly property bool busy: page.fetching[modelData.repoPath] === true
                            readonly property var note: page.fetchNotes[modelData.repoPath] || null
                            // „Zaciągnij” gałęzi głównego katalogu (PT-353) — jego wiersza nie ma na liście.
                            readonly property var main: modelData.main
                            readonly property bool pullBusy: main !== null && page.pulling[main.path] === true
                            readonly property var pullBlocker: main !== null ? Logic.pullBlocker(main) : null
                            readonly property var pullNote: main !== null ? (page.pullNotes[main.path] || null) : null
                            Layout.fillWidth: true
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                Text {
                                    Layout.preferredWidth: 150
                                    leftPadding: 14
                                    text: repoRow.modelData.repo
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Text {
                                    objectName: "repoRemotes"
                                    Layout.fillWidth: true
                                    text: Logic.repoLine(repoRow.modelData.sample, page.nowTs)
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                                Tide.Button {
                                    objectName: "fetchButton"
                                    visible: repoRow.modelData.canFetch
                                    small: true
                                    text: Logic.fetchButtonText(repoRow.busy)
                                    enabled: !repoRow.busy && page.available
                                    toolTip: "Pobiera zmiany ze wszystkich zdalnych (git fetch). Nie zmienia gałęzi ani plików."
                                    onClicked: page.startFetch(repoRow.modelData.repoPath)
                                }
                                Text {
                                    objectName: "repoPullReason"
                                    visible: repoPull.visible && !repoRow.pullBusy && repoRow.pullBlocker !== null
                                    text: repoRow.pullBlocker !== null ? repoRow.pullBlocker.text : ""
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 11
                                }
                                Tide.Button {
                                    id: repoPull
                                    objectName: "repoPullButton"
                                    visible: Logic.canOfferPull(repoRow.main)
                                    small: true
                                    text: Logic.pullButtonText(repoRow.pullBusy, repoRow.main)
                                         + (repoRow.main ? " " + repoRow.main.branch : "")
                                    enabled: !repoRow.pullBusy && repoRow.pullBlocker === null && page.available
                                    toolTip: repoRow.pullBlocker !== null
                                             ? "Nie można zaciągnąć: " + repoRow.pullBlocker.text
                                             : "Przesuwa gałąź głównego katalogu do upstreamu wyłącznie fast-forward."
                                    onClicked: page.startPull(repoRow.main.path)
                                }
                            }
                            Text {
                                objectName: "repoPullNote"
                                Layout.fillWidth: true
                                visible: repoRow.pullNote !== null
                                leftPadding: 14
                                text: repoRow.pullNote ? repoRow.pullNote.text : ""
                                color: repoRow.pullNote && repoRow.pullNote.ok ? Tide.Tokens.positive : Tide.Tokens.neutral
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                objectName: "fetchNote"
                                Layout.fillWidth: true
                                visible: repoRow.note !== null
                                leftPadding: 14
                                text: repoRow.note ? repoRow.note.text : ""
                                color: repoRow.note && repoRow.note.ok ? Tide.Tokens.positive : Tide.Tokens.neutral
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                // Wynik ostatniego sprzątania.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: 10
                    visible: page.results !== null
                    implicitHeight: resultBody.implicitHeight + 24
                    radius: Tide.Tokens.radiusCard
                    color: Tide.Theme.alpha(Tide.Tokens.positive, 0.08)
                    border.width: 1
                    border.color: Tide.Theme.alpha(page.results && page.results.skipped > 0 ? Tide.Tokens.neutral
                                                                                             : Tide.Tokens.positive, 0.35)
                    RowLayout {
                        id: resultBody
                        anchors.fill: parent
                        anchors.margins: 12
                        anchors.leftMargin: 16
                        spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: page.results === null ? "" :
                                      "Usunięto: " + page.results.removed + " (zwolniono "
                                      + Tide.Theme.formatBytes(page.results.freed) + ")"
                                      + (page.results.branchKept > 0 ? " · gałąź została: " + page.results.branchKept : "")
                                      + (page.results.skipped > 0 ? " · nie usunięto: " + page.results.skipped : "")
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: text !== ""
                                wrapMode: Text.WordWrap
                                text: page.results === null ? "" : page.results.details.join("\n")
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                            }
                        }
                        Tide.Button {
                            text: "OK"
                            onClicked: page.results = null
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: 40
                    visible: !page.available || page.worktrees.length === 0 || page.shown.length === 0
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: !page.available ? "Usługa tide-daemon nie działa.\nUruchom: systemctl --user start tide-daemon"
                        : page.worktrees.length === 0 ? "Brak repozytoriów git w katalogu projektów."
                        : "Nic nie pasuje do wyszukiwania."
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }

                // 1. W pracy
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.active.length > 0
                    spacing: 8
                    GroupHeader {
                        Layout.fillWidth: true
                        Layout.topMargin: 10
                        tint: Tide.Tokens.working
                        title: "W pracy"
                        count: page.active.length
                        description: "agent działa (tych nie usuniesz, póki pracuje) albo coś zmieniło się w ostatniej dobie, "
                                     + "a praca nie jest jeszcze scalona"
                    }
                    Repeater {
                        model: page.active
                        WorktreeItem {
                            required property var modelData
                            Layout.fillWidth: true
                            worktree: modelData
                            nowTs: page.nowTs
                            homeDir: page.homeDir
                            canTerminal: page.canTerminal
                            canRider: page.canRider
                            checked: page.selected[modelData.path] === true
                            enabled: !page.removing
                            onToggled: page.toggle(modelData.path)
                            onOpenRequested: (path, app) => page.openRequested(path, app)
                            onDiffRequested: (path, kind) => page.openDiff(path, kind)
                            pullBusy: page.pulling[modelData.path] === true
                            pullNote: page.pullNotes[modelData.path] || null
                            onPullRequested: path => page.startPull(path)
                        }
                    }
                }

                // 2. Do sprzątnięcia
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.available && page.worktrees.length > 0
                    spacing: 8
                    GroupHeader {
                        Layout.fillWidth: true
                        tint: Tide.Tokens.positive
                        title: "Do sprzątnięcia"
                        count: page.clean.length
                        description: "scalone z lokalną gałęzią główną (bez łączenia z serwerem), bez zmian, nikt w nich nie pracuje; "
                                     + "scalona praca trafia tu od razu"
                        actionText: page.clean.length === 0 || page.removing ? ""
                                  : page.allShownPicked ? "Odznacz wszystkie"
                                  : "Zaznacz wszystkie (" + page.clean.length + ")"
                        onActionTriggered: page.toggleAllShown()
                    }
                    Text {
                        visible: page.clean.length === 0
                        leftPadding: 18
                        text: "Nic do sprzątnięcia."
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    Repeater {
                        model: page.clean
                        WorktreeItem {
                            required property var modelData
                            Layout.fillWidth: true
                            worktree: modelData
                            nowTs: page.nowTs
                            homeDir: page.homeDir
                            checked: page.selected[modelData.path] === true
                            enabled: !page.removing
                            onToggled: page.toggle(modelData.path)
                            onDiffRequested: (path, kind) => page.openDiff(path, kind)
                            pullBusy: page.pulling[modelData.path] === true
                            pullNote: page.pullNotes[modelData.path] || null
                            onPullRequested: path => page.startPull(path)
                        }
                    }
                }

                // 3. Do decyzji
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.decide.length > 0
                    spacing: 8
                    GroupHeader {
                        Layout.fillWidth: true
                        tint: Tide.Tokens.neutral
                        title: "Do decyzji"
                        count: page.decide.length
                        description: "stoją od ponad doby, ale mają niescalone commity albo zmiany — otwórz i zdecyduj; "
                                     + "zaznaczone usuniesz po potwierdzeniu, co przepadnie"
                    }
                    Repeater {
                        model: page.showAllDecide ? page.decide : page.decide.slice(0, Logic.DECIDE_PREVIEW)
                        WorktreeItem {
                            required property var modelData
                            Layout.fillWidth: true
                            worktree: modelData
                            nowTs: page.nowTs
                            homeDir: page.homeDir
                            canTerminal: page.canTerminal
                            canRider: page.canRider
                            checked: page.selected[modelData.path] === true
                            enabled: !page.removing
                            onToggled: page.toggle(modelData.path)
                            onOpenRequested: (path, app) => page.openRequested(path, app)
                            onDiffRequested: (path, kind) => page.openDiff(path, kind)
                            pullBusy: page.pulling[modelData.path] === true
                            pullNote: page.pullNotes[modelData.path] || null
                            onPullRequested: path => page.startPull(path)
                        }
                    }
                    Text {
                        visible: page.decide.length > Logic.DECIDE_PREVIEW
                        leftPadding: 14
                        text: page.showAllDecide ? "zwiń do " + Logic.DECIDE_PREVIEW
                            : "+ " + (page.decide.length - Logic.DECIDE_PREVIEW) + " kolejnych · pokaż wszystkie ("
                              + page.decide.length + ")"
                        color: moreArea.containsMouse ? Qt.lighter(Tide.Tokens.accent, 1.2) : Tide.Tokens.accent
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        Accessible.role: Accessible.Button
                        Accessible.name: text
                        Accessible.onPressAction: page.showAllDecide = !page.showAllDecide
                        MouseArea {
                            id: moreArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.showAllDecide = !page.showAllDecide
                        }
                    }
                }

                // Worktrees, których katalogu już nie ma — porządkuje je tylko `git worktree prune`.
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.missing.length > 0 || page.notice !== ""
                    spacing: 6
                    Repeater {
                        model: page.missing
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                Layout.fillWidth: true
                                leftPadding: 14
                                text: modelData.repo + ": " + Logic.count(modelData.count, "worktree", "worktrees", "worktrees")
                                      + " bez katalogu (usunięte ręcznie) — git pamięta tylko wpis"
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Tide.Button {
                                small: true
                                text: "Usuń wpisy (git worktree prune)"
                                toolTip: "Usuwa tylko wpisy worktrees, których katalogu już nie ma. Pliki i gałęzie zostają."
                                onClicked: page.pruneRequested(modelData.repoPath)
                            }
                        }
                    }
                    Text {
                        visible: page.notice !== ""
                        leftPadding: 14
                        text: page.notice
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                }

                // Gałęzie główne: zwinięte, nigdy do sprzątania.
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.mains.length > 0
                    spacing: 8
                    GroupHeader {
                        Layout.fillWidth: true
                        tint: Tide.Tokens.textMuted
                        title: "Gałęzie główne"
                        count: page.mains.length
                        description: "worktrees na master, main albo develop — nigdy ich nie sprzątamy"
                        actionText: page.showMain ? "zwiń" : "pokaż"
                        onActionTriggered: page.showMain = !page.showMain
                    }
                    Repeater {
                        model: page.showMain ? page.mains : []
                        WorktreeItem {
                            required property var modelData
                            Layout.fillWidth: true
                            worktree: modelData
                            nowTs: page.nowTs
                            homeDir: page.homeDir
                            canTerminal: page.canTerminal
                            canRider: page.canRider
                            onOpenRequested: (path, app) => page.openRequested(path, app)
                            onDiffRequested: (path, kind) => page.openDiff(path, kind)
                            pullBusy: page.pulling[modelData.path] === true
                            pullNote: page.pullNotes[modelData.path] || null
                            onPullRequested: path => page.startPull(path)
                        }
                    }
                }
            }
        }

        // --- stopka ----------------------------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: footer.implicitHeight + 28
            color: Tide.Tokens.backgroundView
            Rectangle {
                anchors.top: parent.top
                width: parent.width
                height: 1
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
            }
            RowLayout {
                id: footer
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 12
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    text: page.removing ? "Sprzątam… usługa sprawdza każdy worktree jeszcze raz przed usunięciem."
                        : page.forcing
                          ? "Zaznaczone: " + page.picked.items.length + ", w tym " + page.picked.risky.length
                            + " ze zmianami albo niescalonymi commitami — potwierdzenie wymieni, co przepadnie."
                        : page.picked.items.length > 0
                          ? "Zaznaczone: " + page.picked.items.length + " · usunie foldery worktrees i scalone gałęzie; "
                            + "gałęzie główne i niescalone commity zostają."
                        : "Zaznacz worktrees do usunięcia."
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
                Tide.Button {
                    text: "Odśwież"
                    enabled: page.available
                    onClicked: page.refreshRequested()
                }
                Tide.Button {
                    id: removeButton
                    enabled: page.picked.items.length > 0 && !page.removing && page.available
                    text: page.picked.items.length > 0
                          ? "Usuń zaznaczone (" + page.picked.items.length + ") · " + Tide.Theme.formatBytes(page.picked.bytes)
                          : "Usuń zaznaczone"
                    variant: "danger"
                    onClicked: page.askRemove()
                }
            }
        }
    }

    // --- potwierdzenie --------------------------------------------------------------------------
    Rectangle {
        id: confirm
        anchors.fill: parent
        visible: page.confirming
        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.72)

        MouseArea { anchors.fill: parent; onClicked: page.confirming = false }

        Rectangle {
            id: dialog
            objectName: "confirmDialog"
            anchors.centerIn: parent
            width: Math.min(page.forcing ? 760 : 680, parent.width - 48)
            height: Math.min(dialogBody.implicitHeight + 40, parent.height - 48)
            radius: Tide.Tokens.radiusCard
            color: Tide.Tokens.backgroundCard
            border.width: page.forcing ? 2 : 1
            border.color: Tide.Theme.alpha(Tide.Tokens.negative, page.forcing ? 0.9 : 0.35)
            Accessible.role: Accessible.Dialog
            Accessible.name: page.forcing ? "Potwierdzenie usunięcia mimo ryzyka" : "Potwierdzenie usunięcia"

            MouseArea { anchors.fill: parent }  // kliknięcie w okno potwierdzenia go nie zamyka

            ColumnLayout {
                id: dialogBody
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: page.forcing
                          ? "⚠ Usunąć mimo to " + Logic.count(page.picked.items.length, "worktree", "worktrees", "worktrees")
                            + "? " + (page.picked.risky.length === page.picked.items.length
                                      ? (page.picked.risky.length === 1 ? "Nie jest sugerowany" : "Żaden nie jest sugerowany")
                                      : page.picked.risky.length + " z nich nie " + (page.picked.risky.length === 1 ? "jest sugerowany"
                                                                                                                    : "są sugerowane"))
                            + " do usunięcia."
                          : "Usunąć " + Logic.count(page.picked.items.length, "worktree", "worktrees", "worktrees")
                            + " i zwolnić ok. " + Tide.Theme.formatBytes(page.picked.bytes) + "?"
                    color: page.forcing ? Tide.Tokens.negative : Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: page.forcing
                          ? "Usunę te katalogi z wymuszeniem (git worktree remove --force), razem z tym, co w nich jest. "
                            + "Na czerwono to, co przepadnie bez kopii — tego nie da się cofnąć. Usługa sprawdzi stan "
                            + "jeszcze raz i nic nie usunie, jeśli od tej chwili przybyło zmian albo commitów."
                          : "Usunę dokładnie te katalogi (git worktree remove, bez wymuszania). Każdy jest scalony "
                            + "z lokalną gałęzią główną i nie ma niezacommitowanych zmian — usługa sprawdzi to jeszcze raz "
                            + "tuż przed usunięciem i pominie te, które się zmieniły."
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }

                Flickable {
                    id: list
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: Math.min(listColumn.implicitHeight, page.forcing ? 420 : 340)
                    clip: true
                    contentHeight: listColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: Tide.ScrollBar {}

                    Column {
                        id: listColumn
                        width: list.width - 12
                        spacing: 10
                        Repeater {
                            model: page.confirming ? page.picked.items : []
                            Column {
                                id: entry
                                required property var modelData
                                readonly property bool risky: Logic.risky(modelData)
                                objectName: "confirmEntry"
                                width: listColumn.width
                                spacing: 2
                                Text {
                                    width: parent.width
                                    elide: Text.ElideRight
                                    text: (entry.risky ? "⚠ " : "• ") + modelData.title + " · " + modelData.repo + "  ("
                                          + Tide.Theme.formatBytes(modelData.size) + ")"
                                    color: entry.risky ? Tide.Tokens.negative : Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    font.weight: Font.Medium
                                }
                                Text {
                                    width: parent.width
                                    leftPadding: 12
                                    elide: Text.ElideMiddle
                                    text: "katalog " + Logic.tildePath(modelData.path, page.homeDir)
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 11
                                }
                                Text {
                                    width: parent.width
                                    leftPadding: 12
                                    visible: !page.forcing
                                    wrapMode: Text.WordWrap
                                    text: Logic.branchFate(modelData)
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 11
                                }
                                // Mocniejsze potwierdzenie: co dokładnie przepadnie (czerwone — bez kopii).
                                Repeater {
                                    model: page.forcing
                                           ? Logic.lossLines(entry.modelData, page.deleteBranches,
                                                             Tide.Theme.agoText(entry.modelData.lastChangeTs, page.nowTs))
                                           : []
                                    Text {
                                        required property var modelData
                                        objectName: modelData.danger ? "lossDanger" : "lossInfo"
                                        width: entry.width
                                        leftPadding: 12
                                        wrapMode: Text.WordWrap
                                        text: (modelData.danger ? "✕ " : "· ") + modelData.text
                                        color: modelData.danger ? Tide.Tokens.negative : Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 12
                                        font.weight: modelData.danger ? Font.DemiBold : Font.Normal
                                    }
                                }
                            }
                        }
                    }
                }

                // „Usuń też gałąź” — tylko gdy któraś z gałęzi nie jest scalona; domyślnie odznaczone.
                Rectangle {
                    id: branchToggle
                    objectName: "deleteBranchesToggle"
                    Layout.fillWidth: true
                    visible: page.forcing && page.unmergedInfo.count > 0
                    implicitHeight: branchRow.implicitHeight + 16
                    radius: Tide.Tokens.radiusFrame
                    color: page.deleteBranches ? Tide.Theme.alpha(Tide.Tokens.negative, 0.12)
                         : branchArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06) : "transparent"
                    border.width: 1
                    border.color: Tide.Theme.alpha(Tide.Tokens.negative, page.deleteBranches ? 0.6 : 0.25)
                    activeFocusOnTab: true
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: branchLabel.text
                    Accessible.checkable: true
                    Accessible.checked: page.deleteBranches
                    Accessible.onToggleAction: page.deleteBranches = !page.deleteBranches
                    Accessible.onPressAction: page.deleteBranches = !page.deleteBranches
                    Keys.onSpacePressed: page.deleteBranches = !page.deleteBranches

                    RowLayout {
                        id: branchRow
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        spacing: 10
                        Rectangle {
                            implicitWidth: 18
                            implicitHeight: 18
                            radius: 5
                            color: page.deleteBranches ? Tide.Tokens.negative : "transparent"
                            border.width: page.deleteBranches ? 0 : 1.5
                            border.color: Tide.Theme.alpha(Tide.Tokens.negative, 0.7)
                            Text {
                                anchors.centerIn: parent
                                visible: page.deleteBranches
                                text: "✓"
                                color: Tide.Tokens.backgroundDesktop
                                font.pixelSize: 12
                                font.weight: Font.Bold
                            }
                        }
                        Text {
                            id: branchLabel
                            objectName: "deleteBranchesLabel"
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            text: Logic.deleteBranchLabel(page.unmergedInfo)
                            color: page.deleteBranches ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            font.weight: Font.Medium
                        }
                    }
                    MouseArea {
                        id: branchArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.deleteBranches = !page.deleteBranches
                    }
                }

                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: page.forcing
                          ? "Nigdy nie usuwam głównego katalogu repozytorium ani gałęzi głównych (master, main, develop), "
                            + "a gałąź niescaloną tylko z polem wyżej."
                          : "Gałęzie główne (master, main, develop), niescalone commity i worktrees ze zmianami zostają."
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    Tide.Button {
                        text: "Anuluj"
                        onClicked: page.confirming = false
                    }
                    Tide.Button {
                        objectName: "confirmButton"
                        text: page.forcing ? "Usuń mimo to"
                                           : "Usuń " + Logic.count(page.picked.items.length, "worktree", "worktrees", "worktrees")
                        variant: "danger"
                        onClicked: page.confirmRemove()
                    }
                }
            }
        }
    }

    // Zmiany worktree w widoku diffa (K-36, PT-360): nakładka na całe okno; Esc i „Zamknij” wracają do listy.
    Loader {
        id: diffLoader
        objectName: "worktreeDiffOverlay"
        anchors.fill: parent
        z: 100
        active: page.diffRequest !== null
        sourceComponent: Component {
            Git.DiffView {
                objectName: "worktreeDiffView"
                request: page.diffRequest
                backend: page.diffBackend
                onCloseRequested: page.closeDiff()
                Component.onCompleted: forceActiveFocus()
            }
        }
    }
}
