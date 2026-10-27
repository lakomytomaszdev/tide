// Pozycja „Na daily”: „TOM-146: tekst”, pod spodem po co (about) i następny krok (dalej: …).
// Pozycja „i N więcej” wyciszona, nieaktualna („już nieaktualne”) przekreślona i wyciszona, notka
// „Skończone dziś” z dopiskiem o ręcznym sprawdzeniu. Wszystko jako zwykły tekst.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "reports.js" as Logic

ColumnLayout {
    id: entry

    property var item: ({})
    property bool muted: false

    spacing: 2

    Text {
        objectName: "itemText"
        Layout.fillWidth: true
        text: Logic.itemText(entry.item)
        textFormat: Text.PlainText
        color: entry.item.more || entry.muted || entry.item.stale ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 13
        font.italic: !!entry.item.more
        font.strikeout: !!entry.item.stale
        lineHeight: 1.25
        wrapMode: Text.Wrap
    }
    // Pozycja „Czeka na mnie”, na którą agent już nie czeka (przeliczane na żywo w usłudze).
    Text {
        objectName: "itemStale"
        Layout.fillWidth: true
        visible: !!entry.item.stale
        text: "już nieaktualne — agent na to nie czeka"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 11
    }
    // Notka „Skończone dziś”: co sprawdzić ręcznie (z planu albo karty).
    Text {
        objectName: "itemCheck"
        Layout.fillWidth: true
        visible: text !== ""
        text: Logic.checkText(entry.item)
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        lineHeight: 1.2
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "itemAbout"
        Layout.fillWidth: true
        visible: !!entry.item.about
        text: entry.item.about ? String(entry.item.about) : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        lineHeight: 1.2
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "itemNext"
        Layout.fillWidth: true
        visible: !!entry.item.next
        text: entry.item.next ? "dalej: " + String(entry.item.next) : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.teal
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        lineHeight: 1.2
        wrapMode: Text.Wrap
    }
}
