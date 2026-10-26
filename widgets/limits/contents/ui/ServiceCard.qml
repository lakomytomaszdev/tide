// Karta jednej usługi w popupie: limit sesji i tygodniowy, resety, godzina odczytu,
// przycisk otwarcia strony w Firefoksie, a przy wylogowaniu przycisk logowania.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

Tide.Card {
    id: card

    required property var source           // LimitsSource
    required property string serviceKey    // chatgpt | claude
    required property string title
    property string sessionLabel: "Sesja"
    // ChatGPT często nie ma okna sesji — wtedy pokazujemy sam limit tygodniowy.
    property bool hideEmptySession: false
    // Gęsty układ dla paska bocznego: reset obok miernika, mniejsze odstępy.
    property bool dense: false

    readonly property var info: source.service(serviceKey)
    readonly property string serviceState: source.available ? (info.state || "checking") : "offline"
    readonly property var sessionValue: source.remaining(serviceKey, "session")
    readonly property string readTime: source.lastRead(serviceKey) || ""

    highlighted: serviceState === "login"
    accent: Tide.Tokens.neutral
    spacing: dense ? 6 : 8
    padding: dense ? 10 : 12

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Text {
            text: card.title
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: card.readTime !== "" ? "odczyt " + card.readTime : "brak odczytu"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight
        }
        QQC2.ToolButton {
            icon.name: "internet-web-browser-symbolic"
            display: QQC2.AbstractButton.IconOnly
            text: "Otwórz " + card.title + " w Firefoksie"
            enabled: card.source.available
            onClicked: card.source.open(card.serviceKey)
            QQC2.ToolTip.text: text
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 600
        }
    }

    MetricRow {
        Layout.fillWidth: true
        visible: !(card.hideEmptySession && card.sessionValue === null)
        label: card.sessionLabel
        value: card.sessionValue
        reset: card.source.reset(card.serviceKey, "session")
        dense: card.dense
    }
    MetricRow {
        Layout.fillWidth: true
        label: "Limit tygodniowy"
        value: card.source.remaining(card.serviceKey, "weekly")
        reset: card.source.reset(card.serviceKey, "weekly")
        dense: card.dense
    }

    Text {
        Layout.fillWidth: true
        visible: text !== ""
        wrapMode: Text.WordWrap
        text: card.serviceState === "unavailable" ? "Nie udało się odczytać danych"
            : card.serviceState === "login" ? "Nie zalogowano — zaloguj się, żeby odczytać limity"
            : card.serviceState === "checking" && card.source.available ? "Sprawdzanie…"
            : ""
        color: card.serviceState === "unavailable" ? Tide.Tokens.negative
            : card.serviceState === "login" ? Tide.Tokens.neutral
            : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }

    // Logowanie odbywa się w zwykłym Firefoksie (POST /open), usługa sama pobierze ciasteczka.
    Rectangle {
        visible: card.serviceState === "login"
        Layout.alignment: Qt.AlignRight
        implicitHeight: loginLabel.implicitHeight + 12
        implicitWidth: loginLabel.implicitWidth + 28
        radius: Tide.Tokens.radiusSmall
        color: loginArea.containsMouse ? Qt.lighter(Tide.Tokens.accent, 1.08) : Tide.Tokens.accent

        Text {
            id: loginLabel
            anchors.centerIn: parent
            text: "Zaloguj się w Firefoksie"
            color: Tide.Tokens.backgroundWindow
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: loginArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: card.source.open(card.serviceKey)
        }
    }
}
