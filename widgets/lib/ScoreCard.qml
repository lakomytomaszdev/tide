// Karta „Wynik” w kolumnie „Teraz” (PT-91, PT-105, decyzja 6 K-29): karta na panelu jest niezależna od nakładki
// u widzów. Rdzeń pokazuje ją sam w Observerze na żywo i wtedy, gdy wynik widzą widzowie; poza tym się ją
// przypina — także przy schowanej nakładce (znacznik „tylko ty”). Seria: +1 dla każdej strony, cofnij i zamiana
// stron. Bez serii pola „Nowa seria” (gracze i BO), więc nie trzeba wchodzić do kategorii. Zerowanie zostaje
// w Panelu streamera (przytrzymanie). Nakładkę u widzów przełącza karta Zawsze.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "stream.js" as Stream

NowCard {
    id: card

    property var view: null
    property bool coreUp: false
    signal commandRequested(string name, var args)

    readonly property var model: Stream.scoreCard(view, coreUp)
    readonly property var series: model.series
    property int format: 3

    chip: model.chip

    Text {
        objectName: "scoreState"
        Layout.fillWidth: true
        visible: !!card.series
        elide: Text.ElideRight
        text: card.series ? "BO" + card.series.format + (card.series.finished
                ? " · koniec" + (card.series.winner ? " · wygrał " + card.series.winner : "")
                : " · gra " + card.series.game) : ""
        textFormat: Text.PlainText
        color: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }

    // Gdy karta zabiera wolne miejsce kolumny, rosną cyfry wyniku (VerticalFit), a nie pusty pas.
    GridLayout {
        visible: !!card.series
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.verticalStretchFactor: 100
        columns: 2
        columnSpacing: 8
        rowSpacing: 8
        Repeater {
            model: card.series ? [0, 1] : []
            ColumnLayout {
                required property int modelData
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                spacing: 4
                Text {
                    objectName: modelData === 0 ? "scoreNameLeft" : "scoreNameRight"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: card.width / 2 - 24
                    text: modelData === 0 ? card.series.left : card.series.right
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 1
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: modelData === 0 ? "scoreDigitsLeft" : "scoreDigitsRight"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: Math.ceil(Tokens.fontSizePt * 2.2 * 2)
                    Layout.maximumHeight: Math.ceil(Tokens.fontSizePt * 10 * 2)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: String(card.series.score[modelData])
                    textFormat: Text.PlainText
                    color: card.series.finished && card.series.winner
                           && card.series.winner === (modelData === 0 ? card.series.left : card.series.right)
                           ? Tokens.accent : Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt * 10
                    minimumPointSize: Tokens.fontSizePt * 2.2
                    fontSizeMode: Text.VerticalFit
                    font.weight: Font.Bold
                }
                TextButton {
                    objectName: modelData === 0 ? "scoreLeft" : "scoreRight"
                    Layout.fillWidth: true
                    variant: "outline"
                    topPadding: 9
                    bottomPadding: 9
                    pointSize: Tokens.fontSizePt + 1
                    enabled: !card.busy && !card.series.finished
                    text: "+1"
                    textColor: Tokens.textPrimary
                    opacity: enabled ? 1 : 0.45
                    Accessible.name: "+1 dla " + (modelData === 0 ? card.series.left : card.series.right)
                    onClicked: card.commandRequested("observer.point", { side: modelData === 0 ? "left" : "right" })
                }
            }
        }
    }

    RowLayout {
        visible: !!card.series
        Layout.fillWidth: true
        spacing: 8
        Repeater {
            model: [
                { key: "undo", text: "Cofnij", cmd: "observer.undo" },
                { key: "swap", text: "Zamień strony", cmd: "observer.swap" }
            ]
            TextButton {
                required property var modelData
                objectName: "score-" + modelData.key
                Layout.fillWidth: true
                variant: "outline"
                topPadding: 7
                bottomPadding: 7
                pointSize: Tokens.fontSizePt - 1
                enabled: !card.busy
                text: modelData.text
                textColor: Tokens.textPrimary
                onClicked: card.commandRequested(modelData.cmd, {})
            }
        }
    }

    // Bez serii: nowa seria wprost z karty (observer.series.start).
    Text {
        visible: !card.series
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: "Brak serii. Wpisz graczy i wybierz BO."
        textFormat: Text.PlainText
        color: Tokens.textSecondary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }
    RowLayout {
        visible: !card.series
        Layout.fillWidth: true
        spacing: 8
        SeriesField {
            id: leftField
            objectName: "series-left"
            text: card.model.left
            placeholderText: "Gracz lewy"
        }
        SeriesField {
            id: rightField
            objectName: "series-right"
            text: card.model.right
            placeholderText: "Gracz prawy"
        }
    }
    RowLayout {
        visible: !card.series
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: Stream.SERIES_FORMATS
            TextButton {
                required property int modelData
                objectName: "series-bo" + modelData
                variant: "outline"
                topPadding: 6
                bottomPadding: 6
                text: "BO" + modelData
                textColor: card.format === modelData ? Tokens.textPrimary : Tokens.textSecondary
                Accessible.name: "Format BO" + modelData + (card.format === modelData ? ", wybrany" : "")
                background: Rectangle {
                    radius: Tokens.radiusSmall
                    color: card.format === modelData ? Theme.alpha(Tokens.accent, 0.18) : Theme.alpha(Tokens.textPrimary, 0.04)
                    border.width: 1
                    border.color: card.format === modelData ? Tokens.accent : Theme.alpha(Tokens.textPrimary, 0.14)
                }
                onClicked: card.format = modelData
            }
        }
        Item { Layout.fillWidth: true }
        TextButton {
            objectName: "series-start"
            readonly property var command: Stream.seriesStart(leftField.text, rightField.text, card.format)
            variant: "outline"
            topPadding: 6
            bottomPadding: 6
            enabled: !card.busy && command !== null
            text: "Nowa seria"
            textColor: Tokens.accent
            onClicked: card.commandRequested(command.name, command.args)
        }
    }

    // Reszta wolnego miejsca (cyfry osiągnęły największy rozmiar albo nie ma serii) na dole karty.
    Item { Layout.fillHeight: true; Layout.verticalStretchFactor: 1 }

    component SeriesField: QQC2.TextField {
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        selectByMouse: true
        color: Tokens.textPrimary
        placeholderTextColor: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt
        Accessible.name: placeholderText
        background: Rectangle {
            implicitHeight: 30
            color: Tokens.backgroundView
            radius: Tokens.radiusSmall
            border.width: 1
            border.color: parent.activeFocus ? Tokens.accent : Theme.alpha(Tokens.textPrimary, 0.18)
        }
    }
}
