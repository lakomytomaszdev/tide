// Okno „Tide”: pasek widoków po lewej i widok na wierzchu. Stan i backendy daje obiekt shell
// z tide.py (views.json: widoki, backend(id): backend widoku, showRequested: prośba z D-Bus Show).
// grabPath: zapis zrzutu okna i wyjście (podgląd); initialView, initialItem (--view, --select)
// i preview (--preview) — stan na start.
//
// Widok z modułem ładuje się z views/<id>/qml/<qml> przy pierwszym pokazaniu i zostaje (zachowuje
// zaznaczenie i przewinięcie). Umowa widoku: backend, active, preview, showItem(item), takeFocus(),
// railInfo ({label, value, note} — ramka na dole paska, {link: {text, view, item, activate}} — link
// nad nią; RailInfo.qml). Widok bez modułu to PlaceholderView.
import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import "tide" as Tide

Window {
    id: win

    property var shell: null
    property string grabPath: ""
    property string initialView: ""
    property string initialItem: ""
    property var preview: ({})

    property string current: initialView
    onCurrentChanged: if (shell) shell.current = current
    onShellChanged: if (shell) shell.current = current
    readonly property var views: {
        if (!shell) return []
        try { return JSON.parse(shell.viewsJson) } catch (e) { return [] }
    }
    readonly property int currentIndex: indexOf(current)
    property int loadedCount: 0  // odświeża currentView, gdy widok się załaduje
    readonly property var currentView: {
        win.loadedCount
        const loader = loaders.count > 0 && currentIndex >= 0 ? loaders.itemAt(currentIndex) : null
        return loader ? loader.item : null
    }

    function indexOf(id) {
        for (let i = 0; i < views.length; i++) if (views[i].id === id) return i
        return -1
    }

    // Otwiera widok (pusty id — bieżący) i przekazuje mu element; widok bez danych ładuje się teraz.
    function showView(id, item) {
        if (id && indexOf(id) >= 0) current = id
        const loader = loaders.itemAt(currentIndex)
        if (!loader) return
        if (item) loader.deliver(item)
        loader.visited = true
        if (loader.item) loader.item.takeFocus()
    }

    function step(delta) {
        if (views.length === 0) return
        showView(views[(currentIndex + delta + views.length) % views.length].id, "")
    }

    width: 1800
    height: 1000
    minimumWidth: 1380
    minimumHeight: 620
    visible: true
    title: "Tide"
    color: Tide.Tokens.backgroundWindow

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rail {
            id: rail
            Layout.preferredWidth: 200
            Layout.fillHeight: true
            views: win.views
            current: win.current
            shell: win.shell
            info: win.currentView && win.currentView.railInfo ? win.currentView.railInfo : null
            onViewRequested: (id, item) => win.showView(id, item)
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.currentIndex

            Repeater {
                id: loaders
                model: win.views
                delegate: Loader {
                    id: loader
                    required property var modelData
                    property bool visited: win.current === modelData.id
                    property string pendingItem: modelData.id === win.initialView ? win.initialItem : ""

                    function deliver(item) {
                        if (loader.item) loader.item.showItem(item)
                        else pendingItem = item
                    }

                    function properties() {
                        const backend = win.shell ? win.shell.backend(modelData.id) : null
                        const base = { backend: backend, preview: win.preview, active: win.current === modelData.id }
                        if (modelData.url) return base
                        const placeholder = modelData.placeholder || {}
                        return Object.assign(base, { title: modelData.title, icon: modelData.icon,
                                                     heading: placeholder.title || "", text: placeholder.text || "" })
                    }

                    function load() {
                        if (status !== Loader.Null || !visited) return
                        setSource(modelData.url || "PlaceholderView.qml", properties())
                    }

                    onVisitedChanged: load()
                    Component.onCompleted: load()
                    onLoaded: {
                        win.loadedCount++
                        item.active = Qt.binding(() => win.current === modelData.id)
                        if (pendingItem !== "") {
                            item.showItem(pendingItem)
                            pendingItem = ""
                        }
                        if (win.current === modelData.id) item.takeFocus()
                    }
                }
            }
        }
    }

    Connections {
        target: win.shell
        function onShowRequested(view, item) {
            win.showView(view, item)
            win.show()
            win.raise()
            win.requestActivate()
        }
    }

    // Ctrl+1…6 — widoki w kolejności paska, Ctrl+PgUp/PgDown — poprzedni i następny.
    Instantiator {
        model: win.views
        delegate: Shortcut {
            required property var modelData
            required property int index
            sequence: "Ctrl+" + (index + 1)
            onActivated: win.showView(modelData.id, "")
        }
    }
    Shortcut { sequence: "Ctrl+PgDown"; onActivated: win.step(1) }
    Shortcut { sequence: "Ctrl+PgUp"; onActivated: win.step(-1) }
    Shortcut { sequences: [StandardKey.Close]; onActivated: win.close() }

    Timer {
        interval: 900
        running: win.grabPath !== ""
        onTriggered: win.contentItem.grabToImage(function (result) {
            result.saveToFile(win.grabPath)
            Qt.quit()
        })
    }
}
