// Standard tekstu Tide (K-128, PT-501): dłuższy tekst agenta, usługi albo karty jako HTML do Tide.Prose.
//
// Granica bezpieczeństwa: `render` NAJPIERW escapuje cały tekst (&, <, >, ", '), a dopiero potem rozpoznaje bloki
// i wyróżnienia na tekście już bezpiecznym. Znaczniki wstawia tylko ten plik, ze stałymi atrybutami (`class`,
// `style` z palety) — dopasowany tekst nigdy nie trafia do atrybutu. Żadnych linków z tekstu: wynik nie ma
// `href` ani `<img>`, więc kliknięcie niczego nie otwiera, a widok nie robi żądań sieciowych. Schemat
// `tide-card:` jest zarezerwowany dla `Tide.CardRef` (etap A2) i dziś nie powstaje.
//
// Bloki: akapity (pusta linia rozdziela, pojedynczy koniec linii zostaje złamaniem), listy `-`/`*`/`1.`,
// cytaty `>`, kod w trzech backtickach. Zdanie z co najmniej trzema członami po `; ` po dwukropku staje się listą.
// W linii: `kod` (polecenie — masło, ścieżka — sky), ścieżki bez backticków (sky), adresy i domeny (lawenda),
// klawisze (Ctrl+Enter, Esc… — `kbd`), numery kart K-12 (`cref`) i ticketów PT-7 (sky mono).
//
// Tryb Markdown (`options.markdown`): dodatkowo nagłówki `#`–`######`, **pogrubienie**, *kursywa*, linia `---`
// i linki `[tekst](http…)` jako tekst z adresem w lawendzie — bez `href`; link o innym schemacie zostaje samym
// tekstem. To źródło opisu karty tam, gdzie gospodarz nie ma `renderMarkdown` (Sesje).
//
// Kolory: `options.colors` z `colors(Tokens, tło)` — paleta, nic wpisanego na sztywno; tła wyróżnień są już
// zmieszane z tłem tekstu (`surface`), więc kontrast liczy się na jednym kolorze (test w stylu test_diff_highlight).
.pragma library

var ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }

function escape(text) {
    return String(text === undefined || text === null ? "" : text).replace(/[&<>"']/g, function (ch) {
        return ESCAPES[ch]
    })
}

// --- kolory ---------------------------------------------------------------------------------------------------

