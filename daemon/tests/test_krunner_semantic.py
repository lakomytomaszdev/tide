"""Wyszukiwanie po znaczeniu w KRunnerze (PT-19): dokument sesji, plik indeksu, indeksowanie przyrostowe,
przebudowa po zmianie modelu, granice rozmiaru, wyłączenie, kolejka modeli, ranking, zapytanie (cisza
przed zapytaniem, pamięć, serwer nie odpowiada, budżet czasu) i łączenie z wynikami słownymi w KRunnerCore.

Model to atrapa: wektor „pojęć” (docker, logowanie, raport, wysyłanie) z tekstu — podobne znaczeniowo
teksty bez wspólnych słów dają podobne wektory, tak jak prawdziwy bge-m3. Wywołania przez prawdziwe
models.embed idą do atrapy serwera HTTP (test_models_core.FakeModelServer), nigdy do Ollamy tego komputera.
"""
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from . import history
from .fakebin import make_env
from .test_krunner_core import RecordingLaunchers
from .test_models_core import closed_port, start_server, write_models
from tide_daemon.krunner import semantic
from tide_daemon.krunner.index import SessionIndex
from tide_daemon.krunner.runner import LOW, MEANING_NOTE, KRunnerCore
from tide_daemon.models import client as models_client
from tide_daemon.models import config as models_config
from tide_daemon.models import queue as models_queue
from tide_daemon.models.transport import ModelError

NOW = 1_800_000_000.0
DAY = 86400.0

# Pojęcia atrapy: słowa (rdzenie) w różnych językach i formach — „znaczenie” bez wspólnych słów.
CONCEPTS = [
    ("docker", "kontener", "container", "port"),
    ("login", "logow", "zaloguj", "uwierzyt"),
    ("raport", "report", "daily", "poranny"),
    ("upload", "wysył", "wysyl", "retries", "ponawia"),
    ("motyw", "sddm", "theme"),
]


def concept_vector(text: str) -> list:
    low = text.casefold()
    return [1.0 if any(stem in low for stem in stems) else 0.0 for stems in CONCEPTS] + [0.3]


class FakeEmbed:
    """Atrapa models.embed: zapisuje wywołania; `error` — wyjątek przy następnym wywołaniu; `delay` — sekundy."""

    def __init__(self, model="bge-m3", dims=None):
        self.model = model
        self.dims = dims
        self.calls = []
        self.error = None
        self.delay = 0.0
        self.lock = threading.Lock()

    def __call__(self, texts, *, role=None, priority=None, wait=None, timeout=None, cancel=None, **_):
        with self.lock:
            self.calls.append({"texts": list(texts), "role": role, "priority": priority, "wait": wait,
                               "timeout": timeout})
            error = self.error
        if self.delay:
            time.sleep(self.delay)
        if error is not None:
            raise error
        vectors = [concept_vector(t) for t in texts]
        if self.dims:
            vectors = [(v * self.dims)[:self.dims] for v in vectors]
        return models_client.Embeddings(vectors, "local", self.model, "http://127.0.0.1:9")

    def texts(self):
        return [t for c in self.calls for t in c["texts"]]


class FakeIndex:
    def __init__(self, entries):
        self.items = list(entries)
        self.refreshed = 0
        self.built = True

    def refresh(self):
        self.refreshed += 1

    def entries(self):
        return list(self.items)


def entry(sid, title, *, project="plasma-tide", branch="master", prompt="", age=1.0, cwd="/tmp", tool="claude"):
    return {"id": f"{tool}:{sid}", "tool": tool, "sessionId": sid, "cwd": cwd, "project": project, "branch": branch,
            "title": title, "prompt": prompt, "lastActivity": NOW - age * DAY, "desktop": False, "source": ""}


def config(data=None):
    return lambda: models_config.from_dict(data or {})


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def stored(path):
    """Pozycje pliku indeksu po odtworzeniu dziennika: (model, {id: (skrót, wektor)})."""
    store = semantic.Store(path)
    assert store.load() == "", path
    return store.model, {k: (v["hash"], list(v["vec"])) for k, v in store.items.items()}


