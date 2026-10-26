"""Widżet System: pomocnik sysinfo.py, logika JS, bufor historii, pakiet, instalacja i QML.

kpackagetool6 jest atrapą (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe. Pomocnik
dostaje fałszywe /proc, mountinfo i /dev/disk. Testy QML zastępują pomocnika i kstart
atrapami, a magistralę sesji ustawiają na nieistniejącą — bez ksystemstats i tide-daemon.
Jeden test porównuje widżet z prawdziwymi czujnikami; bez działającego ksystemstats jest pomijany.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from unittest import mock

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env

WIDGET_ID = "org.tide.system"
SYSTEM_DIR = os.path.join(WIDGETS_DIR, "system")
SYSINFO = os.path.join(SYSTEM_DIR, "contents", "code", "sysinfo.py")
PALETTE = os.path.join(REPO, "theme", "palette", "mist.json")

def load_sysinfo():
    """Moduł pomocnika wprost z pakietu, bez zostawiania __pycache__ w katalogu widżetu."""
    spec = importlib.util.spec_from_file_location("tide_sysinfo", SYSINFO)
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


sysinfo = load_sysinfo()

StatVfs = namedtuple("StatVfs", "f_blocks f_bavail f_frsize")


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(text)


class MountsTest(unittest.TestCase):
    """Punkt montowania → urządzenie → UUID → identyfikator czujnika disk/<id>/…"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sysinfo-mounts-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.dev = os.path.join(self.tmp, "dev")
        for name in ("nvme0n1p2", "nvme0n1p7", "sdb1", "dm-0"):
            write(os.path.join(self.dev, name), "")
        by_uuid = os.path.join(self.dev, "disk", "by-uuid")
        by_label = os.path.join(self.dev, "disk", "by-label")
        os.makedirs(by_uuid)
        os.makedirs(by_label)
        os.symlink("../../nvme0n1p2", os.path.join(by_uuid, "6970dbe3-a625-428d-b2d3-d95f1e2f0d4b"))
        os.symlink("../../nvme0n1p7", os.path.join(by_uuid, "80064ba3-68e3-4db3-989a-d202a96ef9b8"))
        os.symlink("../../nvme0n1p2", os.path.join(by_label, "kde-neon-root"))
        os.symlink("../../sdb1", os.path.join(by_label, "Dane\\x20robocze"))
        # /dev/mapper/home → ../dm-0, a UUID wskazuje na dm-0 (LUKS/LVM).
        os.makedirs(os.path.join(self.dev, "mapper"))
        os.symlink("../dm-0", os.path.join(self.dev, "mapper", "crypt"))
        os.symlink("../../dm-0", os.path.join(by_uuid, "aaaa-bbbb"))
        self.sizes = {}

    def mountinfo(self, *entries):
        lines = [f"{20 + i} 1 259:{i} / {target} rw,relatime shared:1 - {fstype} {source} rw"
                 for i, (target, fstype, source) in enumerate(entries)]
        path = os.path.join(self.tmp, "mountinfo")
        write(path, "\n".join(lines) + "\n")
        return path

    def statvfs(self, target):
        blocks, avail = self.sizes.get(target, (1000, 100))
        return StatVfs(blocks, avail, 4096)

    def report(self, paths, *entries):
        return sysinfo.mounts_report(paths, self.mountinfo(*entries), os.path.join(self.dev, "disk"), self.statvfs)

    def test_root_and_home_map_to_uuid_sensor_ids(self):
        self.sizes = {"/": (1000, 96), "/home": (5000, 600)}
        result = self.report(["/", "/home"],
                             ("/", "ext4", os.path.join(self.dev, "nvme0n1p2")),
                             ("/home", "ext4", os.path.join(self.dev, "nvme0n1p7")),
                             ("/tmp", "tmpfs", "tmpfs"))
        root, home = result["mounts"]
        self.assertEqual((root["target"], root["sensorId"], root["label"]),
                         ("/", "6970dbe3-a625-428d-b2d3-d95f1e2f0d4b", "kde-neon-root"))
        self.assertEqual((home["target"], home["sensorId"], home["label"]),
                         ("/home", "80064ba3-68e3-4db3-989a-d202a96ef9b8", ""))
        # Jak ksystemstats: zajęte = rozmiar − dostępne dla użytkownika.
        self.assertEqual(root["size"], 1000 * 4096)
        self.assertEqual(root["free"], 96 * 4096)
        self.assertEqual(root["usedPercent"], 90.4)
        self.assertEqual(result["targets"], ["/", "/home", "/tmp"])

    def test_home_on_root_partition_is_listed_once(self):
        result = self.report(["/", "/home"], ("/", "ext4", os.path.join(self.dev, "nvme0n1p2")))
        self.assertEqual([m["target"] for m in result["mounts"]], ["/"])
        self.assertEqual(sysinfo.mount_for("/home/user", sysinfo.read_mountinfo(self.mountinfo(
            ("/", "ext4", "/dev/x"), ("/home", "ext4", "/dev/y"))))["target"], "/home")

    def test_without_uuid_label_then_device_name(self):
        result = self.report(["/data", "/other"],
                             ("/", "ext4", os.path.join(self.dev, "nvme0n1p2")),
                             ("/data", "ext4", os.path.join(self.dev, "sdb1")),
                             ("/other", "xfs", os.path.join(self.dev, "nvme0n1p7x")))
        data, other = result["mounts"]
        self.assertEqual(data["sensorId"], "Dane robocze")
        self.assertEqual(other["sensorId"], "nvme0n1p7x")

    def test_mapper_device_resolves_through_symlink(self):
        result = self.report(["/home"], ("/", "ext4", os.path.join(self.dev, "nvme0n1p2")),
                             ("/home", "ext4", os.path.join(self.dev, "mapper", "crypt")))
        self.assertEqual(result["mounts"][0]["sensorId"], "aaaa-bbbb")

    def test_escaped_mount_points(self):
        mounts = sysinfo.read_mountinfo(self.mountinfo(("/", "ext4", "/dev/a"), ("/mnt/My\\040Disk", "ext4", "/dev/b")))
        self.assertEqual(mounts[1]["target"], "/mnt/My Disk")
        self.assertEqual(sysinfo.mount_for("/mnt/My Disk/x", mounts)["target"], "/mnt/My Disk")
        self.assertEqual(sysinfo.mount_for("/mnt/My Diskette", mounts)["target"], "/")


