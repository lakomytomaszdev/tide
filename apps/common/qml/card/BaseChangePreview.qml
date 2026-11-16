import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "../tide" as Tide

QQC2.Popup {
    id: preview
    objectName: "baseChangePreview"
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, 1000)
    height: Math.min(parent.height - 40, 760)
    modal: true
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape
    property string cardId: ""
    property string requestTag: ""
    property bool busy: false
    property bool selecting: false
    property string error: ""
    property var result: null
    property alias selectionContent: selector.data
    property var selectedBase: null
    property bool selectionReady: false
    signal requested(var request)
    signal refreshed(var request)
    signal keepRequested(string step)
    signal confirmed(var request)
    signal fileRequested(string path)
    onClosed: requestTag = ""

    function select(card) {
        cardId = card
        result = null
        error = ""
        busy = false
        selecting = true
        open()
    }
    function show(card, reply) {
        cardId = card
        selecting = false
        busy = false
        error = ""
        result = reply
        open()
    }
    function describe(r) {
        if (!r) return ""
        const base = b => (b && b.baseRef || "unknown") + "\nSHA: " + (b && b.baseCommit || "unknown")
        const lines = ["Stara baza: " + base(r.oldBase), "Nowa baza: " + base(r.newBase),
                       "Wersja bazy: " + r.baseVersion, "Identyfikator podglądu: " + r.previewId,
                       "Dowód: " + r.evidence, "", r.canChange ? "Pusta karta kwalifikuje się do korekty."
                         : "Odmowa korekty bazy."]
        for (const refusal of r.blockedReasons || []) lines.push(refusal.message + " [" + refusal.reason + "]")
        for (const b of r.branches || []) {
            if (b.previousOrder) lines.push("", "Poprzednie zlecenie — zachowawcza kontrola zachowanych danych:")
            lines.push("", b.ref + " · " + b.worktree, "Zapisany start: " + b.startRef + " · " + b.startCommit,
                       "Aktualny SHA: " + b.commit)
            for (const c of b.commits || []) lines.push("Commit wkładu: " + c.sha)
            for (const kind of ["staged", "unstaged", "untracked"])
                for (const entry of b.localChanges && b.localChanges[kind] || [])
                    lines.push(kind + ": " + entry.paths.join(" ← "))
            const ignored = b.ignored || []
            const shown = ignored.slice(0, 10)
            const count = b.ignoredCount === undefined ? ignored.length : b.ignoredCount
            for (const path of shown) lines.push("Ignorowane (pozostają tutaj): " + path)
            if (count > shown.length)
                lines.push("… oraz " + (count - shown.length) + " pozostałych ignorowanych ścieżek/katalogów.")
        }
        for (const p of r.processes || [])
            lines.push((p.previousOrder ? "Proces poprzedniego zlecenia: " : "Proces: ") + p.step + " · " + p.unit + " · " + p.state)
        lines.push("", "Unieważnienia po potwierdzeniu: " + (r.invalidations || []).join(", "),
                   "", r.ignoredPolicy, r.localStatePolicy || "", r.scope, "", r.instruction,
                   r.followup && r.followup.message || "", "", r.executionMessage)
        return lines.join("\n")
    }
    background: Rectangle {
        color: Tide.Tokens.backgroundPanel
        radius: Tide.Tokens.radiusCard
        border.color: Tide.Tokens.textMuted
    }
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            Layout.fillWidth: true
            text: preview.cardId + " · Podgląd korekty bazy"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }
        ColumnLayout {
            id: selector
            Layout.fillWidth: true
            visible: preview.selecting
        }
        Tide.TextButton {
            objectName: "baseChangeSelect"
            visible: preview.selecting
            text: "Zgłoś zmianę bazy i pokaż podgląd (karta czeka na decyzję)"
            enabled: preview.selectionReady && !preview.busy
            onClicked: {
                preview.busy = true
                preview.requested({baseRef: preview.selectedBase.ref, baseCommit: preview.selectedBase.commit})
            }
        }
        QQC2.ScrollView {
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}
            QQC2.ScrollBar.horizontal: Tide.ScrollBar {}
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            QQC2.TextArea {
                objectName: "baseChangeText"
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                text: (preview.busy ? "Odczytuję dowody…\n" : "") + (preview.error ? preview.error + "\n\n" : "") + preview.describe(preview.result)
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: TextEdit.Wrap
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            Tide.TextButton {
                objectName: "baseChangeKeep"
                visible: !!(preview.result && preview.result.decisionStep)
                text: "Zachowaj bazę, aby kontynuować"
                enabled: !preview.busy
                onClicked: preview.keepRequested(preview.result.decisionStep)
            }
            Tide.TextButton {
                objectName: "baseChangeRefresh"
                visible: !!preview.result
                text: "Ponów podgląd"
                enabled: !preview.busy
                onClicked: {
                    preview.busy = true
                    preview.refreshed({baseRef: preview.result.newBase.baseRef,
                                       baseCommit: preview.result.newBase.baseCommit,
                                       step: preview.result.decisionStep || ""})
                }
            }
            Tide.TextButton {
                objectName: "baseChangeSketch"
                visible: !!(preview.result && preview.result.followup && preview.result.followup.available)
                text: "Otwórz szkic K-50-replay-code"
                onClicked: preview.fileRequested(preview.result.followup.path)
            }
            Tide.TextButton {
                objectName: "baseChangeConfirm"
                text: "Potwierdź pustą korektę i unieważnij stare zgody"
                enabled: !!(preview.result && preview.result.canExecute && preview.result.previewId) && !preview.busy
                onClicked: {
                    preview.busy = true
                    preview.confirmed({choice: "change-base", step: preview.result.decisionStep || "",
                                       baseRef: preview.result.newBase.baseRef,
                                       baseCommit: preview.result.newBase.baseCommit,
                                       previewId: preview.result.previewId})
                }
            }
            Tide.TextButton {
                objectName: "baseChangeCancel"
                text: preview.result ? "Pozostaw w oczekiwaniu / zamknij" : "Anuluj"
                onClicked: preview.close()
            }
        }
    }
}
