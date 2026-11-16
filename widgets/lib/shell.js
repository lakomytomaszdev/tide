// Kolorowanie skryptu powłoki dla Tide.CodeBlock (K-128, PT-506): prosty tokenizer linii, bez wykonywania czegokolwiek.
//
// `tokens(line)` dzieli jedną linię na kawałki `{kind, text}`: "comment" (`#` na początku słowa do końca linii),
// "keyword" (słowo kluczowe powłoki w miejscu polecenia: if, then, for, export, echo…), "string" ('…' bez rozwijania
// i "…" — zmienne w środku osobno), "variable" ($X, ${X}, $1, $@), "command" (pierwsze słowo polecenia: na początku
// linii, po `;`, `|`, `&`, `(`, `$(`, `{` i po słowie kluczowym), "text" (reszta). Złączone kawałki dają linię znak
// w znak. Niedomknięty napis ciągnie się do końca linii (skrypt jest kolorowany linia po linii).
//
// Granica bezpieczeństwa: treść skryptu to niezaufany tekst. `html()` escapuje KAŻDY kawałek (&, <, >, ", ')
// i dopiero wtedy owija go w <span> ze stałym stylem z palety; tekst nigdy nie trafia do atrybutu. Bez linków
// i obrazów. Kolory: `colors(Tokens)` — paleta, nic wpisanego na sztywno.
.pragma library

var ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }

function escape(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (ch) {
        return ESCAPES[ch]
    })
}

var KEYWORDS = {
    "if": 1, "then": 1, "else": 1, "elif": 1, "fi": 1, "for": 1, "while": 1, "until": 1, "do": 1, "done": 1,
    "case": 1, "esac": 1, "in": 1, "function": 1, "select": 1, "time": 1, "return": 1, "exit": 1, "export": 1,
    "local": 1, "readonly": 1, "declare": 1, "set": 1, "unset": 1, "shift": 1, "source": 1, "echo": 1,
    "printf": 1, "cd": 1, "trap": 1, "exec": 1, "eval": 1, "true": 1, "false": 1, "break": 1, "continue": 1
}
// Po tych słowach kluczowych następne słowo znowu jest poleceniem (np. `then docker …`, `! grep …`).
var OPENS_COMMAND = { "then": 1, "else": 1, "do": 1, "if": 1, "elif": 1, "while": 1, "until": 1, "time": 1,
                      "exec": 1, "!": 1 }
var SEPARATORS = ";|&(){}`"

function isSpace(ch) { return ch === " " || ch === "\t" }

