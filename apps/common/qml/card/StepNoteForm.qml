import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "../tide" as Tide

ColumnLayout {
    id: form
    objectName: "stepNoteForm"
    property string stepId: ""
    property string caption: ""
    property bool canAdd: false
    property var draft: ({text: "", busy: false, error: "", saved: ""})
    property var notes: []
    signal edited(string text)
    signal submitted()
    spacing: 8

    Text {
        Layout.fillWidth: true
        text: form.caption
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
    }
    Text {
        Layout.fillWidth: true
        visible: form.canAdd
        text: "Uwaga trafi do najbliższego wznowienia. Zapis nie przerywa pracy kroku ani nie uruchamia nowej sesji."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.9
    }
    QQC2.TextArea {
        objectName: "stepNoteText"
        Layout.fillWidth: true
        visible: form.canAdd
        enabled: !form.draft.busy
        text: form.draft.text
        placeholderText: "Instrukcja do następnego wznowienia (do 16 KiB)"
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.Wrap
        selectByMouse: true
        Accessible.name: "Uwaga do kroku " + form.stepId
        onTextChanged: if (text !== form.draft.text) form.edited(text)
    }
    Tide.TextButton {
        objectName: "stepNoteSave"
        visible: form.canAdd
        text: form.draft.busy ? "Zapisywanie…" : "Dopisz uwagę"
        variant: "outline"
        enabled: !form.draft.busy && form.draft.text.trim().length > 0
        onClicked: form.submitted()
    }
    Text {
        objectName: "stepNoteReply"
        Layout.fillWidth: true
        visible: !!form.draft.error || !!form.draft.saved
        text: form.draft.error || ("Zapisano uwagę: " + form.draft.saved)
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: form.draft.error ? Tide.Tokens.rose : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.9
    }
    Repeater {
        model: form.notes
        delegate: Text {
            required property var modelData
            objectName: "stepNoteStatus"
            Layout.fillWidth: true
            text: modelData.text + "\n" + modelData.statusLabel
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.9
        }
    }
}
