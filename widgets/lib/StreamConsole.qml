// Konsola streamu na pulpicie lewego monitora w aktywności Stream (PT-37): karta Sceny (podgląd i przyciski scen),
// pod nią pełna karta Stream (StreamCard.qml, full): tryb i kto go ustawił, antena, OBS i nagrywanie,
// profil gracza z listą, przełączniki modułów, „Wycisz mikrofon”, BRB i Panel streamera.
// Same dane i sygnały — bez PlasmoidItem (podgląd: widgets/preview_stream.py).
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: deck

    property var view: null
    property bool coreUp: false
    property string obsMemory: ""
    property string previewImage: ""
    property bool previewSupported: true
    property string status: ""
    property bool statusError: false
    property bool busy: false

    signal commandRequested(string name, var args)
    signal panelRequested()

    spacing: 14

    ScenesCard {
        Layout.fillWidth: true
        view: deck.view
        coreUp: deck.coreUp
        previewImage: deck.previewImage
        previewSupported: deck.previewSupported
        busy: deck.busy
        onSceneRequested: name => deck.commandRequested("scene.set", { scene: name })
    }

    StreamCard {
        Layout.fillWidth: true
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
    }
}
