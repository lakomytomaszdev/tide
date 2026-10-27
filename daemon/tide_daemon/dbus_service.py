"""Kontrakt D-Bus usługi (opis: docs/tide-daemon.md).

Usługa org.tide.Daemon.

Obiekt /Agents, interfejs org.tide.Daemon.Agents:
- Sessions() -> s            lista sesji jako JSON,
- ActivateSession(s id) -> b przejście do okna sesji, False gdy nie da się go ustalić,
- ActivateLongestWaiting() -> b  przejście do sesji czekającej najdłużej (skrót Meta+A);
                             False, gdy nic nie czeka albo okna nie da się ustalić; sesje
                             w stanie „done” (skończona tura) nie są brane pod uwagę,
- sygnał Changed()           lista sesji się zmieniła — warto pobrać ją ponownie.

Obiekt /Worktrees, interfejs org.tide.Daemon.Worktrees:
- List() -> s                lista worktrees jako JSON,
- Remove(as paths) -> s      sprzątanie; JSON z wynikiem dla każdej ścieżki (odpowiedź po zakończeniu),
- RemoveForced(as paths, b deleteUnmergedBranches, s confirmed) -> s
                             usuwanie po potwierdzeniu „Usuń mimo to”: `git worktree remove --force`,
                             gałąź -d gdy scalona, -D tylko z deleteUnmergedBranches; główny katalog,
                             gałąź główna, brakujący, zablokowany i z agentem — nigdy; confirmed to
                             JSON ze stanem z potwierdzenia (gorszy świeży stan = odmowa „changed”),
- Refresh()                  odświeżenie na żądanie (wynik przyjdzie sygnałem Changed),
- Open(s path, s app) -> b   otwarcie worktree: app = "terminal" albo "rider",
- Launchers() -> s           JSON {"terminal": bool, "rider": bool, "window": bool} — które przyciski pokazać,
- ShowWindow() -> b          otwiera okno Worktrees (False, gdy nie jest zainstalowane),
- Prune(s repoPath) -> s     `git worktree prune` w repozytorium z worktrees bez katalogu (odpowiedź po zakończeniu),
- sygnał Changed()           lista worktrees się zmieniła.

Obiekt /Tasks, interfejs org.tide.Daemon.Tasks (nowe zadanie dla agenta):
- Repos() -> s               repozytoria z katalogu projektów jako JSON, od ostatnio aktywnych,
- Defaults() -> s            JSON: zapamiętany wybór (repoPath, tool, newWorktree), dostępne narzędzia,
                             Konsole, katalog zrzutów i limity,
- SuggestBranch(s repoPath, s tool, s prompt) -> s
                             JSON {ok, branch, path, base}: gałąź z polecenia (naming.py) i katalog worktree,
- Start(s request) -> s      JSON prośby {repoPath, tool, prompt, newWorktree, branch, images, addDirs} → wynik
                             {ok, error, message, cwd, branch, worktree, title, mode}; worktree od gałęzi
                             głównej, karta Konsoli z agentem; przy błędzie nic nie zostaje (odpowiedź po starcie),
- ShowDialog() -> b          otwiera okno „Nowe zadanie” (False, gdy nie jest zainstalowane).

Obiekt /Idle, interfejs org.tide.Daemon.Idle (bezczynność użytkownika dla tapety z kotem):
- właściwość Idle (b)        od progu nie było klawiatury ani myszy (False, gdy źródło nie działa),
- właściwość Available (b)   czy kompozytor podaje bezczynność,
- właściwość ThresholdMs (i) próg bezczynności (paleta: wallpaper.dozeAfterS),
- IsIdle() -> b              to samo co Idle, jednym wywołaniem (dla QML),
- State() -> s               JSON {available, idle, thresholdMs, idleForMs, source} — diagnostyka,
- sygnał IdleChanged(b idle) bezczynność się zmieniła.

Obiekt /Reports, interfejs org.tide.Daemon.Reports (raport poranny):
- Generate() -> s            składa raport bieżącej doby teraz (bez powiadomienia); ścieżka index.html
                             albo pusty napis przy błędzie (odpowiedź po zakończeniu),
- Latest() -> s              ścieżka index.html najnowszego raportu albo pusty napis,
- Status() -> s              JSON {path, day, generatedAt, projects, waiting, unread} najnowszego raportu
                             (bez raportu path pusty, unread false) — link „Raport” w widżetach; raport
                             bieżącej doby: waiting na żywo (bez nieaktualnych), waitingNow, finished,
- Open() -> b                najnowszy raport w przeglądarce (xdg-open) i oznaczenie go jako przeczytany;
                             False, gdy raportu nie ma albo nie da się go otworzyć,
- Days() -> s                JSON {root, today, keepDays, latest, unread, days: [{day, generatedAt, projects,
                             waiting, data}]} — dni z raportem od najnowszego (widok „Raporty” okna Tide),
- Day(s day) -> s            JSON raportu doby z report.json (przegląd, „Dziś w skrócie”, „Na daily”, notki
                             „Skończone dziś”, teksty do schowka) z polami ok, latest, unread, legacy; bieżąca
                             doba także stale/now/nowText (czeka na żywo); brak doby — {ok: false, error: "missing"},
- OpenDay(s day, s project) -> b  strona doby w przeglądarce: index.html (pusty project) albo <slug>.html;
                             strona najnowszego raportu oznacza go jako przeczytany,
- MarkRead(s day) -> b       najnowszy raport obejrzany w oknie Tide — przeczytany jak po Open(),
- sygnał Ready(s path)       powstał nowy raport,
- sygnał Changed()           nowy (także żywy) raport, notka po tickecie, zmiana tego, co czeka, albo zmiana
                             przeczytania — warto pobrać Status() (i Days()/Day()) ponownie.

Obiekt /Coordination, interfejs org.tide.Daemon.Coordination (koordynatorzy, tickety i podagenci):
- Coordinators() -> s        lista koordynatorów z ostatnich dni jako JSON (z licznikami ticketów),
- Board(s id) -> s           tablica koordynatora: tickety, ich stan, oś czasu, przebiegi podagentów,
- RunDetail(s id, s run) -> s  wynik podagenta i wycinek jego dziennika („Pokaż wynik”),
- Activate(s id) -> b        przejście do koordynatora (okno sesji albo wznowienie w Konsoli),
- OpenDiff(s id, s ticket) -> b  `git diff <główna>...<gałąź ticketu>` w Konsoli,
- OpenWorktree(s id, s ticket) -> b  worktree z gałęzią ticketu w Konsoli,
- ShowWindow(s id) -> b      okno „Tide Sesje” (pusty id — bez wyboru koordynatora),
- Refresh(b force)           odświeżenie; force pomija pamięć podręczną YouTracka,
- sygnał Changed()           lista albo któraś tablica się zmieniła.

Obiekt /Limits, interfejs org.tide.Daemon.Limits (prognoza Limitów AI z historii odczytów):
- Forecast() -> s            JSON {available, error, services: {usługa: {session|weekly: prognoza}}},
- sygnał Changed()           prognoza albo dostępność usługi Limity AI się zmieniła.

Obiekt /Models, interfejs org.tide.Daemon.Models (modele lokalne: serwery z models.json i role):
- Status() -> s              JSON {checked, default, config, endpoints: {nazwa: stan}, roles: {rola: …}} — bez tokenów,
- Refresh()                  odczyt stanu teraz (wynik przyjdzie sygnałem Changed, jeśli coś się zmieniło),
- sygnał Changed()           stan serwerów, modeli albo ustawień się zmienił — warto pobrać Status() ponownie.

Obiekt /Board, interfejs org.tide.Daemon.Board (tablica pracy w repozytorium git, board/):
- Snapshot() -> s            JSON: kolumny, projekty, tagi, karty (z tagami projektu), katalog, ostatni
                             commit, błędy plików, repozytoria do dodania jako projekty,
- CreateCard(s json) -> s    nowa karta {title, description, project|repo, tags, column, note, …} → {ok, id, commitError},
- UpdateCard(s id, s json) -> s  zmiana pól karty (bez kolumny i kolejności) → {ok, id, changed, commitError},
- MoveCard(s id, s column, i order) -> s  ręczna kolumna i pozycja w niej (od 0, -1 = koniec),
- UpsertProject(s json) -> s nowy albo zmieniony projekt → {ok, slug, changed, commitError},
- UpsertTag(s json) -> s     nowy albo zmieniony tag (także newName — zmiana nazwy w kartach) → {ok, name, changed, commitError},
- RenderFlow(s id, s mode) -> s  polecenie trybu dla karty: {ok, text, mode, tool, branch, docsDir, …},
- RenderLaunch(s id, s json) -> s  to samo z wyborem z okna „Co ma zrobić agent?”: {mode, tool, branch},
- Badge() -> s               plakietka widoku Tablica w oknie Tide: JSON {text, tone} („● N” czeka, inaczej
                             liczba kart w grze; pusty tekst — ukryta),
- ShowWindow(s id) -> b      okno Tide na widoku Tablica, z ID karty (K-12) od razu na niej, z launch:K-12 — także
                             z oknem „Co ma zrobić agent?” (False, gdy okna nie ma),
- sygnał Changed()           tablica się zmieniła (nasz zapis albo zmiana z zewnątrz).
Każda zmiana to commit w repozytorium tablicy (nieudany commit: ok z opisem w commitError, commit
czeka); błąd to JSON {ok: false, error: <kod>, message: <tekst po polsku>} jak w /Tasks — dane
sprawdzamy przed zapisem, więc złe żądanie nie zostawia pliku ani commita.
Obiekt /Docker, interfejs org.tide.Daemon.Docker (kontenery przez Docker Engine API):
- Snapshot() -> s            JSON {available, state, error, host, engine, live, summary, groups},
- Stats() -> s               CPU i pamięć działających kontenerów (na żądanie, pamięć 5 s; odpowiedź po odczycie),
- Disk() -> s                kategorie porządków z miejscem do odzyskania (odpowiedź po odczycie),
- DiskNow() -> s             to samo, zawsze świeży odczyt (potwierdzenie porządków),
- Start(s id), Stop(s id), Restart(s id), Remove(s id) -> s
                             JSON {ok, error, id, name, note}; Remove tylko zatrzymanego, bez wolumenów,
- GroupAction(s project, s action) -> s
                             zadanie w tle: start/stop/restart wszystkich kontenerów projektu compose; od razu
                             {ok, error, job}, wynik {ok, error, job, project, done, failed} sygnałem ActionFinished,
- Prune(s categories) -> s   zadanie w tle dla listy JSON ["cache", "images", "volumes", "stopped"]; od razu
                             {ok, error, job} (zła lista — odmowa bez zadania), wynik {ok, error, job,
                             reclaimedBytes, details} sygnałem ActionFinished; tylko wymienione kategorie,
- Logs(s id, i lines) -> s   ostatnie linie logów {ok, error, lines: [{text, stderr}]},
- OpenLogs(s id) -> b        `docker logs -f --tail 200 <nazwa>` w karcie Konsoli,
- OpenShell(s id) -> b       powłoka w działającym kontenerze (bash albo sh) w karcie Konsoli,
- Badge() -> s               plakietka widoku Docker w oknie Tide: JSON {text, tone} — „● N” (negative), gdy coś
                             padło, inaczej liczba działających (muted), pusta przy zerze i bez Dockera,
- ShowWindow(s id) -> b      okno Tide na widoku Docker, opcjonalnie od razu na kontenerze albo grupie,
- sygnał Changed()           migawka kontenerów się zmieniła,
- sygnał ActionFinished(s job, s json)  koniec zadania Prune albo GroupAction.
"""
from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage

