// Popup kategorii ustawień nad kolumną „Teraz” (PT-105 i PT-106, decyzje 2 i 4 K-29). Otwiera go ikona z rzędu
// kategorii, zamyka Esc, × albo ponowne kliknięcie tej samej ikony; karty „Teraz” zostają pod spodem. Popup jest
// nieprzezroczysty i sięga od rzędu kategorii do dołu prawej kolumny (wysokość ustawia NowColumn), więc nie
// zostawia pod sobą do połowy zasłoniętej karty; dłuższa treść przewija się w środku.
// Treść liczy Stream.categoryContent (te same komendy rdzenia i blokady co kategorie na tablecie), popup tylko ją
// rysuje: nagłówki, dopiski, przełączniki z testem, przyciski wyboru, rzędy przycisków, formularze, wynik serii
// i wynik kontroli przed startem. Pola tekstowe zapisują się dopiero Enterem albo przyciskiem, a stan z rdzenia
// nie nadpisuje pola, w którym coś wpisano. Wpisany, niezapisany tekst (i fokus pola) trzyma popup w `drafts`, więc
// przetrwa też przebudowę treści (np. koniec serii dokłada formularz następnej) — znika po udanym zapisie albo po
// zamknięciu popupu. Pole zostaje oznaczone jako zmienione, dopóki rdzeń nie potwierdzi zapisu (commandDone).
// Komendę wysyła tylko wtedy, gdy przepuszcza ją Stream.allowedNow.
import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "stream.js" as Stream

