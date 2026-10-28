// Karta Stream (PT-37): stan streamu z rdzenia Streamer — popup kafelka Stream w docku i dolna karta konsoli
// streamu na pulpicie (full). Nagłówek „Stream” z trybem i tym, kto go ustawił (automat albo ty), wiersze stanu
// (antena, nagrywanie, scena, OBS, moduły), „Wycisz mikrofon · Pause”, link „Panel streamera” i dopisek, że start
// i koniec streamu są tylko w Panelu. W konsoli dochodzą profil gracza z listą, przełączniki modułów i BRB.
// Same dane i sygnały — komendy wysyła widżet przez CoreSource.execute (tylko komendy z Stream.ALLOWED).
// Bez PlasmoidItem, więc da się ją załadować w podglądzie (widgets/preview_stream.py).
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

Card {
    id: card

    property var view: null
    property bool coreUp: false
    property bool full: false
    property string status: ""
    property bool statusError: false
    property bool busy: false
    property bool profilesOpen: false
    property real textPointSize: Tokens.fontSizePt

    signal micRequested()
    signal brbRequested()
    signal panelRequested()
    signal moduleRequested(string key, bool visible)
    signal profileRequested(string id)

    readonly property var model: Stream.card(view, coreUp, full)
    readonly property var profile: Stream.profileTile(view, coreUp)
    readonly property bool ready: model.notice === ""

    padding: 16
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: "Stream"
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        Rectangle {
            visible: card.model.chip !== ""
            radius: Tokens.radiusSmall
            color: Theme.alpha(Tokens.accent, 0.20)
            border.width: 1
            border.color: Theme.alpha(Tokens.accent, 0.35)
            implicitWidth: chip.implicitWidth + 20
            implicitHeight: chip.implicitHeight + 6
            Text {
                id: chip
                anchors.centerIn: parent
                text: card.model.chip
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
        }
    }

    // Rdzeń nie działa albo jeszcze bez odczytu.
    Text {
        visible: !card.ready
        Layout.fillWidth: true
        text: card.model.notice
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: card.coreUp ? Tokens.textMuted : Tokens.neutral
        font.family: Theme.uiFamily
        font.pointSize: card.textPointSize
    }

    GridLayout {
        visible: card.ready
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 12
        rowSpacing: 7

        Repeater {
            model: card.model.rows.length * 2
            Item {
                id: cell
                required property int index
                readonly property var row: card.model.rows[Math.floor(index / 2)]
                readonly property bool isLabel: index % 2 === 0
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                implicitHeight: cellRow.implicitHeight
                Row {
                    id: cellRow
                    spacing: 6
                    width: parent.width
                    Rectangle {
                        visible: !cell.isLabel && cell.row.tint !== ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: 8
                        height: 8
                        radius: 4
                        color: cell.row.tint !== "" ? Tokens[cell.row.tint] : "transparent"
                    }
                    Text {
                        width: cellRow.width - (cell.isLabel || cell.row.tint === "" ? 0 : 14)
                        text: cell.isLabel ? cell.row.label : cell.row.value
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: cell.isLabel ? Tokens.textSecondary
                             : cell.row.tint === Stream.WARNING_TINT ? Tokens.neutral : Tokens.textPrimary
                        font.family: Theme.uiFamily
                        font.pointSize: card.textPointSize
                    }
                }
            }
        }

        // Konsola: profil gracza z listą do wyboru (profile.set).
        Text {
            visible: card.full
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.alignment: Qt.AlignVCenter
            text: "Profil gracza"
            textFormat: Text.PlainText
            color: Tokens.textSecondary
            font.family: Theme.uiFamily
            font.pointSize: card.textPointSize
        }
        TextButton {
            visible: card.full
            objectName: "profileButton"
            Layout.alignment: Qt.AlignVCenter
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            leftPadding: 0
            topPadding: 0
            bottomPadding: 0
            variant: "link"
            accent: Tokens.textPrimary
            pointSize: card.textPointSize
            enabled: card.profile.enabled
            text: card.profile.value + "  ▾"
            Accessible.name: "Profil gracza: " + card.profile.value + ", zmień"
            contentItem: Row {
                spacing: 6
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: card.profile.value
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: card.textPointSize
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: card.profilesOpen ? "▴" : "▾"
                    textFormat: Text.PlainText
                    color: Tokens.accent
                    font.family: Theme.uiFamily
                    font.pointSize: card.textPointSize
                }
            }
            onClicked: card.profilesOpen = !card.profilesOpen
        }
    }

    ProfileList {
        visible: card.full && card.profilesOpen && card.ready
        Layout.fillWidth: true
        view: card.view
        onPicked: id => { card.profilesOpen = false; card.profileRequested(id) }
    }

    // Konsola: przełączniki modułów (module.set).
    Flow {
        visible: card.full && card.ready
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: card.model.modules
            TextButton {
                required property var modelData
                objectName: "module-" + modelData.key
                variant: "outline"
                topPadding: 6
                bottomPadding: 6
                pointSize: card.textPointSize - 1
                text: modelData.label
                checkable: false
                textColor: modelData.on ? Tokens.textPrimary : Tokens.textMuted
                Accessible.name: modelData.label + (modelData.on ? ": widoczny, ukryj" : ": ukryty, pokaż")
                background: Rectangle {
                    radius: Tokens.radiusSmall
                    color: modelData.on ? Theme.alpha(Tokens.accent, 0.16) : Theme.alpha(Tokens.textPrimary, 0.04)
                    border.width: 1
                    border.color: modelData.on ? Theme.alpha(Tokens.accent, 0.75) : Theme.alpha(Tokens.textPrimary, 0.14)
                }
                onClicked: card.moduleRequested(modelData.key, !modelData.on)
            }
        }
    }

    RowLayout {
        visible: card.ready
        Layout.fillWidth: true
        Layout.topMargin: 4
        spacing: 8

        TextButton {
            objectName: "micButton"
            Layout.fillWidth: true
            variant: "outline"
            topPadding: 10
            bottomPadding: 10
            pointSize: card.textPointSize
            enabled: card.model.mic.enabled && !card.busy
            text: card.model.mic.label
            textColor: Tokens.textPrimary
            background: Rectangle {
                radius: Tokens.radiusFrame
                readonly property color tone: card.model.mic.warning ? Tokens.neutral : Tokens.accent
                color: Theme.alpha(tone, card.model.mic.warning ? 0.30 : 0.16)
                border.width: 1
                border.color: Theme.alpha(tone, 0.80)
                opacity: parent.enabled ? 1 : 0.45
            }
            onClicked: card.micRequested()
        }
        TextButton {
            visible: card.full
            objectName: "brbButton"
            variant: "outline"
            topPadding: 10
            bottomPadding: 10
            pointSize: card.textPointSize
            enabled: card.model.obsConnected && !card.busy
            text: "BRB"
            textColor: Tokens.textPrimary
            Accessible.name: "BRB i powrót (Meta+B)"
            onClicked: card.brbRequested()
        }
        TextButton {
            objectName: "panelLink"
            variant: "outline"
            topPadding: 10
            bottomPadding: 10
            pointSize: card.textPointSize
            text: "Panel streamera"
            textColor: Tokens.accent
            onClicked: card.panelRequested()
        }
    }

    Text {
        visible: card.status !== ""
        Layout.fillWidth: true
        text: card.status
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: card.statusError ? Tokens.neutral : Tokens.textSecondary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }

    Text {
        Layout.fillWidth: true
        text: card.model.note
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }
}
