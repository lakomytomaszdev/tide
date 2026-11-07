// Sekcja „Środowisko karty” w planie do zatwierdzenia (PT-172, K-38): stan pudełka, pozycje z kolorem ryzyka,
// zgody na pozycje wysokiego ryzyka, „Zatwierdź plan i środowisko”, „Tylko plan” i „Zmień” (edycja YAML).
//
// Komponent niczego nie liczy: klasy ryzyka, kolory (`token`), które pozycje wymagają zgody (`needsConsent`),
// listę kluczy profilu i klucz zgody (`acceptKey`) podaje usługa w modelu (`environment`, derived.flow.plan.environment).
// Walidację YAML-a i zgód robi usługa przy ApproveCardEnvironment; odmowa wraca w `error`/`reply` i jest pokazana tutaj.
// Zgoda to wyłącznie jawne zaznaczenie pozycji przez użytkownika; nic nie jest zaznaczone z góry.
// Wspólny z oknem planu karty (K-35): ten sam plik w widoku Tablica i w innych widokach z panelem karty.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide

ColumnLayout {
    id: section
    objectName: "cardEnvironment"

    property var environment: null           // model z usługi (patrz wyżej); null — sekcja ukryta
    property bool planPending: true          // plan czeka na zatwierdzenie; inaczej tylko „Zatwierdź środowisko”
    property bool busy: false
    property string error: ""                // komunikat usługi po odmowie
    property var reply: null                 // odpowiedź usługi po odmowie: `items` do ponownego pokazania
    property var consented: []               // identyfikatory pozycji zaznaczonych przez użytkownika
    property string toggleLine: ""           // fragment YAML wybrany przełącznikiem pozycji (`toggle.on` / `toggle.off`)
    property bool editing: false
    property bool exposureAccepted: false
    property string seenExposure: ""
    readonly property bool projectExposure: !!(environment && environment.projectExposure)
    readonly property var exposure: (reply && reply.exposure) || (environment && environment.exposure) || ({})
    onExposureChanged: {
        if (seenExposure !== (exposure.expected || "")) exposureAccepted = false
        seenExposure = exposure.expected || ""
    }
    readonly property var shownItems: reply && reply.items && reply.items.length ? reply.items
                                      : (environment && environment.items) || []
    signal approveRequested(string yaml, bool onlyPlan)

    visible: !!environment
    spacing: 8
    Layout.fillWidth: true

    // Tekst wysyłany do usługi: YAML z modelu (albo poprawiony w „Zmień”) plus zaznaczenia użytkownika.
    function composeYaml(action) {
        let text = editing ? editor.text : (environment.yaml || "")
        text = text.replace(/\s*$/, "\n")
        if (toggleLine !== "") text += toggleLine + "\n"
        if (consented.length > 0)
            text += environment.acceptKey + ":\n" + consented.map(id => "  - " + JSON.stringify(id)).join("\n") + "\n"
        if (projectExposure && environment.exposureControls) {
            const keys = environment.exposureControls
            text += keys.action + ": " + (action || "approve") + "\n"
            text += keys.expected + ": " + JSON.stringify(action === "revoke" ? environment.approvedVersion : (exposure.expected || "")) + "\n"
            text += keys.accepted + ": " + (exposureAccepted ? "true" : "false") + "\n"
        }
        return text
    }

    function isChecked(item) {
        return consented.indexOf(item.id) >= 0
    }

    function toggleConsent(item) {
        const next = consented.filter(id => id !== item.id)
        if (next.length === consented.length) next.push(item.id)
        consented = next
    }

    function diffText() {
        const d = (environment && environment.diff) || {}
        const parts = []
        if ((d.added || []).length) parts.push("dodane: " + d.added.join(", "))
        if ((d.removed || []).length) parts.push("usunięte: " + d.removed.join(", "))
        return parts.join(" · ")
    }

    Text {
        Layout.fillWidth: true
        text: section.projectExposure ? "Środowisko i zgoda projektu" : "Środowisko karty"
        textFormat: Text.PlainText
        color: Tide.Tokens.sky
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 1.1
        font.bold: true
    }
    Text {
        objectName: "envProjectScope"
        Layout.fillWidth: true
        visible: section.projectExposure
        text: section.exposure.scope || ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envProjectManifest"
        Layout.fillWidth: true
        visible: section.projectExposure
        text: section.exposure.details || ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.9
        wrapMode: Text.WrapAnywhere
    }
    QQC2.CheckBox {
        objectName: "envProjectConsent"
        visible: section.projectExposure && !section.exposure.approved
        text: "Zatwierdzam pokazany zakres i wersję zgody projektu"
        checked: section.exposureAccepted
        enabled: !section.busy && !!section.exposure.expected
        onToggled: section.exposureAccepted = checked
    }
    Text {
        objectName: "envProjectApproved"
        Layout.fillWidth: true
        visible: section.projectExposure && !!section.exposure.approved
        text: "Ten dokładny zakres ma już zgodę projektu. Kolejna karta nie wymaga ponownego zaznaczenia."
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envProjectStatus"
        Layout.fillWidth: true
        visible: section.projectExposure && text !== ""
        text: (section.reply && section.reply.message) || ""
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envState"
        Layout.fillWidth: true
        text: section.environment ? "Pudełko: " + section.environment.stateLabel : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envDiff"
        Layout.fillWidth: true
        visible: text !== ""
        text: section.diffText()
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.95
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envWait"
        Layout.fillWidth: true
        visible: text !== ""
        text: section.environment && section.environment.boxWaitLabel ? "Karta: " + section.environment.boxWaitLabel : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.95
        font.bold: true
    }
    Text {
        objectName: "envNotes"
        Layout.fillWidth: true
        visible: text !== ""
        text: section.environment ? (section.environment.notes || []).join(" · ") : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.95
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "envQuestion"
        Layout.fillWidth: true
        visible: text !== ""
        text: section.environment && section.environment.question
              ? section.environment.question + " " + (section.environment.questionHint || "") : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        font.bold: true
        wrapMode: Text.Wrap
    }

    Repeater {
        model: section.shownItems
        delegate: Rectangle {
            id: row
            objectName: "envItem"
            required property var modelData
            readonly property string itemId: modelData.id
            readonly property bool consentItem: modelData.needsConsent === true
            readonly property bool toggleItem: !!modelData.toggle
            // Zaznaczenie pozycji: zgoda użytkownika (pozycje wymagające zgody), wybór przełącznika albo stan z modelu.
            readonly property bool checked: {
                if (modelData.blocked === true) return false
                if (consentItem) return section.isChecked(modelData)
                if (toggleItem && section.toggleLine !== "") return section.toggleLine === modelData.toggle.on
                return modelData.enabled === true
            }
            Layout.fillWidth: true
            implicitHeight: content.implicitHeight + 14
            radius: Tide.Tokens.radiusTile
            color: Tide.Tokens.backgroundCard
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens[modelData.token], 0.5)

            RowLayout {
                id: content
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 7
                spacing: 8
                Rectangle {
                    objectName: "envItemRisk"
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 3
                    implicitWidth: 10
                    implicitHeight: 10
                    radius: 5
                    color: Tide.Tokens[row.modelData.token]
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        objectName: "envItemLabel"
                        Layout.fillWidth: true
                        text: row.modelData.label + " · " + row.modelData.groupLabel
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "envItemRiskLabel"
                        Layout.fillWidth: true
                        text: row.modelData.riskLabel + (row.modelData.scope ? " · " + row.modelData.scope : "")
                        textFormat: Text.PlainText
                        color: Tide.Tokens[row.modelData.token]
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    }
                    Text {
                        objectName: "envItemNote"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: row.modelData.note || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        wrapMode: Text.Wrap
                    }
                }
                Rectangle {
                    objectName: "envItemSwitch"
                    enabled: row.modelData.blocked !== true
                    visible: row.consentItem || row.toggleItem
                    Layout.alignment: Qt.AlignTop
                    implicitWidth: 18
                    implicitHeight: 18
                    radius: 4
                    color: row.checked ? Tide.Tokens.accent : "transparent"
                    border.width: 1.5
                    border.color: row.checked ? Tide.Tokens.accent
                                              : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                    activeFocusOnTab: true
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: row.modelData.label
                    Accessible.checked: row.checked
                    Accessible.onToggleAction: row.flip()
                    Keys.onSpacePressed: row.flip()
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -6
                        cursorShape: Qt.PointingHandCursor
                        onClicked: row.flip()
                    }
                }
            }

            function flip() {
                if (consentItem) section.toggleConsent(modelData)
                else if (toggleItem) section.toggleLine = checked ? modelData.toggle.off : modelData.toggle.on
            }
        }
    }

    QQC2.TextArea {
        id: editor
        objectName: "envEditor"
        Layout.fillWidth: true
        visible: section.editing
        wrapMode: TextEdit.Wrap
        font.family: Tide.Theme.monoFamily
        placeholderText: "Profil karty (YAML)"
        onTextChanged: section.exposureAccepted = false
    }
    Text {
        objectName: "envError"
        Layout.fillWidth: true
        visible: text !== ""
        text: section.error
        textFormat: Text.PlainText
        color: Tide.Tokens.negative
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.95
        wrapMode: Text.Wrap
    }

    RowLayout {
        Layout.fillWidth: true
        visible: section.projectExposure
        spacing: 8
        Tide.TextButton {
            objectName: "envProjectPreview"
            visible: section.projectExposure
            text: "Pokaż dokładny zakres"
            variant: "outline"
            enabled: !section.busy && !!section.environment && !!section.environment.exposureControls
            onClicked: {
                section.exposureAccepted = false
                section.approveRequested(section.composeYaml("preview"), false)
            }
        }
        Tide.TextButton {
            objectName: "envProjectRevoke"
            visible: section.projectExposure && !!section.environment.approvedVersion
            text: "Wycofaj zgodę projektu"
            variant: "outline"
            enabled: !section.busy
            onClicked: {
                section.exposureAccepted = false
                section.approveRequested(section.composeYaml("revoke"), false)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Tide.TextButton {
            objectName: "envApprove"
            text: section.planPending ? "Zatwierdź plan i środowisko" : "Zatwierdź środowisko"
            variant: "filled"
            enabled: !section.busy && (!section.projectExposure || section.exposureAccepted || !!section.exposure.approved)
            opacity: enabled ? 1 : 0.45
            onClicked: section.approveRequested(section.composeYaml(), false)
        }
        Tide.TextButton {
            objectName: "envOnlyPlan"
            visible: section.planPending
            text: "Tylko plan"
            variant: "outline"
            enabled: !section.busy
            opacity: enabled ? 1 : 0.45
            onClicked: section.approveRequested(section.composeYaml(), true)
        }
        Tide.TextButton {
            objectName: "envEdit"
            text: section.editing ? "Wróć do modelu" : "Zmień"
            variant: "outline"
            enabled: !section.busy
            onClicked: {
                if (!section.editing) editor.text = section.environment.yaml || ""
                section.exposureAccepted = false
                section.editing = !section.editing
            }
        }
    }
}
