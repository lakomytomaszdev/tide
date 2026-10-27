"""Widok Docker okna Tide (apps/docker).

- pakiet okna Tide: moduł widoku w views/docker (backend.py, QML i biblioteka z Tokens.qml); bez kolorów
  wpisanych na sztywno, teksty zawsze jako zwykły tekst,
- logika (docker.js, qml6 offscreen): grupy compose i „Zatrzymane”, testcontainers, szukanie, porty,
  stany słowami, liczba mnoga, podsumowanie, potwierdzenie i wynik porządków, wyniki akcji,
- DockerPage na przykładowych danych: akcje z wyłączonymi przyciskami i wynikiem (także błędem) w wierszu,
  akcje projektu, usuwanie i porządki tylko po potwierdzeniu, szuflada logów, otwarcie na elemencie,
  puste stany (usługa, Docker nie działa, brak uprawnień),
- DockerView z atrapą backendu: Stats() tylko na wierzchu, logi odświeżane tylko z otwartą szufladą,
  element sprzed pierwszej migawki, wyniki z backendu do strony, railInfo,
- backend: dane z pliku (akcje wyłączone), plakietka jak Badge() usługi, a na prywatnej magistrali
  z prawdziwym tide-daemon i atrapą Engine API — akcje, zadania (GroupAction, Prune) z wynikiem
  z ActionFinished, logi i brak Stats() na starcie,
- zrzut okna Tide na widoku Docker z --data.
Prawdziwego Dockera testy nie dotykają (DOCKER_HOST wskazuje atrapę albo nieistniejący socket).
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

from . import APP_DIR, REPO
from .fixture import data as fixture
from daemon.tests.fakebin import files_under
from daemon.tests.privatebus import PrivateBus, requires_dbus, wait_until

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.docker import snapshot as daemon_snapshot  # noqa: E402

NOW = 1_793_090_000
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
    except subprocess.TimeoutExpired as exc:  # wyjątek w harnessie przed Qt.quit() — pokaż, co zdążyło się wypisać
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return err + "\n<qml6: przekroczony czas>"
    return result.stderr


def warned(log: str, tag: str) -> str:
    match = re.search(rf"{tag} (.*)$", log, re.M)
    return match.group(1).strip() if match else f"<brak {tag}>\n{log[-3000:]}"


def problems(log: str) -> list:
    return [line for line in log.splitlines() if re.search(QML_PROBLEMS, line)]


def load_backend():
    spec = importlib.util.spec_from_file_location("tide_docker_view", os.path.join(APP_DIR, "backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-docker-pkg-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_package_contains_view_module_and_library(self):
        files = files_under(os.path.join(self.pkg, "views", "docker"))
        for name in ("backend.py", "qml/DockerView.qml", "qml/DockerPage.qml", "qml/ContainerRow.qml",
                     "qml/GroupSection.qml", "qml/LogsDrawer.qml", "qml/CleanupCard.qml", "qml/ResourcesCard.qml",
                     "qml/docker.js", "qml/tide/Tokens.qml", "qml/tide/Theme.qml", "qml/tide/qmldir"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("tests/") or f.endswith(("__init__.py", "install.sh"))])
        with open(os.path.join(self.pkg, "views.json"), encoding="utf-8") as fh:
            view = next(v for v in json.load(fh)["views"] if v["id"] == "docker")
        self.assertEqual((view["module"], view["qml"]), ("apps/docker", "DockerView.qml"))
        self.assertNotIn("placeholder", view)

    def test_no_hardcoded_colors(self):
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                self.assertEqual(re.findall(r'"#[0-9a-fA-F]{3,8}"', fh.read()), [], name)

    def test_texts_are_plain(self):
        """Nazwy kontenerów, obrazy, linie logów i błędy z Dockera — nigdy jako HTML."""
        for name in os.listdir(os.path.join(APP_DIR, "qml")):
            if not name.endswith(".qml"):
                continue
            with open(os.path.join(APP_DIR, "qml", name), encoding="utf-8") as fh:
                source = fh.read()
            for _indent, body in re.findall(r"\n(\s*)Text \{\n(.*?)\n\1\}", "\n" + source, re.S):
                if "text:" in body:
                    self.assertIn("textFormat: Text.PlainText", body, f"{name}: {body[:80]}")


@unittest.skipUnless(shutil.which("qml6"), "brak qml6")
class QmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-docker-qml-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.data = fixture(NOW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def harness(self, name: str, body: str) -> str:
        path = os.path.join(self.pkg, "views", "docker", "qml", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("%NOW%", str(NOW)).replace("%DATA%", json.dumps(self.data, ensure_ascii=False)))
        return run_qml(path)

    def test_logic(self):
        log = self.harness("LogicSmoke.qml", """import QtQuick
