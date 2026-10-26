// Zwarta lista sesji agentów: jedna linia na sesję — kropka stanu (teal pracuje, brzoskwinia
// czeka, puste błękitne kółko skończył), projekt, krótkie słowo stanu i czas. Czekające wiersze
// mają lekkie brzoskwiniowe tło i mały przycisk („Przejdź”, dla Codex Desktop „Otwórz w Codex”);
// skończone i bezczynne są przygaszone. W popupie (showDetail) pod projektem jest druga,
// wyciszona linia (AgentRows.detailOf). Ten sam komponent rysuje kafelek paska bocznego
// i popup kafelka w docku. Wiersze (rows) przygotowuje AgentRows.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: list

    property var rows: []
    property bool showDetail: false
    signal activateRequested(string sessionId)

    readonly property bool anyButton: rows.some(r => r.button)
    // Kolumna przycisku jest zarezerwowana we wszystkich wierszach, gdy choć jeden czeka — wtedy
    // słowa stanu i czasy stoją w równych kolumnach. Szerokość według najdłuższego napisu.
    readonly property real buttonWidth: rows.some(r => r.button && r.deeplink)
                                        ? Math.max(shortProbe.implicitWidth, longProbe.implicitWidth)
                                        : shortProbe.implicitWidth

    function rowOpacity(state) {
        return state === "done" ? 0.72 : state === "idle" ? 0.55 : state === "ended" ? 0.45 : 1
    }

    spacing: 2

    Repeater {
        model: list.rows

        delegate: Item {
            id: row
            required property var modelData
            readonly property bool waiting: modelData.state === "waiting"
            readonly property bool detailed: list.showDetail && modelData.detail !== ""

            Layout.fillWidth: true
            implicitHeight: Math.max(30, line.implicitHeight + (detailed ? 10 : 8))
            opacity: list.rowOpacity(modelData.state)

            // Czekający wiersz lekko w brzoskwini; najechany — delikatne podświetlenie.
            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: -6
                anchors.rightMargin: -6
                radius: Tide.Tokens.radiusSmall
                color: row.waiting ? Tide.Theme.alpha(Tide.Tokens.waiting, area.containsMouse ? 0.16 : 0.10)
                                   : Tide.Theme.alpha(Tide.Tokens.textPrimary, area.containsMouse ? 0.05 : 0)
            }
            // Kliknięcie w wiersz przenosi do sesji (okno, karta Konsole albo wątek w Codex);
            // przycisk robi to samo i jest dostępny z klawiatury.
            MouseArea {
                id: area
                anchors.fill: parent
                enabled: row.modelData.clickable
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: list.activateRequested(row.modelData.id)
            }

            RowLayout {
                id: line
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                Tide.StateDot {
                    Layout.alignment: Qt.AlignVCenter
                    sessionState: row.modelData.state
                    size: 8
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Text {
                        Layout.fillWidth: true
                        text: row.modelData.project
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: row.detailed
                        text: row.modelData.detail
                        color: row.waiting ? Tide.Tokens.waiting : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 2
                        elide: Text.ElideRight
                    }
                }
                Text {
                    Layout.preferredWidth: Math.ceil(Math.max(implicitWidth, wordWidth.advanceWidth))
                    text: row.modelData.word
                    color: row.modelData.state === "idle" || row.modelData.state === "ended"
                           ? Tide.Tokens.textMuted : Tide.Theme.stateColor(row.modelData.state)
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
                Text {
                    Layout.preferredWidth: Math.ceil(Math.max(implicitWidth, agoWidth.advanceWidth))
                    horizontalAlignment: Text.AlignRight
                    text: row.modelData.ago
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 2
                }
                Item {
                    visible: list.anyButton
                    Layout.preferredWidth: list.buttonWidth
                    Layout.preferredHeight: shortProbe.implicitHeight

                    Tide.TextButton {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        visible: row.modelData.button
                        variant: "filled"
                        accent: Tide.Tokens.waiting
                        text: row.modelData.buttonText
                        Accessible.name: row.modelData.activateText + ": " + row.modelData.project
                        onClicked: list.activateRequested(row.modelData.id)
                    }
                }
            }
        }
    }

    // Kolumny słowa stanu („bezczynna” — najdłuższe) i czasu („59 min”) mają stałą szerokość,
    // żeby wiersze układały się w równe kolumny.
    TextMetrics {
        id: wordWidth
        text: "bezczynna"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }
    TextMetrics {
        id: agoWidth
        text: "59 min"
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
    }
    // Wzorce przycisku do zmierzenia kolumny (niewidoczne, więc bez fokusu i bez miejsca w układzie).
    Tide.TextButton {
        id: shortProbe
        visible: false
        Layout.preferredHeight: 0
        variant: "filled"
        text: "Przejdź"
    }
    Tide.TextButton {
        id: longProbe
        visible: false
        Layout.preferredHeight: 0
        variant: "filled"
        text: "Otwórz w Codex"
    }
}
