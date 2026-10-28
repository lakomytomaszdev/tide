// Sekcja „Modele lokalne” w popupie kafelka AI (PT-41): to, co z popupu dawnego kafelka Modele naprawdę się
// liczy — stan domyślnego serwera z kropką, powód niedostępności, model w pamięci i ile z niego jest na karcie
// (z terminem zwolnienia), kolejka i pobieranie, problemy ról i pliku ustawień — oraz „Otwórz Modele ↗”
// (widok Modele w oknie Tide, gdzie są wszystkie serwery, role i tryb raportu). Słowa z biblioteki
// (tide/models.js), te same co w kafelku Modele i w podpowiedzi. Bez usługi, przed pierwszym odczytem
// i bez serwera w ustawieniach sekcji nie ma, jak kropki w kafelku. Sam nie rozmawia z usługą ani z oknem:
// prośba idzie sygnałem (main.qml). Teksty z usługi to dane: zwykły tekst. Bez PlasmoidItem, więc da się go
// załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide/models.js" as Logic
import "tide" as Tide

Tide.Card {
    id: card

    property var snapshot: null
    property bool serviceUp: false
    property real nowMs: Date.now()
    property string windowNote: ""

    signal openRequested()                   // okno Tide na widoku Modele

    readonly property var info: Logic.indicator(snapshot, serviceUp)
    readonly property string endpointName: info.visible ? Logic.defaultName(snapshot) : ""
    // Domyślny serwer jest pierwszy na liście endpoints().
    readonly property var endpoint: info.visible ? Logic.endpoints(snapshot)[0] : null
    readonly property var queue: endpoint ? Logic.queueFor(snapshot, endpointName) : null
    readonly property var pulls: endpoint ? Logic.pullsFor(snapshot, endpointName) : []
    readonly property var memory: endpoint && endpoint.online ? Logic.loaded(endpoint) : []
    // Role, które nie działają — bez powtarzania „serwer nie odpowiada” przy niedostępnym domyślnym serwerze
    // (to mówi już linia błędu serwera).
    readonly property var roleProblems: info.visible ? Logic.roles(snapshot).filter(r =>
        r.problem !== "" && !(r.endpoint === endpointName && endpoint && !endpoint.online)) : []
    readonly property string configText: info.visible ? Logic.configLine(snapshot) : ""

    visible: info.visible
    highlighted: info.warning
    accent: info.tone === "offline" ? Tide.Tokens.negative : Tide.Tokens.neutral
    spacing: 4
    Accessible.role: Accessible.Grouping
    Accessible.name: "Modele lokalne: " + info.text

    component Line: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        wrapMode: Text.WordWrap
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.bottomMargin: 2
        spacing: 8

        Text {
            text: "Modele lokalne"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 1
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        LocalDot {
            objectName: "localDot"
            Layout.alignment: Qt.AlignVCenter
            tone: card.info.tone
        }
        Text {
            objectName: "localEndpoint"
            Layout.fillWidth: true
            // Stan serwera mówi kropka, a gdy nie działa — linia błędu pod nagłówkiem.
            text: card.endpoint ? Logic.endpointTitle(card.endpoint) : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight
        }
        Tide.TextButton {
            objectName: "openModels"
            text: "Otwórz Modele ↗"
            Accessible.name: "Otwórz widok Modele w oknie Tide"
            onClicked: card.openRequested()
        }
    }

    Line {
        objectName: "localError"
        visible: card.endpoint !== null && !card.endpoint.online
        text: card.endpoint ? (card.endpoint.error || "Serwer modeli nie odpowiada") : ""
        color: Tide.Tokens.negative
    }

    // Modele w pamięci: nazwa i udział karty, pod spodem rozmiar, kontekst i termin zwolnienia.
    Repeater {
        model: card.memory
        ColumnLayout {
            id: memRow
            required property var modelData
            Layout.fillWidth: true
            spacing: 1
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    objectName: "localModel"
                    Layout.fillWidth: true
                    text: Logic.shortModel(memRow.modelData.name)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                    elide: Text.ElideMiddle
                }
                Text {
                    objectName: "localGpu"
                    text: Logic.gpuText(memRow.modelData)
                    textFormat: Text.PlainText
                    color: Logic.onCard(memRow.modelData) || Logic.isCloudModel(memRow.modelData)
                        ? Tide.Tokens.positive : Tide.Tokens.neutral
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
            }
            Line {
                objectName: "localDetail"
                visible: text !== ""
                text: Logic.loadedDetail(memRow.modelData, card.nowMs)
                font.pointSize: Tide.Tokens.fontSizePt - 1.5
            }
        }
    }

    Line {
        objectName: "localIdle"
        visible: text !== "" && !Logic.queueBusy(card.queue)
        text: card.endpoint ? Logic.idleLine(card.endpoint) : ""
    }
    Line {
        objectName: "localQueue"
        visible: text !== ""
        text: Logic.queueLine(card.queue, card.nowMs)
        color: Tide.Tokens.working
    }
    Repeater {
        model: card.info.visible ? Logic.queueOther(card.snapshot, card.endpointName, card.nowMs) : []
        Line {
            required property string modelData
            objectName: "localQueueOther"
            text: modelData
            color: Tide.Tokens.working
        }
    }
    Repeater {
        model: card.pulls
        Line {
            required property var modelData
            objectName: "localPull"
            text: Logic.pullLine(modelData)
            color: modelData.state === "error" ? Tide.Tokens.negative
                : modelData.state === "done" ? Tide.Tokens.positive : Tide.Tokens.textSecondary
        }
    }
    Repeater {
        model: card.roleProblems
        Line {
            required property var modelData
            objectName: "localRoleProblem"
            text: modelData.name + " (" + modelData.model + "): " + modelData.problem
            color: Tide.Tokens.negative
        }
    }
    Line {
        objectName: "localConfig"
        visible: text !== ""
        text: card.configText
        color: Tide.Tokens.neutral
    }
    Line {
        objectName: "windowNote"
        visible: card.windowNote !== ""
        text: card.windowNote
        color: Tide.Tokens.neutral
    }
}
