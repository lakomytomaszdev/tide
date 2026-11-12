// Prawdziwy podświetlacz (K-36, PT-351): moduł QML KSyntaxHighlighting nad QTextDocument linii. Plik jest ładowany
// dynamicznie przez DiffView (`Qt.createComponent`); gdy modułu nie ma, import się nie powiedzie, komponent nie
// powstanie, a widok zostaje przy zwykłym tekście. Nie wolno go importować statycznie.
//
// Kolory pochodzą z ciemnego motywu modułu (`Repository.defaultTheme(DarkTheme)`), nie z kodu. `readable` jest częścią
// umowy podświetlacza (SyntaxText.qml), ale moduł nie pozwala podmienić koloru pojedynczego formatu.
import QtQuick
import org.kde.syntaxhighlighting

QtObject {
    id: root

    property var textEdit: null
    property string language: ""
    property var readable: null

    property SyntaxHighlighter engine: SyntaxHighlighter {
        textEdit: root.textEdit
        definition: Repository.definitionForName(root.language)
        theme: Repository.defaultTheme(Repository.DarkTheme)
    }
}
