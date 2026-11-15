"""Dym reproduktora CPU na postoju K-98 #5 (`perf_idle_cpu.py`): podział raportu próbnika, dane sesji, atrapa
serwera embeddingów i pomiar w procesie (sonda, indeks bez zmian) na małym zestawie.

Nie mierzy czasu — sprawdza kształt wyniku i liczniki, na izolowanych danych."""
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request

from . import perf_idle_cpu as perf

REPORT = """Próbki stosów: 6000 w 60 s (co 10 ms, czas ścienny — nie czas CPU; oczekiwanie wewnątrz kodu C może wyglądać jak praca)
Próbek poza ramkami rozpoznanymi jako czekanie: 1000

## Funkcje — czas własny (próbki)
    300  processes.py:probe

## Funkcje — czas łączny (próbki)
   1000  threading.py:run
    400  processes.py:probe
    150  engine.py:tick
     50  semantic.py:sync

## Najczęstsze stosy (wątek: ostatnie 12 ramek)
    400  [tide-process-probe] threading.py:run > processes.py:probe
"""


class ParseProfileTest(unittest.TestCase):
    def test_split_into_index_probe_tick_and_rest(self):
        split = perf.parse_profile(REPORT)
        self.assertEqual(split["busySamples"], 1000)
        self.assertEqual(split["samples"], {"index": 50, "probe": 400, "tick": 150, "rest": 400})
        self.assertEqual(split["share"]["probe"], 0.4)

    def test_missing_frames_count_as_zero(self):
        split = perf.parse_profile("Próbek poza ramkami rozpoznanymi jako czekanie: 10\n")
        self.assertEqual(split["samples"], {"index": 0, "probe": 0, "tick": 0, "rest": 10})


class DataTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tide-perf-idle-test-")
        self.addCleanup(shutil.rmtree, self.base)

    def test_sessions_spread_over_the_window(self):
        now = time.time()
        claude = perf.write_sessions(self.base, 12, now)
        files = [os.path.join(root, f) for root, _d, names in os.walk(claude) for f in names]
        self.assertEqual(len(files), 12)
        ages = sorted(now - os.path.getmtime(f) for f in files)
        self.assertLess(ages[0], 86400)
        self.assertLess(ages[-1], 30 * 86400)
        self.assertGreater(ages[-1], 86400)

    def test_fake_embed_server_answers_api_embed(self):
        server = perf.EmbedServer()
        self.addCleanup(server.close)
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/embed", method="POST",
                                         data=json.dumps({"model": "bge-m3", "input": ["a", "b"]}).encode())
        with urllib.request.urlopen(request, timeout=5) as reply:
            data = json.loads(reply.read())
        self.assertEqual(len(data["embeddings"]), 2)
        self.assertEqual(len(data["embeddings"][0]), perf.DIMS)
        self.assertEqual((server.calls, server.texts), (1, 2))

    def test_micro_reports_probe_and_idle_index_pass(self):
        claude = perf.write_sessions(self.base, 6, time.time())
        os.makedirs(os.path.join(self.base, "codex", "sessions"))
        server = perf.EmbedServer()
        self.addCleanup(server.close)
        perf.write_models(os.path.join(self.base, "models.json"), server.port)
        result = perf.micro(self.base, perf.DEFAULT_SRC, claude, server.port)
        self.assertNotIn("error", result, result.get("error"))
        self.assertEqual(len(result["probe"]["cpuMs"]), 20)
        index = result["index"]
        self.assertEqual(index["sessions"], 6)
        self.assertEqual(index["state"]["state"], "ready", index["state"])
        self.assertEqual(server.texts, 6, "pierwszy przebieg liczy wszystko, kolejne nic")
        self.assertEqual(index["idlePassBytesWritten"], [0] * 5, "przebieg bez zmian nie zapisuje pliku")


if __name__ == "__main__":
    unittest.main()
