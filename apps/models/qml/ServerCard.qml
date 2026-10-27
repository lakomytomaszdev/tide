// Karta serwera modeli: „Ten komputer” albo nazwa (Mac Studio), stan (działa / nie odpowiada / sprawdzam…),
// wersja Ollamy, liczba modeli i model w pamięci z częścią na karcie. Używany serwer ma znacznik „Używany”
// i obwódkę w tealu; inne — „Używaj tego serwera” (SetDefault: przełącznik ten komputer / Mac). Dalej
// „Sprawdź połączenie”, „Edytuj” (formularz w miejscu karty) i „Usuń” z potwierdzeniem w karcie.
// Wyniki (także odmowy usługi) są w karcie, obok przycisku, który je wywołał.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "models.js" as Logic

Rectangle {
    id: card

    property var server: null               // pozycja z Logic.servers() (ModelsPage szuka jej po nazwie)
    property var status: null
    property var page: null                  // ModelsPage: notes, busy, send()
    property bool editing: false
    property bool confirmingRemove: false
    property bool flash: false
    readonly property string tag: "server:" + (server ? server.name : "")
    readonly property var lines: Logic.loadedLines(server)
    readonly property bool isDefault: !!server && server.isDefault

    signal editRequested()
    signal editClosed()
    signal removeAsked()
    signal removeCancelled()

    function note(suffix) {
        return page ? page.notes[tag + ":" + suffix] || null : null
    }
    // Podgląd: inny adres w otwartej edycji (np. ostrzeżenie o tokenie).
    function fillUrl(url) {
        form.urlText = url
    }
    function busy(suffix) {
        return page ? page.busy[tag + ":" + suffix] === true : false
    }

    implicitHeight: body.implicitHeight + 32
    radius: Tide.Tokens.radiusBar
    color: flash ? Tide.Theme.tinted(Tide.Tokens.backgroundView, Tide.Tokens.accent, 0.12)
         : isDefault ? Tide.Theme.tinted(Tide.Tokens.backgroundView, Tide.Tokens.accent, 0.06) : Tide.Tokens.backgroundView
    border.width: 1
    border.color: isDefault || flash ? Tide.Theme.alpha(Tide.Tokens.accent, 0.45) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
    Accessible.role: Accessible.Grouping
    Accessible.name: server ? server.label + ", " + server.state.text + (isDefault ? ", używany" : "") : ""

    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 16
        spacing: 8

        // --- nagłówek: znak, nazwa, adres, „Używany” ----------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Rectangle {
                Layout.alignment: Qt.AlignTop
                implicitWidth: 38
                implicitHeight: 38
                radius: Tide.Tokens.radiusTile
                color: Tide.Theme.alpha(card.isDefault ? Tide.Tokens.accent : Tide.Tokens.textMuted, 0.12)
                Glyph {
                    anchors.centerIn: parent
                    width: 20
                    height: 20
                    name: "chip"
                    color: card.isDefault ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    objectName: "serverTitle"
                    Layout.fillWidth: true
                    text: card.server ? card.server.label : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    objectName: "serverHost"
                    Layout.fillWidth: true
                    text: card.server ? Logic.hostText(card.server.url) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 12
                    elide: Text.ElideMiddle
                }
                Text {
                    objectName: "serverAuth"
                    visible: !!card.server && card.server.auth === true
                    text: "z tokenem"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                }
            }
            Rectangle {
                objectName: "defaultMark"
                Layout.alignment: Qt.AlignTop
                visible: card.isDefault
                implicitHeight: 24
                implicitWidth: usedRow.implicitWidth + 18
                radius: height / 2
                color: Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                Row {
                    id: usedRow
                    anchors.centerIn: parent
                    spacing: 5
                    Glyph {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 12
                        height: 12
                        name: "check"
                        color: Tide.Tokens.accent
                        stroke: 2.4
                    }
                    Text {
                        text: "Używany"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.accent
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                }
            }
        }

        // --- stan ------------------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            visible: !card.editing
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                StateDot { kind: card.server ? card.server.state.kind : "checking" }
                Text {
                    objectName: "serverState"
                    Layout.fillWidth: true
                    text: card.server ? card.server.state.text + " · " + Logic.serverFacts(card.server) : ""
                    textFormat: Text.PlainText
                    color: card.server && card.server.state.kind === "offline" ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
            }
            Note {
                Layout.fillWidth: true
                visible: text !== ""
                text: card.server && card.server.state.kind === "offline" ? card.server.error : ""
            }
            Repeater {
                model: card.lines
                Text {
                    required property var modelData
                    objectName: "loadedLine"
                    Layout.fillWidth: true
                    text: "W pamięci: " + modelData.name + " · " + modelData.text
                    textFormat: Text.PlainText
                    color: modelData.slow ? Tide.Tokens.neutral : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
            Note {
                Layout.fillWidth: true
                text: card.lines.length === 0 ? Logic.loadedSummary(card.server) : ""
            }

            // Przełącznik „ten komputer / Mac”: jedno kliknięcie, role bez przypięcia idą za nim.
            PillButton {
                objectName: "useButton"
                Layout.topMargin: 4
                visible: !card.isDefault
                text: card.busy("default") ? "Przełączam…" : "Używaj tego serwera"
                enabled: !card.busy("default")
                implicitHeight: 34
                strong: true
                fill: Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                textColor: Tide.Tokens.accent
                onClicked: card.page.send("SetDefault", card.server.name, card.tag + ":default")
            }
            Note {
                Layout.fillWidth: true
                visible: !card.isDefault && text !== ""
                text: card.server && card.server.state.kind === "offline"
                      ? "Nie odpowiada — po przełączeniu raport i karty dostaną opis z reguł, dopóki nie wstanie." : ""
            }
            Note {
                objectName: "defaultNote"
                Layout.fillWidth: true
                tone: "error"
                text: card.note("default") ? card.note("default").text : ""
            }

            // --- akcje ---------------------------------------------------------------------
            Flow {
                Layout.fillWidth: true
                Layout.topMargin: 4
                visible: !card.confirmingRemove
                spacing: 16
                LinkText {
                    objectName: "testLink"
                    text: card.busy("test") ? "Sprawdzam…" : "Sprawdź połączenie"
                    enabled: !card.busy("test")
                    onClicked: card.page.send("TestEndpoint", JSON.stringify({ name: card.server.name }), card.tag + ":test")
                }
                LinkText {
                    objectName: "editLink"
                    text: "Edytuj"
                    onClicked: card.editRequested()
                }
                LinkText {
                    objectName: "removeLink"
                    text: "Usuń"
                    tint: Tide.Tokens.negative
                    onClicked: card.removeAsked()
                }
            }
            Note {
                objectName: "testNote"
                Layout.fillWidth: true
                tone: card.note("test") && card.note("test").error ? "error" : "ok"
                text: card.busy("test") ? "" : card.note("test") ? card.note("test").text : ""
            }

            // Usunięcie: potwierdzenie w karcie, odmowa usługi (domyślny, jedyny, role przypięte) tutaj.
            ColumnLayout {
                objectName: "removeConfirm"
                Layout.fillWidth: true
                visible: card.confirmingRemove
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: "Usunąć serwer „" + (card.server ? card.server.label : "") + "” z ustawień Tide? "
                          + "Modele na nim zostają, a jego zapisany token znika."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Item { Layout.fillWidth: true }
                    PillButton {
                        text: "Anuluj"
                        implicitHeight: 32
                        fill: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                        onClicked: card.removeCancelled()
                    }
                    PillButton {
                        objectName: "removeButton"
                        text: card.busy("remove") ? "Usuwam…" : "Usuń serwer"
                        enabled: !card.busy("remove")
                        implicitHeight: 32
                        strong: true
                        fill: Tide.Tokens.negative
                        textColor: Tide.Tokens.backgroundDesktop
                        onClicked: card.page.send("RemoveEndpoint", card.server.name, card.tag + ":remove")
                    }
                }
            }
            Note {
                objectName: "removeNote"
                Layout.fillWidth: true
                tone: "error"
                text: card.note("remove") ? card.note("remove").text : ""
            }
        }

        // --- edycja w miejscu karty --------------------------------------------------------------
        ServerForm {
            id: form
            objectName: "editForm"
            Layout.fillWidth: true
            Layout.topMargin: 4
            visible: card.editing
            adding: false
            status: card.status
            testing: card.busy("edit-test")
            saving: card.busy("save")
            testNote: card.note("edit-test")
            saveNote: card.note("save")
            onEdited: card.page.keepDraft(card.tag, form.draft())
            onTestRequested: request => card.page.send("TestEndpoint", request, card.tag + ":edit-test")
            onSaveRequested: request => card.page.send("SetEndpoint", request, card.tag + ":save")
            onCancelled: card.editClosed()
        }
    }

    function openForm() {
        form.load(server, page ? page.draft(tag) : null)
        focusTimer.start()
    }

    onEditingChanged: if (editing) openForm()
    // Karta odtworzona w trakcie edycji (nowy serwer na liście) — formularz ze szkicu.
    Component.onCompleted: if (editing) openForm()

    // Fokus po pokazaniu formularza; zegar ginie razem z kartą (Repeater odtwarza karty przy nowym stanie).
    Timer {
        id: focusTimer
        interval: 0
        onTriggered: if (card.editing) form.focusFirst()
    }
}
