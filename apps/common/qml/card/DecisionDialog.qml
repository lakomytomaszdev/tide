// Okno decyzji karty (K-35, PT-132): wyśrodkowane nad stroną, jak pytanie w czacie. Pokazuje jedną decyzję
// z `derived.flow.decisions[]` (PT-123) naraz: plakietka rodzaju, karta · ticket · wykonawca, treść decyzji
// (DecisionForm.qml — wspólna z blokiem „Twój ruch” pełnej karty: „co się stało”, „dlaczego”, polecenie, możliwości
// z jednym zdaniem skutku, „Inna odpowiedź…”, zwinięte „Szczegóły techniczne”), stopka „Otwórz całą kartę”,
// „Później” (Esc) i „Wybierz” (Enter).
//
// Okno niczego nie liczy i niczego nie zaznacza z góry (D10): po otwarciu i po każdej odpowiedzi nic nie jest
// wybrane, „Wybierz” jest nieaktywne, a Enter bez zaznaczenia nic nie robi. Po odpowiedzi okno przechodzi do
// następnej decyzji (odpowiedziane czekają na nową migawkę ukryte); licznik „1 z N” tylko przy N > 1.
// Esc zamyka bez wysyłania — decyzje zostają w „Czeka na mnie”.
//
// Umowa: `cardId`, `decisions` (z migawki), `start()` — otwarcie od pierwszej decyzji, `failed(id, tekst)` —
// błąd usługi przy odpowiedzi (decyzja wraca), sygnały `answered(decisionId, method, params)` — żądanie
// gotowe do wysłania (metoda /Board i parametry z decyzji), `laterRequested()`, `fullCardRequested(notice)`
// (z napisem — możliwość wymaga wyboru w panelu karty, np. wykonawcy; bez — „Otwórz całą kartę”),
// `cardOpen` + `cardOpenRequested(wartość)` — pole ustawienia otwierania karty w stopce (PT-133),
// i `finished()` — nic już nie czeka (ostatnia odpowiedziana albo migawka bez decyzji).
//
// Błąd Tide (PT-125): pole własnej odpowiedzi ma podpis z usługi („Opisz problem i ponów”, `reply.label`). Gdy
// `reply.preview` jest ustawione, pierwszy wybór wysyła `previewRequested(decisionId, method, params)`, strona
// oddaje odpowiedź usługi przez `showPreview(decisionId, reply)` (karta z opisem i wycinkiem dziennika), a dopiero
// drugi wybór („Zapisz i ponów”) wysyła `answered` z `reply.method`. Zmiana opisu unieważnia podgląd.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "decision.js" as Decision

