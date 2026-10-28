// Pasek zrzutów ekranu zadania agenta: miniatury z nazwą pliku i „×”, kafelek „Zrób zrzut” w przerywanej
// ramce i podpowiedź „albo przeciągnij plik / albo wklej Ctrl+V”. Wspólny dla okna „Nowe zadanie”
// (apps/newtask) i okna „Co ma zrobić agent?” na Tablicy (apps/board). Sam niczego nie zapisuje: lista
// ścieżek przychodzi z backendu (apps/common/taskimages.py), a prośby wychodzą sygnałami. Przeciąganie
// plików łapie DropArea strony — tu tylko dropHover zmienia podpowiedź. Miniatury, które nie mieszczą się
// w szerokości, przechodzą do następnego wiersza (Flow; w układzie daj Layout.fillWidth).
import QtQuick

Flow {
    id: strip

    property var images: []
    property bool canCapture: false
    property bool busy: false
    property bool dropHover: false
    property int limit: 10
    property int thumbWidth: 150
    property int thumbHeight: 92
    property string hint: "albo przeciągnij plik\nalbo wklej Ctrl+V"

    signal captureRequested()
    signal removeRequested(string path)

    function imageName(path) {
        const parts = String(path || "").split("/")
        return parts[parts.length - 1]
    }

    spacing: 12

    Repeater {
        model: strip.images
        delegate: Rectangle {
            id: thumb
            required property string modelData
            objectName: "shot-" + strip.imageName(modelData)
            implicitWidth: strip.thumbWidth
            implicitHeight: strip.thumbHeight
            radius: 10
            color: Tokens.backgroundCard
            border.width: 1
            border.color: Theme.alpha(Tokens.textPrimary, 0.12)
            clip: true
            Accessible.role: Accessible.Graphic
            Accessible.name: "Zrzut " + strip.imageName(modelData)
            Image {
                anchors.fill: parent
                anchors.margins: 1
                source: "file://" + thumb.modelData
                sourceSize.width: strip.thumbWidth * 2
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: false
            }
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 1
                height: 22
                color: Theme.alpha(Tokens.backgroundDesktop, 0.82)
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    x: 8
                    width: parent.width - 16
                    text: strip.imageName(thumb.modelData)
                    textFormat: Text.PlainText
                    color: Tokens.textSecondary
                    font.family: Theme.monoFamily
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                }
            }
            Rectangle {
                objectName: "shotRemove-" + strip.imageName(thumb.modelData)
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: 6
                width: 18
                height: 18
                radius: 9
                color: Theme.alpha(Tokens.backgroundDesktop, 0.85)
                Accessible.role: Accessible.Button
                Accessible.name: "Usuń zrzut " + strip.imageName(thumb.modelData)
                Accessible.onPressAction: strip.removeRequested(thumb.modelData)
                Text {
                    anchors.centerIn: parent
                    text: "×"
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.pixelSize: 13
                }
                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -4
                    enabled: !strip.busy
                    cursorShape: Qt.PointingHandCursor
                    onClicked: strip.removeRequested(thumb.modelData)
                }
            }
        }
    }
    // „Zrób zrzut”: przerywana ramka, okno chowa się na czas zaznaczania obszaru.
    Item {
        id: captureTile
        objectName: "captureTile"
        visible: strip.canCapture && strip.images.length < strip.limit
        implicitWidth: strip.thumbWidth
        implicitHeight: strip.thumbHeight
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: "Zrób zrzut zaznaczonego obszaru ekranu"
        Accessible.onPressAction: if (!strip.busy) strip.captureRequested()
        Keys.onSpacePressed: if (!strip.busy) strip.captureRequested()
        Keys.onReturnPressed: if (!strip.busy) strip.captureRequested()
        Canvas {
            id: dashes
            anchors.fill: parent
            property color stroke: captureArea.containsMouse || captureTile.activeFocus
                                   ? Tokens.accent : Theme.alpha(Tokens.textPrimary, 0.28)
            onStrokeChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                ctx.strokeStyle = stroke
                ctx.lineWidth = 1
                ctx.setLineDash([4, 4])
                const r = 10, w = width - 1, h = height - 1
                ctx.beginPath()
                ctx.moveTo(r + 0.5, 0.5)
                ctx.arcTo(w + 0.5, 0.5, w + 0.5, h + 0.5, r)
                ctx.arcTo(w + 0.5, h + 0.5, 0.5, h + 0.5, r)
                ctx.arcTo(0.5, h + 0.5, 0.5, 0.5, r)
                ctx.arcTo(0.5, 0.5, w + 0.5, 0.5, r)
                ctx.closePath()
                ctx.stroke()
            }
        }
        Column {
            anchors.centerIn: parent
            spacing: 6
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "⌖"
                textFormat: Text.PlainText
                color: Tokens.accent
                font.pixelSize: 16
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Zrób zrzut"
                textFormat: Text.PlainText
                color: Tokens.textSecondary
                font.family: Theme.uiFamily
                font.pixelSize: 13
            }
        }
        MouseArea {
            id: captureArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (!strip.busy) strip.captureRequested()
        }
    }
    // Podpowiedź na wysokości miniatur, wyśrodkowana w pionie jak w wierszu.
    Item {
        width: Math.min(hintText.implicitWidth, strip.width)
        height: strip.thumbHeight
        Text {
            id: hintText
            objectName: "shotHint"
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            text: strip.dropHover ? "Upuść, aby dołączyć" : strip.hint
            textFormat: Text.PlainText
            color: strip.dropHover ? Tokens.accent : Tokens.textMuted
            font.family: Theme.uiFamily
            font.pixelSize: 12
            lineHeight: 1.3
            wrapMode: Text.Wrap
        }
    }
}
