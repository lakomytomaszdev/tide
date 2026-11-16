// Możliwość w oknie decyzji: karta z tytułem i jednym zdaniem skutku, jak odpowiedź w czacie (makieta S-9 `.opt`,
// obszar „Prośba o zgodę”, wariant A). Zaznaczenie trzyma okno (`checked`); karta tylko zgłasza wybór (klik,
// Spacja). Polecona ma plakietkę „polecane” — o tym, która jest polecona, decyduje usługa (`recommended`), nie okno.
//
// Jedno zalecenie (K-128, PT-506): oznaczenia opiekuna liczy decision.js (`optionAdvice`), karta je tylko rysuje —
// `caretakerBadge` (plakietka „opiekun”, lawenda), `note` z `tone` ("agree" — teal, "differ" — masło) i przycisk
// `link` rozwijający komentarz opiekuna (`noteOpen`, sygnał `noteToggled()`; treść komentarza wstawia formularz
// jako dziecko karty — `detail`).
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide

Rectangle {
    id: option

    property string title: ""
    property string effect: ""
    property bool recommended: false
    property bool checked: false
    property string caretakerBadge: ""
    property string note: ""
    property string tone: ""
    property string link: ""
    property bool noteOpen: false
    default property alias detail: detailHolder.data
    readonly property color noteColor: tone === "differ" ? Tide.Tokens.butter : Tide.Tokens.teal
    signal picked()
    signal noteToggled()

    implicitHeight: column.implicitHeight + 24
    radius: Tide.Tokens.radiusTile
    color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09)
         : area.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
    border.width: 1
    border.color: checked || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                : recommended ? Tide.Theme.alpha(Tide.Tokens.teal, 0.35)
                : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
    activeFocusOnTab: true

    Accessible.role: Accessible.RadioButton
    Accessible.name: title + (recommended ? " (polecane)" : "") + (caretakerBadge !== "" ? " (poleca opiekun)" : "")
    Accessible.description: effect
    Accessible.checked: checked
    Accessible.onPressAction: picked()
    Keys.onSpacePressed: picked()

    // Pod treścią: klik w dowolne miejsce karty wybiera; przyciski i tekst komentarza nad nią mają własną obsługę.
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
            OptionBadge {
                objectName: "decisionRecommended"
                visible: option.recommended
                label: "polecane"
                tint: Tide.Tokens.teal
            }
            OptionBadge {
                objectName: "decisionCaretakerBadge"
                visible: option.caretakerBadge !== ""
                label: option.caretakerBadge
                tint: Tide.Tokens.lavender
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
        // Dopisek opiekuna: „✓ Polecane — opiekun karty też to poleca · uzasadnienie opiekuna” albo
        // „◆ Opiekun poleca tę opcję · dlaczego?”.
        RowLayout {
            objectName: "decisionAdviceNote"
            Layout.fillWidth: true
            Layout.leftMargin: 23
            visible: option.note !== ""
            spacing: 6
            readonly property string text: option.note
            Text {
                objectName: "decisionAdviceText"
                Layout.fillWidth: true
                Layout.maximumWidth: Math.ceil(implicitWidth) + 1
                text: option.note + " ·"
                textFormat: Text.PlainText
                color: option.noteColor
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                wrapMode: Text.Wrap
            }
            Tide.Button {
                objectName: "decisionAdviceToggle"
                text: (option.noteOpen ? "▾ " : "▸ ") + option.link
                small: true
                tone: option.noteColor
                Accessible.name: option.noteOpen ? "Zwiń komentarz opiekuna" : "Pokaż komentarz opiekuna"
                onClicked: option.noteToggled()
            }
            Item { Layout.fillWidth: true }
        }
        ColumnLayout {
            id: detailHolder
            Layout.fillWidth: true
            Layout.leftMargin: 23
            visible: option.note !== "" && option.noteOpen
        }
    }
}
