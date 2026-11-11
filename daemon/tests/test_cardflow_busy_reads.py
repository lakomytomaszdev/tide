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
    def blocked_tick(self, write=None):
        """Prawdziwy `engine.tick` na workerze `tide-cardflow`, zatrzymany w `_project_base` pod blokadą dziennika
        (`with journal.lock: store.project_card_base(...)`) aż do `release`; `write()` — zapis ticku tuż przed
        zatrzymaniem (wciąż pod blokadą). Zwraca (release, czy tick skończony)."""
        entered, release, where = threading.Event(), threading.Event(), []
        real = self.store.project_card_base

        def held(*args, **kwargs):
            where.append(threading.current_thread().name)
            if write is not None and not entered.is_set():
                write()
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

    @contextlib.contextmanager
    def blocked_action(self, flow=None):
        """Akcja w kolejce mutacji (jak StartCard) trzymająca blokadę dziennika aż do `release`. Zwraca
        (release, czy akcja doręczona)."""
        entered, release, finished = threading.Event(), threading.Event(), []

        def action():
            with self.engine.journal.lock:
                entered.set()
                release.wait(30)
            return {"ok": True}
        self.assertTrue((flow or self.live)._submit(action, finished.append))
        try:
            self.assertTrue(pump(entered.is_set), "akcja nie wystartowała")
            yield release, (lambda: bool(finished))
        finally:
            release.set()
            self.assertTrue(pump(lambda: finished, 40), "akcja nie skończyła się po odblokowaniu")

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
    def check_reads(self, card, version):
        """Snapshot (Panel karty), GetCardFlowSettings, BoxStatus i CardDocs odpowiadają; BoxStatus niesie wersję
        tej samej publikacji co Snapshot."""
        snap = self.snapshot()
        self.assertEqual(snap["readState"], "ready")
        version(snap["version"])
        panel = self.card_of(snap, card)["derived"]["flow"]
        self.assertTrue(panel.get("environment") or (panel.get("plan") or {}).get("environment"),
                        "Panel karty bez sekcji środowiska w opublikowanej wersji")
        settings = self.flow_settings()
        self.assertTrue(settings["ok"], settings)
        self.assertEqual(settings["readState"], "ready")
        self.assertTrue(settings["version"])
        status = self.box_status()
        self.assertTrue(status["ok"], status)
        self.assertEqual((status["version"], status["readState"]), (snap["version"], "ready"))
        docs = []
        self.board.card_docs(card, docs.append)
        self.assertTrue(pump(lambda: docs), "CardDocs bez odpowiedzi")
        self.assertTrue(json.loads(docs[0]).get("ok", True), docs[0])

    def test_reads_answer_while_the_tick_holds_the_journal(self):
        before = self.snapshot()
        card = self.plasma_cards[0]
        with self.blocked_tick() as (release, done):
            # Publikacja nie bierze blokady dziennika: tick jej nie wstrzymuje, odczyt jest świeży od razu.
            self.assertTrue(self.board.publish(), "publikacja czekała na blokadę trzymaną przez tick")
            self.board._plans_mark = None      # Snapshot opublikuje ponownie (jak przy nowym planie w repozytorium)
            self.check_reads(card, lambda v: self.assertGreaterEqual(v, before["version"]))
            # Wszystkie odpowiedzi przyszły, a worker mutacji nadal stoi w ticku pod blokadą dziennika.
            self.assertFalse(release.is_set())
            self.assertFalse(done(), "odpowiedź po zakończeniu ticku nie zalicza próby")
            free = self.engine.journal.lock.acquire(blocking=False)
            if free:
                self.engine.journal.lock.release()
            self.assertFalse(free, "tick nie trzymał blokady dziennika")

    def test_snapshot_shows_what_the_tick_already_wrote_while_it_still_holds_the_journal(self):
        # Przebieg 4 `make test-changed` (FullPlanningTest): test widzi zdarzenie ticku w pliku dziennika i od razu
        # czyta Snapshot. Publikacja musi je pokazać deterministycznie, choć tick dalej trzyma blokadę dziennika.
        card = self.proj_cards[0]
        before = self.snapshot()
        self.assertFalse(self.card_of(before, card)["derived"]["flow"]["paused"])
        with self.blocked_tick(write=lambda: self.engine.journal.append(card, "card.paused")) as (release, done):
            with open(self.engine.journal.mirror_path(card), encoding="utf-8") as fh:   # bez blokady — trzyma ją tick
                self.assertIn('"card.paused"', fh.read(), "tick nie zapisał zdarzenia")
            self.board._plans_mark = None      # jak nowy plan w repozytorium: Snapshot publikuje
            snap = self.snapshot()
            self.assertGreater(snap["version"], before["version"])
            self.assertTrue(self.card_of(snap, card)["derived"]["flow"]["paused"],
                            "Snapshot nie pokazuje zapisu ticku, który jest już w dzienniku")
            self.assertFalse(done())

    def test_reads_answer_while_an_action_holds_the_journal(self):
        before = self.snapshot()
        card = self.plasma_cards[0]
        with self.blocked_action() as (release, done):
            # Akcja w toku: publikacja odłożona (bez czekania), odczyty dostają poprzednią, spójną wersję.
            self.assertFalse(self.board.publish())
            self.board._plans_mark = None
            self.check_reads(card, lambda v: self.assertEqual(v, before["version"]))
            self.assertFalse(done(), "odpowiedź po zakończeniu akcji nie zalicza próby")

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
    def test_snapshot_shows_a_finished_action_while_another_thread_holds_the_journal(self):
        card = self.proj_cards[0]
        before = self.snapshot()
        self.assertFalse(self.card_of(before, card)["derived"]["flow"]["paused"])
        holding, release, appended, done = threading.Event(), threading.Event(), threading.Event(), []

        def pause():
            self.engine.journal.append(card, "card.paused")
            appended.set()

        def other_holder():   # inny wątek usługi (nie kolejka) trzyma blokadę dziennika
            appended.wait(5)
            with self.engine.journal.lock:
                holding.set()
                release.wait(10)
        self.assertTrue(self.live._submit(pause, done.append))
        # Bez pętli zdarzeń: `pause` kończy się na workerze, a drugi wątek bierze blokadę, zanim wątek główny
        # zdąży doręczyć wynik.
        holder = threading.Thread(target=other_holder)
        holder.start()
        self.addCleanup(holder.join)
        self.addCleanup(release.set)
        self.assertTrue(holding.wait(5))
        # Wynik dociera do wątku głównego sygnałem z workera; pod obciążeniem (pełny przebieg r1) jeszcze go nie ma
        # po jednym `processEvents`. Czekamy na samo doręczenie — wynik stoi w kolejce, bo commit czeka na blokadę.
        self.assertTrue(pump(lambda: self.live._deliveries), "wynik akcji nie dotarł do wątku głównego")
        snap = self.snapshot()
        self.assertGreater(snap["version"], before["version"])
        self.assertTrue(self.card_of(snap, card)["derived"]["flow"]["paused"])
        self.assertFalse(done, "odpowiedź akcji przed commitem dziennika")
        release.set()
        self.assertTrue(pump(lambda: done), "odpowiedź akcji po zwolnieniu blokady")


    def test_next_queued_job_starts_only_after_the_action_is_published(self):
        card = self.proj_cards[0]
        seen = []

        def pause():
            self.engine.journal.append(card, "card.paused")

        def next_job():   # np. tick tuż po akcji: musi zastać migawkę ze skutkiem akcji
            published = self.board._current
            seen.append(self.card_of(published, card)["derived"]["flow"]["paused"])
        self.assertTrue(self.live._submit(pause, lambda f: None))
        self.assertTrue(self.live._submit(next_job, lambda f: None, whole=False))
        self.assertTrue(pump(lambda: seen))
        self.assertEqual(seen, [True], "następna mutacja ruszyła przed publikacją skutku akcji")


