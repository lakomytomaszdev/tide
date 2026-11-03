// Treść jednej decyzji karty (K-35): „co się stało” (`happened`), „dlaczego” (`why`), polecenie z
// `technical.command`, uwagi recenzenta planu, możliwości jako karty z jednym zdaniem skutku (kolejność i polecona —
// z usługi), na końcu „Inna odpowiedź…” (gdy `freeText`) i zwinięte „Szczegóły techniczne”. Wspólna dla okna
// decyzji (DecisionDialog.qml, PT-132) i bloku „Twój ruch” pełnej karty (CardFullView.qml, PT-128).
//
// Formularz niczego nie liczy i niczego nie zaznacza z góry (D10): po zmianie decyzji nic nie jest wybrane, a
// `choose()` bez zaznaczenia (albo z pustą własną odpowiedzią) nic nie robi. Żądanie składa decision.js z gotowych
// pól `method` i `params` decyzji.
//
// Umowa: `decision` (jedna decyzja z `derived.flow.decisions[]`), `canChoose`, `choose()`, `error` (np. błąd usługi
// po odpowiedzi), sygnały `answered(decisionId, method, params)`, `inputNeeded(notice)` — możliwość wymaga wyboru
// spoza formularza (np. wykonawcy; na razie w panelu karty) i `escapePressed()` z pola odpowiedzi.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide
import "decision.js" as Decision

