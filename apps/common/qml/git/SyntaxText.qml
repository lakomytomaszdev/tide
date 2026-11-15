// Treść jednej linii kodu z podświetleniem składni (K-36, PT-351). Loader tworzy lekki, tylko do odczytu TextEdit
// wyłącznie wtedy, gdy linia ma język (`language`) i jest podświetlacz (`highlighter`); delegat ListView istnieje tylko
// dla widocznych linii, więc TextEdit też. Bez języka albo bez podświetlacza Loader jest pusty (`item === null`), a gospodarz
// zostawia zwykły `Text`.
//
// Umowa podświetlacza (`highlighter` to Component, wstrzykiwany jednym punktem przez DiffView): tworzony z właściwościami
// `textEdit` (TextEdit tej linii), `language` (nazwa definicji, np. „Python”) i `readable` (funkcja koloru: zwraca kolor
// czytelny na tłach linii diffu, inaczej tekst z palety). Prawdziwy: ModuleHighlighter.qml (moduł KSyntaxHighlighting).
// Podświetlacz powstaje raz, przy tworzeniu TextEdit: zmiana `highlighter` przy żywym delegacie nie tworzy nowej instancji
// (gospodarz ustala go przed danymi, `resolveHighlighter`).
import QtQuick
import "../tide" as Tide

Loader {
    id: root

    property string text: ""
    property string language: ""
    property Component highlighter: null
    property var readable: null
    readonly property var instance: item ? item.instance : null   // utworzony podświetlacz (do testów i diagnozy)

    active: language !== "" && highlighter !== null && text !== ""

    sourceComponent: Component {
        TextEdit {
            id: edit
            width: root.width
            readOnly: true
            enabled: false             // kliknięcia przechodzą do MouseArea linii (zaznaczanie wierszy diffa)
            selectByMouse: false
            activeFocusOnPress: false
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.WrapAnywhere
            text: root.text
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 13

            property var instance: null

            // Podświetlacz jest dzieckiem TextEdit: znika razem z nim, gdy delegat opuszcza widok.
            Component.onCompleted: instance = root.highlighter.createObject(edit, {
                textEdit: edit, language: root.language, readable: root.readable
            })
        }
    }
}
