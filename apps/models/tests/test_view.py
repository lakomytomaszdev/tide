"""Widok Modele okna Tide (apps/models).

- pakiet okna Tide: moduł widoku w views/models (backend.py, QML i biblioteka z Tokens.qml); bez kolorów
  wpisanych na sztywno, teksty zawsze jako zwykły tekst,
- backend bez usługi: „Ten komputer” dla adresów lokalnych, problemy i plakietka (tylko to, czego Tide używa),
  dane z pliku (zapis wyłączony, nieznana metoda odrzucona),
- logika (models.js, qml6 offscreen): stany serwerów, role i przypięcie, kontekst, tryb raportu, podpowiedzi,
  pobierania, nazwa nowego serwera, żądania do usługi,
- ModelsPage na przykładowym Status(): zapisy idą sygnałem z tagiem, przycisk w trakcie wyłączony, wynik
  (także odmowa usługi) przy kontrolce, która go wywołała; formularz dodania sprawdza pola przed wysłaniem;
  ostrzeżenie, że zapisany token pójdzie pod nowy adres; usuwanie po potwierdzeniu; pusty stan bez usługi,
- backend na prywatnej magistrali z prawdziwym tide-daemon i atrapą Ollamy: Status, zapisy z wynikiem
  w replied, odmowa usługi, próba połączenia, plakietka; usługa bez /Models,
- zrzuty okna Tide na widoku Modele z --data.
Prawdziwej Ollamy ani ~/.config testy nie dotykają (własny models.json i XDG_CONFIG_HOME).
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import APP_DIR, REPO
from .fixture import sample
from daemon.tests.fakebin import files_under
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

QML_PROBLEMS = r"Unable to assign|TypeError|ReferenceError|is not a type|overrides a member|is not defined|" \
               r"Cannot read|unavailable|Invalid property|Binding loop|failed to load|Only binding to one"
TIDE_DIR = os.path.join(REPO, "apps", "tide")


def stage(out: str) -> str:
    subprocess.run(["/usr/bin/python3", os.path.join(REPO, "widgets", "stage.py"), "--app", TIDE_DIR, out], check=True)
    return out


def run_qml(path: str) -> str:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
    try:
        result = subprocess.run(["qml6", path], env=env, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return err + "\n<qml6: przekroczony czas>"
    return result.stderr


def warned(log: str, tag: str) -> str:
    match = re.search(rf"{tag} (.*)$", log, re.M)
    return match.group(1).strip() if match else f"<brak {tag}>\n{log[-3000:]}"


def problems(log: str) -> list:
    return [line for line in log.splitlines() if re.search(QML_PROBLEMS, line)]


def load_backend():
    spec = importlib.util.spec_from_file_location("tide_models_view", os.path.join(APP_DIR, "backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-models-pkg-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_package_contains_view_module_and_library(self):
        files = files_under(os.path.join(self.pkg, "views", "models"))
        for name in ("backend.py", "qml/ModelsView.qml", "qml/ModelsPage.qml", "qml/ServerCard.qml",
                     "qml/ServerForm.qml", "qml/RoleRow.qml", "qml/ModelPicker.qml", "qml/ServerModels.qml",
                     "qml/models.js", "qml/tide/Tokens.qml", "qml/tide/Theme.qml", "qml/tide/qmldir"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])
        with open(os.path.join(self.pkg, "views.json"), encoding="utf-8") as fh:
            views = json.load(fh)["views"]
        view = next(v for v in views if v["id"] == "models")
        self.assertEqual((view["title"], view["module"], view["qml"]), ("Modele", "apps/models", "ModelsView.qml"))
        self.assertEqual(views[-1]["id"], "models", "Modele na końcu paska: Ctrl+1…5 zostają bez zmian")

    def test_no_hardcoded_colors(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)

    def test_texts_are_plain(self):
        """Nazwy serwerów i modeli, komunikaty usługi — nigdy jako HTML."""
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            if not name.endswith(".qml"):
                continue
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                source = fh.read()
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", "\n" + source, re.S):
                if "text:" in body:
                    self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")
        for name in ("Note.qml", "SectionTitle.qml", "LinkText.qml", "FieldLabel.qml"):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertIn("textFormat: Text.PlainText", fh.read(), name)


class BackendDataTest(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.mod = load_backend()

    def test_server_labels(self):
        m = self.mod
        for url in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "https://127.3.2.1"):
            self.assertTrue(m.is_local_url(url), url)
        for url in ("http://mac-studio:11434", "http://10.20.1.40:11434", "http://[fd7a:115c::1]:11434", "nie url"):
            self.assertFalse(m.is_local_url(url), url)
        self.assertEqual(m.server_label("local", "http://127.0.0.1:11434"), "Ten komputer")
        self.assertEqual(m.server_label("Mac-Studio", "http://mac-studio:11434"), "Mac-Studio")
        self.assertEqual(m.server_label("lm_studio", "http://mac-studio:1234"), "lm_studio", "nazwa z pliku bez przerabiania")
        two = {"local": {"url": "http://127.0.0.1:11434"}, "lm": {"url": "http://127.0.0.1:1234"}}
        self.assertEqual(m.server_label("lm", "http://127.0.0.1:1234", two), "Ten komputer · lm")
        status = m.decorate(sample("mac"))
        self.assertEqual({n: (e["label"], e["local"]) for n, e in status["endpoints"].items()},
                         {"local": ("Ten komputer", True), "Mac-Studio": ("Mac-Studio", False),
                          "lm-studio": ("lm-studio", False)})

    def test_problems_and_badge(self):
        m = self.mod
        texts = {v: [p["text"] for p in m.problems(sample(v))] for v in ("local", "mac-offline", "mac", "pulling",
                                                                         "errors", "checking")}
        self.assertEqual(texts["local"], [])
        self.assertEqual(texts["mac"], [])
        self.assertEqual(texts["checking"], [], "przed pierwszym odczytem nic nie jest problemem")
        self.assertEqual(texts["mac-offline"], ["Agent: serwer „Mac-Studio” nie odpowiada"],
                         "raport z Claude: serwer lokalny liczy się tylko dla agenta")
        self.assertEqual(texts["pulling"], ["Opisy kart: modelu gpt-oss:20b nie ma na serwerze „Ten komputer”"])
        self.assertEqual(texts["errors"], ["Używany serwer „Ten komputer” nie odpowiada"],
                         "role na tym serwerze nie są liczone drugi raz, Mac jeszcze niesprawdzony")
        self.assertEqual(m.badge(sample("errors")), ("● 1", "waiting"))
        self.assertEqual(m.badge(sample("local")), ("", "muted"))
        # Wyłączony serwer, z którego nic nie korzysta (raport z Claude, bez agenta) — bez plakietki.
        quiet = sample("errors")
        quiet["report"]["summary"] = "claude"
        self.assertEqual(m.badge(quiet), ("", "muted"))
        broken = sample("local")
        broken["config"]["error"] = "nie JSON"
        self.assertEqual(m.badge(broken), ("● 1", "waiting"))
        self.assertEqual(m.problems({}), [])

    def test_fixture_mode_refuses_writes(self):
        backend = self.mod.create(None, sample("mac-offline"))
        self.assertTrue(backend.available and backend.preview)
        status = json.loads(backend.statusJson)
        self.assertEqual(status["endpoints"]["Mac-Studio"]["label"], "Mac-Studio")
        self.assertEqual(len(status["problems"]), 1)
        self.assertEqual((backend.badgeText, backend.badgeTone), ("● 1", "waiting"))
        got = []
        backend.replied.connect(lambda *a: got.append(a))
        backend.request("SetDefault", "Mac-Studio", "server:Mac-Studio:default")
        backend.request("DeleteModel", "{}", "x")
        backend.refresh()
        backend.reload()
        self.assertEqual([(t, m, json.loads(r)["error"]) for t, m, r in got],
                         [("server:Mac-Studio:default", "SetDefault", "podgląd bez usługi — nic nie zmieniono"),
                          ("x", "DeleteModel", "nieznana operacja: DeleteModel")])
        empty = self.mod.create(None, None)
        self.assertEqual((empty.available, empty.badgeText), (False, ""))
        lost = self.mod.Backend(None)
        lost.replied.connect(lambda *a: got.append(a))
        lost.request("SetDefault", "local", "t")
        self.assertIn("nie odpowiedziała", json.loads(got[-1][2])["error"])


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.tmp = tempfile.mkdtemp(prefix="tide-models-qml-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        mod = load_backend()
        cls.data = {v: mod.decorate(sample(v)) for v in ("local", "mac-offline", "mac", "pulling", "errors",
                                                         "checking", "busy")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def harness(self, name: str, body: str) -> str:
        path = os.path.join(self.pkg, "views", "models", "qml", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("%DATA%", json.dumps(self.data, ensure_ascii=False)))
        return run_qml(path)

    def test_logic(self):
        log = self.harness("LogicSmoke.qml", """import QtQuick
