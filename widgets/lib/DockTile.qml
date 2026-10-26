// Kafelek widżetu w docku: zaokrąglone, lekko zabarwione pastelem widżetu tło i dwie linie —
// mała etykieta w text.secondary („Agenci”) i pod nią wartość słowami (zawartość kafelka).
// Dock ma 64 px, na kafelek zostaje ok. 48 px wysokości: etykieta 8 pt i wartość 10 pt
// mieszczą się z marginesem. Bez etykiety (showLabel: false) zawartość ma całą wysokość.
import QtQuick

Rectangle {
    id: tile

    property string label: ""
    property string detail: ""            // dopisek przy etykiecie, np. „5 w pracy”
    property color detailColor: Tokens.textMuted
    property bool showLabel: label !== ""
    property color tint: Tokens.textPrimary
    property real tintStrength: 0.11
    property bool active: false            // najechany albo z otwartym popupem
    property real horizontalPadding: 11
    // Miejsce po prawej na element przyklejony do krawędzi (np. przycisk „＋” w kafelku agentów).
    property real trailingWidth: 0
    property real verticalPadding: 5
    property real labelPointSize: Tokens.fontSizePt - 2
    property real valuePointSize: Tokens.fontSizePt
    // Wysokość w docku: 64 px panelu minus marginesy pływającego panelu.
    readonly property real dockHeight: 48
    // Odstęp między sąsiednimi kafelkami (po połowie z każdej strony kafelka).
    readonly property real dockGap: 12
    property alias spacing: body.spacing
    default property alias content: body.data

    implicitWidth: Math.ceil(body.implicitWidth + horizontalPadding * 2 + trailingWidth)
    implicitHeight: Math.ceil(body.implicitHeight + verticalPadding * 2)
    radius: Tokens.radiusTile
    color: Theme.alpha(tint, active ? tintStrength * 1.9 : tintStrength)
    border.width: 1
    border.color: Theme.alpha(tint, active ? 0.40 : 0.24)

    Behavior on color { ColorAnimation { duration: 200 } }
    Behavior on border.color { ColorAnimation { duration: 200 } }

    Column {
        id: body
        x: tile.horizontalPadding
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1

        Row {
            visible: tile.showLabel
            spacing: 0
            Text {
                text: tile.label
                color: Tokens.textSecondary
                font.family: Theme.uiFamily
                font.pointSize: tile.labelPointSize
                font.weight: Font.Medium
            }
            Text {
                visible: tile.detail !== ""
                text: " · " + tile.detail
                color: tile.detailColor
                font.family: Theme.uiFamily
                font.pointSize: tile.labelPointSize
            }
        }
    }
}
