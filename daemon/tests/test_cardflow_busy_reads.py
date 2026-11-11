"""K-98 #2 (PT-343): odczyty Panelu karty odpowiadają, gdy kolejka mutacji jest zajęta.

- worker mutacji (`tide-cardflow`) zablokowany w prawdziwym ticku pod blokadą dziennika: Snapshot (Panel karty —
  `cards[].derived.flow`), GetCardFlowSettings, BoxStatus i CardDocs odpowiadają, zanim worker zostanie odblokowany,
- publikacja migawki nie miesza stanu sprzed i po mutacji: zapis przepływu w połowie (pod blokadą) nie trafia do
  żadnej opublikowanej wersji; każda odpowiedź niesie `version` i `readState`,
- przed pierwszą publikacją: jawne `preparing` (Snapshot, BoxStatus, CardDocs), a nie pusta, gotowa Tablica,
- zmiana karty, profilu, zgody i `boxWait` po zakończeniu mutacji jest widoczna w następnym odświeżeniu z nową wersją;
  opublikowany podgląd nie autoryzuje skutku (zgoda przeliczana świeżo w kolejce),
- zapisy i ciężkie podglądy Gita zostają w jednej, szeregowej kolejce; BoxStatus to odczyt poza nią, bez zapisu
  stanu slotu pudełka,
- na prawdziwej usłudze na prywatnej magistrali: StartCard zablokowany w `git worktree add` (blokada dziennika i
  transakcja Tablicy trzymane przez worker) — odczyty /Board odpowiadają przed odblokowaniem.

Testy nie mierzą czasu (pomiar p95: `perf_board_refresh.py --busy-ms`), tylko kolejność: odpowiedź przed odblokowaniem.
"""
import contextlib
import json
import os
import shutil
import threading
import time
from unittest import mock

from tide_daemon.board_monitor import BoardMonitor
from tide_daemon.box import admission
from tide_daemon.box import profile as bp
from tide_daemon.cardflow import boxwait
from tide_daemon.cardflow_monitor import CardFlowMonitor

from .boxflow import SLUG
from .privatebus import app, wait_until
from .test_cardflow_dbus import FlowCase
from .test_cardflow_refresh_context import PLASMA, RefreshContextCase

try:
    from PyQt6.QtDBus import QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply
except ImportError:  # pragma: no cover
    QDBusMessage = None


def pump(until, timeout=15.0):
    """Pętla zdarzeń Qt (odpowiedzi z pul wracają sygnałem do wątku głównego) aż do warunku."""
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.005)
    return until()


class BusyReadsCase(RefreshContextCase):
    """Usługa w procesie z włączonym pudełkiem: BoardMonitor + prawdziwy CardFlowMonitor z workerem mutacji."""

    def setUp(self):
        super().setUp()
        # RefreshContextCase zatrzymuje przepływ; tu potrzebny żywy worker (bez zegara ticku).
        self.live = CardFlowMonitor(self.engine, self.board, poll_ms=3_600_000)
        self.board.flow = self.live
        self.addCleanup(self.live.stop)
        self.assertTrue(self.board.publish())

    def snapshot(self, board=None):
        return json.loads((board or self.board).snapshot_json())

    @contextlib.contextmanager
    def blocked_tick(self):
        """Prawdziwy `engine.tick` na workerze `tide-cardflow`, zatrzymany w `_project_base` pod blokadą dziennika
        (`with journal.lock: store.project_card_base(...)`) aż do `release`. Zwraca (release, czy tick skończony)."""
        entered, release, where = threading.Event(), threading.Event(), []
        real = self.store.project_card_base

        def held(*args, **kwargs):
            where.append(threading.current_thread().name)
            entered.set()
            release.wait(30)
            return real(*args, **kwargs)
        with mock.patch.object(self.store, "project_card_base", side_effect=held):
            self.live.tick()
            try:
                self.assertTrue(pump(entered.is_set), "tick nie doszedł do zapisu pod blokadą dziennika")
                self.assertTrue(where[0].startswith("tide-cardflow"), where)
                self.assertFalse(self.live._ticking is False, "tick zakończył się przed odczytami")
                yield release, (lambda: not self.live._ticking)
            finally:
                release.set()
                self.assertTrue(pump(lambda: not self.live._ticking, 40), "tick nie skończył się po odblokowaniu")

    def flow_settings(self, board=None):
        got = []
        (board or self.board).get_card_flow_settings(got.append)
        self.assertTrue(pump(lambda: got), "GetCardFlowSettings bez odpowiedzi")
        return json.loads(got[0])

    def box_status(self, slug=SLUG, flow=None):
        got = []
        if flow is None:
            self.board.box_status(slug, got.append)
        else:
            flow.box_status(slug, got.append)
        self.assertTrue(pump(lambda: got), "BoxStatus bez odpowiedzi")
        return json.loads(got[0])


