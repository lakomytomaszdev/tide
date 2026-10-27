// Karta „Porządki” w prawej kolumnie (mock „Tide Docker”): kategorie z Disk() z polem wyboru, opisem
// (masło przy ostrzeżeniu — wolumeny mogą trzymać dane baz, wolumeny anonimowe zatrzymanych) i miejscem
// do odzyskania. „Wyczyść zaznaczone · X GB” prosi stronę o potwierdzenie, które wymienia dokładnie,
// co zniknie; w trakcie porządków — postęp, potem wynik z ActionFinished (odzyskane bajty i błędy
// kategorii). Domyślnie nic nie jest zaznaczone.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "docker.js" as Logic

Rectangle {
    id: card

    property var disk: null
    property var picks: ({})
    property bool pruning: false
    property var result: null                // Logic.pruneResult(…) albo null
    property bool enabledActions: true
    signal toggled(string id)
    signal cleanRequested()
    signal resultDismissed()

    readonly property var categories: disk && disk.categories ? disk.categories : []
    readonly property real pickedBytes: Logic.pickedBytes(categories, picks)
    readonly property int pickedCount: Logic.pickedIds(categories, picks).length

    implicitHeight: body.implicitHeight + 28
    radius: Tide.Tokens.radiusCard
    color: Tide.Tokens.backgroundCard
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: "Porządki"
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Text {
                objectName: "reclaimable"
                visible: !!(card.disk && card.disk.ok)
                text: card.disk && card.disk.ok ? Logic.bytes(card.disk.totalReclaimableBytes || 0) + " do odzyskania" : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.neutral
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
            }
        }

        Text {
            Layout.fillWidth: true
            visible: card.categories.length === 0
            text: card.disk && card.disk.error ? "Nie udało się policzyć miejsca: " + card.disk.error
                                               : "Liczę miejsce na dysku…"
            textFormat: Text.PlainText
            color: card.disk && card.disk.error ? Tide.Tokens.negative : Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: card.categories
            Rectangle {
                id: option
                required property var modelData
                readonly property bool checked: card.picks[modelData.id] === true
                readonly property bool usable: (modelData.count || 0) > 0 && !card.pruning && card.enabledActions
                objectName: "cleanup-" + modelData.id
                Layout.fillWidth: true
                implicitHeight: optionRow.implicitHeight + 16
                radius: Tide.Tokens.radiusFrame
                color: checked ? Tide.Theme.alpha(Tide.Tokens.positive, 0.08)
                     : optionArea.containsMouse && usable ? Qt.lighter(Tide.Tokens.backgroundWindow, 1.1) : Tide.Tokens.backgroundWindow
                border.width: 1
                border.color: activeFocus ? Tide.Tokens.accent : checked ? Tide.Theme.alpha(Tide.Tokens.positive, 0.25) : "transparent"
                opacity: (modelData.count || 0) > 0 ? 1 : 0.55
                activeFocusOnTab: usable
                Accessible.role: Accessible.CheckBox
                Accessible.name: modelData.label + ", " + Logic.bytes(modelData.reclaimableBytes) + ", " + (modelData.note || "")
                Accessible.checkable: true
                Accessible.checked: checked
                Accessible.onToggleAction: if (usable) card.toggled(modelData.id)
                Accessible.onPressAction: if (usable) card.toggled(modelData.id)
                Keys.onSpacePressed: if (usable) card.toggled(modelData.id)
                Keys.onReturnPressed: if (usable) card.toggled(modelData.id)

                RowLayout {
                    id: optionRow
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 10
                    Rectangle {
                        implicitWidth: 18
                        implicitHeight: 18
                        radius: 5
                        color: option.checked ? Tide.Tokens.accent : "transparent"
                        border.width: option.checked ? 0 : 1.5
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.25)
                        Text {
                            anchors.centerIn: parent
                            visible: option.checked
                            text: "✓"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.backgroundDesktop
                            font.pixelSize: 12
                            font.weight: Font.Bold
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            Layout.fillWidth: true
                            text: option.modelData.label || option.modelData.id
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: (option.modelData.count || 0) > 0 ? (option.modelData.note || "") : "nic do odzyskania"
                            textFormat: Text.PlainText
                            color: option.modelData.warn ? Tide.Tokens.neutral : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }
                    }
                    Text {
                        text: Logic.bytes(option.modelData.reclaimableBytes || 0)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                }
                MouseArea {
                    id: optionArea
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: option.usable
                    cursorShape: Qt.PointingHandCursor
                    onClicked: card.toggled(option.modelData.id)
                }
            }
        }

        PillButton {
            objectName: "cleanButton"
            Layout.fillWidth: true
            visible: card.categories.length > 0
            enabled: card.pickedCount > 0 && !card.pruning && card.enabledActions
            text: card.pruning ? "Porządkuję…"
                : card.pickedCount > 0 ? "Wyczyść zaznaczone · " + Logic.bytes(card.pickedBytes) : "Zaznacz, co wyczyścić"
            fill: Tide.Tokens.negative
            textColor: Tide.Tokens.backgroundDesktop
            strong: true
            onClicked: card.cleanRequested()
        }

        // Postęp: zadanie w tle w usłudze — porządki potrafią trwać minutami.
        ColumnLayout {
            Layout.fillWidth: true
            visible: card.pruning
            spacing: 6
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 3
                radius: 2
                clip: true
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.10)
                Rectangle {
                    id: sweep
                    width: parent.width * 0.3
                    height: parent.height
                    radius: 2
                    color: Tide.Tokens.negative
                    SequentialAnimation on x {
                        running: card.pruning && card.visible
                        loops: Animation.Infinite
                        NumberAnimation { from: -sweep.width; to: sweep.parent.width; duration: 1400; easing.type: Easing.InOutSine }
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: "Docker usuwa zaznaczone kategorie po kolei (obrazy, wolumeny, pamięć podręczna, kontenery). "
                      + "Duże obrazy potrafią się usuwać kilka minut."
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
        }

        // Wynik ostatnich porządków.
        Rectangle {
            objectName: "pruneResult"
            Layout.fillWidth: true
            visible: card.result !== null && !card.pruning
            implicitHeight: resultBody.implicitHeight + 20
            radius: Tide.Tokens.radiusFrame
            color: Tide.Theme.alpha(card.result && card.result.ok ? Tide.Tokens.positive : Tide.Tokens.negative, 0.08)
            border.width: 1
            border.color: Tide.Theme.alpha(card.result && card.result.ok ? Tide.Tokens.positive : Tide.Tokens.negative, 0.30)
            ColumnLayout {
                id: resultBody
                anchors.fill: parent
                anchors.margins: 10
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "pruneResultTitle"
                        Layout.fillWidth: true
                        text: card.result ? card.result.title : ""
                        textFormat: Text.PlainText
                        color: card.result && card.result.ok ? Tide.Tokens.positive : Tide.Tokens.negative
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                    }
                    LinkText {
                        text: "OK"
                        onClicked: card.resultDismissed()
                    }
                }
                Repeater {
                    model: card.result ? card.result.lines : []
                    Text {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.text
                        textFormat: Text.PlainText
                        color: modelData.error ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: "Nic nie znika bez zaznaczenia. Działające kontenery, ich obrazy i wolumeny nigdy nie trafiają do porządków."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            lineHeight: 1.15
            wrapMode: Text.WordWrap
        }
    }
}
