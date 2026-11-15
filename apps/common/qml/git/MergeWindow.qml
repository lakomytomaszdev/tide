// Okno scalania (K-99, PT-390, #6): wspólny komponent obok widoku diffa, osadzany jako nakładka na całe okno gospodarza
// (Tablica dziś, Worktrees w K-99 #10). Daje strefę celu z wyborem (domyślny cel, skróty i wyszukiwanie lokalnych
// gałęzi przez wspólny BranchPicker), czytelny podgląd z odpowiedzi `PreviewCardMerge` w nazwanych sekcjach
// (MergeSection: blokady zdaniami, konflikty, kolizje z lokalną pracą, przygotowanie celu, commity, pliki, ostrzeżenia,
// zwinięte „Szczegóły” z SHA i kodami; długie listy przewijają się w sekcji) i rząd przycisków, który zawsze jest widoczny.
//
// Cel. `open(card, ref, branch)` z pustym `ref` nie zakłada celu: okno prosi o listę (`branchesRequested(text)`),
// a z odpowiedzi `ListCardBranches` (`receiveBranches(tag, odpowiedź)`) bierze wyłącznie `defaultTargetRef`, `mainRef`
// i `recentTargets` — żadnych nazw na sztywno. Domyślny cel wybieralny dostaje podgląd od razu. Przy bazie nieznanej
// (`baseRef` pusty) bez `defaultTargetRef` nazwana główna jest tylko propozycją: podgląd idzie, ale „Scal do”
// czeka na świadomy wybór celu. Zmiana celu zeruje podgląd i wysyła nowy; cel niewybieralny pokazuje powód i nie
// wysyła podglądu. Wybór celu nie jest zgodą i nie zmienia bazy karty.
//
// Kontrakt zgody. Komponent nie woła D-Bus i nie zna strony gospodarza: dane dostaje przez właściwości i funkcje,
// a gospodarz odbiera sygnały:
//  - `previewRequested(targetRef)` — gospodarz wysyła `PreviewCardMerge` z nowym tagiem i woła `track(tag)`;
//  - `branchesRequested(text)` — gospodarz wysyła `ListCardBranches` (purpose merge) z nowym tagiem i woła
//    `trackBranches(tag)`;
//  - `mergeRequested({targetRef, previewToken})` — jedyna zgoda: pola bieżącego podglądu, tylko po kliknięciu
//    „Scal do <cel>” przy `canMerge === true` i niepustym `previewToken`; gospodarz wysyła `MergeCard`
//    z nowym tagiem i woła `track(tag)`;
//  - `changesRequested(cardId)` — „Zmiany gałęzi” (widok diffa karty z K-36);
//  - `closed()` — okno zamknięte (Zamknij, Escape, `close()` przy zmianie kontekstu karty albo po scaleniu).
// Odpowiedź usługi gospodarz oddaje przez `receive(tag, błąd, odpowiedź)`; odpowiedź z innym tagiem niż bieżące
// żądanie (stare odświeżenie, okno zamknięte w międzyczasie) jest pomijana. Zamknięcie, Escape i „Odśwież” nigdy
// nie wysyłają zgody. Po błędzie scalenia przycisk scalenia jest zablokowany do nowego, udanego podglądu.
// Teksty z gita (nazwy gałęzi, ścieżki, powody) są zwykłym tekstem (Text.PlainText).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "start.js" as Start

