// Grupa w widoku Docker: nagłówek (kropka, projekt, znacznik repozytorium „◆ CookApp”, stan „3 z 3
// działa”, wynik ostatniej akcji, akcje projektu — compose logs, Restart, Uruchom, Zatrzymaj) i wiersze
// kontenerów. „Zatrzymane” można zwinąć — padnięte widać zawsze. Sam nie rozmawia z usługą: prośby
// idą sygnałami do strony.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "docker.js" as Logic

ColumnLayout {
    id: section

    property var group: ({})
    property real nowTs: Date.now() / 1000
    property var stats: null
    property bool collapsed: false
    property string logsId: ""
    property string flashId: ""              // kontener albo ta grupa (po showItem)
    property var busy: ({})                  // id kontenera → akcja
    property string groupBusy: ""            // akcja na projekcie w toku
    property var notes: ({})                 // id kontenera → {text, error}
    property var groupNote: null

    signal containerAction(string id, string action)   // logs|shell|restart|stop|start|remove
    signal groupActionRequested(string action)          // logs|restart|start|stop
    signal linkRequested(string url)
    signal toggleCollapsed()

    readonly property bool stoppedGroup: group.kind === "stopped"
    readonly property var actions: Logic.groupActions(group)
    readonly property var shownContainers: {
        const all = group.containers || []
        return stoppedGroup && collapsed ? all.filter(c => c.failed) : all
    }
    readonly property int hiddenCount: (group.containers || []).length - shownContainers.length

    function rowFor(id) {
        for (let i = 0; i < rows.count; i++) {
            const item = rows.itemAt(i)
            if (item && item.container.id === id) return item
        }
        return null
    }

    spacing: 6

    RowLayout {
        id: header
        Layout.fillWidth: true
        Layout.leftMargin: 4
        Layout.rightMargin: 4
        spacing: 10

        StateDot {
            Layout.alignment: Qt.AlignVCenter
            kind: section.stoppedGroup ? "off" : "run"
        }
        Text {
            text: section.group.title || ""
            textFormat: Text.PlainText
            color: section.flashId === section.group.id ? Tide.Tokens.accent : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Row {
            visible: !!(section.group.repo && section.group.repo.name)
            spacing: 4
            Text {
                text: "◆"
                textFormat: Text.PlainText
                color: Tide.Tokens.accent
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
            }
            Text {
                text: section.group.repo ? section.group.repo.name : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
            }
        }
        Text {
            objectName: "groupStatus"
            Layout.fillWidth: true
            text: Logic.groupStatus(section.group)
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            elide: Text.ElideRight
        }
        Text {
            objectName: "groupNote"
            Layout.maximumWidth: 420
            visible: section.groupBusy !== "" || section.groupNote !== null
            text: section.groupBusy !== "" ? (Logic.GROUP_BUSY[section.groupBusy] || "…")
                                          : section.groupNote ? section.groupNote.text : ""
            textFormat: Text.PlainText
            color: section.groupBusy !== "" ? Tide.Tokens.accent
                 : section.groupNote && section.groupNote.error ? Tide.Tokens.negative : Tide.Tokens.positive
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            elide: Text.ElideRight
        }
        Row {
            spacing: 12
            visible: section.actions.length > 0
            Repeater {
                model: section.actions
                LinkText {
                    required property var modelData
                    text: modelData.label
                    // Logi da się otworzyć zawsze, reszta czeka na koniec akcji na projekcie.
                    enabled: modelData.id === "logs" || section.groupBusy === ""
                    onClicked: section.groupActionRequested(modelData.id)
                }
            }
        }
        LinkText {
            visible: section.stoppedGroup && (section.group.containers || []).length > (section.group.failed || 0)
            text: section.collapsed ? "pokaż wszystkie (" + (section.group.containers || []).length + ")" : "zwiń"
            onClicked: section.toggleCollapsed()
        }
    }

    Repeater {
        id: rows
        model: section.shownContainers
        ContainerRow {
            required property var modelData
            required property int index
            Layout.fillWidth: true
            container: modelData
            nowTs: section.nowTs
            stats: Logic.statsOf(section.stats, modelData)
            statsKnown: !!(section.stats && section.stats.ok)
            fullName: section.stoppedGroup
            logsOpen: section.logsId === modelData.id
            flash: section.flashId === modelData.id
            busyAction: section.busy[modelData.id] || ""
            locked: section.groupBusy !== ""
            note: section.notes[modelData.id] || null
            onLogsRequested: section.containerAction(modelData.id, "logs")
            onShellRequested: section.containerAction(modelData.id, "shell")
            onRestartRequested: section.containerAction(modelData.id, "restart")
            onStopRequested: section.containerAction(modelData.id, "stop")
            onStartRequested: section.containerAction(modelData.id, "start")
            onRemoveRequested: section.containerAction(modelData.id, "remove")
            onLinkRequested: url => section.linkRequested(url)
        }
    }

    Text {
        Layout.leftMargin: 26
        visible: section.stoppedGroup && section.collapsed && section.hiddenCount > 0
        text: Logic.count(section.hiddenCount, "zatrzymany ukryty", "zatrzymane ukryte", "zatrzymanych ukrytych")
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
}
