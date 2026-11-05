// Wspólna prezentacja źródeł podpowiedzi. Wyłącznie jawne użycie zmienia pole bazy.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

ColumnLayout {
    id: suggestions
    property bool active: false
    property string cardId: ""
    property string project: ""
    property string repo: ""
    property string text: ""
    property string description: ""
    property string selectionKey: ""
    property int revision: 0
    property string pendingTag: ""
    property var candidates: []
    property string message: ""
    property bool loading: false
    property string scope: "text"
    signal requested(var request)
    signal chosen(var candidate)

    function invalidate() {
        revision++
        pendingTag = ""
        candidates = []
        message = ""
        loading = false
        debounce.restart()
    }
    function refresh(source) {
        if (!active || !cardId || !project || !repo) return
        debounce.stop()
        scope = source || "text"
        pendingTag = ""
        candidates = []
        message = ""
        loading = true
        requested({text: text, revision: revision, scope: scope})
    }
    function receive(tag, reply) {
        if (!active || !pendingTag || tag !== pendingTag) return
        if (reply && reply.ok && (reply.revision !== revision || reply.card !== cardId
                                 || reply.project !== project || reply.repo !== repo)) return
        pendingTag = ""
        loading = false
        candidates = reply && reply.ok ? reply.candidates || [] : []
        message = reply && reply.ok ? (reply.warnings || []).join("\n")
                  : "Nie można przeanalizować podpowiedzi. Możesz wybrać bazę ręcznie."
    }
    function useCandidate(candidate) {
        if (active && candidate && candidate.selectable
                && candidates.some(c => c.ref === candidate.ref && c.commit === candidate.commit)) chosen(candidate)
    }
    onActiveChanged: invalidate()
    onCardIdChanged: invalidate()
    onProjectChanged: invalidate()
    onRepoChanged: invalidate()
    onTextChanged: invalidate()
    onDescriptionChanged: invalidate()
    onSelectionKeyChanged: invalidate()
    Timer { id: debounce; interval: 250; onTriggered: suggestions.refresh() }
    visible: active
    spacing: 8
    FieldLabel { text: "Podpowiedzi bazy z opisu i polecenia" }
    QQC2.Button {
        objectName: "checkBaseFiles"
        text: "Sprawdź lokalną historię plików"
        enabled: !suggestions.loading
        onClicked: suggestions.refresh("files")
    }
    Text {
        Layout.fillWidth: true
        text: "Podpowiedź nie zmienia bazy. Użyj pełnego refu, a następnie uruchom kartę."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(results.implicitHeight, 240)
        visible: suggestions.candidates.length > 0
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            id: results
            width: parent.width
            Repeater {
                model: suggestions.candidates
                delegate: ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Text {
                        objectName: "baseEvidence" + index
                        Layout.fillWidth: true
                        text: modelData.source + " · " + modelData.strength + " · " + modelData.commit + "\n"
                              + (modelData.source === "files" ? "Pozycja " + modelData.rank + " · ścieżki: " + modelData.matchedPaths + "\n" : "")
                              + (modelData.evidence || []).map(e => modelData.source === "files"
                                  ? e.path + " · commit " + e.commit + " · " + e.time
                                  : (e.field === "description" ? "Opis" : "Polecenie")
                                    + ", wiersz " + e.line + ": " + e.text).join("\n")
                              + (modelData.reason ? "\n" + modelData.reason : "")
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    Rectangle {
                        objectName: "useBaseSuggestion" + index
                        Layout.fillWidth: true
                        implicitHeight: useLabel.implicitHeight + 16
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        enabled: modelData.selectable
                        activeFocusOnTab: true
                        signal clicked()
                        onClicked: suggestions.useCandidate(modelData)
                        Accessible.role: Accessible.Button
                        Accessible.name: useLabel.text
                        Accessible.onPressAction: if (enabled) clicked()
                        Keys.onReturnPressed: if (enabled) clicked()
                        Keys.onSpacePressed: if (enabled) clicked()
                        Text {
                            id: useLabel
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.margins: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Użyj " + modelData.ref
                            textFormat: Text.PlainText
                            wrapMode: Text.WrapAnywhere
                            color: modelData.selectable ? Tide.Tokens.accent : Tide.Tokens.textMuted
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 12
                        }
                        MouseArea { anchors.fill: parent; onClicked: parent.clicked() }
                    }
                }
            }
        }
    }
    Text {
        Layout.fillWidth: true
        text: suggestions.loading ? (suggestions.scope === "files" ? "Analizuję lokalną historię plików…" : "Analizuję tekst…")
              : suggestions.message || (suggestions.candidates.length ? "" : "Brak jawnych wskazań znanych gałęzi.")
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
}
