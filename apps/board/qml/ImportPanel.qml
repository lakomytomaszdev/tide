// „Importuj z YouTracka” (PT-7) na stronie „Projekty i tagi”: lista zgłoszeń użytkownika (nierozwiązane,
// przypisane do niego albo zgłoszone przez niego bez przypisania) z /Board YouTrackIssues, filtr, zaznaczanie
// i „Importuj zaznaczone” (/Board ImportYouTrack) — karty trafiają do Backlogu z polem youtrack, tytułem,
// opisem i projektem tablicy po kluczu projektu YouTracka. Zgłoszenie, które ma już kartę, jest na końcu
// listy, z jej ID i bez pola wyboru (bez duplikatów). Teksty zgłoszeń to dane: zawsze zwykły tekst.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: panel

    property var issues: []
    property var picked: ({})          // klucz zgłoszenia → true
    property bool loading: false
    property bool busy: false
    property string error: ""
    property string result: ""
    property string query: ""
    readonly property var rows: Logic.importRows(issues, query)
    readonly property int pickedCount: Object.keys(picked).filter(k => picked[k]).length
    signal refreshRequested()
    signal importRequested(var keys)
    signal closeRequested()

    function toggle(key) {
        const next = Object.assign({}, picked)
        if (next[key]) delete next[key]
        else next[key] = true
        picked = next
    }

    function pickAll() {
        const next = {}
        for (const row of rows) if (!row.card) next[row.id] = true
        picked = next
    }

    radius: 12
    color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
    implicitHeight: content.implicitHeight + 28
    Accessible.role: Accessible.Pane
    Accessible.name: "Import z YouTracka"

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: panel.loading ? "Pobieram zgłoszenia z YouTracka…"
                    : panel.issues.length + " " + Logic.plural(panel.issues.length, "zgłoszenie", "zgłoszenia", "zgłoszeń")
                      + " · trafią do Backlogu z odnośnikiem do YouTracka"
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            PillButton {
                objectName: "importRefresh"
                implicitHeight: 28
                pixelSize: 12
                flat: true
                enabled: !panel.loading
                text: "Odśwież"
                onClicked: panel.refreshRequested()
            }
            PillButton {
                implicitHeight: 28
                pixelSize: 12
                flat: true
                text: "Zamknij"
                onClicked: panel.closeRequested()
            }
        }

        LineInput {
            objectName: "importFilter"
            Layout.fillWidth: true
            placeholder: "Filtruj po kluczu, tytule albo projekcie"
            onEdited: panel.query = text
            onTextChanged: panel.query = text
        }

        Text {
            objectName: "importError"
            Layout.fillWidth: true
            visible: panel.error !== ""
            text: panel.error
            textFormat: Text.PlainText
            color: Tide.Tokens.rose
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.Wrap
        }

        ListView {
            id: list
            objectName: "importList"
            Layout.fillWidth: true
            implicitHeight: Math.min(contentHeight, 360)
            clip: true
            spacing: 4
            model: panel.rows
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
            delegate: Rectangle {
                id: row
                required property var modelData
                readonly property bool linked: !!modelData.card
                readonly property bool checked: !!panel.picked[modelData.id]
                objectName: "importRow"
                width: list.width - 12
                implicitHeight: rowLayout.implicitHeight + 12
                radius: 8
                color: rowArea.containsMouse && !linked ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                                                       : checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.08) : "transparent"
                Accessible.role: Accessible.CheckBox
                Accessible.name: modelData.id + " " + modelData.summary
                Accessible.checkable: !linked
                Accessible.checked: checked
                RowLayout {
                    id: rowLayout
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: 10
                    Rectangle {
                        implicitWidth: 16
                        implicitHeight: 16
                        radius: 4
                        visible: !row.linked
                        color: row.checked ? Tide.Tokens.accent : "transparent"
                        border.width: 1
                        border.color: row.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.3)
                        Text {
                            anchors.centerIn: parent
                            visible: row.checked
                            text: "✓"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.backgroundDesktop
                            font.pixelSize: 11
                            font.weight: Font.Bold
                        }
                    }
                    Text {
                        text: row.modelData.id
                        textFormat: Text.PlainText
                        color: Tide.Tokens.sky
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                    Text {
                        Layout.fillWidth: true
                        text: row.modelData.summary
                        textFormat: Text.PlainText
                        color: row.linked ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                    Text {
                        visible: row.modelData.new && !row.linked
                        text: "nowe"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.peach
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    Text {
                        text: row.linked ? "już jest: " + row.modelData.card
                            : row.modelData.boardProjectName ? "→ " + row.modelData.boardProjectName
                            : row.modelData.project + " · bez projektu"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    Text {
                        text: row.modelData.state || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                }
                MouseArea {
                    id: rowArea
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: !row.linked
                    cursorShape: row.linked ? Qt.ArrowCursor : Qt.PointingHandCursor
                    onClicked: panel.toggle(row.modelData.id)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                objectName: "importResult"
                Layout.fillWidth: true
                text: panel.result
                textFormat: Text.PlainText
                color: Tide.Tokens.mint
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
            PillButton {
                implicitHeight: 30
                pixelSize: 12
                flat: true
                visible: panel.rows.some(r => !r.card)
                text: "Zaznacz wszystkie"
                onClicked: panel.pickAll()
            }
            PillButton {
                objectName: "importSubmit"
                implicitHeight: 30
                pixelSize: 12
                strong: true
                enabled: panel.pickedCount > 0 && !panel.busy
                text: panel.busy ? "Importuję…" : "Importuj zaznaczone" + (panel.pickedCount > 0 ? " (" + panel.pickedCount + ")" : "")
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                onClicked: panel.importRequested(Object.keys(panel.picked).filter(k => panel.picked[k]))
            }
        }
    }
}
