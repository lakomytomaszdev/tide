// Tide.CodeBlock (K-128, PT-506): blok kodu okna Tide według makiety S-9 (`.code`, obszar „Prośba o zgodę”, wariant A).
//
// Dwa warianty:
// - zwijany (domyślny): nagłówek ze ścieżką (`path`, sky, Geist Mono), liczbą linii, językiem, „pokaż treść skryptu”
//   i „Kopiuj”; **domyślnie zwinięty** (`expanded: false`). Po rozwinięciu linie z numerami i kolorowaniem powłoki
//   (shell.js: komentarze, słowa kluczowe, napisy, zmienne, polecenia) w ramce najwyżej `maxBodyHeight` z własnym
//   przewijaniem (Tide.ScrollBar). `truncated` — zdanie, że treść jest skrócona,
// - jednowierszowy (`oneLine: true`): polecenie w polu z „Kopiuj” (jak dawne pole polecenia decyzji), bez kolorowania.
//
// Treść to niezaufany tekst. Zwijany wariant rysuje linię przez RichText wyłącznie z HTML-a z shell.js, który
// escapuje każdy kawałek przed owinięciem go w <span> ze stałym stylem z palety (bez linków i obrazów). Numery linii,
// nagłówek i wariant jednowierszowy są zwykłym tekstem. „Kopiuj” kopiuje surowy tekst bez znaczników.
// Linie liczą się dopiero po rozwinięciu, więc zwinięty blok nie tokenizuje skryptu.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "shell.js" as Shell

