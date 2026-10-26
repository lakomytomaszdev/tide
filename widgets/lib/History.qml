// Historia próbek w buforze pierścieniowym o stałej pojemności (np. procesor z ostatniej minuty).
// push(v) nadpisuje najstarszą próbkę po zapełnieniu bufora; samples to kopia od najstarszej
// do najnowszej, gotowa do wykresu. null oznacza brak danych w danej chwili.
import QtQuick

QtObject {
    id: history

    property int capacity: 30

    readonly property int count: _count
    readonly property var samples: _snapshot
    readonly property var latest: _count > 0 ? _snapshot[_count - 1] : null
    // Średnia i szczyt liczone tylko z próbek z danymi; null, gdy takich nie ma.
    readonly property var average: _stats.average
    readonly property var peak: _stats.peak

    property var _buffer: []
    property int _head: 0      // indeks, pod który trafi następna próbka
    property int _count: 0
    property var _snapshot: []
    property var _stats: ({ average: null, peak: null })

    function push(value) {
        const size = Math.max(1, capacity)
        if (_buffer.length !== size) {
            _resize(size)
        }
        const sample = typeof value === "number" && isFinite(value) ? value : null
        _buffer[_head] = sample
        _head = (_head + 1) % size
        _count = Math.min(_count + 1, size)
        _refresh()
    }

    function clear() {
        _buffer = []
        _head = 0
        _count = 0
        _refresh()
    }

    // Zmiana pojemności zachowuje najnowsze próbki.
    function _resize(size) {
        const kept = _ordered().slice(-size)
        _buffer = new Array(size).fill(null)
        for (let i = 0; i < kept.length; i++) {
            _buffer[i] = kept[i]
        }
        _count = kept.length
        _head = kept.length % size
    }

    function _ordered() {
        const size = _buffer.length
        const out = []
        for (let i = 0; i < _count; i++) {
            out.push(_buffer[(_head - _count + i + size) % size])
        }
        return out
    }

    function _refresh() {
        const ordered = _ordered()
        const known = ordered.filter(v => v !== null)
        _snapshot = ordered
        _stats = known.length === 0 ? { average: null, peak: null } : {
            average: known.reduce((a, b) => a + b, 0) / known.length,
            peak: Math.max.apply(null, known)
        }
    }

    onCapacityChanged: if (_buffer.length > 0) { _resize(Math.max(1, capacity)); _refresh() }
}