// Długość zmiennej od `$` na pozycji `at` (0 — sam znak `$`, np. `$(`).
function variableLength(line, at) {
    var next = line.charAt(at + 1)
    if (next === "{") {
        var end = line.indexOf("}", at + 2)
        return end < 0 ? line.length - at : end - at + 1
    }
    if (/[A-Za-z_]/.test(next)) {
        var m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(line.slice(at + 1))
        return 1 + m[0].length
    }
    if (/[0-9@#?$!*-]/.test(next)) return 2
    return 0
}

function push(out, kind, text) {
    if (text === "") return
    var last = out.length ? out[out.length - 1] : null
    if (last && last.kind === kind && kind !== "comment") last.text += text
    else out.push({ kind: kind, text: text })
}

// Napis "…" od pozycji `at`: zwraca indeks za cudzysłowem zamykającym (albo koniec linii); zmienne osobno.
function doubleQuoted(line, at, out) {
    var i = at + 1
    var chunk = "\""
    while (i < line.length) {
        var ch = line.charAt(i)
        if (ch === "\\" && i + 1 < line.length) {
            chunk += line.substr(i, 2)
            i += 2
            continue
        }
        if (ch === "$") {
            var n = variableLength(line, i)
            if (n > 0) {
                push(out, "string", chunk)
                chunk = ""
                push(out, "variable", line.substr(i, n))
                i += n
                continue
            }
        }
        chunk += ch
        i += 1
        if (ch === "\"") break
    }
    push(out, "string", chunk)
    return i
}

function tokens(line) {
    var out = []
    var source = String(line === undefined || line === null ? "" : line)
    var i = 0
    var commandPosition = true
    while (i < source.length) {
        var ch = source.charAt(i)
        if (isSpace(ch)) {
            var s = i
            while (i < source.length && isSpace(source.charAt(i))) i++
            push(out, "text", source.slice(s, i))
            continue
        }
        if (ch === "#") {
            push(out, "comment", source.slice(i))
            break
        }
        if (ch === "'") {
            var close = source.indexOf("'", i + 1)
            var end = close < 0 ? source.length : close + 1
            push(out, "string", source.slice(i, end))
            i = end
            commandPosition = false
            continue
        }
        if (ch === "\"") {
            i = doubleQuoted(source, i, out)
            commandPosition = false
            continue
        }
        if (ch === "$") {
            if (source.charAt(i + 1) === "(") {
                push(out, "text", "$(")
                i += 2
                commandPosition = true
                continue
            }
            var n = variableLength(source, i)
            if (n > 0) {
                push(out, "variable", source.substr(i, n))
                i += n
                commandPosition = false
                continue
            }
        }
        if (ch === "&" && i > 0 && (source.charAt(i - 1) === ">" || source.charAt(i - 1) === "<")) {
            push(out, "text", ch)              // przekierowanie `2>&1`, `<&3` — nie separator polecenia
            i += 1
            continue
        }
        if (SEPARATORS.indexOf(ch) >= 0) {
            push(out, "text", ch)
            i += 1
            commandPosition = ch !== ")" && ch !== "}"
            continue
        }
        // Słowo: do spacji, separatora, cudzysłowu albo `$`; `\x` należy do słowa.
        var start = i
        while (i < source.length) {
            var c = source.charAt(i)
            if (c === "\\" && i + 1 < source.length) { i += 2; continue }
            if (isSpace(c) || SEPARATORS.indexOf(c) >= 0 || c === "'" || c === "\"" || c === "$") break
            i += 1
        }
        var word = source.slice(start, i)
        if (commandPosition && /^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) {
            push(out, "text", word)            // przypisanie przed poleceniem: `X=1 make test`
        } else if (commandPosition && KEYWORDS[word]) {
            push(out, "keyword", word)
            commandPosition = !!OPENS_COMMAND[word]
        } else if (commandPosition && word === "!") {
            push(out, "text", word)
        } else if (commandPosition) {
            push(out, "command", word)
            commandPosition = false
        } else {
            push(out, "text", word)
        }
    }
    return out
}

// --- kolory -----------------------------------------------------------------------------------------------------

function hex(color) {
    if (color && typeof color === "object" && color.r !== undefined) return { r: color.r, g: color.g, b: color.b }
    var digits = String(color || "").replace("#", "")
    if (digits.length === 8) digits = digits.slice(2)
    return { r: parseInt(digits.slice(0, 2), 16) / 255, g: parseInt(digits.slice(2, 4), 16) / 255,
             b: parseInt(digits.slice(4, 6), 16) / 255 }
}

function css(c) {
    function two(v) {
        var n = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16)
        return n.length < 2 ? "0" + n : n
    }
    return "#" + two(c.r) + two(c.g) + two(c.b)
}

function mix(top, bottom, amount) {
    var a = hex(top), b = hex(bottom)
    return css({ r: a.r * amount + b.r * (1 - amount), g: a.g * amount + b.g * (1 - amount),
                 b: a.b * amount + b.b * (1 - amount) })
}

// Krycie nagłówka bloku (makieta S-9 `.code .head` — tło `hover`, tekst ~0,04 na tle widoku).
var HEAD_TINT = 0.04

// Kolory bloku kodu (makieta S-9: .t-c, .t-k, .t-s, .t-v, .t-f) na tle `background.view`, jako pełne "#rrggbb".
function colors(tokens) {
    var bg = tokens.backgroundView
    return {
        background: css(hex(bg)), head: mix(tokens.textPrimary, bg, HEAD_TINT),
        text: css(hex(tokens.textPrimary)), number: css(hex(tokens.textMuted)), muted: css(hex(tokens.textMuted)),
        comment: css(hex(tokens.textMuted)), keyword: css(hex(tokens.lavender)), string: css(hex(tokens.butter)),
        variable: css(hex(tokens.sky)), command: css(hex(tokens.teal))
    }
}

// Pary (tekst, tło) do testu kontrastu: każdy rodzaj kawałka na tle bloku, numery linii i nagłówek.
function pairs(c) {
    var out = []
    var kinds = ["text", "comment", "keyword", "string", "variable", "command", "number"]
    for (var i = 0; i < kinds.length; i++) out.push({ name: kinds[i], fg: c[kinds[i]], bg: c.background })
    out.push({ name: "head", fg: c.text, bg: c.head })
    out.push({ name: "headMuted", fg: c.muted, bg: c.head })
    return out
}

function spanStyle(c, kind) {
    var color = c[kind] || c.text
    return "color:" + color + (kind === "comment" ? ";font-style:italic" : "")
}

// HTML jednej linii: każdy kawałek escapowany, kolor ze stałego stylu (nigdy z treści).
function html(line, c) {
    var parts = tokens(line)
    var out = ""
    for (var i = 0; i < parts.length; i++) {
        out += "<span style=\"" + escape(spanStyle(c, parts[i].kind)) + "\">" + escape(parts[i].text) + "</span>"
    }
    return out
}

// Linie tekstu skryptu (koniec linii \n, \r\n albo \r); pusty tekst — brak linii, końcowy znak nowej linii nie
// tworzy pustej linii na końcu.
function lines(text) {
    var source = String(text === undefined || text === null ? "" : text)
    if (source === "") return []
    var list = source.split(/\r\n|\r|\n/)
    if (list.length > 1 && list[list.length - 1] === "") list.pop()
    return list
}

// „34 linie”, „1 linia”, „5 linii” (odmiana polska).
function lineLabel(n) {
    var mod10 = n % 10, mod100 = n % 100
    var word = n === 1 ? "linia" : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? "linie" : "linii"
    return n + " " + word
}