class ProcessesTest(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.mkdtemp(prefix="tide-sysinfo-proc-")
        self.addCleanup(shutil.rmtree, self.proc)

    def add(self, pid, resident, shared, argv, comm):
        base = os.path.join(self.proc, str(pid))
        write(os.path.join(base, "statm"), f"999999 {resident} {shared} 1 0 1 0\n")
        write(os.path.join(base, "cmdline"), b"\0".join(a.encode() for a in argv) + (b"\0" if argv else b""), "wb")
        write(os.path.join(base, "comm"), comm + "\n")

    def test_groups_private_memory_by_program(self):
        self.add(10, 300, 100, ["/usr/lib/firefox/firefox-bin"], "firefox-bin")
        self.add(11, 250, 50, ["/usr/lib/firefox/firefox-bin", "-contentproc"], "Isolated Web Co")
        self.add(12, 120, 20, ["/opt/google/chrome/chrome --type=renderer --lang=pl"], "chrome")
        self.add(13, 450, 100, ["dotnet", "build"], "dotnet")
        self.add(14, 0, 0, [], "kworker/0:1")                   # wątek jądra
        self.add(15, 40, 10, [], "Xwayland")                     # bez wiersza poleceń → comm
        self.add(16, 10, 10, ["sshd: user@pts/0"], "sshd")       # bez pamięci prywatnej
        write(os.path.join(self.proc, "self", "statm"), "1 1 1\n")  # nie liczba — pomijane
        result = sysinfo.top_processes(3, self.proc, page_size=1000)
        self.assertEqual(result["processes"], [
            {"name": "firefox", "bytes": 400_000, "count": 2},
            {"name": "dotnet", "bytes": 350_000, "count": 1},
            {"name": "chrome", "bytes": 100_000, "count": 1},
        ])
        everything = sysinfo.top_processes(10, self.proc, page_size=1000)["processes"]
        self.assertIn({"name": "Xwayland", "bytes": 30_000, "count": 1}, everything)
        self.assertNotIn("kworker/0:1", [p["name"] for p in everything])
        self.assertNotIn("sshd", [p["name"] for p in everything])

    def test_vanished_process_is_skipped(self):
        self.add(20, 100, 0, ["a"], "a")
        os.makedirs(os.path.join(self.proc, "21"))  # zniknął przed odczytem
        self.assertEqual(sysinfo.top_processes(3, self.proc, page_size=1)["processes"],
                         [{"name": "a", "bytes": 100, "count": 1}])


class InfoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-sysinfo-info-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home, self.system = os.path.join(self.tmp, "home"), os.path.join(self.tmp, "usr")
        self.empty_path = os.path.join(self.tmp, "bin")
        os.makedirs(self.empty_path)

    def test_apps_and_ksystemstats_detection(self):
        with mock.patch.dict(os.environ, {"PATH": self.empty_path}):
            bare = sysinfo.info([self.home, self.system])
        self.assertFalse(bare["ksystemstats"])
        self.assertEqual(bare["apps"], {"systemMonitor": "", "diskUsage": ""})
        self.assertTrue(bare["hostname"])

        write(os.path.join(self.system, "dbus-1", "services", "org.kde.ksystemstats1.service"), "")
        write(os.path.join(self.system, "applications", "org.kde.ksysguard.desktop"), "")
        write(os.path.join(self.home, "applications", "org.kde.filelight.desktop"), "")
        with mock.patch.dict(os.environ, {"PATH": self.empty_path}):
            found = sysinfo.info([self.home, self.system])
        self.assertTrue(found["ksystemstats"])
        self.assertEqual(found["apps"], {"systemMonitor": "org.kde.ksysguard", "diskUsage": "org.kde.filelight"})

        write(os.path.join(self.system, "applications", "org.kde.plasma-systemmonitor.desktop"), "")
        self.assertEqual(sysinfo.info([self.home, self.system])["apps"]["systemMonitor"], "org.kde.plasma-systemmonitor")

    def test_cli_on_real_system(self):
        result = subprocess.run(["/usr/bin/python3", SYSINFO, "mounts", "/", "/home"], check=True,
                                capture_output=True, text=True)
        mounts = json.loads(result.stdout)["mounts"]
        self.assertEqual(mounts[0]["target"], "/")
        for mount in mounts:
            self.assertTrue(mount["sensorId"])
            self.assertGreater(mount["size"], 0)
        top = json.loads(subprocess.run(["/usr/bin/python3", SYSINFO, "top", "2"], check=True,
                                        capture_output=True, text=True).stdout)
        self.assertLessEqual(len(top["processes"]), 2)
        self.assertEqual(subprocess.run(["/usr/bin/python3", SYSINFO, "bogus"], capture_output=True).returncode, 2)


class StageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-system-stage-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), SYSTEM_DIR, self.pkg],
                       check=True, capture_output=True)

    def test_package_contains_widget_helper_and_library(self):
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/SystemSource.qml",
                     "contents/ui/tide/DockTile.qml", "contents/ui/tide/Texts.qml",
                     "contents/ui/CompactSystem.qml", "contents/ui/FullSystem.qml", "contents/ui/ActionButton.qml",
                     "contents/code/sysinfo.py", "contents/code/system.js",
                     "contents/ui/tide/Ring.qml", "contents/ui/tide/BarChart.qml", "contents/ui/tide/History.qml",
                     "contents/ui/tide/MeterBar.qml", "contents/ui/tide/Tokens.qml"):
            self.assertIn(name, files)
        self.assertNotIn("contents/code/Config.js", files)
        with open(os.path.join(self.pkg, "metadata.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["KPlugin"]["Id"], WIDGET_ID)

    def test_qml_has_no_hardcoded_colors(self):
        for base in (SYSTEM_DIR, os.path.join(WIDGETS_DIR, "lib")):
            for dirpath, _dirs, names in os.walk(base):
                for name in names:
                    if name.endswith((".qml", ".js")):
                        with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                            self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)

    def test_rings_and_charts_come_from_shared_library(self):
        with open(os.path.join(SYSTEM_DIR, "contents/ui/CompactSystem.qml"), encoding="utf-8") as fh:
            compact = fh.read()
        # W docku mierniki z procentem obok podpisu zamiast małych pierścieni.
        self.assertIn("Tide.TileMeter", compact)
        self.assertNotIn("Tide.Ring", compact)
        with open(os.path.join(SYSTEM_DIR, "contents/ui/FullSystem.qml"), encoding="utf-8") as fh:
            full = fh.read()
        for component in ("Tide.BarChart", "Tide.MeterBar", "Tide.Card"):
            self.assertIn(component, full)
        with open(os.path.join(SYSTEM_DIR, "contents/ui/SystemSource.qml"), encoding="utf-8") as fh:
            self.assertIn("Tide.History", fh.read())


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-system-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.plasmoids = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids")

    def make(self, target):
        return subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True,
                              capture_output=True, text=True)

    def test_install_update_uninstall(self):
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.make("install-widget-system")
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        installed = os.path.join(self.plasmoids, WIDGET_ID)
        self.assertIn("contents/code/sysinfo.py", files_under(installed))
        self.make("install-widget-system")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")

        os.makedirs(os.path.join(self.plasmoids, "org.tide.agents"))
        self.make("uninstall-widget-system")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(os.listdir(self.plasmoids), ["org.tide.agents"])

    def test_part_of_make_install_and_uninstall(self):
        targets = subprocess.run(["make", "-s", "-C", REPO, "-p", "-n", "help"], env=self.env,
                                 capture_output=True, text=True).stdout
        install = re.search(r"^INSTALL_TARGETS :?= (.*)$", targets, re.M).group(1).split()
        uninstall = re.search(r"^UNINSTALL_TARGETS :?= (.*)$", targets, re.M).group(1).split()
        self.assertIn("install-widget-system", install)
        self.assertIn("uninstall-widget-system", uninstall)


