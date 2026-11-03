"""Katalog wykonawców (PT-60, models/executors.py): odczyt, zapis do models.json i zamiana klasy na wykonawcę.

Zamiana klasy jest sprawdzana tabelą ze specyfikacji (preferowany dostawca × klasa) na katalogu domyślnym
i z innym dostawcą. Wszystko w katalogu tymczasowym, bez sieci; token innego dostawcy tylko w pliku.
"""
import json
import os
import pathlib
import secrets
import shutil
import stat
import tempfile
import unittest

from tide_daemon.models import config as models_config
from tide_daemon.models import executors as ex
from tide_daemon.models import settings, status
from tide_daemon.models.settings import SettingsError

from .test_models_settings import SettingsTestCase

TABLE = {   # preferowany dostawca → klasa → wykonawca (specyfikacja, sekcja „Wykonawcy”)
    "anthropic": {"lekki": "Haiku", "standard": "Sonnet", "mocny": "Opus", "lokalny": "Lokalny"},
    "openai": {"lekki": "Codex lekki", "standard": "Codex standard", "mocny": "Codex mocny", "lokalny": "Lokalny"},
    "local": {"lekki": "Lokalny", "standard": "Lokalny", "mocny": "Lokalny", "lokalny": "Lokalny"},
}


def online(url="http://127.0.0.1:11434", models=("qwen3-coder:30b", "qwen3:8b", "gpt-oss:120b"), name="local"):
    return {"name": name, "url": url, "kind": "ollama", "online": True, "error": "",
            "models": [{"name": m} for m in models]}


def offline(name="local"):
    return {"name": name, "url": "http://127.0.0.1:11434", "kind": "ollama", "online": False,
            "error": "Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)", "models": []}