QQC2.Popup {
    id: popup

    // Wpis z Stream.categories(): {id, label, short, icon, warning}.
    property var category: null
    property var view: null
    property bool coreUp: false
    property bool busy: false
    // Wynik ostatniej kontroli przed startem (preflight.run z chwilą `at`) i czy kontrola trwa.
    property var preflight: null
    property bool preflightRunning: false
    // Błąd ostatniej komendy (karta Stream jest pod popupem, więc komunikat stoi też tutaj).
    property string status: ""
    signal panelRequested()
    signal commandRequested(string name, var args)
    // Wynik zapisu formularza (klucz pozycji): ok — rdzeń przyjął komendę.
    signal formSettled(string key, bool ok)

    // Niezapisane wpisy pól: {"<kategoria>/<pozycja>/<pole>": tekst}, także wybrane BO („…/format”).
    property var drafts: ({})
    // Pole, które miało fokus (klucz jak w drafts) — wraca do niego po przebudowie treści.
    property string focusKey: ""
    // Zapis formularza czekający na odpowiedź rdzenia: {name, key} albo null.
    property var pendingForm: null
    // Zegar dla końca odliczania (przycisk „Wyłącz” gaśnie, gdy odliczanie minie).
    property real now: Date.now()
    Timer {
        interval: 5000
        repeat: true
        running: popup.opened
        onTriggered: popup.now = Date.now()
    }

    readonly property var content: Stream.categoryContent(category ? category.id : "", view, coreUp,
                                                          { preflight: preflight, preflightRunning: preflightRunning,
                                                            now: now })

    function draftKey(item, field) {
        return (category ? category.id : "") + "/" + item + "/" + field
    }
    function dropDrafts(item) {
        const prefix = draftKey(item, "")
        const next = {}
        for (const k in drafts) if (k.indexOf(prefix) !== 0) next[k] = drafts[k]
        drafts = next
    }
    // Odpowiedź rdzenia na komendę (NowColumn.commandDone). Udany zapis formularza czyści jego wpisy.
    function commandDone(name, ok) {
        if (!pendingForm || pendingForm.name !== name) return
        const key = pendingForm.key
        pendingForm = null
        if (ok) dropDrafts(key)
        formSettled(key, ok)
    }
    onOpened: { now = Date.now(); flick.contentY = 0 }
    onClosed: { drafts = ({}); focusKey = ""; pendingForm = null }
    // Fokus wraca do pola po przebudowie tylko wtedy, gdy nie odszedł poza popup (np. na ikonę kategorii) —
    // przebudowa nie może zabrać fokusu z innego miejsca.
    Connections {
        target: popup.contentItem ? popup.contentItem.Window.window : null
        function onActiveFocusItemChanged() {
            const item = target.activeFocusItem
            if (!item || popup.focusKey === "") return
            for (let it = item; it; it = it.parent) if (it === popup.contentItem || it === popup.contentItem.parent) return
            popup.focusKey = ""
        }
    }
    // Delegaty powstają od nowa tylko przy zmianie układu (rodzaj i klucz pozycji), nie przy każdej zmianie stanu —
    // wpisany tekst, fokus i potwierdzenie odłączenia przetrwają odświeżenie z rdzenia.
    readonly property string layoutKey: (category ? category.id + "/" : "")
                                        + content.items.map(i => i.kind + ":" + i.key).join("|")

    // true, gdy komenda poszła (z listy ALLOWED, zmiana trybu tylko poza anteną).
    function send(command) {
        if (!command || !Stream.allowedNow(command.name, view, coreUp)) return false
        commandRequested(command.name, command.args)
        return true
    }

    objectName: "categoryPopup"
    padding: 0
    modal: false
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape
    background: Rectangle {
        objectName: "categoryBackground"
        radius: Tokens.radiusCard
        color: Tokens.backgroundCard
        border.width: 1
        border.color: Theme.alpha(Tokens.accent, 0.65)
    }

    contentItem: Item {
        RowLayout {
            id: header
            x: 16
            y: 12
            width: parent.width - 32
            spacing: 8
            LineIcon {
                name: popup.category ? popup.category.icon : ""
                size: 18
                color: Tokens.accent
            }
            Text {
                objectName: "categoryTitle"
                text: popup.category ? popup.category.label : ""
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: "Esc zamyka"
                textFormat: Text.PlainText
                color: Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 2
            }
            IconButton {
                objectName: "categoryClose"
                iconName: "x"
                tip: "Zamknij kategorię"
                onClicked: popup.close()
            }
        }

        Rectangle {
            id: rule
            x: 16
            y: header.y + header.height + 10
            width: parent.width - 32
            height: 1
            color: Theme.alpha(Tokens.textPrimary, 0.08)
        }

        Flickable {
            id: flick
            objectName: "categoryFlick"
            x: 16
            y: rule.y + 1
            width: parent.width - 32
            height: Math.max(0, parent.height - y - 4)
            contentWidth: width
            contentHeight: body.implicitHeight + 24
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: body
                objectName: "categoryBody"
                y: 12
                width: flick.width
                spacing: 10

                Text {
                    objectName: "categoryWarning"
                    visible: !!popup.category && popup.category.warning !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: popup.category ? "Wymaga uwagi: " + popup.category.warning : ""
                    textFormat: Text.PlainText
                    color: Tokens.neutral
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                }

                Text {
                    objectName: "categoryStatus"
                    visible: popup.status !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: popup.status
                    textFormat: Text.PlainText
                    color: Tokens.textSecondary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt - 1
                }

                Repeater {
                    model: popup.layoutKey === "" ? [] : popup.layoutKey.split("|")
                    Loader {
                        required property int index
                        // Świeży wpis przy każdej zmianie stanu (bez tworzenia delegata od nowa).
                        readonly property var entry: popup.content.items[index] || ({ kind: "", key: "" })
                        Layout.fillWidth: true
                        Layout.topMargin: entry.kind === "heading" && index > 0 ? 8 : 0
                        sourceComponent: ({ heading: headingItem, note: noteItem, row: rowItem, choice: choiceItem,
                                            actions: actionsItem, form: formItem, score: scoreItem,
                                            checks: checksItem })[entry.kind] || null
                    }
                }

                TextButton {
                    objectName: "categoryPanel"
                    visible: popup.content.panel
                    variant: "link"
                    text: "Otwórz Panel streamera"
                    onClicked: popup.panelRequested()
                }
            }
        }

        // Wąski wskaźnik przewijania, jak w kolumnie „Teraz”.
        Rectangle {
            objectName: "categoryScroll"
            visible: flick.interactive
            x: parent.width - 8
            y: flick.y + flick.visibleArea.yPosition * flick.height
            width: 3
            height: flick.visibleArea.heightRatio * flick.height
            radius: width / 2
            color: Theme.alpha(Tokens.textPrimary, 0.30)
        }
    }

    // --- Części pozycji. Komponenty niżej czytają wpis ze slotu: `parent.entry` (rodzic to Loader). ---

    component Label: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tokens.textPrimary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt
    }

    component Detail: Text {
        Layout.fillWidth: true
        visible: text !== ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 1
    }

    // Przycisk ustawienia: zaznaczony (on) ma tło i obwódkę w kolorze akcentu, wyłączony jest przygaszony.
    component SettingButton: TextButton {
        property bool on: false
        variant: "outline"
        topPadding: 7
        bottomPadding: 7
        pointSize: Tokens.fontSizePt - 1
        enabled: !popup.busy
        textColor: on ? Tokens.textPrimary : Tokens.textSecondary
        // Zaznaczona, ale zablokowana opcja (ostatni cel transmisji, bieżący tryb na żywo) nie wygląda na wyłączoną.
        opacity: enabled || on ? 1 : 0.45
        Accessible.name: text + (on ? ", wybrane" : "")
        onActiveFocusChanged: if (activeFocus) popup.focusKey = ""
        background: Rectangle {
            radius: Tokens.radiusSmall
            color: parent.on ? Theme.alpha(Tokens.accent, parent.lit ? 0.26 : 0.18)
                             : Theme.alpha(Tokens.textPrimary, parent.lit ? 0.10 : 0.04)
            border.width: 1
            border.color: parent.visualFocus ? Tokens.accent
                        : parent.on ? Theme.alpha(Tokens.accent, 0.75) : Theme.alpha(Tokens.textPrimary, 0.14)
        }
    }

    // Pole tekstowe z zapisem na żądanie: tekst z rdzenia wchodzi tylko wtedy, gdy w polu nic nie zmieniono
    // (dirty), więc odświeżenie stanu nie zabiera tego, co właśnie wpisujesz.
    component SettingField: QQC2.TextField {
        id: settingField
        property string stateValue: ""
        property bool dirty: false
        property string draft: ""          // klucz w popup.drafts
        signal commit()
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        selectByMouse: true
        leftPadding: 9
        rightPadding: 9
        color: Tokens.textPrimary
        placeholderTextColor: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt
        Accessible.name: placeholderText
        // Tekst z rdzenia pokazuje się od początku (długi podtytuł nie jest przewinięty do końca).
        function showState() { text = stateValue; cursorPosition = 0 }
        // Niezapisany wpis sprzed przebudowy treści wraca razem z fokusem.
        Component.onCompleted: {
            if (draft in popup.drafts) {
                text = popup.drafts[draft]
                dirty = true
                if (popup.focusKey === draft) { forceActiveFocus(); cursorPosition = length }
            } else showState()
        }
        onStateValueChanged: if (!dirty) showState()
        onTextEdited: { dirty = true; popup.drafts[draft] = text }
        onActiveFocusChanged: if (activeFocus) popup.focusKey = draft
        Connections {
            target: popup
            function onClosed() { settingField.dirty = false; settingField.showState() }
        }
        onAccepted: commit()
        background: Rectangle {
            implicitHeight: 32
            color: Tokens.backgroundView
            radius: Tokens.radiusSmall
            border.width: 1
            border.color: parent.activeFocus ? Tokens.accent
                        : parent.dirty ? Theme.alpha(Tokens.accent, 0.55) : Theme.alpha(Tokens.textPrimary, 0.18)
        }
    }

    Component {
        id: headingItem
        Text {
            objectName: "cat-" + parent.entry.key
            text: String(parent.entry.text).toUpperCase()
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 2
            font.weight: Font.DemiBold
            font.letterSpacing: 1.2
        }
    }

    Component {
        id: noteItem
        Text {
            objectName: "cat-" + parent.entry.key
            text: parent.entry.text
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Tokens[parent.entry.tone] || Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
        }
    }

    // Przełącznik albo wiersz stanu: nazwa i dopisek po lewej, test i przycisk po prawej.
    Component {
        id: rowItem
        RowLayout {
            id: row
            readonly property var entry: parent.entry
            // Pierwsze kliknięcie przycisku z potwierdzeniem tylko je uzbraja; po 4 s wraca zwykły napis.
            property bool armed: false
            objectName: "cat-" + entry.key + "-row"
            spacing: 8
            Timer {
                id: disarm
                interval: 4000
                onTriggered: row.armed = false
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Label { text: row.entry.label }
                RowLayout {
                    visible: row.entry.detail !== ""
                    Layout.fillWidth: true
                    spacing: 6
                    Rectangle {
                        visible: row.entry.tint !== ""
                        width: 7
                        height: 7
                        radius: width / 2
                        color: row.entry.tint !== "" ? Tokens[row.entry.tint] : "transparent"
                    }
                    Detail {
                        objectName: "cat-" + row.entry.key + "-detail"
                        readonly property string warning: row.armed && row.entry.button ? row.entry.button.warning || "" : ""
                        text: warning !== "" ? warning : row.entry.detail
                        color: warning !== "" || row.entry.tint === Stream.WARNING_TINT ? Tokens.neutral : Tokens.textMuted
                    }
                }
            }
            SettingButton {
                objectName: "cat-" + row.entry.key + "-test"
                visible: !!row.entry.test
                enabled: !popup.busy && !!row.entry.test && row.entry.test.enabled
                text: row.entry.test ? row.entry.test.text : ""
                Accessible.name: row.entry.test ? row.entry.test.tip || text : ""
                onClicked: popup.send(row.entry.test.command)
            }
            SettingButton {
                objectName: "cat-" + row.entry.key
                visible: !!row.entry.button
                Layout.preferredWidth: Math.max(implicitWidth, 108)
                on: row.entry.on === true
                enabled: !popup.busy && !!row.entry.button && row.entry.button.enabled
                text: !row.entry.button ? "" : row.armed ? row.entry.button.confirm : row.entry.button.text
                textColor: row.armed ? Tokens.neutral : on ? Tokens.textPrimary : Tokens.textSecondary
                Accessible.name: row.entry.label + ": " + text
                onClicked: {
                    const b = row.entry.button
                    if (b.confirm !== "" && !row.armed) { row.armed = true; disarm.restart(); return }
                    row.armed = false
                    popup.send(b.command)
                }
            }
        }
    }

    Component {
        id: choiceItem
        ColumnLayout {
            id: choice
            readonly property var entry: parent.entry
            objectName: "cat-" + entry.key + "-choice"
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: choice.entry.label
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                }
                Detail {
                    objectName: "cat-" + choice.entry.key + "-detail"
                    text: choice.entry.detail
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: choice.entry.options.length
                    SettingButton {
                        id: option
                        required property int index
                        readonly property var opt: choice.entry.options[index] || ({})
                        objectName: "cat-" + choice.entry.key + "-" + opt.key
                        on: opt.on === true
                        enabled: !popup.busy && opt.enabled === true
                        text: opt.text || ""
                        PlainToolTip {
                            visible: option.hovered && !!option.opt.tip
                            text: option.opt.tip || ""
                        }
                        onClicked: popup.send(opt.command)
                    }
                }
            }
        }
    }

    Component {
        id: actionsItem
        ColumnLayout {
            id: actions
            readonly property var entry: parent.entry
            objectName: "cat-" + entry.key + "-actions"
            spacing: 5
            Label {
                visible: actions.entry.label !== ""
                text: actions.entry.label
            }
            Detail {
                objectName: "cat-" + actions.entry.key + "-detail"
                text: actions.entry.detail
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: actions.entry.buttons.length
                    SettingButton {
                        required property int index
                        readonly property var act: actions.entry.buttons[index] || ({})
                        objectName: "cat-" + actions.entry.key + "-" + act.key
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        enabled: !popup.busy && act.enabled === true
                        text: act.text || ""
                        textColor: Tokens.textPrimary
                        Accessible.name: act.tip || text
                        onClicked: popup.send(act.command)
                    }
                }
            }
        }
    }

    // Formularz: pola w jednym rzędzie z „Zapisz”; nowa seria ma pod polami wybór BO i „Nowa seria”.
    Component {
        id: formItem
        ColumnLayout {
            id: form
            readonly property var entry: parent.entry
            readonly property bool series: Array.isArray(entry.formats)
            readonly property string formatKey: popup.draftKey(entry.key, "format")
            property int format: formatKey in popup.drafts ? popup.drafts[formatKey] : entry.format || 0
            objectName: "cat-" + entry.key + "-form"
            spacing: 5

            // Pola zostają oznaczone jako zmienione, dopóki rdzeń nie przyjmie zapisu; po odmowie dalej są.
            Connections {
                target: popup
                function onFormSettled(key, ok) {
                    if (key !== form.entry.key || !ok) return
                    for (let i = 0; i < fields.count; i++) {
                        const f = fields.itemAt(i)
                        if (f) { f.dirty = false; f.showState() }
                    }
                }
            }

            function values() {
                const out = {}
                for (let i = 0; i < fields.count; i++) {
                    const f = fields.itemAt(i)
                    if (f) out[f.key] = f.text
                }
                return out
            }
            // Komenda z bieżących pól (null przy niepełnym formularzu) — przelicza się przy każdym znaku.
            readonly property var pending: Stream.formCommand(entry, values(), format)
            function save() {
                const cmd = Stream.formCommand(entry, values(), format)
                if (!cmd || popup.busy) return
                popup.pendingForm = { name: cmd.name, key: entry.key }
                if (!popup.send(cmd)) popup.pendingForm = null
            }

            Label {
                visible: form.entry.label !== ""
                text: form.entry.label
            }
            Detail {
                objectName: "cat-" + form.entry.key + "-detail"
                text: form.entry.detail
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    id: fields
                    model: form.entry.fields.length
                    SettingField {
                        required property int index
                        readonly property var field: form.entry.fields[index] || ({ key: "", placeholder: "", value: "" })
                        readonly property string key: field.key
                        objectName: "cat-" + form.entry.key + "-" + field.key
                        draft: popup.draftKey(form.entry.key, field.key)
                        placeholderText: field.placeholder
                        stateValue: field.value
                        onCommit: form.save()
                    }
                }
                SettingButton {
                    objectName: "cat-" + form.entry.key + "-save"
                    visible: !form.series
                    enabled: !popup.busy && form.pending !== null
                    text: form.entry.save
                    textColor: Tokens.accent
                    onClicked: form.save()
                }
            }
            RowLayout {
                visible: form.series
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: form.series ? form.entry.formats : []
                    SettingButton {
                        required property int modelData
                        objectName: "cat-" + form.entry.key + "-bo" + modelData
                        on: form.format === modelData
                        text: "BO" + modelData
                        Accessible.name: "Format BO" + modelData + (on ? ", wybrany" : "")
                        onClicked: { form.format = modelData; popup.drafts[form.formatKey] = modelData }
                    }
                }
                Item { Layout.fillWidth: true }
                SettingButton {
                    objectName: "cat-" + form.entry.key + "-start"
                    enabled: !popup.busy && form.pending !== null
                    text: form.entry.save
                    textColor: Tokens.accent
                    onClicked: form.save()
                }
            }
        }
    }

    Component {
        id: scoreItem
        ColumnLayout {
            id: score
            readonly property var entry: parent.entry
            objectName: "cat-" + entry.key + "-score"
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                    text: score.entry.left
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 1
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "cat-" + score.entry.key + "-points"
                    text: score.entry.score
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 8
                    font.weight: Font.Bold
                }
                Text {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    elide: Text.ElideRight
                    text: score.entry.right
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 1
                    font.weight: Font.DemiBold
                }
            }
            Text {
                Layout.alignment: Qt.AlignHCenter
                text: score.entry.line + (score.entry.winner !== "" ? " · wygrał " + score.entry.winner : "")
                textFormat: Text.PlainText
                color: score.entry.winner !== "" ? Tokens.accent : Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
        }
    }

    // Wynik kontroli przed startem: najpierw to, co trzeba poprawić (z instrukcją), potem jedna linia „w porządku”.
    Component {
        id: checksItem
        ColumnLayout {
            id: checks
            readonly property var entry: parent.entry
            objectName: "cat-" + entry.key
            spacing: 6
            Detail {
                objectName: "cat-" + checks.entry.key + "-summary"
                text: (checks.entry.bad.length ? checks.entry.bad.length + " do poprawy" : "Wszystko w porządku")
                      + (checks.entry.at !== "" ? " · sprawdzone " + checks.entry.at : "")
                color: checks.entry.bad.length ? Tokens.neutral : Tokens.positive
            }
            Repeater {
                model: checks.entry.bad.length
                Rectangle {
                    id: check
                    required property int index
                    readonly property var fault: checks.entry.bad[index] || ({})
                    objectName: "cat-check-" + fault.id
                    Layout.fillWidth: true
                    implicitHeight: checkBody.implicitHeight + 16
                    radius: Tokens.radiusSmall
                    color: Theme.alpha(Tokens.neutral, 0.10)
                    border.width: 1
                    border.color: Theme.alpha(Tokens.neutral, 0.35)
                    ColumnLayout {
                        id: checkBody
                        x: 10
                        y: 8
                        width: parent.width - 20
                        spacing: 2
                        Label { text: check.fault.label + " — " + check.fault.message }
                        Detail {
                            text: check.fault.fix || ""
                            color: Tokens.textSecondary
                        }
                    }
                }
            }
            Detail {
                visible: checks.entry.ok.length > 0
                text: "W porządku: " + checks.entry.ok.join(", ")
            }
        }
    }
}
