// Jeden worktree w oknie: [pole wyboru] · narzędzie · tytuł · repozytorium / opis · kiedy · rozmiar · stan · akcje.
// Wygląd zależy od grupy: „active” (teal), „clean” (mięta, z polem wyboru), „decide” (masło),
// „main” (wyciszony). Pole wyboru ma każdy worktree, który da się usunąć (Logic.selectable) —
// nie-sugerowany zaznaczony jest czerwony, bo jego usunięcie wymaga mocniejszego potwierdzenia.
// Sam niczego nie usuwa ani nie otwiera — zgłasza to sygnałami.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "worktrees.js" as Logic

Rectangle {
    id: row

    required property var worktree
    property real nowTs: Date.now() / 1000
    property bool checked: false
    property bool canTerminal: true
    property bool canRider: false
    property string homeDir: ""
    signal toggled()
    signal openRequested(string path, string app)
    signal diffRequested(string path, string kind)   // kind: "worktree" (zmiany w katalogu) albo "range" (względem głównej)
    // „Zaciągnij” (PT-353): fast-forward do upstreamu; stan żądania i wynik trzyma strona.
    property bool pullBusy: false
    property var pullNote: null
    signal pullRequested(string path)
    readonly property var pullBlocker: Logic.pullBlocker(worktree)

    readonly property string kind: worktree.group || ""
    readonly property bool selectable: Logic.selectable(worktree)
    readonly property bool risky: Logic.risky(worktree)
    readonly property string lockReason: Logic.lockReason(worktree)
    readonly property bool old: kind === "decide" && Logic.isOld(worktree, nowTs)
    readonly property string why: Logic.whyText(worktree)
    readonly property color pickTint: risky ? Tide.Tokens.negative : Tide.Tokens.positive

    implicitHeight: Math.max(52, body.implicitHeight + 20)
    radius: Tide.Tokens.radiusCard
    color: selectable && checked ? Tide.Theme.alpha(pickTint, 0.08)
         : kind === "active" ? Tide.Theme.alpha(Tide.Tokens.working, 0.07)
         : hover.containsMouse && selectable ? Qt.lighter(Tide.Tokens.backgroundView, 1.15)
         : Tide.Tokens.backgroundView
    border.width: 1
    border.color: selectable && checked ? Tide.Theme.alpha(pickTint, 0.3)
                : kind === "active" ? Tide.Theme.alpha(Tide.Tokens.working, 0.18)
                : "transparent"
    opacity: old || kind === "main" ? 0.62 : 1.0

    Accessible.role: selectable ? Accessible.CheckBox : Accessible.ListItem
    Accessible.name: worktree.title + ", " + worktree.repo
    Accessible.checkable: selectable
    Accessible.checked: checked
    Accessible.onToggleAction: if (selectable) toggled()
    Accessible.onPressAction: if (selectable) toggled()

    // Kliknięcie w dowolne miejsce wiersza, który da się usunąć, zaznacza go albo odznacza.
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: row.selectable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (row.selectable) row.toggled()
    }

    RowLayout {
        id: body
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        spacing: 12

        Item {
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
            Rectangle {
                anchors.fill: parent
                visible: row.selectable
                radius: 6
                color: !row.checked ? "transparent" : row.risky ? Tide.Tokens.negative : Tide.Tokens.accent
                border.width: row.checked ? 0 : 1.5
                border.color: row.risky ? Tide.Theme.alpha(Tide.Tokens.negative, 0.45)
                                        : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.25)
                Text {
                    anchors.centerIn: parent
                    visible: row.checked
                    text: "✓"
                    color: Tide.Tokens.backgroundDesktop
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }
            }
        }

        ToolChip {
            tool: row.worktree.tool || ""
            Layout.preferredWidth: 66
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            spacing: 3

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Text {
                    visible: text !== ""
                    text: row.worktree.tag && row.worktree.tag !== row.worktree.tool ? row.worktree.tag : ""
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 11
                }
                Text {
                    Layout.fillWidth: implicitWidth > parent.width * 0.7
                    Layout.maximumWidth: implicitWidth
                    text: row.worktree.title || row.worktree.name
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    text: "· " + row.worktree.repo
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: Logic.detail(row.worktree)
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Text {
                objectName: "worktreeSync"
                Layout.fillWidth: true
                visible: text !== ""
                text: Logic.syncLabel(row.worktree)
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
                elide: Text.ElideRight
            }
            Text {
                objectName: "worktreeRepoRemotes"
                Layout.fillWidth: true
                visible: text !== ""
                text: Logic.repoLine(row.worktree, row.nowTs)
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                elide: Text.ElideRight
            }
            // Zmiany w widoku diffa (PT-360): bez terminala, Esc wraca na listę.
            Row {
                objectName: "worktreeChangeActions"
                visible: changes.visible || branchChanges.visible || pull.visible
                spacing: 8
                topPadding: 2
                PillButton {
                    id: pull
                    objectName: "pullButton"
                    visible: Logic.canOfferPull(row.worktree)
                    implicitHeight: 26
                    horizontalPadding: 10
                    pixelSize: 12
                    text: Logic.pullButtonText(row.pullBusy, row.worktree)
                    enabled: !row.pullBusy && row.pullBlocker === null
                    toolTip: row.pullBlocker !== null ? "Nie można zaciągnąć: " + row.pullBlocker.text
                             : "Przesuwa " + (row.worktree.branch || "") + " do " + (row.worktree.upstream || "")
                               + " wyłącznie fast-forward. Tide nie scala historii ani nie robi rebase."
                    onClicked: row.pullRequested(row.worktree.path)
                }
                Text {
                    objectName: "pullReason"
                    visible: pull.visible && !row.pullBusy && row.pullBlocker !== null
                    anchors.verticalCenter: pull.verticalCenter
                    text: row.pullBlocker !== null ? row.pullBlocker.text : ""
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                }
                PillButton {
                    id: changes
                    objectName: "worktreeChanges"
                    visible: Logic.canShowChanges(row.worktree)
                    implicitHeight: 26
                    horizontalPadding: 10
                    pixelSize: 12
                    text: "Zmiany w katalogu"
                    toolTip: "Niezacommitowana praca: w indeksie, poza indeksem i pliki nieśledzone"
                    onClicked: row.diffRequested(row.worktree.path, "worktree")
                }
                PillButton {
                    id: branchChanges
                    objectName: "worktreeBranchChanges"
                    visible: Logic.canShowBranchChanges(row.worktree)
                    implicitHeight: 26
                    horizontalPadding: 10
                    pixelSize: 12
                    text: "Zmiany względem głównej"
                    toolTip: "Zmiany gałęzi " + (row.worktree.branch || "") + " względem " + (row.worktree.mainBranch || "")
                    onClicked: row.diffRequested(row.worktree.path, "range")
                }
            }
            Text {
                objectName: "pullNote"
                Layout.fillWidth: true
                visible: row.pullNote !== null
                text: row.pullNote ? row.pullNote.text : ""
                color: row.pullNote && row.pullNote.ok ? Tide.Tokens.positive : Tide.Tokens.neutral
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }

        Text {
            objectName: "worktreeActivity"
            Layout.preferredWidth: row.worktree.hasCard ? 160 : 110
            text: row.worktree.hasCard ? "zajęty przez kartę"
                  : row.worktree.hasAgent ? "agent pracuje" : Tide.Theme.agoText(row.worktree.lastActivityTs, row.nowTs)
            color: row.kind === "active" ? Tide.Tokens.working : row.old ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            font.weight: row.kind === "active" ? Font.DemiBold : Font.Medium
            elide: Text.ElideRight
        }

        Text {
            Layout.preferredWidth: 64
            horizontalAlignment: Text.AlignRight
            text: row.worktree.missing ? "—" : Tide.Theme.formatBytes(row.worktree.size)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
        }

        Text {
            Layout.preferredWidth: 104
            horizontalAlignment: Text.AlignRight
            text: row.kind === "clean" ? "scalony"
                : row.kind === "main" ? "gałąź główna"
                : row.kind === "active" && row.worktree.removable ? "scalony"
                : row.why
            color: row.kind === "clean" || (row.kind === "active" && row.worktree.removable) ? Tide.Tokens.positive
                 : row.kind === "main" ? Tide.Tokens.textMuted : Tide.Tokens.neutral
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            elide: Text.ElideRight
        }

        Row {
            Layout.preferredWidth: 66
            layoutDirection: Qt.RightToLeft
            spacing: 6
            visible: row.kind !== "clean" && !row.worktree.missing

            PillButton {
                visible: row.canRider
                implicitWidth: 30
                implicitHeight: 30
                horizontalPadding: 0
                text: "RD"
                pixelSize: 11
                strong: true
                toolTip: "Otwórz w Riderze"
                onClicked: row.openRequested(row.worktree.path, "rider")
            }
            PillButton {
                visible: row.canTerminal
                implicitWidth: 30
                implicitHeight: 30
                horizontalPadding: 0
                text: "›_"
                mono: true
                toolTip: "Otwórz w terminalu"
                onClicked: row.openRequested(row.worktree.path, "terminal")
            }
        }
        Item {
            Layout.preferredWidth: 66
            visible: row.kind === "clean" || row.worktree.missing
        }
    }

    QQC2.ToolTip.visible: hover.containsMouse && hover.mouseY < height && row.kind !== "clean"
    QQC2.ToolTip.delay: 900
    QQC2.ToolTip.text: Logic.tildePath(worktree.path, homeDir) + (worktree.branch ? "\n" + worktree.branch : "")
                       + (worktree.sessionTitle && worktree.subject ? "\n" + worktree.subject : "")
                       + (lockReason ? "\nNie do usunięcia: " + lockReason
                          : risky ? "\nMożna usunąć po potwierdzeniu, co przepadnie" : "")
}
