// Możliwość w oknie decyzji: karta z tytułem i jednym zdaniem skutku, jak odpowiedź w czacie.
// Zaznaczenie trzyma okno (`checked`); karta tylko zgłasza wybór (klik, Spacja). Polecona ma plakietkę
// „polecane” — o tym, która jest polecona, decyduje usługa (`recommended`), nie okno.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide

Rectangle {
    id: option

    property string title: ""
    property string effect: ""
    property bool recommended: false
    property bool checked: false
    signal picked()

    implicitHeight: column.implicitHeight + 24
    radius: Tide.Tokens.radiusTile
    color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09)
         : area.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
    border.width: 1
    border.color: checked || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                         : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
    activeFocusOnTab: true

    Accessible.role: Accessible.RadioButton
    Accessible.name: recommended ? title + " (polecane)" : title
    Accessible.description: effect
    Accessible.checked: checked
    Accessible.onPressAction: picked()
    Keys.onSpacePressed: picked()

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        anchors.leftMargin: 14
        spacing: 5

        RowLayout {
            Layout.fillWidth: true
            spacing: 9
            Rectangle {
                objectName: "decisionOptionMark"
                implicitWidth: 14
                implicitHeight: 14
                radius: width / 2
                color: "transparent"
                border.width: option.checked ? 4 : 1.5
                border.color: option.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
            }
            Text {
                Layout.fillWidth: true
                text: option.title
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 1.1
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            Rectangle {
                objectName: "decisionRecommended"
                visible: option.recommended
                implicitWidth: recommendedText.implicitWidth + 14
                implicitHeight: recommendedText.implicitHeight + 6
                radius: Tide.Tokens.radiusSmall
                color: Tide.Theme.alpha(Tide.Tokens.positive, 0.14)
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.positive, 0.4)
                Text {
                    id: recommendedText
                    anchors.centerIn: parent
                    text: "polecane"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.positive
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.85
                    font.weight: Font.Medium
                }
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 23
            visible: text !== ""
            text: option.effect
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            wrapMode: Text.Wrap
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            option.forceActiveFocus()
            option.picked()
        }
    }
}
