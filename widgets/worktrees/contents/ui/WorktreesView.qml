// Popup widżetu worktrees: lista pogrupowana według repozytoriów i sprzątanie scalonych
// z potwierdzeniem. Sam nie rozmawia z usługą — dostaje dane i zgłasza prośby sygnałami
// (dzięki temu da się go załadować w teście bez Plasmy).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "tide" as Tide

Item {
    id: view

    property var worktrees: []
    property bool available: true
    property var launchers: ({ terminal: true, rider: false })
    property real nowTs: Date.now() / 1000
    // Sprzątanie: "" → "confirm" → "running" → "done" (stan trzyma main.qml).
    property string cleanup: ""
    property var cleanupResults: []

    signal cleanupChangeRequested(string state)
    signal removeRequested()
    signal openRequested(string path, string app)

    readonly property var linked: worktrees.filter(w => !w.isMain)
    readonly property var cleanable: worktrees.filter(w => w.removable)
    readonly property real cleanableBytes: cleanable.reduce((sum, w) => sum + Math.max(0, w.size), 0)
    readonly property bool sizesPending: cleanable.some(w => w.size < 0)
    readonly property var groups: {
        const out = []
        for (const w of worktrees) {
            let group = out.length > 0 && out[out.length - 1].repoPath === w.repoPath ? out[out.length - 1] : null
            if (!group) {
                group = { repo: w.repo, repoPath: w.repoPath, items: [] }
                out.push(group)
            }
            group.items.push(w)
        }
        return out
    }
    readonly property string mergedNote: "„Scalony” = scalony z lokalną gałęzią główną (master, main albo develop). "
        + "Bez łączenia z serwerem — gałąź wypchnięta, ale niescalona lokalnie, liczy się jako niescalona."

    Layout.preferredWidth: Kirigami.Units.gridUnit * 28
    Layout.preferredHeight: Kirigami.Units.gridUnit * 32
    Layout.minimumWidth: Kirigami.Units.gridUnit * 20
    Layout.minimumHeight: Kirigami.Units.gridUnit * 14

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing * 2

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing

            Text {
                text: "Worktrees"
                color: Kirigami.Theme.textColor
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Tide.CountPill {
                tint: Tide.Tokens.lavender
                count: view.linked.length
                textColor: Kirigami.Theme.textColor
            }
            Item { Layout.fillWidth: true }
            Text {
                visible: view.cleanable.length > 0
                text: "do sprzątnięcia"
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
            }
            Tide.CountPill {
                tint: Tide.Tokens.positive
                count: view.cleanable.length
                emphasized: view.cleanable.length > 0
                textColor: Kirigami.Theme.textColor
            }
        }

        Text {
            Layout.fillWidth: true
            text: view.mergedNote
            wrapMode: Text.WordWrap
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }

        QQC2.ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: view.worktrees.length > 0
            contentWidth: availableWidth

            ColumnLayout {
                width: scroll.availableWidth
                spacing: Kirigami.Units.smallSpacing

                Repeater {
                    model: view.groups

                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        spacing: Kirigami.Units.smallSpacing

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: modelData.repo
                                color: Kirigami.Theme.textColor
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt + 1
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: modelData.items[0].mainBranch ? "gałąź główna: " + modelData.items[0].mainBranch
                                                                   : "brak master/main/develop"
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.monoFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 1
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                        Repeater {
                            model: modelData.items
                            WorktreeRow {
                                required property var modelData
                                worktree: modelData
                                nowTs: view.nowTs
                                canTerminal: view.launchers.terminal === true
                                canRider: view.launchers.rider === true
                                Layout.fillWidth: true
                                onOpenRequested: (path, app) => view.openRequested(path, app)
                            }
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: view.worktrees.length === 0
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
            text: !view.available ? "Usługa tide-daemon nie działa.\nUruchom: systemctl --user start tide-daemon"
                : "Brak repozytoriów git w katalogu projektów."
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }

        // Sprzątanie: przycisk → potwierdzenie w popupie → wynik.
        QQC2.Button {
            Layout.fillWidth: true
            visible: view.cleanup === ""
            enabled: view.cleanable.length > 0 && view.available
            text: view.cleanable.length > 0
                  ? "Wyczyść scalone (" + view.cleanable.length + ") · zwolni "
                    + (view.sizesPending ? "liczę…" : Tide.Theme.formatBytes(view.cleanableBytes))
                  : "Nic do sprzątnięcia"
            icon.name: "edit-clear-history"
            onClicked: view.cleanupChangeRequested("confirm")
        }

        Tide.Card {
            Layout.fillWidth: true
            visible: view.cleanup === "confirm"
            highlighted: true
            accent: Tide.Tokens.positive

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Usunąć " + view.cleanable.length + " "
                      + Tide.Theme.plural(view.cleanable.length, "worktree", "worktrees", "worktrees")
                      + " i zwolnić ok. " + Tide.Theme.formatBytes(view.cleanableBytes) + "?"
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Tylko scalone z lokalną gałęzią główną, bez niezacommitowanych zmian i bez agenta. "
                      + "Bez wymuszania (git worktree remove), a gałąź znika tylko wtedy, "
                      + "gdy git potwierdzi scalenie (git branch -d)."
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: view.cleanable.map(w => w.repo + " / " + (w.branch || w.name)).join("\n")
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
                maximumLineCount: 8
                elide: Text.ElideRight
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    text: "Anuluj"
                    onClicked: view.cleanupChangeRequested("")
                }
                QQC2.Button {
                    text: "Usuń"
                    icon.name: "edit-delete"
                    onClicked: view.removeRequested()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: view.cleanup === "running"
            QQC2.BusyIndicator { running: view.cleanup === "running"; implicitHeight: 24; implicitWidth: 24 }
            Text {
                text: "Sprzątam…"
                color: Kirigami.Theme.textColor
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
            }
        }

        Tide.Card {
            id: summary
            Layout.fillWidth: true
            visible: view.cleanup === "done"

            readonly property var removed: view.cleanupResults.filter(r => r.removed)
            readonly property var skipped: view.cleanupResults.filter(r => !r.removed)
            readonly property real freed: removed.reduce((sum, r) => sum + (r.freed || 0), 0)

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Usunięto: " + summary.removed.length + " (zwolniono " + Tide.Theme.formatBytes(summary.freed)
                      + ") · pominięto: " + summary.skipped.length
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                wrapMode: Text.WordWrap
                text: view.cleanupResults
                    .filter(r => !r.removed || (r.branch && !r.branchDeleted))
                    .map(r => (r.path ? r.path.split("/").pop() + ": " : "")
                              + (r.removed ? r.branchMessage : r.message))
                    .join("\n")
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
            }
            QQC2.Button {
                Layout.alignment: Qt.AlignRight
                text: "OK"
                onClicked: view.cleanupChangeRequested("")
            }
        }
    }
}
