// Tide.Sheet (K-128, PT-504): rama szuflady z prawej według makiety S-9 (`.sheet`, `.dim`, obszar „Karta z Tablicy”).
//
// Gospodarz rozciąga Sheet na widok (`anchors.fill: parent`) i ustawia `visible`. Sheet rysuje przyciemnienie pod
// spodem (klik w nie = `closeRequested()`), szufladę przy prawej krawędzi i w niej, od góry: nagłówek z ✕, opcjonalny
// pasek kroków, przewijaną treść (`Tide.ScrollBar`) i stałą stopkę. Szerokość to `widthFraction` szerokości okna Tide
// (nie widoku: okno 1380 px z paskiem bocznym 200 px daje widokowi 1180 px, a szuflada ma mieć ok. 64 % okna), nie
// mniej niż `minSheetWidth` i nie więcej niż szerokość widoku.
//
// Umowa:
// - dzieci Sheeta (domyślna właściwość `content`) trafiają do przewijanej kolumny; `scrolls: false` wyłącza
//   przewijanie ramy, a kolumna dostaje całą wysokość (treść z własnym przewijaniem, np. dokument, używa
//   `Layout.fillHeight`),
// - `header` — elementy nagłówka (RowLayout, przed ✕), `footer` — elementy stopki (RowLayout; pusta stopka znika),
// - `steps` (lista napisów) i `currentStep` — pasek kroków pod nagłówkiem; pusta lista = bez paska,
// - `closeRequested()` — ✕, klik w przyciemnienie albo Esc. Esc zamyka, chyba że treść go przejmie:
//   `contentTakesEscape: true` oznacza, że Sheet nie zamyka i nie przyjmuje zdarzenia, więc idzie do rodzica
//   gospodarza (np. tryb edycji karty wraca do czytania). Zdarzenie przyjęte przez element treści z fokusem
//   do Sheeta w ogóle nie dochodzi.
// Kolory wyłącznie z Tokens (paleta theme/palette/).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Window

FocusScope {
    id: sheet

    property real widthFraction: 0.64
    property real minSheetWidth: 640
    property string title: ""                 // opis dla czytników ekranu (nagłówek niesie gospodarz)
    property var steps: []
    property int currentStep: 0
    property bool scrolls: true
    property bool contentTakesEscape: false
    property bool closeOnDim: true
    property bool showFooter: true             // stopka pokazuje się tylko z elementami i przy `showFooter`
    property real contentPadding: 24
    default property alias content: column.data
    property alias header: headerRow.data
    property alias footer: footerRow.data
    readonly property alias sheetItem: drawer
    readonly property alias flick: flick
    readonly property alias contentColumn: column
    readonly property alias closeButton: closeButton
    readonly property real referenceWidth: Window.width > 0 ? Window.width : width
    readonly property real sheetWidth: Math.min(width, Math.max(minSheetWidth, Math.round(widthFraction * referenceWidth)))

    signal closeRequested()

    focus: true
    Accessible.role: Accessible.Dialog
    Accessible.name: title

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape && !sheet.contentTakesEscape) {
            sheet.closeRequested()
            event.accepted = true
        }
    }

    // Przyciemnienie: klik poza szufladą zamyka, kółko myszy nie przechodzi do widoku pod spodem.
    Rectangle {
        objectName: "sheetDim"
        anchors.fill: parent
        color: Theme.alpha(Tokens.backgroundDesktop, 0.55)
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onClicked: if (sheet.closeOnDim) sheet.closeRequested()
            onWheel: wheel => wheel.accepted = true
        }
    }

    Rectangle {
        id: drawer
        objectName: "sheetDrawer"
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        width: sheet.sheetWidth
        color: Tokens.backgroundPanel

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 1
            color: Theme.alpha(Tokens.textPrimary, Tokens.opacityOutline)
        }
        // Klik w szufladę nie dochodzi do przyciemnienia.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onWheel: wheel => wheel.accepted = true
        }

        // Nagłówek.
        Item {
            id: headerBox
            objectName: "sheetHeader"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: Math.max(56, headerRow.implicitHeight + 24)
            RowLayout {
                id: headerRow
                anchors.left: parent.left
                anchors.right: closeButton.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: sheet.contentPadding - 4
                anchors.rightMargin: 10
                spacing: 10
            }
            Button {
                id: closeButton
                objectName: "sheetClose"
                anchors.right: parent.right
                anchors.rightMargin: 14
                anchors.verticalCenter: parent.verticalCenter
                variant: "ghost"
                iconOnly: true
                text: "✕"
                toolTip: "Zamknij (Esc)"
                Accessible.name: "Zamknij"
                onClicked: sheet.closeRequested()
            }
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
            }
        }

        // Pasek kroków (opcjonalny).
        Item {
            id: stepsBar
            objectName: "sheetSteps"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: headerBox.bottom
            visible: sheet.steps.length > 0
            height: visible ? 40 : 0
            Row {
                anchors.left: parent.left
                anchors.leftMargin: sheet.contentPadding - 4
                anchors.verticalCenter: parent.verticalCenter
                spacing: 18
                Repeater {
                    model: sheet.steps
                    delegate: Text {
                        required property var modelData
                        required property int index
                        text: (index + 1) + ". " + modelData
                        textFormat: Text.PlainText
                        color: index === sheet.currentStep ? Tokens.accent : Tokens.textMuted
                        font.family: Theme.uiFamily
                        font.pointSize: Tokens.fontSizePt
                        font.weight: index === sheet.currentStep ? Font.DemiBold : Font.Normal
                    }
                }
            }
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
            }
        }

        // Stopka.
        Item {
            id: footerBox
            objectName: "sheetFooter"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            visible: sheet.showFooter && footerRow.children.length > 0
            height: visible ? footerRow.implicitHeight + 24 : 0
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: 1
                color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
            }
            RowLayout {
                id: footerRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: sheet.contentPadding - 4
                anchors.rightMargin: 20
                spacing: 8
            }
        }

        // Przewijana treść.
        Flickable {
            id: flick
            objectName: "sheetBody"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: stepsBar.visible ? stepsBar.bottom : headerBox.bottom
            anchors.bottom: footerBox.visible ? footerBox.top : parent.bottom
            clip: true
            contentWidth: width
            contentHeight: sheet.scrolls ? column.implicitHeight + 48 : height
            interactive: sheet.scrolls
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: ScrollBar {}

            ColumnLayout {
                id: column
                x: sheet.contentPadding
                y: 18
                // Pasek przewijania leży na krawędzi: treść go nie zasłania.
                width: flick.width - sheet.contentPadding - sheet.contentPadding + 4 - Tokens.scrollbarWidth
                height: sheet.scrolls ? implicitHeight : flick.height - 18 - 12
                spacing: 14
            }
        }
    }
}
