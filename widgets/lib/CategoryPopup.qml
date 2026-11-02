// Popup kategorii ustawień nad kolumną „Teraz” (PT-105, decyzja 4 K-29). Otwiera go ikona z rzędu kategorii,
// zamyka Esc, × albo ponowne kliknięcie tej samej ikony; karty „Teraz” zostają pod spodem. Treść kategorii
// przyjdzie w PT-106 (te same komendy rdzenia co tablet) — do tego czasu krótka zapowiedź i Panel streamera.
// PT-106 podmienia `body` (Loader według `category.id`), reszta popupu zostaje.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

QQC2.Popup {
    id: popup

    // Wpis z Stream.categories(): {id, label, short, icon, warning}.
    property var category: null
    signal panelRequested()

    objectName: "categoryPopup"
    padding: 16
    height: body.implicitHeight + topPadding + bottomPadding
    modal: false
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape
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
            spacing: 8
            LineIcon {
                name: popup.category ? popup.category.icon : ""
                size: 18
                color: Tokens.accent
            }
            Text {
                text: popup.category ? popup.category.label : ""
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: "Esc zamyka"
                textFormat: Text.PlainText
                color: Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 2
            }
            IconButton {
                objectName: "categoryClose"
                iconName: "x"
                tip: "Zamknij kategorię"
                onClicked: popup.close()
            }
        }
        Text {
            visible: !!popup.category && popup.category.warning !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: popup.category ? "Wymaga uwagi: " + popup.category.warning : ""
            textFormat: Text.PlainText
            color: Tokens.neutral
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "Ustawienia tej kategorii pojawią się tutaj w kolejnym kroku. Do tego czasu są w Panelu streamera."
            textFormat: Text.PlainText
            color: Tokens.textSecondary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt
        }
        TextButton {
            objectName: "categoryPanel"
            variant: "link"
            text: "Otwórz Panel streamera"
            onClicked: popup.panelRequested()
        }
    }
}
