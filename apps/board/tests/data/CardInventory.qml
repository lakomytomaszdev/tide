// Zbieracz inwentarza kafelka karty (PT-505, test_card_inventory.py): przechodzi po widocznych elementach kafelka
// i zwraca JSON z tym, co widzi użytkownik i czytnik ekranu — bez zależności od budowy kafelka (nazwy typów, liczba
// ramek i prostokątów tła się nie liczą). Pola wpisu: name (objectName), text, tip (podpowiedź), an/ad/ar
// (Accessible.name, .description, .role), tone (kolor znaczenia), kind (tag, button, segment, progressBar).
import QtQuick
import QtQuick.Controls

QtObject {
    function roleName(role) {
        if (role === Accessible.Button) return "button"
        if (role === Accessible.Link) return "link"
        if (role === Accessible.StaticText) return "statictext"
        if (role === Accessible.NoRole) return "none"
        return "role" + role
    }

    function colorOf(value) {
        return value === undefined || value === null ? "" : String(value)
    }

    function tipOf(it) {
        const attached = it.ToolTip ? String(it.ToolTip.text || "") : ""
        return attached !== "" ? attached : (typeof it.toolTip === "string" ? it.toolTip : "")
    }

    function isButton(it) {
        return it.fill !== undefined && (it.textColor !== undefined || it.ink !== undefined)
    }

    function entry(it) {
        const e = {
            name: String(it.objectName || ""),
            text: typeof it.text === "string" ? it.text : "",
            tip: tipOf(it),
            an: String(it.Accessible.name || ""),
            ad: String(it.Accessible.description || ""),
            ar: roleName(it.Accessible.role)
        }
        let kind = ""
        if (it.dotColor !== undefined) {
            kind = "tag"
            e.text = String(it.name || "")
            e.tone = colorOf(it.dotColor)
        } else if (isButton(it)) {
            kind = "button"
            e.tones = [colorOf(it.fill), colorOf(it.ink !== undefined ? it.ink : it.textColor)]
        } else if (e.name === "ticketSegment") {
            kind = "segment"
            e.tone = colorOf(it.color)
            e.phase = String(it.parent.phase || "")
        } else if (e.name === "cardYouTrack") {
            e.tone = colorOf(it.color)
        } else if (it.children.length === 1 && Math.round(it.height) === 4 && it.radius !== undefined
                   && it.children[0].width !== undefined && it.width > 0) {
            kind = "progressBar"
            e.ratio = Math.round(it.children[0].width / it.width * 1000) / 1000
            e.tone = colorOf(it.children[0].color)
        } else if (it.font !== undefined && typeof it.text === "string" && it.color !== undefined) {
            e.tone = colorOf(it.color)
        }
        if (kind !== "") e.kind = kind
        return e
    }

    function relevant(e) {
        return e.kind !== undefined || (e.name !== "" && e.name.indexOf("button") !== 0)
            || e.text !== "" || e.tip !== "" || e.an !== "" || e.ad !== "" || e.ar !== "none"
    }

    function walk(it, top, items, rects, withOpacity) {
        for (let i = 0; i < it.children.length; i++) {
            const child = it.children[i]
            if (!child.visible) continue
            const e = entry(child)
            if (e.name === "ticketSegment") {
                const p = child.mapToItem(top, 0, 0)
                rects.push({entry: e, x: p.x, width: child.width})
                if (withOpacity) e.opacity = Math.round(child.opacity * 100) / 100
            }
            if (relevant(e)) items.push(e)
            // Przycisk to jedna pozycja (tekst, podpowiedź, dostępność, kolory); jego wnętrze to szczegół wyglądu.
            if (e.kind !== "button") walk(child, top, items, rects, withOpacity)
        }
    }

    function rootEntry(it) {
        const e = entry(it)
        e.compact = Boolean(it.compact)
        e.waiting = Boolean(it.waiting)
        e.selected = Boolean(it.selected)
        e.ghost = Boolean(it.ghost)
        e.activeFocus = Boolean(it.activeFocus)
        e.opacity = Math.round(it.opacity * 100) / 100
        e.border = it.border && it.border.width > 0 ? colorOf(it.border.color) : ""
        e.titleMaxLines = 0
        const title = it.c ? String(it.c.title || "") : ""
        const find = function (node) {
            for (let i = 0; i < node.children.length; i++) {
                const c = node.children[i]
                if (c.visible && c.text === title && c.maximumLineCount !== undefined) return c
                const r = find(c)
                if (r) return r
            }
            return null
        }
        const titleItem = title !== "" ? find(it) : null
        if (titleItem) e.titleMaxLines = titleItem.maximumLineCount
        return e
    }

    function dump(top, withOpacity) {
        const items = []
        const rects = []
        walk(top, top, items, rects, withOpacity === true)
        // Przerwa między falami: odstęp przed segmentem większy od najmniejszego odstępu na pasku.
        const gaps = []
        for (let i = 1; i < rects.length; i++) gaps.push(rects[i].x - (rects[i - 1].x + rects[i - 1].width))
        const least = gaps.length ? Math.min.apply(null, gaps) : 0
        for (let i = 0; i < rects.length; i++)
            rects[i].entry.gapBefore = i > 0 && gaps[i - 1] - least > 2
        return JSON.stringify({root: rootEntry(top), items: items})
    }
}
