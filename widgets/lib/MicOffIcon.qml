// Ikona wyciszonego mikrofonu (mikrofon przekreślony) rysowana w kolorze z palety — kafelek Stream przy
// wyciszonym mikrofonie na żywo. Bez motywu ikon, więc wygląda tak samo w panelu i w podglądzie.
import QtQuick

Canvas {
    id: icon

    property color color: Tokens.textPrimary
    property real size: 14

    implicitWidth: size
    implicitHeight: size
    onColorChanged: requestPaint()

    onPaint: {
        const ctx = getContext("2d")
        const s = width / 24
        ctx.reset()
        ctx.strokeStyle = icon.color
        ctx.lineWidth = 2 * s
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        ctx.beginPath()
        // korpus mikrofonu
        ctx.moveTo(9 * s, 5 * s)
        ctx.arc(12 * s, 5 * s, 3 * s, Math.PI, 0)
        ctx.lineTo(15 * s, 11 * s)
        ctx.arc(12 * s, 11 * s, 3 * s, 0, Math.PI)
        ctx.closePath()
        // pałąk i nóżka
        ctx.moveTo(5 * s, 11 * s)
        ctx.arc(12 * s, 11 * s, 7 * s, Math.PI, 0, true)
        ctx.moveTo(12 * s, 18 * s)
        ctx.lineTo(12 * s, 22 * s)
        // przekreślenie
        ctx.moveTo(3 * s, 3 * s)
        ctx.lineTo(21 * s, 21 * s)
        ctx.stroke()
    }
}