class DocumentTest(unittest.TestCase):
    def test_document_has_only_metadata_and_tickets(self):
        e = entry("a", "Kafelek Docker w docku", prompt="widżet docker z listą kontenerów", branch="feat/pt-10_docker.tile")
        text = semantic.document(e, ["PT-10 Kafelek Docker", "PT-11 Raporty w oknie"])
        self.assertEqual(text, "Kafelek Docker w docku\nwidżet docker z listą kontenerów\nplasma-tide · feat pt 10 docker "
                               "tile\nPT-10 Kafelek Docker; PT-11 Raporty w oknie")
        same = semantic.document(entry("b", "Tytuł", prompt="Tytuł"))
        self.assertEqual(same, "Tytuł\nplasma-tide · master", "początek rozmowy równy tytułowi — raz")

    def test_secrets_are_stripped_and_home_hidden(self):
        token = "sk-ant-" + "A1b2C3d4" * 4
        e = entry("a", f"klucz {token} w /home/ktos/projekt/plik.env", prompt="export GITHUB_TOKEN=ghp_" + "x" * 30)
        text = semantic.document(e, home="/home/ktos")
        self.assertNotIn(token, text)
        self.assertNotIn("ghp_", text)
        self.assertIn("[ukryte]", text)
        self.assertIn("~/projekt/plik.env", text)
        self.assertNotIn("/home/ktos", text)

    def test_document_is_clipped(self):
        e = entry("a", "x" * 90, prompt="y" * 99)
        long = semantic.document(e, [f"PT-{n} " + "z" * 80 for n in range(30)])
        self.assertLessEqual(len(long), semantic.DOC_LIMIT)
        self.assertLessEqual(long.count("PT-"), semantic.TICKETS_MAX)
        few = semantic.document(entry("a", "t"), [f"PT-{n} x" for n in range(30)])
        self.assertEqual(few.count("PT-"), semantic.TICKETS_MAX)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-search-store-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "data", "tide", "search", "sessions.json")

    def test_round_trip_private_file_without_texts(self):
        store = semantic.Store(self.path)
        store.model, store.dims = "bge-m3:latest", 3
        store.items["claude:a"] = {"hash": "h1", "ts": NOW, "vec": semantic.normalize([1.0, 2.0, 2.0])}
        store.updated = NOW
        store.save()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        records = read_lines(self.path)
        self.assertEqual(records[0], {"version": 2, "model": "bge-m3:latest", "dims": 3, "updated": NOW})
        self.assertEqual(set(records[1]), {"id", "hash", "ts", "vec"}, "bez tekstu dokumentu")
        again = semantic.Store(self.path)
        self.assertEqual(again.load(), "")
        self.assertEqual((again.model, again.dims, again.updated), ("bge-m3:latest", 3, NOW))
        for got, want in zip(again.items["claude:a"]["vec"], (1 / 3, 2 / 3, 2 / 3)):
            self.assertAlmostEqual(got, want, places=3)  # float16

    def test_broken_old_or_foreign_file_starts_empty(self):
        os.makedirs(os.path.dirname(self.path))
        old = json.dumps({"version": 1, "model": "m", "dims": 2, "updated": 0,
                          "items": {"ok": {"hash": "h", "ts": 1, "vec": semantic.pack([0.6, 0.8])}}})
        head = '{"version":2,"model":"m","dims":2,"updated":0}\n'
        for text, problem in (("{", "nieczytelny"), ('{"version": 99}\n', "innej wersji"),
                              (old, "innej wersji"), (old + "\n", "innej wersji"), ("", "nieczytelny"),
                              ('{"version":2,"model":1,"dims":3}\n', "nieczytelny"),
                              (head + "nie json\n", "nieczytelny"),
                              (head + '{"id":"a","hash":"h","ts":1,"vec":"' + semantic.pack([0.6, 0.8]) + '"}', "urwany"),
                              (head + '{"coś":1}\n', "nieczytelny")):
            with self.subTest(text=text[:40]):
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                store = semantic.Store(self.path)
                self.assertIn(problem, store.load())
                self.assertEqual(store.items, {})
                self.assertTrue(store.unsaved(), "następny zapis przepisze plik w całości")
        with open(self.path, "w", encoding="utf-8") as fh:  # zła pozycja — pominięta, reszta zostaje
            fh.write(head + "".join(json.dumps(r) + "\n" for r in (
                {"id": "ok", "hash": "h", "ts": 1, "vec": semantic.pack([0.6, 0.8])},
                {"id": "short", "hash": "h", "ts": 1, "vec": semantic.pack([0.6])},
                {"id": "bad", "hash": "h", "ts": 1, "vec": "%%"})))
        store = semantic.Store(self.path)
        self.assertEqual(store.load(), "")
        self.assertEqual(list(store.items), ["ok"])

    def test_journal_replays_puts_touches_and_deletes(self):
        store = semantic.Store(self.path)
        store.model, store.dims = "m", 2
        store.put("a", "h1", 1.0, semantic.normalize([1.0, 0.0]))
        store.put("b", "h2", 2.0, semantic.normalize([0.0, 1.0]))
        store.flush()
        self.assertEqual(store.counters["fullWrites"], 1, "nowy plik — pełny zapis")
        store.put("a", "h3", 3.0, semantic.normalize([1.0, 1.0]))
        store.touch("b", 5.0)
        store.drop("c")      # nieznana — nic
        store.flush()
        store.drop("b")
        store.updated = 7.0
        store.flush()
        self.assertEqual((store.counters["fullWrites"], store.counters["appends"]), (1, 2))
        again = semantic.Store(self.path)
        self.assertEqual(again.load(), "")
        self.assertEqual(sorted(again.items), ["a"])
        self.assertEqual((again.items["a"]["hash"], again.items["a"]["ts"], again.updated), ("h3", 3.0, 7.0))
        self.assertFalse(again.unsaved(), "plik zgodny z pamięcią — nic do zapisu")

    def test_journal_is_compacted_when_it_grows(self):
        store = semantic.Store(self.path)
        store.model, store.dims = "m", 2
        store.put("a", "h", 1.0, semantic.normalize([1.0, 0.0]))
        store.flush()
        for n in range(semantic.COMPACT_SLACK + 5):
            store.touch("a", 10.0 + n)
            store.flush()
        self.assertEqual(store.counters["fullWrites"], 2, "dziennik przepisany raz po przekroczeniu progu")
        self.assertLessEqual(len(read_lines(self.path)), semantic.COMPACT_SLACK + 3)
        again = semantic.Store(self.path)
        again.load()
        self.assertEqual(again.items["a"]["ts"], 10.0 + semantic.COMPACT_SLACK + 4)


class IndexingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-search-index-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "search", "sessions.json")
        self.index = FakeIndex([
            entry("docker", "Kontenery i mapowanie portów", age=1),
            entry("login", "Formularz logowania", age=2),
            entry("report", "Poprawki sekcji Na daily", age=3),
        ])
        self.embed = FakeEmbed()
        self.settings = {}
        self.changes = 0

    def search(self, **kwargs):
        s = semantic.SemanticSearch(self.index, self.path, config=lambda: models_config.from_dict(self.settings),
                                    embed=self.embed, clock=lambda: NOW, home="/home/ktos", debounce=0, **kwargs)
        s.add_listener(self.count)
        return s

    def count(self):
        self.changes += 1

    def test_first_pass_embeds_everything_at_idle_priority(self):
        s = self.search()
        s.sync()
        self.assertEqual(len(self.embed.texts()), 3)
        self.assertEqual({c["priority"] for c in self.embed.calls}, {models_queue.IDLE})
        self.assertEqual({c["role"] for c in self.embed.calls}, {"embed"})
        state = s.state()
        self.assertEqual((state["state"], state["items"], state["pending"], state["model"], state["dims"], state["error"]),
                         ("ready", 3, 0, "bge-m3:latest", len(CONCEPTS) + 1, ""))
        self.assertTrue(state["updated"].startswith("2027-01-15T"), state["updated"])
        self.assertTrue(os.path.exists(self.path))
        self.assertGreater(self.changes, 0, "zmiana stanu dla /Models")

    def test_incremental_only_new_and_changed(self):
        s = self.search()
        s.sync()
        self.embed.calls.clear()
        s.sync()
        self.assertEqual(self.embed.calls, [], "bez zmian — żadnego wywołania")
        self.index.items[1] = entry("login", "Formularz logowania i walidacja", age=2)
        self.index.items.append(entry("upload", "Ponawianie wysyłania plików", age=0.5))
        s.sync()
        self.assertEqual(sorted(self.embed.texts()), ["Formularz logowania i walidacja\nplasma-tide · master",
                                                      "Ponawianie wysyłania plików\nplasma-tide · master"])
        self.assertEqual(s.state()["items"], 4)
        # Nowy proces czyta indeks z dysku i niczego nie liczy od nowa.
        self.embed.calls.clear()
        fresh = self.search()
        fresh.sync()
        self.assertEqual(self.embed.calls, [])
        self.assertEqual(fresh.state()["items"], 4)

    def test_sessions_out_of_window_or_over_limit_drop_out(self):
        s = self.search()
        s.sync()
        self.index.items[2] = entry("report", "Poprawki sekcji Na daily", age=31)  # poza oknem 30 dni
        s.sync()
        self.assertEqual(sorted(s.store.items), ["claude:docker", "claude:login"])
        with mock.patch.object(semantic, "MAX_ITEMS", 1):
            s.sync()
        self.assertEqual(list(s.store.items), ["claude:docker"], "najświeższa zostaje")
        self.assertEqual(list(stored(self.path)[1]), ["claude:docker"])

    def test_model_change_rebuilds_index(self):
        s = self.search()
        s.sync()
        self.embed.calls.clear()
        self.settings = {"roles": {"embed": "nomic-embed-text"}}
        self.embed.model = "nomic-embed-text"
        s.sync()
        self.assertEqual(len(self.embed.texts()), 3, "inny model — wszystko od nowa")
        self.assertEqual(s.state()["model"], "nomic-embed-text:latest")
        self.assertEqual(stored(self.path)[0], "nomic-embed-text:latest")

    def test_dimension_change_resets_and_rebuilds(self):
        s = self.search()
        s.sync()
        self.index.items.append(entry("upload", "Ponawianie wysyłania", age=0.5))
        self.embed.dims = 8  # ten sam model, inne wektory (np. inna wersja) — nieporównywalne
        s.sync()
        self.assertEqual(s.store.items, {}, "niezgodne wymiary — indeks wyczyszczony")
        self.embed.calls.clear()
        s.sync()
        self.assertEqual(len(self.embed.texts()), 4)
        self.assertEqual(s.state()["dims"], 8)

    def test_disabled_indexes_nothing_and_removes_file(self):
        s = self.search()
        s.sync()
        self.assertTrue(os.path.exists(self.path))
        self.settings = {"semanticSearch": False}
        self.embed.calls.clear()
        s.sync()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.embed.calls, [])
        self.assertEqual((s.state()["state"], s.state()["items"]), ("off", 0))
        self.assertFalse(s.ready())
        self.assertFalse(s.wants("ta sesja o dockerze i portach"))

    def test_errors_are_state_not_exceptions(self):
        s = self.search()
        self.embed.error = ModelError("Model lokalny: serwer odpowiedział 404: model \"bge-m3\" not found (127.0.0.1:11434)")
        s.sync()
        state = s.state()
        self.assertEqual((state["state"], state["items"], state["pending"]), ("error", 0, 3))
        self.assertIn("not found", state["error"])
        self.embed.error = models_queue.Busy("Model lokalny: kolejka: czekał 300 s — serwer zajęty: raport poranny")
        s.sync()
        self.assertEqual(s.state()["state"], "waiting", "zajęty serwer to nie błąd — następny przebieg")
        self.assertTrue(s._retry)
        self.embed.error = None
        s.sync()
        self.assertEqual((s.state()["state"], s.state()["error"]), ("ready", ""))
        self.settings = {"roles": {"embed": "kimi-k2:cloud"}}
        s.sync()
        self.assertEqual(s.state()["state"], "error")
        self.assertIn("chmurze Ollamy", s.state()["error"])

    def test_turning_off_cancels_waiting_batch_and_deletes_file_at_once(self):
        s = self.search()
        s.sync()
        self.assertTrue(os.path.exists(self.path))
        self.index.items.append(entry("upload", "Ponawianie wysyłania", age=0.5))
        waiting = threading.Event()

        def blocked(texts, *, cancel=None, **kwargs):
            # Partia czeka w kolejce (IDLE) — kończy się dopiero przerwaniem przebiegu, jak w models.queue.
            waiting.set()
            for _ in range(500):
                if cancel is not None and cancel.is_set():
                    raise ModelError("Model lokalny: wywołanie przerwane — usługa się zamyka (127.0.0.1:11434)")
                time.sleep(0.01)
            return self.embed(texts, **kwargs)

        s.embed = blocked
        worker = threading.Thread(target=s.sync)
        worker.start()
        self.assertTrue(waiting.wait(5))
        self.settings = {"semanticSearch": False}
        s.wake()  # tak budzi go zapis ustawień (models_monitor) — w wątku wołającego
        self.assertFalse(os.path.exists(self.path), "plik znika od razu, nie po przebiegu")
        worker.join(3)
        self.assertFalse(worker.is_alive(), "czekająca partia przerwana")
        self.assertFalse(os.path.exists(self.path), "przerwany przebieg nie zapisuje pliku z powrotem")
        self.assertEqual((s.state()["state"], s.state()["items"], s.state()["error"]), ("off", 0, ""))
        self.assertFalse(s.ready())

    def test_quiet_is_checked_before_every_batch(self):
        queue = models_queue.Queue()
        lane = models_queue.lane_of(models_config.DEFAULT_URL)
        self.index.items = [entry(f"s{n}", f"Sesja {n} o dockerze", age=1 + n / 100) for n in range(semantic.BATCH + 4)]
        s = self.search(queue=queue)
        original = self.embed

        def embed_then_describe(texts, **kwargs):
            result = original(texts, **kwargs)
            # Między partiami raport albo opis karty użył qwen3:8b — bge-m3 nie powinien go zaraz wypchnąć.
            queue.run(lambda: None, endpoint="local", lane=lane, role="describe", model="qwen3:8b")
            return result

        s.embed = embed_then_describe
        s.sync()
        self.assertEqual(len(self.embed.calls), 1, "druga partia odłożona")
        self.assertEqual((s.state()["state"], s.state()["pending"]), ("waiting", 4))
        self.assertTrue(s._retry)

    def test_waits_for_quiet_server_after_other_model(self):
        queue = models_queue.Queue()
        s = self.search(queue=queue)
        lane = models_queue.lane_of(models_config.DEFAULT_URL)
        queue.run(lambda: None, endpoint="local", lane=lane, role="describe", model="qwen3:8b")
        s.sync()
        self.assertEqual(self.embed.calls, [], "qwen3:8b przed chwilą — bge-m3 by go wypchnął")
        self.assertEqual(s.state()["state"], "waiting")
        queue._last_end[lane] -= semantic.QUIET_S + 1
        s.sync()
        self.assertEqual(len(self.embed.texts()), 3)
        self.assertEqual(s.state()["state"], "ready")