SERVICE = "org.tide.Daemon"
AGENTS_PATH = "/Agents"
AGENTS_IFACE = "org.tide.Daemon.Agents"
WORKTREES_PATH = "/Worktrees"
WORKTREES_IFACE = "org.tide.Daemon.Worktrees"
TASKS_PATH = "/Tasks"
TASKS_IFACE = "org.tide.Daemon.Tasks"
IDLE_PATH = "/Idle"
IDLE_IFACE = "org.tide.Daemon.Idle"
REPORTS_PATH = "/Reports"
REPORTS_IFACE = "org.tide.Daemon.Reports"
COORDINATION_PATH = "/Coordination"
COORDINATION_IFACE = "org.tide.Daemon.Coordination"
LIMITS_PATH = "/Limits"
LIMITS_IFACE = "org.tide.Daemon.Limits"
MODELS_PATH = "/Models"
MODELS_IFACE = "org.tide.Daemon.Models"
BOARD_PATH = "/Board"
BOARD_IFACE = "org.tide.Daemon.Board"
DOCKER_PATH = "/Docker"
DOCKER_IFACE = "org.tide.Daemon.Docker"

AGENTS_XML = f"""<interface name="{AGENTS_IFACE}">
  <method name="Sessions"><arg name="json" type="s" direction="out"/></method>
  <method name="ActivateSession">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="ActivateLongestWaiting"><arg name="ok" type="b" direction="out"/></method>
  <signal name="Changed"/>
</interface>"""