class PreparingTest(BusyReadsCase):
    def test_before_the_first_publication_reads_say_preparing(self):
        fresh = BoardMonitor(self.store, debounce_ms=60_000, poll_ms=3_600_000)
        flow = CardFlowMonitor(self.engine, fresh, poll_ms=3_600_000)
        fresh.flow = flow
        self.addCleanup(flow.stop)
        self.addCleanup(fresh.stop)
        card = self.plasma_cards[0]
        with self.blocked_action(flow):        # akcja w kolejce tej usługi: pierwszej publikacji jeszcze nie ma
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
        # Odłożona pierwsza publikacja przychodzi sama po zakończeniu akcji.
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


class BoardTransactionBusyTest(BusyReadsCase):
    """Review r1 MAJOR 1: wczytanie Tablicy w publikacji nie czeka na transakcję Tablicy trzymaną przez mutację
    (worker: StartCard, scalenie, tick) ani przez inny proces — publikacja jest odłożona, Snapshot oddaje poprzednią
    wersję, a mutacja nadal trwa. Bez poprawki wątek główny stałby na `_transaction_lock`/`flock` do końca mutacji;
    strażnik zwalnia ją po WATCHDOG_S i oznacza to jako porażkę, żeby test nie wisiał."""

    # Pod obciążeniem pełnego przebiegu (14 procesów) BoxStatus i Snapshot trwają sekundy; strażnik musi być dłuższy
    # od nich, a krótszy od czasu pętli `pump` (40 s), żeby czekanie wątku głównego dało porażkę, a nie zawieszenie.
    WATCHDOG_S = 30.0

    @contextlib.contextmanager
    def held_transaction(self, submit):
        """`submit(job)` umieszcza `job` w kolejce mutacji; job wchodzi w `store.transaction()` i czeka. Zwraca
        (czy job nadal trwa, czy strażnik musiał zwolnić blokadę)."""
        inside, release, finished, timed_out = threading.Event(), threading.Event(), [], []

        def job():
            with self.store.transaction():
                inside.set()
                if not release.wait(self.WATCHDOG_S):
                    timed_out.append(True)
            return {"ok": True}
        submit(job, finished.append)
        try:
            self.assertTrue(pump(inside.is_set), "mutacja nie weszła w transakcję Tablicy")
            yield inside, (lambda: not finished), timed_out
        finally:
            release.set()
            self.assertTrue(pump(lambda: finished, 40), "mutacja nie skończyła się po odblokowaniu")
        self.assertEqual(timed_out, [], "wątek główny czekał na transakcję Tablicy trzymaną przez mutację")

    def test_publication_does_not_wait_for_a_tick_holding_the_board_transaction(self):
        # Tick nie jest akcją (bilet ważny), więc bez poprawki publikacja weszłaby w `store.load()` i czekała.
        before = self.snapshot()
        submit = lambda job, then: self.assertTrue(self.live._submit(job, then, whole=False))
        with self.held_transaction(submit) as (_, running, timed_out):
            self.assertFalse(self.board.publish(), "publikacja przy zajętej transakcji Tablicy")
            self.board._apply()               # przeliczenie z zegara (rederive) — też bez czekania
            self.board._plans_mark = None     # Snapshot spróbuje opublikować (czeka najwyżej SNAPSHOT_WAIT_S)
            snap = self.snapshot()
            self.assertEqual((snap["version"], snap["readState"]), (before["version"], "ready"))
            self.assertTrue(running(), "odpowiedź po zakończeniu mutacji nie zalicza próby")
            self.assertTrue(self.flow_settings()["ok"])
            self.assertTrue(self.box_status()["ok"])
            self.assertEqual(timed_out, [])
        # Odłożona publikacja przychodzi sama po zwolnieniu transakcji.
        self.assertTrue(pump(lambda: self.board._pending is None and self.board.publish()))

    def test_external_sync_does_not_wait_for_the_board_transaction(self):
        # Pełny przebieg r1: zegar synchronizacji z zewnątrz (debounce po zdarzeniu obserwatora plików) stał w wątku
        # głównym na `store.sync()` → `transaction()`, więc nie dochodziły odpowiedzi BoxStatus z puli odczytu.
        # Teraz commit zmian z zewnątrz i znacznik specyfikacji są odkładane do następnej synchronizacji.
        submit = lambda job, then: self.assertTrue(self.live._submit(job, then, whole=False))
        with self.held_transaction(submit) as (_, running, timed_out):
            self.board._debounce.stop()
            self.board.sync()
            self.assertTrue(running(), "sync wrócił dopiero po zakończeniu mutacji")
            self.assertTrue(self.board._debounce.isActive(), "odłożona synchronizacja nie jest zaplanowana")
            self.assertTrue(self.box_status()["ok"])
            self.assertEqual(timed_out, [])

    def test_snapshot_after_a_tick_write_waits_out_a_short_board_transaction(self):
        # Pełny przebieg r1 (`test_cardflow_ticket_wait`): test widzi zapis ticku w dzienniku i od razu czyta
        # Snapshot, a tick trzyma jeszcze krótką transakcję Tablicy. Publikacje w tle jej nie czekają, ale jawny
        # Snapshot() czeka najwyżej SNAPSHOT_WAIT_S — krótka transakcja ticku nie zostawia mu wersji sprzed zapisu.
        card = self.proj_cards[0]
        before = self.snapshot()
        self.assertFalse(self.card_of(before, card)["derived"]["flow"]["paused"])
        written, release, finished = threading.Event(), threading.Event(), []

        def tick_like():   # zapis ticku, potem krótka transakcja Tablicy (jak `project_card_base`)
            self.engine.journal.append(card, "card.paused")
            with self.store.transaction():
                written.set()
                release.wait(10)
        self.assertTrue(self.live._submit(tick_like, finished.append, whole=False))
        self.addCleanup(release.set)
        self.assertTrue(written.wait(10))
        self.assertFalse(self.board.publish(), "publikacja w tle czekała na transakcję")   # odłożona, bez czekania
        threading.Timer(0.05, release.set).start()     # transakcja kończy się w trakcie oczekiwania Snapshot()
        snap = self.snapshot()
        self.assertGreater(snap["version"], before["version"])
        self.assertTrue(self.card_of(snap, card)["derived"]["flow"]["paused"],
                        "Snapshot po zapisie ticku oddał wersję sprzed zapisu")
        self.assertTrue(pump(lambda: finished))

    def test_action_entering_the_board_transaction_after_the_gate_does_not_freeze_publication(self):
        # Okno ze zgłoszenia: bilet jest już ważny (akcji nie było), a akcja wchodzi w transakcję Tablicy, zanim
        # publikacja wczyta Tablicę — tu z `repos_source`, które stoi między bramką a `store.snapshot`.
        before = self.snapshot()
        state = {}
        real = self.board.repos_source

        def repos():
            if "running" not in state:
                cm = self.held_transaction(lambda job, then: self.assertTrue(self.live._submit(job, then)))
                state["cm"] = cm
                state["inside"], state["running"], state["timed_out"] = cm.__enter__()
            return real()
        with mock.patch.object(self.board, "repos_source", side_effect=repos):
            published = self.board.publish()
        try:
            self.assertFalse(published, "publikacja przy akcji trzymającej transakcję Tablicy")
            self.assertTrue(state["running"](), "publikacja wróciła dopiero po zakończeniu akcji")
            self.assertEqual(state["timed_out"], [])
            snap = self.snapshot()
            self.assertEqual(snap["version"], before["version"])
            self.assertTrue(state["running"]())
        finally:
            state["cm"].__exit__(None, None, None)

    def test_projection_does_not_wait_for_the_board_transaction(self):
        # Ścieżka `_apply` (zegar rederive, YouTrack): podstawa z pamięci, Tablicę czyta dopiero projekcja kart
        # (widok środowiska) — po bramce. Zajęta transakcja odkłada tę wersję zamiast podać treść zastępczą.
        before = self.snapshot()
        submit = lambda job, then: self.assertTrue(self.live._submit(job, then, whole=False))
        with self.held_transaction(submit) as (_, running, timed_out):
            self.assertFalse(self.board._apply())
            self.assertEqual(self.snapshot()["version"], before["version"])
            self.assertTrue(running())
        # Po zwolnieniu: odłożona wersja ma te same karty (z widokiem środowiska) co przed mutacją — trafienie na
        # zajętą transakcję nie zostawiło zastępczej treści ani w migawce, ani w pamięci widoku środowiska.
        # (`lastCommit` może się zmienić: commit dziennika z przygotowania testu.)
        self.assertTrue(pump(lambda: self.board._pending is None and self.board.publish()))
        self.assertEqual(json.loads(self.board._json)["cards"], before["cards"])

    def test_publication_does_not_wait_for_another_process_holding_the_board_lock(self):
        from tide_daemon.board import lockfile
        before = self.snapshot()
        holding, release, timed_out = threading.Event(), threading.Event(), []

        def other():   # inny „proces”: osobny deskryptor i `flock` (np. zapis profilu pudełka K-38 z CLI)
            with lockfile.exclusive(self.store.root):
                holding.set()
                if not release.wait(self.WATCHDOG_S):
                    timed_out.append(True)
        holder = threading.Thread(target=other)
        holder.start()
        try:
            self.assertTrue(holding.wait(5))
            self.assertFalse(self.board.publish())
            self.assertEqual(self.snapshot()["version"], before["version"])
            self.assertFalse(release.is_set())
        finally:
            release.set()
            holder.join()
        self.assertEqual(timed_out, [], "publikacja czekała na flock innego procesu")
        self.assertTrue(pump(lambda: self.board._pending is None and self.board.publish()))

    def test_writes_still_wait_for_the_board_transaction(self):
        # Granica tylko dla publikacji: zwykła transakcja (zapis) poza `without_waiting` dalej czeka.
        entered = threading.Event()
        with self.store.transaction():
            worker = threading.Thread(target=lambda: (self.store.load(), entered.set()))
            worker.start()
            self.assertFalse(entered.wait(0.2), "odczyt poza publikacją nie czekał na transakcję")
        worker.join(10)
        self.assertTrue(entered.is_set())
        with self.store.without_waiting() as probe:
            self.store.load()                 # wolna transakcja: zwykłe wczytanie
        self.assertFalse(probe.busy)


