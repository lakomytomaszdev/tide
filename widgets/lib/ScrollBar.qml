// Pasek przewijania Tide (K-128, makieta S-9, wariant A): widoczny od razu, gdy jest co przewijać,
// ukryty, gdy nie ma. Tor w kolorze tekstu ~4 %, uchwyt ~30 %, po najechaniu i przy przeciąganiu akcent ~75 %;
// kolory i szerokość z palety (Tokens.scrollbar*). Pionowy i poziomy: używamy jak wbudowany pasek,
//   Flickable { QQC2.ScrollBar.vertical: Tide.ScrollBar {} }   albo   ListView { ScrollBar.horizontal: Tide.ScrollBar {} }
// Pasek leży na krawędzi przewijanego widoku (nakłada się na treść); miejsce na niego, gdy treść ma go
// nie zasłaniać, odejmuj Tide.Tokens.scrollbarWidth od szerokości treści.
import QtQuick
import QtQuick.Templates as T

T.ScrollBar {
    id: bar

    readonly property bool scrollable: size < 1.0
    readonly property bool lit: hovered || pressed

    // Rozmiar uchwytu (size) liczy Flickable; AlwaysOn trzyma pasek w stanie "active", a widoczność
    // zależy tylko od tego, czy jest co przewijać — bez zależności od wysokości treści w polityce.
    policy: T.ScrollBar.AlwaysOn
    visible: scrollable
    hoverEnabled: true
    minimumSize: 0.08
    padding: 0

    implicitWidth: horizontal ? 0 : Tokens.scrollbarWidth
    implicitHeight: horizontal ? Tokens.scrollbarWidth : 0

    background: Rectangle {
        radius: Math.min(width, height) / 2
        color: Tokens.scrollbarTrack
    }

    contentItem: Rectangle {
        // Przezroczystość koloru uchwytu pochodzi z tokenów; krótka animacja koloru jak w innych elementach Tide.
        implicitWidth: bar.horizontal ? 24 : Tokens.scrollbarWidth
        implicitHeight: bar.horizontal ? Tokens.scrollbarWidth : 24
        radius: Math.min(width, height) / 2
        color: bar.lit ? Tokens.scrollbarThumbActive : Tokens.scrollbarThumb
        Behavior on color { ColorAnimation { duration: 120 } }
    }
}
