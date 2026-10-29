// Karta „Zawsze” lewego monitora Stream (PT-91, decyzja 29.10): stały pasek jak w Panelu streamera —
// mikrofon, BRB, ekran prywatności, schowanie nakładek i przejście do Panelu. Zawsze w tym samym miejscu, pod
// Scenami, niezależnie od sytuacji. Same sygnały, komendy wysyła StreamDesk/main.qml.
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

Card {
    id: card

    property var view: null
    property bool coreUp: false
    property var obsMemory: ""
    property bool busy: false

    signal commandRequested(string name, var args)
    signal panelRequested()

    readonly property var mic: Stream.card(view, coreUp, true, obsMemory).mic
    readonly property var core: view && view.core ? view.core : ({})
    readonly property bool obsUp: !!(view && view.obs && view.obs.connected)
    readonly property bool privacy: !!(core.privacy && core.privacy.value)
    readonly property bool hidden: !!(core.overlays_hidden && core.overlays_hidden.value)

    padding: 16
    spacing: 10

    Text {
        text: "Zawsze"
        textFormat: Text.PlainText
        color: Tokens.textPrimary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt + 3
        font.weight: Font.DemiBold
    }

    TextButton {
        objectName: "alwaysMic"
        Layout.fillWidth: true
        variant: "outline"
        topPadding: 14
        bottomPadding: 14
        pointSize: Tokens.fontSizePt + 1
        enabled: card.mic.enabled && !card.busy
        text: card.mic.label
        textColor: Tokens.textPrimary
        background: Rectangle {
            radius: Tokens.radiusFrame
            readonly property color tone: card.mic.warning ? Tokens.neutral : Tokens.accent
            color: Theme.alpha(tone, card.mic.warning ? 0.30 : 0.16)
            border.width: 1
            border.color: Theme.alpha(tone, 0.80)
            opacity: parent.enabled ? 1 : 0.45
        }
        onClicked: card.commandRequested("mic.set", { muted: card.mic.muted !== true })
    }

    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 8
        rowSpacing: 8
        Repeater {
            model: [
                { key: "brb", text: "BRB", cmd: "brb.toggle", on: false },
                { key: "privacy", text: card.privacy ? "Zdejmij zasłonę" : "Ekran prywatności", cmd: "privacy.toggle", on: card.privacy },
                { key: "overlays", text: card.hidden ? "Pokaż nakładki" : "Schowaj nakładki", cmd: "overlays.toggle", on: card.hidden },
                { key: "panel", text: "Panel streamera", cmd: "", on: false }
            ]
            TextButton {
                required property var modelData
                objectName: "always-" + modelData.key
                Layout.fillWidth: true
                variant: "outline"
                topPadding: 10
                bottomPadding: 10
                pointSize: Tokens.fontSizePt
                enabled: modelData.cmd === "" || (card.obsUp && !card.busy)
                text: modelData.text
                textColor: modelData.cmd === "" ? Tokens.accent : Tokens.textPrimary
                background: Rectangle {
                    radius: Tokens.radiusSmall
                    color: modelData.on ? Theme.alpha(Tokens.neutral, 0.30) : Theme.alpha(Tokens.textPrimary, 0.04)
                    border.width: 1
                    border.color: Theme.alpha(Tokens.textPrimary, 0.14)
                    opacity: parent.enabled ? 1 : 0.45
                }
                onClicked: modelData.cmd === "" ? card.panelRequested() : card.commandRequested(modelData.cmd, {})
            }
        }
    }
}