WORKTREES_XML = f"""<interface name="{WORKTREES_IFACE}">
  <method name="List"><arg name="json" type="s" direction="out"/></method>
  <method name="Remove">
    <arg name="paths" type="as" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="RemoveForced">
    <arg name="paths" type="as" direction="in"/>
    <arg name="deleteUnmergedBranches" type="b" direction="in"/>
    <arg name="confirmed" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Refresh"/>
  <method name="Open">
    <arg name="path" type="s" direction="in"/>
    <arg name="app" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="Launchers"><arg name="json" type="s" direction="out"/></method>
  <method name="ShowWindow"><arg name="ok" type="b" direction="out"/></method>
  <method name="Prune">
    <arg name="repoPath" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <signal name="Changed"/>
</interface>"""

REPORTS_XML = f"""<interface name="{REPORTS_IFACE}">
  <method name="Generate"><arg name="path" type="s" direction="out"/></method>
  <method name="Latest"><arg name="path" type="s" direction="out"/></method>
  <method name="Status"><arg name="json" type="s" direction="out"/></method>
  <method name="Open"><arg name="ok" type="b" direction="out"/></method>
  <method name="Days"><arg name="json" type="s" direction="out"/></method>
  <method name="Day">
    <arg name="day" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="OpenDay">
    <arg name="day" type="s" direction="in"/>
    <arg name="project" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="MarkRead">
    <arg name="day" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <signal name="Ready"><arg name="path" type="s"/></signal>
  <signal name="Changed"/>
</interface>"""

