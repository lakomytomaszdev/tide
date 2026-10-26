"""Widżet Limity AI: pakiet z tokenem z konfiguracji usługi, instalacja, QML i podmiana w panelu.

kpackagetool6 i qdbus6 są atrapami (daemon/tests/fakebin.py), HOME i XDG_* są tymczasowe,
a usługę QuotaApp udaje lokalny serwer HTTP w wątku testu. Token jest losowy dla każdego testu.
"""
import http.server
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest

from . import REPO, WIDGETS_DIR
from daemon.tests.fakebin import calls, files_under, make_env, qdbus_args

WIDGET_ID = "org.tide.limits"
LIMITS_DIR = os.path.join(WIDGETS_DIR, "limits")
SWAP = os.path.join(REPO, "scripts", "swap-limits-widget.sh")

# Kształt odpowiedzi GET /status jak w działającej usłudze (backend/app.py w QuotaApp).
STATUS = {
    "chatgpt": {"session": {"remaining": None, "reset": None},
                "weekly": {"remaining": 91, "reset": "~poniedziałek 11:41"},
                "lastRead": "2026-10-26T16:41+01:00", "state": "ready"},
    "claude": {"session": {"remaining": 12, "reset": "poniedziałek 20:00"},
               "weekly": {"remaining": 97, "reset": "sobota 18:00"},
               "lastRead": "2026-10-26T16:42+01:00", "state": "ready"},
    "paused": False, "fullscreenKnown": True, "browserOpen": False, "busy": False,
}


def write_limity_config(config_home: str, token: str, port: int = 45123) -> str:
    folder = os.path.join(config_home, "limity-ai-widget")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "port": port, "dataDir": "/nonexistent"}, fh)
    os.chmod(path, 0o600)
    return path


def files_containing(root: str, needle: str) -> list[str]:
    found = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    if needle.encode() in fh.read():
                        found.append(path)
            except OSError:
                pass
    return found


def stage_env(config_home: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "LIMITY_CONFIG"}
    env["XDG_CONFIG_HOME"] = config_home
    return env


class StageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-stage-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config_home = os.path.join(self.tmp, "config")
        self.pkg = os.path.join(self.tmp, "pkg")
        self.token = "test-" + secrets.token_hex(12)

    def stage(self):
        return subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), LIMITS_DIR, self.pkg],
                              env=stage_env(self.config_home), check=True, capture_output=True, text=True)

    def test_package_has_tokens_and_private_config(self):
        write_limity_config(self.config_home, self.token, port=45999)
        self.stage()
        files = files_under(self.pkg)
        for name in ("metadata.json", "contents/ui/main.qml", "contents/ui/LimitsSource.qml",
                     "contents/ui/ServiceCard.qml", "contents/ui/CompactLimits.qml",
                     "contents/ui/tide/Tokens.qml", "contents/ui/tide/MeterBar.qml", "contents/code/Config.js"):
            self.assertIn(name, files)
        config_js = os.path.join(self.pkg, "contents/code/Config.js")
        self.assertEqual(stat.S_IMODE(os.stat(config_js).st_mode), 0o600)
        with open(config_js, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(".pragma library", text)
        self.assertIn("var port = 45999;", text)
        self.assertIn(f'var token = "{self.token}";', text)
        with open(os.path.join(self.pkg, "contents/ui/tide/Tokens.qml"), encoding="utf-8") as fh:
            self.assertIn('readonly property string name: "Mist"', fh.read())

    def test_missing_service_config_gives_empty_token(self):
        result = self.stage()
        self.assertIn("brak konfiguracji usługi Limity AI", result.stderr)
        with open(os.path.join(self.pkg, "contents/code/Config.js"), encoding="utf-8") as fh:
            self.assertIn('var token = "";', fh.read())

    def test_token_never_in_source_tree(self):
        self.assertFalse(os.path.exists(os.path.join(LIMITS_DIR, "contents/code/Config.js")))
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("widgets/*/contents/code/Config.js", fh.read())

    def test_agents_package_has_no_config(self):
        write_limity_config(self.config_home, self.token)
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"),
                        os.path.join(WIDGETS_DIR, "agents"), self.pkg],
                       env=stage_env(self.config_home), check=True, capture_output=True)
        self.assertNotIn("contents/code/Config.js", files_under(self.pkg))

    def test_qml_has_no_hardcoded_colors(self):
        for dirpath, _dirs, names in os.walk(LIMITS_DIR):
            for name in names:
                if name.endswith(".qml"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)


