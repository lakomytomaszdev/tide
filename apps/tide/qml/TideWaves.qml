// Logo „trzy fale” bez kafelka do nagłówka paska widoków (26 px): teal, lawenda, brzoskwinia.
import QtQuick
import QtQuick.Shapes
import "tide" as Tide

Item {
    id: logo

    implicitWidth: 26
    implicitHeight: 26

    Shape {
        width: 26
        height: 26
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: logo.width / 26; yScale: logo.height / 26 }

        ShapePath {
            strokeColor: Tide.Tokens.teal
            strokeWidth: 2.4
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M3 8c3 -3 6 3 10 0s7 -3 10 0" }
        }
        ShapePath {
            strokeColor: Tide.Tokens.lavender
            strokeWidth: 2.4
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M3 13c3 -3 6 3 10 0s7 -3 10 0" }
        }
        ShapePath {
            strokeColor: Tide.Tokens.peach
            strokeWidth: 2.4
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M3 18c3 -3 6 3 10 0s7 -3 10 0" }
        }
    }
}
