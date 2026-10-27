// Przełącznik (mock: „Nowy worktree”): tor w akcencie, gdy włączony, gałka w kolorze pulpitu.
// Wyłączony (enabled: false) — przygaszony i bez reakcji.
import QtQuick
import "tide" as Tide

Rectangle {
    id: toggle

    property bool checked: false
    property string accessibleName: ""
    signal toggled(bool checked)

    function flip() {
        if (!enabled) return
        toggled(!checked)
    }

    implicitWidth: 34
    implicitHeight: 20
    radius: height / 2
    color: checked && enabled ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.18)
    border.width: activeFocus ? 2 : 0
    border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
    activeFocusOnTab: enabled
    Accessible.role: Accessible.CheckBox
    Accessible.name: accessibleName
    Accessible.checkable: true
    Accessible.checked: checked
    Accessible.onToggleAction: flip()
    Accessible.onPressAction: flip()
    Keys.onSpacePressed: flip()
    Keys.onReturnPressed: flip()

    Rectangle {
        width: 14
        height: 14
        radius: 7
        y: 3
        x: toggle.checked ? toggle.width - width - 3 : 3
        color: Tide.Tokens.backgroundDesktop
        Behavior on x { NumberAnimation { duration: 120 } }
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: toggle.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: toggle.flip()
    }
}