class BusyWorkerReadsTest(BusyReadsCase):
    def test_reads_answer_while_the_tick_holds_the_journal(self):
        before = self.snapshot()
        card = self.plasma_cards[0]
        with self.blocked_tick() as (release, done):
            # Publikacja nie czeka na blokadę dziennika — odkłada się, a odczyt dostaje poprzednią wersję.
            self.assertFalse(self.board.publish())
            self.board._plans_mark = None      # Snapshot spróbuje publikacji (jak przy nowym planie w repozytorium)
            snap = self.snapshot()
            self.assertEqual((snap["version"], snap["readState"]), (before["version"], "ready"))
            panel = self.card_of(snap, card)["derived"]["flow"]
            self.assertTrue(panel.get("environment") or (panel.get("plan") or {}).get("environment"),
                            "Panel karty bez sekcji środowiska w opublikowanej wersji")
            settings = self.flow_settings()
            self.assertTrue(settings["ok"], settings)
            self.assertEqual(settings["readState"], "ready")
            self.assertTrue(settings["version"])
            status = self.box_status()
            self.assertTrue(status["ok"], status)
            self.assertEqual((status["version"], status["readState"]), (before["version"], "ready"))
            docs = []
            self.board.card_docs(card, docs.append)
            self.assertTrue(pump(lambda: docs), "CardDocs bez odpowiedzi")
            self.assertTrue(json.loads(docs[0]).get("ok", True), docs[0])
            # Wszystkie odpowiedzi przyszły, a worker mutacji nadal stoi w ticku.
            self.assertFalse(release.is_set())
            self.assertFalse(done(), "odpowiedź po zakończeniu ticku nie zalicza próby")
        # Po zwolnieniu blokady odłożona publikacja wraca sama (bez nowego wywołania).
        self.assertTrue(pump(lambda: self.board._pending is None and not self.board._retry.isActive()))

    def test_publication_never_mixes_one_mutation_half_done(self):
        # Jedna sekcja blokady na oba zapisy oraz dwie osobne sekcje — jak `engine.moved` (card.moved, card.paused);
        # akcja z kolejki jest niepodzielna dla publikacji w obu przypadkach.
        for one_section in (True, False):
            with self.subTest(one_section=one_section):
                self.check_half_done(one_section)

    def check_half_done(self, one_section):
        a, b = self.proj_cards
        paused =self.card_of(self.snapshot(), a)["derived"]["flow"]["paused"]
        event = "card.resumed" if paused else "card.paused"
        before = self.snapshot()
        seen = []
        hook = lambda: seen.append(json.loads(self.board._json))
        self.board.changed.connect(hook)
        self.addCleanup(lambda: self.board.changed.disconnect(hook))
        midway, release = threading.Event(), threading.Event()

        def mutation():   # jedna akcja w kolejce: dwa zapisy dziennika
            with self.engine.journal.lock if one_section else contextlib.nullcontext():
                self.engine.journal.append(a, event)
                midway.set()
                release.wait(30)
                self.engine.journal.append(b, event)
        finished = []
        self.assertTrue(self.live._submit(mutation, finished.append))
        try:
            self.assertTrue(pump(midway.is_set))
            self.assertFalse(self.board.publish())
            self.board._apply()                  # przeliczenie z zegara — też odłożone
            for _ in range(20):                  # kolejne próby odłożonej publikacji przy trzymanej blokadzie
                app().processEvents()
                time.sleep(0.01)
            snap = self.snapshot()
            self.assertEqual(snap["version"], before["version"])
            self.assertEqual(self.card_of(snap, a)["derived"]["flow"]["paused"], paused, "połowa mutacji w odczycie")
        finally:
            release.set()
        self.assertTrue(pump(lambda: finished))
        self.assertTrue(pump(lambda: self.snapshot()["version"] > before["version"]))
        after = self.snapshot()
        self.assertEqual(self.card_of(after, a)["derived"]["flow"]["paused"], not paused)
        self.assertEqual(self.card_of(after, b)["derived"]["flow"]["paused"], not paused)
        for published in seen:
            pa = self.card_of(published, a)["derived"]["flow"]["paused"]
            pb = self.card_of(published, b)["derived"]["flow"]["paused"]
            self.assertEqual(pa, pb, f"wersja {published['version']} miesza stan sprzed i po mutacji")


