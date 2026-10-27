// Znak widoku Modele: kreska 2 px w siatce 24×24, zaokrąglone końce (jak RailIcon okna Tide,
// docs/design-system.md „Ikony”). „chip” to ten sam znak co w kafelku „Modele” (PT-18) i w pasku okna.
import QtQuick
import QtQuick.Shapes

Item {
    id: glyph

    property string name: ""
    property color color: "transparent"
    property real stroke: 2

    readonly property var paths: ({
        "chip": "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2z M10 2v4 M14 2v4"
                + " M10 18v4 M14 18v4 M2 10h4 M2 14h4 M18 10h4 M18 14h4 M10 10h4v4h-4z",
        "plus": "M12 5v14 M5 12h14",
        "check": "M5 12.5l4.5 4.5 9.5 -10",
        "close": "M7 7l10 10 M17 7l-10 10",
        "chevron": "M9 6l6 6 -6 6",
        "down": "M6 9l6 6 6 -6",
        "refresh": "M20 11a8 8 0 1 0 -2.3 5.7 M20 5v6h-6",
        "download": "M12 4v11 M7 10l5 5 5 -5 M5 20h14",
        "eye": "M2 12s3.5 -6 10 -6 10 6 10 6 -3.5 6 -10 6 -10 -6 -10 -6z M9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0z",
        "eyeOff": "M2 12s3.5 -6 10 -6 10 6 10 6 -3.5 6 -10 6 -10 -6 -10 -6z M4 4l16 16"
    })

    implicitWidth: 15
    implicitHeight: 15

    Shape {
        width: 24
        height: 24
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: glyph.width / 24; yScale: glyph.height / 24 }

        ShapePath {
            strokeColor: glyph.color
            strokeWidth: glyph.stroke
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: glyph.paths[glyph.name] || "" }
        }
    }
}