TASKS_XML = f"""<interface name="{TASKS_IFACE}">
  <method name="Repos"><arg name="json" type="s" direction="out"/></method>
  <method name="Defaults"><arg name="json" type="s" direction="out"/></method>
  <method name="SuggestBranch">
    <arg name="repoPath" type="s" direction="in"/>
    <arg name="tool" type="s" direction="in"/>
    <arg name="prompt" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Start">
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="ShowDialog"><arg name="ok" type="b" direction="out"/></method>
</interface>"""

IDLE_XML = f"""<interface name="{IDLE_IFACE}">
  <property name="Idle" type="b" access="read"/>
  <property name="Available" type="b" access="read"/>
  <property name="ThresholdMs" type="i" access="read"/>
  <method name="IsIdle"><arg name="idle" type="b" direction="out"/></method>
  <method name="State"><arg name="json" type="s" direction="out"/></method>
  <signal name="IdleChanged"><arg name="idle" type="b"/></signal>
</interface>"""

COORDINATION_XML = f"""<interface name="{COORDINATION_IFACE}">
  <method name="Coordinators"><arg name="json" type="s" direction="out"/></method>
  <method name="Board">
    <arg name="id" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="RunDetail">
    <arg name="id" type="s" direction="in"/>
    <arg name="run" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Activate">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="OpenDiff">
    <arg name="id" type="s" direction="in"/>
    <arg name="ticket" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="OpenWorktree">
    <arg name="id" type="s" direction="in"/>
    <arg name="ticket" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="ShowWindow">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="Refresh"><arg name="force" type="b" direction="in"/></method>
  <signal name="Changed"/>
</interface>"""

LIMITS_XML = f"""<interface name="{LIMITS_IFACE}">
  <method name="Forecast"><arg name="json" type="s" direction="out"/></method>
  <signal name="Changed"/>
</interface>"""

MODELS_XML = f"""<interface name="{MODELS_IFACE}">
  <method name="Status"><arg name="json" type="s" direction="out"/></method>
  <method name="Refresh"/>
  <signal name="Changed"/>
</interface>"""

_ID_ACTION = """  <method name="{name}">
    <arg name="id" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>"""

DOCKER_XML = f"""<interface name="{DOCKER_IFACE}">
  <method name="Snapshot"><arg name="json" type="s" direction="out"/></method>
  <method name="Stats"><arg name="json" type="s" direction="out"/></method>
  <method name="Disk"><arg name="json" type="s" direction="out"/></method>
  <method name="DiskNow"><arg name="json" type="s" direction="out"/></method>
{chr(10).join(_ID_ACTION.format(name=n) for n in ("Start", "Stop", "Restart", "Remove"))}
  <method name="GroupAction">
    <arg name="project" type="s" direction="in"/>
    <arg name="action" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Prune">
    <arg name="categories" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Logs">
    <arg name="id" type="s" direction="in"/>
    <arg name="lines" type="i" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="OpenLogs">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="OpenShell">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <method name="Badge"><arg name="json" type="s" direction="out"/></method>
  <method name="ShowWindow">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <signal name="Changed"/>
  <signal name="ActionFinished">
    <arg name="job" type="s"/>
    <arg name="json" type="s"/>
  </signal>
</interface>"""


