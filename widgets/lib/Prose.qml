// Tide.Prose (K-128, PT-501): jeden sposób pokazania dłuższego tekstu w Tide — tylko do odczytu, z zaznaczaniem.
//
// `text` to surowy tekst (agenta, usługi, karty). HTML robi prose.js: najpierw escapuje cały tekst, potem dodaje
// wyróżnienia z palety (ścieżki, polecenia, adresy, klawisze, numery kart i ticketów, listy, cytaty, kod) — bez
// linków i obrazów. `markdown: true` włącza tryb Markdown; wtedy `renderer` gospodarza (np. backend.renderMarkdown
// Tablicy: parser Qt bez HTML ze źródła, tylko linki http(s)) ma pierwszeństwo, a bez niego zostaje prose.js
// w trybie Markdown (Sesje). Kliknięty link z HTML gospodarza przechodzi dalej tylko jako http(s) (`linkClicked`).
//
// To — obok MarkdownBlock.qml Tablicy — jedyne miejsce widoku z RichText (test statyczny). Renderowanie dzieje się
// tylko przy zmianie `text`, `markdown`, `renderer` albo tła (`renders` liczy przebiegi), nie przy każdym
// przebudowaniu listy. Akapit ma najwyżej ok. `measure` znaków szerokości (80), interlinię 1,6; kopiowanie
// (Ctrl+C, `copy()`) daje czysty tekst. `collapsedLines` > 0 zwija dłuższy tekst do tylu linii z „więcej”.
import QtQuick
import QtQuick.Layouts
import "prose.js" as ProseText

Item {
    id: prose

    property string text: ""
    property bool markdown: false
    property var renderer: null          // funkcja(markdown, kolor linku) → HTML albo null
    property int collapsedLines: 0
    property bool expanded: false
    property int measure: 80             // szerokość akapitu w znakach
    property color surface: Tokens.backgroundCard   // tło pod tekstem: z nim mieszają się tła wyróżnień
    property color textColor: Tokens.textSecondary
    property real pointSize: Tokens.fontSizePt * 1.05
    readonly property alias html: body.text
    readonly property alias edit: body
    readonly property bool collapsible: collapsedLines > 0 && body.lineCount > collapsedLines
    readonly property real maxWidth: Math.ceil(metrics.averageCharacterWidth * measure)
    property int renders: 0
    signal linkClicked(string url)

    readonly property var proseColors: ProseText.colors(Tokens, surface, Theme.monoFamily)
    property bool ready: false
    // Co ostatnio narysowano: ten sam tekst, tryb, kolory i renderer nie liczą HTML-a drugi raz (np. nowy obiekt
    // decyzji z tym samym tekstem albo ponownie wyliczone, ale równe kolory).
    property string renderedKey: JSON.stringify(["", false, null])
    property var renderedWith: null

    function render() {
        if (!ready) return
        const key = JSON.stringify([text, markdown, text ? proseColors : null])
        if (key === renderedKey && renderer === renderedWith) return
        renderedKey = key
        renderedWith = renderer
        let html = ""
        if (text) {
            html = markdown && renderer ? (renderer(text, Tokens.sky) || "")
                                        : ProseText.render(text, { markdown: markdown, colors: proseColors })
        }
        body.text = html
        renders += 1
    }

    // Zaznaczenie jako czysty tekst (separatory akapitów i złamań QTextDocument → \n).
    function plainSelection() {
        return body.selectedText.replace(new RegExp("[\\u2028\\u2029]", "g"), "\n")
    }

    function copy() {
        const plain = plainSelection()
        if (!plain) return
        plainCopy.text = plain
        plainCopy.selectAll()
        plainCopy.copy()
        plainCopy.text = ""
    }

    onTextChanged: render()
    onMarkdownChanged: render()
    onRendererChanged: render()
    onProseColorsChanged: render()
    Component.onCompleted: { ready = true; render() }

    implicitWidth: Math.min(Math.ceil(body.implicitWidth), maxWidth)
    implicitHeight: frame.height + (more.visible ? more.implicitHeight + 4 : 0)
    Layout.maximumWidth: maxWidth

    Accessible.role: Accessible.StaticText
    Accessible.name: text

    FontMetrics {
        id: metrics
        font: body.font
    }

    Item {
        id: frame
        width: parent.width
        clip: true
        height: prose.collapsible && !prose.expanded
                ? Math.ceil(body.contentHeight / Math.max(1, body.lineCount) * prose.collapsedLines)
                : body.height

        TextEdit {
            id: body
            width: parent.width
            readOnly: true
            selectByMouse: true
            activeFocusOnTab: false
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.RichText
            color: prose.textColor
            selectionColor: Theme.alpha(Tokens.sky, 0.35)
            selectedTextColor: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: prose.pointSize

            Keys.onPressed: event => {
                if (event.matches(StandardKey.Copy)) {
                    prose.copy()
                    event.accepted = true
                }
            }
            onLinkActivated: link => {
                if (/^https?:\/\/[^\s<>"']+$/i.test(link)) prose.linkClicked(link)
            }
            HoverHandler {
                cursorShape: body.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor
            }
        }
    }

    TextButton {
        id: more
        objectName: "proseMore"
        anchors.top: frame.bottom
        anchors.topMargin: 4
        visible: prose.collapsible
        text: prose.expanded ? "mniej" : "więcej"
        onClicked: prose.expanded = !prose.expanded
    }

    // Pomocnik kopiowania: zwykły tekst bez znaczników trafia do schowka z pola PlainText.
    TextEdit {
        id: plainCopy
        visible: false
        textFormat: TextEdit.PlainText
    }
}
