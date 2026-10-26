// Karta Tide: zaokrąglone tło z palety, opcjonalnie wyróżnione kolorem (accent).
// Zawartość układa się pionowo (ColumnLayout), wysokość wynika z zawartości.
import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card

    property color accent: Tokens.accent
    property bool highlighted: false
    property real padding: 12
    property alias spacing: body.spacing
    default property alias content: body.data

    implicitWidth: body.implicitWidth + padding * 2
    implicitHeight: body.implicitHeight + padding * 2
    radius: Tokens.radiusCard
    color: highlighted ? Theme.tinted(Tokens.backgroundCard, accent, 0.09) : Tokens.backgroundCard
    border.width: 1
    border.color: highlighted ? Theme.alpha(accent, 0.55) : Theme.alpha(Tokens.textPrimary, 0.06)

    Behavior on color { ColorAnimation { duration: 200 } }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: card.padding
        spacing: 6
    }
}
