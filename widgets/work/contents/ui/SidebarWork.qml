// Kafelek Praca w pasku bocznym (PT-40, zastępuje kafelki Agenci i Tablica): „Praca · 10”, linia
// „● 3 w toku · ● 2 czeka · ● 2 do sprawdzenia” tym samym językiem co kafelek w docku i pod spodem
// najwyżej maxRows wierszy — najpierw to, co czeka na ciebie (z „Przejdź”). Resztę i Skończone pokazuje
// „+N więcej” (pełna lista w okienku obok paska, jak dawniej u Agentów). W nagłówku „＋ Nowe zadanie”,
// na dole „Tablica” i „Sesje” (okno Tide). Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "worktexts.js" as Texts

Tide.SidebarTile {
    id: tile

    property var work: null
    property bool available: true
    property real nowTs: Date.now() / 1000
    property int maxRows: Texts.SIDEBAR_ROWS
    property string problem: ""          // "not-installed", "no-service" albo ""
    signal goRequested(string sessionId, string cardId)
    signal openCardRequested(string cardId)
    signal launchRequested(string cardId)
    signal newTaskRequested()
    signal boardRequested()
    signal sessionsRequested()
    signal moreRequested()

    readonly property var counts: Texts.counts(work)
    readonly property var parts: available ? Texts.tileParts(counts, false) : []
    readonly property var listing: available ? Texts.sidebarRows(work, nowTs, maxRows) : ({ rows: [], more: 0 })
    readonly property var rows: listing.rows
    readonly property int hiddenCount: listing.more + (available ? counts.finished : 0)
    readonly property string summary: available ? Texts.tileSummary(counts, false) : "usługa nie działa"
    readonly property bool showProblem: !available || problem !== ""
    readonly property string problemText: !available || problem === "no-service"
        ? "Usługa tide-daemon nie odpowiada" : "Okno Tide nie jest zainstalowane"

    title: "Praca" + (available && counts.total ? " · " + counts.total : "")
    spacing: 8
    Accessible.name: "Praca: " + (available ? summary : problemText)

    headerItems: Tide.TextButton {
        objectName: "newTaskButton"
        text: "＋ Nowe zadanie"
        Accessible.name: "Nowe zadanie dla agenta"
        onClicked: tile.newTaskRequested()
    }

    Row {
        visible: tile.available
        spacing: 6
        Repeater {
            model: tile.parts
            Row {
                id: part
                required property var modelData
                required property int index
                spacing: 6
                Text {
                    visible: part.index > 0
                    anchors.verticalCenter: parent.verticalCenter
                    text: "·"
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 7
                    radius: width / 2
                    color: part.modelData.hollow ? "transparent" : Tide.Tokens[part.modelData.tint]
                    border.width: part.modelData.hollow ? 1.5 : 0
                    border.color: Tide.Tokens[part.modelData.tint]
                }
                Text {
                    id: number
                    text: part.modelData.count
                    color: part.modelData.tint === "waiting" ? Tide.Tokens.waiting : Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    font.weight: Font.DemiBold
                }
                Text {
                    anchors.baseline: number.baseline
                    text: part.modelData.word
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
            }
        }
        Text {
            visible: tile.parts.length === 0
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: tile.rows.length > 0
        spacing: 2
        Repeater {
            model: tile.rows
            WorkRow {
                required property var modelData
                Layout.fillWidth: true
                compact: true
                row: modelData
                onOpenRequested: modelData.kind === "session" ? tile.goRequested(modelData.session, modelData.card)
                                                              : tile.openCardRequested(modelData.card)
                onGoRequested: tile.goRequested(modelData.session, modelData.card)
                onLaunchRequested: tile.launchRequested(modelData.card)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Tide.TextButton {
            id: moreButton
            objectName: "moreButton"
            visible: tile.hiddenCount > 0
            text: "+" + tile.hiddenCount + " więcej"
            Accessible.name: "Pokaż całą pracę (" + tile.hiddenCount + " więcej)"
            onClicked: tile.moreRequested()
        }
        Item { Layout.fillWidth: true }
        Tide.TextButton {
            objectName: "boardButton"
            text: tile.showProblem ? "Spróbuj ponownie" : "Tablica"
            Accessible.name: "Otwórz Tablicę w oknie Tide"
            onClicked: tile.boardRequested()
        }
        Tide.TextButton {
            objectName: "sessionsButton"
            text: "Sesje"
            Accessible.name: "Otwórz Sesje w oknie Tide: koordynatorzy, tickety i podagenci"
            onClicked: tile.sessionsRequested()
        }
    }

    Text {
        Layout.fillWidth: true
        visible: tile.showProblem
        text: tile.problemText
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    readonly property alias moreButton: moreButton
}