class ResolveTest(unittest.TestCase):
    def ctx(self, data=None, state="up", agent="qwen3-coder:30b"):
        data = dict(data or {})
        data.setdefault("roles", {"agent": agent} if agent else {})
        cfg = models_config.from_dict(data)
        endpoints = {"up": {"local": online()}, "down": {"local": offline()}, None: None}[state]
        return ex.context_from(cfg, None if endpoints is None else {"checked": True, "endpoints": endpoints})

    def test_default_table_for_every_preferred_provider(self):
        ctx = self.ctx()
        for provider, row in TABLE.items():
            for klass, name in row.items():
                result = ex.resolve(ctx, provider, klass)
                self.assertTrue(result.ok, (provider, klass, result.error))
                self.assertEqual(result.executor["name"], name, (provider, klass))
                self.assertEqual(result.local, name == "Lokalny")
        self.assertEqual(ex.resolve(ctx, "anthropic", "mocny").executor["program"], "claude")
        self.assertEqual(ex.resolve(ctx, "openai", "mocny").executor["program"], "codex")

    def test_local_follows_the_agent_role(self):
        ctx = self.ctx({"agentProgram": "claude"})
        local = ex.resolve(ctx, "local", "standard").executor
        self.assertEqual((local["program"], local["model"], local["endpoint"]), ("claude", "qwen3-coder:30b", "local"))

    def test_other_provider_by_classes_and_local(self):
        data = {"providers": {"groq": {"url": "https://api.groq.example", "api": "openai", "label": "Groq"}},
                "executors": [
                    {"name": "Groq mały", "program": "codex", "model": "llama-8b", "provider": "groq",
                     "classes": ["lekki", "standard"]},
                    {"name": "Groq duży", "program": "codex", "model": "llama-70b", "provider": "groq",
                     "classes": ["mocny"]},
                    {"name": "Lokalny", "provider": "local", "classes": ["lokalny"]}]}
        ctx = self.ctx(data)
        got = {k: ex.resolve(ctx, "groq", k).executor["name"] for k in ex.CLASSES}
        self.assertEqual(got, {"lekki": "Groq mały", "standard": "Groq mały", "mocny": "Groq duży",
                               "lokalny": "Lokalny"})
        self.assertEqual(ex.resolve(ctx, "groq", "mocny").executor["url"], "https://api.groq.example")
        self.assertFalse(ex.resolve(ctx, "anthropic", "standard").ok, "katalog bez wykonawców Anthropic")
        self.assertIn("Anthropic nie ma wykonawcy klasy standard", ex.resolve(ctx, "anthropic", "standard").error)

    def test_mixed_suggests_and_lists_the_whole_catalog(self):
        ctx = self.ctx()
        result = ex.resolve(ctx, "mixed", "mocny")
        self.assertTrue(result.ok and result.suggested)
        self.assertEqual(result.executor["name"], "Opus")
        self.assertEqual(set(result.candidates), {e.name for e in ex.DEFAULT_EXECUTORS})
        self.assertEqual(result.candidates[0], "Opus", "klasa dopasowana na początku listy")
        self.assertEqual(ex.resolve(ctx, "mixed", "lokalny").executor["name"], "Lokalny")

    def test_local_class_never_goes_to_the_cloud_when_the_server_is_down(self):
        ctx = self.ctx(state="down")
        for provider in ("anthropic", "openai", "local", "mixed"):
            result = ex.resolve(ctx, provider, "lokalny")
            self.assertFalse(result.ok, provider)
            self.assertTrue(result.local)
            self.assertIsNone(result.executor, "bez zastępstwa z chmury")
            self.assertIn("nie odpowiada", result.error)
            self.assertIn("nie przejdzie do chmury", result.error)
        only = ex.resolve(ctx, "local", "mocny")
        self.assertFalse(only.ok and only.executor, "tylko lokalnie: serwer padł — odmowa, nie Opus")
        self.assertTrue(ex.resolve(ctx, "anthropic", "mocny").ok, "klasy chmurowe nie zależą od serwera")

    def test_local_model_missing_on_the_server_is_explained(self):
        result = ex.resolve(self.ctx(agent="brak:1b"), "local", "lekki")
        self.assertFalse(result.ok)
        self.assertIn("modelu brak:1b nie ma na serwerze", result.error)

    def test_unchecked_status_does_not_block(self):
        self.assertTrue(ex.resolve(self.ctx(state=None), "local", "lekki").ok)

    def test_bad_requests(self):
        ctx = self.ctx()
        self.assertIn("nieznana klasa", ex.resolve(ctx, "anthropic", "ogromny").error)
        self.assertIn("nieznany preferowany dostawca", ex.resolve(ctx, "xyz", "lekki").error)

    def test_reviewer_is_always_another_model(self):
        ctx = self.ctx()
        for provider in ("anthropic", "openai"):
            for klass in ("lekki", "standard", "mocny"):
                author = ex.resolve(ctx, provider, klass).executor["name"]
                review = ex.reviewer(ctx, provider, klass, author)
                self.assertTrue(review.ok, (provider, klass, review.error))
                self.assertNotEqual(review.executor["model"], ctx.resolved(ctx.catalog.get(author))["model"])
                self.assertEqual(review.executor["provider"], provider)
        self.assertEqual(ex.reviewer(ctx, "anthropic", "mocny", "Opus").executor["name"], "Sonnet")
        self.assertEqual(ex.reviewer(ctx, "openai", "mocny", "Codex mocny").executor["name"], "Codex Astra")

    def test_mixed_reviewer_is_from_another_provider(self):
        ctx = self.ctx()
        review = ex.reviewer(ctx, "mixed", "mocny", "Opus")
        self.assertTrue(review.ok)
        self.assertEqual(review.executor["provider"], "openai")
        self.assertEqual(ex.reviewer(ctx, "mixed", "standard", "Codex standard").executor["provider"], "anthropic")

    def test_local_author_gets_a_local_reviewer_or_a_refusal(self):
        ctx = self.ctx()
        refused = ex.reviewer(ctx, "mixed", "mocny", "Lokalny")
        self.assertFalse(refused.ok, "jeden wykonawca lokalny: nie ma innego modelu, a do chmury nie wolno")
        self.assertIn("lokalnego", refused.error)
        data = {"executors": [dict(ex.executor_to_dict(e)) for e in ex.DEFAULT_EXECUTORS]
                + [{"name": "Lokalny duży", "program": "claude", "model": "gpt-oss:120b", "provider": "local",
                    "classes": ["lokalny"]}]}
        ctx = self.ctx(data)
        review = ex.reviewer(ctx, "anthropic", "lokalny", "Lokalny")
        self.assertEqual((review.executor["name"], review.local), ("Lokalny duży", True))

    def test_local_reviewer_unavailable_is_not_replaced_by_cloud(self):
        data = {"executors": [dict(ex.executor_to_dict(e)) for e in ex.DEFAULT_EXECUTORS]
                + [{"name": "Lokalny duży", "program": "claude", "model": "gpt-oss:120b", "provider": "local",
                    "classes": ["lokalny"]}]}
        data["executors"][-1]["model"] = "brak:1b"
        review = ex.reviewer(self.ctx(data), "mixed", "lokalny", "Lokalny")
        self.assertFalse(review.ok)
        self.assertIn("nie ma na serwerze", review.error)

    def test_no_secrets_in_results(self):
        data = {"providers": {"groq": {"url": "https://api.groq.example", "api": "openai",
                                       "tokenFile": "/home/x/.config/tide/tokens/provider-groq"}},
                "executors": [{"name": "G", "program": "codex", "model": "m", "provider": "groq",
                               "classes": ["lekki"]}]}
        ctx = self.ctx(data)
        text = json.dumps(ex.resolve(ctx, "groq", "lekki").to_dict())
        self.assertNotIn("tokenFile", text)
        self.assertNotIn("tokens/", text)
        state = json.dumps(ex.public_state(models_config.from_dict(data), {}, True))
        self.assertNotIn("tokens/", state)


