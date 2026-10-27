// Zawartość widoku Docker (mock „Tide Docker”, zatwierdzony 27.10.2026): nagłówek z podsumowaniem,
// przełącznik testcontainers (domyślnie ukryte) i szukanie; po lewej grupy compose z kontenerami
// i „Zatrzymane” na końcu, pod nimi szuflada logów; po prawej Zasoby (CPU i RAM) i Porządki.
// Sam nie rozmawia z usługą — dostaje migawkę, Stats() i Disk(), a prośby zgłasza sygnałami; wyniki
// wracają przez finishAction/finishGroup/finishPrune/showLogs. Dzięki temu da się go załadować
// w teście z przykładowymi danymi.
//
// Usuwanie kontenera i porządki idą przez potwierdzenie (wzór z widoku Worktrees): okno wymienia
// dokładnie, co zniknie, a dopiero „Usuń”/„Wyczyść” wysyła prośbę. Potwierdzenie porządków prosi
// o świeży odczyt miejsca (freshDiskRequested → DiskNow) i do jego nadejścia „Wyczyść” jest wyłączone:
// lista z odczytu sprzed np. `docker compose down` gdzie indziej nie zawierałaby świeżo osieroconych
// wolumenów baz, a `volumes/prune` usunąłby je mimo to. Zatrzymanie i restart są odwracalne,
// więc idą od razu — przycisk jest wyłączony, dopóki usługa nie odpowie.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "docker.js" as Logic

