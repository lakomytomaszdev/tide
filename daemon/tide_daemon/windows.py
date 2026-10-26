"""Przejście do okna sesji: terminal (albo aplikacja) w łańcuchu przodków procesu agenta.

Ustalanie jest najlepszym możliwym przybliżeniem. Gdy w przodkach nie ma znanego terminala
ani aplikacji, sesja po prostu nie jest klikalna.

Aktywacja na Waylandzie idzie przez skrypt KWin ładowany przez D-Bus (org.kde.KWin /Scripting),
bo zwykła aplikacja nie może podnieść cudzego okna. Dla Konsole najpierw przełączamy kartę
(org.kde.konsole.Window.setCurrentSession), żeby okno pokazało właściwą sesję.
"""
import json
import os
import tempfile

from .processes import ProcessTable

# Nazwy procesów (comm, maks. 15 znaków), które mają okno, do którego warto przejść.
WINDOW_OWNERS = {
    "konsole": "Konsole",
    "yakuake": "Yakuake",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "foot": "foot",
    "footclient": "foot",
    "wezterm-gui": "WezTerm",
    "ghostty": "Ghostty",
    "xterm": "XTerm",
    "gnome-terminal-": "Terminal GNOME",
    "ptyxis": "Ptyxis",
    "tilix": "Tilix",
    "terminator": "Terminator",
    "code": "VS Code",
    "codium": "VSCodium",
    "cursor": "Cursor",
    "zed-editor": "Zed",
    "ChatGPT": "Codex",
    "t3code": "T3 Code",
    "t3-code": "T3 Code",
}
SCRIPT_NAME = "tide-activate-window"


def target_for(table: ProcessTable, pid: int) -> dict | None:
    """Cel aktywacji dla procesu agenta albo None, gdy nie da się go ustalić."""
    proc = table.get(pid)
    if not proc:
        return None
    chain = table.ancestors(pid)
    owner = next((p for p in chain if p.name in WINDOW_OWNERS), None)
    if not owner:
        return None
    pids = [p.pid for p in chain[: chain.index(owner) + 1]]
    target = {"pids": pids, "label": WINDOW_OWNERS[owner.name]}
    env = proc.env or {}
    if owner.name == "konsole" and all(env.get(k) for k in ("KONSOLE_DBUS_SERVICE", "KONSOLE_DBUS_WINDOW",
                                                            "KONSOLE_DBUS_SESSION")):
        target["konsole"] = {
            "service": env["KONSOLE_DBUS_SERVICE"],
            "window": env["KONSOLE_DBUS_WINDOW"],
            "session": env["KONSOLE_DBUS_SESSION"],
        }
    return target


def kwin_script(pids: list[int], caption_hint: str = "") -> str:
    """Skrypt KWin: aktywuje okno należące do najbliższego z podanych procesów."""
    return f"""// Wygenerowane przez tide-daemon: przejście do okna sesji agenta.
const pids = {json.dumps(pids)};
const hint = {json.dumps(caption_hint)};
let best = null, bestScore = -1;
for (const w of workspace.windowList()) {{
    if (!w.normalWindow) continue;
    const rank = pids.indexOf(w.pid);
    if (rank < 0) continue;
    let score = 100 - rank;
    if (hint && w.caption.indexOf(hint) >= 0) score += 1000;
    if (score > bestScore) {{ best = w; bestScore = score; }}
}}
if (best) {{
    if (best.minimized) best.minimized = false;
    workspace.activeWindow = best;
}}
"""


def activate(target: dict) -> bool:
    """Przełącza kartę Konsole (jeśli dotyczy) i podnosi okno przez skrypt KWin."""
    from PyQt6.QtCore import QMetaType, QTimer
    from PyQt6.QtDBus import QDBus, QDBusArgument, QDBusConnection, QDBusMessage

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False

    def call(service, path, iface, member, *args, timeout=2000):
        msg = QDBusMessage.createMethodCall(service, path, iface, member)
        if args:
            msg.setArguments(list(args))
        return bus.call(msg, QDBus.CallMode.Block, timeout)

    hint = ""
    konsole = target.get("konsole")
    if konsole:
        try:
            number = int(konsole["session"].rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            number = -1
        if number >= 0:
            call(konsole["service"], konsole["window"], "org.kde.konsole.Window", "setCurrentSession",
                 QDBusArgument(number, QMetaType.Type.Int.value))
            reply = call(konsole["service"], konsole["session"], "org.kde.konsole.Session", "title",
                         QDBusArgument(1, QMetaType.Type.Int.value))
            if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
                hint = str(reply.arguments()[0])

    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    script_dir = os.path.join(runtime, "tide")
    os.makedirs(script_dir, exist_ok=True)
    path = os.path.join(script_dir, "activate-window.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(kwin_script(target["pids"], hint))

    kwin = ("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting")
    call(*kwin, "unloadScript", SCRIPT_NAME)
    reply = call(*kwin, "loadScript", path, SCRIPT_NAME)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        return False
    call(*kwin, "start")
    # Skrypt wykonuje się od razu; sprzątamy go chwilę później.
    QTimer.singleShot(2000, lambda: call(*kwin, "unloadScript", SCRIPT_NAME))
    return True