Rectangle {
    id: block

    property string text: ""
    property string path: ""
    property string language: "bash"
    property int lineCount: 0            // z usługi; 0 — policz z tekstu
    property bool truncated: false
    property bool expanded: false
    property bool oneLine: false
    property int maxBodyHeight: 420
    property string copyName: "Kopiuj"   // nazwa przycisku dla czytnika ekranu
    property bool copied: false

    readonly property var colors: Shell.colors(Tokens)
    readonly property var lines: expanded && !oneLine ? Shell.lines(text) : []
    readonly property int shownLines: lineCount > 0 ? lineCount : Shell.lines(text).length
    readonly property string summary: [Shell.lineLabel(shownLines), language].filter(x => !!x).join(" · ")
    readonly property alias copyButton: copyButton

    function toggle() { expanded = !expanded }

    function copy() {
        clipboard.text = block.text
        clipboard.selectAll()
        clipboard.copy()
        clipboard.text = ""
        copied = true
    }

    onTextChanged: copied = false

    implicitHeight: oneLine ? Math.max(single.implicitHeight, copyButton.implicitHeight) + 14
                            : column.implicitHeight + 2
    radius: oneLine ? Tokens.radiusFrame : Tokens.radiusTile
    color: Tokens.backgroundView
    border.width: 1
    border.color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
    clip: true

    Accessible.role: Accessible.Grouping
    Accessible.name: oneLine ? "Polecenie" : (path || "Skrypt") + ", " + summary

    TextEdit {
        id: clipboard
        visible: false
    }

    // --- wariant jednowierszowy ------------------------------------------------------------------------------------
    TextEdit {
        id: single
        objectName: "codeBlockText"
        visible: block.oneLine
        readOnly: true
        selectByMouse: true
        selectionColor: Theme.alpha(Tokens.sky, 0.35)
        anchors.left: parent.left
        anchors.right: copyButton.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 12
        anchors.rightMargin: 8
        text: block.oneLine ? block.text : ""
        textFormat: TextEdit.PlainText
        color: Tokens.textPrimary
        font.family: Theme.monoFamily
        font.pointSize: Tokens.fontSizePt * 0.95
        wrapMode: TextEdit.WrapAnywhere
    }

    // --- wariant zwijany ---------------------------------------------------------------------------------------------
    ColumnLayout {
        id: column
        visible: !block.oneLine
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 1
        spacing: 0

        Rectangle {
            id: head
            objectName: "codeBlockHead"
            Layout.fillWidth: true
            implicitHeight: headRow.implicitHeight + 14
            color: block.colors.head
            radius: Tokens.radiusTile - 1
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: (block.expanded ? "Zwiń treść skryptu " : "Pokaż treść skryptu ") + block.path
            Accessible.onPressAction: block.toggle()
            Keys.onSpacePressed: block.toggle()
            Keys.onReturnPressed: event => { block.toggle(); event.accepted = true }

            // Dolne rogi kwadratowe, gdy pod nagłówkiem stoją linie.
            Rectangle {
                visible: block.expanded
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.radius
                color: parent.color
            }
            Rectangle {
                visible: block.expanded
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: block.toggle()
            }

            RowLayout {
                id: headRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 8
                spacing: 10
                Text {
                    objectName: "codeBlockPath"
                    Layout.maximumWidth: implicitWidth
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: block.path
                    textFormat: Text.PlainText
                    color: Tokens.sky
                    font.family: Theme.monoFamily
                    font.pointSize: Tokens.fontSizePt * 0.9
                    elide: Text.ElideMiddle
                }
                Text {
                    objectName: "codeBlockSummary"
                    text: (block.path ? "· " : "") + block.summary
                    textFormat: Text.PlainText
                    color: Tokens.textMuted
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt * 0.9
                }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "codeBlockToggle"
                    text: block.expanded ? "zwiń" : "pokaż treść skryptu"
                    variant: "ghost"
                    small: true
                    onClicked: block.toggle()
                }
                Button {
                    objectName: "codeBlockCopy"
                    text: block.copied ? "Skopiowano" : "Kopiuj"
                    variant: "ghost"
                    small: true
                    Accessible.name: block.copyName
                    onClicked: block.copy()
                }
            }
        }

        Flickable {
            id: flick
            objectName: "codeBlockBody"
            Layout.fillWidth: true
            visible: block.expanded
            implicitHeight: block.expanded ? Math.min(lineColumn.implicitHeight + 20, block.maxBodyHeight) : 0
            contentHeight: lineColumn.implicitHeight + 20
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: ScrollBar {}

            Column {
                id: lineColumn
                y: 10
                width: flick.width - Tokens.scrollbarWidth
                Repeater {
                    model: block.lines
                    delegate: Item {
                        id: lineRow
                        objectName: "codeBlockLine"
                        required property string modelData
                        required property int index
                        readonly property string html: Shell.html(modelData, block.colors)
                        width: lineColumn.width
                        implicitHeight: Math.max(code.implicitHeight, number.implicitHeight)
                        height: implicitHeight
                        Text {
                            id: number
                            objectName: "codeBlockNumber"
                            width: numbers.digit * Math.max(2, String(block.lines.length).length)
                            x: 10
                            text: String(lineRow.index + 1)
                            textFormat: Text.PlainText
                            horizontalAlignment: Text.AlignRight
                            color: block.colors.number
                            font.family: Theme.monoFamily
                            font.pointSize: Tokens.fontSizePt * 0.88
                            lineHeight: 1.3
                        }
                        Text {
                            id: code
                            objectName: "codeBlockCode"
                            x: number.x + number.width + 12
                            width: lineRow.width - x - 10
                            text: "<div style=\"white-space:pre-wrap\">" + lineRow.html + "</div>"
                            textFormat: Text.RichText
                            color: block.colors.text
                            font.family: Theme.monoFamily
                            font.pointSize: Tokens.fontSizePt * 0.88
                            lineHeight: 1.3
                            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                        }
                    }
                }
            }
        }

        Text {
            objectName: "codeBlockTruncated"
            Layout.fillWidth: true
            Layout.margins: 10
            Layout.topMargin: 0
            visible: block.expanded && block.truncated
            text: "Treść skrócona — pokazano początek pliku."
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt * 0.9
            wrapMode: Text.Wrap
        }
    }

    FontMetrics {
        id: numbers
        font.family: Theme.monoFamily
        font.pointSize: Tokens.fontSizePt * 0.88
        readonly property real digit: Math.ceil(advanceWidth("0"))
    }

    // „Kopiuj” wariantu jednowierszowego (w zwijanym stoi w nagłówku).
    Button {
        id: copyButton
        objectName: "codeBlockCopy"
        visible: block.oneLine
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        text: block.copied ? "Skopiowano" : "Kopiuj"
        variant: "ghost"
        small: true
        Accessible.name: block.copyName
        onClicked: block.copy()
    }
}
