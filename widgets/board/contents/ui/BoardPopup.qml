// Popup kafelka tablicy (mock zatwierdzony 27.10.2026): nagłówek „Tablica” z „Otwórz w Tide ↗”,
// podsumowanie („11 kart w 7 projektach · Firma 5 · własne 6”), wiersze — najpierw to, co czeka
// na ciebie (brzoskwiniowy wiersz z „Przejdź”), potem w toku (postęp 3/10), do sprawdzenia i do
// zrobienia — a na dole szybkie dopisanie do backlogu („Dopisz do backlogu i Enter”) z wyborem
// projektu. Kliknięcie wiersza otwiera okno Tide na tej karcie, a „Uruchom” przy karcie do zrobienia —
// okno Tide od razu z „Co ma zrobić agent?” (ShowWindow("launch:K-12")). Teksty z tablicy to dane: zwykły tekst.
// Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "boardtexts.js" as Texts

ColumnLayout {
    id: popup

    property var snapshot: null
    property bool available: true
    property bool loading: false         // pierwsza migawka jeszcze się przygotowuje (PT-385)
    property string quickProject: ""
    property string status: ""           // komunikat po dopisaniu: błąd albo „Dopisano K-23”
    property bool statusError: false
    property bool busy: false
    signal openRequested(string cardId)  // pusty — samo okno Tide na widoku Tablica
    signal launchRequested(string cardId)  // „Uruchom” w wierszu do zrobienia
    signal agentRequested(string sessionId, string cardId)  // „Przejdź” — okno agenta czekającej karty
    signal addRequested(string title, string project)
    signal projectChosen(string slug)

    readonly property var counts: Texts.counts(snapshot)
    readonly property var listing: Texts.rows(snapshot, Texts.MAX_ROWS)
    readonly property var rows: listing.rows
    readonly property var projectList: Texts.projects(snapshot)
    readonly property string projectSlug: Texts.resolveProject(snapshot, quickProject)
    readonly property alias input: input

    function submit() {
        const title = input.text.trim()
        if (!title || busy || !available) return
        addRequested(title, projectSlug)
    }

    // Po udanym dopisaniu pole się czyści (woła main.qml).
    function clearInput() {
        input.text = ""
    }

    spacing: 12

    RowLayout {
        objectName: "popupHeader"
        Layout.fillWidth: true
        spacing: 10
        // Znak tablicy: trzy kolumny różnej wysokości (jak w pasku okna Tide).
        Row {
            spacing: 2
            Layout.alignment: Qt.AlignVCenter
            Repeater {
                model: [16, 11, 7]
                Rectangle {
                    required property int modelData
                    width: 5
                    height: modelData
                    radius: 1.5
                    color: "transparent"
                    border.width: 1.8
                    border.color: Tide.Tokens.mint
                }
            }
        }
        Text {
            Layout.fillWidth: true
            text: "Tablica"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1
            font.weight: Font.DemiBold
        }
        Tide.TextButton {
            objectName: "openTide"
            text: "Otwórz w Tide ↗"
            Accessible.name: "Otwórz tablicę w oknie Tide"
            onClicked: popup.openRequested("")
        }
    }

    Text {
        objectName: "popupSubtitle"
        Layout.fillWidth: true
        text: !popup.available ? "Usługa tide-daemon nie działa"
            : popup.loading ? "Tablica się wczytuje…" : Texts.subtitle(popup.counts)
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    ColumnLayout {
        objectName: "popupRows"
        Layout.fillWidth: true
        spacing: 2
        visible: popup.available

        Repeater {
            model: popup.rows
            Rectangle {
                id: row
                required property var modelData
                readonly property bool waiting: modelData.kind === "waiting"
                Layout.fillWidth: true
                implicitHeight: rowLayout.implicitHeight + 14
                radius: 10
                color: waiting ? Tide.Theme.alpha(Tide.Tokens.peach, rowArea.containsMouse ? 0.16 : 0.10)
                     : rowArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                Accessible.role: Accessible.Button
                Accessible.name: modelData.id + " " + modelData.project + ", " + modelData.word + ": " + modelData.line
                Accessible.onPressAction: popup.openRequested(modelData.id)

                MouseArea {
                    id: rowArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: popup.openRequested(row.modelData.id)
                }

                RowLayout {
                    id: rowLayout
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: 10

                    Rectangle {
                        implicitWidth: 8
                        implicitHeight: 8
                        radius: 4
                        color: row.modelData.hollow ? "transparent" : Tide.Tokens[row.modelData.tint]
                        border.width: row.modelData.hollow ? 1.5 : 0
                        border.color: Tide.Tokens[row.modelData.tint]
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Row {
                            Layout.fillWidth: true
                            spacing: 5
                            clip: true
                            Text {
                                text: row.modelData.id
                                textFormat: Text.PlainText
                                color: Tide.Tokens.sky
                                font.family: Tide.Theme.monoFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 1.5
                                anchors.baseline: projectText.baseline
                            }
                            Text {
                                id: projectText
                                text: row.modelData.project
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: row.modelData.line
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt - 2
                            elide: Text.ElideRight
                        }
                    }
                    Text {
                        text: row.modelData.word
                        textFormat: Text.PlainText
                        color: row.modelData.tint === "textSecondary" ? Tide.Tokens.textMuted : Tide.Tokens[row.modelData.tint]
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                    // Akcja: „Przejdź” przy czekającej, postęp przy pracującej, link przy reszcie.
                    Tide.TextButton {
                        visible: row.waiting
                        variant: "filled"
                        accent: Tide.Tokens.peach
                        text: "Przejdź"
                        Accessible.name: (row.modelData.session ? "Przejdź do agenta karty " : "Przejdź do karty ") + row.modelData.id
                        // Do agenta, który czeka (Konsola albo wątek Codexa); bez sesji — karta w oknie Tide.
                        onClicked: row.modelData.session ? popup.agentRequested(row.modelData.session, row.modelData.id)
                                                         : popup.openRequested(row.modelData.id)
                    }
                    Text {
                        visible: !row.waiting && row.modelData.progress !== ""
                        Layout.preferredWidth: 44
                        horizontalAlignment: Text.AlignRight
                        text: row.modelData.progress
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                    Text {
                        objectName: "launchRow"
                        visible: row.modelData.kind === "todo"
                        Layout.preferredWidth: 52
                        horizontalAlignment: Text.AlignRight
                        text: "Uruchom"
                        textFormat: Text.PlainText
                        color: launchArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                        Accessible.role: Accessible.Button
                        Accessible.name: "Uruchom agenta dla karty " + row.modelData.id
                        Accessible.onPressAction: popup.launchRequested(row.modelData.id)
                        MouseArea {
                            id: launchArea
                            anchors.fill: parent
                            anchors.margins: -4
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: popup.launchRequested(row.modelData.id)
                        }
                    }
                    Text {
                        visible: !row.waiting && row.modelData.progress === "" && row.modelData.kind !== "todo"
                        Layout.preferredWidth: 44
                        horizontalAlignment: Text.AlignRight
                        text: "Pokaż"
                        textFormat: Text.PlainText
                        color: rowArea.containsMouse ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.accent, 0.8)
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            visible: popup.listing.more > 0
            text: "+ " + popup.listing.more + " w Tide"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 2
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.topMargin: 4
            Layout.bottomMargin: 4
            visible: popup.rows.length === 0
            text: "Nic w toku ani do zrobienia — dopisz kartę niżej."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            wrapMode: Text.WordWrap
        }
    }

    // Szybkie dopisanie do backlogu.
    Rectangle {
        objectName: "quickAdd"
        Layout.fillWidth: true
        implicitHeight: 38
        radius: Tide.Tokens.radiusFrame
        color: Tide.Tokens.backgroundView
        border.width: 1
        border.color: input.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
        opacity: popup.available ? 1 : 0.5

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 6
            spacing: 8
            Text {
                text: "＋"
                textFormat: Text.PlainText
                color: Tide.Tokens.accent
                font.pointSize: Tide.Tokens.fontSizePt + 1
            }
            TextInput {
                id: input
                objectName: "quickInput"
                Layout.fillWidth: true
                enabled: popup.available
                color: Tide.Tokens.textPrimary
                selectionColor: Tide.Tokens.accent
                selectedTextColor: Tide.Tokens.backgroundDesktop
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                maximumLength: 200
                clip: true
                onAccepted: popup.submit()
                onTextEdited: if (popup.statusError) popup.status = ""
                Accessible.role: Accessible.EditableText
                Accessible.name: "Dopisz do backlogu"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: input.text === ""
                    text: "Dopisz do backlogu i Enter"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font: input.font
                }
            }
            Rectangle {
                id: projectChip
                objectName: "quickProject"
                implicitHeight: 26
                implicitWidth: Math.min(chipRow.implicitWidth + 16, 170)
                radius: 7
                color: chipArea.containsMouse || projectMenu.opened ? Qt.lighter(Tide.Tokens.backgroundCard, 1.15)
                                                                   : Tide.Tokens.backgroundCard
                Accessible.role: Accessible.ComboBox
                Accessible.name: "Projekt nowej karty: " + Texts.projectName(popup.snapshot, popup.projectSlug)
                Row {
                    id: chipRow
                    anchors.centerIn: parent
                    width: Math.min(implicitWidth, projectChip.width - 16)
                    spacing: 5
                    clip: true
                    Text {
                        text: "◆"
                        textFormat: Text.PlainText
                        color: popup.projectSlug ? Tide.Tokens.accent : Tide.Tokens.textMuted
                        font.pointSize: Tide.Tokens.fontSizePt - 2
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: Texts.projectName(popup.snapshot, popup.projectSlug) + " ▾"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                        elide: Text.ElideRight
                    }
                }
                MouseArea {
                    id: chipArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: projectMenu.opened ? projectMenu.close() : projectMenu.open()
                }

                QQC2.Popup {
                    id: projectMenu
                    y: -implicitHeight - 6
                    x: parent.width - width
                    width: 230
                    padding: 6
                    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
                    background: Rectangle {
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                    }
                    contentItem: ListView {
                        implicitHeight: Math.min(8, count) * 30
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        model: popup.projectList
                        delegate: Rectangle {
                            id: option
                            required property var modelData
                            readonly property bool on: modelData.slug === popup.projectSlug
                            width: ListView.view.width
                            height: 30
                            radius: 7
                            color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                                 : optionArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                            Accessible.role: Accessible.MenuItem
                            Accessible.name: modelData.name
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                x: 10
                                width: parent.width - 20 - (newHint.visible ? newHint.width + 8 : 0)
                                text: option.modelData.name
                                textFormat: Text.PlainText
                                color: option.on ? Tide.Tokens.accent
                                     : option.modelData.slug ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 1
                                elide: Text.ElideRight
                            }
                            Text {
                                id: newHint
                                // Repozytorium, które nie jest jeszcze projektem: projekt powstanie z kartą.
                                visible: option.modelData.kind === "repo"
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                text: "nowy projekt"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.butter
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 2
                            }
                            MouseArea {
                                id: optionArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    popup.projectChosen(option.modelData.slug)
                                    projectMenu.close()
                                    input.forceActiveFocus()
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Text {
        objectName: "quickStatus"
        Layout.fillWidth: true
        Layout.topMargin: -4
        visible: popup.status !== ""
        text: popup.status
        textFormat: Text.PlainText
        color: popup.statusError ? Tide.Tokens.rose : Tide.Tokens.mint
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
        wrapMode: Text.WordWrap
    }
}