TOPICS = ("Kontenery i porty dockera", "Formularz logowania", "Raport poranny", "Ponawianie wysyłania",
          "Motyw SDDM", "Indeks sesji", "Sonda procesów", "Tablica kart")


class IncrementalTest(unittest.TestCase):
    """K-98 #5 (PT-346): przebieg bez zmian nic nie przelicza ani nie zapisuje; zmiana jednej sesji (zestaw ≥ 200
    sesji) liczy jeden dokument i dopisuje jeden wpis; wynik przyrostowy = pełne przeliczenie. Liczniki, nie czas."""

    SESSIONS = 220

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-search-incr-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "search", "sessions.json")
        self.index = FakeIndex([entry(f"s{n:03d}", f"{TOPICS[n % len(TOPICS)]} {n}", prompt=f"zadanie {n}",
                                      age=0.1 + n * 0.1) for n in range(self.SESSIONS)])
        self.tickets = {"claude:s000": ["PT-1 Kafelek dockera"]}
        self.embed = FakeEmbed()

    def search(self, path=None, index=None):
        return semantic.SemanticSearch(index or self.index, path or self.path, config=config(), embed=self.embed,
                                       clock=lambda: NOW, home="/home/ktos", debounce=0,
                                       tickets=lambda: self.tickets)

    def snapshot(self, s):
        s.counters.update(documents=0, digests=0)
        return dict(s.store.counters), os.stat(self.path).st_mtime_ns, os.path.getsize(self.path)

    def test_pass_without_changes_builds_and_writes_nothing(self):
        s = self.search()
        s.sync()
        self.assertEqual(s.counters["documents"], self.SESSIONS, "pierwszy przebieg buduje każdy dokument")
        writes, mtime, size = self.snapshot(s)
        self.embed.calls.clear()
        for _ in range(3):
            s.sync()
        self.assertEqual((s.counters["documents"], s.counters["digests"]), (0, 0), "żaden tekst ani skrót")
        self.assertEqual(s.store.counters, writes, "żaden zapis pliku")
        self.assertEqual((os.stat(self.path).st_mtime_ns, os.path.getsize(self.path)), (mtime, size))
        self.assertEqual(self.embed.calls, [])
        self.assertEqual(s.state()["state"], "ready")

    def test_one_changed_session_rebuilds_one_document_and_appends_one_entry(self):
        s = self.search()
        s.sync()
        before, _mtime, size = self.snapshot(s)
        header = len(json.dumps(read_lines(self.path)[0], separators=(",", ":"))) + 1
        entry_size = max(len(json.dumps(r, separators=(",", ":"))) + 1 for r in read_lines(self.path)[1:])
        self.embed.calls.clear()
        self.index.items[7] = entry("s007", "Nowy tytuł o logowaniu", prompt="zadanie 7", age=0.8)
        s.sync()
        self.assertEqual((s.counters["documents"], s.counters["digests"]), (1, 1), "tylko dokument zmienionej sesji")
        self.assertEqual(self.embed.texts(), ["Nowy tytuł o logowaniu\nzadanie 7\nplasma-tide · master"])
        after = s.store.counters
        self.assertEqual(after["fullWrites"], before["fullWrites"], "bez przepisywania całego pliku")
        self.assertEqual(after["writtenItems"] - before["writtenItems"], 1, "zapisany jeden wpis")
        self.assertLessEqual(after["writtenBytes"] - before["writtenBytes"], entry_size + header)
        self.assertLessEqual(os.path.getsize(self.path) - size, entry_size + header)
        # Zmiana ticketów koordynatora to też zmiana jednego dokumentu.
        self.snapshot(s)
        self.tickets = {"claude:s000": ["PT-1 Kafelek dockera", "PT-2 Porty"]}
        s.sync()
        self.assertEqual(s.counters["documents"], 1)

    def test_incremental_updates_equal_full_recompute(self):
        s = self.search()
        s.sync()
        self.index.items[3] = entry("s003", "Raport tygodniowy i notki", prompt="zadanie 3", age=0.4)   # zmiana
        self.index.items.append(entry("new", "Kontener z bazą i portem 5432", age=0.05))                 # dodanie
        del self.index.items[10]                                                                        # usunięcie
        self.index.items[20] = dict(self.index.items[20], lastActivity=NOW - 0.01 * DAY)                # sam czas
        s.sync()
        self.tickets = {}
        s.sync()
        self.assertEqual(s.store.counters["fullWrites"], 1, "po pierwszym zapisie tylko dopisywanie")
        full_path = os.path.join(self.tmp, "full", "sessions.json")
        full = self.search(path=full_path)
        full.sync()
        incremental_disk = stored(self.path)
        self.assertEqual(incremental_disk, stored(full_path), "plik po aktualizacjach = plik pełnego przeliczenia")
        self.assertEqual({k: v["hash"] for k, v in s.store.items.items()},
                         {k: h for k, (h, _vec) in incremental_disk[1].items()}, "pamięć = plik (wektory: float16)")
        reloaded = semantic.Store(self.path)
        reloaded.load()
        self.assertEqual({k: v["ts"] for k, v in reloaded.items.items()},
                         {k: round(v["ts"], 3) for k, v in full.store.items.items()})
        entries = self.index.entries()
        for query in ("sesja o dockerze i portach", "logowanie użytkownika", "raport poranny", "wysyłanie plików"):
            vector = semantic.normalize(concept_vector(query))
            self.assertEqual([(round(x, 6), e["id"]) for x, e in s.similar(vector, entries)],
                             [(round(x, 6), e["id"]) for x, e in full.similar(vector, entries)], query)

    def test_old_or_broken_file_gives_one_full_recompute_without_error(self):
        os.makedirs(os.path.dirname(self.path))
        old = {"version": 1, "model": "bge-m3:latest", "dims": 6, "updated": NOW,
               "items": {"claude:s000": {"hash": "x", "ts": NOW, "vec": semantic.pack([1.0] * 6)}}}
        for text in (json.dumps(old), "śmieci\x00{", '{"version":2,"model":"bge-m3:latest","dims":6}\n{"id":'):
            with self.subTest(text=text[:20]):
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.embed.calls.clear()
                s = self.search()
                s.sync()
                self.assertEqual(len(self.embed.texts()), self.SESSIONS, "jedno pełne przeliczenie")
                state = s.state()
                self.assertEqual((state["state"], state["error"], state["items"]), ("ready", "", self.SESSIONS))
                self.assertEqual(read_lines(self.path)[0]["version"], semantic.STORE_VERSION, "plik przepisany")
                self.embed.calls.clear()
                again = self.search()
                again.sync()
                self.assertEqual(self.embed.calls, [], "następny start czyta nowy plik — bez przeliczania")


class RankingAndQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-search-query-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.index = FakeIndex([
            entry("docker", "Kontenery i mapowanie portów", age=1),
            entry("login", "Formularz logowania", age=2),
            entry("report", "Poprawki sekcji Na daily", age=3),
            entry("other", "Motyw SDDM", age=4),
        ])
        self.embed = FakeEmbed()
        self.search = semantic.SemanticSearch(self.index, os.path.join(self.tmp, "s.json"), config=config(),
                                              embed=self.embed, clock=lambda: NOW, debounce=0, budget=1.0)
        self.search.sync()
        self.embed.calls.clear()

    def test_similar_keeps_only_clear_matches(self):
        vector = semantic.normalize(concept_vector("ta sesja o dockerze"))
        hits = self.search.similar(vector, self.index.entries())
        self.assertEqual([e["id"] for _s, e in hits], ["claude:docker"])
        self.assertAlmostEqual(hits[0][0], 1.0, places=2)
        nothing = semantic.normalize(concept_vector("tapeta i kolory okna"))
        self.assertEqual(self.search.similar(nothing, self.index.entries()), [], "nic podobnego — nic")
        self.assertEqual(self.search.similar(None, self.index.entries()), [])
        self.assertEqual(self.search.similar((1.0, 0.0), self.index.entries()), [], "inne wymiary — nic")

    def test_wants_long_queries_only(self):
        self.assertFalse(self.search.wants("docker"))
        self.assertFalse(self.search.wants("ab cd ef"), "trzy słowa, ale za krótko")
        self.assertTrue(self.search.wants("ta sesja o dockerze"))

    def test_commands_paths_urls_and_secrets_are_not_embedded(self):
        for query in ("git log --oneline -5 master", "otwórz https://example.com/strona teraz",
                      "/home/ktos/projekt/plik.txt i coś", "cat ~/notatki/plan na jutro", "ls -la w katalogu domowym",
                      "export TOKEN=abc123 dla serwera", "echo $HOME i reszta tekstu"):
            with self.subTest(query=query):
                self.assertEqual(semantic.query_text(query), "")
                self.assertFalse(self.search.wants(query))
                self.assertIsNone(self.search.query_vector(query))
        secret = "klucz sk-ant-" + "A1b2C3d4" * 4 + " do serwera"
        self.assertEqual(semantic.query_text(secret), "", "sekret w zapytaniu — nic nie idzie do modelu")
        self.assertIsNone(self.search.query_vector(secret))
        self.assertEqual(self.embed.calls, [])
        self.assertEqual(semantic.query_text("ta sesja o dockerze i portach"), "ta sesja o dockerze i portach")
        self.assertEqual(semantic.query_text("sesja o portach 5432 i 8080 dockera"), "sesja o portach 5432 i 8080 dockera",
                         "zwykłe liczby to nie polecenie")

    def test_query_vector_uses_high_priority_and_cache(self):
        vector = self.search.query_vector("Sesja o dockerze i portach")
        self.assertEqual(len(vector), len(CONCEPTS) + 1)
        self.assertEqual(self.embed.calls[0]["priority"], models_queue.HIGH)
        self.assertEqual(self.embed.calls[0]["texts"], ["sesja o dockerze i portach"])
        self.search.query_vector("sesja  o DOCKERZE i portach")
        self.assertEqual(len(self.embed.calls), 1, "to samo zapytanie — z pamięci")

    def test_newer_query_supersedes_older_before_server(self):
        self.search.debounce = 0.3
        results = {}
        first = threading.Thread(target=lambda: results.setdefault("a", self.search.query_vector("pierwsze zapytanie o dockerze")))
        first.start()
        time.sleep(0.05)
        results["b"] = self.search.query_vector("drugie zapytanie o logowaniu")
        first.join()
        self.assertIsNone(results["a"], "użytkownik pisał dalej")
        self.assertIsNotNone(results["b"])
        self.assertEqual([c["texts"] for c in self.embed.calls], [["drugie zapytanie o logowaniu"]])

    def test_server_down_backs_off_without_error(self):
        self.embed.error = ModelError("Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)")
        self.assertIsNone(self.search.query_vector("ta sesja o dockerze"))
        self.assertIsNone(self.search.query_vector("inna sesja o logowaniu"))
        self.assertEqual(len(self.embed.calls), 1, "po błędzie serwera przerwa BACKOFF_S")
        self.embed.error = models_queue.Busy("Model lokalny: kolejka: czekał 2 s — serwer zajęty: raport poranny")
        self.search._down_until = 0
        self.assertIsNone(self.search.query_vector("trzecia sesja o raporcie"))
        self.embed.error = None
        self.assertIsNotNone(self.search.query_vector("czwarta sesja o raporcie"), "zajęty serwer — bez przerwy")

    def test_slow_model_returns_none_within_budget_and_caches_later(self):
        self.search.budget = 0.2
        self.embed.delay = 0.6
        started = time.monotonic()
        self.assertIsNone(self.search.query_vector("ta sesja o dockerze"))
        self.assertLess(time.monotonic() - started, 0.5, "KRunner nie czeka na zimny model")
        time.sleep(0.7)
        self.embed.delay = 0
        calls = len(self.embed.calls)
        self.assertIsNotNone(self.search.query_vector("ta sesja o dockerze"))
        self.assertEqual(len(self.embed.calls), calls, "wynik z tła w pamięci")

    def test_query_with_other_model_than_index_is_ignored(self):
        self.embed.model = "nomic-embed-text"
        self.assertIsNone(self.search.query_vector("ta sesja o dockerze"))


