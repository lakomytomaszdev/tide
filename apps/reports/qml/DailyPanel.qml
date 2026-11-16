// Prawy panel: „Na daily” wybranego projektu — Na czym polega, Skończone dziś (notki po tickecie),
// Zrobione, W toku, Czeka na mnie (nieaktualne przekreślone), Uwaga (testy, konflikty numerów — tylko gdy są),
// Dalej i zwinięte Porządki; na dole „Kopiuj jako tekst” (tekst z raportu, ten sam co przycisk na stronie)
// i „Pełna strona ↗” (strona projektu w przeglądarce). Teksty z raportu zawsze jako zwykły tekst.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "reports.js" as Logic

Rectangle {
    id: panel

    property var project: null
    property bool legacy: false
    property bool canOpen: true
    property bool copied: false
    property bool showChores: false
    readonly property var sections: Logic.sections(project)
    readonly property var chores: Logic.chores(project)
    readonly property var finished: Logic.finished(project)
    readonly property string copyText: project && project.copyText ? String(project.copyText) : ""
    signal copyRequested(string text)
    signal pageRequested(string slug)

    onProjectChanged: {
        copied = false
        showChores = false
        flick.contentY = 0
    }

    radius: Tide.Tokens.radiusCard
    color: Tide.Tokens.backgroundCard
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

    Flickable {
        id: flick
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        anchors.margins: 18
        anchors.bottomMargin: 10
        clip: true
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: Tide.ScrollBar {}

        ColumnLayout {
            id: content
            width: flick.width
            spacing: 14

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "NA DAILY"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    font.letterSpacing: 0.7
                }
                Text {
                    objectName: "panelTitle"
                    Layout.fillWidth: true
                    text: Logic.projectLabel(panel.project)
                          + (Logic.projectFolder(panel.project) ? "  ·  " + Logic.projectFolder(panel.project) : "")
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
            }

            Text {
                objectName: "panelLegacy"
                Layout.fillWidth: true
                visible: panel.legacy
                text: "Ten raport powstał przed widokiem w oknie Tide i nie ma danych „Na daily” poza stroną HTML. "
                      + "Otwórz pełną stronę w przeglądarce."
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                lineHeight: 1.25
                wrapMode: Text.Wrap
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: !panel.legacy && !!panel.project && !!panel.project.theme
                spacing: 4
                Text {
                    text: "Na czym polega"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "panelTheme"
                    Layout.fillWidth: true
                    text: panel.project && panel.project.theme ? String(panel.project.theme) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    lineHeight: 1.25
                    wrapMode: Text.Wrap
                }
            }

            // „Skończone dziś” — notki po tickecie (co zrobiono, co to daje, czy sprawdzić ręcznie).
            ColumnLayout {
                objectName: "section-finished"
                Layout.fillWidth: true
                visible: !panel.legacy && panel.finished.length > 0
                spacing: 6
                RowLayout {
                    spacing: 7
                    Dot { tint: "mint"; size: 8 }
                    Text {
                        objectName: "finishedTitle"
                        text: "Skończone dziś"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                }
                Repeater {
                    model: panel.legacy ? [] : panel.finished
                    delegate: DailyItem {
                        objectName: "finishedItem"
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        item: modelData
                    }
                }
            }

            Repeater {
                model: panel.legacy ? [] : panel.sections
                delegate: ColumnLayout {
                    id: section
                    required property var modelData
                    objectName: "section-" + modelData.key
                    Layout.fillWidth: true
                    visible: !modelData.optional || modelData.items.length > 0
                    spacing: 6

                    RowLayout {
                        spacing: 7
                        Dot { tint: section.modelData.tint; hollow: section.modelData.hollow; size: 8 }
                        Text {
                            objectName: "sectionTitle"
                            text: section.modelData.title
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                        }
                    }
                    Repeater {
                        model: section.modelData.items
                        delegate: DailyItem {
                            Layout.fillWidth: true
                            Layout.leftMargin: 15
                            item: modelData
                        }
                    }
                    Text {
                        Layout.leftMargin: 15
                        visible: section.modelData.items.length === 0
                        text: "nic"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                }
            }

            // Porządki (tracker, plany i instrukcje agentów) — zwinięte jak na stronie projektu.
            ColumnLayout {
                Layout.fillWidth: true
                visible: !panel.legacy && panel.chores.length > 0
                spacing: 6
                Text {
                    objectName: "choresToggle"
                    text: (panel.showChores ? "▾ " : "▸ ") + "Porządki · " + panel.chores.length
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    activeFocusOnTab: parent.visible
                    Keys.onReturnPressed: panel.showChores = !panel.showChores
                    Keys.onSpacePressed: panel.showChores = !panel.showChores
                    Accessible.role: Accessible.Button
                    Accessible.name: text
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: panel.showChores = !panel.showChores
                    }
                }
                Repeater {
                    model: panel.showChores ? panel.chores : []
                    delegate: DailyItem {
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        item: modelData
                        muted: true
                    }
                }
            }
        }
    }

    ColumnLayout {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 18
        spacing: 8

        PillButton {
            objectName: "copyButton"
            Layout.fillWidth: true
            visible: !panel.legacy
            enabled: panel.copyText !== ""
            text: panel.copied ? "Skopiowano ✓" : "Kopiuj jako tekst"
            strong: true
            fill: Tide.Tokens.accent
            textColor: Tide.Tokens.backgroundDesktop
            onClicked: panel.copyRequested(panel.copyText)
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Text {
                objectName: "fullPageLink"
                visible: !!panel.project && !!panel.project.slug
                text: "Pełna strona ↗"
                textFormat: Text.PlainText
                color: !panel.canOpen ? Tide.Tokens.textMuted
                     : linkArea.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                font.underline: activeFocus
                activeFocusOnTab: visible
                Keys.onReturnPressed: panel.pageRequested(panel.project.slug)
                Keys.onSpacePressed: panel.pageRequested(panel.project.slug)
                Accessible.role: Accessible.Link
                Accessible.name: "Pełna strona projektu w przeglądarce"
                MouseArea {
                    id: linkArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.pageRequested(panel.project.slug)
                }
            }
        }
    }

    Timer {
        running: panel.copied
        interval: 2500
        onTriggered: panel.copied = false
    }
}
