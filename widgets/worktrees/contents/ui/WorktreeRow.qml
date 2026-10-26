// Jeden worktree w popupie: kropka stanu, gałąź, opis stanu, czas ostatniej zmiany, rozmiar
// i przyciski otwarcia w terminalu albo w Riderze.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

Tide.Card {
    id: row

    required property var worktree
    property real nowTs: Date.now() / 1000
    property bool canTerminal: true
    property bool canRider: false
    signal openRequested(string path, string app)

    readonly property color statusColor: Tide.Theme.worktreeColor(worktree)

    padding: 9
    spacing: 3
    highlighted: worktree.hasAgent === true
    accent: Tide.Tokens.working
    opacity: worktree.missing ? 0.5 : 1.0

    function statusText(w) {
        const parts = []
        if (w.isMain) parts.push("główny katalog")
        if (w.hasAgent) parts.push("pracuje agent")
        if (w.missing) parts.push("katalogu już nie ma")
        if (w.dirty > 0) parts.push(w.dirty + " " + Tide.Theme.plural(w.dirty, "zmiana", "zmiany", "zmian"))
        if (w.dirty < 0) parts.push("stan nieznany")
        if (!w.isMain) {
            if (!w.mainBranch) parts.push("brak gałęzi głównej")
            else if (w.merged) parts.push("scalony z " + w.mainBranch)
            else if (w.unmerged > 0) parts.push(w.unmerged + " " + Tide.Theme.plural(w.unmerged, "niescalony commit",
                                                    "niescalone commity", "niescalonych commitów"))
        }
        if (w.locked) parts.push("zablokowany")
        return parts.join("  ·  ")
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Rectangle {
            implicitWidth: 9
            implicitHeight: 9
            radius: 4.5
            color: row.statusColor
        }
        Text {
            text: row.worktree.branch || (row.worktree.detached ? "odłączony " + row.worktree.head : row.worktree.name)
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }
        Text {
            text: Tide.Theme.agoText(row.worktree.lastChangeTs, row.nowTs)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        Text {
            text: Tide.Theme.formatBytes(row.worktree.size)
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            horizontalAlignment: Text.AlignRight
            Layout.minimumWidth: 52
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 6

        Text {
            Layout.fillWidth: true
            text: row.statusText(row.worktree)
            color: row.worktree.hasAgent || row.worktree.dirty !== 0 || row.worktree.unmerged > 0
                   ? row.statusColor : Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight

            QQC2.ToolTip.visible: pathArea.containsMouse
            QQC2.ToolTip.text: row.worktree.path
            MouseArea { id: pathArea; anchors.fill: parent; hoverEnabled: true }
        }
        QQC2.ToolButton {
            visible: row.canTerminal && !row.worktree.missing
            icon.name: "utilities-terminal"
            display: QQC2.AbstractButton.IconOnly
            text: "Otwórz w terminalu"
            implicitHeight: 24
            implicitWidth: 24
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.text: text
            onClicked: row.openRequested(row.worktree.path, "terminal")
        }
        QQC2.ToolButton {
            visible: row.canRider && !row.worktree.missing
            text: "Rider"
            font.pointSize: Tide.Tokens.fontSizePt - 1
            implicitHeight: 24
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.text: "Otwórz w Riderze"
            onClicked: row.openRequested(row.worktree.path, "rider")
        }
    }
}
