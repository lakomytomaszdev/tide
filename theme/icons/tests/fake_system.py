"""Udawany system: katalogi XDG z plikami .desktop i motywami ikon w katalogu tymczasowym.

Każda aplikacja reprezentuje jedną kategorię audytu, a narzędzia zmieniające
pulpit są atrapami zapisującymi wywołania do calls.log.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "theme" / "icons" / "tide_icons.py"
RIDER_ID = "jetbrains-rider-44178d44-9a4a-480d-b842-05b2863d7712"
BLUE_FOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<path d="M4 10h20l4 4h32v40H4z" fill="#35cafc"/><path d="M4 18h56v36H4z" fill="#78d9fd"/></svg>\n')
DOCUMENTS = BLUE_FOLDER.replace("</svg>", '<path d="M26 26h12v16H26z" fill="#03b6ec"/>'
                                          '<circle cx="50" cy="44" r="3" fill="#ffffff"/></svg>')


def circle_svg(color: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="32" fill="{color}"/></svg>\n'


def square_png(path: Path, size: int = 256, color=(200, 80, 40, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (size, size), color)
    ImageDraw.Draw(image).rectangle((size // 4, size // 4, size // 2, size // 2), fill=(250, 250, 250, 255))
    image.save(path)


def desktop(name: str, icon: str | None, **extra: str) -> str:
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", f"Exec={name.lower()}"]
    if icon is not None:
        lines.append(f"Icon={icon}")
    lines += [f"{k}={v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


class FakeSystem:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.data = self.home / ".local/share"
        self.state = self.home / ".local/state"
        self.config = self.home / ".config"
        self.system = base / "usr/share"
        self.opt = base / "opt"
        self.log = base / "calls.log"
        self.bin = base / "bin"
        self.bin.mkdir()
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CONFIG_DIRS": "/etc/xdg",  # bez ~/.config/kdedefaults z sesji Plasmy
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_DATA_DIRS": str(self.system),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "TIDE_ICON_BASE": "Base",
        }
        self.env.pop("FLATPAK_USER_DIR", None)
        self.stubs()
        self.themes()
        self.apps()

    def cleanup(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------ atrapy

    def stub(self, name: str, body: str = "") -> None:
        script = self.bin / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n{body}')
        script.chmod(0o755)

    def stubs(self) -> None:
        self.stub("kbuildsycoca6")
        self.stub("dbus-send")
        # Prawdziwe plasma-changeicons zapisuje motyw w kdeglobals — atrapa robi to samo.
        self.stub("plasma-changeicons", 'kwriteconfig6 --file kdeglobals --group Icons --key Theme "$1"\n')
        overrides = self.data / "flatpak/overrides/global"
        self.stub("flatpak", f'mkdir -p "{overrides.parent}"\n'
                             f'printf "[Context]\\nfilesystems=xdg-data/icons:ro;\\n" > "{overrides}"\n')

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    # ------------------------------------------------------------ motywy

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def themes(self) -> None:
        base = self.data / "icons/Base"
        self.write(base / "index.theme", "[Icon Theme]\nName=Base\nInherits=Other,hicolor\nPanelDefault=48\n"
                   "KDE-Extensions=.svg\nDirectories=apps/scalable,places/scalable\n\n"
                   "[apps/scalable]\nSize=64\nContext=Applications\nType=Scalable\nMinSize=16\nMaxSize=512\n\n"
                   "[places/scalable]\nSize=64\nContext=Places\nType=Scalable\nMinSize=16\nMaxSize=512\n")
        for name, color in (("firefox", "#ff8800"), ("rider", "#dd3377"), ("visual-studio-code", "#2277ee"),
                            ("code", "#ffaa00")):
            self.write(base / f"apps/scalable/{name}.svg", circle_svg(color))
        self.write(base / "places/scalable/folder.svg", BLUE_FOLDER)
        self.write(base / "places/scalable/folder-documents.svg", DOCUMENTS)
        self.write(base / "places/scalable/folder-blue.svg", BLUE_FOLDER)

        other = self.system / "icons/Other"
        self.write(other / "index.theme", "[Icon Theme]\nName=Other\nDirectories=apps/48\n\n"
                   "[apps/48]\nSize=48\nContext=Applications\nType=Fixed\n")
        self.write(other / "apps/48/falkon.svg", circle_svg("#44aaff"))

        hicolor = self.system / "icons/hicolor"
        self.write(hicolor / "index.theme", "[Icon Theme]\nName=Hicolor\nDirectories=256x256/apps,scalable/apps\n\n"
                   "[256x256/apps]\nSize=256\nContext=Applications\nType=Threshold\n\n"
                   "[scalable/apps]\nSize=128\nContext=Applications\nType=Scalable\nMinSize=8\nMaxSize=512\n")
        square_png(self.data / "icons/hicolor/256x256/apps/steam_icon_42.png")
        self.write(self.data / f"icons/hicolor/scalable/apps/{RIDER_ID}.svg", circle_svg("#000000"))
        square_png(self.opt / "vscode.png", color=(30, 110, 220, 255))
        square_png(self.opt / "zed.png", color=(20, 20, 20, 255))

    # ------------------------------------------------------------ aplikacje

    def apps(self) -> None:
        system = self.system / "applications"
        self.write(system / "firefox.desktop", desktop("Firefox", "firefox"))
        self.write(system / f"{RIDER_ID}.desktop", desktop("Rider", RIDER_ID, StartupWMClass="jetbrains-rider"))
        self.write(system / "game.desktop", desktop("Gra", "steam_icon_42"))
        self.write(system / "falkon.desktop", desktop("Falkon", "falkon"))
        self.write(system / "editor.desktop", desktop("Edytor", "code"))
        self.write(system / "chatgpt.desktop", desktop("ChatGPT", "chatgpt"))
        self.write(system / "code_code.desktop",
                   desktop("Visual Studio Code", str(self.opt / "vscode.png"), StartupWMClass="code_code",
                           **{"X-SnapInstanceName": "code"})
                   + f"\n[Desktop Action new-empty-window]\nName=New Empty Window\nIcon={self.opt / 'vscode.png'}\n")
        self.write(system / "hidden.desktop", desktop("Ukryta", "/nope.png", NoDisplay="true"))
        self.write(system / "gnome.desktop", desktop("Tylko GNOME", "/nope.png", OnlyShowIn="GNOME;"))
        self.write(system / "gone.desktop", desktop("Bez programu", "/nope.png", TryExec="/nonexistent/bin"))
        # Plik użytkownika w ~/.local/share/applications, jak instalator Zeda.
        self.write(self.data / "applications/dev.zed.Zed.desktop", desktop("Zed", str(self.opt / "zed.png")))
        self.write(self.data / "applications/mine.desktop", desktop("Moja", "firefox"))

    # ------------------------------------------------------------ uruchamianie

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["/usr/bin/python3", str(TOOL), *args], env=self.env, check=check,
                              capture_output=True, text=True)

    def script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/icons.py"), *args], env=self.env,
                              check=True, capture_output=True, text=True)
