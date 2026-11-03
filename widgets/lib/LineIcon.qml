// Liniowa ikona konsoli Stream (PT-105): kategorie ustawień, pinezka, ×, plus i kłódka z mocka wariantu A
// (siatka 24×24, obrys 1,8). Rysowana kształtem w kolorze z palety, bez motywu ikon, więc wygląda tak samo
// w Plasmie i w podglądzie offscreen.
import QtQuick
import QtQuick.Shapes

Item {
    id: icon

    property string name: ""
    property color color: Tokens.textSecondary
    property real size: 16

    // Ścieżki SVG z mocka (prostokąty i okręgi zapisane łukami).
    readonly property var paths: ({
        prep: "M4 7h10M18 7h2M4 17h4M12 17h8M14 7a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M8 17a2 2 0 1 0 4 0a2 2 0 1 0 -4 0",
        scenes: "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2zM10 9.5l4.5 2.5-4.5 2.5z",
        score: "M7 4h10v4a5 5 0 0 1-10 0zM7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8",
        layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
        bell: "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 20.5h4",
        spark: "M12 3v5M12 16v5M3 12h5M16 12h5M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
        device: "M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2zM11 18h2",
        pin: "M9 3h6l-1 6 3 3H7l3-3zM12 12v9",
        x: "M6 6l12 12M18 6L6 18",
        plus: "M12 5v14M5 12h14",
        lock: "M7.5 11h9a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1 -1.5 1.5h-9a1.5 1.5 0 0 1 -1.5 -1.5v-6a1.5 1.5 0 0 1 1.5 -1.5zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
        mic: "M9 6a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"
    })

    implicitWidth: size
    implicitHeight: size

    Shape {
        width: 24
        height: 24
        scale: icon.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: icon.color
            strokeWidth: 1.8
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.paths[icon.name] || "" }
        }
    }
}