import "models.js" as Logic
Item {
    readonly property var d: %DATA%
    Component.onCompleted: {
        const mac = d["mac-offline"]
        console.warn("SERVERS " + Logic.servers(d.mac).map(s => s.label + ":" + s.state.kind + (s.isDefault ? "*" : "")).join(","))
        console.warn("STATES " + [Logic.serverState(mac.endpoints["Mac-Studio"], true).text,
                                  Logic.serverState(d.errors.endpoints["Mac-Studio"], true).text,
                                  Logic.serverState(d.checking.endpoints.local, false).text,
                                  Logic.serverState(d.local.endpoints.local, true).text].join("|"))
        console.warn("FACTS " + Logic.serverFacts(Logic.server(d.mac, "Mac-Studio")) + "|"
                     + Logic.serverFacts(Logic.server(d.mac, "lm-studio")) + "|" + Logic.serverFacts(Logic.server(mac, "Mac-Studio")))
        console.warn("LOADED " + Logic.loadedLines(Logic.server(mac, "local")).map(l => l.name + ":" + l.slow + ":" + l.text).join(",")
                     + "|" + Logic.loadedSummary(Logic.server(d.mac, "local")) + "|" + Logic.loadedSummary(Logic.server(d.mac, "lm-studio")))
        console.warn("SUMMARY " + Logic.summaryText(d.mac))
        const rows = Logic.roleRows(mac)
        console.warn("ROLES " + rows.map(r => r.id + ":" + r.model + "@" + r.endpoint + (r.pinned ? "!" : "") + ":" + r.models.length).join(","))
        console.warn("WARN " + rows.map(r => Logic.roleWarning(r)).join("|") + "|" + Logic.roleWarning(Logic.roleRows(d.pulling)[1]))
        console.warn("AGENT " + JSON.stringify(Logic.roleRows(d.local)[2].exists) + ":" + Logic.roleRows(d.local)[2].endpoint)
        const explicit = JSON.parse(JSON.stringify(d.local))
        explicit.roles.summary.pinned = true
        const old = JSON.parse(JSON.stringify(d.local))
        delete old.roles.summary.pinned
        console.warn("PIN " + Logic.pinned(d.local, d.local.roles.summary) + "|" + Logic.pinned(explicit, explicit.roles.summary)
                     + "|" + Logic.pinned(old, old.roles.summary) + "|" + Logic.rolesWritable(old) + "|" + Logic.rolesWritable(d.local)
                     + "|" + Logic.pinOptions(mac).map(o => o.value + "=" + o.label).join(","))
        console.warn("CTX " + [Logic.ctxText("summary", 8192), Logic.ctxText("agent", 32768), Logic.ctxText("agent", null),
                               Logic.ctxPlaceholder("describe"), Logic.ctxPlaceholder("agent"),
                               Logic.ctxValue(""), Logic.ctxValue(" 16384 "), Logic.ctxValue("abc"), Logic.ctxValue("2000000")].map(String).join("|"))
        console.warn("ROLEREQ " + Logic.roleRequest("agent", "qwen3:8b", "", undefined) + " " + Logic.roleRequest("summary", "x", "local", null))
        console.warn("REPORT " + ["claude", "local", "rules"].map(m => Logic.reportText(d.mac, m)).join(" ¦ "))
        console.warn("REPORTLOCAL " + Logic.reportText(d.local, "local"))
        console.warn("NOTES " + Logic.reportNotes(d.errors).map(n => n.tone + ":" + n.text).join(" ¦ ") + " ¦ "
                     + Logic.reportNotes({}).length)
        console.warn("MODELS " + Logic.serverModels(d.mac, "Mac-Studio").map(m => m.name + ":" + m.size + ":" + m.loadedText + ":" + m.usedBy.join("+")).join(","))
        console.warn("HINTS " + Logic.suggestions(d.local, "local").map(s => s.model + (s.installed ? "✓" : "")).join(",")
                     + "|" + Logic.suggestions(d.mac, "Mac-Studio").map(s => s.model + (s.installed ? "✓" : "")).join(","))
        console.warn("PULLS " + Logic.pulls(d.pulling, "local").map(p => p.model + ":" + p.running + ":" + p.text + ":" + (p.fraction === null ? "-" : p.fraction.toFixed(2))).join(" ¦ "))
        console.warn("STAGES " + ["start", "pulling manifest", "verifying sha256 digest", "writing manifest", "pulling 24d7"].map(Logic.pullStage).join("|"))
        console.warn("SLUG " + ["Mac Studio", "  Mój Łódź-serwer ", "mac.local", "--x", "ąę"].map(Logic.slug).join("|"))
        console.warn("FORM " + [Logic.formProblem({ name: "", url: "" }, mac, true), Logic.formProblem({ name: "Nowy Mac", url: "x" }, mac, true),
                                Logic.formProblem({ name: "local", url: "http://a" }, mac, true),
                                Logic.formProblem({ name: "Nowy", url: "http://nowy:11434", token: "a b" }, mac, true),
                                Logic.formProblem({ name: "Nowy", url: "http://nowy:11434" }, mac, true)]
                               .map(p => p ? p.field + ":" + p.text : "ok").join("|"))
        console.warn("EPREQ " + Logic.endpointRequest({ name: "Mac Studio", url: " http://m:1 ", kind: "ollama", token: "t0k" }, true)
                     + " " + Logic.endpointRequest({ name: "local", url: "http://127.0.0.1:11434", kind: "openai", token: "x", clearToken: true }, false)
                     + " " + Logic.testRequest({ name: "Mac-Studio", url: "http://m:1", kind: "ollama", token: "" }, false)
                     + " " + Logic.testRequest({ name: "Nowy", url: "http://n:1", kind: "openai", token: "abc" }, true)
                     + " " + Logic.testRequest({ name: "Mac-Studio", url: "http://m:2", kind: "ollama", token: "", clearToken: true }, false))
        console.warn("TEST " + [Logic.testText({ ok: true, version: "0.40.11", models: 3 }), Logic.testText({ ok: true, version: null, models: 1 }),
                                Logic.testText({ ok: false, error: "serwer nie odpowiada" }), Logic.testText(null)].map(t => (t.error ? "!" : "") + t.text).join("|"))
        console.warn("BYTES " + [Logic.bytes(5225388164), Logic.bytes(65290196070), Logic.bytes(566700000), Logic.bytes(1157672605), Logic.bytes(-1)].join("|"))
        const odd = { queue: { local: { running: null, waiting: 3, other: [{ lane: "http://x:1", running: null, waiting: [] }, "zepsute"] },
                               mac: { running: { role: "embed", model: "bge-m3" }, waiting: [] } } }
        console.warn("QUEUE " + [Logic.queueLines(d.busy, "local"), Logic.queueLines(d.local, "local"),
                                 Logic.queueLines(odd, "local"), Logic.queueLines(odd, "mac"), Logic.queueLines(null, "local")]
                                .map(l => l.map(x => (x.other ? "~" : "") + x.text).join(" + ")).join(" | "))
        console.warn("PROBLEM " + Logic.problem(d.local, false).title + "|" + Logic.problem({ endpoints: {} }, true).title + "|" + Logic.problem(d.local, true))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "SERVERS"), "Ten komputer:online,Mac-Studio:online*,lm-studio:online")
        self.assertEqual(warned(log, "STATES"), "nie odpowiada|sprawdzam…|sprawdzam…|działa")
        self.assertEqual(warned(log, "FACTS"), "Ollama 0.41.2 · 5 modeli|zgodny z OpenAI · 2 modele|Ollama")
        self.assertEqual(warned(log, "LOADED"), "gpt-oss:20b:true:83% na karcie — reszta na procesorze, pisze wolniej|"
                                                "nic nie jest teraz załadowane|serwer nie mówi, co ma w pamięci")
        self.assertEqual(warned(log, "SUMMARY"), "Używany serwer: Mac-Studio · działa · Ollama 0.41.2 · 5 modeli")
        self.assertEqual(warned(log, "ROLES"), "summary:qwen3:8b@local:5,describe:qwen3:8b@local:5,"
                                               "agent:qwen3-coder:30b@Mac-Studio!:0")
        self.assertEqual(warned(log, "WARN"), "||Serwer „Mac-Studio” nie odpowiada — rola niedostępna|"
                                              "Modelu gpt-oss:20b nie ma na serwerze „Ten komputer”")
        self.assertEqual(warned(log, "AGENT"), "false:local")
        self.assertEqual(warned(log, "PIN"), "false|true|null|false|true|=Za domyślnym,local=Ten komputer,Mac-Studio=Mac-Studio")
        self.assertEqual(warned(log, "CTX"), "|32768||domyślny (8192)|domyślny (kontekst serwera)|null|16384|undefined|undefined")
        self.assertEqual(warned(log, "ROLEREQ"), '{"role":"agent","model":"qwen3:8b","endpoint":""} '
                                                 '{"role":"summary","model":"x","endpoint":"local","ctx":null}')
        report = warned(log, "REPORT").split(" ¦ ")
        self.assertIn("Anthropic", report[0])
        self.assertIn("limitów Claude", report[0])
        self.assertIn("na serwerze „Mac-Studio” (gpt-oss:120b)", report[1])
        self.assertIn("Nic nie jest wysyłane", report[2])
        self.assertIn("na tym komputerze. Tekst jest tu słabszy niż z Claude", warned(log, "REPORTLOCAL"))
        notes = warned(log, "NOTES")
        self.assertIn("warn:Usługa działa z opcją --report-summary local", notes)
        self.assertIn("warn:Model lokalny niedostępny — raport i karty dostaną opis z reguł, bez Claude.", notes)
        self.assertTrue(notes.endswith(" ¦ 1"), "bez `report` (np. --print-models) — jedna uwaga")
        self.assertEqual(warned(log, "MODELS"), "gpt-oss:120b:65,3 GB:w pamięci · 100% na karcie:Raport,qwen3:32b:20,2 GB::,"
                                                "qwen3-coder:30b:18,6 GB::Agent,qwen3:8b:5,2 GB:w pamięci · 100% na karcie:"
                                                "Opisy kart,bge-m3:1,2 GB::")
        self.assertEqual(warned(log, "HINTS"), "qwen3:8b✓,gpt-oss:20b✓,bge-m3✓|gpt-oss:120b✓,qwen3:32b✓,qwen3-coder:30b✓,qwen3:8b✓")
        self.assertEqual(warned(log, "PULLS"), "gpt-oss:20b:true:5,5 GB z 13,8 GB · 39%:0.40 ¦ llama-nie-ma:7b:false:"
                                               "Model lokalny: serwer odpowiedział 500: pull model manifest: file does not "
                                               "exist:-")
        self.assertEqual(warned(log, "STAGES"), "zaczynam…|pobieram opis modelu…|sprawdzam sumę kontrolną…|zapisuję…|pobieram…")
        self.assertEqual(warned(log, "SLUG"), "Mac-Studio|Moj-Lodz-serwer|mac.local|x|ae")
        self.assertEqual(warned(log, "FORM"), "name:podaj nazwę, np. Mac Studio|url:adres zaczyna się od http:// albo https://|"
                                              "name:serwer o tej nazwie już jest|token:token bez spacji|ok")
        self.assertEqual(warned(log, "EPREQ"), '{"name":"Mac-Studio","url":"http://m:1","kind":"ollama","token":"t0k"} '
                                               '{"name":"local","url":"http://127.0.0.1:11434","kind":"openai","clearToken":true} '
                                               '{"url":"http://m:1","kind":"ollama","name":"Mac-Studio"} '
                                               '{"url":"http://n:1","kind":"openai","token":"abc"} '
                                               '{"url":"http://m:2","kind":"ollama"}')
        self.assertEqual(warned(log, "TEST"), "Połączenie działa · Ollama 0.40.11 · 3 modele|Połączenie działa · 1 model|"
                                              "!serwer nie odpowiada|!brak odpowiedzi usługi")
        self.assertEqual(warned(log, "BYTES"), "5,2 GB|65,3 GB|567 MB|1,2 GB|")
        self.assertEqual(warned(log, "PROBLEM"), "Usługa tide-daemon nie odpowiada|Brak serwerów modeli|null")
        self.assertEqual(warned(log, "QUEUE"), "Pracuje: raport (qwen3:8b) · 2 w kolejce: opisy kart + "
                                               "~Na starym adresie 10.0.0.5:11434: pracuje: agent (qwen3-coder:30b) | "
                                               " | 3 w kolejce | Pracuje: wyszukiwanie (bge-m3) |",
                         "bez pola `queue` i przy bezczynnej kolejce nic; bezczynny i zepsuty wpis `other` pominięte")

    def test_server_card_shows_queue(self):
        """Karta serwera: linia kolejki (PT-16) i wywołania na starym adresie serwera (`other`)."""
        log = self.harness("QueueSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1000
    readonly property var d: %DATA%
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d.busy
    }
    function texts(item, name, out) {
        if (item.objectName === name && item.visible) out.push(item.text)
        for (const child of item.children) texts(child, name, out)
        return out
    }
    Timer {
        interval: 200
        running: true
        onTriggered: {
            console.warn("LINES " + texts(page, "queueLine", []).join(" + ") + " | " + texts(page, "queueOtherLine", []).join(" + "))
            page.status = top.d.local
            console.warn("IDLE " + texts(page, "queueLine", []).length + "|" + texts(page, "queueOtherLine", []).length)
            Qt.quit()
        }
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "LINES"), "Pracuje: raport (qwen3:8b) · 2 w kolejce: opisy kart | "
                                               "Na starym adresie 10.0.0.5:11434: pracuje: agent (qwen3-coder:30b)")
        self.assertEqual(warned(log, "IDLE"), "0|0")

    def test_page_requests_notes_forms_and_empty_state(self):
        log = self.harness("PageSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1100
    readonly property var d: %DATA%
    property var calls: []
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d["mac-offline"]
        onRequested: (member, argument, tag) => top.calls.push(member + "(" + argument + ")@" + tag)
        onRefreshRequested: top.calls.push("Refresh")
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    function findAll(item, name, out) {
        if (item.objectName === name) out.push(item)
        for (const child of item.children) findAll(child, name, out)
        return out
    }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    function card(name) {
        return findAll(page, "serverTitle", []).map(t => t.parent.parent.parent.parent).find(c => c.server && c.server.name === name)
    }
    Timer {
        interval: 100
        running: true
        onTriggered: top.steps()
    }
    function steps() {
        console.warn("SUMMARY " + texts(page, [], "summary").join(""))
        console.warn("ISSUES " + texts(page, [], "issue").join(" ¦ "))
        console.warn("TITLES " + texts(page, [], "serverTitle").join(","))
        const mac = card("Mac-Studio"), local = card("local")
        console.warn("MARKS " + find(local, "defaultMark").visible + "|" + find(mac, "defaultMark").visible + "|"
                     + find(local, "useButton").visible + "|" + find(mac, "useButton").visible)
        // „Używaj tego serwera”: SetDefault z tagiem, przycisk w trakcie wyłączony, drugie kliknięcie nic nie wysyła.
        const use = find(mac, "useButton")
        use.clicked()
        use.clicked()
        console.warn("DEFAULT " + top.calls.join(",") + "|" + use.enabled + "|" + use.text)
        page.finish("server:Mac-Studio:default", "SetDefault", { ok: false, error: "default: serwer „Mac-Studio” jest pominięty w models.json — popraw go najpierw" })
        console.warn("DEFAULTNOTE " + texts(mac, [], "defaultNote").join("") + "|" + use.enabled)
        // Próba połączenia: wynik w karcie.
        top.calls = []
        find(local, "testLink").clicked()
        page.finish("server:local:test", "TestEndpoint", { ok: true, error: "", version: "0.40.11", models: 5 })
        console.warn("TEST " + top.calls.join(",") + "|" + texts(local, [], "testNote").join(""))
        // Usunięcie: potwierdzenie w karcie, odmowa usługi przy nim.
        top.calls = []
        page.askRemove("local")
        console.warn("REMOVEASK " + find(local, "removeConfirm").visible + "|" + top.calls.length)
        find(local, "removeButton").clicked()
        page.finish("server:local:remove", "RemoveEndpoint", { ok: false, error: "serwer „local” jest domyślny — najpierw ustaw inny serwer jako domyślny" })
        console.warn("REMOVE " + top.calls.join(",") + "|" + texts(local, [], "removeNote").join("") + "|" + page.removing)
        page.askRemove("Mac-Studio")
        find(mac, "removeButton").clicked()
        page.finish("server:Mac-Studio:remove", "RemoveEndpoint", { ok: true, error: "" })
        console.warn("REMOVED " + page.removing + "|" + top.calls[top.calls.length - 1])
        // Edycja: zmiana adresu serwera z tokenem — ostrzeżenie, że token pójdzie pod nowy adres; „Usuń token”.
        top.calls = []
        page.edit("Mac-Studio")
        const form = find(mac, "editForm")
        console.warn("EDIT " + form.visible + "|" + form.urlText + "|" + form.auth + "|" + find(form, "tokenMoves").visible)
        form.urlText = "http://10.20.1.40:11434"
        console.warn("MOVES " + find(form, "tokenMoves").visible + "|" + texts(form, [], "tokenMoves").join(""))
        form.test()
        page.finish("server:Mac-Studio:edit-test", "TestEndpoint", { ok: false, error: "serwer nie odpowiada" })
        find(form, "clearToken").clicked()
        console.warn("CLEARED " + find(form, "tokenMoves").visible + "|" + form.clearToken)
        form.test()
        form.save()
        console.warn("EDITREQ " + top.calls.join(","))
        page.finish("server:Mac-Studio:save", "SetEndpoint", { ok: false, error: "nazwa „qwen3” jest początkiem modelu roli summary" })
        console.warn("EDITERR " + texts(form, [], "saveNote").join("") + "|" + page.editing)
        page.finish("server:Mac-Studio:save", "SetEndpoint", { ok: true, error: "" })
        console.warn("EDITOK " + page.editing)
        // Dodanie: pola sprawdzone przed wysłaniem, „Sprawdź” bez zapisu, po zapisie formularz znika.
        top.calls = []
        page.openAdd()
        const add = find(page, "addForm")
        add.save()
        console.warn("ADDEMPTY " + top.calls.length + "|" + texts(add, [], "formProblem").join(""))
        add.nameText = "Mac Studio 2"
        add.urlText = "http://mac-studio:11434"
        add.test()
        page.finish("add:test", "TestEndpoint", { ok: false, error: "Model lokalny niedostępny: nie ma takiego serwera (mac-studio)" })
        console.warn("ADDTEST " + top.calls.join(",") + "|" + texts(add, [], "testNote").join(""))
        add.save()
        page.finish("add:save", "SetEndpoint", { ok: true, error: "" })
        console.warn("ADDED " + top.calls[top.calls.length - 1] + "|" + page.adding)
        // Tryb raportu i role.
        top.calls = []
        const report = find(page, "reportSwitch")
        report.picked("claude")
        report.picked("local")
        page.finish("report", "SetReportSummary", { ok: false, error: "raport: nie udało się zapisać report.json (PermissionError)" })
        console.warn("REPORT " + top.calls.join(",") + "|" + texts(page, [], "reportError").join(""))
        top.calls = []
        const agent = find(page, "role_agent")
        console.warn("AGENTROW " + texts(agent, [], "roleServer").join("") + "|" + texts(agent, [], "roleWarning").join(""))
        agent.setModel("qwen3:8b")
        agent.setPin("")
        const summary = find(page, "role_summary")
        summary.setModel("gpt-oss:20b")
        summary.advanced = true
        find(summary, "ctxField").text = "16384"
        summary.saveCtx()
        find(summary, "ctxField").text = "dużo"
        summary.saveCtx()
        console.warn("ROLES " + top.calls.join(",") + "|" + texts(summary, [], "ctxNote").join("") + "|" + page.advanced.summary)
        // Pobieranie: zła nazwa nie idzie do usługi, dobra — PullModel na wybrany serwer.
        top.calls = []
        const pulls = find(page, "pullField")
        pulls.text = "zła nazwa"
        find(page, "pullButton").clicked()
        pulls.text = "qwen3:14b"
        find(page, "pullButton").clicked()
        page.finish("pull:local", "PullModel", { ok: false, error: "na serwerze „local” trwa już pobieranie qwen3:8b — poczekaj, aż się skończy", key: "" })
        console.warn("PULL " + top.calls.join(",") + "|" + texts(page, [], "pullNote").join(""))
        // Otwarcie na serwerze i odświeżenie.
        console.warn("REVEAL " + page.reveal("Mac-Studio") + "|" + page.modelsServer + "|" + page.flashServer + "|" + page.reveal("nie-ma"))
        find(page, "refreshButton").clicked()
        console.warn("REFRESH " + top.calls[top.calls.length - 1])
        // Pobierania i plakietki w innym stanie.
        page.status = top.d.pulling
        console.warn("PULLROWS " + findAll(page, "pullRow", []).length + "|" + texts(page, [], "rolePull").join(""))
        // Bez usługi — spokojny pusty stan.
        page.serviceUp = false
        console.warn("NOSERVICE " + find(page, "problemCard").visible + "|" + texts(page, [], "problemTitle").join("") + "|" + page.flick.visible)
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "SUMMARY"), "Używany serwer: Ten komputer · działa · Ollama 0.40.11 · 5 modeli")
        self.assertEqual(warned(log, "ISSUES"), "● Agent: serwer „Mac-Studio” nie odpowiada")
        self.assertEqual(warned(log, "TITLES"), "Ten komputer,Mac-Studio")
        self.assertEqual(warned(log, "MARKS"), "true|false|false|true")
        self.assertEqual(warned(log, "DEFAULT"), "SetDefault(Mac-Studio)@server:Mac-Studio:default|false|Przełączam…")
        self.assertEqual(warned(log, "DEFAULTNOTE"), "default: serwer „Mac-Studio” jest pominięty w models.json — popraw go "
                                                     "najpierw|true")
        self.assertEqual(warned(log, "TEST"), 'TestEndpoint({"name":"local"})@server:local:test|'
                                              "Połączenie działa · Ollama 0.40.11 · 5 modeli")
        self.assertEqual(warned(log, "REMOVEASK"), "true|0")
        self.assertEqual(warned(log, "REMOVE"), "RemoveEndpoint(local)@server:local:remove|serwer „local” jest domyślny — "
                                                "najpierw ustaw inny serwer jako domyślny|local")
        self.assertEqual(warned(log, "REMOVED"), "|RemoveEndpoint(Mac-Studio)@server:Mac-Studio:remove")
        self.assertEqual(warned(log, "EDIT"), "true|http://mac-studio:11434|true|false")
        moves = warned(log, "MOVES")
        self.assertTrue(moves.startswith("true|⚠ Zapisany token zostanie wysłany pod nowy adres"), moves)
        self.assertEqual(warned(log, "CLEARED"), "false|true")
        self.assertEqual(warned(log, "EDITREQ"),
                         'TestEndpoint({"url":"http://10.20.1.40:11434","kind":"ollama","name":"Mac-Studio"})'
                         '@server:Mac-Studio:edit-test,'
                         'TestEndpoint({"url":"http://10.20.1.40:11434","kind":"ollama"})@server:Mac-Studio:edit-test,'
                         'SetEndpoint({"name":"Mac-Studio","url":"http://10.20.1.40:11434","kind":"ollama",'
                         '"clearToken":true})@server:Mac-Studio:save')
        self.assertEqual(warned(log, "EDITERR"), "nazwa „qwen3” jest początkiem modelu roli summary|Mac-Studio")
        self.assertEqual(warned(log, "EDITOK"), "")
        self.assertEqual(warned(log, "ADDEMPTY"), "0|podaj nazwę, np. Mac Studio")
        self.assertEqual(warned(log, "ADDTEST"), 'TestEndpoint({"url":"http://mac-studio:11434","kind":"ollama"})@add:test|'
                                                 "Model lokalny niedostępny: nie ma takiego serwera (mac-studio)")
        self.assertEqual(warned(log, "ADDED"), 'SetEndpoint({"name":"Mac-Studio-2","url":"http://mac-studio:11434",'
                                               '"kind":"ollama"})@add:save|false')
        self.assertEqual(warned(log, "REPORT"), "SetReportSummary(local)@report|raport: nie udało się zapisać report.json "
                                                "(PermissionError)")
        self.assertEqual(warned(log, "AGENTROW"), "na: Mac-Studio · przypięta|⚠ Serwer „Mac-Studio” nie odpowiada — rola "
                                                  "niedostępna")
        self.assertEqual(warned(log, "ROLES"),
                         'SetRole({"role":"agent","model":"qwen3:8b","endpoint":"Mac-Studio"})@role:agent,'
                         'SetRole({"role":"agent","model":"qwen3-coder:30b","endpoint":""})@role:agent:pin,'
                         'SetRole({"role":"summary","model":"gpt-oss:20b","endpoint":""})@role:summary,'
                         'SetRole({"role":"summary","model":"qwen3:8b","endpoint":"","ctx":16384})@role:summary:ctx|'
                         "kontekst: liczba od 0 do 1048576 albo puste (domyślny)|true")
        self.assertEqual(warned(log, "PULL"), 'PullModel({"endpoint":"local","model":"qwen3:14b"})@pull:local|'
                                              "na serwerze „local” trwa już pobieranie qwen3:8b — poczekaj, aż się skończy")
        self.assertEqual(warned(log, "REVEAL"), "true|Mac-Studio|Mac-Studio|false")
        self.assertEqual(warned(log, "REFRESH"), "Refresh")
        self.assertEqual(warned(log, "PULLROWS"), "2|Pobieranie gpt-oss:20b: 5,5 GB z 13,8 GB · 39%")
        self.assertEqual(warned(log, "NOSERVICE"), "true|Usługa tide-daemon nie odpowiada|false")

    def test_drafts_survive_status_updates(self):
        """Formularz edycji serwera i niezapisany kontekst roli nie wracają do zapisanych wartości, gdy przyjdzie
        nowy Status() (model się załadował, serwer wstał albo padł, odczyt po „Sprawdź”, nowy serwer)."""
        log = self.harness("DraftSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1100
    readonly property var d: %DATA%
    property var calls: []
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d["mac-offline"]
        onRequested: (member, argument, tag) => top.calls.push(member + "(" + argument + ")@" + tag)
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    function findAll(item, name, out) {
        if (item.objectName === name) out.push(item)
        for (const child of item.children) findAll(child, name, out)
        return out
    }
    function form() {
        return findAll(page, "editForm", []).find(f => f.visible) || null
    }
    function changed(base, fn) {
        const next = JSON.parse(JSON.stringify(base))
        fn(next)
        return next
    }
    Timer {
        interval: 100
        running: true
        onTriggered: top.steps()
    }
    function steps() {
        page.edit("Mac-Studio")
        Qt.callLater(() => {
            const f = form()
            f.urlText = "http://10.20.1.40:11434"
            find(f, "tokenField").text = "nowy-token"
            find(f, "tokenField").edited()
            const summary = find(page, "role_summary")
            summary.advanced = true
            find(summary, "ctxField").text = "16384"
            find(summary, "ctxField").edited()
            // Nowe stany z usługi: model załadowany, inna wersja, Mac wstał, potem nowy serwer.
            page.status = top.changed(top.d["mac-offline"], s => { s.endpoints.local.loaded = []; s.endpoints.local.version = "0.41.0" })
            page.status = top.changed(top.d["mac-offline"], s => { s.endpoints["Mac-Studio"].online = true; s.endpoints["Mac-Studio"].error = "" })
            page.status = top.changed(top.d.mac, s => {})
            Qt.callLater(() => {
                const g = form()
                console.warn("FORM " + (g !== null) + "|" + (g ? g.urlText + "|" + find(g, "tokenField").text : "") + "|" + page.editing)
                console.warn("CTX " + find(find(page, "role_summary"), "ctxField").text + "|" + find(page, "role_summary").advanced)
                g.save()
                console.warn("SAVE " + top.calls.join(","))
                Qt.quit()
            })
        })
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "FORM"), "true|http://10.20.1.40:11434|nowy-token|Mac-Studio")
        self.assertEqual(warned(log, "CTX"), "16384|true")
        self.assertEqual(warned(log, "SAVE"), 'SetEndpoint({"name":"Mac-Studio","url":"http://10.20.1.40:11434",'
                                              '"kind":"ollama","token":"nowy-token"})@server:Mac-Studio:save')

    def test_vanished_server_drops_edit_draft_and_removal(self):
        """Serwer znika z listy (usunięty gdzie indziej, ręczna edycja pliku): edycja, szkic z tokenem i potwierdzenie
        usunięcia znikają razem z nim — nie wracają, gdy serwer o tej nazwie pojawi się znowu."""
        log = self.harness("VanishSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1000
    readonly property var d: %DATA%
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d["mac-offline"]
    }
    function without(name) {
        const next = JSON.parse(JSON.stringify(top.d["mac-offline"]))
        delete next.endpoints[name]
        delete next.roles.agent
        return next
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            page.edit("Mac-Studio")
            page.keepDraft("server:Mac-Studio", { url: "http://x:1", kind: "ollama", token: "sekret", clearToken: false })
            page.status = top.without("Mac-Studio")
            console.warn("GONE " + page.editing + "|" + JSON.stringify(page.draft("server:Mac-Studio")))
            page.status = top.d["mac-offline"]
            console.warn("BACK " + page.editing + "|" + JSON.stringify(page.draft("server:Mac-Studio")))
            page.askRemove("Mac-Studio")
            page.status = top.without("Mac-Studio")
            console.warn("REMOVING " + page.removing)
            Qt.quit()
        }
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "GONE"), "|null")
        self.assertEqual(warned(log, "BACK"), "|null")
        self.assertEqual(warned(log, "REMOVING"), "")

    def test_role_pinning(self):
        """Z `pinned` z usługi: przypięcie do używanego serwera widać, model i kontekst wysyłają serwer przypięcia,
        „przełączenie przeniesie” tylko przy nieprzypiętych. Bez `pinned` (stara usługa) — tylko do odczytu."""
        log = self.harness("PinSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1100
    readonly property var d: %DATA%
    property var calls: []
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d.mac
        onRequested: (member, argument, tag) => top.calls.push(member + "(" + argument + ")@" + tag)
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) {
            const hit = find(child, name)
            if (hit) return hit
        }
        return null
    }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    Timer {
        interval: 100
        running: true
        onTriggered: top.steps()
    }
    function steps() {
        // Agent przypięty do Maca, który jest też używanym serwerem; opisy kart — za domyślnym.
        const agent = find(page, "role_agent"), describe = find(page, "role_describe")
        agent.advanced = true
        describe.advanced = true
        console.warn("LABELS " + texts(agent, [], "roleServer").join("") + "|" + texts(describe, [], "roleServer").join(""))
        console.warn("NOTES " + texts(agent, [], "pinNote").join("") + "|" + texts(describe, [], "pinNote").join(""))
        console.warn("SWITCH " + find(agent, "pinSwitch").current + "|" + find(describe, "pinSwitch").current)
        agent.setModel("qwen3:32b")
        find(agent, "ctxField").text = "65536"
        agent.saveCtx()
        describe.setModel("qwen3:32b")
        console.warn("CALLS " + top.calls.join(","))
        // Stara usługa bez `pinned`: role tylko do odczytu, żadnego SetRole.
        top.calls = []
        const old = JSON.parse(JSON.stringify(top.d.mac))
        for (const r in old.roles) delete old.roles[r].pinned
        page.status = old
        Qt.callLater(() => {
            const a = find(page, "role_agent")
            console.warn("OLD " + find(a, "modelPicker").enabled + "|" + find(a, "pinSwitch").enabled + "|"
                         + find(a, "ctxSave").enabled + "|" + texts(page, [], "rolesReadOnly").join("") + "|"
                         + texts(a, [], "roleServer").join(""))
            a.setModel("qwen3:8b")
            a.setPin("")
            a.saveCtx()
            find(page, "role_summary").setModel("qwen3:8b")
            console.warn("OLDCALLS " + top.calls.length)
            Qt.quit()
        })
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "LABELS"), "na: Mac-Studio · przypięta|na: Mac-Studio · za domyślnym")
        notes = warned(log, "NOTES").split("|")
        self.assertIn("zostaje na tym serwerze", notes[0])
        self.assertNotIn("przeniesie", notes[0])
        self.assertIn("przełączenie używanego serwera przeniesie", notes[1])
        self.assertEqual(warned(log, "SWITCH"), "Mac-Studio|")
        self.assertEqual(warned(log, "CALLS"),
                         'SetRole({"role":"agent","model":"qwen3:32b","endpoint":"Mac-Studio"})@role:agent,'
                         'SetRole({"role":"agent","model":"qwen3-coder:30b","endpoint":"Mac-Studio","ctx":65536})'
                         '@role:agent:ctx,'
                         'SetRole({"role":"describe","model":"qwen3:32b","endpoint":""})@role:describe')
        old = warned(log, "OLD").split("|")
        self.assertEqual(old[:3], ["false", "false", "false"])
        self.assertIn("zaktualizuj usługę Tide", old[3])
        self.assertEqual(old[4], "na: Mac-Studio")
        self.assertEqual(warned(log, "OLDCALLS"), "0")

    def test_view_routes_backend_and_preview(self):
        """ModelsView z atrapą backendu: prośby strony idą do backend.request, replied wraca do strony, railInfo,
        element sprzed pierwszego stanu, opcje podglądu."""
        log = self.harness("ViewSmoke.qml", """import QtQuick
Item {
    id: top
    width: 1500; height: 1000
    readonly property var d: %DATA%
    QtObject {
        id: fake
        property string statusJson: ""
        property bool available: true
        property bool preview: false
        property var calls: []
        signal replied(string tag, string member, string text)
        function request(member, argument, tag) { calls.push(member + "@" + tag) }
        function refresh() { calls.push("Refresh") }
    }
    ModelsView {
        id: view
        anchors.fill: parent
        backend: fake
        preview: ({ advanced: ["agent"], "note": "role:summary|rola „summary”: podaj nazwę modelu" })
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            console.warn("BEFORE " + view.railInfo + "|" + view.page.ready)
            view.showItem("Mac-Studio")
            fake.statusJson = JSON.stringify(top.d.mac)
            Qt.callLater(() => Qt.callLater(() => {
                console.warn("RAIL " + JSON.stringify(view.railInfo))
                console.warn("PENDING " + view.pendingItem + "|" + view.page.flashServer + "|" + view.page.modelsServer)
                console.warn("PREVIEW " + view.page.advanced.agent + "|" + view.page.notes["role:summary"].text)
                view.page.send("SetDefault", "local", "server:local:default")
                fake.replied("server:local:default", "SetDefault", JSON.stringify({ ok: false, error: "nie" }))
                console.warn("ROUTE " + fake.calls.join(",") + "|" + view.page.notes["server:local:default"].text)
                Qt.quit()
            }))
        }
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "BEFORE"), "null|false")
        self.assertEqual(json.loads(warned(log, "RAIL")), {"label": "Używany serwer", "value": "Mac-Studio",
                                                           "note": "mac-studio:11434 · działa · Ollama 0.41.2 · 5 modeli"})
        self.assertEqual(warned(log, "PENDING"), "|Mac-Studio|Mac-Studio")
        self.assertEqual(warned(log, "PREVIEW"), "true|rola „summary”: podaj nazwę modelu")
        self.assertEqual(warned(log, "ROUTE"), "SetDefault@server:local:default|nie")


@requires_dbus
class BackendOverDbusTest(unittest.TestCase):
    """Backend widoku na prywatnej magistrali z prawdziwym tide-daemon, atrapą Ollamy i własnym models.json
    oraz XDG_CONFIG_HOME (report.json) — prawdziwe ustawienia nie są czytane ani zapisywane."""

    def setUp(self):
        from daemon.tests.test_models_core import closed_port, ollama_routes, start_server
        self.tmp = tempfile.mkdtemp(prefix="tide-models-view-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.empty = os.path.join(self.tmp, "empty")
        os.makedirs(self.empty)
        self.config_home = os.path.join(self.tmp, "config")
        self.models = os.path.join(self.config_home, "tide", "models.json")
        os.makedirs(os.path.dirname(self.models))
        self.server = start_server(self, ollama_routes())
        self.server.routes[("GET", "/api/ps")] = (200, {"models": []})
        self.down = f"http://127.0.0.1:{closed_port()}"
        with open(self.models, "w", encoding="utf-8") as fh:
            json.dump({"default": "local", "endpoints": {"local": {"url": self.server.url}}}, fh)
        self.bus = PrivateBus("tide-models-view")
        self.addCleanup(self.bus.stop)
        self.mod = load_backend()

    def spawn(self, *args):
        self.bus.spawn_daemon("--claude-dir", self.empty, "--codex-dir", self.empty, "--workspace", self.empty,
                              "--no-processes", "--no-krunner", "--no-idle", "--no-limits", "--models-poll", "600", *args,
                              extra_env={"TIDE_MODELS_CONFIG": self.models, "XDG_CONFIG_HOME": self.config_home})

    def ask(self, backend, member, argument, tag, timeout=15):
        got = []
        backend.replied.connect(lambda t, m, r: got.append((t, m, json.loads(r))) if t == tag else None)
        backend.request(member, argument, tag)
        self.assertTrue(wait_until(lambda: got, timeout=timeout), f"{member}: brak odpowiedzi")
        return got[0]

    def status(self, backend):
        return json.loads(backend.statusJson or "{}")

    def test_status_writes_and_refusals(self):
        self.spawn()
        backend = self.mod.Backend(self.bus.bus)
        self.assertTrue(wait_until(lambda: self.status(backend).get("endpoints", {}).get("local", {}).get("online"),
                                   timeout=15), "Status() z usługi, po Refresh serwer odpowiada")
        self.assertTrue(backend.available)
        self.assertEqual(self.status(backend)["endpoints"]["local"]["label"], "Ten komputer")

        # Mac, który nie odpowiada: próba bez zapisu, potem dodanie z tokenem (nigdy nie wraca w stanie).
        tag, member, result = self.ask(backend, "TestEndpoint", json.dumps({"url": self.down, "kind": "ollama"}), "add:test")
        self.assertEqual((tag, member, result["ok"]), ("add:test", "TestEndpoint", False))
        self.assertIn("serwer nie odpowiada", result["error"])
        _t, _m, result = self.ask(backend, "SetEndpoint", json.dumps({"name": "Mac-Studio", "url": self.down,
                                                                      "kind": "ollama", "token": "sekret-123"}), "add:save")
        self.assertEqual(result, {"ok": True, "error": ""})
        self.assertTrue(wait_until(lambda: "Mac-Studio" in self.status(backend).get("endpoints", {}), timeout=5),
                        "stan od razu po zapisie, bez czekania na cykl")
        mac = self.status(backend)["endpoints"]["Mac-Studio"]
        # Atrapa „Maca” też jest na 127.0.0.1, więc to drugi serwer na tym komputerze — z nazwą.
        self.assertEqual((mac["label"], mac["auth"]), ("Ten komputer · Mac-Studio", True))
        self.assertEqual(self.status(backend)["endpoints"]["local"]["label"], "Ten komputer · local")
        self.assertNotIn("sekret-123", backend.statusJson)

        # Agent przypięty do Maca, który nie odpowiada → plakietka; przełączenie i odmowa usunięcia domyślnego.
        _t, _m, result = self.ask(backend, "SetRole", json.dumps({"role": "agent", "model": "qwen3-coder:30b",
                                                                  "endpoint": "Mac-Studio"}), "role:agent")
        self.assertEqual(result["ok"], True)
        self.assertTrue(wait_until(lambda: "agent" in self.status(backend).get("roles", {}), timeout=5))
        roles = self.status(backend)["roles"]
        self.assertEqual((roles["agent"]["pinned"], roles["summary"]["pinned"]), (True, False),
                         "usługa mówi, które role są przypięte — widok może je zmieniać")
        backend.refresh()
        self.assertTrue(wait_until(lambda: backend.badgeText == "● 1", timeout=15), backend.badgeText)
        self.assertEqual(backend.badgeTone, "waiting")
        _t, _m, result = self.ask(backend, "RemoveEndpoint", "local", "server:local:remove")
        self.assertEqual(result["ok"], False)
        self.assertIn("domyślny", result["error"])
        _t, _m, result = self.ask(backend, "RemoveEndpoint", "Mac-Studio", "server:Mac-Studio:remove")
        self.assertIn("agent", result["error"], "usługa odmawia, bo agent jest przypięty do Maca")
        _t, _m, result = self.ask(backend, "SetDefault", "Mac-Studio", "server:Mac-Studio:default")
        self.assertEqual(result["ok"], True)
        self.assertTrue(wait_until(lambda: self.status(backend).get("default") == "Mac-Studio", timeout=5))
        _t, _m, result = self.ask(backend, "SetReportSummary", "local", "report")
        self.assertEqual(result["ok"], True)
        self.assertTrue(wait_until(lambda: self.status(backend)["report"]["summary"] == "local", timeout=5))
        _t, _m, result = self.ask(backend, "TestEndpoint", json.dumps({"name": "local"}), "server:local:test")
        self.assertEqual((result["ok"], result["version"], result["models"]), (True, "0.40.11", 3))
        with open(self.models, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual((saved["default"], saved["roles"]["agent"]), ("Mac-Studio", "Mac-Studio:qwen3-coder:30b"))

    def test_service_without_models_object(self):
        """Usługa bez /Models (--no-models): backend niedostępny, plakietka ukryta, zapis kończy się błędem."""
        self.spawn("--no-models")
        backend = self.mod.Backend(self.bus.bus)
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual((backend.available, backend.badgeText), (False, ""))
        _t, _m, result = self.ask(backend, "SetDefault", "local", "x")
        self.assertEqual(result["ok"], False)
        self.assertIn("nie odpowiedziała", result["error"])


class GrabTest(unittest.TestCase):
    """Okno Tide na widoku Modele z danymi z pliku (bez magistrali) — także z formularzem i odmową usługi."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-models-grab-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def grab(self, name, variant, *argv, size=("1640", "940")):
        data = os.path.join(self.tmp, variant + ".json")
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(sample(variant), fh, ensure_ascii=False)
        png = os.path.join(self.tmp, name + ".png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "models", "--data",
                                 data, *argv, "--grab", png, "--width", size[0], "--height", size[1]], env=env,
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(problems(result.stderr), [])
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        return os.path.getsize(png)

    def test_grabs(self):
        self.assertGreater(self.grab("mac", "mac", "--select", "lm-studio"), 60_000)
        self.assertGreater(self.grab("add", "local", "--preview", "add=Mac-Studio", "--preview", "url=http://m:11434"),
                           60_000)
        self.assertGreater(self.grab("errors", "errors", "--preview", "remove=local", "--preview", "edit=Mac-Studio",
                                     "--preview", "note=server:local:remove|odmowa", size=("1380", "620")), 40_000)
        self.assertGreater(self.grab("pulling", "pulling", "--preview", "advanced=summary", "--preview",
                                     "advanced=agent"), 60_000)
        self.assertGreater(self.grab("busy", "busy"), 60_000)


if __name__ == "__main__":
    unittest.main()
