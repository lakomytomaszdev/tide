import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "tide" as Tide

QQC2.Popup {
    id: preview
    objectName: "mergePreview"
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, 1000)
    height: Math.min(parent.height - 40, 760)
    modal: true
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape
    property string requestTag: ""
    property string cardId: ""
    property string targetBranch: ""
    property bool busy: false
    property string error: ""
    property var result: null

    function show(tag, card, target) {
        requestTag = tag
        cardId = card
        targetBranch = target
        result = null
        error = ""
        busy = true
        open()
    }

    function describe(r) {
        if (!r) return ""
        const lines = ["Źródło: " + r.sourceRef, "SHA źródła: " + r.sourceCommit,
                       "Cel: " + r.targetRef, "SHA celu: " + r.targetCommit,
                       "Drzewo wyniku: " + (r.resultTree || "nieprzygotowane"),
                       "Worktree celu: " + r.targetWorktree + (r.targetWorktreeExists ? "" : " (jeszcze nie istnieje)"),
                       "", r.reason ? r.message + " [" + r.reason + "]" : "Drzewo scalenia bez konfliktów.",
                       "Gotowość do wykonania nie została potwierdzona. Lokalne kolizje: nieocenione."]
        for (const warning of r.warnings || []) lines.push(warning)
        lines.push("", "Wnoszone commity (" + r.commitCount + ") — cel..karta:")
        for (const c of r.commits || []) lines.push(c.sha + "  " + c.subject)
        lines.push("", "Zmiany plików względem drzewa celu (" + r.fileCount + "):")
        for (const f of r.files || [])
            lines.push(f.status + "  " + (f.oldPath && f.newPath && f.oldPath !== f.newPath
                       ? f.oldPath + " → " + f.newPath : f.newPath || f.oldPath))
        lines.push("", "Konflikty (" + r.conflictCount + "):")
        for (const path of r.conflicts || []) lines.push(path)
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
            text: preview.cardId + " · Podgląd scalenia do " + preview.targetBranch
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            wrapMode: Text.Wrap
        }
        QQC2.ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            QQC2.TextArea {
                objectName: "mergePreviewText"
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                text: preview.busy ? "Przygotowuję odczyt…" : preview.error || preview.describe(preview.result)
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                wrapMode: TextEdit.Wrap
            }
        }
        Tide.TextButton {
            objectName: "mergePreviewClose"
            text: "Zamknij"
            onClicked: preview.close()
        }
    }
}