ColumnLayout {
    id: form

    readonly property string otherId: "__other__"

    property var decision: null
    property string selected: ""
    property bool techOpen: false
    property string error: ""
    // Uwagi recenzenta zaznaczone do odesłania (id z `decision.findings`); domyślne zaznaczenie daje usługa.
    property var checkedIds: []

    readonly property string decisionId: decision ? decision.id : ""
    readonly property var options: decision ? decision.options || [] : []
    readonly property bool freeText: !!decision && decision.freeText === true && !!decision.reply
    readonly property bool onlyText: freeText && options.length === 0
    readonly property string choice: onlyText ? otherId : selected
    readonly property bool canChoose: choice === otherId ? answer.text.trim() !== "" : choice !== ""
    readonly property alias answerField: answer

    signal answered(string decisionId, string method, var params)
    signal inputNeeded(string notice)
    signal escapePressed()

    function reset() {
        selected = ""
        techOpen = false
        error = ""
        answer.text = ""
        checkedIds = Decision.defaultChecked(decision)
    }

    function toggleFinding(id) {
        const at = checkedIds.indexOf(id)
        checkedIds = at >= 0 ? checkedIds.filter(x => x !== id) : checkedIds.concat([id])
        error = ""
    }

    function pick(id) {
        selected = id
        error = ""
        if (id === otherId) answer.forceActiveFocus()
    }

    // „Wybierz” i Enter: bez zaznaczenia (albo z pustą własną odpowiedzią) nic się nie dzieje.
    function choose() {
        if (!decision || !canChoose) return false
        let request = null
        if (choice === otherId) {
            request = Decision.replyRequest(decision, answer.text.trim())
        } else {
            const option = options.find(o => o.id === choice)
            if (!option) return false
            if (Decision.needsInput(option)) {
                inputNeeded("„" + option.title + "”: wybierz wykonawcę w panelu karty.")
                return false
            }
            request = Decision.optionRequest(option)
        }
        if (!request || !request.method) return false
        // Odesłanie planu: do poprawy idą tylko zaznaczone uwagi i własny tekst (niezaznaczone nie).
        request = Decision.applyFindings(decision, request, checkedIds)
        if (request.params && request.params.action === "return" && decision.findings && decision.findings.length > 0
                && (request.params.findings || []).length === 0 && !request.params.feedback) {
            error = "Zaznacz uwagi do odesłania albo dopisz własne."
            return false
        }
        answered(decision.id, request.method, request.params)
        return true
    }

    onDecisionIdChanged: reset()

    spacing: 12

    Text {
        objectName: "decisionHappened"
        Layout.fillWidth: true
        text: form.decision ? form.decision.happened : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 1.35
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "decisionWhy"
        Layout.fillWidth: true
        text: form.decision ? form.decision.why : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 1.05
        wrapMode: Text.Wrap
    }
    Rectangle {
        objectName: "decisionCommand"
        Layout.fillWidth: true
        visible: commandText.text !== ""
        implicitHeight: commandText.implicitHeight + 18
        radius: Tide.Tokens.radiusFrame
        color: Tide.Tokens.backgroundView
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        Text {
            id: commandText
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: 12
            text: form.decision && form.decision.technical ? Decision.text(form.decision.technical.command) : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.95
            wrapMode: Text.WrapAnywhere
        }
    }

    Text {
        objectName: "decisionError"
        Layout.fillWidth: true
        visible: form.error !== ""
        text: form.error
        textFormat: Text.PlainText
        color: Tide.Tokens.negative
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }

    // Uwagi recenzenta planu: waga, treść i tickety; do poprawy idą zaznaczone (nit domyślnie nie).
    ColumnLayout {
        objectName: "decisionFindings"
        Layout.fillWidth: true
        visible: !!form.decision && (form.decision.findings || []).length > 0
        spacing: 6
        Accessible.role: Accessible.Grouping
        Accessible.name: "Uwagi recenzenta"
        Text {
            Layout.fillWidth: true
            text: "Uwagi recenzenta — zaznaczone trafią do poprawy"
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.Medium
        }
        Repeater {
            model: form.decision ? form.decision.findings || [] : []
            delegate: DecisionFinding {
                objectName: "decisionFinding"
                required property var modelData
                Layout.fillWidth: true
                severity: modelData.severity
                text: modelData.text
                tickets: modelData.tickets
                checked: form.checkedIds.indexOf(modelData.id) >= 0
                onToggled: form.toggleFinding(modelData.id)
            }
        }
    }

    // Możliwości w kolejności z usługi; „Inna odpowiedź…” zawsze ostatnia.
    ColumnLayout {
        objectName: "decisionOptions"
        Layout.fillWidth: true
        Layout.topMargin: 4
        spacing: 8
        Accessible.role: Accessible.Grouping
        Accessible.name: "Możliwości"

        Repeater {
            model: form.options
            DecisionOption {
                objectName: "decisionOption"
                Layout.fillWidth: true
                title: modelData.title
                effect: modelData.effect
                recommended: modelData.recommended === true
                checked: form.selected === modelData.id
                onPicked: form.pick(modelData.id)
            }
        }

        Rectangle {
            id: other
            objectName: "decisionOther"
            Layout.fillWidth: true
            visible: form.freeText
            readonly property bool checked: form.choice === form.otherId
            implicitHeight: otherColumn.implicitHeight + 24
            radius: Tide.Tokens.radiusTile
            color: checked && !form.onlyText ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09) : Tide.Tokens.backgroundCard
            border.width: 1
            border.color: checked && !form.onlyText || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                   : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
            activeFocusOnTab: !form.onlyText
            Accessible.role: Accessible.RadioButton
            Accessible.name: "Inna odpowiedź…"
            Accessible.checked: checked
            Accessible.onPressAction: form.pick(form.otherId)
            Keys.onSpacePressed: form.pick(form.otherId)

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: form.pick(form.otherId)
            }

            ColumnLayout {
                id: otherColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 12
                anchors.leftMargin: 14
                spacing: 8
                RowLayout {
                    spacing: 9
                    visible: !form.onlyText
                    Rectangle {
                        implicitWidth: 14
                        implicitHeight: 14
                        radius: width / 2
                        color: "transparent"
                        border.width: other.checked ? 4 : 1.5
                        border.color: other.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                    }
                    Text {
                        text: "Inna odpowiedź…"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                }
                Text {
                    visible: form.onlyText
                    text: "Twoja odpowiedź"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    font.weight: Font.Medium
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: form.onlyText ? 0 : 23
                    visible: form.onlyText || other.checked
                    implicitHeight: Math.max(64, answer.implicitHeight + 16)
                    radius: Tide.Tokens.radiusFrame
                    color: Tide.Tokens.backgroundView
                    border.width: 1
                    border.color: answer.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                     : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                    TextEdit {
                        id: answer
                        objectName: "decisionAnswer"
                        anchors.fill: parent
                        anchors.margins: 8
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap
                        color: Tide.Tokens.textPrimary
                        selectionColor: Tide.Theme.alpha(Tide.Tokens.accent, 0.4)
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.05
                        activeFocusOnTab: true
                        Accessible.role: Accessible.EditableText
                        Accessible.name: "Inna odpowiedź"
                        onActiveFocusChanged: if (activeFocus && !form.onlyText) form.selected = form.otherId
                        // Enter wysyła jak w czacie, Shift+Enter — nowa linia; Esc oddaje do okna albo widoku.
                        Keys.onPressed: event => {
                            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                    && !(event.modifiers & Qt.ShiftModifier)) {
                                form.choose()
                                event.accepted = true
                            } else if (event.key === Qt.Key_Escape) {
                                form.escapePressed()
                                event.accepted = true
                            }
                        }
                        Text {
                            anchors.fill: parent
                            visible: answer.text === ""
                            text: "Odpowiedz własnymi słowami, jak w czacie z agentem."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font: answer.font
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }

    // Szczegóły techniczne — zwinięte.
    Tide.TextButton {
        objectName: "decisionTechToggle"
        visible: techRepeater.count > 0
        text: (form.techOpen ? "▾ " : "▸ ") + "Szczegóły techniczne"
        accent: Tide.Tokens.textMuted
        onClicked: form.techOpen = !form.techOpen
    }
    GridLayout {
        objectName: "decisionTech"
        Layout.fillWidth: true
        visible: form.techOpen
        columns: 2
        columnSpacing: 14
        rowSpacing: 4
        Repeater {
            id: techRepeater
            model: form.decision ? Decision.techRows(form.decision.technical) : []
            delegate: Item {
                id: techRow
                required property var modelData
                Layout.columnSpan: 2
                Layout.fillWidth: true
                implicitHeight: Math.max(techLabel.implicitHeight, techValue.implicitHeight)
                Text {
                    id: techLabel
                    width: 150
                    text: techRow.modelData.label
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    elide: Text.ElideRight
                }
                Text {
                    id: techValue
                    x: techLabel.width + 14
                    width: techRow.width - x
                    text: techRow.modelData.value
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    wrapMode: Text.WrapAnywhere
                }
            }
        }
    }
}
