"""Widok Modele: wykonawcy i dostawcy (PT-60) na danych z pliku.

- backend.placement: co idzie lokalnie, a co do chmury — role (wg trybu raportu) i wykonawcy, zgodnie ze stanem
  usługi (serwer nie odpowiada, tryb raportu); wykonawca lokalny niedostępny to jasna informacja,
- ExecutorsPanel (QML): lista wykonawców i dostawców, dodanie / zmiana / usunięcie przez SetExecutor,
  RemoveExecutor, SetProvider, RemoveProvider (bez edycji plików), sprawdzenie pól przed wysłaniem, odmowa usługi
  przy kontrolce, token tylko w żądaniu zapisu (nigdy w stanie widoku), starsza usługa bez katalogu,
- zrzut okna Tide z katalogiem.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from .fixture import sample
from .test_view import load_backend, problems, run_qml, stage, warned


class PlacementTest(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.mod = load_backend()

    def rows(self, status):
        return {r["id"]: r for r in self.mod.decorate(status)["placement"]}

    def test_roles_follow_the_report_mode(self):
        local = self.rows(sample("local"))
        self.assertEqual((local["role:summary"]["where"], local["role:describe"]["where"]), ("local", "local"))
        self.assertIn("Ten komputer · qwen3:8b", local["role:summary"]["detail"])
        cloud = self.rows(sample("catalog"))
        self.assertEqual((cloud["role:summary"]["where"], cloud["role:describe"]["where"]), ("cloud", "cloud"))
        rules = sample("local")
        rules["report"]["summary"] = "rules"
        self.assertEqual(self.rows(rules)["role:summary"]["where"], "off")
        old = sample("local")
        del old["report"]
        self.assertEqual(self.rows(old)["role:summary"]["where"], "unknown", "stara usługa nie podaje trybu raportu")

    def test_executors_cloud_and_local(self):
        rows = self.rows(sample("catalog"))
        self.assertEqual([k for k in rows if k.startswith("executor:")][:3],
                         ["executor:Haiku", "executor:Sonnet", "executor:Opus"])
        self.assertEqual(rows["executor:Opus"]["where"], "cloud")
        self.assertEqual(rows["executor:Codex mocny"]["where"], "cloud")
        self.assertEqual(rows["executor:Groq mały"]["detail"], "codex · llama-3.1-8b · https://api.groq.example")
        self.assertEqual(rows["executor:Lokalny"]["where"], "local")
        self.assertIn("qwen3-coder:30b", rows["executor:Lokalny"]["detail"])
        self.assertTrue(rows["executor:Lokalny"]["ok"])

    def test_unavailable_local_executor_is_stated(self):
        rows = self.rows(sample("mac-offline"))
        local = rows["executor:Lokalny"]
        self.assertEqual(local["where"], "local")
        self.assertFalse(local["ok"])
        self.assertIn("nie odpowiada", local["error"])
        self.assertTrue(rows["executor:Opus"]["ok"], "chmura nie zależy od serwera lokalnego")
        no_agent = self.rows(sample("local"))["executor:Lokalny"]
        self.assertFalse(no_agent["ok"])
        self.assertIn("brak modelu", no_agent["error"])

    def test_old_service_has_no_catalog_rows(self):
        old = sample("local")
        for key in ("executors", "providers", "catalog"):
            del old[key]
        rows = self.mod.decorate(old)["placement"]
        self.assertEqual([r["id"] for r in rows], ["role:summary", "role:describe"])


class ExecutorsQmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.tmp = tempfile.mkdtemp(prefix="tide-models-exec-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        mod = load_backend()
        cls.data = {v: mod.decorate(sample(v)) for v in ("catalog", "local", "mac-offline")}
        old = sample("local")
        for key in ("executors", "providers", "catalog"):
            del old[key]
        cls.data["old"] = mod.decorate(old)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def harness(self, body: str) -> str:
        path = os.path.join(self.pkg, "views", "models", "qml", "ExecSmoke.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("%DATA%", json.dumps(self.data, ensure_ascii=False)))
        return run_qml(path)

    def test_catalog_editing(self):
        log = self.harness("""import QtQuick
