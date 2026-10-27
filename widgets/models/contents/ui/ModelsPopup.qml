// Popup kafelka Modele (400 px jak Docker): nagłówek ze znakiem układu w lawendzie, „Odśwież” (Refresh()
// usługi) i „Otwórz w Tide ↗” (widok Modele w oknie Tide); tryb raportu, jeśli usługa go podaje; karta na
// każdy serwer — nazwa („Ten komputer” dla adresu lokalnego), „domyślny”, stan, adres i wersja, powód
// niedostępności, kolejka zadań, pobieranie modeli, modele w pamięci z udziałem karty i terminem zwolnienia,
// liczba pobranych; role z modelem i dostępnością (błędy po polsku); problemy z plikiem ustawień.
// Sam nie rozmawia z usługą: prośby idą sygnałami (main.qml). Teksty z usługi to dane: zwykły tekst.
import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import "../code/models.js" as Logic
import "tide" as Tide

ColumnLayout {
    id: popup

    property var snapshot: null
    property bool serviceUp: true
    property real nowMs: Date.now()
    property bool refreshing: false
    property string windowNote: ""

    signal refreshRequested()
    signal openRequested()                   // okno Tide na widoku Modele

    readonly property string modelsStatus: Logic.status(snapshot, serviceUp)
    readonly property bool ready: modelsStatus === "ok" || modelsStatus === "offline"
    readonly property var endpointList: ready ? Logic.endpoints(snapshot) : []
    readonly property var roleList: ready ? Logic.roles(snapshot) : []
    readonly property string reportText: ready ? Logic.reportLine(snapshot) : ""
    readonly property string configText: ready ? Logic.configLine(snapshot) : ""

    spacing: 12

    component Line: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1.5
        wrapMode: Text.WordWrap
    }

    component Dot: Rectangle {
        property color tint: Tide.Tokens.textMuted
        implicitWidth: 8
        implicitHeight: 8
        radius: 4
        color: tint
    }

    // --- nagłówek ---------------------------------------------------------------------------
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        // Znak: układ scalony (kwadrat z nóżkami) kreską 2 px w siatce 24×24.
        Shape {
            implicitWidth: 18
            implicitHeight: 18
            preferredRendererType: Shape.CurveRenderer
            transform: Scale { xScale: 18 / 24; yScale: 18 / 24 }
            ShapePath {
                strokeColor: Tide.Tokens.lavender
                strokeWidth: 2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2z M10 2v4 M14 2v4 M10 18v4 M14 18v4 M2 10h4 M2 14h4 M18 10h4 M18 14h4 M10 10h4v4h-4z" }
            }
        }
        Text {
            Layout.fillWidth: true
            text: "Modele"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1.5
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Tide.TextButton {
            objectName: "refreshButton"
            visible: popup.serviceUp
            enabled: !popup.refreshing
            text: popup.refreshing ? "Odświeżam…" : "Odśwież"
            textColor: Tide.Tokens.textSecondary
            Accessible.name: "Odśwież stan serwerów modeli"
            onClicked: if (!popup.refreshing) popup.refreshRequested()
        }
        Tide.TextButton {
            objectName: "openButton"
            text: "Otwórz w Tide ↗"
            Accessible.name: "Otwórz widok Modele w oknie Tide"
            onClicked: popup.openRequested()
        }
    }

    Line {
        objectName: "windowNote"
        visible: popup.windowNote !== ""
        text: popup.windowNote
        color: Tide.Tokens.neutral
    }

    // --- brak usługi albo pierwszy odczyt ----------------------------------------------------------
    Text {
        objectName: "problem"
        Layout.fillWidth: true
        visible: !popup.ready
        text: Logic.problem(popup.snapshot, popup.serviceUp)
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        wrapMode: Text.WordWrap
    }
    // Polecenie w osobnej linii, bez zawijania — do skopiowania w całości.
    Text {
        objectName: "problemCommand"
        Layout.topMargin: -8
        visible: !popup.ready && text !== ""
        text: Logic.problemCommand(popup.snapshot, popup.serviceUp)
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1.5
        wrapMode: Text.NoWrap
    }

    // --- tryb raportu (PT-25) -------------------------------------------------------------------
    Rectangle {
        Layout.fillWidth: true
        visible: popup.reportText !== ""
        implicitHeight: reportLabel.implicitHeight + 12
        radius: Tide.Tokens.radiusFrame
        color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.09)
        Text {
            id: reportLabel
            objectName: "report"
            x: 10
            width: parent.width - 20
            anchors.verticalCenter: parent.verticalCenter
            text: popup.reportText
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            wrapMode: Text.WordWrap
        }
    }

    // --- serwery ------------------------------------------------------------------------------
    Repeater {
        // Model jako liczba i odczyt z tablicy: modelData z listy obiektów zamienia zagnieżdżone tablice
        // (models, loaded) na listy Qt, a logika liczy na tablice JS.
        model: popup.endpointList.length

        Rectangle {
            id: card
            required property int index
            readonly property var ep: popup.endpointList[index]
            readonly property var queue: Logic.queueFor(popup.snapshot, ep.name)
            readonly property var pulls: Logic.pullsFor(popup.snapshot, ep.name)
            readonly property var mem: Logic.loaded(ep)
            objectName: "endpoint"
            Layout.fillWidth: true
            implicitHeight: body.implicitHeight + 24
            radius: Tide.Tokens.radiusTile
            color: Tide.Tokens.backgroundWindow
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)

            ColumnLayout {
                id: body
                x: 12
                y: 12
                width: parent.width - 24
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Dot { tint: card.ep.online ? Tide.Tokens.positive : Tide.Tokens.negative }
                    Text {
                        id: title
                        objectName: "endpointTitle"
                        text: Logic.endpointTitle(card.ep)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        font.weight: Font.DemiBold
                    }
                    Text {
                        visible: card.ep.isDefault && popup.endpointList.length > 1
                        Layout.alignment: Qt.AlignBaseline
                        text: "domyślny"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 2
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        objectName: "endpointState"
                        text: Logic.endpointState(card.ep)
                        textFormat: Text.PlainText
                        color: card.ep.online ? Tide.Tokens.positive : Tide.Tokens.negative
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                }
                Text {
                    objectName: "endpointMeta"
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    text: Logic.endpointMeta(card.ep)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 2
                    elide: Text.ElideRight
                }
                Line {
                    objectName: "endpointError"
                    Layout.leftMargin: 16
                    visible: !card.ep.online && text !== ""
                    text: card.ep.error || ""
                    color: Tide.Tokens.negative
                }

                // Kolejka zadań na tym serwerze (PT-16).
                Line {
                    objectName: "queueLine"
                    Layout.leftMargin: 16
                    Layout.topMargin: 2
                    visible: text !== ""
                    text: Logic.queueLine(card.queue, popup.nowMs)
                    color: Tide.Tokens.working
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }

                // Pobieranie modeli (PT-25).
                Repeater {
                    model: card.pulls
                    ColumnLayout {
                        id: pullRow
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.leftMargin: 16
                        Layout.topMargin: 2
                        spacing: 3
                        Line {
                            objectName: "pullLine"
                            text: Logic.pullLine(pullRow.modelData)
                            color: pullRow.modelData.state === "error" ? Tide.Tokens.negative
                                : pullRow.modelData.state === "done" ? Tide.Tokens.positive : Tide.Tokens.textSecondary
                            font.pointSize: Tide.Tokens.fontSizePt - 1
                        }
                        Tide.MeterBar {
                            Layout.fillWidth: true
                            visible: pullRow.modelData.state === "running"
                            implicitHeight: 4
                            value: pullRow.modelData.percent === null ? null : pullRow.modelData.percent / 100
                            color: Tide.Tokens.accent
                        }
                    }
                }

                // Modele w pamięci: nazwa, udział karty z paskiem, rozmiar i termin zwolnienia.
                Repeater {
                    model: card.mem
                    ColumnLayout {
                        id: memRow
                        required property var modelData
                        objectName: "loaded"
                        Layout.fillWidth: true
                        Layout.leftMargin: 16
                        Layout.topMargin: 4
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                objectName: "loadedName"
                                Layout.fillWidth: true
                                text: Logic.shortModel(memRow.modelData.name)
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.monoFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 1
                                elide: Text.ElideMiddle
                            }
                            Text {
                                objectName: "loadedGpu"
                                text: Logic.gpuText(memRow.modelData)
                                textFormat: Text.PlainText
                                color: Logic.onCard(memRow.modelData) ? Tide.Tokens.positive : Tide.Tokens.neutral
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt - 1
                            }
                        }
                        Tide.MeterBar {
                            Layout.fillWidth: true
                            visible: typeof memRow.modelData.gpuPercent === "number"
                            implicitHeight: 4
                            value: typeof memRow.modelData.gpuPercent === "number" ? memRow.modelData.gpuPercent / 100 : null
                            color: Logic.onCard(memRow.modelData) ? Tide.Tokens.positive : Tide.Tokens.neutral
                        }
                        Line {
                            objectName: "loadedDetail"
                            visible: text !== ""
                            text: Logic.loadedDetail(memRow.modelData, popup.nowMs)
                        }
                    }
                }

                Line {
                    objectName: "idleLine"
                    Layout.leftMargin: 16
                    Layout.topMargin: 2
                    visible: text !== "" && !Logic.queueBusy(card.queue)
                    text: Logic.idleLine(card.ep)
                }
                Line {
                    objectName: "installedLine"
                    Layout.leftMargin: 16
                    visible: text !== ""
                    text: Logic.installedLine(card.ep)
                }
            }
        }
    }

    // --- role ---------------------------------------------------------------------------------
    ColumnLayout {
        Layout.fillWidth: true
        visible: popup.roleList.length > 0
        spacing: 2

        Text {
            Layout.leftMargin: 2
            Layout.bottomMargin: 2
            text: "Role"
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            font.weight: Font.DemiBold
        }

        Repeater {
            model: popup.roleList
            ColumnLayout {
                id: roleRow
                required property var modelData
                objectName: "role"
                Layout.fillWidth: true
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 2
                    Layout.topMargin: 2
                    Layout.bottomMargin: 2
                    spacing: 8
                    Dot { tint: roleRow.modelData.available ? Tide.Tokens.positive : Tide.Tokens.negative }
                    Text {
                        objectName: "roleName"
                        Layout.preferredWidth: 96
                        text: roleRow.modelData.name
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 0.5
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "roleModel"
                        Layout.fillWidth: true
                        text: roleRow.modelData.model + (popup.endpointList.length > 1 ? " · " + roleRow.modelData.where : "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1.5
                        elide: Text.ElideMiddle
                    }
                }
                Line {
                    objectName: "roleProblem"
                    Layout.leftMargin: 18
                    visible: text !== ""
                    text: roleRow.modelData.problem
                    color: Tide.Tokens.negative
                }
            }
        }
    }

    Line {
        objectName: "config"
        visible: text !== ""
        text: popup.configText
        color: Tide.Tokens.neutral
    }
}