BOARD_XML = f"""<interface name="{BOARD_IFACE}">
  <method name="Snapshot"><arg name="json" type="s" direction="out"/></method>
  <method name="CreateCard">
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="UpdateCard">
    <arg name="id" type="s" direction="in"/>
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="MoveCard">
    <arg name="id" type="s" direction="in"/>
    <arg name="column" type="s" direction="in"/>
    <arg name="order" type="i" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="UpsertProject">
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="UpsertTag">
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="RenderFlow">
    <arg name="id" type="s" direction="in"/>
    <arg name="mode" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="RenderLaunch">
    <arg name="id" type="s" direction="in"/>
    <arg name="request" type="s" direction="in"/>
    <arg name="json" type="s" direction="out"/>
  </method>
  <method name="Badge"><arg name="json" type="s" direction="out"/></method>
  <method name="ShowWindow">
    <arg name="id" type="s" direction="in"/>
    <arg name="ok" type="b" direction="out"/>
  </method>
  <signal name="Changed"/>
</interface>"""


@pyqtClassInfo("D-Bus Interface", AGENTS_IFACE)
@pyqtClassInfo("D-Bus Introspection", AGENTS_XML)
class AgentsAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Sessions(self) -> str:
        return self.monitor.sessions_json()

    @pyqtSlot(str, result=bool)
    def ActivateSession(self, session_id: str) -> bool:
        return self.monitor.activate(session_id)

    @pyqtSlot(result=bool)
    def ActivateLongestWaiting(self) -> bool:
        return self.monitor.activate_longest_waiting()


@pyqtClassInfo("D-Bus Interface", WORKTREES_IFACE)
@pyqtClassInfo("D-Bus Introspection", WORKTREES_XML)
class WorktreesAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor, bus: QDBusConnection):
        super().__init__(parent)
        self.monitor = monitor
        self.bus = bus
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def List(self) -> str:
        return self.monitor.list_json()

    @pyqtSlot("QStringList", QDBusMessage, result=str)
    def Remove(self, paths, message: QDBusMessage) -> str:
        # Usuwanie może trwać (duże katalogi), więc odpowiadamy później, z wątku głównego,
        # a pętla zdarzeń w tym czasie obsługuje inne wywołania.
        self.monitor.remove(list(paths), self._delayed(message, "Remove"))
        return ""

    @pyqtSlot("QStringList", bool, str, QDBusMessage, result=str)
    def RemoveForced(self, paths, delete_unmerged: bool, confirmed: str, message: QDBusMessage) -> str:
        self.monitor.remove(list(paths), self._delayed(message, "RemoveForced"), force=True,
                            delete_unmerged=bool(delete_unmerged), confirmed=confirmed)
        return ""

    def _delayed(self, message: QDBusMessage, name: str):
        """Odpowiedź z opóźnieniem: funkcja, która odeśle tekst, gdy praca w tle się skończy."""
        message.setDelayedReply(True)
        request = QDBusMessage(message)  # kopia — oryginał żyje tylko do końca tego wywołania

        def reply(text: str) -> None:
            if not self.bus.send(request.createReply([text])):
                print(f"tide-daemon: nie udało się odesłać wyniku {name}", flush=True)

        return reply

    @pyqtSlot()
    def Refresh(self) -> None:
        self.monitor.refresh()

    @pyqtSlot(str, str, result=bool)
    def Open(self, path: str, app: str) -> bool:
        return self.monitor.open(path, app)

    @pyqtSlot(result=str)
    def Launchers(self) -> str:
        return self.monitor.launchers_json()

    @pyqtSlot(result=bool)
    def ShowWindow(self) -> bool:
        return self.monitor.show_window()

    @pyqtSlot(str, QDBusMessage, result=str)
    def Prune(self, repo_path: str, message: QDBusMessage) -> str:
        message.setDelayedReply(True)
        request = QDBusMessage(message)
        self.monitor.prune(repo_path, lambda text: self.bus.send(request.createReply([text])))
        return ""


@pyqtClassInfo("D-Bus Interface", TASKS_IFACE)
@pyqtClassInfo("D-Bus Introspection", TASKS_XML)
class TasksAdaptor(QDBusAbstractAdaptor):
    def __init__(self, parent: QObject, monitor, bus: QDBusConnection):
        super().__init__(parent)
        self.monitor = monitor
        self.bus = bus

    def _delayed(self, message: QDBusMessage, name: str):
        message.setDelayedReply(True)
        request = QDBusMessage(message)

        def reply(text: str) -> None:
            if not self.bus.send(request.createReply([text])):
                print(f"tide-daemon: nie udało się odesłać wyniku {name}", flush=True)

        return reply

    @pyqtSlot(QDBusMessage, result=str)
    def Repos(self, message: QDBusMessage) -> str:
        self.monitor.repos(self._delayed(message, "Repos"))
        return ""

    @pyqtSlot(result=str)
    def Defaults(self) -> str:
        return self.monitor.defaults_json()

    @pyqtSlot(str, str, str, QDBusMessage, result=str)
    def SuggestBranch(self, repo_path: str, tool: str, prompt: str, message: QDBusMessage) -> str:
        self.monitor.suggest(repo_path, tool, prompt, self._delayed(message, "SuggestBranch"))
        return ""

    @pyqtSlot(str, QDBusMessage, result=str)
    def Start(self, request: str, message: QDBusMessage) -> str:
        self.monitor.start(request, self._delayed(message, "Start"))
        return ""

    @pyqtSlot(result=bool)
    def ShowDialog(self) -> bool:
        return self.monitor.show_dialog()


