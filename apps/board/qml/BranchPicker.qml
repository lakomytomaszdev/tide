// Odczyt lokalnych baz projektu karty. Odpowiedzi starego projektu i zapytania są odrzucane po tagu.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

ColumnLayout {
    id: picker
    property bool active: false
    property string contextKey: ""
    property var branches: []
    property var selected: null
    property string mainRef: ""
    property string pendingTag: ""
    property string message: ""
    property bool loading: false
    property int currentIndex: 0
    signal requested(string text)
    readonly property bool ready: active && selected !== null && !loading && message === ""

    function reset() {
        pendingTag = ""
        branches = []
        selected = null
        mainRef = ""
        message = ""
        loading = false
        status.text = ""
        search.text = ""
        if (active && contextKey) refresh()
    }
    function refresh() {
        if (!active || !contextKey) return
        pendingTag = ""
        loading = true
        message = ""
        status.text = ""
        branches = []
        requested(search.text)
    }
    function receive(tag, reply) {
        if (!active || tag !== pendingTag) return
        loading = false
        if (!reply || !reply.ok) {
            branches = []
            selected = null
            message = reply && reply.message ? reply.message : "Nie można pobrać gałęzi. Odśwież listę."
            return
        }
        branches = reply.branches || []
        mainRef = reply.mainRef || ""
        currentIndex = 0
        // Domyślna baza jest nazwanym refem głównej, nigdy bieżącym HEAD.
        if (!selected && search.text === "") selected = branches.find(b => b.ref === mainRef && b.selectable) || null
        message = ""
        status.text = (reply.hasMore ? "Zawęź wyszukiwanie — lista ma więcej wyników. " : "")
                    + (reply.warnings || []).join(" ")
                    + (branches.length === 0 ? " Brak pasujących lokalnych gałęzi." : "")
    }
    function choose(index) {
        const item = branches[index]
        if (item && item.selectable) selected = item
    }
    onActiveChanged: reset()
    onContextKeyChanged: reset()
    spacing: 8
    FieldLabel { text: "Gałąź bazowa" }
    Text {
        Layout.fillWidth: true
        text: "Wybór dotyczy projektu karty. " + (picker.mainRef ? "Główna: " + picker.mainRef.substring(11) : "")
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    Text {
        Layout.fillWidth: true
        visible: !picker.active
        text: "Wybór bazy jest dostępny tylko w Od razu. Mam plan i Planowanie czekają na obsługę przypiętej bazy; Tylko specyfikacja — na obsługę wybranej rewizji."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    RowLayout {
        Layout.fillWidth: true
        enabled: picker.active
        LineInput {
            id: search
            objectName: "baseSearch"
            Layout.fillWidth: true
            placeholder: "Szukaj po dowolnym fragmencie nazwy…"
            onEdited: picker.refresh()
            onAccepted: picker.choose(picker.currentIndex)
            Keys.onDownPressed: picker.currentIndex = Math.min(picker.branches.length - 1, picker.currentIndex + 1)
            Keys.onUpPressed: picker.currentIndex = Math.max(0, picker.currentIndex - 1)
        }
        PillButton { text: "Odśwież"; onClicked: picker.refresh() }
    }
    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(results.implicitHeight, 160)
        visible: picker.active && picker.branches.length > 0
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: results
            width: parent.width
            spacing: 2
            Repeater {
                id: resultRepeater
                model: picker.branches
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "baseResult" + index
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
                    Keys.onDownPressed: { picker.currentIndex = Math.min(picker.branches.length - 1, index + 1); resultRepeater.itemAt(picker.currentIndex).forceActiveFocus() }
                    Keys.onUpPressed: { picker.currentIndex = Math.max(0, index - 1); resultRepeater.itemAt(picker.currentIndex).forceActiveFocus() }
                    Text {
                        id: branchName
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.name + (modelData.selectable ? "" : " — " + modelData.reason)
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
        visible: picker.active
        text: picker.selected ? "Wybrana baza: " + picker.selected.name + " · " + picker.selected.commit
                              : "Wybierz lokalną gałąź bazową."
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
        text: picker.loading ? "Pobieram lokalne gałęzie…" : picker.message
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
}
