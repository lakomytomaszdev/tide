// Karta tablicy według standardu okna Tide (makieta S-9, `.bcard`, karta K-128 / PT-505): wiersz meta — numer
// w Geist Mono (sky), projekt, pastylki dodatkowych projektów i znacznik YouTracka (ze stanem synchronizacji, PT-7),
// tytuł 13 px/600, tagi jako pastylki z kropkami, niżej znacznik fali, pasek ticketów, notatka, a gdy kartę prowadzi
// koordynator (derived, PT-5) — pasek postępu, „● Claude · koordynator · 3 z 10” i akcje kolumny w `Tide.Button`
// (Uruchom agenta…, Przejdź do agenta, Zmiany gałęzi, Gotowe) oraz linki (Przejdź do agenta, Otwórz w Sesjach).
// W Gotowych zwarta wersja (compact): „K-10 · projekt” i tytuł (3 linie).
// Czekająca — brzoskwiniowe tło i ramka; zaznaczona i z fokusem — obwódka akcentu z poświatą; najechana —
// jaśniejsze tło i obrys `outline`; przeciągana — krycie 0,35; duch przeciągania — bez przycisków.
// Kartę w kolumnie ręcznej da się przeciągnąć (dragHost: strona tablicy z duchem karty).
// Test inwentarza (tests/test_card_inventory.py) pilnuje, żeby odświeżenie wyglądu nie zgubiło żadnego elementu,
// podpowiedzi ani opisu dla czytnika ekranu. Teksty to dane: zawsze Text.PlainText.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: root

    property var card: ({})
    property bool compact: false
    property bool selected: false
    property bool canLaunch: false      // „Uruchom agenta…” w Do zrobienia (okno „Co ma zrobić agent?”)
    property bool canDiff: false        // K-36 #17: „Zmiany gałęzi” w Do sprawdzenia
    property var tagTone: null          // funkcja(nazwa) → kolor kropki tagu
    property var dragHost: null         // strona tablicy (BoardPage) — duch karty i upuszczanie
    property bool ghost: false          // kopia karty pod kursorem w czasie przeciągania
    signal clicked()
    signal moveRequested(string column)       // „Gotowe” w Do sprawdzenia
    signal sessionRequested(string session)   // „Otwórz w Sesjach”: koordynator karty w widoku Sesje
    signal agentRequested(string session)     // „Przejdź do agenta”: okno agenta (Konsola, wątek Codexa)
    signal launchRequested()
    signal diffRequested()
    signal keyNavigation(int dx, int dy)      // strzałki: sąsiednia karta
    signal keyMove(int dx, int dy)            // Ctrl+strzałki: przenieś kartę

    readonly property var c: card || ({})   // karta albo pusty obiekt (delegat w chwili zmiany modelu)
    readonly property string column: Logic.displayColumn(root.c)
    readonly property bool waiting: column === "waiting"
    readonly property var progress: Logic.progress(root.c)
    readonly property string note: Logic.cardNote(root.c)
    readonly property var derived: root.c.derived || null
    readonly property bool canDrag: !ghost && dragHost !== null && Logic.draggable(root.c)
    readonly property bool dragged: dragHost !== null && dragHost.dragging && dragHost.dragId === root.c.id && !ghost
    readonly property bool dragActive: area.drag.active
    readonly property string syncGlyph: ytChip.visible ? ytChip.glyph : ""   // znaczek synchronizacji z YouTrackiem (PT-7)

    function tone(name) {
        return tagTone ? tagTone(name) : Tide.Tokens.textMuted
    }

    implicitHeight: (compact ? compactBody.implicitHeight + 16 : body.implicitHeight + 20)
    radius: compact ? 10 : Tide.Tokens.radiusTile
    color: compact ? Tide.Theme.alpha(Tide.Tokens.backgroundCard, area.containsMouse ? 0.95 : 0.7)
         : waiting ? Tide.Theme.tinted(Tide.Tokens.backgroundCard, Tide.Tokens.peach, area.containsMouse ? 0.11 : 0.08)
         : area.containsMouse && !ghost ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
    border.width: compact && !selected && !activeFocus ? 0 : 1
    border.color: selected || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                : waiting ? Tide.Theme.alpha(Tide.Tokens.peach, 0.32)
                : ghost ? Tide.Theme.alpha(Tide.Tokens.accent, 0.45)
                : Tide.Theme.alpha(Tide.Tokens.textPrimary, area.containsMouse ? Tide.Tokens.opacityOutline : Tide.Tokens.opacityBorder)
    opacity: dragged ? 0.35 : 1
    activeFocusOnTab: !ghost
    Component.onCompleted: if (!ghost) Logic.noteCardDelegate()   // licznik pomiarowy (PT-323), duch karty się nie liczy

    Accessible.role: Accessible.Button
    Accessible.name: (root.c.id || "") + " " + (root.c.title || "")
    Accessible.description: [root.c.projectName || "", (root.c.resolvedTags || []).join(", "), root.note]
        .filter(x => x).join(" · ")
    Accessible.onPressAction: root.clicked()
    Keys.onPressed: event => {
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
        const dx = event.key === Qt.Key_Left ? -1 : event.key === Qt.Key_Right ? 1 : 0
        const dy = event.key === Qt.Key_Up ? -1 : event.key === Qt.Key_Down ? 1 : 0
        if (dx !== 0 || dy !== 0) {
            if (ctrl) root.keyMove(dx, dy)
            else root.keyNavigation(dx, dy)
            event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
            root.clicked()
            event.accepted = true
        }
    }

    // Poświata zaznaczenia jak w widoku Sesje.
    Rectangle {
        visible: root.selected
        anchors.fill: parent
        anchors.margins: -3
        radius: root.radius + 3
        color: "transparent"
        border.width: 3
        border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.10)
    }

    // --- zwarta karta (Gotowe) ------------------------------------------------------------------
    ColumnLayout {
        id: compactBody
        visible: root.compact
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 9
        anchors.topMargin: 8
        spacing: 3

        RowLayout {
            Layout.fillWidth: true
            spacing: 0
            Text {
                text: root.c.id || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
            }
            Text {
                Layout.fillWidth: true
                visible: (root.c.projectName || "") !== ""
                text: " · " + (root.c.projectName || "")
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                elide: Text.ElideRight
            }
        }
        Text {
            Layout.fillWidth: true
            text: root.c.title || ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            lineHeight: 1.15
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
    }

    // --- pełna karta ----------------------------------------------------------------------------
    ColumnLayout {
        id: body
        visible: !root.compact
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 11
        anchors.rightMargin: 11
        anchors.topMargin: 10
        spacing: 6

        // Wiersz meta (`.bcard .meta`): numer, projekt, pastylki dodatkowych projektów i znacznik YouTracka. Zawija się
        // do drugiej linii w wąskiej kolumnie, zamiast ściskać nazwę projektu albo wystawać poza kafelek.
        Flow {
            id: meta
            Layout.fillWidth: true
            spacing: 6
            Text {
                id: idText
                text: root.c.id || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            Text {
                visible: text !== ""
                width: Math.min(implicitWidth, Math.max(40, meta.width - idText.implicitWidth - meta.spacing))
                text: root.c.projectName || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                elide: Text.ElideRight
            }
            // Dodatkowe projekty karty (PT-28): małe chipy „+ mobileapp” obok projektu.
            Repeater {
                model: root.c.extraProjects || []
                Rectangle {
                    required property var modelData
                    objectName: "cardExtraProject"
                    radius: 5
                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                    implicitWidth: extraText.implicitWidth + 10
                    implicitHeight: extraText.implicitHeight + 2
                    Text {
                        id: extraText
                        objectName: "cardExtraProjectText"
                        anchors.centerIn: parent
                        text: "+ " + (parent.modelData.name || parent.modelData.slug || "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 10
                    }
                }
            }
            // Znacznik YouTracka ze stanem synchronizacji (PT-7): ✓ / ⟳ / ⚠; podpowiedź mówi resztę.
            Rectangle {
                id: ytChip
                objectName: "cardYouTrack"
                readonly property var sync: root.c.youtrackSync || null
                readonly property string glyph: Logic.syncGlyph(sync)
                visible: (root.c.youtrack || "") !== ""
                radius: 5
                color: sync && sync.status === "conflict" ? Tide.Theme.alpha(Tide.Tokens.rose, 0.14)
                                                          : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                implicitWidth: ytRow.implicitWidth + 10
                implicitHeight: ytRow.implicitHeight + 2
                Accessible.role: Accessible.StaticText
                Accessible.name: Logic.syncHint(root.c)
                Row {
                    id: ytRow
                    anchors.centerIn: parent
                    spacing: 4
                    Text {
                        id: ytText
                        text: "YT " + (root.c.youtrack || "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 10
                    }
                    Text {
                        objectName: "cardYouTrackSync"
                        visible: ytChip.glyph !== ""
                        text: ytChip.glyph
                        textFormat: Text.PlainText
                        color: Tide.Tokens[Logic.syncTone(ytChip.sync)]
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                    }
                }
                HoverHandler { id: ytHover }
                QQC2.ToolTip.visible: ytHover.hovered && !root.ghost
                QQC2.ToolTip.text: Logic.syncHint(root.c)
                QQC2.ToolTip.delay: 400
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.c.title || ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            font.weight: Font.DemiBold
            lineHeight: 1.2
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
        }

        Flow {
            Layout.fillWidth: true
            visible: (root.c.resolvedTags || []).length > 0
            spacing: 4
            Repeater {
                model: root.c.resolvedTags || []
                TagChip {
                    required property var modelData
                    name: modelData
                    dotColor: root.tone(modelData)
                }
            }
        }

        Text {
            objectName: "cardTileWaveLabel"
            Layout.fillWidth: true
            visible: Boolean(root.derived && root.derived.flow && root.derived.flow.waveLabel)
            text: root.derived && root.derived.flow ? root.derived.flow.waveLabel || "" : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
        }
        RowLayout {
            Layout.fillWidth: true
            visible: Boolean(root.derived && root.derived.flow && root.derived.flow.waveLabel)
            spacing: 3
            Repeater {
                objectName: "cardTicketStrip"
                model: root.derived && root.derived.flow ? root.derived.tickets || [] : []
                Item {
                    id: segment
                    Layout.fillWidth: true
                    implicitHeight: 6
                    readonly property int wave: modelData.wave || 0
                    readonly property bool gap: index > 0 && root.derived.tickets[index - 1].wave !== wave
                    // Stan od usługi (05.11): pracuje / kolejka (ruszy sam) / ty (bez Ciebie nie ruszy) / review /
                    // gotowe / todo. Starsza usługa bez `phase` — dawne stany.
                    readonly property string phase: Logic.ticketPhase(modelData)
                    Rectangle {
                        objectName: "ticketSegment"
                        anchors.fill: parent
                        anchors.leftMargin: parent.gap ? 5 : 0
                        radius: height / 2
                        color: segment.phase === "gotowe" ? Tide.Tokens.mint
                             : segment.phase === "review" ? Tide.Tokens.lavender
                             : segment.phase === "pracuje" ? Tide.Tokens.sky
                             : segment.phase === "ty" ? Tide.Tokens.peach
                             : segment.phase === "kolejka" ? Tide.Theme.alpha(Tide.Tokens.sky, 0.18)
                             : Tide.Theme.alpha(Tide.Tokens.textMuted, 0.3)
                        border.width: segment.phase === "kolejka" ? 1 : 0
                        border.color: Tide.Theme.alpha(Tide.Tokens.sky, 0.75)
                        // Pulsowanie z jednej wspólnej fazy strony (PT-325), bez animacji na segment; w tle faza stoi na 1.
                        // Puls bierzemy ze wspólnej fazy strony; duch przeciąganej karty nie ma dragHost, więc jego segment
                        // „pracuje” ma stałe krycie 1 i odróżnia się samym kolorem.
                        opacity: segment.phase === "pracuje" && root.dragHost ? root.dragHost.workPulse : 1
                    }
                    HoverHandler { id: segmentHover }
                    QQC2.ToolTip.visible: segmentHover.hovered && !root.ghost
                    QQC2.ToolTip.delay: 300
                    QQC2.ToolTip.text: segment.hint
                    readonly property string hint: {
                        const label = ({"pracuje": "pracuje", "kolejka": "w kolejce", "ty": "czeka na Ciebie",
                                        "review": "review", "gotowe": "gotowe", "odrzucone": "odrzucony",
                                        "todo": "nie ruszył"})[segment.phase]
                        const why = modelData.why || ""
                        return (modelData.id || "") + " · "
                            + (!why ? label : segment.phase === "ty" || segment.phase === "pracuje" && !why.startsWith("poza")
                               ? label + ": " + why : why)
                    }
                }
            }
        }
        // Postęp z koordynatora (PT-5).
        ColumnLayout {
            Layout.fillWidth: true
            visible: root.progress !== null
            spacing: 5
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 4
                radius: 2
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.1)
                Rectangle {
                    width: parent.width * (root.progress ? root.progress.ratio : 0)
                    height: parent.height
                    radius: 2
                    color: root.waiting ? Tide.Tokens.peach : Tide.Tokens.teal
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: "●"
                    textFormat: Text.PlainText
                    color: root.waiting ? Tide.Tokens.peach : Tide.Tokens.teal
                    font.pixelSize: 9
                }
                Text {
                    Layout.fillWidth: true
                    text: Logic.who(root.c) + (root.progress ? " · " + root.progress.text : "")
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: root.note !== ""
            text: root.note
            textFormat: Text.PlainText
            color: Tide.Tokens[Logic.noteTint(root.c, root.note)]
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            lineHeight: 1.15
            wrapMode: Text.Wrap
            maximumLineCount: root.waiting ? 4 : 3
            elide: Text.ElideRight
        }

        // Do sprawdzenia: gałąź (lawenda, Geist Mono).
        Rectangle {
            visible: root.column === "review" && root.derived !== null && (root.derived.branch || root.c.branch || "") !== ""
            Layout.maximumWidth: body.width
            radius: 6
            color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.10)
            implicitWidth: Math.min(branchText.implicitWidth + 14, body.width)
            implicitHeight: branchText.implicitHeight + 6
            Text {
                id: branchText
                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - 14)
                text: root.derived ? (root.derived.branch || root.c.branch || "") : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.lavender
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
                elide: Text.ElideMiddle
            }
        }

        // Akcje kolumny (Tide.Button; nazwa i opis dla czytnika ekranu bez zmian względem dawnych PillButton).
        Tide.Button {
            Layout.fillWidth: true
            visible: root.column === "todo" && root.canLaunch && !root.ghost
            small: true
            // Po starcie karta czeka w Do zrobienia, aż koordynator ruszy (PT-5) — wtedy „ponownie” (przycisk tonowany).
            readonly property bool relaunch: (root.c.launched || "") !== ""
            text: relaunch ? "Uruchom ponownie…" : "Uruchom agenta…"
            variant: relaunch ? "normal" : "primary"
            tone: relaunch ? Tide.Tokens.accent : "transparent"
            onClicked: root.launchRequested()
        }
        Tide.Button {
            objectName: "agentButton"
            Layout.fillWidth: true
            visible: root.waiting && root.derived !== null && Logic.agentSession(root.c) !== "" && !root.ghost
            small: true
            text: "Przejdź do agenta"
            tone: Tide.Tokens.peach
            onClicked: root.agentRequested(Logic.agentSession(root.c))
        }
        // Dwa osobne linki: okno agenta (Konsola, wątek Codexa) i koordynator w widoku Sesje okna Tide. Zawijają się
        // w wąskiej kolumnie.
        Flow {
            Layout.fillWidth: true
            visible: (root.column === "progress" || root.waiting) && root.derived !== null && !root.ghost
                     && Logic.agentSession(root.c) !== ""
            spacing: 14
            Text {
                objectName: "agentLink"
                visible: !root.waiting
                text: "Przejdź do agenta"
                textFormat: Text.PlainText
                color: agentLink.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                Accessible.role: Accessible.Link
                Accessible.name: text
                Accessible.onPressAction: root.agentRequested(Logic.agentSession(root.c))
                MouseArea {
                    id: agentLink
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.agentRequested(Logic.agentSession(root.c))
                }
            }
            Text {
                objectName: "sessionsLink"
                visible: Logic.sessionsTarget(root.c) !== ""
                text: "Otwórz w Sesjach →"
                textFormat: Text.PlainText
                color: sessionsLink.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                Accessible.role: Accessible.Link
                Accessible.name: text
                Accessible.onPressAction: root.sessionRequested(Logic.sessionsTarget(root.c))
                MouseArea {
                    id: sessionsLink
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.sessionRequested(Logic.sessionsTarget(root.c))
                }
            }
        }
        // Do sprawdzenia: dwa przyciski obok siebie, a gdy brakuje miejsca — jeden pod drugim.
        Flow {
            Layout.fillWidth: true
            visible: root.column === "review" && !root.ghost
            spacing: 6
            Tide.Button {
                objectName: "cardTileDiff"
                visible: root.canDiff && !!(root.c.derived && root.c.derived.flow && root.c.derived.flow.cardBranch)
                small: true
                text: "Zmiany gałęzi"
                onClicked: root.diffRequested()
            }
            Tide.Button {
                small: true
                text: "Gotowe"
                tone: Tide.Tokens.mint
                toolTip: "Przenieś kartę do Gotowych"
                Accessible.name: "Przenieś kartę do Gotowych"   // jak dawny PillButton: podpowiedź zamiast napisu
                Accessible.description: ""
                onClicked: root.moveRequested("done")
            }
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        z: -1                                   // przyciski karty dostają kliknięcia pierwsze
        enabled: !root.ghost
        hoverEnabled: true
        preventStealing: root.canDrag
        cursorShape: root.canDrag ? (drag.active ? Qt.ClosedHandCursor : Qt.PointingHandCursor) : Qt.PointingHandCursor
        drag.target: root.canDrag ? root.dragHost.ghost : null
        drag.threshold: 6
        drag.smoothed: false               // duch zostaje pod kursorem w tym samym miejscu, co przy naciśnięciu
        onPressed: mouse => {
            root.forceActiveFocus()
            if (root.canDrag) root.dragHost.prepareDrag(root, mouse.x, mouse.y)
        }
        onReleased: if (root.dragHost && root.dragHost.dragging && root.dragHost.dragId === root.c.id) root.dragHost.finishDrag()
        onCanceled: if (root.dragHost && root.dragHost.dragId === root.c.id) root.dragHost.cancelDrag()
        onClicked: root.clicked()
    }
    onDragActiveChanged: if (dragActive && dragHost) dragHost.startDrag()
}
