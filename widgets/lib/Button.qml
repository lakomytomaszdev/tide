// Tide.Button (K-128, PT-502): wspólny przycisk okna Tide według makiety S-9 (`.btn`, docs/agents/k-128/makieta).
//
// Prawdziwy przycisk z Qt Quick Templates (`AbstractButton`): rola Button dla czytników ekranu, fokus z klawiatury
// z obwódką teal, spacja (AbstractButton) i Enter/Return (niżej), wyłączony — krycie 0,45 i brak reakcji. Bez stylu
// Plasmy, więc wygląda tak samo w oknie i w podglądzie offscreen. `TextButton` i `IconButton` zostają dla widżetów
// Plasmy.
//
// Warianty (`variant`):
// - "primary" — akcent z tekstem w kolorze tła okna; w jednym widoku najwyżej jeden,
// - "normal"  — tło tekstu ~0,06 z obrysem `opacity.outline`, tekst podstawowy (domyślny),
// - "ghost"   — bez tła i obrysu, tekst drugorzędny; po najechaniu tło jak „normal”,
// - "danger"  — róża: tło 14 %, obrys 35 %, tekst w róży (akcje niszczące).
// `small` — 26 px wysokości (zwykły 32 px), `iconOnly` — przycisk-ikona (sam znak, np. „⋯”, węższe marginesy),
// `kbd` — skrót obok tekstu (np. „↵”), `toolTip` — podpowiedź po najechaniu i opis dla czytnika ekranu,
// `tone` — kolor przycisku tonowanego (np. powrót w Tide.TopBar): tło 12 %, obrys 45 %, tekst w tym kolorze.
// Kolory wyłącznie z Tokens (paleta theme/palette/); `fill`, `ink` i `edge` pokazują wynik dla testów.
import QtQuick
import QtQuick.Templates as T

T.AbstractButton {
    id: button

    property string variant: "normal"
    property bool small: false
    property bool iconOnly: false
    property string kbd: ""
    property string toolTip: ""
    property color tone: "transparent"
    property int alignment: Qt.AlignHCenter
    readonly property bool toned: tone.a > 0
    readonly property bool strong: variant === "primary" || toned
    readonly property bool lit: hovered || down

    // Tło, tekst i obrys wariantu (bez krycia wyłączonego przycisku, które dokłada `opacity`).
    readonly property color fill: toned ? Theme.alpha(tone, lit ? 0.18 : 0.12)
        : variant === "primary" ? (lit ? Qt.lighter(Tokens.accent, 1.07) : Tokens.accent)
        : variant === "danger" ? Theme.alpha(Tokens.rose, lit ? 0.20 : 0.14)
        : variant === "ghost" ? (lit ? Theme.alpha(Tokens.textPrimary, 0.06) : "transparent")
        : Theme.alpha(Tokens.textPrimary, lit ? 0.11 : 0.06)
    readonly property color ink: toned ? tone
        : variant === "primary" ? Tokens.backgroundWindow
        : variant === "danger" ? Tokens.rose
        : variant === "ghost" ? (lit ? Tokens.textPrimary : Tokens.textSecondary)
        : Tokens.textPrimary
    readonly property color edge: toned ? Theme.alpha(tone, 0.45)
        : variant === "danger" ? Theme.alpha(Tokens.rose, 0.35)
        : variant === "normal" ? Theme.alpha(Tokens.textPrimary, Tokens.opacityOutline)
        : "transparent"
    readonly property real pointSize: small ? Tokens.fontSizePt * 0.9 : Tokens.fontSizePt * 0.975
    // Wewnętrzne teksty bez objectName (testy widoków szukają przycisków po nazwie i tekście), dostępne aliasami.
    readonly property alias labelItem: label
    readonly property alias kbdItem: shortcut

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    opacity: enabled ? 1 : 0.45
    leftPadding: iconOnly ? (small ? 7 : 9) : (small ? 9 : 13)
    rightPadding: leftPadding
    topPadding: 0
    bottomPadding: 0
    implicitWidth: Math.max(implicitHeight, Math.ceil(implicitContentWidth + leftPadding + rightPadding))
    implicitHeight: small ? 26 : 32

    // Rola i nazwa wprost (AbstractButton ustawia je dopiero przy aktywnej dostępności); akcja „naciśnij” z czytnika
    // ekranu klika tylko włączony przycisk. Spację obsługuje AbstractButton, Enter i Return — te wiersze.
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.description: toolTip
    Accessible.onPressAction: if (button.enabled) button.clicked()
    Keys.onReturnPressed: event => { if (button.enabled) button.clicked(); event.accepted = true }
    Keys.onEnterPressed: event => { if (button.enabled) button.clicked(); event.accepted = true }

    contentItem: Item {
        implicitWidth: label.implicitWidth + (shortcut.visible ? shortcut.implicitWidth + 7 : 0)
        implicitHeight: label.implicitHeight
        Row {
            id: row
            spacing: 7
            width: Math.min(implicitWidth, parent.width)
            height: parent.height
            x: button.alignment === Qt.AlignLeft ? 0 : Math.max(0, (parent.width - width) / 2)
            Text {
                id: label
                width: Math.min(implicitWidth, row.parent.width - (shortcut.visible ? shortcut.implicitWidth + row.spacing : 0))
                height: parent.height
                text: button.text
                textFormat: Text.PlainText
                color: button.ink
                font.family: Theme.uiFamily
                font.pointSize: button.iconOnly ? button.pointSize * 1.15 : button.pointSize
                font.weight: button.strong ? Font.DemiBold : Font.Medium
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
            Text {
                id: shortcut
                visible: button.kbd !== ""
                height: parent.height
                text: button.kbd
                textFormat: Text.PlainText
                color: button.ink
                opacity: 0.7
                font.family: Theme.monoFamily
                font.pointSize: Tokens.fontSizePt * 0.825
                verticalAlignment: Text.AlignVCenter
            }
        }
    }

    background: Rectangle {
        objectName: "buttonBackground"
        radius: Tokens.radiusSmall
        color: button.fill
        border.width: 1
        border.color: button.edge
        // Fokus z klawiatury: obwódka teal poza obrysem, nie zmienia rozmiaru przycisku.
        Rectangle {
            objectName: "buttonFocus"
            anchors.fill: parent
            anchors.margins: -3
            radius: Tokens.radiusSmall + 3
            color: "transparent"
            border.width: 2
            border.color: Tokens.teal
            visible: button.visualFocus
        }
    }

    HoverHandler {
        cursorShape: button.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    }

    PlainToolTip {
        visible: button.toolTip !== "" && button.hovered
        text: button.toolTip
    }
}