Item {
    id: top
    width: 1500; height: 1800
    readonly property var d: %DATA%
    property var calls: []
    ModelsPage {
        id: page
        anchors.fill: parent
        status: top.d.catalog
        onRequested: (member, argument, tag) => top.calls.push(member + "(" + argument + ")@" + tag)
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) { const hit = find(child, name); if (hit) return hit }
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
    Timer { interval: 100; running: true; onTriggered: top.steps() }
    function steps() {
        const panel = find(page, "executorsPanel")
        console.warn("NAMES " + texts(panel, [], "executorName").join(","))
        console.warn("PLACE " + findAll(panel, "placementWhere", []).map(t => t.text).join(","))
        console.warn("LINE " + texts(find(panel, "executor_Groq mały"), [], "executorLine").join(""))
        // Zmiana klas istniejącego wykonawcy.
        panel.openExecutor("Sonnet")
        console.warn("OPENED " + find(panel, "exName").text + "|" + find(panel, "exModel").text + "|" + panel.exProvider + "|" + panel.classes.join("+"))
        panel.toggleClass("mocny")
        panel.saveExecutor()
        console.warn("SAVE1 " + top.calls.join(","))
        page.finish("executor:save", "SetExecutor", { ok: false, error: "wykonawca „Sonnet”: boom" })
        console.warn("SAVEERR " + texts(panel, [], "exSaveNote").join("") + "|" + panel.editingExecutor)
        page.finish("executor:save", "SetExecutor", { ok: true, error: "" })
        console.warn("SAVEOK " + panel.editingExecutor + "|" + page.busy["executor:save"])
        // Nowy: sprawdzenie pól przed wysłaniem, potem żądanie.
        top.calls = []
        panel.openExecutor("+")
        panel.saveExecutor()
        const empty = top.calls.length + "|" + texts(panel, [], "exProblem").join("")
        find(panel, "exName").text = "Opus"
        find(panel, "exModel").text = "opus"
        panel.saveExecutor()
        console.warn("NEWBAD " + empty + "|" + top.calls.length + "|" + texts(panel, [], "exProblem").join(""))
        find(panel, "exName").text = "Groq duży"
        find(panel, "exModel").text = "llama-70b"
        panel.exProvider = "groq"
        panel.exProgram = "codex"
        panel.classes = ["mocny"]
        panel.saveExecutor()
        console.warn("NEWOK " + top.calls.join(","))
        page.finish("executor:save", "SetExecutor", { ok: true, error: "" })
        // Lokalny bez modelu: puste pole to „jak agent lokalny”.
        top.calls = []
        panel.openExecutor("Lokalny")
        console.warn("LOCALFORM " + JSON.stringify(find(panel, "exModel").text) + "|" + panel.exEndpoint + "|" + find(panel, "exEndpoint").visible)
        panel.saveExecutor()
        console.warn("LOCALSAVE " + top.calls.join(","))
        // Usunięcie po potwierdzeniu.
        top.calls = []
        find(find(panel, "executor_Haiku"), "executorRemove").clicked()
        console.warn("CONFIRM " + find(find(panel, "executor_Haiku"), "executorRemoveConfirm").visible + "|" + top.calls.length)
        find(find(panel, "executor_Haiku"), "executorRemoveYes").clicked()
        page.finish("executor:Haiku:remove", "RemoveExecutor", { ok: true, error: "" })
        console.warn("REMOVED " + top.calls.join(",") + "|" + panel.removingExecutor)
        // Dostawca: sprawdzenie pól, żądanie z tokenem, odmowa usunięcia przy wykonawcach.
        top.calls = []
        panel.openProvider("+")
        find(panel, "prName").text = "openai"
        panel.saveProvider()
        const reserved = top.calls.length + "|" + texts(panel, [], "prProblem").join("")
        find(panel, "prName").text = "tanio"
        find(panel, "prUrl").text = "https://u:p@x"
        panel.saveProvider()
        const creds = top.calls.length
        find(panel, "prUrl").text = "https://api.tanio.example"
        find(panel, "prToken").text = "sekret-123"
        panel.apiKind = "anthropic"
        panel.saveProvider()
        console.warn("PROV " + reserved + "|" + creds + "|" + top.calls.join(","))
        page.finish("provider:save", "SetProvider", { ok: true, error: "" })
        console.warn("PROVDONE " + panel.editingProvider + "|" + JSON.stringify(find(panel, "prToken").text))
        top.calls = []
        find(find(panel, "provider_groq"), "providerRemove").clicked()
        page.finish("provider:groq:remove", "RemoveProvider", { ok: false, error: "dostawca „groq” ma wykonawców: Groq mały — usuń ich najpierw" })
        console.warn("PROVREMOVE " + top.calls.join(",") + "|" + texts(find(panel, "provider_groq"), [], "").filter(t => t.indexOf("usuń ich") >= 0).length)
        panel.openProvider("groq")
        console.warn("PROVEDIT " + find(panel, "prName").text + "|" + find(panel, "prName").enabled + "|" + find(panel, "prClearToken").visible + "|" + JSON.stringify(find(panel, "prToken").text))
        console.warn("STATEHASNOTOKEN " + (JSON.stringify(top.d.catalog).indexOf("sekret-123") < 0))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "NAMES"), "Haiku,Sonnet,Opus,Codex lekki,Codex standard,Codex mocny,Lokalny,Groq mały")
        self.assertEqual(warned(log, "PLACE"), ",".join(["Chmura"] * 8 + ["Lokalnie", "Chmura"]))
        self.assertEqual(warned(log, "LINE"), "codex · llama-3.1-8b · chmura")
        self.assertEqual(warned(log, "OPENED"), "Sonnet|sonnet|anthropic|standard")
        self.assertEqual(warned(log, "SAVE1"),
                         'SetExecutor({"name":"Sonnet","program":"claude","model":"sonnet","provider":"anthropic",'
                         '"endpoint":"","classes":["standard","mocny"],"oldName":"Sonnet"})@executor:save')
        self.assertEqual(warned(log, "SAVEERR"), "wykonawca „Sonnet”: boom|Sonnet")
        self.assertEqual(warned(log, "SAVEOK"), "|undefined")
        self.assertEqual(warned(log, "NEWBAD"),
                         "0|Podaj nazwę wykonawcy.|0|Wykonawca „Opus” już jest w katalogu.")
        self.assertEqual(warned(log, "NEWOK"),
                         'SetExecutor({"name":"Groq duży","program":"codex","model":"llama-70b","provider":"groq",'
                         '"endpoint":"","classes":["mocny"]})@executor:save')
        self.assertEqual(warned(log, "LOCALFORM"), '""||true')
        self.assertEqual(warned(log, "LOCALSAVE"),
                         'SetExecutor({"name":"Lokalny","program":"","model":"","provider":"local","endpoint":"",'
                         '"classes":["lokalny"],"oldName":"Lokalny"})@executor:save')
        self.assertEqual(warned(log, "CONFIRM"), "true|0")
        self.assertEqual(warned(log, "REMOVED"), "RemoveExecutor(Haiku)@executor:Haiku:remove|")
        self.assertEqual(warned(log, "PROV"),
                         '0|Ta nazwa jest zarezerwowana.|0|SetProvider({"name":"tanio","label":"","url":'
                         '"https://api.tanio.example","api":"anthropic","token":"sekret-123"})@provider:save')
        self.assertEqual(warned(log, "PROVDONE"), '|""')
        self.assertEqual(warned(log, "PROVREMOVE"), "RemoveProvider(groq)@provider:groq:remove|1")
        self.assertEqual(warned(log, "PROVEDIT"), 'groq|false|true|""')
        self.assertEqual(warned(log, "STATEHASNOTOKEN"), "true")

    def test_old_service_and_unavailable_local(self):
        log = self.harness("""import QtQuick
Item {
    id: top
    width: 1500; height: 1500
    readonly property var d: %DATA%
    ModelsPage { id: page; anchors.fill: parent; status: top.d.old }
    ModelsPage { id: down; anchors.fill: parent; status: top.d["mac-offline"] }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children) { const hit = find(child, name); if (hit) return hit }
        return null
    }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    Timer { interval: 100; running: true; onTriggered: top.steps() }
    function steps() {
        const panel = find(page, "executorsPanel")
        console.warn("OLD " + texts(panel, [], "catalogOld").join("") + "|" + find(panel, "executorAdd").visible + "|" + find(panel, "providerAdd").visible)
        const lost = find(down, "executorsPanel")
        console.warn("DOWN " + texts(lost, [], "placementError").join(" ¦ "))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "OLD"), "Ta wersja usługi nie ma katalogu wykonawców — zaktualizuj usługę Tide "
                                             "(make install-daemon).|false|false")
        self.assertIn("● niedostępny: wykonawca „Lokalny”: serwer „Mac-Studio” nie odpowiada", warned(log, "DOWN"))

    def test_grab_with_catalog(self):
        data = os.path.join(self.tmp, "catalog.json")
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(sample("catalog"), fh, ensure_ascii=False)
        png = os.path.join(self.tmp, "catalog.png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "models", "--data",
                                 data, "--grab", png, "--width", "1640", "--height", "1400"], env=env,
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(problems(result.stderr), [])
        self.assertGreater(os.path.getsize(png), 60_000)


if __name__ == "__main__":
    unittest.main()
