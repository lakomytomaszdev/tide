// Przełącznik „Klik w kartę z Czeka na mnie: Okno decyzji | Pełna karta” (PT-133). To jedno ustawienie okna Tide
// (apps/common/windowconfig.py, `cardOpen`) pokazane w dwóch miejscach: w menu „Widok” Tablicy i w stopce okna
// decyzji. Przełącznik niczego nie zapisuje — `current` przychodzi z backendu, a wybór wraca sygnałem `picked`.
import QtQuick
import "../tide" as Tide

Row {
    id: choice

    property string current: "decision"   // "decision" | "full"
    signal picked(string value)

    readonly property var options: [{ value: "decision", label: "Okno decyzji" }, { value: "full", label: "Pełna karta" }]

    spacing: 8
    Accessible.role: Accessible.Grouping
    Accessible.name: "Klik w kartę z „Czeka na mnie”"

    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: "Klik w kartę z „Czeka na mnie”:"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.85
    }

    Rectangle {
        height: 28
        width: pills.implicitWidth + 4
        radius: height / 2
        color: Tide.Tokens.backgroundCard
        Row {
            id: pills
            anchors.centerIn: parent
            Repeater {
                model: choice.options
                Rectangle {
                    id: pill
                    required property var modelData
                    readonly property bool on: modelData.value === choice.current
                    objectName: "cardOpen-" + modelData.value
                    width: label.implicitWidth + 20
                    height: 24
                    radius: height / 2
                    color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                         : area.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                    border.width: activeFocus ? 1 : 0
                    border.color: Tide.Tokens.accent
                    activeFocusOnTab: true
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    Accessible.checked: on
                    Accessible.onPressAction: choice.picked(modelData.value)
                    Keys.onSpacePressed: choice.picked(modelData.value)
                    Text {
                        id: label
                        anchors.centerIn: parent
                        text: pill.modelData.label
                        textFormat: Text.PlainText
                        color: pill.on ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        font.weight: pill.on ? Font.DemiBold : Font.Normal
                    }
                    MouseArea {
                        id: area
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: choice.picked(pill.modelData.value)
                    }
                }
            }
        }
    }
}