class ReadAfterWriteTest(BusyReadsCase):
    def test_snapshot_shows_a_finished_action_while_the_next_job_holds_the_journal(self):
        card = self.proj_cards[0]
        before = self.snapshot()
        self.assertFalse(self.card_of(before, card)["derived"]["flow"]["paused"])
        holding, release, done = threading.Event(), threading.Event(), []

        def pause():
            self.engine.journal.append(card, "card.paused")

        def next_job():   # następna akcja trzyma blokadę dziennika krótko (krócej niż SETTLE_MS)
            holding.set()
            release.wait(5)
        self.assertTrue(self.live._submit(pause, done.append))
        self.assertTrue(self.live._submit(next_job, lambda f: None))
        self.assertTrue(pump(holding.is_set))
        app().processEvents()                  # wynik `pause` dociera, ale publikacja czeka na blokadę
        self.assertFalse(done, "wynik akcji opublikowany mimo trzymanej blokady")
        threading.Timer(0.05, release.set).start()
        snap = self.snapshot()                 # czeka najwyżej SETTLE_MS na blokadę i publikuje zaległy wynik
        self.assertGreater(snap["version"], before["version"])
        self.assertTrue(self.card_of(snap, card)["derived"]["flow"]["paused"])
        self.assertTrue(done, "odpowiedź akcji przyszła dopiero po publikacji jej skutku")


class PreparingTest(BusyReadsCase):
    def test_before_the_first_publication_reads_say_preparing(self):
        fresh = BoardMonitor(self.store, debounce_ms=60_000, poll_ms=3_600_000)
        flow = CardFlowMonitor(self.engine, fresh, poll_ms=3_600_000)
        fresh.flow = flow
        self.addCleanup(flow.stop)
        self.addCleanup(fresh.stop)
        card = self.plasma_cards[0]
        with self.blocked_tick():
            snap = self.snapshot(fresh)
            self.assertEqual((snap["readState"], snap["version"], snap["cards"]), ("preparing", 0, []))
            self.assertIsNone(fresh.published())
            status = self.box_status(flow=flow)
            self.assertEqual((status["ok"], status["error"], status["readState"], status["state"]),
                             (False, "preparing", "preparing", "unknown"))
            docs = []
            fresh.card_docs(card, docs.append)
            self.assertEqual(json.loads(docs[0])["error"], "preparing")
            work = json.loads(fresh.work_json())
            self.assertEqual(work["readState"], "preparing")
            self.assertEqual(self.flow_settings(fresh)["readState"], "ready")   # plik ustawień nie zależy od Tablicy
        # Odłożona pierwsza publikacja przychodzi sama po zwolnieniu blokady.
        self.assertTrue(pump(lambda: fresh._json))
        ready = json.loads(fresh._json)
        self.assertEqual((ready["readState"], ready["version"]), ("ready", 1))
        self.assertIn(card, [c["id"] for c in ready["cards"]])
        self.assertEqual(self.box_status(flow=flow)["readState"], "ready")


