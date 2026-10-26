// Mały popup kafelka worktrees — tylko gdy okna „Tide Worktrees” nie udało się otworzyć.
// Podaje podsumowanie i mówi, co zrobić. Sam nie rozmawia z usługą (da się go załadować w teście).
// W pasku bocznym (sidebar) to stałe, zwarte podsumowanie: tytuł z przyciskiem otwierającym
// okno i jedna linia liczb; opis problemu pojawia się dopiero, gdy okna nie udało się otworzyć.
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
    property bool sidebar: false
    readonly property bool showProblem: !sidebar || !available || problem !== ""
    signal retryRequested()

    Layout.preferredWidth: Kirigami.Units.gridUnit * 20
    Layout.preferredHeight: body.implicitHeight + margins * 2
    Layout.minimumWidth: Kirigami.Units.gridUnit * 16
    readonly property real margins: sidebar ? Kirigami.Units.smallSpacing : Kirigami.Units.largeSpacing

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: view.margins
        spacing: Kirigami.Units.smallSpacing * (view.sidebar ? 1 : 2)

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Worktrees"
                color: Kirigami.Theme.textColor
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            QQC2.Button {
                visible: view.sidebar && !view.showProblem
                text: "Otwórz okno Worktrees"
                onClicked: view.retryRequested()
            }
        }
        Text {
            Layout.fillWidth: true
            wrapMode: view.sidebar ? Text.NoWrap : Text.WordWrap
            elide: Text.ElideRight
            visible: view.available
            text: view.sidebar
                  ? view.cleanCount + " do sprzątnięcia (" + Tide.Theme.formatBytes(view.cleanBytes) + ") · "
                    + view.activeCount + " w pracy · razem " + view.totalCount
                  : "Do sprzątnięcia bez ryzyka: " + view.cleanCount + " (" + Tide.Theme.formatBytes(view.cleanBytes) + ")"
                    + "\nW pracy: " + view.activeCount + " · razem: " + view.totalCount
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            visible: view.showProblem
            text: !view.available || view.problem === "no-service"
                  ? "Usługa tide-daemon nie odpowiada. Uruchom: systemctl --user start tide-daemon"
                  : "Okno Worktrees nie jest zainstalowane. Zainstaluj je: make install-worktrees-window"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        QQC2.Button {
            Layout.alignment: Qt.AlignRight
            visible: view.showProblem
            text: "Spróbuj ponownie"
            onClicked: view.retryRequested()
        }
    }
}
