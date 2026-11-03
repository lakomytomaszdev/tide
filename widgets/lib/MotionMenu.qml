// Ustawienia i test animacji żubra w Plasmie. Komendy przechodzą przez CoreSource; na żywo
// odpowiedź motion.test uruchamia lokalny podgląd na zrzucie sceny, poza anteną gra nakładka OBS.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

QQC2.Popup {
    id: menu

    property var view: null
    property bool coreUp: false
    property bool busy: false
    signal commandRequested(string name, var args)

    readonly property var core: view && view.core ? view.core : ({})
    readonly property var motion: core.motion || ({})
    readonly property bool globalOn: !core.modules || !core.modules.motion || core.modules.motion.value !== false
    readonly property bool live: !!(view && view.obs && view.obs.streaming)
    readonly property var kinds: [
        { key: "transition", label: "Przejście scen" },
        { key: "alert", label: "Żubr przy alercie" },
        { key: "peek", label: "Wychylenie" },
        { key: "raid", label: "Stado przy raidzie" }
    ]

    padding: 16
    height: body.implicitHeight + topPadding + bottomPadding
    modal: false
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
    background: Rectangle {
        radius: Tokens.radiusCard
        color: Tokens.backgroundCard
        border.width: 1
        border.color: Theme.alpha(Tokens.accent, 0.65)
    }

    contentItem: ColumnLayout {
        id: body
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Animacje żubra"
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            TextButton {
                objectName: "motion-close"
                text: "Zamknij"
                variant: "link"
                onClicked: menu.close()
            }
        }

        TextButton {
            objectName: "motion-global"
            Layout.fillWidth: true
            variant: "outline"
            text: menu.globalOn ? "Wszystkie animacje: włączone" : "Wszystkie animacje: wyłączone"
            enabled: menu.coreUp && !menu.busy
            onClicked: menu.commandRequested("module.set", { module: "motion", visible: !menu.globalOn })
        }

        Repeater {
            model: menu.kinds
            RowLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true
                spacing: 6

                Text {
                    Layout.fillWidth: true
                    text: row.modelData.label
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                    elide: Text.ElideRight
                }
                TextButton {
                    objectName: "motion-toggle-" + row.modelData.key
                    variant: "outline"
                    text: menu.motion[row.modelData.key] === false ? "Wył." : "Wł."
                    Accessible.name: row.modelData.label + ": " + (menu.motion[row.modelData.key] === false ? "wyłączone" : "włączone")
                    enabled: menu.coreUp && !menu.busy
                    onClicked: menu.commandRequested("motion.set", {
                        key: row.modelData.key, enabled: menu.motion[row.modelData.key] === false
                    })
                }
                TextButton {
                    objectName: "motion-test-" + row.modelData.key
                    variant: "outline"
                    text: "Przetestuj"
                    Accessible.name: "Przetestuj: " + row.modelData.label
                    enabled: menu.coreUp && !menu.busy
                    onClicked: menu.commandRequested("motion.test", { kind: row.modelData.key })
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: menu.live ? "Na żywo test zobaczysz tylko tutaj, na podglądzie sceny."
                            : "Poza anteną test pojawi się na nakładce w OBS."
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
        }
    }
}
