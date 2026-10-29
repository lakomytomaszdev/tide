// Karta Sceny konsoli streamu (PT-37): podgląd bieżącej sceny programu OBS z oznaczeniem „NA ŻYWO” i nazwą sceny,
// pod nim przyciski scen trybu (scene.set, bez przytrzymania — zmiana sceny nie jest akcją krytyczną). BRB nie ma
// tu przycisku (jest osobny w karcie Stream), a sceny startu i końca na żywo są wyłączone z powodem.
// Rdzeń nie daje jeszcze scen trybu ani zrzutu sceny: wtedy spokojne „jeszcze niedostępne w rdzeniu”, a model
// (Stream.scenes, Stream.previewNote, previewImage z CoreSource) jest gotowy na te dane. Bez PlasmoidItem
// (podgląd w preview_stream.py).
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

Card {
    id: card

    property var view: null
    property bool coreUp: false
    property string previewImage: ""
    property bool previewSupported: true
    property url motionBaseUrl: ""
    property string motionKind: ""
    property bool motionPlaying: false
    property bool busy: false
    signal sceneRequested(string name)
    signal browserWindowRequested()

    function playMotionPreview(kind) {
        if (!motionBaseUrl || ["transition", "alert", "peek", "raid"].indexOf(kind) < 0) return
        motionClip.playing = false
        motionClip.source = ""
        motionKind = kind
        motionClip.source = String(motionBaseUrl) + kind + ".webp"
        motionPlaying = true
        motionClip.playing = true
        motionTimer.interval = ({ transition: 900, alert: 2500, peek: 2700, raid: 3700 })[kind]
        motionTimer.restart()
    }

    function stopMotionPreview() {
        motionTimer.stop()
        motionClip.playing = false
        motionClip.source = ""
        motionPlaying = false
    }

    readonly property var model: Stream.scenes(view, coreUp)
    readonly property string previewNote: Stream.previewNote(view, coreUp, previewSupported, previewImage !== "")

    padding: 16
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        Text {
            text: "Sceny"
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        Text {
            text: "program OBS"
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
        }
    }

    // Podgląd 16:9 bieżącej sceny.
    Rectangle {
        objectName: "scenePreview"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.round(width * 9 / 16)
        radius: Tokens.radiusFrame
        color: Tokens.backgroundView
        border.width: 1
        border.color: Theme.alpha(Tokens.textPrimary, 0.08)
        clip: true

        Image {
            anchors.fill: parent
            anchors.margins: 1
            visible: card.previewImage !== ""
            source: card.previewImage
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
        }
        // Małe, przezroczyste klipy z animacji nakładki, odtwarzane tylko lokalnie na zrzucie OBS.
        // Na żywo rdzeń oznacza test jako preview; widzowie nie dostają tego zdarzenia na nakładce.
        AnimatedImage {
            id: motionClip
            objectName: "motion-preview"
            readonly property var placement: ({
                transition: [0, 0, 1, 1],
                alert: [0.64, 0.72, 0.283, 0.136],
                peek: [0.75, 0.79, 0.136, 0.111],
                raid: [0, 0.84, 1, 0.158]
            })[card.motionKind] || [0, 0, 0, 0]
            x: parent.width * placement[0]
            y: parent.height * placement[1]
            width: parent.width * placement[2]
            height: parent.height * placement[3]
            visible: card.motionPlaying && status === Image.Ready
            fillMode: Image.Stretch
            cache: false
            playing: false
            onStatusChanged: if (status === Image.Error) card.stopMotionPreview()
        }
        Timer {
            id: motionTimer
            repeat: false
            onTriggered: card.stopMotionPreview()
        }
        Text {
            visible: card.previewNote !== "" && !card.motionPlaying
            anchors.centerIn: parent
            width: parent.width - 40
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: card.previewNote
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt
        }
        Rectangle {
            objectName: "liveBadge"
            visible: card.model.live
            x: 10; y: 10
            radius: Tokens.radiusSmall
            color: Tokens.negative
            implicitWidth: live.implicitWidth + 16
            implicitHeight: live.implicitHeight + 4
            width: implicitWidth
            height: implicitHeight
            Text {
                id: live
                anchors.centerIn: parent
                text: "NA ŻYWO"
                textFormat: Text.PlainText
                color: Tokens.backgroundWindow
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 2
                font.weight: Font.Bold
                font.letterSpacing: 0.6
            }
        }
        Rectangle {
            visible: card.model.current !== ""
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 10
            radius: Tokens.radiusSmall
            color: Theme.alpha(Tokens.backgroundWindow, 0.85)
            width: sceneName.implicitWidth + 16
            height: sceneName.implicitHeight + 4
            Text {
                id: sceneName
                anchors.centerIn: parent
                text: card.model.current
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
        }
    }

    // Przyciski scen trybu albo spokojna informacja, że rdzeń ich jeszcze nie podaje.
    GridLayout {
        visible: card.model.available
        Layout.fillWidth: true
        columns: 3
        columnSpacing: 8
        rowSpacing: 8
        Repeater {
            model: card.model.items
            TextButton {
                id: sceneButton
                required property var modelData
                objectName: "scene-" + modelData.name
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                variant: "outline"
                topPadding: 12
                bottomPadding: 12
                pointSize: Tokens.fontSizePt
                enabled: !card.busy && modelData.enabled
                text: modelData.name
                textColor: modelData.enabled ? Tokens.textPrimary : Tokens.textMuted
                Accessible.name: "Scena " + modelData.name + (modelData.current ? ", bieżąca" : "")
                                 + (modelData.reason ? ", " + modelData.reason : "")
                Accessible.description: modelData.reason
                opacity: modelData.enabled ? 1 : 0.55
                background: Rectangle {
                    radius: Tokens.radiusFrame
                    color: sceneButton.modelData.current ? Theme.alpha(Tokens.accent, 0.16)
                         : Theme.alpha(Tokens.textPrimary, sceneButton.lit ? 0.10 : 0.04)
                    border.width: sceneButton.modelData.current ? 1.5 : 1
                    border.color: sceneButton.modelData.current ? Tokens.accent
                                : Theme.alpha(Tokens.textPrimary, 0.14)
                }
                onClicked: if (!modelData.current) card.sceneRequested(modelData.name)
            }
        }
    }
    TextButton {
        objectName: "browserWindowButton"
        visible: card.model.available && card.model.items.some(item => item.name === "Browser")
        enabled: !card.busy
        variant: "link"
        text: "Wybierz okno dla Browser w OBS"
        Accessible.description: "Otwiera właściwości źródła przeglądarki w OBS"
        onClicked: card.browserWindowRequested()
    }
    // Na żywo: dlaczego start i koniec są wyłączone.
    Text {
        visible: card.model.available && card.model.note !== ""
        Layout.fillWidth: true
        text: card.model.note
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tokens.textSecondary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }
    Rectangle {
        visible: !card.model.available && card.coreUp
        Layout.fillWidth: true
        implicitHeight: unavailable.implicitHeight + 24
        radius: Tokens.radiusFrame
        color: "transparent"
        border.width: 1
        border.color: Theme.alpha(Tokens.textPrimary, 0.16)
        Text {
            id: unavailable
            anchors.centerIn: parent
            width: parent.width - 24
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: card.model.note
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
        }
    }
}
