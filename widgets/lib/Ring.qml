// Pierścień miernika (np. procesor, pamięć): value od 0 do 1, środek na własną zawartość.
import QtQuick
import QtQuick.Shapes

Item {
    id: ring

    property var value: null
    property color color: Tokens.accent
    property color trackColor: Theme.alpha(Tokens.textPrimary, 0.12)
    property real thickness: Math.max(3, Math.min(width, height) * 0.12)
    readonly property real clamped: value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value))
    readonly property real arcRadius: (Math.min(width, height) - thickness) / 2
    default property alias content: center.data

    implicitWidth: 40
    implicitHeight: 40

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: ring.trackColor
            strokeWidth: ring.thickness
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.arcRadius
                radiusY: ring.arcRadius
                startAngle: -90
                sweepAngle: 360
            }
        }
        ShapePath {
            strokeColor: ring.value === null ? "transparent" : ring.color
            strokeWidth: ring.thickness
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.arcRadius
                radiusY: ring.arcRadius
                startAngle: -90
                sweepAngle: 360 * ring.clamped
                Behavior on sweepAngle { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
            }
        }
    }

    Item {
        id: center
        anchors.centerIn: parent
        width: childrenRect.width
        height: childrenRect.height
    }
}
