// Rachunek odstępu „Środek” — osobno, żeby dało się go przetestować bez Plasmy.
.pragma library

// Pierwszy widoczny widżet o niezerowej szerokości za odstępem (zwykle zegar).
function nextShown(items, own) {
    let after = false
    for (let i = 0; i < items.length; ++i) {
        const item = items[i]
        if (item === own) {
            after = true
        } else if (after && item.visible && item.width > 0) {
            return item
        }
    }
    return null
}

// Nowa szerokość odstępu: przesuwa następny widżet o tyle, o ile jego środek mija środek panelu.
// Pomiar faktycznego położenia zamiast sumy szerokości sąsiadów — odstęp między widżetami i schowane menu
// nie psują wyniku. Poniżej pół piksela nic nie zmieniamy, żeby pasek nie drgał.
function nextSize(size, nextCenter, panelWidth) {
    const shift = nextCenter - panelWidth / 2
    if (Math.abs(shift) < 0.5)
        return size
    return Math.max(1, Math.round(size - shift))
}
