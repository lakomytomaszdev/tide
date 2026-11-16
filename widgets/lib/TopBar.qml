// Tide.TopBar (K-128, PT-502): stały pasek górny widoku według makiety S-9 (obszar „Rama i pasek”, wariant B).
//
// Od lewej: powrót (`backText` — nazwa celu, np. „Tablica”; pusty = brak powrotu) w akcencie, okruszki (`crumbs`,
// wyciszone, oddzielone „›”), numer (`number`, sky, mono — nigdy nie skracany) i tytuł (`title`, skracany), a po
// prawej najwyżej jedna akcja główna (`actionText`) i menu ⋯ (`menuItems`). Pod spodem opcjonalne zakładki (`tabs`).
// Wysokość 52 px, z zakładkami 52 + 38 px. Pasek stoi poza przewijaną treścią: gospodarz kotwiczy pod nim Flickable.
//
// Umowa:
// - `back()` — klik w powrót (gospodarz wraca; Esc obsługuje gospodarz, bo zna swoje dialogi),
// - `actionTriggered()` — akcja główna; `actionVariant` (domyślnie "normal"), `actionName` — objectName przycisku,
// - `menuItems: [{id, label, danger, enabled, hint, name}]`, `menuOpen`, `menuTriggered(id)` — pozycja menu ⋯;
//   wyłączona pozycja nie reaguje i pokazuje `hint`; menu zamyka klik obok, klik pozycji i Esc,
// - `tabs: [{id, label, enabled, hint}]`, `currentTab`, `tabTriggered(id)` — zakładki; pasek tylko zgłasza wybór,
//   bieżącą zakładkę ustawia gospodarz (zakładka może otwierać inny widok zamiast treści).
// `name` to przedrostek objectName elementów (powrót `<name>Back`, menu `<name>Menu`, pozycje `<name>MenuItem`
// albo `name` pozycji, zakładki `<name>Tab`, numer i tytuł `<name>Number`, `<name>Heading`).
import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as T
import QtQuick.Window

