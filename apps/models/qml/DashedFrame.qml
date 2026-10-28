// Przerywana ramka („Dodaj serwer”) — QtQuick.Rectangle nie rysuje przerywanej obwódki, więc to Shape
// z PathRectangle (jak w widoku Tablica).
import QtQuick
import QtQuick.Shapes

Shape {
    id: frame

    property color strokeColor: "transparent"
    property real strokeWidth: 1
    property real radius: 10
    property color fillColor: "transparent"

    preferredRendererType: Shape.CurveRenderer

    ShapePath {
        strokeColor: frame.strokeColor
        strokeWidth: frame.strokeWidth
        fillColor: frame.fillColor
        strokeStyle: ShapePath.DashLine
        dashPattern: [3, 2.5]
        PathRectangle {
            x: frame.strokeWidth / 2
            y: frame.strokeWidth / 2
            width: frame.width - frame.strokeWidth
            height: frame.height - frame.strokeWidth
            radius: frame.radius
        }
    }
}
