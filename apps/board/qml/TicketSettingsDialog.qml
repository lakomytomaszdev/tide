// Okno ustawień ticketu (PT-334, K-97): wykonawca, recenzent i klasa ticketu, który jeszcze nie ruszył. Otwiera się
// z przycisku „Ustawienia” na kafelku planu pełnej karty Tablicy.
//
// Umowa: `ticketId`, `row` (wiersz planu `derived.flow.plan.rows[]` z `editable`, `executor`, `reviewer`, `class`,
// `body`), `modelsStatus` (Status() z /Models). `open(id, row)` otwiera okno z obecnymi wartościami, `close()`
// zamyka. `busy` i `error` ustawia strona: odpowiedź usługi wraca do okna (błąd zostaje z komunikatem usługi).
// Sygnały: `saveRequested(request)` — gotowe `EditCardPlan {action: "edit", ticket, executor, reviewer, class,
// body}` z niezmienioną kartką z wiersza; `closed()`. Esc i „Anuluj” niczego nie wysyłają.
//
// Reguły przydziału: apps/common/qml/card/assignment.js (czyste funkcje). Listy mają wszystkie pozycje katalogu;
// niedozwolone są wyszarzone z powodem. Ticket, który już ruszył (`editable === false`), pokazuje tylko zdanie
// bez pól zapisu.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "card/assignment.js" as Assignment

Rectangle {
    id: dialog

    property string ticketId: ""
    property var row: null
    property var modelsStatus: null
    property bool busy: false
    property string error: ""
    property var selection: ({ executor: "", reviewer: "", "class": "" })

    readonly property bool started: !row || row.editable !== true
    readonly property var lists: Assignment.options(modelsStatus, selection)
    readonly property var verdict: row ? Assignment.verdict(modelsStatus, selection, {
        executor: row.executor || "", reviewer: row.reviewer || "", "class": row["class"] || "" }) : { ok: false, reason: "" }
    readonly property bool canSave: !started && verdict.ok && !busy

    signal saveRequested(var request)
    signal closed()

    function open(id, planRow) {
        ticketId = id
        row = planRow
        error = ""
        busy = false
        selection = { executor: planRow && planRow.executor || "", reviewer: planRow && planRow.reviewer || "",
                      "class": planRow && planRow["class"] || "" }
        visible = true
        forceActiveFocus()
    }

    function close() {
        if (!visible) return
        visible = false
        busy = false
        error = ""
        closed()
    }

    // Zmiana wykonawcy albo klasy przelicza wyszarzenia; recenzent zostaje tylko wtedy, gdy nadal jest dozwolony.
    function choose(group, value) {
        if (busy) return
        const next = Object.assign({}, selection)
        next[group] = value
        selection = group === "reviewer" ? next : Assignment.keepReviewer(modelsStatus, next)
        error = ""
    }

    function save() {
        if (!canSave) return
        error = ""
        busy = true
        saveRequested(Assignment.editRequest(row, selection))
    }

    visible: false
    color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
    focus: visible
    Keys.onEscapePressed: dialog.close()
    Keys.onReturnPressed: dialog.save()
    Keys.onEnterPressed: dialog.save()
    Accessible.role: Accessible.Dialog
    Accessible.name: "Ustawienia ticketu " + ticketId

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onClicked: dialog.forceActiveFocus()
    }

    Rectangle {
        id: card
        objectName: "ticketSettingsCard"
        anchors.centerIn: parent
        width: Math.min(980, parent.width - 48)
        height: Math.min(parent.height - 48, body.implicitHeight + 40)
        radius: Tide.Tokens.radiusTile
        color: Tide.Tokens.backgroundPanel
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)

        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                objectName: "ticketSettingsTitle"
                Layout.fillWidth: true
                text: "Ustawienia ticketu " + dialog.ticketId + (dialog.row && dialog.row.title ? " — " + dialog.row.title : "")
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 1.15
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }

            Text {
                objectName: "ticketSettingsStarted"
                Layout.fillWidth: true
                visible: dialog.started
                text: "Ticket już ruszył — zmiana w następnym kroku"
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
            }

            Flickable {
                id: scroller
                visible: !dialog.started
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: groups.implicitHeight
                contentHeight: groups.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                RowLayout {
                    id: groups
                    width: scroller.width
                    spacing: 12
                    Repeater {
                        model: [
                            { key: "executor", title: "Wykonawca", list: "executors" },
                            { key: "reviewer", title: "Recenzent", list: "reviewers" },
                            { key: "class", title: "Klasa", list: "classes" }
                        ]
                        delegate: ColumnLayout {
                            id: group
                            required property var modelData
                            readonly property var entries: dialog.lists[modelData.list]
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.alignment: Qt.AlignTop
                            spacing: 4
                            Text {
                                text: group.modelData.title
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                font.weight: Font.DemiBold
                            }
                            Repeater {
                                model: group.entries.length
                                delegate: Rectangle {
                                    id: choice
                                    required property int index
                                    readonly property var entry: group.entries[index]
                                    readonly property string groupKey: group.modelData.key
                                    readonly property string value: entry.value
                                    readonly property bool disabled: entry.disabled
                                    readonly property string reason: entry.reason
                                    readonly property bool checked: dialog.selection[groupKey] === entry.value
                                    objectName: "ticketSettingsOption"
                                    Layout.fillWidth: true
                                    implicitHeight: choiceColumn.implicitHeight + 14
                                    radius: Tide.Tokens.radiusSmall
                                    color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.1) : "transparent"
                                    border.width: 1
                                    border.color: checked || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                         : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                                    opacity: disabled ? 0.55 : 1
                                    activeFocusOnTab: !disabled
                                    Accessible.role: Accessible.RadioButton
                                    Accessible.name: value + (disabled ? " — niedostępne: " + reason : "")
                                    Accessible.checked: checked
                                    Accessible.onPressAction: pick()
                                    Keys.onSpacePressed: pick()
                                    function pick() {
                                        if (!disabled) dialog.choose(groupKey, value)
                                    }
                                    ColumnLayout {
                                        id: choiceColumn
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        spacing: 2
                                        Text {
                                            Layout.fillWidth: true
                                            text: (choice.checked ? "● " : "○ ") + choice.entry.label
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textPrimary
                                            font.family: Tide.Theme.uiFamily
                                            font.pointSize: Tide.Tokens.fontSizePt
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            objectName: "ticketSettingsReason"
                                            Layout.fillWidth: true
                                            Layout.leftMargin: 18
                                            visible: choice.disabled
                                            text: choice.reason
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textMuted
                                            font.family: Tide.Theme.uiFamily
                                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !choice.disabled
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            choice.forceActiveFocus()
                                            choice.pick()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Text {
                objectName: "ticketSettingsWhy"
                Layout.fillWidth: true
                visible: !dialog.started && !dialog.verdict.ok && dialog.error === ""
                text: dialog.verdict.reason
                textFormat: Text.PlainText
                color: Tide.Tokens.butter
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "ticketSettingsError"
                Layout.fillWidth: true
                visible: dialog.error !== ""
                text: dialog.error
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    objectName: "ticketSettingsCancel"
                    text: dialog.started ? "Zamknij" : "Anuluj"
                    outline: true
                    onClicked: dialog.close()
                }
                PillButton {
                    objectName: "ticketSettingsSave"
                    visible: !dialog.started
                    text: dialog.busy ? "Zapisuję…" : "Zapisz"
                    fill: Tide.Tokens.accent
                    textColor: Tide.Tokens.backgroundDesktop
                    enabled: dialog.canSave
                    onClicked: dialog.save()
                }
            }
        }
    }
}