class ReviewRulesTest(unittest.TestCase):
    ctx = ResolveTest.ctx
    """Reguły recenzenta i klasy z review PT-60 (każda zabija konkretny mutant)."""

    def test_defaults_codex_models_and_effort(self):
        by = {e.name: e for e in ex.DEFAULT_EXECUTORS}
        self.assertEqual([(by[n].model, by[n].effort, by[n].classes) for n in
                          ("Codex lekki", "Codex standard", "Codex mocny", "Codex Astra")],
                         [("gpt-6-luna", "", ("lekki",)), ("gpt-6.1-sol", "medium", ("standard",)),
                          ("gpt-6.1-sol", "high", ("mocny",)), ("gpt-6-astra", "high", ())])
        ctx = self.ctx()
        self.assertEqual(ex.resolve(ctx, "openai", "mocny").executor["name"], "Codex mocny")
        self.assertEqual(ex.resolve(ctx, "openai", "mocny").executor["effort"], "high")
        self.assertNotIn("Codex Astra", [ex.resolve(ctx, "openai", k).executor["name"] for k in ex.CLASS_ORDER])
        self.assertEqual(ex.resolve(ctx, "mixed", "mocny").executor["name"], "Opus")
        self.assertIn("Codex Astra", ex.resolve(ctx, "mixed", "mocny").candidates)

    def test_same_model_other_effort_is_not_another_reviewer(self):
        ctx = self.ctx()
        for author, bad in (("Codex standard", "Codex mocny"), ("Codex mocny", "Codex standard")):
            review = ex.reviewer(ctx, "openai", "standard", author)
            self.assertTrue(review.ok)
            self.assertNotEqual(review.executor["name"], bad)
            self.assertNotEqual(review.executor["model"], "gpt-6.1-sol")
            self.assertNotIn(bad, review.candidates)

    def test_mixed_reviewer_excludes_the_author_provider(self):
        data = {"executors": [dict(ex.executor_to_dict(e)) for e in ex.DEFAULT_EXECUTORS]
                + [{"name": "Codex mocny 2", "program": "codex", "model": "gpt-x", "provider": "openai",
                    "classes": ["mocny"]}]}
        ctx = self.ctx(data)
        review = ex.reviewer(ctx, "mixed", "mocny", "Codex mocny")
        self.assertEqual(review.executor["name"], "Opus", "wykonawca OpenAI tej samej klasy nie może wygrać")
        self.assertTrue(all(ctx.catalog.get(n).provider == "anthropic" for n in review.candidates))

    def test_same_model_under_another_name_is_the_same_model(self):
        data = {"executors": [dict(ex.executor_to_dict(e)) for e in ex.DEFAULT_EXECUTORS]
                + [{"name": "Opus 2", "program": "claude", "model": "OPUS", "provider": "anthropic",
                    "classes": ["mocny"]}]}
        ctx = self.ctx(data)
        review = ex.reviewer(ctx, "anthropic", "mocny", "Opus")
        self.assertEqual(review.executor["name"], "Sonnet")
        self.assertNotIn("Opus 2", review.candidates)

    def test_reviewer_prefers_stronger_before_weaker(self):
        review = ex.reviewer(self.ctx(), "anthropic", "standard", "Sonnet")
        self.assertEqual(review.candidates, ["Opus", "Haiku"])
        self.assertEqual(review.executor["name"], "Opus")

    def test_mixed_suggestion_never_picks_local(self):
        data = {"executors": [{"name": "Lokalny", "provider": "local", "classes": ["lokalny", "mocny"]}]}
        result = ex.resolve(self.ctx(data), "mixed", "mocny")
        self.assertFalse(result.ok, "klasa mocny ma tylko wykonawcę lokalnego — podpowiedź nie schodzi na lokalnego")
        self.assertIn("Lokalny", result.candidates)

    def test_no_model_message(self):
        result = ex.resolve(self.ctx(agent=""), "local", "lekki")
        self.assertFalse(result.ok)
        self.assertIn("brak modelu — ustaw rolę agent albo model wykonawcy", result.error)

    def test_non_string_requests_are_refused_clearly(self):
        ctx = self.ctx()
        for provider, klass in ((["x"], "lekki"), ("anthropic", {"a": 1}), (None, None)):
            for result in (ex.resolve(ctx, provider, klass), ex.reviewer(ctx, provider, klass, "Opus")):
                self.assertFalse(result.ok)
                self.assertIn("muszą być napisami", result.error)

    def test_cloud_ollama_model_never_counts_as_local(self):
        for model in ("gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "x:cloud"):
            ctx = self.ctx(agent=model, state="up")
            for provider, klass in (("local", "mocny"), ("anthropic", "lokalny"), ("mixed", "lokalny")):
                result = ex.resolve(ctx, provider, klass)
                self.assertFalse(result.ok, (model, provider, klass))
                self.assertIn("agent niedostępna", result.error)
            unchecked = self.ctx(agent=model, state=None)
            self.assertFalse(ex.resolve(unchecked, "local", "mocny").ok, "także bez znanego stanu serwera")
            row = ex.public_state(models_config.from_dict({"roles": {"agent": model}}), {"local": online()},
                                  True)["list"][-1]
            self.assertEqual((row["name"], row["available"]), ("Lokalny", False))
        explicit = models_config.from_dict({"executors": [
            {"name": "Chmurowy", "program": "codex", "model": "qwen3-coder:480b-cloud", "provider": "local",
             "classes": ["lokalny"]}]})
        self.assertEqual(explicit.catalog.executors, ())
        self.assertIn("w chmurze Ollamy", explicit.catalog.warnings[0])


class CatalogFileTest(unittest.TestCase):
    def test_backward_compatible_without_keys(self):
        cfg = models_config.from_dict({"endpoints": {"local": {"url": "http://127.0.0.1:11434"}}})
        self.assertEqual([e.name for e in cfg.catalog.executors],
                         ["Haiku", "Sonnet", "Opus", "Codex lekki", "Codex standard", "Codex mocny", "Codex Astra",
                          "Fable", "Lokalny"])
        self.assertFalse(cfg.catalog.stored)
        self.assertEqual(cfg.warnings, ())

    def test_relative_provider_token_file_is_refused(self):
        cfg = models_config.from_dict({"providers": {"p": {"url": "https://a.example", "api": "openai",
                                                           "tokenFile": "tokens/p"}}})
        self.assertEqual(cfg.catalog.providers, {})
        self.assertIn("tokenFile musi być ścieżką bezwzględną", cfg.catalog.warnings[0])
        ok = models_config.from_dict({"providers": {"p": {"url": "https://a.example", "api": "openai",
                                                           "tokenFile": "~/t"}}})
        self.assertTrue(ex.public_state(ok, {}, True)["providers"]["p"]["auth"])

    def test_provider_without_token_has_no_auth(self):
        cfg = models_config.from_dict({"providers": {"p": {"url": "https://a.example", "api": "openai"}}})
        self.assertFalse(ex.public_state(cfg, {}, True)["providers"]["p"]["auth"])

    def test_effort_is_validated_and_compatible(self):
        old = models_config.from_dict({"executors": [{"name": "C", "program": "codex", "model": "m",
                                                      "provider": "openai"}]})
        self.assertEqual(old.catalog.executors[0].effort, "")
        bad = models_config.from_dict({"executors": [
            {"name": "A", "program": "codex", "model": "m", "provider": "openai", "effort": "max"},
            {"name": "B", "program": "claude", "model": "m", "provider": "anthropic", "effort": "high"}]})
        self.assertEqual(bad.catalog.executors, ())
        self.assertIn("wysiłek to low", bad.catalog.warnings[0])
        self.assertIn("tylko Codexa", bad.catalog.warnings[1])

    def test_bad_entries_are_skipped_with_warnings(self):
        cfg = models_config.from_dict({"providers": {"x": {"url": "ftp://x", "api": "openai"},
                                                     "anthropic": {"url": "https://a", "api": "openai"},
                                                     "ok": {"url": "https://u:p@a", "api": "openai"}},
                                       "executors": [{"name": "A", "program": "claude", "model": "m",
                                                      "provider": "anthropic", "classes": ["lekki"]},
                                                     {"name": "A", "program": "claude", "model": "m",
                                                      "provider": "anthropic"},
                                                     {"name": "B", "program": "codex", "model": "m",
                                                      "provider": "anthropic"},
                                                     {"name": "C", "program": "codex", "model": "m",
                                                      "provider": "nie-ma"},
                                                     {"name": "D", "program": "codex", "model": "m",
                                                      "provider": "openai", "classes": ["ogromny"]},
                                                     "nie obiekt"]})
        self.assertEqual([e.name for e in cfg.catalog.executors], ["A"])
        self.assertEqual(len(cfg.catalog.warnings), 8)
        self.assertEqual(cfg.catalog.providers, {})
        self.assertEqual(cfg.warnings[-8:], cfg.catalog.warnings)


class CatalogSettingsTest(SettingsTestCase):
    def test_first_write_saves_the_whole_catalog(self):
        settings.set_executor({"name": "Mini", "program": "codex", "model": "gpt-x", "provider": "openai",
                               "classes": ["lekki"]}, self.path)
        names = [e["name"] for e in self.data()["executors"]]
        self.assertEqual(names, ["Haiku", "Sonnet", "Opus", "Codex lekki", "Codex standard", "Codex mocny",
                                 "Codex Astra", "Fable", "Lokalny", "Mini"])
        cfg = self.cfg()
        self.assertTrue(cfg.catalog.stored)
        self.assertEqual(ex.resolve(ex.context_from(cfg), "openai", "lekki").executor["name"], "Codex lekki",
                         "pierwszy w katalogu wygrywa")

    def test_change_rename_remove(self):
        settings.set_executor({"name": "Sonnet 5", "oldName": "Sonnet", "program": "claude", "model": "sonnet",
                               "provider": "anthropic", "classes": ["standard", "mocny"]}, self.path)
        entries = {e["name"]: e for e in self.data()["executors"]}
        self.assertNotIn("Sonnet", entries)
        self.assertEqual(entries["Sonnet 5"]["classes"], ["standard", "mocny"])
        self.assertEqual(list(entries).index("Sonnet 5"), 1, "zostaje na swoim miejscu")
        settings.remove_executor("Haiku", self.path)
        self.assertNotIn("Haiku", [e["name"] for e in self.data()["executors"]])
        self.assertIn("nieznany wykonawca", self.refused(settings.remove_executor, "Haiku", self.path))

    def test_refusals(self):
        good = {"name": "X", "program": "codex", "model": "m", "provider": "openai", "classes": ["lekki"]}
        self.assertIn("już jest", self.refused(settings.set_executor, dict(good, name="Opus"), self.path))
        self.assertIn("nieznany wykonawca",
                      self.refused(settings.set_executor, dict(good, oldName="Nie ma"), self.path))
        self.assertIn("już jest", self.refused(settings.set_executor, dict(good, oldName="Haiku", name="Opus"),
                                               self.path))
        self.assertIn("tylko program claude", self.refused(settings.set_executor, dict(good, provider="anthropic"),
                                                           self.path))
        self.assertIn("tylko program codex", self.refused(settings.set_executor, dict(good, program="claude"),
                                                          self.path))
        self.assertIn("nieznany dostawca", self.refused(settings.set_executor, dict(good, provider="nie"), self.path))
        self.assertIn("podaj model", self.refused(settings.set_executor, dict(good, model=""), self.path))
        self.assertIn("klasy to", self.refused(settings.set_executor, dict(good, classes=["x"]), self.path))
        self.assertIn("nieznany serwer", self.refused(settings.set_executor, dict(
            good, provider="local", program="", model="", endpoint="mac"), self.path))
        self.assertFalse(os.path.exists(self.path), "odmowa niczego nie zapisuje")

    def test_provider_token_goes_to_a_file_only(self):
        token = "tok-" + secrets.token_hex(12)
        settings.set_provider({"name": "groq", "label": "Groq", "url": "https://api.groq.example/", "api": "openai",
                               "token": token}, self.path)
        token_file = os.path.join(os.path.dirname(self.path), "tokens", "providers", "groq")
        with open(token_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), token)
        self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)
        self.assertNotIn(token, self.text(), "w katalogu tylko ścieżka pliku")
        self.assertEqual(self.data()["providers"]["groq"],
                         {"url": "https://api.groq.example", "api": "openai", "label": "Groq",
                          "tokenFile": token_file})
        state = json.dumps(status.assemble(self.cfg(), {}))
        self.assertNotIn(token, state)
        self.assertTrue(status.assemble(self.cfg(), {})["providers"]["groq"]["auth"])
        # Zmiana bez tokenu zostawia token, clearToken go usuwa.
        settings.set_provider({"name": "groq", "url": "https://api.groq.example", "api": "openai"}, self.path)
        self.assertTrue(os.path.exists(token_file))
        settings.set_provider({"name": "groq", "url": "https://api.groq.example", "api": "openai",
                               "clearToken": True}, self.path)
        self.assertFalse(os.path.exists(token_file))
        self.assertNotIn("tokenFile", self.data()["providers"]["groq"])

    def test_provider_refusals_and_removal(self):
        base = {"name": "groq", "url": "https://api.groq.example", "api": "openai"}
        for bad, text in ((dict(base, name="openai"), "zarezerwowana"), (dict(base, name="a b"), "niepoprawna"),
                          (dict(base, url="ftp://x"), "niepoprawny adres"),
                          (dict(base, url="https://u:p@x"), "dane logowania"),
                          (dict(base, api="llama"), "openai albo anthropic"),
                          (dict(base, token="z spacją"), "token:")):
            self.assertIn(text, self.refused(settings.set_provider, bad, self.path))
        self.assertFalse(os.path.exists(self.path))
        settings.set_provider(base, self.path)
        settings.set_executor({"name": "G", "program": "codex", "model": "m", "provider": "groq",
                               "classes": ["lekki"]}, self.path)
        self.assertIn("ma wykonawców: G", self.refused(settings.remove_provider, "groq", self.path))
        self.assertIn("zmiana API", self.refused(settings.set_provider, dict(base, api="anthropic"), self.path))
        settings.remove_executor("G", self.path)
        settings.remove_provider("groq", self.path)
        self.assertNotIn("providers", self.data())
        self.assertIn("nieznany dostawca", self.refused(settings.remove_provider, "groq", self.path))

    def test_effort_is_saved(self):
        settings.set_executor({"name": "X", "program": "codex", "model": "m", "provider": "openai",
                               "effort": "xhigh", "classes": ["mocny"]}, self.path)
        self.assertEqual([e["effort"] for e in self.data()["executors"] if e["name"] == "X"], ["xhigh"])
        self.assertIn("wysiłek to", self.refused(settings.set_executor, {
            "name": "Y", "program": "codex", "model": "m", "provider": "openai", "effort": "ultra"}, self.path))

    def test_cloud_ollama_model_is_refused_for_local(self):
        msg = self.refused(settings.set_executor, {"name": "L", "program": "codex", "model": "qwen3-coder:480b-cloud",
                                                   "provider": "local", "classes": ["lokalny"]}, self.path)
        self.assertIn("w chmurze Ollamy", msg)

    def test_provider_and_endpoint_tokens_never_collide(self):
        settings.set_provider({"name": "groq", "url": "https://api.groq.example", "api": "openai",
                               "token": "PROVTOKEN"}, self.path)
        settings.set_endpoint({"name": "provider-groq", "url": "http://10.0.0.5:11434", "kind": "ollama",
                               "token": "ENDPOINTTOKEN"}, self.path)
        cfg = self.cfg()
        prov = cfg.catalog.providers["groq"].token_file
        self.assertEqual(self.text(prov).strip(), "PROVTOKEN")
        self.assertEqual(cfg.endpoints["provider-groq"].headers()["Authorization"], "Bearer ENDPOINTTOKEN")
        self.assertNotEqual(prov, cfg.endpoints["provider-groq"].token_file)
        # Usunięcie jednego nie rusza tokenu drugiego — w obie strony.
        settings.set_provider({"name": "groq", "url": "https://api.groq.example", "api": "openai",
                               "clearToken": True}, self.path)
        self.assertEqual(self.cfg().endpoints["provider-groq"].headers()["Authorization"], "Bearer ENDPOINTTOKEN")
        settings.set_provider({"name": "groq", "url": "https://api.groq.example", "api": "openai",
                               "token": "PROVTOKEN"}, self.path)
        settings.remove_endpoint("provider-groq", self.path)
        self.assertEqual(self.text(prov).strip(), "PROVTOKEN")
        settings.set_endpoint({"name": "provider-groq", "url": "http://10.0.0.5:11434", "kind": "ollama",
                               "token": "ENDPOINTTOKEN"}, self.path)
        settings.remove_provider("groq", self.path)
        self.assertEqual(self.cfg().endpoints["provider-groq"].headers()["Authorization"], "Bearer ENDPOINTTOKEN")
        tokens = os.path.join(os.path.dirname(self.path), "tokens")
        self.assertEqual(stat.S_IMODE(os.stat(tokens).st_mode), 0o700)
        self.assertIn("zarezerwowana", self.refused(settings.set_endpoint, {
            "name": "providers", "url": "http://10.0.0.5:11434", "kind": "ollama"}, self.path))

    def test_refusals_leave_an_existing_file_byte_for_byte(self):
        self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"}}, "roles": {"agent": "qwen3:8b"}})
        before = pathlib.Path(self.path).read_bytes()
        mtime = os.stat(self.path).st_mtime_ns
        bad = {"name": "X", "program": "claude", "model": "m", "provider": "openai"}
        for call in (lambda: settings.set_executor(bad, self.path),
                     lambda: settings.set_executor({**bad, "provider": "local", "model": "a:cloud"}, self.path),
                     lambda: settings.remove_executor("Nie ma", self.path),
                     lambda: settings.set_provider({"name": "openai", "url": "https://a", "api": "openai"}, self.path),
                     lambda: settings.remove_provider("brak", self.path)):
            with self.assertRaises(SettingsError):
                call()
        self.assertEqual(pathlib.Path(self.path).read_bytes(), before)
        self.assertEqual(os.stat(self.path).st_mtime_ns, mtime)

    def test_unknown_keys_and_roles_are_kept(self):
        self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"}}, "roles": {"agent": "qwen3:8b"},
                    "futureKey": [1]})
        settings.set_executor({"name": "Mini", "program": "codex", "model": "m", "provider": "openai"}, self.path)
        data = self.data()
        self.assertEqual((data["roles"], data["futureKey"]), ({"agent": "qwen3:8b"}, [1]))


if __name__ == "__main__":
    unittest.main()