FAKE_HELPER = r'''import json, sys
with open(sys.argv[1], "a") as log:
    log.write(" ".join(sys.argv[2:]) + "\n")
command = sys.argv[2]
if command == "info":
    out = {"hostname": "test-host", "ksystemstats": False,
           "apps": {"systemMonitor": "org.kde.plasma-systemmonitor", "diskUsage": ""}}
elif command == "mounts":
    out = {"mounts": [
        {"target": "/", "source": "/dev/a", "sensorId": "tide-test-none-a", "size": 100000000000,
         "free": 9000000000, "usedPercent": 91.0},
        {"target": "/home", "source": "/dev/b", "sensorId": "tide-test-none-b", "size": 800000000000,
         "free": 400000000000, "usedPercent": 50.0}],
        "targets": ["/", "/home", "/tmp"]}
elif command == "top":
    out = {"processes": [{"name": "rider", "bytes": 4400000000, "count": 3}, {"name": "firefox", "bytes": 3800000000, "count": 9}]}
else:
    sys.exit(2)
print(json.dumps(out))
'''

FAKE_LAUNCHER = """#!/bin/bash
echo "kstart $*" >> "$TIDE_FAKE_LOG"
"""


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    """Komponenty bez PlasmoidItem w qml6 offscreen: logika, bufor historii, widok bez ksystemstats."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-system-qml-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), SYSTEM_DIR, self.pkg],
                       check=True, capture_output=True)
        self.log = os.path.join(self.tmp, "calls.log")

    def run_qml(self, body, isolated_bus=True):
        harness = os.path.join(self.pkg, "contents/ui/Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(body)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1", TIDE_FAKE_LOG=self.log)
        if isolated_bus:
            # Ani ksystemstats, ani tide-daemon: magistrala sesji, której nie ma.
            env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + os.path.join(self.tmp, "no-bus")
        result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=60)
        problems = [line for line in result.stderr.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                                 r"|Cannot assign|Required property|is not defined|Invalid property", line)]
        self.assertEqual(problems, [], result.stderr)
        match = re.search(r"RESULT (\{.*\})", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return json.loads(match.group(1))

    def test_logic(self):
        result = self.run_qml(r"""import QtQuick
