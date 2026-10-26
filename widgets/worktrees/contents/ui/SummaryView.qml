// Mały popup kafelka worktrees — tylko gdy okna „Tide Worktrees” nie udało się otworzyć.
// Podaje podsumowanie i mówi, co zrobić. Sam nie rozmawia z usługą (da się go załadować w teście).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "tide" as Tide

Item {
    id: view

    property bool available: true
    property string problem: ""          // "not-installed", "no-service" albo ""
    property int cleanCount: 0
    property real cleanBytes: 0
    property int activeCount: 0
    property int totalCount: 0
    signal retryRequested()

    Layout.preferredWidth: Kirigami.Units.gridUnit * 20
    Layout.preferredHeight: body.implicitHeight + Kirigami.Units.largeSpacing * 2
    Layout.minimumWidth: Kirigami.Units.gridUnit * 16

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.smallSpacing * 2

        Text {
            text: "Worktrees"
            color: Kirigami.Theme.textColor
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 15
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            visible: view.available
            text: "Do sprzątnięcia bez ryzyka: " + view.cleanCount + " (" + Tide.Theme.formatBytes(view.cleanBytes) + ")"
                  + "\nW pracy: " + view.activeCount + " · razem: " + view.totalCount
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: !view.available || view.problem === "no-service"
                  ? "Usługa tide-daemon nie odpowiada. Uruchom: systemctl --user start tide-daemon"
                  : "Okno Worktrees nie jest zainstalowane. Zainstaluj je: make install-worktrees-window"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        QQC2.Button {
            Layout.alignment: Qt.AlignRight
            text: "Spróbuj ponownie"
            onClicked: view.retryRequested()
        }
    }
}
