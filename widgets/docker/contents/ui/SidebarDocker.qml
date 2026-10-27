// Kafelek Docker w pasku bocznym: „● 9 działa · ● 1 padł · 6 projektów” — tym samym językiem co dock
// (teal, róż tylko przy padniętych), zawsze z liczbą działających (zero wyciszone). Pod spodem kontenery,
// które padły (najwyżej dwa, róż), albo jedna wyciszona linia o problemie (usługa, Docker). W nagłówku
// link „Otwórz w Tide”. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "../code/docker.js" as Logic
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    property var snapshot: null
    property bool serviceUp: true
    property real nowTs: Date.now() / 1000
    signal openRequested(string item)

    readonly property string dockerStatus: Logic.status(snapshot, serviceUp)
    readonly property bool ok: dockerStatus === "ok"
    readonly property var counts: Logic.summary(snapshot)
    readonly property var failed: ok ? Logic.failedContainers(snapshot) : []
    readonly property string problemText: dockerStatus === "down" ? "Usługa tide-daemon nie odpowiada"
        : dockerStatus === "starting" ? "Łączę się z Dockerem…"
        : (snapshot && snapshot.error ? snapshot.error : "Docker nie działa")
    readonly property string summary: Logic.tileValue(snapshot, serviceUp)
        + (ok && counts.projects > 0 ? " · " + Logic.count(counts.projects, "projekt", "projekty", "projektów") : "")

    title: "Docker"
    spacing: 8
    Accessible.name: "Docker: " + summary

    headerItems: Tide.TextButton {
        text: "Otwórz w Tide"
        Accessible.name: "Otwórz widok Docker w oknie Tide"
        onClicked: tile.openRequested("")
    }

    component Count: Row {
        id: count
        property int value: 0
        property string word: ""
        property color dot: Tide.Tokens.working
        spacing: 6
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 7
            height: 7
            radius: 3.5
            color: count.value > 0 ? count.dot : Tide.Theme.alpha(count.dot, 0.35)
        }
        Text {
            id: number
            text: count.value
            textFormat: Text.PlainText
            color: count.value > 0 ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        Text {
            anchors.baseline: number.baseline
            text: count.word
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Row {
        visible: tile.ok
        spacing: 8
        Count {
            value: tile.counts.running
            word: Logic.runsWord(tile.counts.running)
        }
        Text {
            visible: tile.counts.failed > 0
            anchors.verticalCenter: parent.verticalCenter
            text: "·"
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        Count {
            visible: tile.counts.failed > 0
            value: tile.counts.failed
            word: Logic.failedWord(tile.counts.failed)
            dot: Tide.Tokens.negative
        }
        Text {
            visible: tile.counts.projects > 0
            anchors.verticalCenter: parent.verticalCenter
            text: "· " + Logic.count(tile.counts.projects, "projekt", "projekty", "projektów")
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Repeater {
        model: tile.failed.slice(0, 2)
        Text {
            required property var modelData
            Layout.fillWidth: true
            text: modelData.name + " — " + Logic.failedLine(modelData, tile.nowTs)
            textFormat: Text.PlainText
            color: Tide.Tokens.negative
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: tile.openRequested(parent.modelData.id)
            }
        }
    }
    Text {
        Layout.fillWidth: true
        visible: tile.failed.length > 2
        text: "i " + (tile.failed.length - 2) + " więcej"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }

    Text {
        Layout.fillWidth: true
        visible: !tile.ok
        text: tile.problemText
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        wrapMode: Text.WordWrap
        maximumLineCount: 2
        elide: Text.ElideRight
    }
}