class DeliveryAndStrictReadTest(BusyReadsCase):
    def test_drain_is_not_reentered_by_a_synchronous_snapshot_reader(self):
        # Review r1 NIT 1: odbiorca Changed, który od razu woła Snapshot() (→ settle → _drain), nie doręcza wyniku
        # drugi raz ani nie zdejmuje cudzego elementu kolejki.
        card = self.proj_cards[0]
        answers = []
        hook = lambda: self.board.snapshot_json()
        self.board.changed.connect(hook)
        self.addCleanup(lambda: self.board.changed.disconnect(hook))
        self.assertTrue(self.live._submit(lambda: self.engine.journal.append(card, "card.paused"), answers.append))
        self.assertTrue(pump(lambda: answers))
        for _ in range(10):
            app().processEvents()
        self.assertEqual(len(answers), 1)
        self.assertEqual(self.live._deliveries, [])

    def test_strict_journal_readers_refuse_the_presentation_mode(self):
        # Review r1 NIT 4: czytniki bramek nie czytają bez blokady w odczycie prezentacji.
        from tide_daemon.cardflow.journal import StrictReadInPresentation
        card = self.proj_cards[0]
        self.assertTrue(self.engine.journal.read_complete(card))
        with self.engine.journal.presentation():
            self.assertTrue(self.engine.journal.read(card))
            with self.assertRaises(StrictReadInPresentation):
                self.engine.journal.read_complete(card)
            with self.assertRaises(StrictReadInPresentation):
                self.engine.journal.cards_complete()


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