function hex(color) {
    // QML color albo napis "#rrggbb" → {r, g, b} w 0..1.
    if (color && typeof color === "object" && color.r !== undefined)
        return { r: color.r, g: color.g, b: color.b }
    var digits = String(color || "").replace("#", "")
    if (digits.length === 8) digits = digits.slice(2)   // #aarrggbb z QML
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

// Kolor `top` z kryciem `amount` na tle `bottom` jako kolor pełny ("#rrggbb").
function mix(top, bottom, amount) {
    var a = hex(top), b = hex(bottom)
    return css({ r: a.r * amount + b.r * (1 - amount), g: a.g * amount + b.g * (1 - amount),
                 b: a.b * amount + b.b * (1 - amount) })
}

// Krycie tła wyróżnień (makieta S-9: code.p 10 %, code.cmd 9 %, kbd — hover ok. 6 % tekstu, cytat 6 % lawendy).
var TINT = { path: 0.10, command: 0.09, key: 0.07, quote: 0.06, pre: 0.04 }

// Kolory wyróżnień z tokenów palety (Tide.Tokens) dla tekstu na tle `surface`; `mono` — rodzina czcionki kodu.
function colors(tokens, surface, mono) {
    var bg = surface || tokens.backgroundCard
    return {
        text: css(hex(tokens.textSecondary)), strong: css(hex(tokens.textPrimary)), muted: css(hex(tokens.textMuted)),
        sky: css(hex(tokens.sky)), butter: css(hex(tokens.butter)), lavender: css(hex(tokens.lavender)),
        surface: css(hex(bg)),
        pathBg: mix(tokens.sky, bg, TINT.path), commandBg: mix(tokens.butter, bg, TINT.command),
        keyBg: mix(tokens.textPrimary, bg, TINT.key), quoteBg: mix(tokens.lavender, bg, TINT.quote),
        preBg: mix(tokens.textPrimary, bg, TINT.pre),
        mono: mono || tokens.fontMono || "monospace"
    }
}

// Pary (kolor tekstu, tło) każdego wyróżnienia — do testu kontrastu.
function pairs(c) {
    return [
        { name: "path", fg: c.sky, bg: c.pathBg }, { name: "command", fg: c.butter, bg: c.commandBg },
        { name: "url", fg: c.lavender, bg: c.surface }, { name: "key", fg: c.text, bg: c.keyBg },
        { name: "card", fg: c.sky, bg: c.surface }, { name: "ticket", fg: c.sky, bg: c.surface },
        { name: "quote", fg: c.text, bg: c.quoteBg }, { name: "pre", fg: c.strong, bg: c.preBg },
        { name: "text", fg: c.text, bg: c.surface }, { name: "strong", fg: c.strong, bg: c.surface }
    ]
}

function attr(value) {
    // Atrybuty składa tylko ten plik z palety; escapowanie i tak, gdyby rodzina czcionki miała cudzysłów.
    return escape(value)
}

function style(parts) {
    var out = []
    for (var key in parts) {
        if (parts[key] !== undefined && parts[key] !== null && parts[key] !== "")
            out.push(key + ":" + parts[key])
    }
    return out.length ? " style=\"" + attr(out.join(";")) + "\"" : ""
}

// --- wyróżnienia w linii (tekst już escapowany) -----------------------------------------------------------------

var MODS = "(?:Ctrl|Alt|Shift|Meta|Super)"
var KEY_NAMES = "(?:F1[0-2]|F[1-9]|Enter|Return|Esc|Tab|Space|Backspace|Delete|Del|Insert|Home|End|PgUp|PgDn|" +
                "PageUp|PageDown|Up|Down|Left|Right|[A-Z0-9])"
var NOT_TICKETS = { UTF: 1, SHA: 1, ISO: 1, RFC: 1, AES: 1, RSA: 1, TLS: 1, HTTP: 1, IPV: 1, MD: 1, CP: 1, WIN: 1 }
var FILE_EXT = "(?:qml|js|mjs|ts|tsx|py|md|json|jsonl|sh|bash|txt|ya?ml|toml|html|css|cs|csproj|sln|go|rs|c|h|cpp|" +
               "conf|ini|cfg|png|jpg|jpeg|svg|log|lock|desktop|service|xml|sql|patch|diff)"
var TLD = "(?:com|net|org|io|dev|pl|de|eu|app|co|ai|gov|edu|info|me|tv|cloud|run|ms|us|uk)"

function code(cls, css, inner) { return "<code class=\"" + cls + "\"" + css + ">" + inner + "</code>" }

function kind(c, name) {
    var mono = c.mono
    switch (name) {
    case "path": return style({ color: c.sky, "background-color": c.pathBg, "font-family": mono })
    case "command": return style({ color: c.butter, "background-color": c.commandBg, "font-family": mono })
    case "url": return style({ color: c.lavender, "font-family": mono })
    case "key": return style({ color: c.text, "background-color": c.keyBg, "font-family": mono })
    case "card": return style({ color: c.sky, "font-family": mono, "font-weight": "600" })
    case "ticket": return style({ color: c.sky, "font-family": mono })
    case "strong": return style({ color: c.strong, "font-weight": "600" })
    }
    return ""
}

// Znak przed dopasowaniem, przy którym ścieżka albo domena nie zaczyna się (środek słowa, koniec encji `&lt;`).
function joined(source, index) {
    return index > 0 && /[\w;\/.@&#-]/.test(source.charAt(index - 1))
}

// Końcowa interpunkcja zdania nie należy do adresu ani ścieżki.
function trimTail(text) {
    var m = /[.,;:!?)\]]+$/.exec(text)
    return m ? text.length - m[0].length : text.length
}