import "../code/system.js" as Logic
Item {
    Component.onCompleted: {
        const fmt = b => (b / 1e9).toFixed(1) + " GB"
        const targets = ["/", "/home", "/tmp"]
        const trees = [
            { path: "/home/t/repo", isMain: true, size: 9e9, removable: false },
            { path: "/home/t/repo-wt1", isMain: false, size: 2e9, removable: true },
            { path: "/home/t/repo-wt2", isMain: false, size: 1e9, removable: false },
            { path: "/home/t/repo-wt3", isMain: false, size: -1, removable: true },
            { path: "/tmp/scratch-wt", isMain: false, size: 5e9, removable: true },
            { path: "/srv/wt", isMain: false, size: 3e9, removable: true }
        ]
        const home = { target: "/home", usedPercent: 86 }
        console.warn("RESULT " + JSON.stringify({
            fuller: Logic.fullerDisk([{ target: "/", usedPercent: 40 }, { target: "/home", usedPercent: 88 },
                                      { target: "/x", usedPercent: null }]).target,
            noDisks: Logic.fullerDisk([{ target: "/", usedPercent: null }]),
            mountTmp: Logic.mountFor("/tmp/scratch-wt", targets),
            mountHome: Logic.mountFor("/home", targets),
            mountPrefix: Logic.mountFor("/homeless/x", targets),
            usageHome: Logic.worktreeUsage(trees, "/home", targets),
            usageRoot: Logic.worktreeUsage(trees, "/", targets),
            hintHome: Logic.diskHint(home, trees, targets, fmt),
            hintRoot: Logic.diskHint({ target: "/", usedPercent: 91 }, trees, targets, fmt),
            hintNoDaemon: Logic.diskHint(home, null, targets, fmt),
            hintNoTrees: Logic.diskHint({ target: "/home", usedPercent: 90 }, [], targets, fmt),
            hintBelow: Logic.diskHint({ target: "/home", usedPercent: 85 }, trees, targets, fmt),
            gpu: [Logic.gpuLine(31.4, 0.6 * 1073741824, 8 * 1073741824), Logic.gpuLine(5, null, null),
                  Logic.gpuLine(null, 12.4 * 1073741824, 24 * 1073741824)],
            notes: [Logic.diskNote([home, { target: "/", usedPercent: 40 }], trees, targets, fmt),
                    Logic.diskNote([{ target: "/", usedPercent: 91 }], trees, targets, fmt),
                    Logic.diskNote([{ target: "/", usedPercent: 91 }], null, targets, fmt),
                    Logic.diskNote([{ target: "/", usedPercent: 80 }], trees, targets, fmt)],
            procs: Logic.processesLine([{ name: "rider", bytes: 4.1e9 }, { name: "firefox", bytes: 3.6e9 }], fmt),
            noProcs: Logic.processesLine([], fmt),
            caption: Logic.historyCaption(12.4, 48.6, 60),
            captionEmpty: Logic.historyCaption(null, null, 60),
            captionShort: Logic.historyCaption(1, 2, 30),
            percent: [Logic.percentText(33.5), Logic.percentText(null), Logic.fraction(150), Logic.fraction(null)],
            tips: [Logic.toolTip(false, 33.5, 61, { target: "/home", usedPercent: 88 }),
                   Logic.toolTip(true, null, null, { target: "/", usedPercent: 91 }),
                   Logic.toolTip(false, null, null, null)],
            quote: Logic.shellQuote("/a b/it's.py"),
            path: Logic.localPath("file:///home/t/My%20Dir/sysinfo.py"),
            parsed: Logic.parseOutput({ "exit code": 0, stdout: '{"a": 1}' }),
            failed: [Logic.parseOutput({ "exit code": 1, stdout: "{}" }), Logic.parseOutput({ "exit code": 0, stdout: "x" })]
        }))
        Qt.quit()
    }
}
""")
        self.assertEqual(result["fuller"], "/home")
        self.assertIsNone(result["noDisks"])
        self.assertEqual((result["mountTmp"], result["mountHome"], result["mountPrefix"]), ("/tmp", "/home", "/"))
        self.assertEqual(result["usageHome"], {"count": 3, "total": 3e9, "freeable": 2e9})
        self.assertEqual(result["usageRoot"], {"count": 1, "total": 3e9, "freeable": 3e9})
        self.assertEqual(result["hintHome"], "Powyżej 85%: worktrees zajmują 3.0 GB, z czego 2.0 GB można zwolnić")
        self.assertEqual(result["hintRoot"], "Powyżej 85%: worktrees zajmują 3.0 GB, z czego 3.0 GB można zwolnić")
        self.assertEqual(result["hintNoDaemon"], "Dysk prawie pełny")
        self.assertEqual(result["hintNoTrees"], "Dysk prawie pełny")
        self.assertEqual(result["hintBelow"], "")
        # Pasek boczny: karta graficzna w jednej linii i jedna krótka uwaga o pełnym dysku.
        self.assertEqual(result["gpu"], ["GPU 31% · VRAM 0,6/8 GB", "GPU 5%", "GPU — · VRAM 12/24 GB"])
        self.assertEqual(result["notes"], ["Dysk prawie pełny · worktrees zwolnią 2.0 GB",
                                           "Dysk prawie pełny · worktrees zwolnią 3.0 GB",
                                           "Dysk prawie pełny", ""])
        self.assertEqual(result["procs"], "najwięcej: rider 4.1 GB · firefox 3.6 GB")
        self.assertEqual(result["noProcs"], "")
        self.assertEqual(result["caption"], "ostatnia minuta · średnio 12% · szczyt 49%")
        self.assertEqual(result["captionEmpty"], "ostatnia minuta · zbieram dane")
        self.assertEqual(result["captionShort"], "ostatnie 30 s · średnio 1% · szczyt 2%")
        self.assertEqual(result["percent"], ["34%", "—", 1, None])
        self.assertEqual(result["tips"], ["Obciążenie procesora 34%, zajęta pamięć 61%, zajęty dysk /home 88%",
                                          "Zainstaluj pakiet ksystemstats · zajęty dysk / 91%",
                                          "Obciążenie procesora —, zajęta pamięć —, dysk —"])
        self.assertEqual(result["quote"], "'/a b/it'\\''s.py'")
        self.assertEqual(result["path"], "/home/t/My Dir/sysinfo.py")
        self.assertEqual(result["parsed"], {"a": 1})
        self.assertEqual(result["failed"], [None, None])

    def test_history_ring_buffer(self):
        result = self.run_qml("""import QtQuick