@unittest.skipUnless(shutil.which("make"), "brak make")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.token = "test-" + secrets.token_hex(12)
        write_limity_config(self.env["XDG_CONFIG_HOME"], self.token)
        self.plasmoids = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids")
        self.installed = os.path.join(self.plasmoids, WIDGET_ID)

    def make(self, target):
        return subprocess.run(["make", "-s", "-C", REPO, target], env=self.env, check=True,
                              capture_output=True, text=True)

    def test_install_keeps_token_only_in_installed_package(self):
        self.make("install-widget-limits")
        staged = os.path.join(self.env["BUILD_DIR"], "widgets", WIDGET_ID)
        self.assertEqual(calls(self.env), [f"kpackagetool6 -t Plasma/Applet -i {staged}"])
        config_js = os.path.join(self.installed, "contents/code/Config.js")
        self.assertEqual(stat.S_IMODE(os.stat(config_js).st_mode), 0o600)
        with open(config_js, encoding="utf-8") as fh:
            self.assertIn(self.token, fh.read())
        self.assertIn("contents/ui/tide/Tokens.qml", files_under(self.installed))
        # Złożona kopia w BUILD_DIR traci token zaraz po instalacji, repozytorium go nie widzi.
        self.assertTrue(os.path.isdir(staged))
        self.assertEqual(files_containing(self.env["BUILD_DIR"], self.token), [])
        self.assertEqual(files_containing(REPO, self.token), [])

        self.make("install-widget-limits")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -u {staged}")
        self.assertEqual(stat.S_IMODE(os.stat(config_js).st_mode), 0o600)

    def test_uninstall_removes_only_tide_limits(self):
        for other in ("org.limityai.widget", "org.tide.agents"):
            os.makedirs(os.path.join(self.plasmoids, other))
            with open(os.path.join(self.plasmoids, other, "metadata.json"), "w") as fh:
                fh.write("{}")
        self.make("install-widget-limits")
        self.make("uninstall-widget-limits")
        self.assertEqual(calls(self.env)[-1], f"kpackagetool6 -t Plasma/Applet -r {WIDGET_ID}")
        self.assertEqual(sorted(os.listdir(self.plasmoids)), ["org.limityai.widget", "org.tide.agents"])
        self.assertFalse(any("-r org.limityai.widget" in c for c in calls(self.env)))


class FakeService(http.server.ThreadingHTTPServer):
    """Udaje API QuotaApp: sprawdza X-Limity-Token i zapisuje zapytania."""

    def __init__(self, token):
        super().__init__(("127.0.0.1", 0), FakeHandler)
        self.token = token
        self.requests = []


class FakeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        self.server.requests.append((method, self.path, self.headers.get("X-Limity-Token")))
        if self.headers.get("X-Limity-Token") != self.server.token:
            return self.reply(403, {"error": "forbidden"})
        if method == "GET" and self.path == "/status":
            return self.reply(200, STATUS)
        if method == "POST" and (self.path == "/refresh" or self.path.startswith("/open?")):
            return self.reply(202, {"queued": True})
        return self.reply(404, {"error": "not found"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    """Komponenty bez PlasmoidItem ładują się w qml6 offscreen i rozmawiają z udawaną usługą."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-qml-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pkg = os.path.join(self.tmp, "pkg")
        subprocess.run(["/usr/bin/python3", os.path.join(WIDGETS_DIR, "stage.py"), LIMITS_DIR, self.pkg],
                       env=stage_env(os.path.join(self.tmp, "config")), check=True, capture_output=True)

    def run_qml(self, body):
        harness = os.path.join(self.pkg, "contents/ui/Harness.qml")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(body)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        result = subprocess.run(["qml6", harness], env=env, capture_output=True, text=True, timeout=30)
        problems = [line for line in result.stderr.splitlines()
                    if re.search(r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member"
                                 r"|Cannot assign|Required property|is not defined", line)]
        self.assertEqual(problems, [], result.stderr)
        return result.stderr

    def service(self, token):
        server = FakeService(token)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_components_load_without_errors(self):
        log = self.run_qml("""import QtQuick
import QtQuick.Layouts
import "tide" as Tide
Item {
    width: 400; height: 600
    LimitsSource { id: src; port: 1; token: "" }
    ColumnLayout {
        anchors.fill: parent
        CompactLimits { source: src }
        StatusNotice { source: src; Layout.fillWidth: true }
        ServiceCard { source: src; serviceKey: "chatgpt"; title: "ChatGPT"; hideEmptySession: true; Layout.fillWidth: true }
        ServiceCard { source: src; serviceKey: "claude"; title: "Claude"; sessionLabel: "Sesja (5 h)"; Layout.fillWidth: true }
        MetricRow { label: "x"; value: 12; reset: "jutro" }
        MetricRow { label: "y"; value: null }
    }
    Timer {
        interval: 200; running: true
        onTriggered: {
            console.warn("SMOKE " + JSON.stringify({ configured: src.configured, checked: src.checked,
                level: [Tide.Theme.levelColor(41), Tide.Theme.levelColor(40), Tide.Theme.levelColor(15),
                        Tide.Theme.levelColor(null)].map(String) }))
            Qt.quit()
        }
    }
}
""")
        result = json.loads(re.search(r"SMOKE (\{.*\})", log).group(1))
        self.assertFalse(result["configured"])
        self.assertTrue(result["checked"])
        with open(os.path.join(REPO, "theme/palette/mist.json"), encoding="utf-8") as fh:
            palette = json.load(fh)
        pastel, semantic = palette["pastel"], palette["semantic"]
        self.assertEqual(result["level"], [pastel[semantic["positive"]], pastel[semantic["neutral"]],
                                           pastel[semantic["negative"]], palette["text"]["muted"]])

    def source_harness(self, port, token, then="Qt.quit()"):
        return """import QtQuick
LimitsSource {
    id: src
    port: %d
    token: "%s"
    pollInterval: 0
    property int round: 0
    onUpdated: {
        round++
        console.warn("ROUND " + round + " " + JSON.stringify({
            available: available,
            gpt: panelValue("chatgpt"), claude: panelValue("claude"),
            gptSession: remaining("chatgpt", "session"), gptWeekly: remaining("chatgpt", "weekly"),
            claudeReset: reset("claude", "session"), read: lastRead("claude"),
            state: service("claude").state }))
        if (round === 1) { %s } else { Qt.quit() }
    }
    Timer { interval: 10000; running: true; onTriggered: Qt.quit() }
}
""" % (port, token, then)

    def rounds(self, log):
        return [json.loads(m) for m in re.findall(r"ROUND \d+ (\{.*\})", log)]

    def test_reads_status_and_posts_actions_with_token(self):
        token = "test-" + secrets.token_hex(8)
        server = self.service(token)
        log = self.run_qml(self.source_harness(server.server_address[1], token, then='src.open("claude")'))
        rounds = self.rounds(log)
        self.assertEqual(len(rounds), 2, log)
        self.assertEqual(rounds[0], {"available": True, "gpt": 91, "claude": 12, "gptSession": None,
                                     "gptWeekly": 91, "claudeReset": "poniedziałek 20:00", "read": "16:42",
                                     "state": "ready"})
        self.assertEqual(server.requests, [("GET", "/status", token), ("POST", "/open?service=claude", token),
                                           ("GET", "/status", token)])

    def test_refresh_posts_and_rereads(self):
        token = "test-" + secrets.token_hex(8)
        server = self.service(token)
        self.run_qml(self.source_harness(server.server_address[1], token, then="src.refresh()"))
        self.assertEqual([r[:2] for r in server.requests], [("GET", "/status"), ("POST", "/refresh"), ("GET", "/status")])

    def test_wrong_token_or_dead_service_is_unavailable(self):
        server = self.service("right")
        rounds = self.rounds(self.run_qml(self.source_harness(server.server_address[1], "wrong")))
        self.assertFalse(rounds[0]["available"])
        self.assertIsNone(rounds[0]["gpt"])
        port = server.server_address[1]
        server.shutdown()
        server.server_close()
        rounds = self.rounds(self.run_qml(self.source_harness(port, "right")))
        self.assertFalse(rounds[0]["available"])
        self.assertEqual(rounds[0]["state"], "checking")


class SwapScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-swap-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = make_env(self.tmp)
        self.script = subprocess.run(["bash", SWAP, "--print"], env=self.env, check=True,
                                     capture_output=True, text=True).stdout

    def test_script_targets_bottom_panel_and_both_widgets(self):
        self.assertIn('var OLD = "org.limityai.widget";', self.script)
        self.assertIn('var NEW = "org.tide.limits";', self.script)
        self.assertIn('panel.location !== "bottom"', self.script)
        self.assertIn("old.remove()", self.script)
        self.assertIn("panel.addWidget(NEW)", self.script)
        self.assertIn("added.index = index", self.script)
        self.assertEqual(calls(self.env), [])  # --print niczego nie wywołuje

    def test_refuses_without_installed_widget(self):
        result = subprocess.run(["bash", SWAP], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("make install-widget-limits", result.stderr)
        self.assertEqual(calls(self.env), [])

    def test_backs_up_then_sends_script_to_plasmashell(self):
        installed = os.path.join(self.env["XDG_DATA_HOME"], "plasma/plasmoids", WIDGET_ID)
        os.makedirs(installed)
        with open(os.path.join(installed, "metadata.json"), "w") as fh:
            fh.write("{}")
        appletsrc = os.path.join(self.env["HOME"], ".config/plasma-org.kde.plasma.desktop-appletsrc")
        os.makedirs(os.path.dirname(appletsrc))
        with open(appletsrc, "w") as fh:
            fh.write("[Containments][90]\nlocation=4\n")
        subprocess.run(["bash", SWAP], env=self.env, check=True, capture_output=True, text=True)
        evaluate = "qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript"
        self.assertEqual(calls(self.env), [evaluate, evaluate])  # panels() z kopii, potem podmiana
        self.assertEqual(qdbus_args(self.env)[-1], self.script.rstrip("\n"))
        backups = os.listdir(os.path.join(self.env["XDG_DATA_HOME"], "plasma-tide/backups"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].startswith("swap-limits-"))

    @unittest.skipUnless(shutil.which("node"), "brak node")
    def test_script_logic_on_simulated_panels(self):
        """Skrypt na atrapie API skryptów Plasmy: tylko dolny panel, ta sama pozycja, bez duplikatów."""
        simulation = """
const script = require("fs").readFileSync(0, "utf8");
let nextId = 200;
function panel(id, location, screen, types) {
    const p = { id, location, screen, currentConfigGroup: [], list: [] };
    p.widget = (wid, type) => {
        const w = { id: wid, type, index: undefined, removed: false };
        w.remove = () => { w.removed = true; p.list = p.list.filter(x => x !== w); };
        p.list.push(w);
        return w;
    };
    types.forEach(([wid, type]) => p.widget(wid, type));
    p.order = types.map(([wid]) => wid).join(";");
    p.widgets = type => p.list.filter(w => w.type === type);
    p.readConfig = (key, def) => p.currentConfigGroup.join("/") === "General" && key === "AppletOrder" ? p.order : def;
    p.addWidget = type => p.widget(nextId++, type);
    return p;
}
const bottom = panel(90, "bottom", 0, [[91, "org.kde.plasma.icontasks"], [97, "org.limityai.widget"], [98, "org.tide.agents"]]);
const top = panel(10, "top", 0, [[11, "org.limityai.widget"]]);
const lastOnly = panel(70, "bottom", 1, [[71, "x"], [72, "org.limityai.widget"]]);
const already = panel(60, "bottom", 2, [[61, "org.limityai.widget"], [62, "org.tide.limits"]]);
const out = [];
new Function("panels", "print", script)(() => [top, bottom, lastOnly, already], s => out.push(s));
const view = p => p.list.map(w => [w.id, w.type, w.index === undefined ? null : w.index]);
console.log(JSON.stringify({ top: view(top), bottom: view(bottom), lastOnly: view(lastOnly),
                             already: view(already), report: out.join("\\n") }));
"""
        result = subprocess.run(["node", "-e", simulation], input=self.script, capture_output=True,
                                text=True, check=True)
        state = json.loads(result.stdout)
        self.assertEqual(state["top"], [[11, "org.limityai.widget", None]])
        self.assertEqual(state["bottom"], [[91, "org.kde.plasma.icontasks", None], [98, "org.tide.agents", None],
                                           [200, "org.tide.limits", 1]])
        self.assertEqual(state["lastOnly"], [[71, "x", None], [201, "org.tide.limits", None]])
        self.assertEqual(state["already"], [[62, "org.tide.limits", None]])
        self.assertIn("org.limityai.widget #97 -> org.tide.limits #200 na pozycji 1", state["report"])


if __name__ == "__main__":
    unittest.main()
