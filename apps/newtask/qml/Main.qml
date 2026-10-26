// Okno „Tide: nowe zadanie”. Dane i akcje daje obiekt backend z tide-new-task.py (D-Bus do
// tide-daemon /Tasks albo stały plik JSON przy --data). grabPath: zapis zrzutu okna i wyjście
// (podgląd); initialPrompt, initialError, noWorktree — stan formularza do podglądu.
import QtQuick
import QtQuick.Window
import "tide" as Tide

Window {
    id: win

    property var backend: null
    property string grabPath: ""
    property string initialPrompt: ""
    property string initialError: ""
    property bool noWorktree: false
    property string error: initialError

    function parsed(text, fallback) {
        try {
            const value = JSON.parse(text)
            return value === null || value === undefined ? fallback : value
        } catch (e) {
            return fallback
        }
    }

    width: 860
    height: 790
    minimumWidth: 700
    minimumHeight: 700
    visible: true
    title: "Nowe zadanie"
    color: Tide.Tokens.backgroundWindow

    NewTaskPage {
        id: page
        anchors.fill: parent
        repos: win.backend ? win.parsed(win.backend.reposJson, []) : []
        defaults: win.backend ? win.parsed(win.backend.defaultsJson, {}) : ({})
        suggestion: win.backend ? win.parsed(win.backend.suggestionJson, {}) : ({})
        images: win.backend ? win.parsed(win.backend.imagesJson, []) : []
        available: win.backend ? win.backend.available : false
        busy: win.backend ? win.backend.busy : false
        canCapture: win.backend ? win.backend.canCapture : false
        homeDir: win.backend ? win.backend.homeDir : ""
        shortcutText: win.backend ? win.backend.shortcutText : ""
        nowTs: win.backend && win.backend.previewNow > 0 ? win.backend.previewNow : Date.now() / 1000
        error: win.error
        pasteImage: function () { return win.backend ? win.backend.pasteImage() : false }
        onStartRequested: request => {
            win.error = ""
            win.backend.start(request)
        }
        onSuggestRequested: (repoPath, tool, prompt) => win.backend.suggest(repoPath, tool, prompt)
        onCaptureRequested: {
            // Okno chowa się na czas zaznaczania obszaru; pokazuje się po zakończeniu Spectacle.
            win.visible = false
            captureDelay.start()
        }
        onRemoveImageRequested: path => win.backend.removeImage(path)
        onFilesDropped: urls => win.backend.addFiles(urls)
        onCancelRequested: win.close()
        Component.onCompleted: {
            if (win.initialPrompt !== "") prompt = win.initialPrompt
            if (win.noWorktree) newWorktree = false
        }
    }

    Timer {
        id: captureDelay
        interval: 250
        onTriggered: win.backend.capture()
    }

    Connections {
        target: win.backend
        function onStartFinished(text) {
            const result = win.parsed(text, null)
            if (result && result.ok) {
                win.backend.finish()
                win.close()
                return
            }
            win.error = result ? (result.message || "Nie udało się uruchomić zadania") : "Brak odpowiedzi usługi tide-daemon"
        }
        function onCaptureFinished(problem) {
            win.visible = true
            win.raise()
            win.requestActivate()
            if (problem !== "") win.error = problem
        }
        function onImageProblem(problem) { win.error = problem }
        function onRaiseRequested() {
            win.show()
            win.raise()
            win.requestActivate()
        }
    }

    onClosing: if (win.backend) win.backend.discard()

    // Ctrl+Enter działa też poza polem polecenia; Ctrl+V z obrazem — gdy fokus nie jest w polu tekstowym.
    Shortcut { sequences: ["Ctrl+Return", "Ctrl+Enter"]; onActivated: page.start() }
    Shortcut { sequence: StandardKey.Paste; onActivated: if (win.backend) win.backend.pasteImage() }
    Shortcut { sequence: StandardKey.Find; onActivated: page.focusSearch() }
    Shortcut { sequence: "Escape"; onActivated: if (!page.busy) win.close() }

    Timer {
        interval: 900
        running: win.grabPath !== ""
        onTriggered: page.grabToImage(function (result) {
            result.saveToFile(win.grabPath)
            Qt.quit()
        })
    }
}
