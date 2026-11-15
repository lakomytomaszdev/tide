// Prawdziwy podświetlacz (K-36, PT-351): moduł QML KSyntaxHighlighting nad QTextDocument linii. Plik jest ładowany
// dynamicznie przez DiffView (`Qt.createComponent`); gdy modułu nie ma, import się nie powiedzie, komponent nie
// powstanie, a widok zostaje przy zwykłym tekście. Nie wolno go importować statycznie.
//
// Kolory pochodzą z motywu „Tide” generowanego z palety (theme/syntax/generate.py, instalowanego przez make install do
// $XDG_DATA_HOME/org.kde.syntax-highlighting/themes/; każdy kolor ma 4,5:1 na tłach linii diffu, style bez tła formatu).
// Gdy motywu nie zainstalowano, zostaje ciemny motyw modułu (`defaultTheme(DarkTheme)`), nie z kodu. `readable` jest
// częścią umowy podświetlacza (SyntaxText.qml), ale moduł nie pozwala podmienić koloru pojedynczego formatu — dlatego
// kontrast zapewnia motyw, nie ta funkcja.
import QtQuick
import org.kde.syntaxhighlighting

QtObject {
    id: root

    property var textEdit: null
    property string language: ""
    property var readable: null
    readonly property string themeName: "Tide"

    property SyntaxHighlighter engine: SyntaxHighlighter {
        textEdit: root.textEdit
        definition: Repository.definitionForName(root.language)
        theme: {
            const own = Repository.theme(root.themeName)
            return own.name === root.themeName ? own : Repository.defaultTheme(Repository.DarkTheme)
        }
    }
}
