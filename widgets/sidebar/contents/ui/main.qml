// Nagłówek chowanego paska bocznego Tide (lewy monitor): nazwa i skrót, który wysuwa pasek.
//
// Skrót (domyślnie Meta+S) to globalny skrót tego widżetu („Aktywuj widżet”), nadawany przy
// instalacji przez scripts/sidebar.py i zmieniany w jego ustawieniach (Skróty klawiszowe).
// Plasma po aktywacji apletu w panelu z automatycznym chowaniem sama niczego nie wysuwa,
// więc robimy to statusem: panel nie chowa się, dopóki któryś jego aplet ma status co najmniej
// NeedsAttention (PanelView::restoreAutoHide w plasma-workspace 6.8). Po aktywacji status
// rośnie do RequiresAttention na revealMs, potem wraca do Active i panel chowa się sam — chyba
// że jest nad nim mysz. Drugie naciśnięcie skrótu chowa pasek od razu.
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    // Czy pasek jest teraz wysunięty skrótem.
    property bool revealed: false
    // Jak długo pasek wysunięty skrótem zostaje na ekranie bez myszy nad nim.
    property int revealMs: 8000
    // Tekst skrótu z Plasmy — zmiana w ustawieniach widżetu jest widoczna od razu.
    readonly property string shortcutText: String(Plasmoid.globalShortcut || "")

    function toggle() {
        root.revealed = !root.revealed
        if (root.revealed) {
            hideTimer.restart()
        } else {
            hideTimer.stop()
        }
    }

    Plasmoid.title: "Pasek boczny Tide"
    Plasmoid.icon: "view-left-new"
    Plasmoid.status: revealed ? PlasmaCore.Types.RequiresAttentionStatus : PlasmaCore.Types.ActiveStatus
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    // Aktywacja (skrót) nie ma niczego rozwijać — widżet nie ma popupu.
    activationTogglesExpanded: false
    preferredRepresentation: fullRepresentation
    toolTipMainText: "Pasek boczny Tide"
    toolTipSubText: shortcutText !== "" ? "Wysuwa się przy lewej krawędzi albo skrótem " + shortcutText
                                        : "Wysuwa się przy lewej krawędzi"

    Connections {
        target: Plasmoid
        function onActivated() {
            root.toggle()
        }
    }

    Timer {
        id: hideTimer
        interval: root.revealMs
        onTriggered: root.revealed = false
    }

    fullRepresentation: RowLayout {
        Layout.minimumHeight: Math.max(title.implicitHeight, badge.implicitHeight) + Kirigami.Units.smallSpacing * 2
        Layout.preferredHeight: Layout.minimumHeight
        Layout.maximumHeight: Layout.minimumHeight
        Layout.minimumWidth: Kirigami.Units.gridUnit * 6
        spacing: Kirigami.Units.smallSpacing

        Text {
            id: title
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.smallSpacing
            text: "Tide"
            color: Kirigami.Theme.textColor
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 15
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        // Plakietka ze skrótem, jak na mocku: Geist Mono na tle karty.
        Rectangle {
            id: badge
            Layout.rightMargin: Kirigami.Units.smallSpacing
            visible: root.shortcutText !== ""
            implicitWidth: shortcut.implicitWidth + 14
            implicitHeight: shortcut.implicitHeight + 6
            radius: 6
            color: Tide.Tokens.backgroundCard

            Text {
                id: shortcut
                anchors.centerIn: parent
                text: root.shortcutText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
            }
        }
    }
}
