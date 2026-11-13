// Wspólny wybór bazy i lokalnego celu scalenia. Odpowiedzi starego projektu i zapytania odrzucamy po tagu.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide

ColumnLayout {
    id: picker
    // Podpis sekcji (12 px, text.muted).
    component SectionLabel: Text {
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        font.weight: Font.Medium
    }
    property bool active: false
    property string purpose: "base"
    property string baseRef: ""
    property string contextKey: ""
    property var branches: []
    property var recentBases: []
    property var recentTargets: []
    property var createTarget: null
    readonly property var mergeShortcuts: {
        if (purpose !== "merge") return []
        const all = branches.concat(recentTargets)
        const ordered = [all.find(b => b.ref === baseRef), all.find(b => b.ref === mainRef)].concat(recentTargets)
        const seen = new Set()
        return ordered.filter(b => {
            if (!b || seen.has(b.ref)) return false
            seen.add(b.ref)
            return true
        })
    }
    readonly property var searchBranches: branches.filter(b => !mergeShortcuts.some(s => s.ref === b.ref))
    property var selected: null
    property var pinned: null
    property var initialSelection: null
    property bool readOnlyRevision: false
    property string refreshRef: ""
    property string mainRef: ""
    property string pendingTag: ""
    property string message: ""
    property bool loading: false
    property int currentIndex: 0
    signal requested(string text)
    signal shortcutChosen(var target)
    signal chosen(var target)          // wynik wyszukiwania wybrany kliknięciem albo klawiaturą (nie skrót)
    readonly property bool ready: active && selected !== null && !loading && message === ""

    function reset() {
        pendingTag = ""
        refreshRef = ""
        branches = []
        recentBases = []
        recentTargets = []
        createTarget = null
        selected = pinned || initialSelection
        mainRef = ""
        message = ""
        loading = false
        status.text = ""
        search.text = ""
        if (active && contextKey) refresh()
    }
    function refresh() {
        if (!active || !contextKey || pinned) return
        pendingTag = ""
        loading = true
        message = ""
        status.text = ""
        branches = []
        recentBases = []
        recentTargets = []
        createTarget = null
        newTargetName.text = ""
        if (purpose === "merge") selected = null
        requested(search.text)
    }
    function receive(tag, reply) {
        if (!active || pinned || tag !== pendingTag) return
        loading = false
        if (!reply || !reply.ok) {
            branches = []
            selected = null
            message = reply && reply.message ? reply.message : "Nie można pobrać gałęzi. Odśwież listę."
            return
        }
        branches = reply.branches || []
        createTarget = purpose === "merge" ? reply.createTarget || null : null
        recentBases = purpose === "base" ? reply.recentBases || [] : []
        recentTargets = purpose === "merge" ? reply.recentTargets || [] : []
        mainRef = reply.mainRef || ""
        if (refreshRef) {
            selected = branches.find(b => b.ref === refreshRef && b.selectable) || null
            refreshRef = ""
            if (!selected) {
                message = "Wybranej rewizji nie ma na liście — wyszukaj i wybierz ją ponownie."
                return
            }
        }
        currentIndex = 0
        // Domyślna baza jest nazwanym refem głównej, nigdy bieżącym HEAD.
        if (purpose === "base" && !selected && search.text === "")
            selected = branches.find(b => b.ref === mainRef && b.selectable) || null
        if (purpose === "merge" && baseRef.indexOf("refs/remotes/") === 0 && !selected && search.text === "")
            selected = branches.find(b => b.ref === reply.defaultTargetRef && b.selectable) || null
        message = ""
        status.text = (reply.hasMore ? "Zawęź wyszukiwanie — lista ma więcej wyników. " : "")
                    + (reply.warnings || []).join(" ")
                    + (branches.length === 0 ? " Brak pasujących gałęzi." : "")
    }
    function choose(index) {
        const item = searchBranches[index]
        if (item && item.selectable) {
            newTargetName.text = ""
            selected = item
            chosen(item)
        }
    }
    onActiveChanged: reset()
    onContextKeyChanged: reset()
    onPinnedChanged: reset()
    onInitialSelectionChanged: reset()
    spacing: 8
    SectionLabel { text: picker.purpose === "merge" ? "Lokalny cel scalenia" : picker.readOnlyRevision ? "Rewizja do specyfikacji" : "Gałąź bazowa" }
    Text {
        Layout.fillWidth: true
        text: picker.purpose === "merge" ? "Wybór celu nie zmienia bazy karty. Brakujący checkout powstanie dopiero po potwierdzeniu podglądu. "
              + (picker.baseRef.indexOf("refs/remotes/") === 0 ? "Baza jest zdalna — cel domyślny wymaga jednoznacznego śledzenia tej bazy."
                 : !picker.baseRef ? "Baza jest nieznana. Nazwana główna jest tylko propozycją celu." : "Baza: " + picker.baseRef)
              : "Wybór dotyczy tylko projektu karty; dodatkowe projekty zachowują swoje bazy i odczyt. "
              + (picker.readOnlyRevision ? "Kod wybranej rewizji tylko do odczytu; bez gałęzi i worktree karty. " : "")
              + (picker.pinned ? "Baza karty jest utrwalona." : picker.mainRef ? "Główna: " + picker.mainRef.substring(11) : "")
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    Text {
        Layout.fillWidth: true
        visible: !picker.active
        text: "Wybór rewizji wymaga projektu z repozytorium."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    SectionLabel {
        visible: picker.active && picker.mergeShortcuts.length > 0
        text: "Skróty celów repozytorium"
    }
    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(targetShortcuts.implicitHeight, 200)
        visible: picker.active && picker.mergeShortcuts.length > 0
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: targetShortcuts
            width: parent.width
            Repeater {
                model: picker.mergeShortcuts
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "mergeTargetShortcut" + index
                    Layout.fillWidth: true
                    implicitHeight: targetName.implicitHeight + 16
                    color: Tide.Tokens.backgroundCard
                    radius: Tide.Tokens.radiusTile
                    activeFocusOnTab: true
                    signal clicked()
                    onClicked: {
                        if (!modelData.selectable || picker.loading) return
                        picker.selected = modelData
                        picker.shortcutChosen(modelData)
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: targetName.text
                    Accessible.onPressAction: clicked()
                    Keys.onReturnPressed: clicked()
                    Keys.onSpacePressed: clicked()
                    Text {
                        id: targetName
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: (modelData.ref === picker.baseRef ? "Baza karty · "
                               : modelData.ref === picker.mainRef ? "Główna · " : "Ostatni cel · ")
                              + modelData.ref + (modelData.selectable ? "" : " — " + modelData.reason)
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: modelData.selectable ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    MouseArea { anchors.fill: parent; onClicked: parent.clicked() }
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        visible: !picker.pinned
        enabled: picker.active
        BranchInput {
            id: search
            objectName: picker.purpose === "merge" ? "mergeTargetSearch" : "baseSearch"
            Layout.fillWidth: true
            placeholder: "Szukaj po dowolnym fragmencie nazwy…"
            onEdited: picker.refresh()
            onAccepted: picker.choose(picker.currentIndex)
            Keys.onDownPressed: picker.currentIndex = Math.min(picker.searchBranches.length - 1, picker.currentIndex + 1)
            Keys.onUpPressed: picker.currentIndex = Math.max(0, picker.currentIndex - 1)
        }
        Tide.TextButton {
            objectName: picker.purpose === "merge" ? "mergeTargetRefresh" : "baseRefresh"
            variant: "outline"
            text: "Odśwież"
            onClicked: { picker.refreshRef = picker.selected ? picker.selected.ref : ""; picker.refresh() }
        }
    }
    SectionLabel {
        visible: picker.active && !picker.pinned && picker.recentBases.length > 0
        text: "Ostatnie bazy repozytorium"
    }
    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(recents.implicitHeight, 120)
        visible: picker.active && !picker.pinned && picker.recentBases.length > 0
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: recents
            width: parent.width
            Repeater {
                model: picker.recentBases
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "recentBase" + index
                    Layout.fillWidth: true
                    implicitHeight: recentName.implicitHeight + 16
                    color: Tide.Tokens.backgroundCard
                    radius: Tide.Tokens.radiusTile
                    activeFocusOnTab: true
                    signal clicked()
                    onClicked: if (modelData.selectable) picker.selected = modelData
                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.ref + " · " + modelData.commit
                    Accessible.onPressAction: clicked()
                    Keys.onReturnPressed: clicked()
                    Keys.onSpacePressed: clicked()
                    Text {
                        id: recentName
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.ref + " · " + modelData.commit
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    MouseArea { anchors.fill: parent; onClicked: parent.clicked() }
                }
            }
        }
    }
    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(results.implicitHeight, 160)
        visible: picker.active && picker.searchBranches.length > 0
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: results
            width: parent.width
            spacing: 2
            Repeater {
                id: resultRepeater
                model: picker.searchBranches
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: (picker.purpose === "merge" ? "mergeTargetResult" : "baseResult") + index
                    Layout.fillWidth: true
                    implicitHeight: branchName.implicitHeight + 16
                    color: index === picker.currentIndex ? Tide.Theme.alpha(Tide.Tokens.accent, 0.12) : Tide.Tokens.backgroundCard
                    radius: Tide.Tokens.radiusTile
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.name
                    Accessible.onPressAction: picker.choose(index)
                    Keys.onReturnPressed: picker.choose(index)
                    Keys.onSpacePressed: picker.choose(index)
                    Keys.onDownPressed: { picker.currentIndex = Math.min(picker.searchBranches.length - 1, index + 1); resultRepeater.itemAt(picker.currentIndex).forceActiveFocus() }
                    Keys.onUpPressed: { picker.currentIndex = Math.max(0, index - 1); resultRepeater.itemAt(picker.currentIndex).forceActiveFocus() }
                    Text {
                        id: branchName
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: (picker.purpose === "merge" ? (modelData.ref === picker.baseRef ? "Baza karty · " : "")
                               + (modelData.ref === picker.mainRef ? "Główna · " : "") : "")
                              + (modelData.label || (modelData.kind === "remote" ? modelData.ref : modelData.name))
                              + (modelData.selectable ? "" : " — " + modelData.reason)
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: modelData.selectable ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    MouseArea { anchors.fill: parent; onClicked: picker.choose(index) }
                }
            }
        }
    }
    Text {
        Layout.fillWidth: true
        visible: picker.active && picker.createTarget !== null
        text: picker.createTarget ? picker.createTarget.label + ". " + picker.createTarget.reason
              + " Możesz podać inną nazwę lokalnej gałęzi poniżej." : ""
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    BranchInput {
        id: newTargetName
        objectName: picker.purpose === "merge" ? "mergeNewTargetName" : ""
        Layout.fillWidth: true
        visible: picker.active && picker.createTarget !== null
        enabled: !picker.loading
        placeholder: "Inna pełna nazwa lokalnej gałęzi, np. release/wynik"
        onEdited: {
            if (picker.createTarget && text.length > 0)
                picker.selected = {ref: "refs/heads/" + text, name: text, commit: picker.createTarget.commit, kind: "create"}
            else picker.selected = null
        }
    }
    Text {
        Layout.fillWidth: true
        visible: picker.active
        text: picker.selected ? (picker.purpose === "merge" ? "Wybrany cel: " : "Wybrana baza: ")
                               + picker.selected.ref + " · " + picker.selected.commit
                              : picker.purpose === "merge" ? "Wybierz lokalny cel, a następnie otwórz podgląd."
                              : "Wybierz lokalną lub zdalną gałąź bazową."
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.monoFamily
        font.pixelSize: 12
    }
    Text {
        id: status
        Layout.fillWidth: true
        visible: picker.active
        text: ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    Text {
        Layout.fillWidth: true
        visible: picker.active && (picker.loading || picker.message !== "")
        text: picker.loading ? "Odczytuję znane lokalnie gałęzie…" : picker.message
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
}
