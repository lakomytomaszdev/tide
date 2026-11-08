// Znak widoku w pasku okna Tide: kreska 2 px w siatce 24×24, zaokrąglone końce, bez wypełnień
// (jak glify aplikacji, docs/design-system.md „Ikony”). Kształty z mocka „Tide Tablica”; „models” to układ
// scalony jak w kafelku „Modele” (PT-18).
import QtQuick
import QtQuick.Shapes

Item {
    id: icon

    property string name: ""
    property color color: "transparent"

    // Prostokąty i koła z mocka zapisane jako ścieżki (PathSvg nie zna <rect> ani <circle>).
    readonly property var paths: ({
        "board": "M4.5 4h2a1.5 1.5 0 0 1 1.5 1.5v13a1.5 1.5 0 0 1 -1.5 1.5h-2a1.5 1.5 0 0 1 -1.5 -1.5v-13"
                 + "a1.5 1.5 0 0 1 1.5 -1.5z M11.5 4h2a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1 -1.5 1.5h-2"
                 + "a1.5 1.5 0 0 1 -1.5 -1.5v-8a1.5 1.5 0 0 1 1.5 -1.5z M18.5 4h1a1.5 1.5 0 0 1 1.5 1.5v4"
                 + "a1.5 1.5 0 0 1 -1.5 1.5h-1a1.5 1.5 0 0 1 -1.5 -1.5v-4a1.5 1.5 0 0 1 1.5 -1.5z",
        "sessions": "M3.5 6a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0z M15.5 6a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0z"
                    + " M9.5 18a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0z M7.5 8l3.5 7.5 M16.5 8L13 15.5",
        "worktrees": "M4 5a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M4 19a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z"
                     + " M16 8a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M6 7v10 M18 10c0 4 -6 3 -11 7",
        "docker": "M3 8l9 -5 9 5v8l-9 5 -9 -5z M3 8l9 5 9 -5 M12 13v8",
        "reports": "M7 3h7l4 4v14H7z M14 3v4h4 M10 12h5 M10 16h5",
        "issues": "M5 3h10l4 4v14H5z M15 3v4h4 M8 11h8 M8 15h5 M8 18h3",
        "models": "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2z M10 2v4 M14 2v4"
                  + " M10 18v4 M14 18v4 M2 10h4 M2 14h4 M18 10h4 M18 14h4 M10 10h4v4h-4z"
    })

    implicitWidth: 18
    implicitHeight: 18

    Shape {
        width: 24
        height: 24
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: icon.width / 24; yScale: icon.height / 24 }

        ShapePath {
            strokeColor: icon.color
            strokeWidth: 2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.paths[icon.name] || "" }
        }
    }
}
