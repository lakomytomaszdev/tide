// Wiersz listy Praca (popup i pasek boczny): kropka w kolorze grupy, numer karty (Geist Mono, błękit)
// albo „◌ bez karty” dla sesji bez karty, tytuł, słowo stanu i akcje — „Przejdź” przy tym, co czeka
// (brzoskwiniowe tło wiersza), „+ karta” przy sesji bez karty, „Uruchom” przy karcie do zrobienia.
// W popupie druga, wyciszona linia (pytanie agenta w cudzysłowie, notatka); w pasku bocznym (compact)
// jedna linia. Kliknięcie wiersza: karta — okno Tide na karcie, sesja — przejście do agenta.
// Teksty z usługi to dane: zwykły tekst. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "worktexts.js" as Texts

Rectangle {
    id: item

    property var row: ({})
    property bool compact: false
    signal goRequested()          // „Przejdź” — do agenta
    signal openRequested()        // kliknięcie wiersza
    signal launchRequested()      // „Uruchom” — karta do zrobienia
    signal addCardRequested()     // „+ karta” — sesja bez karty

    readonly property bool waiting: row.group === "waiting"
    readonly property bool muted: row.group === "finished"

    implicitHeight: compact ? 28 : layout.implicitHeight + 12
    radius: compact ? Tide.Tokens.radiusSmall : Tide.Tokens.radiusFrame
    color: waiting ? Tide.Theme.alpha(Tide.Tokens.peach, area.containsMouse ? 0.16 : 0.10)
         : area.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
    Accessible.role: Accessible.Button
    Accessible.name: Texts.accessibleRow(row)
    Accessible.onPressAction: item.openRequested()

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: item.openRequested()
    }

    RowLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: item.compact ? 6 : 8
        anchors.rightMargin: item.compact ? 4 : 6
        spacing: item.compact ? 7 : 9
        opacity: item.muted ? 0.62 : 1

        Rectangle {
            implicitWidth: 7
            implicitHeight: 7
            radius: width / 2
            color: item.row.hollow ? "transparent" : Tide.Tokens[item.row.tint || "textMuted"]
            border.width: item.row.hollow ? 1.5 : 0
            border.color: Tide.Tokens[item.row.tint || "textMuted"]
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            RowLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    objectName: "rowLabel"
                    text: item.row.loose ? "◌ bez karty" : item.row.label || ""
                    textFormat: Text.PlainText
                    color: item.row.loose ? Tide.Tokens.textMuted : Tide.Tokens.sky
                    font.family: item.row.loose ? Tide.Theme.uiFamily : Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - (item.compact ? 2 : 1.5)
                }
                Text {
                    Layout.fillWidth: true
                    text: item.row.title || ""
                    textFormat: Text.PlainText
                    color: item.compact ? Tide.Tokens.textSecondary : Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - (item.compact ? 1 : 0)
                    elide: Text.ElideRight
                }
            }
            Text {
                Layout.fillWidth: true
                visible: !item.compact && text !== ""
                text: item.row.line || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 2
                elide: Text.ElideRight
            }
        }

        Text {
            visible: text !== "" && !(item.compact && (item.waiting || item.row.launch))
            text: item.row.word || ""
            textFormat: Text.PlainText
            color: item.row.tint === "textSecondary" || item.muted ? Tide.Tokens.textMuted
                 : Tide.Tokens[item.row.tint || "textMuted"]
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - (item.compact ? 1.5 : 1)
        }
        Text {
            visible: !item.compact && !!item.row.progress
            text: item.row.progress || ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        Tide.TextButton {
            objectName: "goButton"
            visible: !!item.row.go && (!item.compact || item.waiting)
            variant: item.waiting ? "filled" : "link"
            accent: item.waiting ? Tide.Tokens.peach : Tide.Tokens.textMuted
            pointSize: Tide.Tokens.fontSizePt - (item.compact ? 2 : 1)
            text: "Przejdź"
            Accessible.name: item.row.loose ? "Przejdź do agenta sesji " + item.row.title
                                            : "Przejdź do agenta karty " + item.row.label
            onClicked: item.goRequested()
        }
        Tide.TextButton {
            objectName: "addCardButton"
            visible: !!item.row.addCard && !item.compact
            variant: item.muted ? "link" : "outline"
            pointSize: Tide.Tokens.fontSizePt - 1
            text: "+ karta"
            Accessible.name: "Załóż kartę z sesji " + item.row.title
            onClicked: item.addCardRequested()
        }
        Tide.TextButton {
            objectName: "launchButton"
            visible: !!item.row.launch
            pointSize: Tide.Tokens.fontSizePt - (item.compact ? 2 : 1)
            text: "Uruchom"
            Accessible.name: "Uruchom agenta dla karty " + item.row.label
            onClicked: item.launchRequested()
        }
    }
}
