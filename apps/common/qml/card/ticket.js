// Prezentacja ticketu karty (TicketTile.qml, PT-332): podpis stanu. Stan i jego etykietę liczy usługa
// (`derived.tickets[].status`, `statusLabel`); biblioteka podmienia tylko etykietę stanu `merged`, żeby kafelek nie
// sugerował scalenia karty do gałęzi głównej (K-70 pkt 8). Pozostałe teksty zostają z usługi.
.pragma library

var MERGED_LABEL = "scalony do gałęzi karty"

// Podpis stanu na kafelku: dla `merged` „scalony do gałęzi karty”, dla reszty etykieta z usługi.
function statusLabel(ticket) {
    if (!ticket) return ""
    if (ticket.status === "merged") return MERGED_LABEL
    return ticket.statusLabel || ""
}