class FreshAfterMutationTest(BusyReadsCase):
    def test_same_state_keeps_the_version_and_every_change_gets_a_new_one(self):
        card = self.plasma_cards[0]
        v0 = self.snapshot()["version"]
        self.assertTrue(self.board.publish())
        self.assertEqual(self.snapshot()["version"], v0, "odczyt bez zmiany stanu nie podbija wersji")

        # Karta (zapis Tablicy).
        self.assertTrue(json.loads(self.board.update_card(card, json.dumps({"title": "Nowy tytuł"})))["ok"])
        snap = self.snapshot()
        self.assertGreater(snap["version"], v0)
        self.assertEqual(self.card_of(snap, card)["title"], "Nowy tytuł")
        v1 = snap["version"]

        # Zgoda środowiska przez kolejkę mutacji — widoczna w następnym odświeżeniu, z nową wersją.
        view = self.view(snap, card)
        self.assertFalse(view["exposure"]["approved"])
        replies = []
        self.board.approve_card_environment(card, self.consent_text(view, accepted=True), False, replies.append)
        self.assertTrue(pump(lambda: replies))
        self.assertTrue(json.loads(replies[0])["ok"], replies[0])
        self.assertTrue(self.board.publish())    # następne odświeżenie (zgoda projektu nie zapisuje dziennika karty)
        snap = self.snapshot()
        self.assertGreater(snap["version"], v1)
        self.assertTrue(self.view(snap, card)["exposure"]["approved"])
        v2 = snap["version"]

        # Profil projektu (wejście widoku środowiska) — nowy odcisk, zgoda na stary zakres już nie obowiązuje.
        view = self.view(snap, card)
        bp.save_project(PLASMA, {"tide-box": 1, "project": PLASMA, "network": {"domains": ["github.com"]}})
        self.assertTrue(self.board.publish())
        snap = self.snapshot()
        self.assertGreater(snap["version"], v2)
        self.assertNotEqual(self.view(snap, card), view)
        self.assertEqual(self.view(snap, card), self.fresh(card))
        self.assertFalse(self.view(snap, card)["exposure"]["approved"])

    def test_box_status_follows_the_published_box_wait(self):
        card = self.proj_cards[0]
        published = self.board.published()
        first = self.box_status()
        # BoxStatus niesie wersję publikacji, z której wziął Tablicę i boxWait (Snapshot mógłby sam opublikować nową).
        self.assertEqual(first["version"], published.version)
        self.assertIn({"card": card, "boxWait": ""}, first["cards"])
        boxwait.set_box_wait(self.engine.journal, card, boxwait.START, source="test")
        self.assertTrue(self.board.publish())
        second = self.box_status()
        self.assertGreater(second["version"], first["version"])
        self.assertIn({"card": card, "boxWait": boxwait.START}, second["cards"])

    def test_published_view_never_authorizes_the_effect(self):
        card = self.plasma_cards[0]
        stale = self.view(self.snapshot(), card)
        # Wejście zmienia się bez publikacji: odczyt dalej pokazuje stary odcisk, zgoda na nim jest odrzucona.
        bp.save_project(PLASMA, {"tide-box": 1, "project": PLASMA, "network": {"domains": ["github.com"]}})
        self.assertEqual(self.view(self.snapshot(), card), stale)
        replies = []
        self.board.approve_card_environment(card, self.consent_text(stale, accepted=True), False, replies.append)
        self.assertTrue(pump(lambda: replies))
        self.assertFalse(json.loads(replies[0])["ok"], replies[0])
        self.assertEqual([e for e in self.engine.journal.read(card) if e.get("type") == "plan.approved"], [])


class SerialWritesTest(BusyReadsCase):
    def test_box_status_is_a_read_off_the_queue_without_writes(self):
        state = os.path.join(admission.folder(), "state.json")
        stamp = os.stat(state).st_mtime_ns if os.path.exists(state) else None
        events = {c: len(self.engine.journal.read(c)) for c in self.proj_cards + self.plasma_cards}
        threads = []
        real = self.engine.box_status

        def status(*args):
            threads.append(threading.current_thread().name)
            return real(*args)
        with mock.patch.object(self.live, "_submit", side_effect=AssertionError("BoxStatus w kolejce mutacji")), \
                mock.patch.object(self.engine, "box_status", side_effect=status):
            self.assertTrue(self.box_status()["ok"])
        self.assertTrue(threads and threads[0].startswith("tide-board-read"), threads)
        self.assertEqual(os.stat(state).st_mtime_ns if os.path.exists(state) else None, stamp,
                         "BoxStatus zapisał stan slotu pudełka")
        self.assertEqual({c: len(self.engine.journal.read(c)) for c in events}, events)

    def test_writes_and_heavy_git_previews_stay_in_one_serial_queue(self):
        active, peak, threads = [0], [0], []
        guard = threading.Lock()

        def tracked(result):
            def run(*args):
                with guard:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                    threads.append(threading.current_thread().name)
                time.sleep(0.02)
                with guard:
                    active[0] -= 1
                return result
            return run
        replies = []
        with mock.patch.object(self.engine, "set_paused", side_effect=tracked({"ok": True})), \
                mock.patch.object(self.engine, "card_diff", side_effect=tracked({"ok": True})), \
                mock.patch.object(self.engine, "preview_card_merge", side_effect=tracked({"ok": True})):
            for card in self.proj_cards + self.plasma_cards:
                self.board.set_card_paused(card, True, replies.append)
            self.board.card_branch_diff(self.proj_cards[0], replies.append)
            self.board.preview_card_merge(self.proj_cards[0], "{}", replies.append)
            self.assertTrue(pump(lambda: len(replies) == 6))
        self.assertEqual(peak[0], 1, "dwa zapisy naraz")
        self.assertTrue(all(name.startswith("tide-cardflow") for name in threads), threads)