import "tide" as Tide
Item {
    Tide.History { id: h; capacity: 3 }
    Tide.BarChart { id: chart; width: 90; height: 40; slots: 5; values: [0.1, null, 0.5] }
    Component.onCompleted: {
        const steps = {}
        steps.empty = { samples: h.samples, average: h.average, peak: h.peak, latest: h.latest }
        h.push(10); h.push(20)
        steps.two = { samples: h.samples, average: h.average, count: h.count }
        h.push(30); h.push(40); h.push(undefined)
        steps.wrapped = { samples: h.samples, average: h.average, peak: h.peak, latest: h.latest, count: h.count }
        h.push(50); h.push(60); h.push(70); h.push(80)
        steps.again = { samples: h.samples }
        h.capacity = 5
        h.push(90)
        steps.grown = { samples: h.samples, count: h.count }
        h.capacity = 2
        steps.shrunk = { samples: h.samples, count: h.count }
        h.clear()
        steps.cleared = { samples: h.samples, average: h.average, count: h.count }
        steps.chart = { offset: chart.offset, gap: chart.gap, barWidth: chart.barWidth }
        console.warn("RESULT " + JSON.stringify(steps))
        Qt.quit()
    }
}
""")
        self.assertEqual(result["empty"], {"samples": [], "average": None, "peak": None, "latest": None})
        self.assertEqual(result["two"], {"samples": [10, 20], "average": 15, "count": 2})
        self.assertEqual(result["wrapped"], {"samples": [30, 40, None], "average": 35, "peak": 40,
                                             "latest": None, "count": 3})
        self.assertEqual(result["again"], {"samples": [60, 70, 80]})
        self.assertEqual(result["grown"], {"samples": [60, 70, 80, 90], "count": 4})
        self.assertEqual(result["shrunk"], {"samples": [80, 90], "count": 2})
        self.assertEqual(result["cleared"], {"samples": [], "average": None, "count": 0})
        self.assertEqual(result["chart"], {"offset": 2, "gap": 2, "barWidth": 16.4})

    def test_without_ksystemstats_shows_notice_and_disks_from_helper(self):
        helper = os.path.join(self.tmp, "fake_sysinfo.py")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write(FAKE_HELPER)
        launcher = os.path.join(self.tmp, "kstart")
        with open(launcher, "w", encoding="utf-8") as fh:
            fh.write(FAKE_LAUNCHER)
        os.chmod(launcher, 0o755)
        result = self.run_qml("""import QtQuick