function looksLikePath(inner) {
    // `kod` w backtickach: bez spacji, z ukośnikiem, `~`, `./` albo rozszerzeniem pliku — ścieżka; inaczej polecenie.
    return !/\s/.test(inner) && (/\//.test(inner) || /^~/.test(inner) || new RegExp("\\." + FILE_EXT + "(?::\\d+)?$").test(inner))
}

function rules(c, markdown) {
    var list = []
    if (markdown) {
        // Adres może mieć jedną parę nawiasów w środku (np. „javascript:alert(1)” — znika cały).
        list.push({ re: /\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g, fn: function (m) {
            var label = inline(m[1], c, false)
            var url = m[2]
            if (!/^https?:\/\//i.test(url)) return { html: label }   // javascript:, data:, file: — sam tekst
            return { html: label + " (" + code("url", kind(c, "url"), url) + ")" }
        } })
        list.push({ re: /\*\*([^*\n]+)\*\*|__([^_\n]+)__/g, fn: function (m) {
            return { html: "<strong" + kind(c, "strong") + ">" + inline(m[1] || m[2], c, true) + "</strong>" }
        } })
        list.push({ re: /\*([^*\s][^*\n]*)\*/g, fn: function (m) {
            return { html: "<em>" + inline(m[1], c, true) + "</em>" }
        } })
    }
    list.push({ re: /\b(?:https?|ftp):\/\/[^\s&;]+(?:&amp;[^\s&;]+)*/g, fn: function (m) {
        var end = trimTail(m[0])
        return { html: code("url", kind(c, "url"), m[0].slice(0, end)), used: end }
    } })
    list.push({ re: new RegExp("\\b(?:[a-z0-9-]+\\.)+" + TLD + "\\b(?:\\/[^\\s&;]*)?", "g"), fn: function (m, s) {
        if (joined(s, m.index)) return null
        var end = trimTail(m[0])
        return { html: code("url", kind(c, "url"), m[0].slice(0, end)), used: end }
    } })
    list.push({ re: new RegExp("\\b" + MODS + "(?:\\+" + MODS + ")*\\+" + KEY_NAMES + "(?![\\w+])", "g"), fn: function (m) {
        var css = kind(c, "key")
        return { html: m[0].split("+").map(function (k) { return "<kbd" + css + ">" + k + "</kbd>" }).join("+") }
    } })
    list.push({ re: /\b(?:Esc|Enter|Backspace|F1[0-2]|F[1-9])\b(?![+\-\/.]\w)/g, fn: function (m) {
        return { html: "<kbd" + kind(c, "key") + ">" + m[0] + "</kbd>" }
    } })
    list.push({ re: /\bK-\d+\b(?!-)/g, fn: function (m) {
        return { html: "<span class=\"cref\"" + kind(c, "card") + ">" + m[0] + "</span>" }
    } })
    list.push({ re: /\b([A-Z]{2,})-\d+\b(?!-)/g, fn: function (m) {
        if (NOT_TICKETS[m[1]]) return null
        return { html: "<span class=\"tref\"" + kind(c, "ticket") + ">" + m[0] + "</span>" }
    } })
    // Ścieżka od ~/, ./, ../ albo /: przed nią początek, spacja albo nawias (nie koniec encji: „&lt;/script&gt;”).
    list.push({ re: /(?:~|\.\.?)?\/[\w.@+~-]+(?:\/[\w.@+~-]*)*(?::\d+)?/g, fn: function (m, s) {
        if (joined(s, m.index)) return null
        var end = trimTail(m[0])
        return end > 1 ? { html: code("p", kind(c, "path"), m[0].slice(0, end)), used: end } : null
    } })
    // Ścieżka względna: dwa ukośniki, ukośnik na końcu, rozszerzenie pliku albo znak nazwy (-_. cyfra) — nie „tak/nie”.
    list.push({ re: /[\w.@+-]+\/[\w.@+-]*(?:\/[\w.@+-]*)*(?::\d+)?/g, fn: function (m, s) {
        if (joined(s, m.index)) return null
        var end = trimTail(m[0])
        var path = m[0].slice(0, end)
        var slashes = path.split("/").length - 1
        if (!/[A-Za-z]/.test(path) || !(slashes >= 2 || /\/$/.test(path) || /[-_.\d]/.test(path))) return null
        return { html: code("p", kind(c, "path"), path), used: end }
    } })
    list.push({ re: new RegExp("\\b[\\w-]+(?:\\.[\\w-]+)*\\." + FILE_EXT + "\\b(?::\\d+)?", "g"), fn: function (m, s) {
        if (joined(s, m.index)) return null
        return { html: code("p", kind(c, "path"), m[0]) }
    } })
    return list
}

// Jedno przejście od lewej: najwcześniejsze dopasowanie (przy remisie — pierwsza reguła), a wstawiony HTML nie jest
// skanowany ponownie. Odrzucone dopasowanie przesuwa start o jeden znak.
function scan(s, list) {
    var out = "", pos = 0
    while (pos < s.length) {
        var best = null, rule = null
        for (var i = 0; i < list.length; ++i) {
            list[i].re.lastIndex = pos
            var m = list[i].re.exec(s)
            if (m && (best === null || m.index < best.index)) { best = m; rule = list[i] }
        }
        if (best === null) break
        var made = rule.fn(best, s)
        if (made === null) {
            out += s.slice(pos, best.index + 1)
            pos = best.index + 1
            continue
        }
        var used = made.used === undefined ? best[0].length : made.used
        out += s.slice(pos, best.index) + made.html
        pos = best.index + Math.max(1, used)
    }
    return out + s.slice(pos)
}

function inline(s, c, markdown) {
    var out = "", last = 0, re = /`([^`\n]+)`/g, m
    var list = rules(c, markdown)
    while ((m = re.exec(s)) !== null) {
        out += scan(s.slice(last, m.index), list)
        out += looksLikePath(m[1]) ? code("p", kind(c, "path"), m[1]) : code("cmd", kind(c, "command"), m[1])
        last = re.lastIndex
    }
    return out + scan(s.slice(last), list)
}

// --- bloki (linie już escapowane) -----------------------------------------------------------------------------

// Interlinia ok. 1,6 em z makiety: Qt liczy procent od naturalnej wysokości linii czcionki (Geist ok. 1,36 em
// z odstępem), więc 120 % daje ok. 1,6 em; 160 % dawało ok. 2,2 em.
var LINE_HEIGHT = "120%"

function paragraphStyle(c) { return style({ color: c.text, "margin-top": "0px", "margin-bottom": "8px", "line-height": LINE_HEIGHT }) }

// „Zdanie: a; b; c.” → [„Zdanie:”, [a, b, c], ogon po kropce]; null, gdy członów po dwukropku jest mniej niż trzy.
function semicolonList(line) {
    var first = line.indexOf("; ")
    if (first < 0) return null
    var colon = line.lastIndexOf(": ", first)
    if (colon < 0) return null
    var parts = line.slice(colon + 2).split("; ")
    if (parts.length < 3) return null
    var tail = ""
    var lastPart = parts[parts.length - 1]
    var stop = lastPart.search(/\.(\s|$)/)
    if (stop >= 0) {
        tail = lastPart.slice(stop + 1).trim()
        lastPart = lastPart.slice(0, stop)
    }
    parts[parts.length - 1] = lastPart
    for (var i = 0; i < parts.length; ++i) {
        if (!parts[i].trim()) return null
    }
    return { head: line.slice(0, colon + 1), items: parts, tail: tail }
}

function listHtml(items, ordered, c, markdown) {
    var tag = ordered ? "ol" : "ul"
    var out = "<" + tag + style({ color: c.text, "margin-top": "0px", "margin-bottom": "8px" }) + ">"
    for (var i = 0; i < items.length; ++i)
        out += "<li" + style({ "line-height": LINE_HEIGHT }) + ">" + inline(items[i], c, markdown) + "</li>"
    return out + "</" + tag + ">"
}

function paragraphs(lines, c, markdown) {
    var out = "", current = []
    function close() {
        if (current.length) out += "<p" + paragraphStyle(c) + ">" + current.join("<br>") + "</p>"
        current = []
    }
    for (var i = 0; i < lines.length; ++i) {
        var split = semicolonList(lines[i])
        if (!split) {
            current.push(inline(lines[i], c, markdown))
            continue
        }
        current.push(inline(split.head, c, markdown))
        close()
        out += listHtml(split.items, false, c, markdown)
        if (split.tail) current.push(inline(split.tail, c, markdown))
    }
    close()
    return out
}

var FENCE = /^\s*```/
var BULLET = /^\s*[-*+]\s+(.*)$/
var NUMBER = /^\s*\d+[.)]\s+(.*)$/
var QUOTE = /^\s*&gt;\s?(.*)$/
var HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/
var RULE = /^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/

