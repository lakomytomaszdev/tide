// Karta „Zawsze” lewego monitora Stream (PT-91, PT-105 — decyzje 1 i 6 K-29): stała warstwa jak pasek Zawsze
// na tablecie — mikrofon, BRB, ekran prywatności, schowanie grafik i „Wynik u widzów” (nakładka wyniku,
// niezależna od karty wyniku w kolumnie „Teraz”). Zawsze w tym samym miejscu, nad Scenami, niezależnie od
// sytuacji. Same sygnały, komendy wysyła StreamDesk/main.qml.
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

    readonly property var mic: Stream.card(view, coreUp, true, obsMemory).mic
    readonly property var core: view && view.core ? view.core : ({})
    readonly property bool obsUp: !!(view && view.obs && view.obs.connected)
    readonly property bool privacy: !!(core.privacy && core.privacy.value)
    readonly property bool hidden: !!(core.overlays_hidden && core.overlays_hidden.value)
    readonly property var overlay: Stream.scoreOverlay(view, coreUp)

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

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Repeater {
            model: [
                { key: "brb", text: "BRB", cmd: "brb.toggle", on: false, tip: "BRB i powrót (Meta+B)" },
                { key: "privacy", text: card.privacy ? "Zdejmij zasłonę" : "Prywatność", cmd: "privacy.toggle",
                  on: card.privacy, tip: card.privacy ? "Zdejmij ekran prywatności (Meta+Pause)" : "Ekran prywatności (Meta+Pause)" },
                { key: "overlays", text: card.hidden ? "Pokaż grafiki" : "Schowaj grafiki", cmd: "overlays.toggle",
                  on: card.hidden, tip: card.hidden ? "Pokaż nakładkę na grze" : "Schowaj nakładkę na grze" }
            ]
            TextButton {
                required property var modelData
                objectName: "always-" + modelData.key
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                variant: "outline"
                topPadding: 10
                bottomPadding: 10
                pointSize: Tokens.fontSizePt
                enabled: card.obsUp && !card.busy
                text: modelData.text
                textColor: Tokens.textPrimary
                Accessible.name: modelData.tip
                background: Rectangle {
                    radius: Tokens.radiusSmall
                    color: modelData.on ? Theme.alpha(Tokens.neutral, 0.30) : Theme.alpha(Tokens.textPrimary, 0.04)
                    border.width: 1
                    border.color: Theme.alpha(Tokens.textPrimary, 0.14)
                    opacity: parent.enabled ? 1 : 0.45
                }
                onClicked: card.commandRequested(modelData.cmd, {})
            }
        }
    }

    // Nakładka wyniku u widzów (observer.overlay.set); karta wyniku na panelu ma osobne przypięcie.
    TextButton {
        objectName: "always-score"
        Layout.fillWidth: true
        variant: "outline"
        topPadding: 10
        bottomPadding: 10
        pointSize: Tokens.fontSizePt
        enabled: card.overlay.enabled && !card.busy
        text: card.overlay.label
        textColor: Tokens.textPrimary
        Accessible.name: card.overlay.label + (card.overlay.on ? ", schowaj wynik widzom" : ", pokaż wynik widzom")
        background: Rectangle {
            radius: Tokens.radiusSmall
            color: card.overlay.on && card.overlay.series ? Theme.alpha(Tokens.accent, 0.16) : Theme.alpha(Tokens.textPrimary, 0.04)
            border.width: 1
            border.color: card.overlay.on && card.overlay.series ? Theme.alpha(Tokens.accent, 0.70) : Theme.alpha(Tokens.textPrimary, 0.14)
            opacity: parent.enabled ? 1 : 0.45
        }
        onClicked: card.commandRequested("observer.overlay.set", card.overlay.args)
    }
}
