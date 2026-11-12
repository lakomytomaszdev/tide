// Jedyny formatter „startu” w Tide (K-36, PT-354): krótka nazwa gałęzi, data d.MM i 7 znaków SHA.
// Słownictwo „Start od…” na karcie, w oknie scalania i na mapie (K-99) woła tę funkcję — bez kopii.
//
// Wejście to surowe pola z `Git.Diff` (`from`/`to`: {ref, sha, date}); data to ISO 8601 (UTC) z demona.
// Wynik to zwykły tekst („main · 5.11 · abc1234”); pokazuj go jako Text.PlainText — nazwy gałęzi bywają
// dowolne („feature/a<b>”).
.pragma library

// refs/heads/x → x, refs/remotes/o/x → o/x; inne napisy bez zmian (nazwa z „/” i znakami specjalnymi zostaje).
function shortRef(ref) {
    if (typeof ref !== "string") return ""
    if (ref.indexOf("refs/heads/") === 0) return ref.substring(11)
    if (ref.indexOf("refs/remotes/") === 0) return ref.substring(13)
    return ref
}

// d.MM w czasie lokalnym (dzień bez zera wiodącego, miesiąc dwucyfrowy); brak albo zła data → "".
function shortDate(date) {
    if (typeof date !== "string" || date === "") return ""
    const d = new Date(date)
    if (isNaN(d.getTime())) return ""
    const month = d.getMonth() + 1
    return d.getDate() + "." + (month < 10 ? "0" : "") + month
}

// Każda część jest opcjonalna; bez żadnej danych → „nieznany”.
function startLabel(from) {
    const point = from && typeof from === "object" ? from : {}
    const parts = [shortRef(point.ref), shortDate(point.date),
                   typeof point.sha === "string" ? point.sha.substring(0, 7) : ""].filter(p => p !== "")
    return parts.length ? parts.join(" · ") : "nieznany"
}