function blocks(lines, c, markdown) {
    var out = "", para = []
    function flush() {
        if (para.length) out += paragraphs(para, c, markdown)
        para = []
    }
    var i = 0
    while (i < lines.length) {
        var line = lines[i]
        if (FENCE.test(line)) {
            flush()
            var body = []
            ++i
            while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++])
            ++i   // zamykające ``` (albo koniec tekstu)
            out += "<pre" + style({ color: c.strong, "background-color": c.preBg, "font-family": c.mono,
                                    "margin-top": "0px", "margin-bottom": "8px" }) + ">" + body.join("\n") + "</pre>"
            continue
        }
        var h = markdown ? HEADING.exec(line) : null
        if (h) {
            flush()
            var level = h[1].length
            out += "<h" + level + style({ color: c.strong, "font-weight": "600", "margin-top": "10px",
                                          "margin-bottom": "6px" }) + ">"
                   + inline(h[2], c, markdown) + "</h" + level + ">"
            ++i
            continue
        }
        if (markdown && RULE.test(line)) {
            flush()
            out += "<hr>"
            ++i
            continue
        }
        var ordered = NUMBER.test(line)
        if (ordered || BULLET.test(line)) {
            flush()
            var items = []
            var pattern = ordered ? NUMBER : BULLET
            while (i < lines.length) {
                var item = pattern.exec(lines[i])
                if (item) items.push(item[1])
                else if (items.length && /^\s{2,}\S/.test(lines[i])) items[items.length - 1] += " " + lines[i].trim()
                else break
                ++i
            }
            out += listHtml(items, ordered, c, markdown)
            continue
        }
        if (QUOTE.test(line)) {
            flush()
            var quoted = []
            while (i < lines.length && QUOTE.test(lines[i])) quoted.push(QUOTE.exec(lines[i++])[1])
            out += "<blockquote" + style({ color: c.text, "background-color": c.quoteBg, "margin-left": "14px",
                                           "margin-top": "0px", "margin-bottom": "8px" }) + ">"
                   + blocks(quoted, c, markdown) + "</blockquote>"
            continue
        }
        if (!line.trim()) flush()
        else para.push(line)
        ++i
    }
    flush()
    return out
}

// Tekst → HTML. options: { markdown: bool, colors: colors(Tokens, tło) }.
function render(text, options) {
    var opts = options || {}
    var safe = escape(text).replace(/\r\n?/g, "\n")   // najpierw cały tekst, potem rozpoznawanie
    return blocks(safe.split("\n"), opts.colors || {}, !!opts.markdown)
}
