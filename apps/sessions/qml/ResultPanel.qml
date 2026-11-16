// „Pokaż wynik”: raport podagenta (to, co oddał koordynatorowi) i wycinek jego pracy — ostatnie
// wypowiedzi i wywołania narzędzi z jego dziennika. Podagenci działają w procesie koordynatora,
// więc nie ma do czego „przejść” — wynik czytamy w oknie. Tekst da się zaznaczyć i skopiować.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: panel

    property var detail: null      // RunDetail() albo null (ładuje się)
    property real nowTs: Date.now() / 1000
    signal closeRequested()

    readonly property var rounds: detail ? (detail.rounds || []) : []
    readonly property var excerpt: detail ? (detail.excerpt || []) : []

    color: Tide.Tokens.backgroundWindow
    focus: visible
    Keys.onEscapePressed: panel.closeRequested()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Text {
                    Layout.fillWidth: true
                    text: panel.detail ? ("Wynik · " + (panel.detail.label || "podagent")) : "Wczytuję wynik…"
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Accessible.role: Accessible.Heading
                }
                Text {
                    Layout.fillWidth: true
                    visible: panel.detail !== null
                    text: panel.detail ? ("„" + (panel.detail.description || "") + "”"
                                          + (panel.detail.model ? " · " + panel.detail.model : "")
                                          + " · " + Logic.count(panel.rounds.length, "runda", "rundy", "rund")) : ""
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
            }
            Tide.Button {
                text: "Zamknij"
                toolTip: "Wróć do tablicy (Esc)"
                onClicked: panel.closeRequested()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            // Raport podagenta.
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 3
                radius: 14
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)

                Flickable {
                    id: reportFlick
                    anchors.fill: parent
                    anchors.margins: 16
                    clip: true
                    contentWidth: width
                    contentHeight: report.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                    TextEdit {
                        id: report
                        width: reportFlick.width - 10
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.MarkdownText
                        text: panel.detail ? (panel.detail.result || "Podagent nie oddał jeszcze raportu.") : ""
                        color: Tide.Tokens.textSecondary
                        selectionColor: Tide.Tokens.accent
                        selectedTextColor: Tide.Tokens.backgroundDesktop
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                    }
                }
            }

            // Wycinek pracy podagenta.
            ColumnLayout {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                spacing: 8
                Text {
                    text: "Ostatnie kroki podagenta"
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Flickable {
                    id: stepsFlick
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: width
                    contentHeight: steps.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                    ColumnLayout {
                        id: steps
                        width: stepsFlick.width - 10
                        spacing: 8
                        Text {
                            visible: panel.excerpt.length === 0
                            Layout.fillWidth: true
                            text: "Brak dziennika podagenta."
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: panel.excerpt
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: Logic.when(modelData.ts, panel.nowTs) + (modelData.kind === "tool" ? " · narzędzie" : "")
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 11
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.text
                                    color: modelData.kind === "tool" ? Tide.Tokens.lavender : Tide.Tokens.textSecondary
                                    font.family: modelData.kind === "tool" ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
                                    font.pixelSize: modelData.kind === "tool" ? 12 : 13
                                    wrapMode: Text.Wrap
                                    maximumLineCount: modelData.kind === "tool" ? 2 : 8
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
