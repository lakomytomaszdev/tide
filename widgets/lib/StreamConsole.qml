// Konsola streamu na pulpicie lewego monitora w aktywności Stream (PT-37): karta Sceny (podgląd i przyciski scen)
// i pełna karta Stream (StreamCard.qml, full): tryb i kto go ustawił, antena, OBS i nagrywanie,
// profil gracza z listą, przełączniki modułów, „Wycisz mikrofon”, BRB i Panel streamera.
// PT-91: trzy kolumny — środkowa (czat) należy do StreamDesk, prawa stoi w streamX/streamWidth od lewej krawędzi
// konsoli. Bez streamX karta Stream stoi pod Scenami jak wcześniej (widżet org.tide.stream.scenes).
// PT-105 (wariant A K-29): lewa kolumna to karta Zawsze nad Scenami w grupach, prawa to rząd kategorii i karty
// „Teraz” (NowColumn.qml) z kartą Stream na końcu.
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
    property var chatModel: null
    property string status: ""
    property bool statusError: false
    property bool busy: false
    property real reserveBottom: 0     // miejsce pod kartami prawej kolumny (skrócony plan gry)
    property bool nowActive: true      // false: prawa kolumna zasłonięta (pełny plan gry) — popupy się zamykają

    signal commandRequested(string name, var args)
    signal panelRequested()
    signal categoryOpened(string id)

    property url motionBaseUrl: ""
    function playMotionPreview(kind) { scenes.playMotionPreview(kind) }
    // Odpowiedź rdzenia na komendę (prawa kolumna: wynik kontroli przed startem i zapis formularzy kategorii).
    function commandDone(name, ok, result) { now.commandDone(name, ok, result) }

    readonly property bool columns: deck.streamX >= 0
    implicitHeight: columns ? always.height + 14 + scenes.implicitHeight
                            : scenes.implicitHeight + 14 + stream.implicitHeight
    // Dół prawej kolumny (pod nim StreamDesk stawia skrócony plan gry).
    readonly property real streamBottom: columns ? now.contentBottom : stream.y + stream.height

    AlwaysCard {
        id: always
        objectName: "alwaysCard"
        visible: deck.columns
        width: deck.width
        height: visible ? implicitHeight : 0
        view: deck.view
        coreUp: deck.coreUp
        obsMemory: deck.obsMemory
        busy: deck.busy
        onCommandRequested: (name, args) => deck.commandRequested(name, args)
    }

    ScenesCard {
        id: scenes
        y: deck.columns ? always.height + 14 : 0
        width: deck.width
        // Kolumny: Sceny sięgają dołu kolumny (jak czat i karty „Teraz”), bez pustego pasa nad dockiem.
        height: deck.columns ? Math.max(implicitHeight, deck.height - y) : implicitHeight
        fill: deck.columns
        view: deck.view
        coreUp: deck.coreUp
        previewImage: deck.previewImage
        previewSupported: deck.previewSupported
        motionBaseUrl: deck.motionBaseUrl
        busy: deck.busy
        onSceneRequested: name => deck.commandRequested("scene.set", { scene: name })
        onBrowserWindowRequested: deck.commandRequested("browser.window.choose", {})
    }

    NowColumn {
        id: now
        objectName: "nowColumn"
        visible: deck.columns
        x: deck.streamX
        width: deck.streamWidth
        height: deck.height
        view: deck.view
        coreUp: deck.coreUp
        obsMemory: deck.obsMemory
        chatModel: deck.chatModel
        reserveBottom: deck.reserveBottom
        active: deck.nowActive
        status: deck.status
        statusError: deck.statusError
        busy: deck.busy
        onCommandRequested: (name, args) => deck.commandRequested(name, args)
        onPanelRequested: deck.panelRequested()
        onCategoryOpened: id => deck.categoryOpened(id)
    }

    // Jedna kolumna (stary widżet Sceny): karta Stream pod Scenami, z mikrofonem, BRB i Panelem.
    StreamCard {
        id: stream
        visible: !deck.columns
        y: scenes.height + 14
        width: deck.width
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
