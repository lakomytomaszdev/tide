"""Modele chmurowe Ollamy (`nazwa:cloud`, `nazwa:120b-cloud`) w trybie lokalnym: lokalna Ollama tylko
przekazuje je na ollama.com, więc Tide ich nie używa — raport i karty dostają reguły z informacją, a do
serwera nic nie wychodzi. Do tego: SetRole odmawia, Status() oznacza modele `cloud: true`, a rola z takim
modelem jest niedostępna; `tokenFile` musi być ścieżką bezwzględną (albo z `~`).

Prawdziwa Ollama nie jest pytana — atrapy serwera z test_models_core.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon.coordination import describe
from tide_daemon.models import ModelError, client
from tide_daemon.models import config as models_config
from tide_daemon.models import queue as queue_mod
from tide_daemon.models import settings, status
from tide_daemon.models.settings import SettingsError
from tide_daemon.reports import summarize

from .test_coordination_describe import board, ticket
from .test_models_core import OLLAMA_CHAT, OLLAMA_TAGS, ollama_routes, start_server, write_models

CLOUD = "gpt-oss:120b-cloud"
REFUSAL = f"model „{CLOUD}” działa w chmurze Ollamy, nie lokalnie — wybierz model lokalny w Tide → Modele"


class CloudNameTest(unittest.TestCase):
    def test_cloud_model_names(self):
        cloud = ["gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud", "kimi-k2:cloud",
                 "gpt-oss:20B-Cloud", "hf.co/org/model:cloud"]
        local = ["qwen3:8b", "gpt-oss:20b", "bge-m3", "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M",
                 "cloud", "cloud-model:8b", "my-cloud:latest", "", None]
        self.assertEqual([models_config.is_cloud_model(n) for n in cloud], [True] * len(cloud))
        self.assertEqual([models_config.is_cloud_model(n) for n in local], [False] * len(local))
        self.assertEqual(models_config.cloud_refusal(CLOUD), REFUSAL)


class CloudConfigTest(unittest.TestCase):
    def test_role_with_cloud_model_is_unavailable(self):
        cfg = models_config.from_dict({"endpoints": {"local": {"url": "http://127.0.0.1:9"}},
                                       "roles": {"summary": CLOUD, "describe": "local:qwen3:8b"}})
        self.assertEqual(cfg.roles["summary"].error, REFUSAL)
        self.assertEqual(cfg.roles["describe"].error, "")
        with self.assertRaises(ModelError) as ctx:
            cfg.role("summary")
        self.assertEqual(str(ctx.exception), f"Model lokalny niedostępny: {REFUSAL}")
        self.assertIn(f"rola „summary”: {REFUSAL} — rola niedostępna", cfg.warnings)

    def test_token_file_must_be_absolute(self):
        cfg = models_config.from_dict({"endpoints": {
            "rel": {"url": "http://a:1", "tokenFile": "tokens/a"},
            "dot": {"url": "http://b:1", "tokenFile": "./b.token"},
            "home": {"url": "http://c:1", "tokenFile": "~/.config/tide/c.token"},
            "abs": {"url": "http://d:1", "tokenFile": "/etc/tide/d.token"}}})
        self.assertEqual(sorted(cfg.endpoints), ["abs", "home"])
        self.assertEqual(cfg.endpoints["home"].token_file, os.path.expanduser("~/.config/tide/c.token"))
        for name in ("rel", "dot"):
            self.assertIn(f"serwer „{name}”: tokenFile musi być ścieżką bezwzględną (albo zaczynać się od ~) — "
                          f"pominięty", cfg.warnings)


class CloudCallTest(unittest.TestCase):
    """Nic nie wychodzi do serwera: ani z roli, ani z jawnego modelu, ani z raportu i kart."""

    def setUp(self):
        self.server = start_server(self, {("POST", "/api/chat"): (200, OLLAMA_CHAT)})
        self.cfg = models_config.from_dict({"endpoints": {"local": {"url": self.server.url}},
                                            "roles": {"summary": CLOUD, "describe": CLOUD}})
        self.queue = queue_mod.Queue()

    def test_chat_refuses_cloud_role_and_cloud_model(self):
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", role="summary", config=self.cfg, queue=self.queue)
        self.assertEqual(str(ctx.exception), f"Model lokalny niedostępny: {REFUSAL}")
        with self.assertRaises(ModelError) as ctx:
            client.chat("x", endpoint="local", model="kimi-k2:cloud", config=self.cfg, queue=self.queue)
        self.assertIn("„kimi-k2:cloud” działa w chmurze Ollamy, nie lokalnie", str(ctx.exception))
        self.assertEqual(self.server.requests, [])

    def test_cloud_model_set_while_waiting_is_refused_at_start(self):
        """Rola przestawiona na model chmurowy w czasie czekania w kolejce — sprawdzenie przy starcie wywołania."""
        before = models_config.from_dict({"endpoints": {"local": {"url": self.server.url}}})
        after = models_config.from_dict({"endpoints": {"local": {"url": self.server.url}}, "roles": {"describe": CLOUD}})
        with mock.patch.object(models_config, "load", side_effect=[before, after]):
            with self.assertRaises(ModelError) as ctx:
                client.chat("x", role="describe", queue=self.queue)
        self.assertIn("działa w chmurze Ollamy", str(ctx.exception))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.queue.snapshot(), {})

    def test_report_and_cards_get_rules_with_note(self):
        never = mock.patch.object(summarize, "call_claude", side_effect=AssertionError("claude w trybie local"))
        never.start()
        self.addCleanup(never.stop)
        model = summarize.Model("local", role="summary", config=self.cfg)
        with self.assertRaises(summarize.Unavailable) as ctx:
            model.ask({"projects": []}, system_prompt="s")
        self.assertEqual(summarize.local_note(ctx.exception),
                         f"Model lokalny niedostępny — opis bez modelu ({REFUSAL})")
        self.assertFalse(model.remote)

        tmp = tempfile.mkdtemp(prefix="tide-cloud-cards-")
        self.addCleanup(shutil.rmtree, tmp)
        now = {"t": 1000.0}
        d = describe.Describer(mode="local", model="sonnet", cache_file=os.path.join(tmp, "cards.json"),
                               clock=lambda: now["t"], log=lambda text: None, models_config=self.cfg)
        boards = [board([ticket("~a")])]
        d.decorate(boards)
        now["t"] += describe.SETTLE
        d.decorate(boards)
        job = d.due(boards)
        self.assertFalse(d.run(job))
        d.decorate(boards)
        self.assertEqual(boards[0]["tickets"][0]["describeNote"],
                         f"Model lokalny niedostępny — opis bez modelu ({REFUSAL})")
        self.assertEqual(self.server.requests, [], "fakty nie wychodzą do chmury")


class CloudSettingsAndStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-cloud-settings-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.server = start_server(self, ollama_routes())
        tags = json.loads(json.dumps(OLLAMA_TAGS))
        tags["models"].append({"name": CLOUD, "model": CLOUD, "size": 384, "details": {"family": "gptoss"}})
        self.server.routes[("GET", "/api/tags")] = (200, tags)
        self.path = write_models(self.tmp, {"endpoints": {"local": {"url": self.server.url}}})

    def test_set_role_refuses_cloud_model(self):
        with self.assertRaises(SettingsError) as ctx:
            settings.set_role({"role": "summary", "model": CLOUD}, self.path)
        self.assertEqual(str(ctx.exception), REFUSAL)
        with self.assertRaises(SettingsError):
            settings.set_role({"role": "agent", "model": CLOUD, "endpoint": "local"}, self.path)
        with open(self.path, encoding="utf-8") as fh:
            self.assertNotIn("cloud", fh.read(), "odmowa niczego nie zapisuje")
        settings.set_role({"role": "summary", "model": "qwen3:8b"}, self.path)

    def test_status_marks_cloud_models_and_roles(self):
        write_models(self.tmp, {"endpoints": {"local": {"url": self.server.url}}, "roles": {"describe": CLOUD}})
        state = status.read(self.path)
        models = {m["name"]: m for m in state["endpoints"]["local"]["models"]}
        self.assertIs(models[CLOUD].get("cloud"), True)
        self.assertNotIn("cloud", models["qwen3:8b"], "lokalne bez pola")
        role = state["roles"]["describe"]
        self.assertEqual((role["available"], role["error"]), (False, REFUSAL))
        self.assertTrue(state["roles"]["summary"]["available"])


if __name__ == "__main__":
    unittest.main()