import QtQuick.Window
import "tide" as Tide
Window {
    width: 420; height: 700; visible: true
    SystemSource {
        id: src
        helper: "/usr/bin/python3 '%s' '%s'"
        launcher: "'%s'"
        cpuSensorId: "tide-test/none"
        detailed: true
        readyTimeout: 300
    }
    Column {
        CompactSystem { id: compact; source: src }
        FullSystem { id: full; source: src; width: 400 }
    }
    Timer {
        interval: 2500; running: true
        onTriggered: {
            src.launch(src.apps.systemMonitor, "")
            src.launch(src.apps.diskUsage, "")
            src.launch("org.kde.filelight", "file:///home")
            console.warn("RESULT " + JSON.stringify({
                problem: src.problem, ready: src.sensorsReady, cpu: src.cpuPercent, mem: src.memoryPercent,
                info: src.info, disks: src.disks, fuller: src.fullerDisk.target,
                hints: src.disks.map(d => src.diskHint(d)), worktrees: src.worktrees, procs: src.processes,
                meters: compact.meters.map(m => [m.label, m.percent]),
                full: { notice: full.showNotice, text: full.noticeText, cpu: full.showCpu, memory: full.showMemory,
                        gpu: full.showGpu, disks: full.showDisks, monitor: full.showMonitorButton,
                        diskButton: full.showDiskButton, shown: full.shown },
                historyCount: src.history.count, capacity: src.history.capacity }))
            Qt.callLater(Qt.quit)
        }
    }
}
""" % (helper, self.log, launcher))
        self.assertEqual(result["problem"], "missing")
        self.assertFalse(result["ready"])
        self.assertIsNone(result["cpu"])
        self.assertIsNone(result["mem"])
        self.assertEqual(result["info"]["hostname"], "test-host")
        self.assertEqual([(d["target"], d["usedPercent"], d["free"], d["fromSensor"]) for d in result["disks"]],
                         [("/", 91.0, 9e9, False), ("/home", 50.0, 4e11, False)])
        self.assertEqual(result["fuller"], "/")
        self.assertEqual(result["hints"], ["Dysk prawie pełny", ""])
        self.assertIsNone(result["worktrees"])
        self.assertEqual(result["procs"][0]["name"], "rider")
        self.assertEqual(result["meters"], [["CPU", None], ["RAM", None], ["Dysk", 91.0]])
        self.assertEqual(result["full"], {"notice": True, "text": "Zainstaluj pakiet ksystemstats", "cpu": False,
                                          "memory": False, "gpu": False, "disks": True, "monitor": True,
                                          "diskButton": False, "shown": True})
        self.assertEqual((result["historyCount"], result["capacity"]), (0, 30))
        with open(self.log, encoding="utf-8") as fh:
            log = fh.read().splitlines()
        self.assertIn("info", log)
        self.assertIn("mounts / /home", log)
        self.assertIn("top 3", log)
        # Pusty identyfikator (brak Filelighta) niczego nie uruchamia.
        self.assertIn("kstart --application org.kde.plasma-systemmonitor", log)
        self.assertIn("kstart --application org.kde.filelight --url file:///home", log)
        self.assertEqual(len([line for line in log if line.startswith("kstart")]), 2)

    def test_matches_live_sensors(self):
        """Te same liczby co ksystemstats/Monitor systemu: pamięć z /proc/meminfo, dyski ze statvfs."""
        result = self.run_qml("""import QtQuick