@pyqtClassInfo("D-Bus Interface", IDLE_IFACE)
@pyqtClassInfo("D-Bus Introspection", IDLE_XML)
class IdleAdaptor(QDBusAbstractAdaptor):
    IdleChanged = pyqtSignal(bool)

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.idleChanged.connect(self.IdleChanged)

    @pyqtProperty(bool)
    def Idle(self) -> bool:
        return self.monitor.idle

    @pyqtProperty(bool)
    def Available(self) -> bool:
        return self.monitor.available

    @pyqtProperty(int)
    def ThresholdMs(self) -> int:
        return self.monitor.state.threshold_ms

    @pyqtSlot(result=bool)
    def IsIdle(self) -> bool:
        return self.monitor.idle

    @pyqtSlot(result=str)
    def State(self) -> str:
        return self.monitor.state_json()


@pyqtClassInfo("D-Bus Interface", REPORTS_IFACE)
@pyqtClassInfo("D-Bus Introspection", REPORTS_XML)
class ReportsAdaptor(QDBusAbstractAdaptor):
    Ready = pyqtSignal(str)
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor, bus: QDBusConnection):
        super().__init__(parent)
        self.monitor = monitor
        self.bus = bus
        monitor.ready.connect(self.Ready)
        monitor.changed.connect(self.Changed)

    @pyqtSlot(QDBusMessage, result=str)
    def Generate(self, message: QDBusMessage) -> str:
        # Składanie raportu trwa (git, dzienniki) — odpowiedź po zakończeniu, z wątku głównego.
        message.setDelayedReply(True)
        request = QDBusMessage(message)
        self.monitor.generate(lambda path: self.bus.send(request.createReply([path])))
        return ""

    @pyqtSlot(result=str)
    def Latest(self) -> str:
        return self.monitor.latest()

    @pyqtSlot(result=str)
    def Status(self) -> str:
        return self.monitor.status_json()

    @pyqtSlot(result=bool)
    def Open(self) -> bool:
        return self.monitor.open()

    @pyqtSlot(result=str)
    def Days(self) -> str:
        return self.monitor.days_json()

    @pyqtSlot(str, result=str)
    def Day(self, day: str) -> str:
        return self.monitor.day_json(day)

    @pyqtSlot(str, str, result=bool)
    def OpenDay(self, day: str, project: str) -> bool:
        return self.monitor.open_day(day, project)

    @pyqtSlot(str, result=bool)
    def MarkRead(self, day: str) -> bool:
        return self.monitor.mark_read(day)


@pyqtClassInfo("D-Bus Interface", LIMITS_IFACE)
@pyqtClassInfo("D-Bus Introspection", LIMITS_XML)
class LimitsAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Forecast(self) -> str:
        return self.monitor.forecast_json()


@pyqtClassInfo("D-Bus Interface", MODELS_IFACE)
@pyqtClassInfo("D-Bus Introspection", MODELS_XML)
class ModelsAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Status(self) -> str:
        return self.monitor.status_json()

    @pyqtSlot()
    def Refresh(self) -> None:
        self.monitor.refresh()


@pyqtClassInfo("D-Bus Interface", COORDINATION_IFACE)
@pyqtClassInfo("D-Bus Introspection", COORDINATION_XML)
class CoordinationAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Coordinators(self) -> str:
        return self.monitor.coordinators_json()

    @pyqtSlot(str, result=str)
    def Board(self, coordinator_id: str) -> str:
        return self.monitor.board_json(coordinator_id)

    @pyqtSlot(str, str, result=str)
    def RunDetail(self, coordinator_id: str, run_id: str) -> str:
        return self.monitor.run_detail_json(coordinator_id, run_id)

    @pyqtSlot(str, result=bool)
    def Activate(self, coordinator_id: str) -> bool:
        return self.monitor.activate(coordinator_id)

    @pyqtSlot(str, str, result=bool)
    def OpenDiff(self, coordinator_id: str, ticket: str) -> bool:
        return self.monitor.open_diff(coordinator_id, ticket)

    @pyqtSlot(str, str, result=bool)
    def OpenWorktree(self, coordinator_id: str, ticket: str) -> bool:
        return self.monitor.open_worktree(coordinator_id, ticket)

    @pyqtSlot(str, result=bool)
    def ShowWindow(self, coordinator_id: str) -> bool:
        return self.monitor.show_window(coordinator_id)

    @pyqtSlot(bool)
    def Refresh(self, force: bool) -> None:
        self.monitor.refresh(force_tracker=bool(force))


