// Szeroki widok planu karty: fale, przydziały, kartki ticketów i decyzja o uruchomieniu pracy.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: manager
    property var card: null
    property var modelsStatus: null
    property bool busy: false
    property string error: ""
    property string notice: ""
    property string selectedTicketId: ""
    property bool appending: false
    property string appendFingerprint: ""
    property string appendError: ""
    property string appendPayload: ""
    property string appendKey: ""
    readonly property var plan: card && card.derived && card.derived.flow ? card.derived.flow.plan || null : null
    readonly property var rows: plan ? plan.rows || [] : []
    readonly property var selectedTicket: rows.find(r => (r.id || r.workId) === selectedTicketId) || null
    readonly property bool canEdit: !!plan && (plan.status === "blocked" || plan.status === "pending" || plan.status === "approved")
    readonly property bool appendRevision: !!plan && !!plan.revision && plan.revision.kind === "append"
    readonly property bool ticketFieldsEnabled: appending || (canEdit && !!selectedTicket && selectedTicket.editable !== false)
    signal closeRequested()
    signal cardRequested()
    signal editRequested(string cardId, var request)
    signal transferRequested(string cardId, var request)
    signal approveRequested(string cardId)

    function waveOf(id) {
        for (let i = 0; i < (plan ? plan.waves || [] : []).length; i++)
            if (plan.waves[i].indexOf(id) >= 0) return i + 1
        return 0
    }
    function optionIndex(options, value) {
        return (options || []).findIndex(o => o.value === value)
    }
    function selectTicket(row) {
        appending = false
        selectedTicketId = row.id || row.workId
        classPicker.currentIndex = ["lekki", "standard", "mocny", "lokalny"].indexOf(row.class || "standard")
        executorPicker.currentIndex = optionIndex(executorPicker.model, row.executor)
        reviewerPicker.wanted = row.reviewer || ""
        reviewerPicker.currentIndex = optionIndex(reviewerPicker.model, row.reviewer)
        bodyEdit.text = row.body || ""
    }
    function beginAppend() {
        appending = true
        appendFingerprint = plan.fingerprint
        appendError = ""
        titleEdit.text = ""
        blockersEdit.text = ""
        bodyEdit.text = "# Cel\n\n\n## Kryteria akceptacji\n- [ ] \n\n## Dla recenzenta\n"
        classPicker.currentIndex = 1
        executorPicker.currentIndex = -1
        reviewerPicker.wanted = ""
        reviewerPicker.currentIndex = -1
    }
    function submitAppend() {
        const blockers = blockersEdit.text.split(",").map(s => s.trim()).filter(s => s)
        const ids = rows.map(r => r.id || r.workId)
        if (!titleEdit.text.trim() || titleEdit.text.length > 500 || /[|\r\n]/.test(titleEdit.text))
            appendError = "Wpisz tytuł w jednym wierszu (do 500 znaków, bez |)."
        else if (!/^\s*-\s*\[\s*\]\s*\S/m.test(bodyEdit.text))
            appendError = "Dodaj do kartki mierzalne kryteria jako - [ ] opis wyniku."
        else if (executorPicker.currentIndex < 0 || reviewerPicker.currentIndex < 0)
            appendError = "Wybierz wykonawcę i niezależnego recenzenta."
        else if (blockers.some(b => ids.indexOf(b) < 0))
            appendError = "Wybierz blokery z istniejącego planu: " + ids.join(", ")
        else {
            appendError = ""
            const request = {action: "append", planFingerprint: appendFingerprint,
                title: titleEdit.text.trim(), body: bodyEdit.text, class: classPicker.currentText,
                executor: executorPicker.currentValue, reviewer: reviewerPicker.currentValue, blockers: blockers}
            const payload = JSON.stringify(request)
            if (payload !== appendPayload) {
                appendPayload = payload
                appendKey = "append-" + Date.now() + "-" + Math.random().toString(36).slice(2)
            }
            request.requestKey = appendKey
            editRequested(card.id, request)
        }
    }
    onAppendRevisionChanged: if (appendRevision) appending = false
    onRowsChanged: {
        if (!appending && rows.length && !rows.some(r => (r.id || r.workId) === selectedTicketId))
            Qt.callLater(() => { if (manager.rows.length) manager.selectTicket(manager.rows[0]) })
    }

    radius: 16
    color: Tide.Tokens.backgroundPanel
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
    Accessible.role: Accessible.Dialog
    Accessible.name: card ? "Plan karty " + card.id : "Plan karty"
    Keys.onEscapePressed: closeRequested()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: manager.card ? "Plan · " + manager.card.id + " · " + manager.card.title : "Plan karty"
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 22
                font.bold: true
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
            }
            PillButton { text: "Szczegóły karty"; outline: true; onClicked: manager.cardRequested() }
            PillButton {
                objectName: "planManagerAddTicket"
                text: "Dopisz ticket"
                visible: !!manager.plan && manager.plan.canAppend === true && !manager.appending
                enabled: !manager.busy
                onClicked: manager.beginAppend()
            }
            PillButton { text: "Zamknij"; outline: true; onClicked: manager.closeRequested() }
        }
        Text {
            objectName: "planManagerStatus"
            Layout.fillWidth: true
            text: manager.plan && manager.plan.status === "blocked"
                  ? "Przegląd planu wstrzymany: " + ((manager.plan.reviewWait || {}).reason || "czeka")
                  : manager.plan && manager.plan.status === "reviewing" ? "Trwa niezależny przegląd planu. Przydziały możesz teraz obejrzeć."
                  : manager.plan && manager.plan.status === "pending" ? "Plan gotowy do Twojej decyzji. Sprawdź tickety i modele, a potem uruchom implementację."
                  : manager.plan && manager.plan.status === "approved" ? "Plan zatwierdzony; implementacja trwa."
                  : ""
            textFormat: Text.PlainText
            color: manager.plan && manager.plan.status === "blocked" ? Tide.Tokens.butter : Tide.Tokens.sky
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            wrapMode: Text.Wrap
        }
        // Plan z kolumną Projekt (K-33 #4): źródło numerów każdego projektu zlecenia.
        Text {
            objectName: "planManagerAppendDifference"
            Layout.fillWidth: true
            visible: manager.appendRevision
            text: "Dopisanie jednego ticketu: " + (manager.appendRevision ? manager.plan.revision.difference.added.join(", ") : "")
                  + ". Istniejące tickety zachowują treść i przydziały. Po review zatwierdź różnicę osobno."
            textFormat: Text.PlainText
            color: Tide.Tokens.sky
            font.family: Tide.Theme.uiFamily
            wrapMode: Text.Wrap
        }
        Text {
            objectName: "planManagerProjects"
            Layout.fillWidth: true
            visible: text !== ""
            text: Logic.planProjectsLine(manager.plan)
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }
        Text {
            objectName: "planManagerGroupNotice"
            Layout.fillWidth: true
            visible: Logic.planIsGroup(manager.plan)
            text: "Plan obejmuje kilka projektów: zatwierdzanie grupy kart — w następnej wersji."
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }
        // Uwagi review bywają długie (K-33: 23 punkty) — mają własne przewijanie i limit wysokości, żeby lista
        // ticketów i „Zatwierdź plan” zawsze mieściły się w oknie.
        QQC2.ScrollView {
            id: reviewScroll
            objectName: "planManagerReview"
            property bool expanded: false
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(reviewText.implicitHeight,
                                             expanded ? manager.height * 0.45 : 96)
            visible: !!manager.plan && !!manager.plan.review && !!manager.plan.review.verdict
            clip: true
            contentWidth: availableWidth
            Text {
                id: reviewText
                width: reviewScroll.availableWidth
                text: manager.plan && manager.plan.review
                      ? "Review: " + (manager.plan.review.verdict || "") + " · " + (manager.plan.review.reviewer || "")
                        + " · " + Logic.reviewFindingsText(manager.plan.review.findings) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                wrapMode: Text.Wrap
            }
        }
        Text {
            objectName: "planManagerReviewToggle"
            visible: reviewScroll.visible && reviewText.implicitHeight > 96
            text: reviewScroll.expanded ? "Zwiń uwagi review" : "Pokaż więcej uwag review"
            textFormat: Text.PlainText
            color: Tide.Tokens.sky
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            Accessible.role: Accessible.Button
            Accessible.name: text
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: reviewScroll.expanded = !reviewScroll.expanded
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: !!manager.plan && (manager.plan.status === "blocked" ||
                                        (manager.plan.status === "reviewing" && !!manager.plan.reviewStep))
            spacing: 10
            Text { text: manager.plan && manager.plan.reviewStep ? "Zmień recenzenta" : "Recenzent planu"
                   textFormat: Text.PlainText
                   color: Tide.Tokens.textPrimary; font.family: Tide.Theme.uiFamily }
            PlanSelect {
                id: reviewPicker
                objectName: "planManagerReviewExecutor"
                Layout.preferredWidth: 280
                model: [{value: "", label: "Wybierz recenzenta"}].concat(Logic.planReviewerExecutors(
                    manager.modelsStatus, manager.plan ? manager.plan.planner : "",
                    manager.plan && manager.plan.reviewStep ? manager.plan.reviewProvider :
                    manager.plan && manager.plan.reviewWait ? manager.plan.reviewWait.provider : "")
                    .filter(option => !manager.plan || !manager.plan.reviewStep ||
                        (Logic.launchExecutor(manager.modelsStatus, option.value) || {}).provider !== manager.plan.reviewProvider))
                textRole: "label"
                valueRole: "value"
                currentIndex: -1
                Accessible.name: "Model do przeglądu planu"
            }
            PillButton {
                objectName: "planManagerRunReview"
                text: manager.plan && manager.plan.reviewStep ? "Przenieś przegląd" : "Uruchom przegląd tym modelem"
                enabled: !manager.busy && !!reviewPicker.currentValue
                onClicked: {
                    if (manager.plan && manager.plan.reviewStep)
                        manager.transferRequested(manager.card.id, {choice: "transfer",
                            step: manager.plan.reviewStep, executor: reviewPicker.currentValue})
                    else
                        manager.editRequested(manager.card.id,
                            {action: "review-executor", executor: reviewPicker.currentValue})
                }
            }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16
            ColumnLayout {
                Layout.preferredWidth: manager.width * 0.48
                Layout.fillHeight: true
                spacing: 8
                Text {
                    text: "Tickety · " + manager.rows.length
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 16
                    font.bold: true
                }
                ListView {
                    id: ticketList
                    objectName: "planManagerTickets"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: manager.rows
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        readonly property string ticketId: modelData.id || modelData.workId
                        width: ticketList.width - 8
                        height: ticketInfo.implicitHeight + 20
                        radius: 9
                        color: manager.selectedTicketId === ticketId
                               ? Tide.Theme.alpha(Tide.Tokens.sky, 0.16) : Tide.Tokens.backgroundPanel
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.14)
                        ColumnLayout {
                            id: ticketInfo
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 10
                            spacing: 3
                            Text {
                                objectName: "planManagerRow-" + parent.parent.ticketId
                                Layout.fillWidth: true
                                text: "Fala " + manager.waveOf(parent.parent.ticketId) + " · " + parent.parent.ticketId
                                      + (Logic.planRowProject(manager.plan, parent.parent.modelData)
                                         ? " · ◆ " + Logic.planRowProject(manager.plan, parent.parent.modelData) : "")
                                      + " · " + parent.parent.modelData.title
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                                font.bold: true
                                wrapMode: Text.Wrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "Wykonawca: " + parent.parent.modelData.executor + " · Recenzent: "
                                      + parent.parent.modelData.reviewer
                                      + ((parent.parent.modelData.blockers || []).length
                                         ? " · Czeka na " + parent.parent.modelData.blockers.join(", ") : "")
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                        }
                        MouseArea { anchors.fill: parent; onClicked: manager.selectTicket(parent.modelData) }
                    }
                }
            }
            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12) }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10
                Text {
                    Layout.fillWidth: true
                    text: manager.appending ? "Nowy ticket · projekt karty · bez nadanego ID" : manager.selectedTicket ? (manager.selectedTicket.id || manager.selectedTicket.workId)
                          + " · " + manager.selectedTicket.title : "Wybierz ticket z listy"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 16
                    font.bold: true
                    wrapMode: Text.Wrap
                }
                QQC2.TextField {
                    id: titleEdit
                    objectName: "planManagerAppendTitle"
                    Layout.fillWidth: true
                    visible: manager.appending
                    placeholderText: "Tytuł nowego ticketu"
                    Accessible.name: "Tytuł nowego ticketu"
                }
                QQC2.TextField {
                    id: blockersEdit
                    objectName: "planManagerAppendBlockers"
                    Layout.fillWidth: true
                    visible: manager.appending
                    placeholderText: "Blokery z planu, po przecinku (opcjonalne)"
                    Accessible.name: "Blokery nowego ticketu"
                }
                Text {
                    objectName: "planManagerTicketProject"
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: Logic.planRowProject(manager.plan, manager.selectedTicket)
                          ? "Projekt: " + Logic.planRowProject(manager.plan, manager.selectedTicket) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    wrapMode: Text.Wrap
                }
                Text {
                    Layout.fillWidth: true
                    text: manager.selectedTicket && (manager.selectedTicket.blockers || []).length
                          ? "Blokery: " + manager.selectedTicket.blockers.join(", ") : "Blokery: brak"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    wrapMode: Text.Wrap
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 12
                    rowSpacing: 8
                    PlanFieldLabel { text: "Klasa"; hint: "jak trudny jest ticket" }
                    PlanSelect {
                        id: classPicker
                        objectName: "planManagerClass"
                        Layout.fillWidth: true
                        model: ["lekki", "standard", "mocny", "lokalny"]
                        enabled: manager.ticketFieldsEnabled
                        Accessible.name: "Klasa ticketu"
                    }
                    PlanFieldLabel { text: "Wykonawca"; hint: "model, który pisze kod" }
                    PlanSelect {
                        id: executorPicker
                        objectName: "planManagerExecutor"
                        Layout.fillWidth: true
                        model: Logic.transferSettingsExecutors(manager.modelsStatus)
                        textRole: "label"
                        valueRole: "value"
                        enabled: manager.ticketFieldsEnabled
                        Accessible.name: "Wykonawca ticketu"
                    }
                    PlanFieldLabel { text: "Recenzent"; hint: "inny model, który sprawdza zmiany" }
                    PlanSelect {
                        id: reviewerPicker
                        objectName: "planManagerReviewer"
                        // Wybrany recenzent przeżywa przeliczenie listy po zmianie wykonawcy (K-33: lista
                        // wracała do pierwszej pozycji i „Zapisz” po cichu zmieniał recenzenta).
                        property string wanted: ""
                        Layout.fillWidth: true
                        model: Logic.planReviewerExecutors(manager.modelsStatus, executorPicker.currentValue || "")
                        textRole: "label"
                        valueRole: "value"
                        enabled: manager.ticketFieldsEnabled
                        Accessible.name: "Recenzent ticketu"
                        onActivated: index => wanted = model[index].value
                        onModelChanged: Qt.callLater(() => {
                            reviewerPicker.currentIndex = manager.optionIndex(reviewerPicker.model, reviewerPicker.wanted)
                        })
                    }
                }
                QQC2.ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    QQC2.TextArea {
                        id: bodyEdit
                        objectName: "planManagerBody"
                        readOnly: !manager.ticketFieldsEnabled
                        wrapMode: TextEdit.Wrap
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        background: Rectangle { radius: 8; color: Tide.Tokens.backgroundPanel }
                    }
                }
                PillButton {
                    objectName: "planManagerSaveTicket"
                    text: "Zapisz przydział i kartkę"
                    visible: !manager.appending
                    enabled: !manager.busy && manager.canEdit && !!manager.selectedTicket
                             && manager.selectedTicket.editable !== false && executorPicker.currentIndex >= 0
                             && reviewerPicker.currentIndex >= 0
                    onClicked: manager.editRequested(manager.card.id, {action: "edit",
                        ticket: manager.selectedTicket.id || manager.selectedTicket.workId,
                        class: classPicker.currentText, executor: executorPicker.currentValue,
                        reviewer: reviewerPicker.currentValue, body: bodyEdit.text})
                }
                RowLayout {
                    visible: manager.appending
                    PillButton {
                        objectName: "planManagerSubmitAppend"
                        text: "Zapisz propozycję do review"
                        enabled: !manager.busy
                        onClicked: manager.submitAppend()
                    }
                    PillButton {
                        text: "Anuluj"
                        outline: true
                        onClicked: {
                            manager.appending = false
                            if (manager.rows.length) manager.selectTicket(manager.rows[0])
                        }
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            objectName: "planManagerError"
            visible: !!manager.appendError || !!manager.error || !!manager.notice
            text: manager.appendError || manager.error || manager.notice
            textFormat: Text.PlainText
            color: manager.error || manager.appendError ? Tide.Tokens.rose : Tide.Tokens.mint
            font.family: Tide.Theme.uiFamily
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            QQC2.TextField {
                id: feedback
                objectName: "planManagerFeedback"
                Layout.fillWidth: true
                visible: !!manager.plan && manager.plan.status === "pending" && !manager.appendRevision
                placeholderText: "Uwagi, jeśli plan wymaga poprawy"
            }
            PillButton {
                objectName: "planManagerDiscardAppend"
                visible: manager.appendRevision && manager.plan.status === "pending"
                text: "Odrzuć dopisanie"
                outline: true
                enabled: !manager.busy
                onClicked: manager.editRequested(manager.card.id, {action: "discard-revision"})
            }
            PillButton {
                objectName: "planManagerReturn"
                visible: feedback.visible
                text: "Odeślij do poprawy"
                outline: true
                enabled: !manager.busy && feedback.text.trim().length > 0
                onClicked: manager.editRequested(manager.card.id, {action: "return", feedback: feedback.text.trim()})
            }
            PillButton {
                objectName: "planManagerApprove"
                visible: !!manager.plan && manager.plan.status === "pending"
                text: manager.appendRevision ? "Zatwierdź różnicę i dopisz ticket" : "Zatwierdź plan i rozpocznij implementację"
                enabled: !manager.busy
                onClicked: manager.approveRequested(manager.card.id)
            }
        }
    }
}