import "docker.js" as Logic
Item {
    readonly property var d: %DATA%
    function names(groups) { return groups.map(g => g.id + ":" + g.containers.map(c => c.name).join("+")).join(" | ") }
    Component.onCompleted: {
        const snap = d.snapshot
        const groups = Logic.displayGroups(snap, false, "")
        console.warn("GROUPS " + groups.map(g => g.id).join(","))
        console.warn("STOPPED " + groups[groups.length - 1].containers.map(c => c.name).join(","))
        console.warn("STATUS " + groups.map(g => Logic.groupStatus(g)).join("|"))
        console.warn("TC " + Logic.displayGroups(snap, true, "").map(g => g.id).filter(id => id === "testcontainers").length)
        console.warn("Q8080 " + names(Logic.displayGroups(snap, false, ":8080")))
        console.warn("QREPO " + names(Logic.displayGroups(snap, false, "dev-dashboard")))
        console.warn("QPG " + Logic.displayGroups(snap, false, "postgres 5434").map(g => g.containers.length).join(","))
        console.warn("QNONE " + Logic.displayGroups(snap, false, "nic-takiego").length)
        console.warn("SUMMARY " + Logic.summaryText(snap, d.stats))
        console.warn("SUMMARY0 " + Logic.summaryText({ summary: { running: 0, stopped: 1, failed: 1 }, groups: [] }, null))
        console.warn("SUMMARY2 " + Logic.summaryText({ summary: { running: 2, projects: 1, stopped: 3, failed: 2 },
                                                        groups: [] }, null))
        const api = Logic.findContainer(snap, "cookapp-api").container
        const bot = Logic.findContainer(snap, "translations-slack-bot").container
        const old = Logic.findContainer(snap, "app4-release-t-postgres-1").container
        const web = Logic.findContainer(snap, "app4-release-localhost-web-1").container
        console.warn("LINES " + [Logic.stateLine(api, %NOW%).text, Logic.stateLine(bot, %NOW%).text,
                                 Logic.stateLine(bot, %NOW%).tone, Logic.stateLine(old, %NOW%).text,
                                 Logic.stateLine(web, %NOW%).text].join(" | "))
        console.warn("PORTS " + Logic.ports(api).map(p => p.label + (p.http ? "@" + p.url : "")).join(",") + " "
                     + Logic.ports(Logic.findContainer(snap, "cookapp-postgres").container).map(p => p.label).join(","))
        console.warn("FIND " + [Logic.findContainer(snap, api.id.slice(0, 12)).container.name,
                                Logic.findContainer(snap, api.id.slice(0, 20)).container.name,
                                Logic.findContainer(snap, "nie-ma"), Logic.findGroup(snap, "cookapp").id,
                                Logic.shownGroupId(Logic.findContainer(snap, "translations-slack-bot"))].join("|"))
        const partial = { kind: "compose", running: 2, total: 3, failed: 1 }
        console.warn("ACTIONS " + Logic.groupActions(partial).map(a => a.label).join(",") + " | "
                     + Logic.groupActions({ kind: "compose", running: 3, total: 3 }).map(a => a.id).join(",") + " | "
                     + Logic.groupActions({ kind: "standalone", running: 1, total: 1 }).length + " | "
                     + Logic.groupStatus(partial))
        console.warn("PLURAL " + [Logic.count(1, "kontener", "kontenery", "kontenerów"), Logic.count(4, "kontener", "kontenery", "kontenerów"),
                                  Logic.count(12, "kontener", "kontenery", "kontenerów"), Logic.count(22, "kontener", "kontenery", "kontenerów"),
                                  Logic.tcLabel(1, false), Logic.tcLabel(3, false), Logic.tcLabel(5, false), Logic.tcLabel(2, true)].join("|"))
        console.warn("FORMAT " + [Logic.percent(0.04), Logic.percent(12.6), Logic.percent(null), Logic.bytes(2362232012),
                                  Logic.bytes(86016), Logic.uptime(30), Logic.uptime(13 * 3600), Logic.uptime(3 * 86400),
                                  Logic.ago(%NOW% - 21 * 3600, %NOW%), Logic.ago(%NOW% - 86400 - 5, %NOW%)].join("|"))
        const picks = { cache: true, volumes: true, stopped: true }
        const prune = Logic.pruneSummary(d.disk.categories, picks, snap)
        console.warn("PRUNE " + prune.map(p => p.id + "=" + p.size).join(",") + " | " + Logic.pickedIds(d.disk.categories, picks).join(",")
                     + " | " + Logic.bytes(Logic.pickedBytes(d.disk.categories, picks)))
        console.warn("PRUNEWARN " + prune.map(p => p.lines.filter(l => l.warn).map(l => l.text).join("/")).join(" ¦ "))
        console.warn("PRUNESTOP " + prune[2].lines[0].text)
        const paused = JSON.parse(JSON.stringify(snap))
        paused.groups[0].containers[0].running = false
        paused.groups[0].containers[0].state = "paused"
        console.warn("PAUSED " + Logic.stoppedNames(paused).indexOf("bocatalog-postgres") + "|"
                     + Logic.stateLine(paused.groups[0].containers[0], %NOW%).text)
        const lan = JSON.parse(JSON.stringify(snap))
        lan.groups[0].containers[0].ports = [{ hostIp: "10.20.1.5", hostPort: 8088, containerPort: 80, proto: "tcp",
                                               likelyHttp: true, url: "http://10.20.1.5:8088" }]
        console.warn("URLS " + [Logic.openableUrl(lan, "http://10.20.1.5:8088"), Logic.openableUrl(lan, "http://localhost:8080"),
                                Logic.openableUrl(lan, "http://192.168.1.6:8088"), Logic.openableUrl(lan, "file:///etc/passwd"),
                                Logic.openableUrl(snap, "http://localhost:8080/../x"), Logic.ports(lan.groups[0].containers[0])[0].http].join("|"))
        const anon = JSON.parse(JSON.stringify(d.disk.categories))
        anon[3].anonymousVolumes = 2
        console.warn("ANON " + Logic.pruneSummary(anon, { stopped: true }, snap)[0].lines.filter(l => l.warn).map(l => l.text).join("/"))
        const result = Logic.pruneResult({ ok: false, reclaimedBytes: 6335076761, details: [
            { id: "cache", label: "Pamięć podręczna budowania", ok: true, reclaimedBytes: 6335076761, removed: 121 },
            { id: "volumes", label: "Wolumeny bez kontenera", ok: false, error: "Docker właśnie porządkuje" }] })
        console.warn("RESULT " + result.title + " | " + result.lines.map(l => (l.error ? "!" : "") + l.text).join(" | "))
        console.warn("NOTES " + [Logic.actionNote("stop", { ok: true, note: "" }).text,
                                 Logic.actionNote("start", { ok: true, note: "już działał" }).text,
                                 Logic.actionNote("stop", { ok: false, error: "Docker nie pozwolił zatrzymać: boom" }).text,
                                 Logic.groupNote("restart", { ok: true, done: ["a", "b", "c"], failed: [] }).text,
                                 Logic.groupNote("stop", { ok: false, done: ["a"], failed: [{ name: "b", error: "nie" }] }).text,
                                 Logic.groupNote("start", { ok: true, done: [], failed: [], note: "wszystko już działa" }).text].join(" | "))
        console.warn("PROBLEM " + [Logic.problem(null, false).title, Logic.problem({ state: "starting" }, true).title,
                                   Logic.problem({ state: "denied", error: "brak uprawnień" }, true).text,
                                   Logic.problem(snap, true)].join(" | "))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "GROUPS"), "compose:bo-catalog,compose:cockpit,compose:agent-team,"
                         "compose:app4-release-localhost,compose:cookapp,compose:youtrack,standalone,stopped")
        self.assertEqual(warned(log, "STOPPED"), "translations-slack-bot,app4-hotfix-tom-142-web-1,app4-release-i-web-1,"
                         "app4-release-t-postgres-1", "padnięty pierwszy, potem od najświeższego")
        self.assertEqual(warned(log, "STATUS"), "1 z 1 działa|1 z 1 działa|1 z 1 działa|1 z 1 działa|3 z 3 działa|"
                         "1 z 1 działa||4 kontenery · 1 padł")
        self.assertEqual(warned(log, "TC"), "1")
        self.assertEqual(warned(log, "Q8080"), "compose:cookapp:cookapp-api")
        self.assertEqual(warned(log, "QREPO"), "compose:cockpit:cockpit-db", "szukanie po repozytorium projektu")
        self.assertEqual(warned(log, "QPG"), "1")
        self.assertEqual(warned(log, "QNONE"), "0")
        self.assertEqual(warned(log, "SUMMARY"), "9 kontenerów działa w 6 projektach i 1 bez compose · "
                         "4 zatrzymane, 1 z nich padł · CPU 3,0% · RAM 2,2 GB")
        self.assertEqual(warned(log, "SUMMARY0"), "Nic nie działa · 1 zatrzymany, padł")
        self.assertEqual(warned(log, "SUMMARY2"), "2 kontenery działają w 1 projekcie · 3 zatrzymane, 2 z nich padły")
        lines = warned(log, "LINES").split(" | ")
        self.assertEqual(lines[0], "cookapp-api · zdrowy · 13 h")
        self.assertEqual(lines[1], "translationsdownloader-slack-bot · zabity (kod 137), możliwy brak pamięci · 21 h temu")
        self.assertEqual(lines[2], "bad")
        self.assertEqual(lines[3], "postgres:16-alpine · zakończony 2 tyg. temu")
        self.assertEqual(lines[4], "app4-logviewer-localhost:log-value-condition · 1 h")
        self.assertEqual(warned(log, "PORTS"), ":8080@http://localhost:8080 5434→5432")
        self.assertEqual(warned(log, "FIND"), "cookapp-api|cookapp-api||compose:cookapp|stopped")
        self.assertEqual(warned(log, "ACTIONS"), "compose logs,Restart,Uruchom resztę,Zatrzymaj | logs,restart,stop | 0"
                         " | 2 z 3 działa · 1 padł")
        self.assertEqual(warned(log, "PLURAL"), "1 kontener|4 kontenery|12 kontenerów|22 kontenery|testcontainers: 1 ukryty|"
                         "testcontainers: 3 ukryte|testcontainers: 5 ukrytych|Ukryj testcontainers")
        self.assertEqual(warned(log, "FORMAT"), "0,0%|13%|—|2,2 GB|84 KB|teraz|13 h|3 dni|21 h temu|wczoraj")
        self.assertEqual(warned(log, "PRUNE"), "volumes=410 MB,cache=5,9 GB,stopped=84 KB | cache,volumes,stopped | 6,3 GB",
                         "kolejność usuwania jak w usłudze: obrazy, wolumeny, pamięć podręczna, kontenery")
        self.assertEqual(warned(log, "PRUNEWARN"), "mogą trzymać dane baz — usuniętych nie da się odzyskać ¦  ¦")
        self.assertEqual(warned(log, "PRUNESTOP"), "4 kontenery: app4-hotfix-tom-142-web-1, app4-release-i-web-1, "
                         "app4-release-t-postgres-1, translations-slack-bot")
        self.assertIn("2 kontenery mają wolumeny anonimowe", warned(log, "ANON"))
        self.assertEqual(warned(log, "PAUSED"), "-1|postgres:16 · wstrzymany", "wstrzymany nie trafia do porządków")
        self.assertEqual(warned(log, "URLS"), "true|true|false|false|false|true",
                         "link tylko do adresu z migawki, także na konkretnym adresie hosta")
        self.assertEqual(warned(log, "RESULT"), "Częściowo — odzyskano 5,9 GB | Pamięć podręczna budowania: odzyskano 5,9 GB "
                         "(121 elementów) | !Wolumeny bez kontenera: Docker właśnie porządkuje")
        self.assertEqual(warned(log, "NOTES"), "zatrzymany | uruchomiony — już działał | Docker nie pozwolił zatrzymać: boom"
                         " | Zrestartowano: 3 kontenery | Zatrzymano: 1, nie udało się: b — nie | wszystko już działa")
        self.assertEqual(warned(log, "PROBLEM"), "Usługa tide-daemon nie odpowiada | Łączę się z Dockerem… | brak uprawnień"
                         " |")

    def test_page_actions_confirmations_logs_and_reveal(self):
        log = self.harness("PageSmoke.qml", """import QtQuick
Item {
    id: root
    width: 1400; height: 900
    readonly property var d: %DATA%
    property var calls: []
    DockerPage {
        id: page
        anchors.fill: parent
        nowTs: %NOW%
        snapshot: root.d.snapshot
        stats: root.d.stats
        disk: root.d.disk
        onActionRequested: (id, action) => root.calls.push(action + ":" + id.slice(0, 6))
        onGroupActionRequested: (project, action) => root.calls.push("group-" + action + ":" + project)
        onLogsRequested: id => root.calls.push("logs:" + id.slice(0, 6))
        onOpenLogsRequested: id => root.calls.push("openlogs:" + id.slice(0, 6))
        onShellRequested: id => root.calls.push("shell:" + id.slice(0, 6))
        onPruneRequested: list => root.calls.push("prune:" + list)
        onFreshDiskRequested: root.calls.push("fresh")
        onLinkRequested: url => root.calls.push("link:" + url)
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
    function id(name) {
        for (const g of page.snapshot.groups) for (const c of g.containers) if (c.name === name) return c.id
        return ""
    }
    Component.onCompleted: {
        const api = id("cookapp-api"), docs = id("cookapp-docs"), bot = id("translations-slack-bot")
        console.warn("SUMMARY " + texts(page, [], "summary").join(""))
        // Pojedyncza akcja: przycisk wyłączony do odpowiedzi, potem wynik (albo błąd) w wierszu.
        page.containerAction(docs, "stop")
        page.containerAction(docs, "stop")  // drugi raz w trakcie — nic nie wysyła
        console.warn("BUSY " + page.busy[docs] + "|" + root.calls.join(","))
        page.finishAction(docs, "stop", { ok: false, error: "Docker nie pozwolił zatrzymać: conflict" })
        console.warn("NOTE " + JSON.stringify(page.notes[docs]) + "|" + (page.busy[docs] === undefined))
        console.warn("ROWNOTE " + texts(page, [], "actionNote").join(" ¦ "))
        page.containerAction(docs, "restart")
        page.finishAction(docs, "restart", { ok: true, note: "" })
        console.warn("ROWNOTE2 " + texts(page, [], "actionNote").join(" ¦ "))
        // Usunięcie tylko po potwierdzeniu.
        root.calls = []
        page.containerAction(bot, "remove")
        const dialog = find(page, "confirmDialog")
        console.warn("REMOVE " + page.confirming + "|" + root.calls.length + "|" + texts(dialog, []).join(" ¦ "))
        page.confirmRemove()
        console.warn("REMOVED " + root.calls.join(",") + "|" + page.confirming)
        page.finishAction(bot, "remove", { ok: true })
        // Akcja na projekcie: zadanie, przyciski projektu wyłączone, wynik przy nagłówku.
        root.calls = []
        const cookapp = page.groups.find(g => g.id === "compose:cookapp")
        page.groupAction(cookapp, "restart")
        page.groupAction(cookapp, "stop")  // w trakcie — nic
        console.warn("GROUP " + root.calls.join(",") + "|" + page.groupBusy["cookapp"] + "|"
                     + texts(page, [], "groupNote").join(""))
        page.finishGroup("cookapp", "restart", { ok: false, done: ["cookapp-api"], failed: [{ name: "cookapp-postgres", error: "Docker nie odpowiada na czas" }] })
        console.warn("GROUPNOTE " + texts(page, [], "groupNote").join(""))
        page.groupAction(cookapp, "logs")
        console.warn("COMPOSE " + root.calls[root.calls.length - 1])
        // Logi w szufladzie.
        root.calls = []
        page.containerAction(api, "logs")
        page.showLogs(api, { ok: true, lines: [{ text: "<b>raz</b>", stderr: false }, { text: "dwa", stderr: true }] })
        const drawer = find(page, "logsDrawer")
        console.warn("DRAWER " + root.calls.join(",") + "|" + drawer.visible + "|" + drawer.name + "|" + drawer.list.count)
        page.refreshLogs()
        page.showLogs(api, { ok: false, error: "Docker nie odpowiada na czas" })
        console.warn("LOGERR " + texts(page, [], "logsError").join("") + "|" + root.calls.join(","))
        drawer.openInKonsole()
        page.containerAction(api, "shell")
        page.openFailed("shell", api)
        console.warn("KONSOLE " + root.calls.slice(-2).join(",") + "|" + page.notes[api].text)
        page.containerAction(api, "logs")  // drugi klik zamyka
        console.warn("CLOSED " + page.logsId + "|" + drawer.visible)
        // Porządki: potwierdzenie z listą, potem zadanie i wynik.
        root.calls = []
        page.askPrune()
        console.warn("NOPICK " + page.confirming)
        page.togglePick("cache")
        page.togglePick("volumes")
        page.askPrune()
        // Potwierdzenie prosi o świeży Disk i do jego nadejścia nie pozwala wyczyścić.
        const button = find(page, "confirmButton")
        console.warn("WAITING " + [root.calls.join(","), button.text, button.enabled,
                                   texts(find(page, "confirmDialog"), [], "pruneIntro").join(""),
                                   texts(find(page, "confirmDialog"), [], "pruneWarn").length].join("|"))
        page.confirmPrune()
        console.warn("EARLY " + root.calls.join(",") + "|" + page.confirming)
        // Odczyt z pamięci sprzed otwarcia (choć licznik urósł) — dalej czeka.
        page.disk = Object.assign({}, root.d.disk, { generatedTs: Math.floor(page.pruneAskedTs) - 60 })
        page.diskFreshSerial++
        console.warn("STALE " + button.enabled + "|" + button.text)
        // Świeży odczyt: po `docker compose down` gdzie indziej przybyło wolumenów bez kontenera.
        const fresh = JSON.parse(JSON.stringify(root.d.disk))
        fresh.generatedTs = Math.floor(Date.now() / 1000) + 1
        fresh.categories[2].count = 12
        fresh.categories[2].reclaimableBytes = 2147483648
        page.disk = fresh
        page.diskFreshSerial++
        console.warn("PRUNEDIALOG " + texts(find(page, "confirmDialog"), []).join(" ¦ ") + "|" + button.enabled)
        page.confirmPrune()
        console.warn("PRUNING " + root.calls.join(",") + "|" + page.pruning + "|" + find(page, "cleanButton").text
                     + "|" + find(page, "cleanButton").enabled)
        page.finishPrune({ ok: true, reclaimedBytes: 6335076761, details: [{ id: "volumes", label: "Wolumeny bez kontenera",
                                                                             ok: true, reclaimedBytes: 0, removed: 0 }] })
        console.warn("PRUNED " + texts(find(page, "pruneResult"), []).join(" ¦ ") + "|" + Object.keys(page.picks).length)
        page.togglePick("images")
        page.askPrune()
        page.disk = { ok: false, error: "Docker nie odpowiada na czas", categories: [] }
        page.diskFreshSerial++
        console.warn("DISKFAIL " + button.text + "|" + button.enabled + "|"
                     + texts(find(page, "confirmDialog"), [], "pruneIntro").join(""))
        page.confirming = ""  // Anuluj
        console.warn("CANCEL " + root.calls.filter(c => c.indexOf("prune:") === 0).length + "|" + page.picks.images)
        // Otwarcie na elemencie: testcontainers się odsłaniają, szukanie znika, zatrzymane się rozwijają.
        page.query = "youtrack"
        console.warn("REVEAL " + [page.reveal("testcontainers-ryuk-c6715127"), page.showTc, page.flashId.slice(0, 6),
                                  page.reveal(id("cookapp-postgres").slice(0, 12)), page.query,
                                  page.reveal("compose:app4-release-localhost"), page.flashId,
                                  page.reveal("nie-ma")].join("|"))
        page.query = ""
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertIn("9 kontenerów działa", warned(log, "SUMMARY"))
        self.assertEqual(warned(log, "BUSY"), "stop|stop:" + self.cid("cookapp-docs")[:6])
        self.assertEqual(warned(log, "NOTE"), '{"text":"Docker nie pozwolił zatrzymać: conflict","error":true}|true')
        self.assertEqual(warned(log, "ROWNOTE"), "Docker nie pozwolił zatrzymać: conflict")
        self.assertEqual(warned(log, "ROWNOTE2"), "zrestartowany")
        remove = warned(log, "REMOVE")
        self.assertTrue(remove.startswith("remove|0|Usunąć kontener translations-slack-bot?"), remove)
        self.assertIn("docker rm, bez wymuszania", remove)
        self.assertIn("Usuń kontener", remove)
        self.assertEqual(warned(log, "REMOVED"), "remove:" + self.cid("translations-slack-bot")[:6] + "|")
        self.assertEqual(warned(log, "GROUP"), "group-restart:cookapp|restart|restartuję projekt…")
        self.assertEqual(warned(log, "GROUPNOTE"), "Zrestartowano: 1, nie udało się: cookapp-postgres — Docker nie odpowiada na czas")
        self.assertEqual(warned(log, "COMPOSE"), "openlogs:" + self.cid("cookapp-api")[:6])
        self.assertEqual(warned(log, "DRAWER"), "logs:%s|true|cookapp-api|2" % self.cid("cookapp-api")[:6])
        self.assertEqual(warned(log, "LOGERR"), "Docker nie odpowiada na czas|logs:%s,logs:%s" % ((self.cid("cookapp-api")[:6],) * 2))
        self.assertEqual(warned(log, "KONSOLE"), "openlogs:%s,shell:%s|nie udało się otworzyć powłoki w Konsoli"
                         % ((self.cid("cookapp-api")[:6],) * 2))
        self.assertEqual(warned(log, "CLOSED"), "|false")
        self.assertEqual(warned(log, "NOPICK"), "")
        waiting = warned(log, "WAITING").split("|")
        self.assertEqual(waiting[:3], ["fresh", "liczę…", "false"])
        self.assertIn("Liczę od nowa", waiting[3])
        self.assertEqual(waiting[4], "0", "bez świeżego odczytu lista się nie pokazuje")
        self.assertEqual(warned(log, "EARLY"), "fresh|prune", "„Wyczyść” przed świeżym odczytem nic nie wysyła")
        self.assertEqual(warned(log, "STALE"), "false|liczę…", "odczyt starszy niż otwarcie potwierdzenia nie wystarcza")
        dialog = warned(log, "PRUNEDIALOG")
        self.assertTrue(dialog.endswith("|true"), dialog)
        self.assertIn("12 wolumenów bez kontenera (także nazwane)", dialog, "lista ze świeżego odczytu")
        for expected in ("Wyczyścić zaznaczone i odzyskać ok. 7,9 GB?", "1. Wolumeny bez kontenera", "2,0 GB",
                         "⚠ mogą trzymać dane baz — usuniętych nie da się odzyskać", "2. Pamięć podręczna budowania",
                         "Anuluj", "Wyczyść · 7,9 GB"):
            self.assertIn(expected, dialog)
        self.assertNotIn("Nieużywane obrazy", dialog, "potwierdzenie wymienia tylko zaznaczone")
        self.assertEqual(warned(log, "PRUNING"), 'fresh,prune:["cache","volumes"]|true|Porządkuję…|false')
        diskfail = warned(log, "DISKFAIL").split("|")
        self.assertEqual(diskfail[:2], ["Nie mogę policzyć", "false"])
        self.assertIn("Docker nie odpowiada na czas", diskfail[2])
        self.assertEqual(warned(log, "PRUNED"), "Odzyskano 5,9 GB ¦ OK ¦ Wolumeny bez kontenera: odzyskano 0 B|0")
        self.assertEqual(warned(log, "CANCEL"), "1|true", "Anuluj nic nie wysyła (jedno wywołanie to wcześniejsze porządki)")
        self.assertEqual(warned(log, "REVEAL"), "true|true|%s|true||true|compose:app4-release-localhost|false"
                         % self.cid("testcontainers-ryuk-c6715127")[:6])

    def cid(self, name):
        return next(c["id"] for g in self.data["snapshot"]["groups"] for c in g["containers"] if c["name"] == name)

    def test_empty_states(self):
        log = self.harness("EmptySmoke.qml", """import QtQuick
Item {
    width: 1200; height: 700
    DockerPage { id: down; anchors.fill: parent; serviceUp: false; snapshot: null }
    DockerPage { id: off; anchors.fill: parent; snapshot: ({ available: false, state: "unavailable", groups: [],
                 error: "Docker nie działa — brak socketu /var/run/docker.sock" }) }
    DockerPage { id: denied; anchors.fill: parent; snapshot: ({ available: false, state: "denied", groups: [],
                 error: "brak uprawnień do /var/run/docker.sock — dodaj się do grupy docker i zaloguj ponownie" }) }
    DockerPage { id: empty; anchors.fill: parent; snapshot: ({ available: true, state: "ok", groups: [],
                 summary: { total: 0, running: 0, stopped: 0, failed: 0 } }) }
    function texts(item, out, name) {
        if (!item.visible) return out
        if (typeof item.text === "string" && item.text !== "" && (!name || item.objectName === name)) out.push(item.text)
        for (const child of item.children) texts(child, out, name)
        return out
    }
    Component.onCompleted: {
        for (const [tag, page] of [["DOWN", down], ["OFF", off], ["DENIED", denied], ["EMPTY", empty]])
            console.warn(tag + " " + [page.ready, texts(page, [], "problemTitle").join(""), texts(page, [], "problemText").join(""),
                                      texts(page, [], "emptyText").join("")].join("|"))
        Qt.quit()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "DOWN"), "false|Usługa tide-daemon nie odpowiada|Uruchom ją: systemctl --user start "
                         "tide-daemon|")
        off = warned(log, "OFF")
        self.assertTrue(off.startswith("false|Docker nie działa|Docker nie działa — brak socketu /var/run/docker.sock"), off)
        self.assertEqual(warned(log, "DENIED"), "false|Brak dostępu do Dockera|brak uprawnień do /var/run/docker.sock — "
                         "dodaj się do grupy docker i zaloguj ponownie|")
        self.assertEqual(warned(log, "EMPTY"), "true|||Brak kontenerów — Docker działa, ale nic nie jest utworzone.")

    def test_view_polls_stats_only_when_active_and_routes_results(self):
        log = self.harness("ViewSmoke.qml", """import QtQuick
Item {
    width: 1400; height: 900
    readonly property var d: %DATA%
    component Stub: QtObject {
        property string snapshotJson: ""
        property string statsJson: "{}"
        property string diskJson: "{}"
        property bool available: true
        property bool preview: false
        property int stats: 0
        property int disks: 0
        property int logsCalls: 0
        property var actions: []
        signal actionFinished(string id, string action, string text)
        signal groupFinished(string project, string action, string text)
        signal pruneFinished(string text)
        signal logsReady(string id, string text)
        signal opened(string what, string id, bool ok)
        function refreshStats() { stats++ }
        function refreshDisk() { disks++ }
        property int diskFreshSerial: 0
        property int freshCalls: 0
        function refreshDiskNow() {
            freshCalls++
            const disk = JSON.parse(diskJson)
            disk.generatedTs = Math.floor(Date.now() / 1000) + 1
            diskJson = JSON.stringify(disk)
            diskFreshSerial++
        }
        function reload() {}
        function logs(id, lines) { logsCalls++; logsReady(id, JSON.stringify({ ok: true, lines: [{ text: "x", stderr: false }] })) }
        function start(id) { actions.push("start"); actionFinished(id, "start", JSON.stringify({ ok: true, note: "" })) }
        function stop(id) { actions.push("stop"); actionFinished(id, "stop", JSON.stringify({ ok: false, error: "nie" })) }
        function restart(id) {}
        function remove(id) {}
        function groupAction(project, action) { actions.push(action + ":" + project); groupFinished(project, action, JSON.stringify({ ok: true, done: ["a"], failed: [] })) }
        function prune(list) { actions.push("prune " + list); pruneFinished(JSON.stringify({ ok: true, reclaimedBytes: 5, details: [] })) }
        function openLogs(id) { opened("logs", id, false) }
        function openShell(id) { opened("shell", id, true) }
    }
    Stub { id: front; snapshotJson: JSON.stringify(d.snapshot); statsJson: JSON.stringify(d.stats); diskJson: JSON.stringify(d.disk) }
    Stub { id: back; snapshotJson: JSON.stringify(d.snapshot) }
    Stub { id: late }
    DockerView { id: shown; anchors.fill: parent; backend: front; statsMs: 60; logsMs: 60 }
    DockerView { id: hidden; anchors.fill: parent; backend: back; active: false; statsMs: 60; logsMs: 60 }
    DockerView { id: early; anchors.fill: parent; backend: late; active: false; statsMs: 60 }
    Timer {
        id: wait
        interval: 400
        property int step: 0
        property int frozen: 0
        property string pending: ""
        onTriggered: {
            if (step === 0) {
                console.warn("PENDING " + [pending, early.pendingItem, early.page.flashId === id("cookapp-api"),
                                           early.page.logsId === id("cookapp-api"), late.logsCalls > 0, front.freshCalls].join("|"))
                console.warn("POLL " + [front.stats > 1, back.stats, front.disks > 0, back.disks].join("|"))
                shown.active = false
                hidden.active = true
                step = 1
                wait.start()
                return
            }
            if (step === 1) {
                frozen = front.stats
                console.warn("SWITCH " + [back.stats > 0, front.logsCalls > 1, shown.page.logsLines.length].join("|"))
                step = 2
                wait.start()
                return
            }
            console.warn("STOPPED " + (front.stats === frozen))
            Qt.quit()
        }
    }
    function id(name) {
        for (const g of d.snapshot.groups) for (const c of g.containers) if (c.name === name) return c.id
        return ""
    }
    Component.onCompleted: {
        const page = shown.page
        page.containerAction(id("cookapp-docs"), "stop")
        page.containerAction(id("app4-release-t-postgres-1"), "start")
        page.groupAction(page.groups.find(g => g.id === "compose:cookapp"), "stop")
        page.togglePick("cache")
        page.askPrune()
        page.confirmPrune()
        page.containerAction(id("cookapp-api"), "shell")
        console.warn("ROUTED " + [front.actions.join(","), page.notes[id("cookapp-docs")].text,
                                  page.notes[id("app4-release-t-postgres-1")].text, page.groupNotes["cookapp"].text,
                                  page.pruneResult.title, page.notes[id("cookapp-api")] === undefined].join("|"))
        page.containerAction(id("cookapp-api"), "logs")
        page.openLogsRequested(id("cookapp-api"))
        console.warn("OPENFAIL " + page.logsError)
        console.warn("RAIL " + JSON.stringify(shown.railInfo))
        early.showItem("cookapp-api")
        wait.pending = early.pendingItem
        late.snapshotJson = JSON.stringify(d.snapshot)
        shown.showItem("nie-ma")
        console.warn("UNKNOWN " + shown.pendingItem)
        wait.start()
    }
}
""")
        self.assertEqual(problems(log), [])
        self.assertEqual(warned(log, "ROUTED"), 'stop,start,stop:cookapp,prune ["cache"]|nie|uruchomiony|'
                         "Zatrzymano: 1 kontener|Odzyskano 5 B|true")
        self.assertEqual(warned(log, "OPENFAIL"), "nie udało się otworzyć logów w Konsoli")
        rail = json.loads(warned(log, "RAIL"))
        self.assertEqual(rail, {"label": "Docker Engine", "value": "29.9.2 · API 1.56",
                                "note": "socket /var/run/docker.sock · zmiany na żywo"})
        self.assertEqual(warned(log, "PENDING"), "cookapp-api||true|true|true|1",
                         "element czeka na pierwszą migawkę, a kontener dostaje szufladę logów")
        self.assertEqual(warned(log, "UNKNOWN"), "", "nieznany element w gotowej migawce przepada")
        self.assertEqual(warned(log, "POLL"), "true|0|true|0", "Stats() i Disk() tylko w widoku na wierzchu")
        self.assertEqual(warned(log, "SWITCH"), "true|true|1", "logi odświeżane, dopóki szuflada jest otwarta")
        self.assertEqual(warned(log, "STOPPED"), "true", "widok w tle przestaje pytać o Stats()")


class BackendDataTest(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.mod = load_backend()

    def test_fixture_mode_and_badge(self):
        data = fixture(NOW)
        backend = self.mod.create(None, data)
        self.assertTrue(backend.available and backend.preview)
        self.assertEqual(json.loads(backend.snapshotJson)["summary"]["running"], 9)
        self.assertTrue(json.loads(backend.statsJson)["ok"])
        self.assertEqual(len(json.loads(backend.diskJson)["categories"]), 4)
        self.assertEqual((backend.badgeText, backend.badgeTone), ("● 1", "negative"))
        got = []
        backend.actionFinished.connect(lambda *a: got.append(("action",) + a))
        backend.groupFinished.connect(lambda *a: got.append(("group",) + a))
        backend.pruneFinished.connect(lambda *a: got.append(("prune",) + a))
        backend.logsReady.connect(lambda *a: got.append(("logs",) + a))
        backend.opened.connect(lambda *a: got.append(("opened",) + a))
        backend.stop("cookapp-api")
        backend.groupAction("cookapp", "restart")
        backend.prune('["cache"]')
        backend.logs("translations-slack-bot", 200)
        backend.openShell("cookapp-api")
        self.assertEqual([g[0] for g in got], ["action", "group", "prune", "logs", "opened"])
        for entry in got[:3]:
            self.assertEqual(json.loads(entry[-1])["error"], "podgląd bez usługi — nic nie zmieniono")
        logs = json.loads(got[3][2])
        self.assertTrue(logs["ok"])
        self.assertEqual(logs["lines"][-1]["stderr"], True)
        self.assertEqual(got[4], ("opened", "shell", "cookapp-api", False))
        # --print-docker ({snapshot, disk}) też jest dobrym plikiem.
        printed = self.mod.create(None, {"snapshot": data["snapshot"], "disk": data["disk"]})
        self.assertFalse(json.loads(printed.statsJson)["ok"])
        # Bez danych i bez magistrali — pusto, bez plakietki.
        empty = self.mod.create(None, None)
        self.assertEqual((empty.available, empty.badgeText), (False, ""))

    def test_fresh_disk_in_fixture_mode_and_foreign_jobs_expire(self):
        import time as _time
        backend = self.mod.create(None, fixture(NOW))
        seen = []
        backend.diskChanged.connect(lambda: seen.append(backend.diskFreshSerial))
        backend.refreshDiskNow()
        self.assertEqual(seen, [1])
        self.assertGreaterEqual(json.loads(backend.diskJson)["generatedTs"], int(_time.time()) - 1)
        # Wynik cudzego zadania (kafelek, drugie okno) nie zostaje na zawsze: po minucie znika, najwyżej 32 wpisy.
        live = self.mod.Backend(None)
        live._early["stare"] = (_time.monotonic() - 2 * self.mod.EARLY_TTL_S, "{}")
        live._action_finished("nowe", "{}")
        self.assertEqual(sorted(live._early), ["nowe"])
        for i in range(50):
            live._action_finished(f"job-{i}", "{}")
        self.assertLessEqual(len(live._early), self.mod.EARLY_MAX)
        self.assertIn("job-49", live._early)
        # Odczyt z pamięci starszy niż już znany (np. Disk po świeżym DiskNow) nie nadpisuje świeżego.
        self.assertTrue(self.mod.older('{"generatedTs": 5}', '{"generatedTs": 9}'))
        self.assertFalse(self.mod.older('{"generatedTs": 9}', '{"generatedTs": 5}'))

    def test_badge_matches_daemon(self):
        snap = fixture(NOW)["snapshot"]
        cases = [snap, dict(snap, summary=dict(snap["summary"], failed=0)),
                 dict(snap, summary=dict(snap["summary"], failed=0, running=0)), dict(snap, available=False), {}]
        for case in cases:
            expected = daemon_snapshot.badge(case)
            self.assertEqual(self.mod.badge(case), (expected["text"], expected["tone"]))


@requires_dbus
class BackendOverDbusTest(unittest.TestCase):
    """Backend widoku na prywatnej magistrali z prawdziwym tide-daemon i atrapą Engine API."""

    @classmethod
    def setUpClass(cls):
        from daemon.tests.fake_docker import FakeDocker, make_workspace, populate
        cls.tmp = tempfile.mkdtemp(prefix="tide-docker-view-dbus-")
        cls.ws = make_workspace(cls.tmp)
        cls.sock = os.path.join(cls.tmp, "docker.sock")
        cls.fake = FakeDocker(cls.sock)
        cls.c = populate(cls.fake, cls.ws)
        cls.fake.start()
        empty = os.path.join(cls.tmp, "empty")
        os.makedirs(empty)
        cls.service = PrivateBus("tide-docker-view")
        cls.service.spawn_daemon(
            "--claude-dir", empty, "--codex-dir", empty, "--workspace", cls.ws["work"], "--no-processes",
            "--no-notifications", "--no-krunner", "--no-idle", "--no-limits", "--no-coordination", "--no-tab-marks",
            "--no-report", extra_env={"DOCKER_HOST": f"unix://{cls.sock}", "XDG_RUNTIME_DIR": os.path.join(cls.tmp, "run"),
                                      "XDG_DATA_HOME": os.path.join(cls.tmp, "data")})
        cls.mod = load_backend()

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def backend(self):
        backend = self.mod.Backend(self.service.bus)
        self.assertTrue(wait_until(lambda: json.loads(backend.snapshotJson or "{}").get("available"), timeout=15))
        return backend

    def collect(self, signal):
        got = []
        signal.connect(lambda *a: got.append(a))
        return got

    def test_1_start_is_cheap(self):
        self.fake.requests.clear()
        backend = self.backend()
        wait_until(lambda: False, timeout=0.5)
        self.assertTrue(backend.available)
        self.assertEqual((backend.badgeText, backend.badgeTone), ("● 1", "negative"))
        self.assertFalse([p for _m, p, _q in self.fake.requests if p.endswith("/stats") or p == "/system/df"],
                         "na starcie ani Stats(), ani Disk()")
        backend.refreshStats()
        self.assertTrue(wait_until(lambda: json.loads(backend.statsJson).get("ok"), timeout=10))
        backend.refreshDisk()
        self.assertTrue(wait_until(lambda: json.loads(backend.diskJson).get("ok"), timeout=10))
        before = len(self.fake.calls("GET", "/system/df"))
        backend.refreshDiskNow()
        self.assertTrue(wait_until(lambda: backend.diskFreshSerial == 1, timeout=10))
        self.assertEqual(len(self.fake.calls("GET", "/system/df")), before + 1, "DiskNow czyta od nowa, mimo pamięci")

    def test_2_actions_logs_and_jobs(self):
        backend = self.backend()
        self.fake.requests.clear()
        actions = self.collect(backend.actionFinished)
        backend.stop("cookapp-docs")
        self.assertTrue(wait_until(lambda: actions, timeout=25))
        ident, action, text = actions[0]
        self.assertEqual((ident, action, json.loads(text)["ok"]), ("cookapp-docs", "stop", True))
        backend.remove("cookapp-api")
        self.assertTrue(wait_until(lambda: len(actions) == 2, timeout=25))
        self.assertEqual(json.loads(actions[1][2])["error"], "kontener działa — najpierw go zatrzymaj")
        backend.start("cookapp-docs")
        self.assertTrue(wait_until(lambda: len(actions) == 3, timeout=25))
        self.assertEqual([p.rsplit("/", 1)[1] for _m, p, _q in self.fake.mutating()], ["stop", "start"])

        logs = self.collect(backend.logsReady)
        backend.logs("cookapp-api", 2)
        self.assertTrue(wait_until(lambda: logs, timeout=15))
        self.assertEqual([l["stderr"] for l in json.loads(logs[0][1])["lines"]], [True, False])

        groups = self.collect(backend.groupFinished)
        self.fake.requests.clear()
        backend.groupAction("cookapp", "restart")
        self.assertTrue(wait_until(lambda: groups, timeout=30))
        project, action, text = groups[0]
        result = json.loads(text)
        self.assertEqual((project, action, result["ok"]), ("cookapp", "restart", True))
        self.assertEqual(sorted(result["done"]), ["cookapp-api", "cookapp-docs", "cookapp-postgres"])
        backend.groupAction("cookapp", "down")
        self.assertTrue(wait_until(lambda: len(groups) == 2, timeout=5))
        self.assertIn("nieznana akcja", json.loads(groups[1][2])["error"])

        prunes = self.collect(backend.pruneFinished)
        self.fake.requests.clear()
        backend.prune(json.dumps(["cache"]))
        self.assertTrue(wait_until(lambda: prunes, timeout=30))
        result = json.loads(prunes[0][0])
        self.assertEqual((result["ok"], result["reclaimedBytes"]), (True, 5_900_000_000))
        backend.prune('{"volumes": true}')
        self.assertTrue(wait_until(lambda: len(prunes) == 2, timeout=10))
        self.assertFalse(json.loads(prunes[1][0])["ok"])
        self.assertEqual([(m, p) for m, p, _q in self.fake.mutating()], [("POST", "/build/prune")],
                         "porządki tylko dla listy kategorii — obiekt to odmowa bez wywołania")

    def test_3_service_without_docker_object(self):
        """Usługa bez /Docker (--no-docker): backend niedostępny, plakietka ukryta, bez wyjątków."""
        bus = PrivateBus("tide-docker-view-none")
        self.addCleanup(bus.stop)
        empty = os.path.join(self.tmp, "empty")
        bus.spawn_daemon("--claude-dir", empty, "--codex-dir", empty, "--workspace", empty, "--no-processes",
                         "--no-notifications", "--no-docker")
        backend = self.mod.Backend(bus.bus)
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual((backend.available, backend.badgeText), (False, ""))


class GrabTest(unittest.TestCase):
    """Okno Tide na widoku Docker z danymi z pliku (bez magistrali) — także z logami i potwierdzeniem."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-docker-grab-")
        cls.pkg = stage(os.path.join(cls.tmp, "pkg"))
        cls.data = os.path.join(cls.tmp, "docker.json")
        with open(cls.data, "w", encoding="utf-8") as fh:
            json.dump(fixture(), fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def grab(self, name, *argv):
        png = os.path.join(self.tmp, name + ".png")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        result = subprocess.run(["/usr/bin/python3", os.path.join(self.pkg, "tide.py"), "--view", "docker", "--data",
                                 self.data, *argv, "--grab", png, "--width", "1640", "--height", "940"], env=env,
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(problems(result.stderr), [])
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        return os.path.getsize(png)

    def test_grabs(self):
        plain = self.grab("plain")
        self.assertGreater(plain, 80_000, "lista kontenerów z pliku")
        self.assertGreater(self.grab("logs", "--preview", "logs=cookapp-api", "--select", "translations-slack-bot"),
                           80_000)
        self.assertGreater(self.grab("confirm", "--preview", "pick=volumes", "--preview", "confirm=prune"), 60_000)


if __name__ == "__main__":
    unittest.main()
