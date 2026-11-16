// Widok „Raporty” w oknie Tide: raporty poranne bez przeglądarki. Dane i akcje daje obiekt backend
// z backend.py (/Reports w tide-daemon albo stały plik przy --data). Widok nie czyta i nie renderuje
// HTML-a raportu — dostaje report.json doby (Day()) i pokazuje go jako zwykły tekst.
//
// Układ (mock ReportsView): po lewej doby (od najnowszej, „Dziś · wt 27.10”, brzoskwiniowa kropka przy
// nieotwartym raporcie, „Trzymamy 30 dni”), w środku tytuł doby z linią „od kiedy · ile projektów · co
// czeka”, „Wygeneruj teraz” i „Otwórz w przeglądarce ↗”, „Dziś w skrócie” z „Czeka teraz” (na żywo, jak
// widżet agentów), karty projektów (czekające pierwsze), zwinięta linia „Porządki: …” i „Poza projektami”; po prawej „Na daily” wybranego projektu z „Kopiuj jako tekst” i „Pełna strona ↗”.
//
// Raport bieżącej doby żyje: usługa odświeża go po zmianach w pracy i przelicza „czeka” przy każdym
// odczycie, a jej sygnał Changed sam przeładowuje widok (backend.reload) — bez „Wygeneruj teraz”.
//
// Przeczytanie (decyzja 51): obejrzenie najnowszego, nieotwartego raportu w oknie — widok na wierzchu
// w aktywnym oknie przez readAfterMs — woła backend.markRead(doba); usługa zapisuje przeczytanie
// i kropka gaśnie wszędzie (pasek okna, widżety). Starsze doby niczego nie zmieniają, a raport sprzed
// report.json (bez „Na daily” w oknie) też nie — jego treść jest tylko w przeglądarce.
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (z --preview: project —
// slug wybranego projektu, expand=1 — rozwinięte „Poza projektami”), showItem(item) — „RRRR-MM-DD”
// albo „RRRR-MM-DD/slug”, takeFocus(), railInfo — katalog raportów na dole paska.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "reports.js" as Logic

Rectangle {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    property bool windowActive: Window.active
    property int readAfterMs: 1500

    readonly property var days: backend ? Logic.parse(backend.daysJson, ({})) : ({})
    readonly property var dayList: Array.isArray(days.days) ? days.days : []
    readonly property var report: backend ? Logic.parse(backend.dayJson, ({})) : ({})
    readonly property bool hasReport: report.ok === true
    readonly property bool legacy: hasReport && report.legacy === true
    readonly property var projects: hasReport && Array.isArray(report.projects) ? report.projects : []
    readonly property var outside: hasReport && Array.isArray(report.outside) ? report.outside : []
    readonly property var brief: hasReport && !legacy ? Logic.briefLines(report) : []
    readonly property var nowWaiting: hasReport && !legacy ? Logic.nowItems(report) : []
    readonly property var choreProjects: hasReport && !legacy ? Logic.choreProjects(report) : []
    property bool showChoreProjects: false
    readonly property string selectedDay: backend ? backend.selectedDay : ""
    property string selectedKey: ""
    readonly property var selectedProject: Logic.findProject(projects, selectedKey)
    readonly property bool available: backend ? backend.available : false
    readonly property bool generating: backend ? backend.generating : false
    readonly property bool canAct: backend ? !backend.preview : false
    property string pendingDay: ""
    property string pendingProject: ""
    property string notice: ""
    property bool loaded: false
    readonly property alias outsideSection: outsideSection
    readonly property alias panel: panel

    color: Tide.Tokens.backgroundWindow

    readonly property var railInfo: days.root ? {
        label: "Katalog raportów",
        value: Logic.tildePath(days.root, backend ? backend.homeDir : ""),
        note: "Pliki lokalne, tylko na tym komputerze"
    } : null

    // Otwarcie na dobie albo projekcie doby: „2026-10-27” lub „2026-10-27/plasma-tide”.
    function showItem(item) {
        const target = Logic.splitItem(item)
        if (!target.day) return
        pendingProject = target.project
        if (target.day === selectedDay && hasReport) {
            pendingDay = ""
            pendingProject = ""
            if (target.project) selectedKey = target.project   // ta sama doba — od razu projekt
        } else if (dayList.some(d => d.day === target.day)) {
            pendingDay = ""
            selectDay(target.day)
        } else {
            pendingDay = target.day   // lista dni jeszcze nie przyszła
        }
    }

    function takeFocus() {
        view.forceActiveFocus()
    }

    function loadOnce() {
        if (loaded || !active || !backend) return
        loaded = true
        backend.load()
    }

    function selectDay(day) {
        if (backend && day) backend.selectDay(day)
    }

    function step(delta) {
        const i = dayList.findIndex(d => d.day === selectedDay)
        const next = dayList[Math.max(0, Math.min(dayList.length - 1, i + delta))]
        if (next) selectDay(next.day)
    }

    function copy(text) {
        if (backend && backend.copyText(text)) panel.copied = true
        else notice = "Nie udało się skopiować do schowka"
    }

    function previewValue(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return ""
        if (typeof value === "string") return value
        return value.length ? String(value[0]) : ""
    }

    onActiveChanged: loadOnce()
    onBackendChanged: loadOnce()
    Component.onCompleted: {
        loadOnce()
        if (previewValue("project") !== "") selectedKey = previewValue("project")
        if (previewValue("expand") !== "") outsideSection.expanded = true
    }
    onDayListChanged: {
        if (pendingDay !== "" && dayList.some(d => d.day === pendingDay)) {
            const day = pendingDay
            pendingDay = ""
            selectDay(day)
        }
    }
    // Nowa doba w panelu: projekt z showItem, inaczej ten sam projekt co wcześniej (jeśli jest), inaczej pierwszy.
    onReportChanged: {
        if (!hasReport) return
        if (pendingProject !== "" && report.day === selectedDay) {
            selectedKey = pendingProject
            pendingProject = ""
        }
    }

    Keys.onUpPressed: step(-1)
    Keys.onDownPressed: step(1)

    // Najnowszy, nieotwarty raport obejrzany w oknie → przeczytany w usłudze.
    Timer {
        id: readTimer
        interval: view.readAfterMs
        running: view.active && view.windowActive && view.hasReport && !view.legacy && view.report.latest === true
                 && view.report.unread === true && !!view.backend
        onTriggered: view.backend.markRead(view.report.day)
    }

    Connections {
        target: view.backend
        function onGenerateFinished(path) {
            view.notice = path ? "" : (view.canAct ? "Nie udało się złożyć raportu — szczegóły w dzienniku tide-daemon"
                                                   : "Podgląd z pliku — bez składania raportu")
        }
        function onOpenFinished(ok) {
            view.notice = ok ? "" : (view.canAct ? "Nie udało się otworzyć przeglądarki"
                                                 : "Podgląd z pliku — bez otwierania przeglądarki")
        }
    }

    // --- pusty stan ------------------------------------------------------------------------------

    ColumnLayout {
        objectName: "emptyState"
        anchors.centerIn: parent
        width: Math.min(parent.width - 80, 520)
        visible: view.loaded && view.dayList.length === 0
        spacing: 12

        Text {
            objectName: "emptyTitle"
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: view.available ? "Nie ma jeszcze raportów" : "Usługa tide-daemon nie odpowiada"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 19
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: view.available
                  ? "Raport poranny powstaje raz na dobę — przy pierwszym logowaniu albo o 07:00. "
                    + "Możesz złożyć go teraz: zajmie to do kilku minut."
                  : "Raporty pokazuje usługa tide-daemon. Gdy ruszy, lista dni pojawi się tutaj sama."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            lineHeight: 1.25
            wrapMode: Text.Wrap
        }
        Tide.Button {
            Layout.alignment: Qt.AlignHCenter
            visible: view.available
            enabled: !view.generating
            text: view.generating ? "Składam raport…" : "Wygeneruj teraz"
            onClicked: view.backend.generate()
        }
    }

    // --- raporty ---------------------------------------------------------------------------------

    RowLayout {
        anchors.fill: parent
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        spacing: 20
        visible: view.dayList.length > 0

        ColumnLayout {
            Layout.preferredWidth: 186
            Layout.minimumWidth: 186
            Layout.maximumWidth: 186
            Layout.fillHeight: true
            spacing: 4

            Text {
                Layout.leftMargin: 10
                Layout.topMargin: 4
                Layout.bottomMargin: 4
                text: "RAPORTY"
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                font.letterSpacing: 0.7
            }
            ListView {
                id: dayView
                objectName: "dayList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.maximumHeight: contentHeight
                clip: true
                spacing: 4
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                model: view.dayList
                delegate: DayItem {
                    required property var modelData
                    width: ListView.view.width
                    label: Logic.dayLabel(modelData.day, view.days.today)
                    sub: Logic.daySub(modelData)
                    fresh: Logic.unreadDay(modelData.day, view.days)
                    selected: modelData.day === view.selectedDay
                    onClicked: view.selectDay(modelData.day)
                }
            }
            Text {
                objectName: "keepDays"
                Layout.leftMargin: 10
                Layout.topMargin: 4
                text: "Trzymamy " + Logic.count(view.days.keepDays || 30, "dzień", "dni", "dni")
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
            }
            Item { Layout.fillHeight: true }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Text {
                        objectName: "dayTitle"
                        Layout.fillWidth: true
                        text: view.hasReport ? Logic.dayTitle(view.report, view.days.today)
                                             : Logic.dayLabel(view.selectedDay, view.days.today)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 21
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Row {
                        objectName: "summaryLine"
                        Layout.fillWidth: true
                        spacing: 0
                        Repeater {
                            model: Logic.summaryParts(view.report)
                            delegate: Text {
                                required property var modelData
                                required property int index
                                text: (index > 0 ? " · " : "") + modelData.text
                                textFormat: Text.PlainText
                                color: modelData.tone === "waiting" ? Tide.Tokens.waiting : Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                            }
                        }
                    }
                }
                Tide.Button {
                    objectName: "generateButton"
                    enabled: !view.generating && view.available
                    text: view.generating ? "Składam raport…" : "Wygeneruj teraz"
                    toolTip: "Raport bieżącej doby teraz (ten sam początek okresu co poranny)"
                    onClicked: view.backend.generate()
                }
                Tide.Button {
                    objectName: "openButton"
                    enabled: view.hasReport
                    text: "Otwórz w przeglądarce ↗"
                    onClicked: view.backend.openDay(view.selectedDay, "")
                }
            }

            // Składanie raportu w toku: pasek bez końca i zdanie, ile to trwa.
            ColumnLayout {
                objectName: "progress"
                Layout.fillWidth: true
                visible: view.generating
                spacing: 6
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 3
                    radius: 2
                    clip: true
                    color: Tide.Theme.alpha(Tide.Tokens.accent, 0.12)
                    Rectangle {
                        id: runner
                        width: parent.width * 0.3
                        height: parent.height
                        radius: 2
                        color: Tide.Tokens.accent
                        NumberAnimation on x {
                            running: view.generating
                            loops: Animation.Infinite
                            from: -runner.width
                            to: runner.parent ? runner.parent.width : 0
                            duration: 1400
                        }
                    }
                }
                Text {
                    text: "Składam raport bieżącej doby — git, dzienniki agentów i opis „Na daily”; to może potrwać do kilku minut."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
            }

            Text {
                objectName: "notice"
                Layout.fillWidth: true
                visible: view.notice !== ""
                text: view.notice
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 18

                Flickable {
                    id: cards
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentHeight: cardColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: Tide.ScrollBar {}

                    ColumnLayout {
                        id: cardColumn
                        width: cards.width
                        spacing: 10

                        Text {
                            objectName: "dayError"
                            Layout.fillWidth: true
                            visible: view.selectedDay !== "" && view.report.ok === false
                            text: view.report.error === "missing"
                                  ? "Raportu z tej doby już nie ma — usługa trzyma raporty przez "
                                    + Logic.count(view.days.keepDays || 30, "dzień", "dni", "dni") + "."
                                  : "Nie udało się pobrać raportu z usługi."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                        }
                        Text {
                            objectName: "noProjects"
                            Layout.fillWidth: true
                            visible: view.hasReport && view.projects.length === 0
                            text: "W projektach nic się nie dzieje — nie ma nowej pracy ani pracy w toku."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                        }
                        // „Dziś w skrócie”: 3–5 zdań o całym dniu i — na żywo, jak widżet agentów — co czeka teraz.
                        Rectangle {
                            objectName: "briefBox"
                            Layout.fillWidth: true
                            visible: view.brief.length > 0 || view.nowWaiting.length > 0
                            implicitHeight: briefColumn.implicitHeight + 26
                            radius: Tide.Tokens.radiusBar
                            color: Tide.Tokens.backgroundCard
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

                            ColumnLayout {
                                id: briefColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 13
                                anchors.leftMargin: 16
                                anchors.rightMargin: 16
                                spacing: 6
                                Text {
                                    text: "DZIŚ W SKRÓCIE"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 11
                                    font.letterSpacing: 0.7
                                }
                                Repeater {
                                    model: view.brief
                                    delegate: Text {
                                        required property var modelData
                                        objectName: "briefLine"
                                        Layout.fillWidth: true
                                        text: String(modelData)
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 14
                                        lineHeight: 1.3
                                        wrapMode: Text.Wrap
                                    }
                                }
                                ColumnLayout {
                                    objectName: "nowBox"
                                    Layout.fillWidth: true
                                    Layout.topMargin: view.brief.length > 0 ? 4 : 0
                                    visible: view.nowWaiting.length > 0
                                    spacing: 4
                                    RowLayout {
                                        spacing: 7
                                        Dot { tint: "waiting"; size: 8; pulse: true }
                                        Text {
                                            objectName: "nowTitle"
                                            text: "Czeka teraz · " + view.nowWaiting.length
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.waiting
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    Repeater {
                                        model: view.nowWaiting
                                        delegate: Text {
                                            required property var modelData
                                            objectName: "nowLine"
                                            Layout.fillWidth: true
                                            Layout.leftMargin: 15
                                            text: modelData.project + " — " + modelData.text
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textSecondary
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 13
                                            lineHeight: 1.2
                                            wrapMode: Text.Wrap
                                            maximumLineCount: 2
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }
                        Repeater {
                            id: cardRepeater
                            model: view.projects
                            delegate: ProjectCard {
                                required property var modelData
                                Layout.fillWidth: true
                                project: modelData
                                legacy: view.legacy
                                selected: !!view.selectedProject && view.selectedProject.slug === modelData.slug
                                onClicked: view.selectedKey = modelData.slug
                            }
                        }
                        // Projekty z samymi porządkami (instrukcje agentów, tracker, konfiguracja) — jedna zwinięta linia.
                        ColumnLayout {
                            objectName: "choreProjects"
                            Layout.fillWidth: true
                            Layout.leftMargin: 4
                            visible: view.choreProjects.length > 0
                            spacing: 4
                            Text {
                                objectName: "choreLine"
                                Layout.fillWidth: true
                                text: (view.showChoreProjects ? "▾ " : "▸ ") + Logic.choreLine(view.report, 4)
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                activeFocusOnTab: parent.visible
                                Keys.onReturnPressed: view.showChoreProjects = !view.showChoreProjects
                                Keys.onSpacePressed: view.showChoreProjects = !view.showChoreProjects
                                Accessible.role: Accessible.Button
                                Accessible.name: text
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: view.showChoreProjects = !view.showChoreProjects
                                }
                            }
                            Repeater {
                                model: view.showChoreProjects ? view.choreProjects : []
                                delegate: Text {
                                    required property var modelData
                                    objectName: "choreProject"
                                    Layout.fillWidth: true
                                    Layout.leftMargin: 15
                                    text: Logic.projectLabel(modelData) + ": " + Logic.list(modelData.chores).map(c => c.text).join("; ")
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        OutsideSection {
                            id: outsideSection
                            objectName: "outside"
                            Layout.fillWidth: true
                            visible: view.outside.length > 0
                            sessions: view.outside
                            homeDir: view.backend ? view.backend.homeDir : ""
                        }
                    }
                }

                DailyPanel {
                    id: panel
                    objectName: "dailyPanel"
                    Layout.preferredWidth: 360
                    Layout.fillHeight: true
                    visible: view.selectedProject !== null
                    project: view.selectedProject
                    legacy: view.legacy
                    canOpen: view.canAct
                    onCopyRequested: text => view.copy(text)
                    onPageRequested: slug => view.backend.openDay(view.selectedDay, slug)
                }
            }
        }
    }

    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) view.backend.reload()
    }
}