FocusScope {
    id: win
    objectName: "mergeWindow"
    visible: opened

    // --- dane od gospodarza ---
    property bool opened: false
    property string cardId: ""
    property string baseRef: ""            // znana baza karty (pełny ref); pusta — baza nieznana, główna to tylko propozycja
    property string targetRef: ""          // pełny ref celu (podpowiedź nad nazwą); pusty — cel jeszcze nie wybrany
    property string targetBranch: ""       // krótka nazwa celu; po podglądzie z odpowiedzi usługi
    property string startLine: ""          // „Start od: …” karty (git/start.js, cardStartLine); pusta — bez linii
    property string contextKey: ""        // klucz kontekstu karty gospodarza: jego zmiana zamyka okno (robi to gospodarz)
    property bool changesAvailable: true   // „Zmiany gałęzi” (gospodarz bez widoku diffa może ukryć)

    // --- stan żądania i podglądu ---
    property string requestTag: ""         // tag bieżącego żądania (od gospodarza przez `track`)
    property string pending: ""            // "preview" | "merge" | "" — na co czeka okno
    readonly property bool busy: pending !== ""
    property string error: ""
    property var result: null              // ostatnia udana odpowiedź `PreviewCardMerge`
    property bool mergeLocked: false       // po błędzie scalenia: do nowego udanego podglądu
    property bool detailsOpen: false
    property bool proposal: false          // cel jest tylko propozycją (baza nieznana): „Scal do” czeka na świadomy wybór
    property string targetNote: ""         // powód, dla którego domyślny cel nie dostał podglądu
    property bool targetOpen: false        // strefa wyboru celu rozwinięta przyciskiem „Zmień cel”
    readonly property bool pickerShown: targetOpen || targetRef === "" || proposal
    property alias picker: picker
    readonly property bool canConfirm: opened && targetRef !== "" && !proposal && !busy && !mergeLocked && !!result
                                       && result.canMerge === true
                                       && typeof result.previewToken === "string" && result.previewToken !== ""
    readonly property string targetName: targetBranch || Start.shortRef(targetRef)

    signal previewRequested(string targetRef)
    signal branchesRequested(string text)
    signal mergeRequested(var request)
    signal changesRequested(string cardId)
    signal closed()

    // Otwiera okno dla karty i prosi o listę celów. Z `ref` od razu prosi o podgląd tego celu; z pustym `ref` cel
    // wybiera się z odpowiedzi `ListCardBranches` (domyślny cel) albo ręcznie.
    function open(card, ref, branch) {
        const wasOpen = opened
        cardId = card
        targetRef = ref || ""
        targetBranch = ref ? branch || "" : ""
        proposal = false
        targetNote = ""
        targetOpen = false
        result = null
        error = ""
        mergeLocked = false
        detailsOpen = false
        requestTag = ""
        pending = ""
        opened = true
        if (wasOpen) picker.reset()   // zamknięte okno ma nieaktywny wybierak: odczyt listy zaczyna sam `active`
        forceActiveFocus()
        // Okno otwarte z wyskakującego wybieraka celu: zamykany Popup oddaje fokus poprzedniemu elementowi — odbierz go.
        Qt.callLater(() => { if (win.opened) win.forceActiveFocus() })
        if (targetRef !== "") refresh()
    }

    // Gospodarz przypina tag wysłanego `ListCardBranches`.
    function trackBranches(tag) {
        picker.pendingTag = tag
    }

    // Odpowiedź `ListCardBranches`. Bez celu okno bierze z niej domyślny cel albo (baza nieznana) propozycję głównej.
    function receiveBranches(tag, reply) {
        if (!opened) return
        const mine = tag !== "" && tag === picker.pendingTag
        picker.receive(tag, reply)
        if (!mine || targetRef !== "" || !reply || reply.ok !== true) return
        const known = (reply.branches || []).concat(reply.recentTargets || [])
        const byDefault = typeof reply.defaultTargetRef === "string" && reply.defaultTargetRef !== ""
        const ref = byDefault ? reply.defaultTargetRef
                  : baseRef === "" && typeof reply.mainRef === "string" ? reply.mainRef : ""
        if (ref === "") return
        const entry = known.find(b => b.ref === ref)
        if (entry && entry.selectable !== false) {
            targetRef = entry.ref
            targetBranch = entry.name || ""
            proposal = !byDefault
            targetNote = ""
            refresh()
        } else {
            targetNote = "Cel " + ref + " jest niedostępny" + (entry && entry.reason ? ": " + entry.reason : ".")
        }
    }

    // Świadomy wybór celu (skrót albo wynik wyszukiwania): nowy podgląd, a „Scal do” czeka na jego odpowiedź.
    function chooseTarget(entry) {
        if (!opened || !entry || typeof entry.ref !== "string" || entry.ref === "" || pending === "merge") return
        if (entry.selectable === false) {
            targetNote = "Cel " + entry.ref + " jest niedostępny" + (entry.reason ? ": " + entry.reason : ".")
            return
        }
        targetNote = ""
        targetOpen = true   // strefa wyboru zostaje otwarta (nazwa nowego celu, kolejna zmiana); zwija ją „Zmień cel”
        if (entry.ref === targetRef && proposal) {
            // Ta sama propozycja przyjęta świadomie: bieżący podgląd (albo ten w drodze) zostaje.
            proposal = false
            return
        }
        targetRef = entry.ref
        targetBranch = entry.name || ""
        proposal = false
        refresh()
    }

    // Nowy podgląd. Nie w trakcie scalenia: tag scalenia musi zostać, żeby odpowiedź `MergeCard` dotarła.
    function refresh() {
        if (!opened || pending === "merge" || targetRef === "") return
        result = null
        error = ""
        requestTag = ""
        pending = "preview"
        previewRequested(targetRef)
    }

    // Jedyna droga do zgody: klik w „Scal do <cel>” z polami bieżącego podglądu.
    function confirm() {
        if (!canConfirm) return
        const r = result
        const request = { targetRef: typeof r.targetRef === "string" && r.targetRef !== "" ? r.targetRef : targetRef,
                          previewToken: r.previewToken }
        requestTag = ""
        pending = "merge"
        mergeRequested(request)
    }

    // Gospodarz przypina tag żądania, które właśnie wysłał.
    function track(tag) {
        if (pending !== "") requestTag = tag
    }

    // Odpowiedź usługi; zwraca komunikat sukcesu scalenia („Scalono K-12 do <cel>”) albo "".
    function receive(tag, errorText, reply) {
        if (!opened || !tag || tag !== requestTag) return ""
        const kind = pending
        pending = ""
        requestTag = ""
        if (kind === "preview") {
            error = errorText || ""
            result = errorText ? null : reply
            if (!errorText) {
                mergeLocked = false
                if (reply && typeof reply.targetBranch === "string" && reply.targetBranch !== "")
                    targetBranch = reply.targetBranch
            }
            return ""
        }
        if (kind !== "merge") return ""
        if (errorText) {
            // Ponowna zgoda wymaga nowego odczytu; stary podgląd nie może ponowić mutacji.
            error = errorText
            result = null
            mergeLocked = true
            return ""
        }
        const message = "Scalono " + cardId + " do "
                        + (reply && typeof reply.targetBranch === "string" && reply.targetBranch !== ""
                           ? reply.targetBranch : targetName)
        close()
        return message
    }

    function close() {
        if (!opened) return
        opened = false
        pending = ""
        requestTag = ""
        result = null
        error = ""
        mergeLocked = false
        detailsOpen = false
        proposal = false
        targetNote = ""
        targetOpen = false
        closed()
    }

    // --- treść podglądu (czyste funkcje, testowane sondą) ---

    function stateText() {
        if (pending === "preview") return "Przygotowuję podgląd…"
        if (pending === "merge") return "Scalam…"
        const r = result
        if (!r) return error ? (mergeLocked ? "Scalenie nie powiodło się" : "Podgląd nieudany")
                             : targetRef === "" ? "Wybierz cel scalenia" : ""
        if (r.canMerge === true) return "Można scalić"
        const conflicts = conflictPaths(r).length || Number(r.conflictCount) || 0
        if (conflicts > 0) return conflicts === 1 ? "Konflikt w 1 pliku" : "Konflikty w " + conflicts + " plikach"
        const first = blockedLines(r)[0]
        return first ? "Scalenie zablokowane: " + first : "Scalenie zablokowane"
    }

    // Znane kody `blockedReasons`/`reason` z `PreviewCardMerge` (docs/tide-daemon.md) jako zdania po polsku. Test
    // porównuje tę listę z kodami w usłudze, więc nowy kod bez zdania zatrzyma testy.
    readonly property var reasonSentences: ({
        "conflict": "Zmiany karty kolidują z celem — rozwiąż konflikty w plikach z listy.",
        "unrelated-history": "Karta i cel nie mają wspólnej historii, więc nie da się ich scalić.",
        "git-capability": "Zainstalowany git nie obsługuje podglądu scalenia (merge-tree).",
        "git-error": "Git nie zdołał przygotować podglądu — spróbuj odświeżyć albo sprawdź repozytorium.",
        "no-sandbox": "Piaskownica gita jest niedostępna, więc podglądu nie da się bezpiecznie przygotować.",
        "state-changed": "Stan repozytorium zmienił się podczas odczytu — kliknij „Odśwież”.",
        "base-decision": "Najpierw rozstrzygnij pytanie o bazę karty.",
        "not-closed": "Karta nie ma aktualnego domknięcia.",
        "active-step": "Trwa krok karty — poczekaj na jego koniec.",
        "plan-change": "Zmiana planu czeka na zakończenie lub decyzję.",
        "base-change": "Po korekcie bazy karta wymaga nowego domknięcia.",
        "pending-action": "Trwa akcja karty, korekta bazy albo scalanie ticketu — poczekaj na jej koniec.",
        "card-target": "Celem nie może być gałąź tej karty.",
        "protected-target": "Ten cel należy do innej karty albo jej czynnego kroku — wybierz inny.",
        "no-target-worktree": "Zarejestrowany checkout celu jest niedostępny.",
        "target-path-occupied": "Katalog planowanego worktree celu jest zajęty — wybierz inny cel.",
        "target-name-occupied": "Nazwa nowego celu koliduje z istniejącą gałęzią — wybierz inną nazwę albo lokalny cel.",
        "source-dirty": "Checkout gałęzi karty ma niezapisane zmiany albo nie istnieje.",
        "git-operation": "W checkoucie trwa operacja gita albo indeks jest zablokowany.",
        "local-collision": "Lokalna praca w celu koliduje ze ścieżkami scalenia.",
        "local-unassessed": "Nie udało się potwierdzić stanu checkoutów — odśwież podgląd."
    })
    readonly property string unknownReasonSentence: "Scalenie jest zablokowane z powodu, którego to okno nie zna — szczegóły (kod) są niżej."

    function reasonSentence(code) {
        const known = typeof code === "string" && reasonSentences.hasOwnProperty(code)
        return known ? reasonSentences[code] : unknownReasonSentence
    }

    // Zdania blokad: z `blockedReasons`, a bez nich z pojedynczego `reason` (odmowa odczytu). Bez powtórzeń.
    function reasonCodes(r) {
        const codes = []
        const add = code => { if (typeof code === "string" && code !== "" && codes.indexOf(code) < 0) codes.push(code) }
        for (const b of (r && Array.isArray(r.blockedReasons) ? r.blockedReasons : [])) if (b) add(b.reason)
        if (r && r.canMerge !== true) add(r.reason)
        return codes
    }

    function blockedLines(r) {
        const out = []
        if (!r || r.canMerge === true) return out
        for (const code of reasonCodes(r)) {
            const sentence = reasonSentence(code)
            if (out.indexOf(sentence) < 0) out.push(sentence)
        }
        return out
    }

    function conflictPaths(r) {
        return r && Array.isArray(r.conflicts) ? r.conflicts.map(p => plain(p)) : []
    }

    function warningLines(r) {
        return r && Array.isArray(r.warnings) ? r.warnings.map(w => String(w)) : []
    }

    // Ścieżki z gita mogą zawierać tabulator i nowy wiersz: pokazujemy je jako \t i \n, żeby jedna ścieżka była jednym wierszem.
    function plain(text) {
        return String(text === undefined || text === null ? "" : text).replace(/\\/g, "\\\\")
            .replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t")
    }

    function countOf(count, list) {
        return typeof count === "number" && count >= list.length ? count : list.length
    }

    function commitRows(r) {
        return r && Array.isArray(r.commits) ? r.commits.map(c => plain(String(c.sha || "").slice(0, 7)) + "  " + plain(c.subject)) : []
    }

    function filePath(f) {
        return f.oldPath && f.newPath && f.oldPath !== f.newPath
            ? plain(f.oldPath) + " → " + plain(f.newPath) : plain(f.newPath || f.oldPath)
    }

    function fileRows(r) {
        return r && Array.isArray(r.files) ? r.files.map(f => plain(f.status) + "  " + filePath(f)) : []
    }

    // „Kolizje z lokalną pracą”: nagłówek grupy (staged / unstaged / untracked), pod nim wpisy ze ścieżkami scalenia.
    function collisionRows(r) {
        const out = []
        const groups = r && r.localCollisions && typeof r.localCollisions === "object" ? r.localCollisions : null
        if (!groups) return out
        const labels = { staged: "Staged (w indeksie)", unstaged: "Unstaged (poza indeksem)", untracked: "Untracked (nieśledzone)" }
        for (const kind of ["staged", "unstaged", "untracked"]) {
            const entries = Array.isArray(groups[kind]) ? groups[kind] : []
            if (entries.length === 0) continue
            out.push(labels[kind] + " (" + entries.length + "):")
            for (const e of entries)
                out.push("   " + plain(e.status) + "  " + (e.paths || []).map(plain).join(" ← ")
                         + ((e.mergePaths || []).length ? " · scalenie: " + e.mergePaths.map(plain).join(", ") : ""))
        }
        return out
    }

    function collisionCount(r) {
        const groups = r && r.localCollisions && typeof r.localCollisions === "object" ? r.localCollisions : null
        if (!groups) return 0
        let n = 0
        for (const kind of ["staged", "unstaged", "untracked"]) n += Array.isArray(groups[kind]) ? groups[kind].length : 0
        return n
    }

    // „Po zgodzie Tide przygotuje”: nowy ref i nowy worktree dla celu bez lokalnej gałęzi albo bez checkoutu.
    function preparationRows(r) {
        const out = []
        const p = r && r.targetPreparation && typeof r.targetPreparation === "object" ? r.targetPreparation : null
        if (!p) return out
        if (p.createRef) out.push("Nowa gałąź " + plain(r.targetRef || targetRef) + (p.remoteRef ? " od " + plain(p.remoteRef) : ""))
        if (p.createWorktree) out.push("Nowy worktree " + plain(p.path))
        return out
    }

    // „Szczegóły” (domyślnie zwinięte): SHA źródła i celu, drzewo wyniku, worktree celu i kody blokad.
    function detailsText(r) {
        if (!r) return ""
        const lines = ["Źródło: " + plain(r.sourceRef), "SHA źródła: " + (r.sourceCommit || ""),
                       "Cel: " + plain(r.targetRef || targetRef), "SHA celu: " + (r.targetCommit || ""),
                       "Drzewo wyniku: " + (r.resultTree || "nieprzygotowane"),
                       "Worktree celu: " + plain(r.targetWorktree) + (r.targetWorktreeExists === false
                                                                      ? " (jeszcze nie istnieje)" : "")]
        if (r.reason) lines.push("Kod: " + r.reason)
        for (const b of r.blockedReasons || [])
            lines.push("Blokada: [" + (b && b.reason) + "]" + (b && b.message ? " " + plain(b.message) : ""))
        if (r.canMerge !== true && r.message && !(r.blockedReasons || []).length) lines.push("Komunikat usługi: " + plain(r.message))
        lines.push(r.localCollisions && typeof r.localCollisions === "object"
                   ? "Lokalne kolizje: " + collisionCount(r) + ". " + (r.canMerge === true
                     ? "Odczyt nie wykazał blokad scalenia." : "Scalenie jest zablokowane.")
                   : "Gotowość do wykonania nie została potwierdzona. Lokalne kolizje: nieocenione.")
        if (r.targetPreparation) {
            const p = r.targetPreparation
            lines.push("", "Commit startu celu: " + (p.startCommit || ""))
            if (p.createRef) lines.push("Po zgodzie: utwórz " + plain(r.targetRef || targetRef) + " od " + plain(p.remoteRef),
                                        "SHA remote: " + (p.remoteCommit || ""))
            if (p.createWorktree) lines.push("Po zgodzie: utwórz własny worktree " + plain(p.path))
        }
        return lines.join("\n")
    }

    Keys.onEscapePressed: event => {
        win.close()
        event.accepted = true
    }

    // Tło przykrywa gospodarza: kliknięcia i kółko nie przechodzą pod spód (nie zamyka — tylko jawne „Zamknij”/Esc).
    Rectangle {
        anchors.fill: parent
        color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.72)
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onClicked: mouse => mouse.accepted = true
            onWheel: wheel => wheel.accepted = true
        }
    }

    Rectangle {
        id: frame
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 1000)
        height: Math.min(parent.height - 40, 760)
        color: Tide.Tokens.backgroundPanel
        radius: Tide.Tokens.radiusCard
        border.color: Tide.Tokens.textMuted

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 12

            // --- strefa celu: bieżący cel, powód/propozycja i wybór (skróty + wyszukiwanie) ---
            Text {
                objectName: "mergeWindowTarget"
                Layout.fillWidth: true
                text: win.cardId + " → " + (win.targetRef !== "" ? win.targetName : "wybierz cel")
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt + 2
                font.weight: Font.DemiBold
                wrapMode: Text.WrapAnywhere
                HoverHandler { id: targetHover }
                QQC2.ToolTip.visible: targetHover.hovered && win.targetRef !== ""
                QQC2.ToolTip.text: win.targetRef
                QQC2.ToolTip.delay: 400
            }
            Text {
                objectName: "mergeWindowStart"
                Layout.fillWidth: true
                visible: win.startLine !== ""
                text: win.startLine
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.WrapAnywhere
            }
            Text {
                objectName: "mergeWindowProposal"
                Layout.fillWidth: true
                visible: win.proposal
                text: "To tylko propozycja celu: baza karty jest nieznana. Wybierz cel świadomie (kliknij go na liście "
                      + "albo wskaż inny) — dopiero wtedy „Scal do” się odblokuje."
                textFormat: Text.PlainText
                color: Tide.Tokens.butter
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "mergeWindowTargetNote"
                Layout.fillWidth: true
                visible: win.targetNote !== ""
                text: win.targetNote
                textFormat: Text.PlainText
                color: Tide.Tokens.negative
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.WrapAnywhere
            }
            Tide.TextButton {
                objectName: "mergeWindowTargetToggle"
                visible: !(win.targetRef === "" || win.proposal)
                text: (win.targetOpen ? "▾ " : "▸ ") + "Zmień cel"
                onClicked: win.targetOpen = !win.targetOpen
            }
            QQC2.ScrollView {
                id: pickerScroll
                objectName: "mergeWindowPickerScroll"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(picker.implicitHeight, Math.max(240, frame.height * 0.5))
                visible: win.pickerShown
                clip: true
                contentWidth: availableWidth

                BranchPicker {
                    id: picker
                    objectName: "mergeWindowBranchPicker"
                    width: pickerScroll.availableWidth
                    active: win.opened
                    purpose: "merge"
                    baseRef: win.baseRef
                    contextKey: win.contextKey
                    onRequested: text => win.branchesRequested(text)
                    onShortcutChosen: target => win.chooseTarget(target)
                    onChosen: target => win.chooseTarget(target)
                }
            }
            Tide.TextButton {
                objectName: "mergeWindowNewTargetPreview"
                visible: win.pickerShown && picker.createTarget !== null
                text: "Podgląd nowego celu"
                enabled: !!picker.selected && picker.selected.kind === "create" && !picker.loading
                onClicked: win.chooseTarget(picker.selected)
            }

            // --- minimalny podgląd (przewijany) ---
            QQC2.ScrollView {
                id: scroll
                objectName: "mergeWindowScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: scroll.availableWidth
                    spacing: 10

                    Text {
                        objectName: "mergeWindowState"
                        Layout.fillWidth: true
                        text: win.stateText()
                        textFormat: Text.PlainText
                        color: win.result && win.result.canMerge === true ? Tide.Tokens.positive
                             : win.result || win.error ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt + 1
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "mergeWindowError"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: win.busy ? "" : win.error
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.WrapAnywhere
                    }
                    Text {
                        objectName: "mergeWindowLockHint"
                        Layout.fillWidth: true
                        visible: win.mergeLocked && !win.busy
                        text: "Przycisk scalenia wróci po nowym podglądzie — kliknij „Odśwież”."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    MergeSection {
                        objectName: "mergeWindowBlocked"
                        Layout.fillWidth: true
                        title: "Dlaczego nie można scalić"
                        rows: win.blockedLines(win.result)
                        maxListHeight: 200
                    }
                    MergeSection {
                        objectName: "mergeWindowConflicts"
                        Layout.fillWidth: true
                        readonly property var paths: win.conflictPaths(win.result)
                        title: "Konflikty (" + win.countOf(win.result ? win.result.conflictCount : undefined, paths) + ")"
                        rows: paths
                        rowColor: Tide.Tokens.negative
                    }
                    MergeSection {
                        objectName: "mergeWindowCollisions"
                        Layout.fillWidth: true
                        title: "Kolizje z lokalną pracą (" + win.collisionCount(win.result) + ")"
                        rows: win.collisionRows(win.result)
                    }
                    MergeSection {
                        objectName: "mergeWindowPreparation"
                        Layout.fillWidth: true
                        title: "Po zgodzie Tide przygotuje"
                        rows: win.preparationRows(win.result)
                    }
                    MergeSection {
                        objectName: "mergeWindowCommits"
                        Layout.fillWidth: true
                        readonly property var items: win.commitRows(win.result)
                        title: "Wnoszone commity (" + win.countOf(win.result ? win.result.commitCount : undefined, items) + ")"
                        rows: items
                    }
                    MergeSection {
                        objectName: "mergeWindowFiles"
                        Layout.fillWidth: true
                        readonly property var items: win.fileRows(win.result)
                        title: "Pliki (" + win.countOf(win.result ? win.result.fileCount : undefined, items) + ")"
                        rows: items
                    }
                    MergeSection {
                        objectName: "mergeWindowWarnings"
                        Layout.fillWidth: true
                        title: "Ostrzeżenia"
                        titleColor: Tide.Tokens.butter
                        rowColor: Tide.Tokens.butter
                        rows: win.warningLines(win.result)
                        maxListHeight: 120
                    }
                    Text {
                        objectName: "mergeWindowConsent"
                        Layout.fillWidth: true
                        visible: !!win.result
                        text: "Sam podgląd nie jest zgodą. Nie stanowi zgody zamknięcie, Escape ani odświeżenie. "
                              + "Dopiero przycisk „Scal do " + win.targetName + "” potwierdza ten podgląd."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                        wrapMode: Text.WrapAnywhere
                    }
                    Tide.TextButton {
                        objectName: "mergeWindowDetailsToggle"
                        visible: !!win.result
                        text: (win.detailsOpen ? "▾ " : "▸ ") + "Szczegóły"
                        onClicked: win.detailsOpen = !win.detailsOpen
                    }
                    TextEdit {
                        objectName: "mergeWindowDetails"
                        Layout.fillWidth: true
                        visible: win.detailsOpen && !!win.result
                        readOnly: true
                        selectByMouse: true
                        textFormat: TextEdit.PlainText
                        text: win.detailsText(win.result)
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                        wrapMode: TextEdit.WrapAnywhere
                    }
                }
            }

            // --- rząd przycisków (zawsze widoczny, poza przewijaniem) ---
            Tide.TextButton {
                objectName: "mergeWindowMerge"
                Layout.fillWidth: true
                variant: "filled"
                wrapText: true
                text: win.targetRef !== "" ? "Scal do " + win.targetName : "Scal do… (wybierz cel)"
                enabled: win.canConfirm
                opacity: enabled ? 1 : 0.5
                onClicked: win.confirm()
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Tide.TextButton {
                    objectName: "mergeWindowChanges"
                    visible: win.changesAvailable
                    text: "Zmiany gałęzi"
                    onClicked: win.changesRequested(win.cardId)
                }
                Tide.TextButton {
                    objectName: "mergeWindowRefresh"
                    text: "Odśwież"
                    enabled: win.pending !== "merge"
                    onClicked: win.refresh()
                }
                Item { Layout.fillWidth: true }
                Tide.TextButton {
                    objectName: "mergeWindowClose"
                    text: "Zamknij"
                    onClicked: win.close()
                }
            }
        }
    }
}
