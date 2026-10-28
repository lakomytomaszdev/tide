// Popup kafelka Praca (PT-40, mock zatwierdzony 28.10.2026): nagłówek „Praca” z liczbą i „＋ Nowe
// zadanie”, podsumowanie, grupy — Czeka na ciebie (brzoskwiniowe wiersze z „Przejdź”, sesja bez karty
// także z „+ karta”), W toku, Do sprawdzenia, Do zrobienia („Uruchom”) i zwinięte, wyciszone Skończone
// (sesje bez karty po turze albo bezczynne; poza licznikami) — a na dole „Otwórz Tablicę”, „Otwórz
// Sesje” i link do raportu porannego. Lista przewija się powyżej maxListHeight.
// Bez PlasmoidItem, więc da się go załadować w teście i w podglądzie (widgets/preview_work.py).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "worktexts.js" as Texts

ColumnLayout {
    id: popup

    property var work: null
    property bool available: true
    property real nowTs: Date.now() / 1000
    property bool showFinished: false
    property real maxListHeight: 600
    property string status: ""            // wynik „+ karta”: błąd albo „Założono K-31”
    property bool statusError: false
    property bool busy: false
    property var pending: ({})            // sesje, dla których „+ karta” już poszło
    // Najnowszy raport poranny (/Reports Status()) albo null.
    property var report: null
    readonly property bool hasReport: !!report && !!report.path

    signal goRequested(string sessionId, string cardId)   // „Przejdź” — do agenta, w razie czego karta albo Sesje
    signal openCardRequested(string cardId)               // wiersz karty — okno Tide na karcie
    signal launchRequested(string cardId)                 // „Uruchom” — okno Tide z „Co ma zrobić agent?”
    signal addCardRequested(var request)                  // „+ karta” — CreateCard z gotowym żądaniem
    signal newTaskRequested()
    signal boardRequested()
    signal sessionsRequested()
    signal reportRequested()
    signal finishedToggled()

    readonly property var counts: Texts.counts(work)
    readonly property var groups: available ? Texts.groups(work, nowTs, showFinished, pending) : []

    function open(row) {
        if (row.kind === "session") popup.goRequested(row.session, row.card)
        else popup.openCardRequested(row.card)
    }

    spacing: 10

    RowLayout {
        objectName: "popupHeader"
        Layout.fillWidth: true
        spacing: 8

        Text {
            text: "Praca"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1
            font.weight: Font.DemiBold
        }
        Rectangle {
            visible: popup.available && popup.counts.total > 0
            radius: height / 2
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.10)
            implicitHeight: countLabel.implicitHeight + 4
            implicitWidth: countLabel.implicitWidth + 14
            Text {
                id: countLabel
                anchors.centerIn: parent
                text: popup.counts.total
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
            }
        }
        Item { Layout.fillWidth: true }
        Tide.TextButton {
            objectName: "newTaskButton"
            variant: "outline"
            text: "＋ Nowe zadanie"
            Accessible.name: "Nowe zadanie dla agenta"
            onClicked: popup.newTaskRequested()
        }
    }

    Text {
        objectName: "popupSubtitle"
        Layout.fillWidth: true
        Layout.topMargin: -4
        text: popup.available ? Texts.subtitle(popup.counts) : "Usługa tide-daemon nie działa"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    QQC2.ScrollView {
        id: scroll
        objectName: "popupList"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(list.implicitHeight, popup.maxListHeight)
        visible: popup.groups.length > 0
        contentWidth: availableWidth
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        ColumnLayout {
            id: list
            width: scroll.availableWidth
            spacing: 2

            Repeater {
                model: popup.groups
                ColumnLayout {
                    id: section
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.topMargin: index > 0 ? 8 : 0
                    spacing: 2

                    // Nagłówek grupy; Skończone rozwija „Pokaż” / „Ukryj” po prawej.
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 8
                        Layout.rightMargin: 6
                        spacing: 6
                        Accessible.role: Accessible.Heading
                        Accessible.name: section.modelData.title + ": " + section.modelData.count
                        Text {
                            objectName: "sectionTitle"
                            Layout.fillWidth: true
                            text: section.modelData.title.toUpperCase()
                                  + (section.modelData.muted ? " (" + section.modelData.count + ")" : "")
                            textFormat: Text.PlainText
                            color: section.modelData.key === "waiting" ? Tide.Tokens.waiting : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt - 2.5
                            font.weight: Font.DemiBold
                            font.letterSpacing: 0.8
                        }
                        Tide.TextButton {
                            objectName: "finishedToggle"
                            visible: section.modelData.muted
                            accent: Tide.Tokens.textMuted
                            pointSize: Tide.Tokens.fontSizePt - 2
                            text: popup.showFinished ? "Ukryj" : "Pokaż"
                            Accessible.name: (popup.showFinished ? "Ukryj" : "Pokaż") + " skończone sesje bez karty ("
                                             + section.modelData.count + ")"
                            onClicked: popup.finishedToggled()
                        }
                    }

                    Repeater {
                        model: section.modelData.rows
                        WorkRow {
                            required property var modelData
                            Layout.fillWidth: true
                            row: modelData
                            onOpenRequested: popup.open(modelData)
                            onGoRequested: popup.goRequested(modelData.session, modelData.card)
                            onLaunchRequested: popup.launchRequested(modelData.card)
                            onAddCardRequested: if (!popup.busy) popup.addCardRequested(modelData.request)
                        }
                    }
                }
            }
        }
    }

    Text {
        Layout.fillWidth: true
        Layout.leftMargin: 8
        Layout.topMargin: 4
        Layout.bottomMargin: 4
        visible: popup.available && popup.groups.length === 0
        text: "Nic w toku ani do zrobienia — zleć agentowi nowe zadanie."
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        wrapMode: Text.WordWrap
    }

    Text {
        objectName: "popupStatus"
        Layout.fillWidth: true
        Layout.leftMargin: 8
        visible: popup.status !== ""
        text: popup.status
        textFormat: Text.PlainText
        color: popup.statusError ? Tide.Tokens.rose : Tide.Tokens.mint
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
        wrapMode: Text.WordWrap
    }

    RowLayout {
        objectName: "popupFooter"
        Layout.fillWidth: true
        spacing: 6

        Tide.TextButton {
            objectName: "boardButton"
            variant: "outline"
            text: "Otwórz Tablicę"
            Accessible.name: "Otwórz Tablicę w oknie Tide"
            onClicked: popup.boardRequested()
        }
        Tide.TextButton {
            objectName: "sessionsButton"
            variant: "outline"
            text: "Otwórz Sesje"
            Accessible.name: "Otwórz Sesje w oknie Tide: koordynatorzy, tickety i podagenci"
            onClicked: popup.sessionsRequested()
        }
        Item { Layout.fillWidth: true }
        Tide.ReportLink {
            objectName: "reportButton"
            visible: popup.hasReport
            text: Tide.Texts.reportLink(popup.report, popup.nowTs)
            unread: popup.hasReport && popup.report.unread === true
            Accessible.name: Tide.Texts.reportAccessible(popup.report, popup.nowTs)
            onClicked: popup.reportRequested()
        }
    }
}