@pyqtClassInfo("D-Bus Interface", BOARD_IFACE)
@pyqtClassInfo("D-Bus Introspection", BOARD_XML)
class BoardAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()

    def __init__(self, parent: QObject, monitor):
        super().__init__(parent)
        self.monitor = monitor
        monitor.changed.connect(self.Changed)

    @pyqtSlot(result=str)
    def Snapshot(self) -> str:
        return self.monitor.snapshot_json()

    @pyqtSlot(str, result=str)
    def CreateCard(self, request: str) -> str:
        return self.monitor.create_card(request)

    @pyqtSlot(str, str, result=str)
    def UpdateCard(self, card_id: str, request: str) -> str:
        return self.monitor.update_card(card_id, request)

    @pyqtSlot(str, str, int, result=str)
    def MoveCard(self, card_id: str, column: str, order: int) -> str:
        return self.monitor.move_card(card_id, column, order)

    @pyqtSlot(str, result=str)
    def UpsertProject(self, request: str) -> str:
        return self.monitor.upsert_project(request)

    @pyqtSlot(str, result=str)
    def UpsertTag(self, request: str) -> str:
        return self.monitor.upsert_tag(request)

    @pyqtSlot(str, str, result=str)
    def RenderFlow(self, card_id: str, mode: str) -> str:
        return self.monitor.render_flow(card_id, mode)

    @pyqtSlot(str, str, result=str)
    def RenderLaunch(self, card_id: str, request: str) -> str:
        return self.monitor.render_launch(card_id, request)

    @pyqtSlot(result=str)
    def Badge(self) -> str:
        return self.monitor.badge_json()

    @pyqtSlot(str, result=bool)
    def ShowWindow(self, card_id: str) -> bool:
        return self.monitor.show_window(card_id)



@pyqtClassInfo("D-Bus Interface", DOCKER_IFACE)
@pyqtClassInfo("D-Bus Introspection", DOCKER_XML)
class DockerAdaptor(QDBusAbstractAdaptor):
    Changed = pyqtSignal()
    ActionFinished = pyqtSignal(str, str)

    def __init__(self, parent: QObject, monitor, bus: QDBusConnection):
        super().__init__(parent)
        self.monitor = monitor
        self.bus = bus
        monitor.changed.connect(self.Changed)
        monitor.actionFinished.connect(self.ActionFinished)

    def _delayed(self, message: QDBusMessage, name: str):
        # Engine API odpowiada z wątku roboczego (stop czeka do 10 s) — odpowiedź D-Bus później, w terminie
        # REPLY_BUDGET (20 s) od wywołania, poniżej domyślnego limitu klienta 25 s (docker/engine.py).
        # Dłuższe prace to zadania.
        message.setDelayedReply(True)
        request = QDBusMessage(message)

        def reply(text: str) -> None:
            if not self.bus.send(request.createReply([text])):
                print(f"tide-daemon: nie udało się odesłać wyniku Docker.{name}", flush=True)

        return reply

    @pyqtSlot(result=str)
    def Snapshot(self) -> str:
        return self.monitor.snapshot_json()

    @pyqtSlot(QDBusMessage, result=str)
    def Stats(self, message: QDBusMessage) -> str:
        self.monitor.stats(self._delayed(message, "Stats"))
        return ""

    @pyqtSlot(QDBusMessage, result=str)
    def Disk(self, message: QDBusMessage) -> str:
        self.monitor.disk(self._delayed(message, "Disk"))
        return ""

    @pyqtSlot(QDBusMessage, result=str)
    def DiskNow(self, message: QDBusMessage) -> str:
        self.monitor.disk(self._delayed(message, "DiskNow"), fresh=True)
        return ""

    @pyqtSlot(str, QDBusMessage, result=str)
    def Start(self, ident: str, message: QDBusMessage) -> str:
        self.monitor.action(ident, "start", self._delayed(message, "Start"))
        return ""

    @pyqtSlot(str, QDBusMessage, result=str)
    def Stop(self, ident: str, message: QDBusMessage) -> str:
        self.monitor.action(ident, "stop", self._delayed(message, "Stop"))
        return ""

    @pyqtSlot(str, QDBusMessage, result=str)
    def Restart(self, ident: str, message: QDBusMessage) -> str:
        self.monitor.action(ident, "restart", self._delayed(message, "Restart"))
        return ""

    @pyqtSlot(str, QDBusMessage, result=str)
    def Remove(self, ident: str, message: QDBusMessage) -> str:
        self.monitor.remove(ident, self._delayed(message, "Remove"))
        return ""

    @pyqtSlot(str, str, result=str)
    def GroupAction(self, project: str, action: str) -> str:
        return self.monitor.group_action(project, action)

    @pyqtSlot(str, result=str)
    def Prune(self, categories: str) -> str:
        return self.monitor.prune(categories)

    @pyqtSlot(str, int, QDBusMessage, result=str)
    def Logs(self, ident: str, lines: int, message: QDBusMessage) -> str:
        self.monitor.logs(ident, lines, self._delayed(message, "Logs"))
        return ""

    @pyqtSlot(str, result=bool)
    def OpenLogs(self, ident: str) -> bool:
        return self.monitor.open_logs(ident)

    @pyqtSlot(str, result=bool)
    def OpenShell(self, ident: str) -> bool:
        return self.monitor.open_shell(ident)

    @pyqtSlot(result=str)
    def Badge(self) -> str:
        return self.monitor.badge_json()

    @pyqtSlot(str, result=bool)
    def ShowWindow(self, ident: str) -> bool:
        return self.monitor.show_window(ident)


