// Odstęp „Środek”: zajmuje tyle miejsca, żeby następny widżet panelu (zegar) stał na środku panelu.
// Zastępuje lewy elastyczny odstęp Plasmy w górnych paskach. Odstęp Plasmy liczy środek z deklarowanych
// szerokości sąsiadów i nie przelicza się, gdy globalne menu chowa się przy oknie bez menu — zegar uciekał
// wtedy o szerokość menu w lewo (03.11.2026). Ten odstęp mierzy faktyczne położenie zegara i poprawia się sam;
// prawy, zwykły odstęp Plasmy wypełnia resztę paska.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "../code/center.js" as Center

PlasmoidItem {
    id: root

    property real size: 1
    property string problem: ""
    // Powód, dla którego odstęp nie może policzyć środka — raz do dziennika powłoki, bez zalewania co 250 ms.
    onProblemChanged: if (problem) console.warn("Tide · Środek: " + problem)
    // Siatka panelu, w której leżą kontenery widżetów (jak w odstępie Plasmy).
    property GridLayout panelLayout: {
        let candidate = root.parent
        while (candidate) {
            if (candidate instanceof GridLayout)
                return candidate
            candidate = candidate.parent
        }
        return null
    }

    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Layout.fillWidth: false
    Layout.fillHeight: true
    Layout.minimumWidth: size
    Layout.preferredWidth: size
    Layout.maximumWidth: size

    function container() {
        let item = root
        while (item && item.parent !== panelLayout)
            item = item.parent
        return item
    }

    // Środek liczymy względem najwyższego elementu okna paska: Plasmoid.containmentItem bywa pusty dla widżetów
    // spoza Plasmy (03.11), a okno pływającego paska ma marginesy po obu stronach równe, więc środek się zgadza.
    function topItem() {
        let item = panelLayout
        while (item && item.parent)
            item = item.parent
        return item
    }

    function update() {
        const panel = topItem()
        const own = container()
        problem = !panelLayout ? "brak siatki panelu" : !own ? "brak kontenera" : !panel ? "brak panelu" : ""
        if (problem)
            return
        const kids = []
        for (let i = 0; i < panelLayout.children.length; ++i)
            kids.push(panelLayout.children[i])
        const next = Center.nextShown(kids, own)
        problem = next ? "" : "brak widżetu za odstępem (" + kids.length + " w siatce)"
        if (!next)
            return
        const nextCenter = next.mapToItem(panel, 0, 0).x + next.width / 2
        const target = Center.nextSize(size, nextCenter, panel.width)
        if (target !== size)
            size = target
    }

    Component.onCompleted: Plasmoid.removeInternalAction("configure")

    // Pomiar co 250 ms: menu chowa się i pojawia przy każdym przełączeniu okna, a siatka panelu
    // nie ogłasza tego tak, żeby wiązanie zawsze to wychwyciło.
    Timer {
        interval: 250
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: root.update()
    }
}