Item {
    id: bar

    property string name: "topBar"
    property string backText: ""
    property var crumbs: []
    property string number: ""
    property string title: ""
    property string actionText: ""
    property string actionVariant: "normal"
    property string actionName: ""
    property bool actionEnabled: true
    property var menuItems: []
    property bool menuOpen: false
    property var tabs: []
    property string currentTab: ""
    readonly property alias backButton: backButton
    readonly property alias menuButton: menuButton
    readonly property int barHeight: 52
    readonly property int tabsHeight: 38

    signal back()
    signal actionTriggered()
    signal menuTriggered(string id)
    signal tabTriggered(string id)

    implicitHeight: barHeight + (tabs.length > 0 ? tabsHeight : 0)
    height: implicitHeight
    Accessible.role: Accessible.ToolBar
    Accessible.name: [backText ? "Wróć: " + backText : ""].concat(crumbs, [number, title]).filter(x => !!x).join(" › ")

    function trigger(item) {
        menuOpen = false
        if (item && item.enabled !== false) menuTriggered(item.id)
    }

    Keys.onEscapePressed: event => {
        if (menuOpen) {
            menuOpen = false
            event.accepted = true
        } else {
            event.accepted = false
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Tokens.backgroundWindow
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
        }
    }

    RowLayout {
        id: row
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 14
        anchors.rightMargin: 20
        height: bar.barHeight
        spacing: 12

        Button {
            id: backButton
            objectName: bar.name + "Back"
            visible: bar.backText !== ""
            implicitHeight: 36
            leftPadding: 10
            rightPadding: 12
            tone: Tokens.accent
            text: "← " + bar.backText
            toolTip: "Wróć (Esc)"
            Accessible.name: "Wróć: " + bar.backText + " (Esc)"
            onClicked: bar.back()
        }

        Repeater {
            model: bar.crumbs
            delegate: RowLayout {
                id: crumb
                required property string modelData
                required property int index
                spacing: 8
                Text {
                    visible: crumb.index > 0
                    text: "›"
                    textFormat: Text.PlainText
                    color: Tokens.textMuted
                    opacity: 0.6
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                }
                Text {
                    objectName: bar.name + "Crumb"
                    text: crumb.modelData
                    textFormat: Text.PlainText
                    color: Tokens.textMuted
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                }
            }
        }
        Text {
            visible: bar.crumbs.length > 0 && (bar.number !== "" || bar.title !== "")
            text: "›"
            textFormat: Text.PlainText
            color: Tokens.textMuted
            opacity: 0.6
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt
        }
        // Numer zostaje zawsze cały; skraca się tylko tytuł.
        Text {
            objectName: bar.name + "Number"
            visible: bar.number !== ""
            text: bar.number
            textFormat: Text.PlainText
            color: Tokens.sky
            font.family: Theme.monoFamily
            font.pointSize: Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        Text {
            objectName: bar.name + "Heading"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: bar.title
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt
            font.weight: Font.DemiBold
            elide: Text.ElideRight
            maximumLineCount: 1
        }

        Button {
            objectName: bar.actionName !== "" ? bar.actionName : bar.name + "Action"
            visible: bar.actionText !== ""
            variant: bar.actionVariant
            enabled: bar.actionEnabled
            text: bar.actionText
            onClicked: bar.actionTriggered()
        }
        Button {
            id: menuButton
            objectName: bar.name + "Menu"
            visible: bar.menuItems.length > 0
            iconOnly: true
            text: "⋯"
            toolTip: "Więcej akcji"
            Accessible.name: "Więcej akcji: " + bar.menuItems.map(m => m.label).join(", ")
            onClicked: bar.menuOpen = !bar.menuOpen
        }
    }

    Row {
        id: tabRow
        objectName: bar.name + "Tabs"
        visible: bar.tabs.length > 0
        anchors.left: parent.left
        anchors.top: row.bottom
        anchors.leftMargin: 20
        height: bar.tabsHeight
        spacing: 2
        Accessible.role: Accessible.PageTabList
        Repeater {
            model: bar.tabs
            delegate: Item {
                id: tabCell
                required property var modelData
                readonly property bool usable: modelData.enabled !== false
                implicitWidth: tab.implicitWidth
                height: tabRow.height
                width: implicitWidth
                // Podpowiedź wyłączonej zakładki: wyłączony przycisk nie dostaje najechania, więc słucha komórka.
                HoverHandler { id: cellHover }
                PlainToolTip {
                    visible: !tabCell.usable && cellHover.hovered && (tabCell.modelData.hint || "") !== ""
                    text: tabCell.modelData.hint || ""
                }
                T.AbstractButton {
                    id: tab
                    objectName: bar.name + "Tab"
                    readonly property string tabId: tabCell.modelData.id
                    readonly property string hint: tabCell.modelData.hint || ""
                    readonly property bool current: bar.currentTab === tabId
                    anchors.fill: parent
                    enabled: tabCell.usable
                    hoverEnabled: true
                    focusPolicy: Qt.StrongFocus
                    opacity: enabled ? 1 : 0.45
                    leftPadding: 12
                    rightPadding: 12
                    implicitWidth: implicitContentWidth + leftPadding + rightPadding
                    text: tabCell.modelData.label
                    Accessible.role: Accessible.PageTab
                    Accessible.name: text
                    Accessible.description: hint
                    Accessible.selected: current
                    Accessible.onPressAction: if (tab.enabled) bar.tabTriggered(tab.tabId)
                    Keys.onReturnPressed: event => { if (tab.enabled) bar.tabTriggered(tab.tabId); event.accepted = true }
                    Keys.onEnterPressed: event => { if (tab.enabled) bar.tabTriggered(tab.tabId); event.accepted = true }
                    onClicked: bar.tabTriggered(tabId)
                    contentItem: Text {
                        text: tab.text
                        textFormat: Text.PlainText
                        color: tab.current || tab.hovered ? Tokens.textPrimary : Tokens.textMuted
                        font.family: Theme.uiFamily
                        font.pointSize: Tokens.fontSizePt
                        font.weight: tab.current ? Font.DemiBold : Font.Normal
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Item {
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 2
                            color: tab.current ? Tokens.accent : "transparent"
                        }
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: 3
                            radius: Tokens.radiusSmall
                            color: "transparent"
                            border.width: 2
                            border.color: Tokens.teal
                            visible: tab.visualFocus
                        }
                    }
                }
            }
        }
    }

    // Klik obok otwartego menu je zamyka. Warstwa leży nad treścią gospodarza (pasek ma u gospodarza wyższe z),
    // a pod listą menu; rozciąga się na całe okno, licząc od położenia paska w chwili otwarcia.
    MouseArea {
        id: catcher
        objectName: bar.name + "MenuCatcher"
        visible: bar.menuOpen
        z: 50
        onVisibleChanged: if (visible) {
            const origin = bar.mapToItem(null, 0, 0)
            const win = bar.Window.window
            x = -origin.x
            y = -origin.y
            width = win ? win.width : bar.width
            height = win ? win.height : bar.height
        }
        onClicked: bar.menuOpen = false
    }

    Rectangle {
        id: menu
        objectName: bar.name + "MenuList"
        visible: bar.menuOpen
        z: 60
        width: 300
        x: Math.max(8, row.x + menuButton.x + menuButton.width - width)
        y: row.y + menuButton.y + menuButton.height + 6
        height: menuColumn.implicitHeight + 16
        radius: Tokens.radiusCard
        color: Tokens.backgroundPanel
        border.width: 1
        border.color: Theme.alpha(Tokens.textPrimary, Tokens.opacityOutline)
        Accessible.role: Accessible.PopupMenu
        Accessible.name: "Więcej akcji"
        Column {
            id: menuColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 8
            spacing: 2
            Repeater {
                model: bar.menuItems
                delegate: Item {
                    id: cell
                    required property var modelData
                    readonly property bool usable: modelData.enabled !== false
                    width: menuColumn.width
                    height: entry.implicitHeight
                    HoverHandler { id: itemHover }
                    PlainToolTip {
                        visible: !cell.usable && itemHover.hovered && (cell.modelData.hint || "") !== ""
                        text: cell.modelData.hint || ""
                    }
                    Button {
                        id: entry
                        objectName: cell.modelData.name || bar.name + "MenuItem"
                        readonly property string itemId: cell.modelData.id
                        readonly property bool danger: cell.modelData.danger === true
                        anchors.fill: parent
                        variant: danger ? "danger" : "ghost"
                        alignment: Qt.AlignLeft
                        enabled: cell.usable
                        text: cell.modelData.label
                        toolTip: cell.modelData.hint || ""
                        Accessible.role: Accessible.MenuItem
                        onClicked: bar.trigger(cell.modelData)
                    }
                }
            }
        }
    }
}
