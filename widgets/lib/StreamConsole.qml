// Konsola streamu na pulpicie lewego monitora w aktywności Stream (PT-37): karta Sceny (podgląd i przyciski scen)
// i pełna karta Stream (StreamCard.qml, full): tryb i kto go ustawił, antena, OBS i nagrywanie,
// profil gracza z listą, przełączniki modułów, „Wycisz mikrofon”, BRB i Panel streamera.
// PT-91: Sceny w lewej kolumnie, karta Stream w prawej (streamX/streamWidth od lewej krawędzi konsoli);
// środkowa kolumna (czat) należy do StreamDesk. Bez streamX karta Stream stoi pod Scenami jak wcześniej.
// Same dane i sygnały — bez PlasmoidItem (podgląd: widgets/preview_stream.py).
import QtQuick
import QtQuick.Layouts

Item {
    id: deck

    // < 0: karta Stream pod Scenami (jedna kolumna), inaczej osobna kolumna w tym miejscu.
    property real streamX: -1
    property real streamWidth: width

    property var view: null
    property bool coreUp: false
    property var obsMemory: ""
    property string previewImage: ""
    property bool previewSupported: true
    property string status: ""
    property bool statusError: false
    property bool busy: false

    signal commandRequested(string name, var args)
    signal panelRequested()

    property url motionBaseUrl: ""
    function playMotionPreview(kind) { scenes.playMotionPreview(kind) }

    implicitHeight: deck.streamX < 0 ? scenes.implicitHeight + 14 + stream.implicitHeight : scenes.implicitHeight
    // Kolumny (streamX ≥ 0): pod Scenami karta „Zawsze”, w prawej kolumnie wynik serii, gdy sytuacja go chce.
    readonly property bool columns: deck.streamX >= 0
    readonly property bool scoreWanted: columns && !!(view && view.situation && view.situation.cards
                                                     && view.situation.cards.indexOf("score") >= 0)
    readonly property real streamBottom: stream.y + stream.height

    ScenesCard {
        id: scenes
        width: deck.width
        view: deck.view
        coreUp: deck.coreUp
        previewImage: deck.previewImage
        previewSupported: deck.previewSupported
        motionBaseUrl: deck.motionBaseUrl
        busy: deck.busy
        onSceneRequested: name => deck.commandRequested("scene.set", { scene: name })
        onBrowserWindowRequested: deck.commandRequested("browser.window.choose", {})
    }

    AlwaysCard {
        objectName: "alwaysCard"
        visible: deck.columns
        y: scenes.height + 14
        width: deck.width
        view: deck.view
        coreUp: deck.coreUp
        obsMemory: deck.obsMemory
        busy: deck.busy
        onCommandRequested: (name, args) => deck.commandRequested(name, args)
        onPanelRequested: deck.panelRequested()
    }

    ScoreCard {
        id: score
        objectName: "scoreCard"
        visible: deck.scoreWanted
        x: deck.streamX
        width: deck.streamWidth
        view: deck.view
        busy: deck.busy
        onCommandRequested: (name, args) => deck.commandRequested(name, args)
    }

    StreamCard {
        id: stream
        x: deck.streamX < 0 ? 0 : deck.streamX
        y: deck.streamX < 0 ? scenes.height + 14 : (deck.scoreWanted ? score.height + 14 : 0)
        width: deck.streamX < 0 ? deck.width : deck.streamWidth
        actions: !deck.columns
        full: true
        view: deck.view
        coreUp: deck.coreUp
        obsMemory: deck.obsMemory
        status: deck.status
        statusError: deck.statusError
        busy: deck.busy
        onMicRequested: muted => deck.commandRequested("mic.set", { muted: muted })
        onBrbRequested: deck.commandRequested("brb.toggle", {})
        onModuleRequested: (key, visible) => deck.commandRequested("module.set", { module: key, visible: visible })
        onProfileRequested: id => deck.commandRequested("profile.set", { profile: id })
        onPanelRequested: deck.panelRequested()
        onCommandRequested: (name, args) => deck.commandRequested(name, args)
    }
}