INCUS_EMPTY = "#!/bin/sh\necho '[]'\n"
GIT_GATE = """#!/bin/sh
# Atrapa testu K-98 #2: gdy istnieje plik bramki, polecenie z jego wzorcem czeka, aż plik zniknie.
if [ -e {gate} ]; then
    pattern=$(cat {gate})
    case " $* " in
        *"$pattern"*)
            touch {gate}.entered
            while [ -e {gate} ]; do sleep 0.05; done
            ;;
    esac
fi
exec {git} "$@"
"""


class BusyWorkerDbusTest(FlowCase):
    """Prawdziwa usługa na prywatnej magistrali; worker mutacji blokuje się w prawdziwym StartCard."""

    def setUp(self):
        super().setUp()
        fakebin = os.path.join(self.base, "fakebin")
        self.gate = os.path.join(self.base, "git-gate")
        with open(os.path.join(fakebin, "git"), "w", encoding="utf-8") as fh:
            fh.write(GIT_GATE.format(gate=self.gate, git=shutil.which("git", path="/usr/bin:/bin")))
        with open(os.path.join(fakebin, "incus"), "w", encoding="utf-8") as fh:
            fh.write(INCUS_EMPTY)   # BoxStatus nie pyta prawdziwego Incusa hosta
        for name in ("git", "incus"):
            os.chmod(os.path.join(fakebin, name), 0o755)

    def async_call(self, member, *args):
        msg = QDBusMessage.createMethodCall("org.tide.Daemon", "/Board", "org.tide.Daemon.Board", member)
        msg.setArguments(list(args))
        return QDBusPendingCallWatcher(self.service.bus.asyncCall(msg, 120000))

    def read(self, member, *args):
        reply = self.service.call("org.tide.Daemon", "/Board", "org.tide.Daemon.Board", member, *args,
                                  timeout=15000)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, f"{member}: {reply.errorMessage()}")
        return json.loads(reply.arguments()[0])

    def test_board_reads_answer_while_start_card_holds_the_worker(self):
        card = self.card()
        before = self.read("Snapshot")
        self.assertEqual(before["readState"], "ready")
        with open(self.gate, "w", encoding="utf-8") as fh:
            fh.write("worktree add")
        pending = self.async_call("StartCard", card, json.dumps({"entry": "now", "executor": "Sonnet"}))
        try:
            self.assertTrue(wait_until(lambda: os.path.exists(self.gate + ".entered"), timeout=60),
                            "StartCard nie doszedł do git worktree add")
            # Zmiany dziennika w Tablicy budzą synchronizację (DEBOUNCE_MS = 2 s) — dawniej czekała na blokadę
            # dziennika w wątku głównym i zamrażała wszystkie odczyty.
            time.sleep(2.5)
            snap = self.read("Snapshot")
            self.assertEqual(snap["readState"], "ready")
            self.assertGreaterEqual(snap["version"], before["version"])
            self.assertIn(card, [c["id"] for c in snap["cards"]])
            settings = self.read("GetCardFlowSettings")
            self.assertTrue(settings["ok"], settings)
            self.assertTrue(settings["version"])
            status = self.read("BoxStatus", "proj")
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["readState"], "ready")
            docs = self.read("CardDocs", card)
            self.assertNotEqual(docs.get("error"), "internal", docs)
            app().processEvents()
            self.assertTrue(os.path.exists(self.gate), "worker odblokowany przed odczytami")
            self.assertFalse(pending.isFinished(), "StartCard skończył się przed odczytami — próba się nie liczy")
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.gate)
        self.assertTrue(wait_until(pending.isFinished, timeout=120), "StartCard bez odpowiedzi po odblokowaniu")
        started = json.loads(QDBusPendingReply(pending).argumentAt(0))
        self.assertTrue(started["ok"], started)
        # Wynik mutacji widać w następnym odczycie, z nową wersją.
        self.assertTrue(wait_until(lambda: self.read("Snapshot")["version"] > snap["version"] and
                                   (self.shown(card).get("derived") or {}).get("flow"), timeout=30))