Rectangle {
    id: dialog

    property string cardId: ""
    property var decisions: []
    property string cardOpen: "decision"   // ustawienie okna Tide (PT-133); zapis robi strona, okno tylko prosi
    property var answeredIds: []
    property int index: 0

    readonly property var pending: (decisions || []).filter(d => answeredIds.indexOf(d.id) < 0)
    readonly property var current: pending.length > 0 ? pending[Math.min(index, pending.length - 1)] : null
    readonly property string counter: pending.length > 1 ? (Math.min(index, pending.length - 1) + 1) + " z " + pending.length : ""
    readonly property alias answerField: form.answerField
    readonly property alias form: form

    signal answered(string decisionId, string method, var params)
    signal previewRequested(string decisionId, string method, var params)
    signal laterRequested()
    signal fullCardRequested(string notice)
    signal cardOpenRequested(string value)
    signal finished()

    function start() {
        answeredIds = []
        index = 0
        form.reset()
        forceActiveFocus()
    }

    function go(step) {
        const next = Math.max(0, Math.min(pending.length - 1, Math.min(index, pending.length - 1) + step))
        if (next !== index) index = next
    }

    function choose() {
        form.choose()
    }

    // Usługa odrzuciła odpowiedź: decyzja wraca do listy i pokazuje błąd.
    function failed(decisionId, message) {
        answeredIds = answeredIds.filter(id => id !== decisionId)
        const at = pending.findIndex(d => d.id === decisionId)
        if (at >= 0) index = at
        form.fail(message)
    }

    // Odpowiedź usługi na podgląd karty z błędem (PT-125); podgląd innej decyzji formularz pomija.
    function showPreview(decisionId, reply) {
        form.showPreview(decisionId, reply)
        forceActiveFocus()
    }

    // Nic już nie czeka (ostatnia odpowiedź albo nowa migawka bez decyzji) — okno kończy.
    onPendingChanged: {
        if (index > 0 && index >= pending.length) index = Math.max(0, pending.length - 1)
        if (visible && pending.length === 0) finished()
    }

    color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
    focus: visible
    Accessible.role: Accessible.Dialog
    Accessible.name: current ? current.happened : "Decyzja"

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            laterRequested()
            event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            choose()
            event.accepted = true
        }
    }

    // Kliknięcia obok okna nic nie robią (okno modalne).
    MouseArea {
        anchors.fill: parent
        onWheel: wheel => wheel.accepted = true
    }

    Rectangle {
        id: panel
        objectName: "decisionPanel"
        anchors.centerIn: parent
        width: Math.min(720, parent.width - 48)
        height: Math.min(body.implicitHeight + 44 + footer.implicitHeight, parent.height - 32)
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundWindow
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
        clip: true

        MouseArea { anchors.fill: parent; onClicked: dialog.forceActiveFocus() }

        Flickable {
            id: flick
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: footer.top
            anchors.margins: 22
            anchors.bottomMargin: 10
            contentHeight: body.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

            ColumnLayout {
                id: body
                width: flick.width
                spacing: 12

                // Nagłówek: plakietka rodzaju, kto pyta, licznik i nawigacja, zamknięcie.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    DecisionKind {
                        objectName: "decisionKind"
                        decision: dialog.current
                    }
                    Text {
                        objectName: "decisionWho"
                        Layout.fillWidth: true
                        text: Decision.who(dialog.cardId, dialog.current)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        elide: Text.ElideRight
                    }
                    Tide.TextButton {
                        objectName: "decisionPrev"
                        visible: dialog.counter !== ""
                        enabled: dialog.index > 0
                        opacity: enabled ? 1 : 0.4
                        text: "‹"
                        Accessible.name: "Poprzednia decyzja"
                        onClicked: dialog.go(-1)
                    }
                    Text {
                        objectName: "decisionCounter"
                        visible: dialog.counter !== ""
                        text: dialog.counter
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "Decyzja " + text
                    }
                    Tide.TextButton {
                        objectName: "decisionNext"
                        visible: dialog.counter !== ""
                        enabled: dialog.index < dialog.pending.length - 1
                        opacity: enabled ? 1 : 0.4
                        text: "›"
                        Accessible.name: "Następna decyzja"
                        onClicked: dialog.go(1)
                    }
                    Tide.TextButton {
                        objectName: "decisionClose"
                        text: "✕"
                        variant: "link"
                        accent: Tide.Tokens.textMuted
                        Accessible.name: "Później (Esc)"
                        onClicked: dialog.laterRequested()
                    }
                }

                DecisionForm {
                    id: form
                    objectName: "decisionForm"
                    Layout.fillWidth: true
                    decision: dialog.current
                    onInputNeeded: notice => dialog.fullCardRequested(notice)
                    onEscapePressed: dialog.laterRequested()
                    onPreviewRequested: (decisionId, method, params) => dialog.previewRequested(decisionId, method, params)
                    onAnswered: (decisionId, method, params) => {
                        dialog.answeredIds = dialog.answeredIds.concat([decisionId])
                        dialog.answered(decisionId, method, params)
                        if (dialog.pending.length > 0) dialog.forceActiveFocus()
                    }
                }
            }
        }

        // Stopka: u góry ustawienie otwierania karty (to samo co w menu „Widok” Tablicy, PT-133), niżej
        // „Otwórz całą kartę” po lewej, „Później” i „Wybierz” po prawej.
        ColumnLayout {
            id: footer
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 18
            anchors.leftMargin: 22
            spacing: 10
            CardOpenSwitch {
                objectName: "decisionCardOpen"
                current: dialog.cardOpen
                onPicked: value => dialog.cardOpenRequested(value)
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Tide.TextButton {
                    objectName: "decisionFullCard"
                    text: "Otwórz całą kartę"
                    onClicked: dialog.fullCardRequested("")
                }
                Item { Layout.fillWidth: true }
                Tide.TextButton {
                    objectName: "decisionLater"
                    text: "Później  Esc"
                    variant: "outline"
                    Accessible.name: "Później"
                    onClicked: dialog.laterRequested()
                }
                Tide.TextButton {
                    objectName: "decisionChoose"
                    text: form.chooseLabel + "  ↵"
                    variant: "filled"
                    enabled: form.canChoose
                    opacity: enabled ? 1 : 0.45
                    Accessible.name: form.chooseLabel
                    onClicked: dialog.choose()
                }
            }
        }
    }
}
