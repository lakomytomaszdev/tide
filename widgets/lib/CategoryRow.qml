// Rząd siedmiu kategorii ustawień nad kolumną „Teraz” (PT-105, decyzje 1, 2 i 4 K-29): Przygotowanie, Sceny
// i ekrany, Wynik i seria, Na ekranie, Alerty, Animacje żubra, Urządzenia — w stałej kolejności, każda jednym
// kliknięciem. Ikona z podpowiedzią, otwarta kategoria dostaje podpis. Kropka ostrzeżenia, gdy w środku coś
// wymaga uwagi (Stream.categoryWarnings). Rząd tylko zgłasza wybór: popup kategorii otwiera NowColumn.
// API dla PT-106: `current` (id otwartej kategorii albo ""), sygnał categoryRequested(id) z id z Stream.CATEGORIES.
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

RowLayout {
    id: row

    property var view: null
    property bool coreUp: false
    property string current: ""
    signal categoryRequested(string id)

    readonly property var model: Stream.categories(view, coreUp)

    objectName: "categoryRow"
    spacing: 5

    Repeater {
        model: row.model
        IconButton {
            required property var modelData
            objectName: "category-" + modelData.id
            framed: true
            iconName: modelData.icon
            iconSize: 18
            on: row.current === modelData.id
            label: on ? modelData.short : ""
            dot: modelData.warning !== ""
            tip: modelData.label + (modelData.warning !== "" ? " — " + modelData.warning : "")
            Accessible.name: modelData.label + (on ? ", otwarta" : "") + (modelData.warning !== "" ? ", " + modelData.warning : "")
            onClicked: row.categoryRequested(modelData.id)
        }
    }
    Item { Layout.fillWidth: true }
}