Rectangle {
    id: page

    property var snapshot: null
    property var stats: null
    property var disk: null
    property bool serviceUp: true
    property bool preview: false
    property real nowTs: Date.now() / 1000

    signal actionRequested(string id, string action)       // start|stop|restart|remove
    signal groupActionRequested(string project, string action)
    signal logsRequested(string id)                        // Logs(id, 200) do szuflady
    signal openLogsRequested(string id)                    // karta Konsoli z docker logs -f
    signal shellRequested(string id)
    signal pruneRequested(string categories)               // lista JSON z id kategorii
    signal freshDiskRequested()                            // świeży Disk dla potwierdzenia porządków
    signal linkRequested(string url)

    // Stan widoku.
    property string query: ""
    property bool showTc: false
    property var stoppedCollapsed: null      // null — samo (zwinięte przy wielu zatrzymanych)
    property string logsId: ""
    property var logsLines: []
    property string logsError: ""
    property bool logsLoading: false
    property string flashId: ""
    property var busy: ({})                  // id kontenera → akcja
    property var groupBusy: ({})             // projekt → akcja
    property var notes: ({})                 // id kontenera → {text, error}
    property var groupNotes: ({})            // projekt (albo id grupy) → {text, error}
    property var picks: ({})                 // id kategorii porządków → true
    property string confirming: ""           // "", "remove", "prune"
    property string removeId: ""
    property bool pruning: false
    property int diskFreshSerial: 0          // ile świeżych odczytów Disk przyszło (backend.diskFreshSerial)
    property int pruneAskSerial: -1          // stan licznika w chwili otwarcia potwierdzenia
    property real pruneAskedTs: 0
    property var pruneResult: null

    readonly property var problem: Logic.problem(snapshot, serviceUp)
    readonly property bool ready: problem === null
    readonly property var groups: ready ? Logic.displayGroups(snapshot, showTc, query) : []
    readonly property int tcCount: snapshot && snapshot.summary ? (snapshot.summary.testcontainers || 0) : 0
    readonly property int totalCount: snapshot && snapshot.summary ? (snapshot.summary.total || 0) : 0
    readonly property var logsEntry: Logic.findContainer(snapshot, logsId)
    readonly property string logsName: logsEntry ? logsEntry.container.name : logsId
    readonly property var removeEntry: Logic.findContainer(snapshot, removeId)
    readonly property var categories: disk && disk.categories ? disk.categories : []
    readonly property var pruneList: Logic.pruneSummary(categories, picks, snapshot)
    // Odczyt miejsca nowszy niż otwarcie potwierdzenia — dopiero na nim można porządkować.
    readonly property bool pruneDiskFresh: confirming === "prune" && diskFreshSerial > pruneAskSerial
                                           && !!(disk && disk.ok) && (disk.generatedTs || 0) >= Math.floor(pruneAskedTs)
    readonly property bool pruneDiskFailed: confirming === "prune" && diskFreshSerial > pruneAskSerial
                                            && !(disk && disk.ok)
    readonly property bool collapsedStopped: {
        if (stoppedCollapsed !== null) return stoppedCollapsed
        const stopped = groups.find(g => g.kind === "stopped")
        return !!stopped && stopped.total > Logic.STOPPED_COLLAPSE_AFTER && query === ""
    }
    readonly property alias searchText: search.text
    readonly property alias flick: flick

    onQueryChanged: if (search.text !== query) search.text = query

    function focusSearch() {
        search.forceActiveFocus()
        search.selectAll()
    }

    function setIn(map, key, value) {
        const next = Object.assign({}, map)
        if (value === undefined || value === null || value === "") delete next[key]
        else next[key] = value
        return next
    }

    // --- akcje na kontenerach ----------------------------------------------------------------

    function containerAction(id, action) {
        if (action === "logs") return openLogs(id)
        if (action === "shell") return shellRequested(id)
        if (action === "remove") {
            removeId = id
            confirming = "remove"
            return
        }
        runAction(id, action)
    }

    function runAction(id, action) {
        if (busy[id]) return
        busy = setIn(busy, id, action)
        notes = setIn(notes, id, null)
        actionRequested(id, action)
    }

    function confirmRemove() {
        const id = removeId
        confirming = ""
        removeId = ""
        if (id) runAction(id, "remove")
    }

    function finishAction(id, action, result) {
        busy = setIn(busy, id, null)
        notes = setIn(notes, id, Logic.actionNote(action, result))
        if (result && result.ok && action === "remove" && logsId === id) closeLogs()
    }

    function groupAction(group, action) {
        if (action === "logs") {
            const target = Logic.logsTarget(group)
            if (target) openLogsRequested(target.id)
            return
        }
        if (groupBusy[group.project]) return
        groupBusy = setIn(groupBusy, group.project, action)
        groupNotes = setIn(groupNotes, group.project, null)
        groupActionRequested(group.project, action)
    }

    function finishGroup(project, action, result) {
        groupBusy = setIn(groupBusy, project, null)
        groupNotes = setIn(groupNotes, project, Logic.groupNote(action, result))
    }

    // Karta Konsoli się nie otworzyła (OpenLogs/OpenShell → false).
    function openFailed(what, id) {
        const text = what === "shell" ? "nie udało się otworzyć powłoki w Konsoli" + (preview ? " (podgląd)" : "")
                                      : "nie udało się otworzyć logów w Konsoli" + (preview ? " (podgląd)" : "")
        if (logsId === id && what === "logs") logsError = text
        else notes = setIn(notes, id, { text: text, error: true })
    }

    // --- logi ------------------------------------------------------------------------------------

    function openLogs(id) {
        if (logsId === id) {
            closeLogs()
            return
        }
        logsId = id
        logsLines = []
        logsError = ""
        logsLoading = true
        logsRequested(id)
    }

    function refreshLogs() {
        if (logsId === "" || logsLoading) return
        logsLoading = true
        logsRequested(logsId)
    }

    function closeLogs() {
        logsId = ""
        logsLines = []
        logsError = ""
        logsLoading = false
    }

    function showLogs(id, result) {
        if (id !== logsId) return
        logsLoading = false
        if (result && result.ok) {
            logsLines = result.lines || []
            logsError = ""
        } else {
            logsError = result && result.error ? result.error : "brak odpowiedzi usługi"
        }
    }

    // --- porządki -------------------------------------------------------------------------------

    function togglePick(id) {
        picks = setIn(picks, id, picks[id] === true ? null : true)
    }

    function askPrune() {
        if (Logic.pickedIds(categories, picks).length === 0 || pruning) return
        pruneAskSerial = diskFreshSerial
        pruneAskedTs = Date.now() / 1000
        confirming = "prune"
        freshDiskRequested()
    }

    function confirmPrune() {
        if (!pruneDiskFresh) return  // jeszcze bez świeżego odczytu — nic nie wysyłamy
        const ids = Logic.pickedIds(categories, picks)
        confirming = ""
        if (ids.length === 0) return
        pruning = true
        pruneResult = null
        pruneRequested(JSON.stringify(ids))
    }

    function finishPrune(result) {
        pruning = false
        pruneResult = Logic.pruneResult(result)
        if (result && result.ok) picks = ({})
    }

    // --- otwarcie na elemencie ------------------------------------------------------------------

    // Kontener (ID, początek ID, nazwa) albo grupa („compose:cookapp”, nazwa projektu): odsłania go
    // (testcontainers, szukanie, zwinięte „Zatrzymane”), przewija i na chwilę podświetla. false — nie ma.
    function reveal(item) {
        const entry = Logic.findContainer(snapshot, item)
        const group = entry ? null : Logic.findGroup(snapshot, item)
        if (!entry && !group) return false
        if ((entry ? entry.group.kind : group.kind) === "testcontainers") showTc = true
        if (entry) {
            if (query && !Logic.matches(entry.container, entry.group, query)) query = ""
            if (Logic.shownGroupId(entry) === "stopped" && !entry.container.failed && collapsedStopped) stoppedCollapsed = false
            flashId = entry.container.id
        } else {
            query = ""
            flashId = group.id
        }
        flashTimer.restart()
        Qt.callLater(scrollToFlash)
        return true
    }

    function scrollToFlash() {
        for (let i = 0; i < sections.count; i++) {
            const section = sections.itemAt(i)
            if (!section) continue
            const target = section.group.id === flashId ? section : section.rowFor(flashId)
            if (target) {
                const y = target.mapToItem(groupsColumn, 0, 0).y
                flick.contentY = Math.max(0, Math.min(y - 24, flick.contentHeight - flick.height))
                return
            }
        }
    }

    color: Tide.Tokens.backgroundWindow
    focus: true
    Keys.onEscapePressed: {
        if (confirming !== "") confirming = ""
        else if (query !== "") query = ""
        else if (logsId !== "") closeLogs()
    }

    Timer {
        id: flashTimer
        interval: 2600
        onTriggered: page.flashId = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        spacing: 14

        // --- nagłówek ------------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Docker"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 21
                    font.weight: Font.DemiBold
                    Accessible.role: Accessible.Heading
                }
                Text {
                    objectName: "summary"
                    Layout.fillWidth: true
                    text: page.ready ? Logic.summaryText(page.snapshot, page.stats)
                                     : page.problem.title
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                id: tcToggle
                objectName: "tcToggle"
                visible: page.ready && page.tcCount > 0
                implicitHeight: 30
                implicitWidth: tcLabel.implicitWidth + 24
                radius: height / 2
                color: page.showTc ? Tide.Theme.alpha(Tide.Tokens.accent, tcArea.containsMouse ? 0.22 : 0.16)
                     : tcArea.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.12) : Tide.Tokens.backgroundCard
                border.width: activeFocus ? 1 : 0
                border.color: Tide.Tokens.accent
                activeFocusOnTab: visible
                Accessible.role: Accessible.CheckBox
                Accessible.name: "Pokaż testcontainers"
                Accessible.checkable: true
                Accessible.checked: page.showTc
                Accessible.onToggleAction: page.showTc = !page.showTc
                Accessible.onPressAction: page.showTc = !page.showTc
                Keys.onSpacePressed: page.showTc = !page.showTc
                Keys.onReturnPressed: page.showTc = !page.showTc
                Text {
                    id: tcLabel
                    anchors.centerIn: parent
                    text: Logic.tcLabel(page.tcCount, page.showTc)
                    textFormat: Text.PlainText
                    color: page.showTc ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                MouseArea {
                    id: tcArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: page.showTc = !page.showTc
                }
            }

            Rectangle {
                id: searchBox
                Layout.preferredWidth: 230
                Layout.preferredHeight: 36
                radius: Tide.Tokens.radiusFrame
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: search.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                 : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
                Glyph {
                    id: glass
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: 15
                    height: 15
                    name: "search"
                    color: Tide.Tokens.textMuted
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
                    onTextEdited: page.query = text
                    Keys.onEscapePressed: page.query = ""
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "Szukaj kontenerów"
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: search.text === ""
                        text: "Szukaj kontenera, portu…"
                        textFormat: Text.PlainText
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
                    textFormat: Text.PlainText
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

        // --- treść ---------------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12

                Flickable {
                    id: flick
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: page.ready
                    clip: true
                    contentWidth: width
                    contentHeight: groupsColumn.implicitHeight + 8
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

                    ColumnLayout {
                        id: groupsColumn
                        width: flick.width - 10
                        spacing: 14

                        Text {
                            objectName: "emptyText"
                            Layout.fillWidth: true
                            Layout.topMargin: 40
                            visible: page.groups.length === 0
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            text: page.totalCount === 0 ? "Brak kontenerów — Docker działa, ale nic nie jest utworzone."
                                : page.query !== "" ? "Nic nie pasuje do „" + page.query + "”."
                                : "Widać tylko testcontainers — pokaż je przyciskiem u góry."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                        }

                        Repeater {
                            id: sections
                            model: page.groups
                            GroupSection {
                                required property var modelData
                                Layout.fillWidth: true
                                group: modelData
                                nowTs: page.nowTs
                                stats: page.stats
                                collapsed: modelData.kind === "stopped" && page.collapsedStopped
                                logsId: page.logsId
                                flashId: page.flashId
                                busy: page.busy
                                groupBusy: page.groupBusy[modelData.project] || ""
                                notes: page.notes
                                groupNote: page.groupNotes[modelData.project] || null
                                onContainerAction: (id, action) => page.containerAction(id, action)
                                onGroupActionRequested: action => page.groupAction(modelData, action)
                                onLinkRequested: url => page.linkRequested(url)
                                onToggleCollapsed: page.stoppedCollapsed = !page.collapsedStopped
                            }
                        }
                    }
                }

                // Pusty stan: usługa albo Docker nie działa (komunikat z usługi).
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: !page.ready

                    Rectangle {
                        objectName: "problemCard"
                        anchors.centerIn: parent
                        width: Math.min(480, parent.width - 48)
                        height: problemBody.implicitHeight + 56
                        radius: Tide.Tokens.radiusCard
                        color: Tide.Tokens.backgroundView
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

                        ColumnLayout {
                            id: problemBody
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 28
                            spacing: 10
                            Rectangle {
                                Layout.alignment: Qt.AlignHCenter
                                implicitWidth: 52
                                implicitHeight: 52
                                radius: Tide.Tokens.radiusTile
                                color: Tide.Theme.alpha(page.problem && page.problem.busy ? Tide.Tokens.accent : Tide.Tokens.textMuted, 0.10)
                                Glyph {
                                    anchors.centerIn: parent
                                    width: 26
                                    height: 26
                                    name: "docker"
                                    color: page.problem && page.problem.busy ? Tide.Tokens.accent : Tide.Tokens.textMuted
                                }
                            }
                            Text {
                                objectName: "problemTitle"
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: page.problem ? page.problem.title : ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                objectName: "problemText"
                                Layout.fillWidth: true
                                visible: text !== ""
                                horizontalAlignment: Text.AlignHCenter
                                text: page.problem ? page.problem.text : ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                LogsDrawer {
                    id: drawer
                    objectName: "logsDrawer"
                    Layout.fillWidth: true
                    Layout.rightMargin: 10  // w linii z wierszami (miejsce na pasek przewijania)
                    visible: page.logsId !== "" && page.ready
                    name: page.logsName
                    lines: page.logsLines
                    error: page.logsError
                    loading: page.logsLoading
                    onOpenInKonsole: page.openLogsRequested(page.logsId)
                    onCloseRequested: page.closeLogs()
                }
            }

            ColumnLayout {
                Layout.preferredWidth: 300
                Layout.maximumWidth: 300
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignTop
                visible: page.ready
                spacing: 14

                ResourcesCard {
                    Layout.fillWidth: true
                    stats: page.stats
                    disk: page.disk
                    snapshot: page.snapshot
                }
                CleanupCard {
                    id: cleanup
                    Layout.fillWidth: true
                    disk: page.disk
                    picks: page.picks
                    pruning: page.pruning
                    result: page.pruneResult
                    onToggled: id => page.togglePick(id)
                    onCleanRequested: page.askPrune()
                    onResultDismissed: page.pruneResult = null
                }
                Item { Layout.fillHeight: true }
            }
        }
    }

    // --- potwierdzenia ------------------------------------------------------------------------
    Rectangle {
        id: confirm
        anchors.fill: parent
        visible: page.confirming !== ""
        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.72)

        MouseArea { anchors.fill: parent; onClicked: page.confirming = "" }

        Rectangle {
            id: dialog
            objectName: "confirmDialog"
            anchors.centerIn: parent
            width: Math.min(620, parent.width - 48)
            height: Math.min(dialogBody.implicitHeight + 40, parent.height - 48)
            radius: Tide.Tokens.radiusCard
            color: Tide.Tokens.backgroundCard
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.negative, 0.45)
            Accessible.role: Accessible.Dialog
            Accessible.name: page.confirming === "prune" ? "Potwierdzenie porządków" : "Potwierdzenie usunięcia kontenera"

            MouseArea { anchors.fill: parent }  // kliknięcie w okno potwierdzenia go nie zamyka

            ColumnLayout {
                id: dialogBody
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    objectName: "confirmTitle"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: page.confirming === "prune"
                          ? (page.pruneDiskFresh ? "Wyczyścić zaznaczone i odzyskać ok. "
                                                   + Logic.bytes(Logic.pickedBytes(page.categories, page.picks)) + "?"
                                                 : "Wyczyścić zaznaczone?")
                          : "Usunąć kontener " + (page.removeEntry ? page.removeEntry.container.name : page.removeId) + "?"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                }

                // Usunięcie kontenera: co dokładnie zniknie, a co zostaje.
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.confirming === "remove"
                    spacing: 6
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: page.removeEntry ? Logic.stateLine(page.removeEntry.container, page.nowTs).text : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Usunę tylko ten zatrzymany kontener (docker rm, bez wymuszania). Jego wolumeny i obraz "
                              + "zostają, a usługa sprawdzi jeszcze raz, że kontener nie działa."
                              + (page.removeEntry && page.removeEntry.group.kind === "compose"
                                 ? " Projekt " + page.removeEntry.group.label + " odtworzy go przy następnym docker compose up." : "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                    }
                }

                // Porządki: lista dokładnie tego, co zniknie, w kolejności usuwania — ze świeżego odczytu.
                Text {
                    objectName: "pruneIntro"
                    Layout.fillWidth: true
                    visible: page.confirming === "prune"
                    wrapMode: Text.WordWrap
                    text: page.pruneDiskFailed ? "Nie udało się policzyć miejsca od nowa: "
                                                 + (page.disk && page.disk.error ? page.disk.error : "brak odpowiedzi usługi")
                                                 + ". Bez świeżego odczytu nic nie wyczyszczę."
                        : !page.pruneDiskFresh ? "Liczę od nowa, co jest do odzyskania — Docker mógł się zmienić od ostatniego "
                                                 + "odczytu, a lista ma pokazać dokładnie to, co zniknie."
                        : page.pruneList.length === 0 ? "Zaznaczonych kategorii nie ma już czego odzyskać."
                        : "Usunę dokładnie te kategorie, w tej kolejności. Docker sam omija wszystko, czego używa "
                          + "działający kontener."
                    textFormat: Text.PlainText
                    color: page.pruneDiskFailed ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
                Flickable {
                    id: pruneFlick
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(pruneColumn.implicitHeight, 360)
                    visible: page.confirming === "prune" && page.pruneDiskFresh
                    clip: true
                    contentHeight: pruneColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                    Column {
                        id: pruneColumn
                        width: pruneFlick.width - 12
                        spacing: 10
                        Repeater {
                            model: page.confirming === "prune" && page.pruneDiskFresh ? page.pruneList : []
                            Column {
                                id: entry
                                required property var modelData
                                required property int index
                                objectName: "pruneEntry"
                                width: pruneColumn.width
                                spacing: 2
                                RowLayout {
                                    width: parent.width
                                    Text {
                                        Layout.fillWidth: true
                                        text: (entry.index + 1) + ". " + entry.modelData.title
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        font.weight: Font.Medium
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: entry.modelData.size
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textSecondary
                                        font.family: Tide.Theme.monoFamily
                                        font.pixelSize: 12
                                    }
                                }
                                Repeater {
                                    model: entry.modelData.lines
                                    Text {
                                        required property var modelData
                                        objectName: modelData.warn ? "pruneWarn" : "pruneInfo"
                                        width: entry.width
                                        leftPadding: 16
                                        wrapMode: Text.WordWrap
                                        text: (modelData.warn ? "⚠ " : "· ") + modelData.text
                                        textFormat: Text.PlainText
                                        color: modelData.warn ? Tide.Tokens.neutral : Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 12
                                        font.weight: modelData.warn ? Font.DemiBold : Font.Normal
                                    }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    PillButton {
                        text: "Anuluj"
                        fill: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
                        onClicked: page.confirming = ""
                    }
                    PillButton {
                        objectName: "confirmButton"
                        text: page.confirming !== "prune" ? "Usuń kontener"
                            : page.pruneDiskFailed ? "Nie mogę policzyć"
                            : !page.pruneDiskFresh ? "liczę…"
                            : page.pruneList.length === 0 ? "Nic do wyczyszczenia"
                            : "Wyczyść · " + Logic.bytes(Logic.pickedBytes(page.categories, page.picks))
                        enabled: page.confirming !== "prune" || (page.pruneDiskFresh && page.pruneList.length > 0)
                        fill: Tide.Tokens.negative
                        textColor: Tide.Tokens.backgroundDesktop
                        strong: true
                        onClicked: page.confirming === "prune" ? page.confirmPrune() : page.confirmRemove()
                    }
                }
            }
        }
    }
}
