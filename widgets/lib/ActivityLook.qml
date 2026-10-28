// Wygląd bieżącej aktywności z tide-daemon (/ActivityLook, PT-36, decyzja 80). Panele Plasmy są wspólne
// dla wszystkich aktywności, więc kafelki pracy (dock i pasek boczny) chowają się same, gdy wygląd
// bieżącej aktywności nie ma paneli pracy (Stream): aplet z Plasmoid.status = HiddenStatus panel
// Plasmy 6.8 chowa, a jego konfiguracja, miejsce i identyfikator zostają nietknięte.
// Bez usługi albo przed pierwszą odpowiedzią — wygląd pracy: praca jest ważniejsza niż stream.
import QtQuick

Item {
    id: look

    readonly property var state: source.available ? source.result : null
    // Kafelek pracy ma zniknąć (Stream).
    readonly property bool workHidden: state !== null && state !== undefined && state.workPanels === false
    readonly property string name: state && state.look ? String(state.look) : "work"

    // Źródło można podmienić w testach.
    property alias source: source

    visible: false

    DaemonSource {
        id: source
        path: "/ActivityLook"
        iface: "org.tide.Daemon.ActivityLook"
        method: "State"
        // Zmiana przychodzi sygnałem Changed; rzadki odczyt tylko na wypadek zgubionego sygnału.
        pollInterval: 60000
    }
}
