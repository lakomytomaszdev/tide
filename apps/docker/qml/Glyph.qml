// Znak przycisku widoku Docker: kreska 2 px w siatce 24×24, zaokrąglone końce (jak RailIcon okna Tide,
// docs/design-system.md „Ikony”). Kształty z mocka „Tide Docker”; „play” jest wypełniony.
import QtQuick
import QtQuick.Shapes

Item {
    id: glyph

    property string name: ""
    property color color: "transparent"
    property real stroke: 2

    readonly property var paths: ({
        "logs": "M5 6h14 M5 10h14 M5 14h10 M5 18h7",
        "shell": "M5 8l4 4 -4 4 M12 17h7",
        "restart": "M20 11a8 8 0 1 0 -2.3 5.7 M20 5v6h-6",
        "stop": "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2z",
        "play": "M8 5l11 7 -11 7z",
        "trash": "M5 7h14 M10 7v-2h4v2 M7 7l1 12h8l1 -12",
        "search": "M4 11a7 7 0 1 0 14 0a7 7 0 1 0 -14 0z M20 20l-3.5 -3.5",
        "docker": "M3 8l9 -5 9 5v8l-9 5 -9 -5z M3 8l9 5 9 -5 M12 13v8",
        "close": "M7 7l10 10 M17 7l-10 10",
        "chevron": "M9 6l6 6 -6 6"
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
            fillColor: glyph.name === "play" ? glyph.color : "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: glyph.paths[glyph.name] || "" }
        }
    }
}
