// Kafelek Modele w docku (po prawej, zaraz za Limitami AI): neutralna karta w text.secondary jak Docker —
// kolor tylko w kropce stanu domyślnego serwera. Etykieta „Modele” (z nazwą punktu, gdy domyślny nie jest
// tym komputerem), pod nią „● qwen3:8b · na karcie 100%”, „● gotowe”, „● pracuje: opis kart · 2 w kolejce”
// (teal, pulsuje), „● serwer nie odpowiada” (róż). Model częściowo na procesorze — masło. Bez usługi
// wyciszone słowa. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import "tide/models.js" as Logic
import "tide" as Tide

Tide.DockTile {
    id: tile

    property var snapshot: null
    property bool serviceUp: true

    readonly property var info: Logic.tile(snapshot, serviceUp)
    readonly property color dotColor: info.tone === "ok" ? Tide.Tokens.positive
        : info.tone === "working" ? Tide.Tokens.working
        : info.tone === "partial" || info.tone === "warning" ? Tide.Tokens.neutral
        : info.tone === "offline" ? Tide.Tokens.negative
        : Tide.Tokens.textMuted
    // Wartość słowami (dostępność i testy).
    readonly property string summary: info.value
    // Najdłuższa wartość — długie nazwy modeli (Bielik z repozytorium) nie rozpychają docka.
    readonly property real maxValueWidth: 210

    label: info.label
    tint: Tide.Tokens.textSecondary
    tintStrength: 0.2
    Accessible.name: label + ": " + summary

    Row {
        spacing: 5

        Tide.StateDot {
            objectName: "tileDot"
            visible: tile.info.tone !== "muted"
            anchors.verticalCenter: parent.verticalCenter
            size: 7
            tint: tile.dotColor
            sessionState: tile.info.pulse ? "working" : ""
        }
        Text {
            text: tile.summary
            width: Math.min(implicitWidth, tile.maxValueWidth)
            elide: Text.ElideMiddle
            textFormat: Text.PlainText
            color: tile.info.tone === "muted" ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}
