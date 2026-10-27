// Dokument Markdown w panelu karty (plan, specyfikacja): TextEdit tylko do odczytu, z zaznaczaniem
// i kopiowaniem, jak raport podagenta w oknie Sesje (ResultPanel). Markdown z usługi jest surowy —
// HTML do pokazania robi `renderer` (backend.renderMarkdown: parser Qt bez HTML ze źródła, obrazy jako
// „(obraz: opis)”, tylko linki http(s), nagłówki z odstępem, linki w sky). To jedyne miejsce widoku
// z RichText, i tylko dla tego HTML-a; bez renderera treść idzie zwykłym tekstem. Kliknięty link
// przechodzi dalej tylko jako http(s) (drugie sprawdzenie); adres pokazuje podpowiedź.
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide
import "cardDocs.js" as Docs

TextEdit {
    id: block

    property string markdown: ""
    property var renderer: null         // funkcja(markdown, kolor linku) → HTML albo null
    signal linkClicked(string url)

    readOnly: true
    selectByMouse: true
    wrapMode: TextEdit.Wrap
    textFormat: renderer ? TextEdit.RichText : TextEdit.PlainText
    text: renderer ? (markdown ? renderer(markdown, Tide.Tokens.sky) : "") : markdown
    color: Tide.Tokens.textSecondary
    selectionColor: Tide.Tokens.accent
    selectedTextColor: Tide.Tokens.backgroundDesktop
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 13
    activeFocusOnTab: false
    Accessible.role: Accessible.StaticText

    onLinkActivated: link => {
        const url = Docs.safeLink(link)
        if (url) block.linkClicked(url)
    }

    HoverHandler {
        cursorShape: block.hoveredLink ? (Docs.safeLink(block.hoveredLink) ? Qt.PointingHandCursor : Qt.ForbiddenCursor)
                                       : Qt.IBeamCursor
    }
    QQC2.ToolTip.visible: block.hoveredLink !== ""
    QQC2.ToolTip.text: block.hoveredLink
    QQC2.ToolTip.delay: 400
}
