// Zakładka „Plan” albo „Specyfikacja” panelu karty: nagłówek dokumentu (tytuł, status, kiedy zmieniony,
// skąd i ścieżka, „Otwórz plik”, „Pokaż w katalogu”), przy planie zwarta tabela ticketów (ID, tytuł
// z uwagami, stan słowem w kolorze stanu, gałąź), wyróżniona sekcja „Co przetestować ręcznie” i reszta
// dokumentu jako Markdown — surowy z usługi, renderowany bezpiecznie przez backend (MarkdownBlock,
// renderMarkdown: bez obrazów i HTML ze źródła); link otwiera się tylko dla http(s), i to przez backend.
// Dane z dysku (tytuły, tickety, ścieżki) pokazujemy zwykłym tekstem.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic
import "cardDocs.js" as Docs

Item {
    id: view

    property var doc: null            // plan albo spec z CardDocs
    property string kind: "plan"      // plan | spec
    property var others: []           // inne dokumenty karty ({kind, path, title, status, updated})
    property string homeDir: ""
    property real nowTs: Date.now() / 1000
    property var renderer: null       // backend.renderMarkdown — bezpieczny HTML z Markdownu
    readonly property alias flick: flick
    readonly property alias markdownItem: markdownBlock
    readonly property alias manualItem: manualBlock
    readonly property var status: kind === "plan" ? Docs.planStatus(doc ? doc.status : "") : Docs.specStatus(doc)
    readonly property var tickets: kind === "plan" && doc ? (doc.tickets || []) : []
    signal openFile(string path)
    signal showInFolder(string path)
    signal openLink(string url)

    function tone(name) {
        return Tide.Tokens[name] !== undefined ? Tide.Tokens[name] : Tide.Tokens.textMuted
    }

    Flickable {
        id: flick
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight + 8
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: Tide.ScrollBar {}

        ColumnLayout {
            id: content
            width: flick.width - 14
            spacing: 10

            // Nagłówek dokumentu.
            Text {
                objectName: "docTitle"
                Layout.fillWidth: true
                text: view.doc ? view.doc.title : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 17
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                Accessible.role: Accessible.Heading
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    objectName: "docStatus"
                    property string word: view.status.word
                    visible: word !== ""
                    implicitWidth: statusText.implicitWidth + 16
                    implicitHeight: 22
                    radius: 11
                    color: Tide.Theme.alpha(view.tone(view.status.tint), 0.16)
                    Text {
                        id: statusText
                        anchors.centerIn: parent
                        text: view.status.word
                        textFormat: Text.PlainText
                        color: view.tone(view.status.tint)
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: {
                        if (!view.doc) return ""
                        const ts = Docs.docTime(view.doc)
                        return (ts ? "zmieniony " + Logic.when(ts, view.nowTs) + " · " : "") + Docs.source(view.doc, view.kind)
                    }
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    objectName: "docPath"
                    Layout.fillWidth: true
                    text: view.doc ? Logic.shortPath(view.doc.path, view.homeDir) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                }
                PillButton {
                    objectName: "docOpen"
                    implicitHeight: 28
                    pixelSize: 12
                    outline: true
                    text: "Otwórz plik"
                    toolTip: "Otwórz w domyślnym edytorze"
                    onClicked: view.openFile(view.doc.path)
                }
                PillButton {
                    objectName: "docFolder"
                    implicitHeight: 28
                    pixelSize: 12
                    flat: true
                    text: "Pokaż w katalogu"
                    toolTip: "Plik zaznaczony w menedżerze plików"
                    onClicked: view.showInFolder(view.doc.path)
                }
            }

            // Błędy planu (zły stan w tabeli, nieznany klucz) — plan i tak się pokazuje.
            Text {
                Layout.fillWidth: true
                visible: view.kind === "plan" && !!view.doc && (view.doc.errors || []).length > 0
                text: view.doc && view.doc.errors ? "Plan ma błędy: " + view.doc.errors.join("; ") : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.butter
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // Tickety planu.
            Rectangle {
                objectName: "docTickets"
                Layout.fillWidth: true
                Layout.topMargin: 2
                visible: view.tickets.length > 0
                radius: Tide.Tokens.radiusTile
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
                implicitHeight: ticketColumn.implicitHeight + 12

                ColumnLayout {
                    id: ticketColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        Layout.bottomMargin: 4
                        spacing: 12
                        Repeater {
                            model: [{ label: "Ticket", width: 86 }, { label: "Tytuł", width: -1 },
                                    { label: "Stan", width: 92 }, { label: "Gałąź", width: 170 }]
                            Text {
                                required property var modelData
                                Layout.preferredWidth: modelData.width > 0 ? modelData.width : -1
                                Layout.fillWidth: modelData.width < 0
                                text: modelData.label
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                            }
                        }
                    }
                    Repeater {
                        model: view.tickets
                        Rectangle {
                            id: row
                            required property var modelData
                            required property int index
                            readonly property var look: Docs.ticketState(modelData.state)
                            Layout.fillWidth: true
                            implicitHeight: rowLayout.implicitHeight + 12
                            color: "transparent"
                            opacity: Docs.outsideCard(view.doc, modelData) ? 0.5 : 1
                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 1
                                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                            }
                            RowLayout {
                                id: rowLayout
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 12
                                Text {
                                    Layout.preferredWidth: 86
                                    Layout.alignment: Qt.AlignTop
                                    text: row.modelData.id
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.sky
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 2
                                    Text {
                                        Layout.fillWidth: true
                                        text: row.modelData.title || "—"
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 3
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        visible: text !== ""
                                        text: row.modelData.notes || ""
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                    }
                                }
                                Text {
                                    Layout.preferredWidth: 92
                                    Layout.alignment: Qt.AlignTop
                                    text: row.look.word
                                    textFormat: Text.PlainText
                                    color: view.tone(row.look.tint)
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    Layout.preferredWidth: 170
                                    Layout.alignment: Qt.AlignTop
                                    text: row.modelData.branch || ""
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 11
                                    elide: Text.ElideMiddle
                                }
                            }
                        }
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                visible: view.kind === "plan" && !!view.doc && (view.doc.cardTickets || []).length > 0
                text: view.doc && view.doc.cardTickets ? "Koordynator prowadzi kilka kart — ta obejmuje: "
                                                         + view.doc.cardTickets.join(", ") : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // „Co przetestować ręcznie” — osobno i wyróżnione.
            Rectangle {
                objectName: "docManual"
                Layout.fillWidth: true
                Layout.topMargin: 4
                visible: !!view.doc && (view.doc.manualTest || "") !== ""
                radius: Tide.Tokens.radiusTile
                color: Tide.Theme.alpha(Tide.Tokens.butter, 0.08)
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.butter, 0.38)
                implicitHeight: manualColumn.implicitHeight + 24
                ColumnLayout {
                    id: manualColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 12
                    spacing: 6
                    Text {
                        text: "Co przetestować ręcznie"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.butter
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    MarkdownBlock {
                        id: manualBlock
                        objectName: "docManualText"
                        Layout.fillWidth: true
                        markdown: view.doc ? view.doc.manualTest || "" : ""
                        renderer: view.renderer
                        onLinkClicked: url => view.openLink(url)
                    }
                }
            }

            // Reszta dokumentu.
            MarkdownBlock {
                id: markdownBlock
                objectName: "docMarkdown"
                Layout.fillWidth: true
                Layout.topMargin: 4
                markdown: view.doc ? view.doc.markdown || "" : ""
                renderer: view.renderer
                onLinkClicked: url => view.openLink(url)
            }

            // Inne dokumenty karty (starsze plany, pozostałe specyfikacje).
            ColumnLayout {
                objectName: "docOthers"
                Layout.fillWidth: true
                Layout.topMargin: 10
                visible: view.others.length > 0
                spacing: 4
                Text {
                    text: "Inne dokumenty karty"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: view.others
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 86
                            text: Docs.kindWord(modelData.kind)
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title + (modelData.status ? " · " + modelData.status : "")
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                        PillButton {
                            implicitHeight: 24
                            pixelSize: 11
                            flat: true
                            text: "Otwórz"
                            textColor: Tide.Tokens.accent
                            toolTip: Logic.shortPath(modelData.path, view.homeDir)
                            onClicked: view.openFile(modelData.path)
                        }
                    }
                }
            }
        }
    }
}
