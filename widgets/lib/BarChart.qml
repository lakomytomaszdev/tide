// Wykres słupkowy historii (np. procesor z ostatniej minuty): values od 0 do 1, null = brak danych.
// Wykres ma stałą liczbę miejsc (slots), najnowsza wartość stoi z prawej i jest wyróżniona,
// starsze są przygaszone. Dopóki historia jest krótsza, lewa część zostaje pusta.
import QtQuick

Item {
    id: chart

    property var values: []
    property int slots: 30
    property color color: Tokens.accent
    property real spacing: 2
    property real olderOpacity: 0.45
    property real minimumBar: 2           // zero też jest widoczne jako cienka kreska

    readonly property int offset: Math.max(0, slots - (values ? values.length : 0))
    // Przy wielu miejscach na wąskim wykresie odstęp maleje, żeby słupki się zmieściły.
    readonly property real gap: slots > 1 ? Math.min(spacing, width / slots * 0.35) : 0
    readonly property real barWidth: slots > 0 ? Math.max(0.5, (width - gap * (slots - 1)) / slots) : 0

    implicitWidth: 240
    implicitHeight: 44

    Repeater {
        model: chart.slots

        delegate: Rectangle {
            required property int index
            readonly property int sample: index - chart.offset
            readonly property var value: sample >= 0 && sample < chart.values.length ? chart.values[sample] : null
            readonly property real clamped: value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value))

            visible: value !== null && value !== undefined
            x: index * (chart.barWidth + chart.gap)
            width: chart.barWidth
            height: Math.max(chart.minimumBar, chart.height * clamped)
            y: chart.height - height
            radius: Math.min(2, width / 2)
            color: chart.color
            opacity: sample === chart.values.length - 1 ? 1 : chart.olderOpacity
        }
    }
}
