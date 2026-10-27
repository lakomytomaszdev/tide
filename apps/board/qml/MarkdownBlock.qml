// Dokument Markdown w panelu karty (plan, specyfikacja): TextEdit tylko do odczytu, z zaznaczaniem
// i kopiowaniem, jak raport podagenta w oknie Sesje (ResultPanel). Treść przychodzi oczyszczona przez
// usługę (bez obrazów i surowego HTML), a kliknięty link idzie dalej tylko jako http(s) — resztę
// (file:, javascript:, ścieżki względne) pomijamy; adres pokazuje podpowiedź. `styler` (backend
// styleMarkdown) dokłada po każdej zmianie odstęp nad nagłówkami i kolor linków z palety.
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide
import "cardDocs.js" as Docs

TextEdit {
    id: block

    property string markdown: ""
    property var styler: null           // funkcja(ten TextEdit, kolor linku) albo null
    signal linkClicked(string url)

    readOnly: true
    selectByMouse: true
    wrapMode: TextEdit.Wrap
    textFormat: TextEdit.MarkdownText
    text: markdown
    color: Tide.Tokens.textSecondary
    selectionColor: Tide.Tokens.accent
    selectedTextColor: Tide.Tokens.backgroundDesktop
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 13
    activeFocusOnTab: false
    Accessible.role: Accessible.StaticText

    onTextChanged: if (styler) Qt.callLater(block.restyle)
    onStylerChanged: restyle()
    Component.onCompleted: restyle()

    function restyle() {
        if (styler) styler(block, Tide.Tokens.sky)
    }

    onLinkActivated: link => {
        const url = Docs.safeLink(link)
        if (url) block.linkClicked(url)
    }

    HoverHandler {
        cursorShape: block.hoveredLink ? (Docs.safeLink(block.hoveredLink) ? Qt.PointingHandCursor : Qt.ForbiddenCursor)
                                       : Qt.IBeamCursor
    }
    QQC2.ToolTip.visible: block.hoveredLink !== ""
    QQC2.ToolTip.text: Docs.safeLink(block.hoveredLink) ? block.hoveredLink : block.hoveredLink + " — tylko podgląd, nie otwieram"
    QQC2.ToolTip.delay: 400
}