def register(monitor, worktrees=None, bus: QDBusConnection | None = None, idle=None, reports=None,
             limits=None, tasks=None, coordination=None, board=None,
             docker=None, models=None) -> QObject:
    """Rejestruje obiekty i nazwę usługi. Zwraca obiekt, który trzeba trzymać przy życiu."""
    bus = bus or QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError("brak połączenia z magistralą sesji D-Bus")
    holder = QObject()
    holder.agents = QObject(holder)
    holder.agents.adaptor = AgentsAdaptor(holder.agents, monitor)
    if not bus.registerObject(AGENTS_PATH, holder.agents):
        raise RuntimeError(f"nie udało się zarejestrować obiektu {AGENTS_PATH}")
    if worktrees is not None:
        holder.worktrees = QObject(holder)
        holder.worktrees.adaptor = WorktreesAdaptor(holder.worktrees, worktrees, bus)
        if not bus.registerObject(WORKTREES_PATH, holder.worktrees):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {WORKTREES_PATH}")
    if tasks is not None:
        holder.tasks = QObject(holder)
        holder.tasks.adaptor = TasksAdaptor(holder.tasks, tasks, bus)
        if not bus.registerObject(TASKS_PATH, holder.tasks):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {TASKS_PATH}")
    if idle is not None:
        holder.idle = QObject(holder)
        holder.idle.adaptor = IdleAdaptor(holder.idle, idle)
        if not bus.registerObject(IDLE_PATH, holder.idle):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {IDLE_PATH}")
    if reports is not None:
        holder.reports = QObject(holder)
        holder.reports.adaptor = ReportsAdaptor(holder.reports, reports, bus)
        if not bus.registerObject(REPORTS_PATH, holder.reports):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {REPORTS_PATH}")
    if limits is not None:
        holder.limits = QObject(holder)
        holder.limits.adaptor = LimitsAdaptor(holder.limits, limits)
        if not bus.registerObject(LIMITS_PATH, holder.limits):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {LIMITS_PATH}")
    if models is not None:
        holder.models = QObject(holder)
        holder.models.adaptor = ModelsAdaptor(holder.models, models)
        if not bus.registerObject(MODELS_PATH, holder.models):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {MODELS_PATH}")
    if coordination is not None:
        holder.coordination = QObject(holder)
        holder.coordination.adaptor = CoordinationAdaptor(holder.coordination, coordination)
        if not bus.registerObject(COORDINATION_PATH, holder.coordination):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {COORDINATION_PATH}")
    if board is not None:
        holder.board = QObject(holder)
        holder.board.adaptor = BoardAdaptor(holder.board, board)
        if not bus.registerObject(BOARD_PATH, holder.board):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {BOARD_PATH}")
    if docker is not None:
        holder.docker = QObject(holder)
        holder.docker.adaptor = DockerAdaptor(holder.docker, docker, bus)
        if not bus.registerObject(DOCKER_PATH, holder.docker):
            raise RuntimeError(f"nie udało się zarejestrować obiektu {DOCKER_PATH}")
    if not bus.registerService(SERVICE):
        raise RuntimeError(f"nazwa {SERVICE} jest już zajęta (działa inna kopia usługi?)")
    return holder