SystemSource {
    id: src
    worktreesHint: false
    property int tries: 0
    Timer {
        interval: 500; running: true; repeat: true
        onTriggered: {
            src.tries++
            // Pierwsze wartości czujników bywają zastępcze (pamięć 0, dysk = rozmiar partycji),
            // więc czekamy co najmniej 3 s.
            const done = src.sensorsReady && src.memoryTotal > 0 && src.disks.length > 0
                && src.disks.every(d => d.fromSensor) && src.tries >= 6
            if (!done && src.tries < 24) return
            console.warn("RESULT " + JSON.stringify({ ready: done, memTotal: src.memoryTotal, cpu: src.cpuPercent,
                disks: src.disks.map(d => [d.target, d.usedPercent, d.sensorId]) }))
            Qt.quit()
        }
    }
}
""", isolated_bus=False)
        if not result["ready"]:
            self.skipTest("ksystemstats nie dostarcza danych (pakiet nie jest zainstalowany albo usługa nie działa)")
        with open("/proc/meminfo", encoding="ascii") as fh:
            mem_total = int(re.search(r"MemTotal:\s+(\d+) kB", fh.read()).group(1)) * 1024
        self.assertEqual(result["memTotal"], mem_total)
        self.assertGreaterEqual(result["cpu"], 0)
        for target, used_percent, _sensor in result["disks"]:
            st = os.statvfs(target)
            expected = (st.f_blocks - st.f_bavail) * 100.0 / st.f_blocks
            self.assertAlmostEqual(used_percent, expected, delta=0.5, msg=target)


if __name__ == "__main__":
    unittest.main()