class CoreMergeTest(unittest.TestCase):
    """KRunnerCore: słowa mają pierwszeństwo, sesje znaczeniowe niżej i z dopiskiem; bez wektora — same słowa."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-search-core-")
        self.addCleanup(history.remove, self.tmp)
        self.p = history.install(self.tmp)
        self.env = make_env(self.tmp)
        self.index = SessionIndex(self.p["claude"], self.p["codex"], clock=lambda: history.NOW)
        self.index.refresh()
        self.embed = FakeEmbed()
        self.search = semantic.SemanticSearch(self.index, os.path.join(self.tmp, "s.json"), config=config(),
                                              embed=self.embed, clock=lambda: history.NOW, debounce=0)
        self.search.sync()
        self.core = KRunnerCore(self.index, lambda: [], RecordingLaunchers(self.env), clock=lambda: history.NOW,
                                home=os.path.join(self.tmp, "home"), semantic=self.search)

    def merged(self, query):
        words = self.core.match(query)
        text = self.core.meaning_query(query)
        vector = self.search.query_vector(text) if text else None
        return words, self.core.with_meaning(query, words, vector)

    def test_index_has_all_visible_sessions(self):
        self.assertEqual(len(self.search.store.items), len(self.index.entries()))

    def test_meaning_finds_session_without_shared_words(self):
        words, merged = self.merged("gdzie poprawiałem formularz logowania")
        self.assertEqual(words, [], "słowa nie trafiają")
        self.assertEqual([m["id"] for m in merged], ["session:claude:h-titled"])
        m = merged[0]
        self.assertEqual(m["text"], "Claude · alpha · Login form fix")
        self.assertEqual(m["properties"]["subtext"], f"feat/login · 3 dni temu · {MEANING_NOTE}")
        self.assertEqual(m["categoryRelevance"], LOW)
        self.assertLessEqual(m["relevance"], 0.5)

    def test_word_matches_keep_priority_and_are_not_repeated(self):
        words, merged = self.merged("ai login")
        self.assertEqual(merged, words, "za krótkie na znaczenie — same słowa")
        # Słowa trafiają w „Uploader retries”; znaczenie (logowanie i wysyłanie) — w nią i w „Login form fix”.
        words = self.core.match("ai uploader")
        self.assertEqual([m["id"] for m in words], [f"session:codex:{history.codex_uid(101)}"])
        vector = self.search.query_vector(self.core.meaning_query("ai logowanie i ponawianie wysyłania"))
        merged = self.core.with_meaning("ai uploader", words, vector)
        self.assertEqual([m["id"] for m in merged], [f"session:codex:{history.codex_uid(101)}", "session:claude:h-titled"],
                         "sesja ze słów nie powtarza się jako znaczeniowa")
        self.assertEqual(merged[0], words[0], "wynik słowny bez zmian")
        self.assertLess(merged[1]["relevance"], words[0]["relevance"])
        self.assertEqual(merged[1]["categoryRelevance"], LOW)
        self.assertTrue(merged[1]["properties"]["subtext"].endswith(MEANING_NOTE))

    def test_without_vector_or_semantic_only_words(self):
        words = self.core.match("login")
        self.assertIs(self.core.with_meaning("login", words, None), words)
        plain = KRunnerCore(self.index, lambda: [], RecordingLaunchers(self.env), clock=lambda: history.NOW)
        self.assertEqual(plain.meaning_query("ta sesja o logowaniu i formularzu"), "")
        self.assertEqual(self.core.meaning_query("ai ta sesja o logowaniu"), "ta sesja o logowaniu")
        self.assertEqual(self.core.meaning_query("login"), "")

    def test_server_down_gives_word_results_only(self):
        self.embed.error = ModelError("Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)")
        words, merged = self.merged("login form fix walidacja")
        self.assertEqual(merged, words)


class EmbedClientTest(unittest.TestCase):
    """models.embed na atrapie serwera: Ollama /api/embed, serwer zgodny z OpenAI, złe odpowiedzi, chmura."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-embed-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def cfg(self, url, kind="ollama", roles=None):
        return models_config.from_dict({"endpoints": {"local": {"url": url, "kind": kind}}, "roles": roles or {}})

    def test_ollama_embed_request_and_vectors(self):
        server = start_server(self, {("POST", "/api/embed"): (200, {"model": "bge-m3", "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                                                                   "load_duration": 1_500_000_000, "prompt_eval_count": 9})})
        result = models_client.embed(["a", "b"], config=self.cfg(server.url), queue=models_queue.Queue(),
                                     priority=models_queue.IDLE)
        self.assertEqual((result.vectors, result.model, result.endpoint, result.dims), ([[0.1, 0.2], [0.3, 0.4]],
                                                                                       "bge-m3", "local", 2))
        self.assertEqual(result.metrics["loadS"], 1.5)
        body = server.requests[0]["body"]
        self.assertEqual(body, {"model": "bge-m3", "input": ["a", "b"], "truncate": True,
                                "keep_alive": models_client.EMBED_KEEP_ALIVE})

    def test_openai_embed(self):
        server = start_server(self, {("POST", "/v1/embeddings"): (200, {"data": [
            {"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]})})
        result = models_client.embed(["a", "b"], config=self.cfg(server.url, "openai", {"embed": "text-embedding-bge"}),
                                     queue=models_queue.Queue())
        self.assertEqual(result.vectors, [[1.0, 0.0], [0.0, 1.0]], "kolejność według index")
        self.assertEqual(server.requests[0]["body"], {"model": "text-embedding-bge", "input": ["a", "b"]})

    def test_bad_answers_and_unreachable_server_are_model_errors(self):
        for answer in ({"embeddings": [[0.1]]}, {"embeddings": [[0.1], [0.2, 0.3]]}, {"embeddings": [["x"], [1]]},
                       {"nic": 1}):
            server = start_server(self, {("POST", "/api/embed"): (200, answer)})
            with self.subTest(answer=answer), self.assertRaises(ModelError) as caught:
                models_client.embed(["a", "b"], config=self.cfg(server.url), queue=models_queue.Queue())
            self.assertIn("embeddingi", str(caught.exception))
        missing = start_server(self, {("POST", "/api/embed"): (404, {"error": 'model "bge-m3" not found, try pulling it first'})})
        with self.assertRaises(ModelError) as caught:
            models_client.embed("a", config=self.cfg(missing.url), queue=models_queue.Queue())
        self.assertIn("not found", str(caught.exception))
        started = time.monotonic()
        with self.assertRaises(ModelError):
            models_client.embed("a", config=self.cfg(f"http://127.0.0.1:{closed_port()}"), queue=models_queue.Queue())
        self.assertLess(time.monotonic() - started, 2)

    def test_cloud_model_never_called(self):
        server = start_server(self, {("POST", "/api/embed"): (200, {"embeddings": [[1.0]]})})
        cfg = models_config.from_dict({"endpoints": {"local": {"url": server.url}}})
        for kwargs in ({"model": "embed-x:cloud"},):
            with self.assertRaises(ModelError) as caught:
                models_client.embed("a", config=cfg, queue=models_queue.Queue(), **kwargs)
            self.assertIn("chmurze Ollamy", str(caught.exception))
        self.assertEqual(server.requests, [])

    def test_role_from_file_follows_default_server(self):
        server = start_server(self, {("POST", "/api/embed"): (200, {"embeddings": [[1.0, 0.0]]})})
        path = write_models(self.tmp, {"default": "mac", "endpoints": {
            "local": {"url": f"http://127.0.0.1:{closed_port()}"}, "mac": {"url": server.url}}})
        result = models_client.embed("a", config=models_config.load(path), queue=models_queue.Queue())
        self.assertEqual((result.endpoint, result.model), ("mac", "bge-m3"), "rola embed idzie za `default`")

if __name__ == "__main__":
    unittest.main()
