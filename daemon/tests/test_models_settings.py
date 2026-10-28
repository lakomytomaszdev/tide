"""Zapis ustawień modeli lokalnych bez Qt (models/settings.py): models.json, tokeny, report.json, pobieranie.

Wszystko w katalogu tymczasowym: models.json podany ścieżką, report.json też. Prawdziwa Ollama nie jest
pytana — pobieranie i próba połączenia idą do atrapy serwera z test_models_core.
"""
import json
import os
import secrets
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from tide_daemon.models import config as models_config
from tide_daemon.models import pull as models_pull
from tide_daemon.models import settings
from tide_daemon.models import status
from tide_daemon.models.settings import SettingsError
from tide_daemon.models.transport import Cancel, ModelError
from tide_daemon.reports import schedule

from .test_models_core import closed_port, ollama_routes, start_server


def pull_stream(lines, status_code=200):
    """Odpowiedź /api/pull: JSON w liniach, porcjami (chunked) jak w Ollamie."""
    def write(handler):
        try:
            handler.send_response(status_code)
            handler.send_header("Content-Type", "application/x-ndjson")
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            for line in lines:
                data = (json.dumps(line) + "\n").encode()
                handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass
    return write


PULL_LINES = [{"status": "pulling manifest"},
              {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 0},
              {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 600},
              {"status": "pulling bbb", "digest": "sha256:bbb", "total": 100, "completed": 100},
              {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 1000},
              {"status": "verifying sha256 digest"}, {"status": "writing manifest"}, {"status": "success"}]


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-models-settings-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "tide", "models.json")
        self.report = os.path.join(self.tmp, "tide", "report.json")

    def write(self, data, path=None):
        path = path or self.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def data(self, path=None):
        with open(path or self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def text(self, path=None):
        with open(path or self.path, encoding="utf-8") as fh:
            return fh.read()

    def cfg(self):
        return models_config.load(self.path)

    def refused(self, func, *args, **kwargs) -> str:
        with self.assertRaises(SettingsError) as ctx:
            func(*args, **kwargs)
        return str(ctx.exception)


class ModelsJsonTest(SettingsTestCase):
    def test_first_write_starts_from_effective_defaults(self):
        before = self.cfg()
        settings.set_endpoint({"name": "mac", "url": "http://mac-studio:11434", "kind": "ollama"}, self.path)
        data = self.data()
        self.assertEqual(data["default"], "local")
        self.assertEqual(data["endpoints"]["local"], {"url": "http://127.0.0.1:11434", "kind": "ollama"})
        self.assertEqual(data["endpoints"]["mac"], {"url": "http://mac-studio:11434", "kind": "ollama"})
        self.assertEqual(data["roles"], {"summary": "qwen3:8b", "describe": "qwen3:8b"})
        after = self.cfg()
        self.assertEqual(after.warnings, ())
        self.assertEqual(after.roles, before.roles, "role działają jak bez pliku (ten sam punkt, model i ctx)")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_update_preserves_unknown_keys_and_other_settings(self):
        self.write({"note": "moje", "default": "local",
                    "endpoints": {"local": {"url": "http://127.0.0.1:11434", "kind": "ollama", "comment": "GPU"},
                                  "lm": {"url": "http://lm:1234", "kind": "openai"}},
                    "roles": {"polish": {"model": "local:bielik", "ctx": 4096, "x": 1}, "summary": "local:qwen3:8b"}})
        os.chmod(self.path, 0o644)
        settings.set_endpoint({"name": "local", "url": "http://127.0.0.1:11500", "kind": "ollama"}, self.path)
        data = self.data()
        self.assertEqual(data["note"], "moje")
        self.assertEqual(data["endpoints"]["local"], {"url": "http://127.0.0.1:11500", "kind": "ollama",
                                                      "comment": "GPU"})
        self.assertEqual(data["endpoints"]["lm"], {"url": "http://lm:1234", "kind": "openai"})
        self.assertEqual(data["roles"]["polish"], {"model": "local:bielik", "ctx": 4096, "x": 1})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600, "models.json zawsze 0600")
        self.assertEqual(self.cfg().endpoint("local").url, "http://127.0.0.1:11500")

    def test_endpoint_validation_is_the_same_as_reading(self):
        cases = [{"name": "mac", "url": "ftp://mac", "kind": "ollama"},
                 {"name": "mac", "url": "http://[fd7a::1:11434", "kind": "ollama"},
                 {"name": "mac", "url": "http://user:secret@mac:11434", "kind": "ollama"},
                 {"name": "mac", "url": "http://mac:11434", "kind": "llamacpp"},
                 {"name": "mac", "url": None, "kind": "ollama"}]
        for req in cases:
            with self.subTest(req=req):
                message = self.refused(settings.set_endpoint, req, self.path)
                self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                          req["name"]: {"url": req["url"], "kind": req["kind"]}}},
                           os.path.join(self.tmp, "read.json"))
                warnings = models_config.load(os.path.join(self.tmp, "read.json")).warnings
                self.assertEqual([f"{message} — pominięty"], list(warnings))
                self.assertNotIn("secret", message)
                self.assertFalse(os.path.exists(self.path), "odmowa niczego nie zapisuje")
        bad = self.refused(settings.set_endpoint, {"name": "mój punkt", "url": "http://x:1"}, self.path)
        self.write({"endpoints": {"mój punkt": {"url": "http://x:1"}}}, os.path.join(self.tmp, "read.json"))
        self.assertEqual(models_config.load(os.path.join(self.tmp, "read.json")).warnings[0], f"{bad} — pominięty")

    def test_token_file_permissions_and_never_in_status(self):
        token = "tok-" + secrets.token_hex(16)
        settings.set_endpoint({"name": "mac", "url": "http://127.0.0.1:9", "kind": "ollama", "token": token},
                              self.path)
        token_file = os.path.join(self.tmp, "tide", "tokens", "mac")
        self.assertEqual(self.data()["endpoints"]["mac"]["tokenFile"], token_file)
        self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(token_file)).st_mode), 0o700)
        with open(token_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), token)
        self.assertNotIn(token, self.text())
        state = status.pending(self.cfg())
        self.assertTrue(state["endpoints"]["mac"]["auth"])
        self.assertNotIn(token, json.dumps(state))
        self.assertNotIn(token, repr(self.cfg()))

        # Pusty token — zostaje; nowy adres nie gubi tokenu.
        settings.set_endpoint({"name": "mac", "url": "http://127.0.0.1:10", "kind": "ollama", "token": ""}, self.path)
        self.assertEqual(self.data()["endpoints"]["mac"]["tokenFile"], token_file)
        self.assertTrue(os.path.exists(token_file))
        # Zły token: odmowa bez echa tokenu.
        message = self.refused(settings.set_endpoint, {"name": "mac", "url": "http://127.0.0.1:10",
                                                       "token": "zły token\n"}, self.path)
        self.assertNotIn("zły", message)
        self.refused(settings.set_endpoint, {"name": "mac", "url": "http://127.0.0.1:10", "token": "abc",
                                             "clearToken": True}, self.path)
        # clearToken — bez tokenu, nasz plik znika.
        settings.set_endpoint({"name": "mac", "url": "http://127.0.0.1:10", "clearToken": True}, self.path)
        self.assertNotIn("tokenFile", self.data()["endpoints"]["mac"])
        self.assertFalse(os.path.exists(token_file))

    def test_token_is_rolled_back_when_models_json_cannot_be_written(self):
        settings.set_endpoint({"name": "mac", "url": "http://mac:1", "token": "stary"}, self.path)
        before = self.text()
        token_file = os.path.join(self.tmp, "tide", "tokens", "mac")
        real_replace = os.replace

        def failing(src, dst):
            if dst.endswith("models.json"):
                raise PermissionError("ro")
            return real_replace(src, dst)

        with mock.patch.object(settings.os, "replace", side_effect=failing):
            message = self.refused(settings.set_endpoint, {"name": "mac", "url": "http://mac:2", "token": "nowy"},
                                   self.path)
        self.assertEqual(message, "models.json: nie udało się zapisać (PermissionError)")
        self.assertEqual(self.text(), before, "stary plik nietknięty")
        self.assertEqual(self.text(token_file).strip(), "stary", "token wrócił")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["models.json", "tokens"],
                         "bez plików tymczasowych")
        self.assertEqual(os.listdir(os.path.dirname(token_file)), ["mac"])

    def test_broken_file_is_never_overwritten(self):
        for text in ("{nie json", "[1, 2]", '{"endpoints": [1]}', '{"roles": "x"}'):
            with self.subTest(text=text):
                self.write(text)
                message = self.refused(settings.set_default, "local", self.path)
                self.assertIn("nie zapisuję", message)
                self.assertEqual(self.text(), text)

    def test_remove_rules(self):
        self.write({"default": "local", "endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                                     "mac": {"url": "http://mac:11434"},
                                                     "lm": {"url": "http://lm:1234", "kind": "openai"}},
                    "roles": {"agent": "mac:qwen3-coder:30b", "embed": {"model": "mac:bge-m3"},
                              "summary": "qwen3:8b"}})
        self.assertEqual(self.refused(settings.remove_endpoint, "local", self.path),
                         "serwer „local” jest domyślny — najpierw ustaw inny serwer jako domyślny")
        self.assertEqual(self.refused(settings.remove_endpoint, "mac", self.path),
                         "serwer „mac” jest używany przez role: agent, embed — przepnij je na inny serwer albo na "
                         "domyślny, zanim go usuniesz")
        self.assertEqual(self.refused(settings.remove_endpoint, "nope", self.path), "nieznany serwer „nope”")
        token_file = settings.token_path("lm", self.path)
        settings.set_endpoint({"name": "lm", "url": "http://lm:1234", "kind": "openai", "token": "t"}, self.path)
        self.assertTrue(os.path.exists(token_file))
        settings.remove_endpoint("lm", self.path)
        self.assertNotIn("lm", self.data()["endpoints"])
        self.assertFalse(os.path.exists(token_file), "token usuniętego punktu znika")
        self.assertEqual(self.cfg().warnings, ())

        self.write({"endpoints": {"mac": {"url": "http://mac:1"}, "lm": {"url": "http://lm:1"}}, "default": "mac"})
        self.assertIn("jest domyślny", self.refused(settings.remove_endpoint, "mac", self.path))

    def test_cannot_remove_last_endpoint(self):
        self.write({"endpoints": {"mac": {"url": "http://mac:1"}, "zly": {"url": "ftp://x"}}, "default": "zly"})
        # `default` wskazuje pominięty punkt — usunąć można tylko poprawiony domyślny, a ostatniego poprawnego nigdy.
        self.assertEqual(self.refused(settings.remove_endpoint, "mac", self.path),
                         "serwer „mac” jest jedynym serwerem — dodaj inny, zanim go usuniesz")

    def test_default_switch_moves_bare_roles_and_pinned_roles_stay(self):
        settings.set_endpoint({"name": "mac", "url": "http://mac-studio:11434", "kind": "ollama"}, self.path)
        settings.set_role({"role": "agent", "model": "qwen3-coder:30b"}, self.path)
        settings.set_role({"role": "describe", "model": "qwen3:8b", "endpoint": "local"}, self.path)
        self.assertEqual(self.data()["roles"]["agent"], "qwen3-coder:30b", "rola bez punktu idzie za default")
        self.assertEqual(self.data()["roles"]["describe"], "local:qwen3:8b")
        settings.set_default("mac", self.path)
        cfg = self.cfg()
        self.assertEqual(cfg.default, "mac")
        self.assertEqual((cfg.roles["summary"].endpoint, cfg.roles["agent"].endpoint), ("mac", "mac"))
        self.assertEqual((cfg.roles["describe"].endpoint, cfg.roles["describe"].ctx), ("local", 8192))
        self.assertEqual(self.refused(settings.set_default, "nope", self.path), "default: nieznany serwer „nope”")
        self.write({**self.data(), "endpoints": {**self.data()["endpoints"], "zly": {"url": "ftp://x"}}})
        self.assertIn("jest pominięty", self.refused(settings.set_default, "zly", self.path))
        settings.set_default("local", self.path)
        self.assertEqual(self.cfg().roles["agent"].endpoint, "local")

    def test_roles_report_whether_they_are_pinned(self):
        self.write({"default": "local", "endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                                     "mac": {"url": "http://mac:11434"}, "zly": {"url": "ftp://x"}},
                    "roles": {"summary": "local:qwen3:8b", "agent": "qwen3-coder:30b",
                              "embed": {"model": "mac:bge-m3"}, "broken": "zly:x"}})
        cfg = self.cfg()
        pinned = {name: role.pinned for name, role in cfg.roles.items()}
        self.assertEqual(pinned, {"summary": True, "agent": False, "embed": True, "broken": True, "describe": False},
                         "przypięta także do obecnego domyślnego; brakująca rola domyślna — nie")
        state = status.pending(cfg)
        self.assertEqual({n: r["pinned"] for n, r in state["roles"].items()}, pinned)
        # SetRole bez endpoint odpina rolę przypiętą do domyślnego serwera.
        settings.set_role({"role": "summary", "model": "qwen3:8b"}, self.path)
        self.assertFalse(self.cfg().roles["summary"].pinned)

    def test_set_role_ctx_and_validation(self):
        settings.set_role({"role": "summary", "model": "qwen3:8b", "ctx": 4096}, self.path)
        self.assertEqual(self.data()["roles"]["summary"], {"model": "qwen3:8b", "ctx": 4096})
        settings.set_role({"role": "summary", "model": "qwen3:14b"}, self.path)  # bez klucza ctx — bez zmiany
        self.assertEqual(self.data()["roles"]["summary"], {"model": "qwen3:14b", "ctx": 4096})
        settings.set_role({"role": "summary", "model": "qwen3:14b", "ctx": None}, self.path)  # null — domyślny
        self.assertEqual(self.data()["roles"]["summary"], "qwen3:14b")
        self.assertEqual(self.cfg().roles["summary"].ctx, 8192)
        settings.set_role({"role": "agent", "model": "qwen3-coder:30b", "ctx": 0}, self.path)
        self.assertIsNone(self.cfg().roles["agent"].ctx)

        message = self.refused(settings.set_role, {"role": "summary", "model": "x", "ctx": -1}, self.path)
        self.write({"roles": {"summary": {"model": "x", "ctx": -1}}}, os.path.join(self.tmp, "read.json"))
        self.assertEqual(models_config.load(os.path.join(self.tmp, "read.json")).warnings[0], f"{message} — pominięta")
        self.assertIn("ctx musi być", message)
        self.refused(settings.set_role, {"role": "summary", "model": "x", "ctx": True}, self.path)
        self.assertEqual(self.refused(settings.set_role, {"role": "summary", "model": " "}, self.path),
                         "rola „summary”: podaj nazwę modelu")
        self.assertIn("niepoprawna nazwa", self.refused(settings.set_role, {"role": "zła rola", "model": "x"},
                                                        self.path))
        self.assertEqual(self.refused(settings.set_role, {"role": "agent", "model": "x", "endpoint": "mac"},
                                      self.path), "rola „agent”: serwer „mac” nie istnieje")
        self.assertIn("wybierz serwer jawnie", self.refused(settings.set_role, {"role": "agent", "model": "local:x"},
                                                           self.path))
        bielik = "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M"
        settings.set_role({"role": "polish", "model": bielik, "endpoint": "local"}, self.path)
        self.assertEqual((self.cfg().roles["polish"].endpoint, self.cfg().roles["polish"].model), ("local", bielik))

    def test_agent_program_round_trip(self):
        """PT-39: `agentProgram` — jeden klucz najwyższego poziomu; zapis nie rusza ról ani serwerów."""
        self.assertEqual(self.cfg().agent_program, "codex", "bez pliku — Codex")
        settings.set_agent_program("claude", self.path)
        self.assertEqual(self.data(), {**settings.defaults(), "agentProgram": "claude"},
                         "pierwszy zapis zaczyna od wartości domyślnych")
        self.assertEqual(self.cfg().agent_program, "claude")
        settings.set_role({"role": "agent", "model": "qwen3-coder:30b"}, self.path)
        settings.set_default("local", self.path)
        self.assertEqual(self.data()["agentProgram"], "claude", "inne zapisy go zostawiają")
        settings.set_agent_program("codex", self.path)
        self.assertEqual((self.data()["agentProgram"], self.data()["roles"]["agent"]), ("codex", "qwen3-coder:30b"))
        for value in ("Codex", "opencode", "", None, 1):
            with self.subTest(value=value):
                message = self.refused(settings.set_agent_program, value, self.path)
                self.assertIn("(codex albo claude)", message)
                self.assertEqual(message, models_config.agent_program_problem(value))
        self.assertEqual(self.data()["agentProgram"], "codex", "odmowa niczego nie zapisuje")
        # Ręcznie wpisana zła wartość: ostrzeżenie, Codex; poprawny zapis z okna ją naprawia.
        self.write({"agentProgram": "Claude"})
        self.assertEqual((self.cfg().agent_program, len(self.cfg().warnings)), ("codex", 1))
        settings.set_agent_program("claude", self.path)
        self.assertEqual((self.cfg().agent_program, self.cfg().warnings), ("claude", ()))

    def test_handwritten_warnings_do_not_block_but_new_ones_do(self):
        self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"}, "zly": {"url": "ftp://x"}},
                    "roles": {"agent": "zly:model"}})
        settings.set_role({"role": "summary", "model": "qwen3:14b"}, self.path)
        self.assertEqual(self.data()["roles"]["summary"], "qwen3:14b")
        self.assertEqual(self.data()["endpoints"]["zly"], {"url": "ftp://x"}, "cudzy błąd zostaje, nie znika")
        # Naprawa złego punktu z okna: rola znów dostępna.
        settings.set_endpoint({"name": "zly", "url": "http://zly:11434", "kind": "ollama"}, self.path)
        cfg = self.cfg()
        self.assertEqual((cfg.warnings, cfg.roles["agent"].error), ((), ""))

    def test_all_endpoints_invalid_keeps_implicit_local(self):
        self.write({"endpoints": {"zly": {"url": "ftp://x"}}})
        settings.set_endpoint({"name": "mac", "url": "http://mac:1"}, self.path)
        cfg = self.cfg()
        self.assertEqual(cfg.default, "local", "dodanie punktu nie przenosi ról z domyślnego local")
        self.assertIn("local", cfg.endpoints)

    def test_new_endpoint_named_like_a_model_prefix_is_refused(self):
        # „qwen3” jako nazwa punktu: „qwen3:8b” w rolach bez punktu czytałoby się jako punkt qwen3 + model 8b.
        settings.set_role({"role": "agent", "model": "qwen3-coder:30b"}, self.path)
        before = self.text()
        message = self.refused(settings.set_endpoint, {"name": "qwen3", "url": "http://mac:11434"}, self.path)
        self.assertEqual(message, "nazwa „qwen3” zmieniłaby role describe, summary (model „qwen3:8b” czytany "
                                  "jako serwer „qwen3”) — wybierz inną nazwę serwera")
        self.assertEqual(self.text(), before, "nic nie zapisane")
        roles = self.cfg().roles
        self.assertEqual((roles["summary"].endpoint, roles["summary"].model), ("local", "qwen3:8b"))
        # Przypięta rola „local:qwen3:8b” nie jest dwuznaczna — taki punkt można dodać.
        settings.set_role({"role": "summary", "model": "qwen3:8b", "endpoint": "local"}, self.path)
        settings.set_role({"role": "describe", "model": "qwen3:8b", "endpoint": "local"}, self.path)
        settings.set_endpoint({"name": "qwen3", "url": "http://mac:11434"}, self.path)
        roles = self.cfg().roles
        self.assertEqual((roles["summary"].endpoint, roles["summary"].model), ("local", "qwen3:8b"))
        self.assertEqual((roles["agent"].endpoint, roles["agent"].model), ("local", "qwen3-coder:30b"))

    def test_null_sections_read_as_missing(self):
        self.write({"endpoints": None, "roles": None, "note": 1})
        settings.set_endpoint({"name": "mac", "url": "http://mac:1"}, self.path)
        data = self.data()
        self.assertEqual(sorted(data["endpoints"]), ["local", "mac"])
        self.assertEqual(data["note"], 1)
        self.assertEqual(self.cfg().warnings, ())

    def test_input_limits_and_trimmed_messages(self):
        long = "x" * 600
        self.assertEqual(self.refused(settings.set_endpoint, {"name": "mac", "url": "http://" + long}, self.path),
                         "url: najwyżej 512 znaków")
        self.assertEqual(self.refused(settings.set_role, {"role": "agent", "model": long}, self.path),
                         "rola „agent”: model: najwyżej 512 znaków")
        self.assertIn("najwyżej", self.refused(settings.set_endpoint, {"name": "mac", "url": "http://mac:1",
                                                                        "token": "t" * 5000}, self.path))
        message = self.refused(settings.set_endpoint, {"name": "mac", "url": "http://mac:1", "kind": long}, self.path)
        self.assertLess(len(message), 120)
        self.assertLess(len(self.refused(settings.set_report_summary, long, self.report)), 120)
        self.assertLess(len(self.refused(settings.set_role, {"role": long, "model": "x"}, self.path)), 120)

    def test_atomic_write_syncs_the_directory(self):
        synced = []
        real_fsync = os.fsync

        def fsync(fd):
            synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return real_fsync(fd)

        with mock.patch.object(settings.os, "fsync", side_effect=fsync):
            settings.set_default("local", self.path)
        self.assertEqual(synced, [False, True], "plik, potem katalog po rename")

    def test_symlinked_file_stays_a_symlink(self):
        real = os.path.join(self.tmp, "dotfiles", "models.json")
        self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"}}}, real)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        os.symlink(real, self.path)
        settings.set_default("local", self.path)
        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(self.data(real)["default"], "local")


class ReportSummaryTest(SettingsTestCase):
    def test_writes_summary_and_preserves_other_keys(self):
        self.write({"at": "06:30", "summaryModel": "opus", "extra": [1]}, self.report)
        os.chmod(self.report, 0o640)
        settings.set_report_summary("local", self.report)
        self.assertEqual(self.data(self.report), {"at": "06:30", "summaryModel": "opus", "extra": [1],
                                                  "summary": "local"})
        self.assertEqual(stat.S_IMODE(os.stat(self.report).st_mode), 0o640)
        loaded, warnings = schedule.load_settings(self.report)
        self.assertEqual((loaded.summary, loaded.at, warnings), ("local", 6 * 60 + 30, []))

    def test_creates_file_and_refuses_unknown_mode(self):
        settings.set_report_summary("rules", self.report)
        self.assertEqual(self.data(self.report), {"summary": "rules"})
        message = self.refused(settings.set_report_summary, "Local", self.report)
        _s, warnings = schedule.load_settings(self._with({"summary": "Local"}))
        self.assertEqual(warnings, [f"{message} — opis z reguł"])
        self.write("{zły", self.report)
        self.assertIn("nie zapisuję", self.refused(settings.set_report_summary, "local", self.report))

    def _with(self, data):
        path = os.path.join(self.tmp, "other-report.json")
        self.write(data, path)
        return path

    def test_report_state(self):
        self.assertEqual(settings.report_state(self.report),
                         {"summary": "claude", "configured": "claude", "summaryModel": "sonnet", "raw": None,
                          "pinned": False, "path": self.report, "warnings": []})
        self.write({"summary": "Local"}, self.report)
        state = settings.report_state(self.report)
        self.assertEqual((state["summary"], state["configured"], state["raw"]), ("rules", "rules", "Local"))
        self.assertEqual(len(state["warnings"]), 1)
        state = settings.report_state(self.report, live="claude", pinned=True)
        self.assertEqual((state["summary"], state["configured"], state["pinned"]), ("claude", "rules", True))


class TestAndPullTest(SettingsTestCase):
    def test_endpoint_for_test_without_saving(self):
        endpoint = settings.endpoint_for_test({"url": "http://mac:11434/", "kind": "ollama", "token": "abc"},
                                              self.path)
        self.assertEqual((endpoint.url, endpoint.kind, endpoint.token, endpoint.token_file),
                         ("http://mac:11434", "ollama", "abc", ""))
        self.assertNotIn("abc", repr(endpoint))
        self.assertEqual(endpoint.headers()["Authorization"], "Bearer abc")
        self.assertFalse(os.path.exists(self.path), "próba niczego nie zapisuje")
        self.assertEqual(settings.endpoint_for_test({"name": "local"}, self.path).url, "http://127.0.0.1:11434")
        self.assertEqual(self.refused(settings.endpoint_for_test, {"name": "mac"}, self.path), "nieznany serwer „mac”")
        self.assertIn("niepoprawny adres", self.refused(settings.endpoint_for_test, {"url": "mac"}, self.path))

    def test_probe_against_fake_server(self):
        server = start_server(self, ollama_routes())
        endpoint = settings.endpoint_for_test({"url": server.url, "kind": "ollama", "token": "tok"}, self.path)
        self.assertEqual(status.probe(endpoint), {"ok": True, "error": "", "version": "0.40.11", "models": 3})
        self.assertEqual({(r["path"], r["auth"]) for r in server.requests},
                         {("/api/version", "Bearer tok"), ("/api/tags", "Bearer tok")})
        down = settings.endpoint_for_test({"url": f"http://127.0.0.1:{closed_port()}"}, self.path)
        result = status.probe(down)
        self.assertFalse(result["ok"])
        self.assertIn("serwer nie odpowiada", result["error"])

    def test_endpoint_for_pull(self):
        self.write({"endpoints": {"local": {"url": "http://127.0.0.1:11434"},
                                  "lm": {"url": "http://lm:1234", "kind": "openai"}}})
        endpoint, model = settings.endpoint_for_pull({"endpoint": "local", "model": "qwen3:8b"}, self.path)
        self.assertEqual((endpoint.name, model), ("local", "qwen3:8b"))
        self.assertIn("tylko z Ollamą", self.refused(settings.endpoint_for_pull, {"endpoint": "lm", "model": "x"},
                                                     self.path))
        self.assertIn("nazwa modelu", self.refused(settings.endpoint_for_pull,
                                                   {"endpoint": "local", "model": "x y"}, self.path))
        self.assertIn("nieznany serwer", self.refused(settings.endpoint_for_pull, {"endpoint": "mac", "model": "x"},
                                                     self.path))

    def test_pull_progress_and_errors(self):
        server = start_server(self, {("POST", "/api/pull"): (200, pull_stream(PULL_LINES))})
        endpoint = models_config.Endpoint("local", server.url, "ollama")
        progress = []
        clock = iter(range(0, 1000, 1)).__next__  # każda linia „sekundę” później — bez dławienia
        models_pull.pull(endpoint, "qwen3:8b", progress.append, clock=clock)
        self.assertEqual(server.requests[0]["body"], {"model": "qwen3:8b", "stream": True})
        self.assertEqual(progress[2], {"status": "pulling aaa", "completed": 600, "total": 1000})
        self.assertEqual(progress[3], {"status": "pulling bbb", "completed": 700, "total": 1100})
        self.assertEqual(progress[-1], {"status": "success", "completed": 1100, "total": 1100})

        server.routes[("POST", "/api/pull")] = (200, pull_stream([
            {"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}]))
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "nope:1b", lambda p: None)
        self.assertIn("nie udało się pobrać nope:1b: pull model manifest: file does not exist", str(ctx.exception))

        server.routes[("POST", "/api/pull")] = (200, pull_stream([{"status": "pulling manifest"}]))
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None)
        self.assertIn("przerwane przez serwer", str(ctx.exception))

        server.routes[("POST", "/api/pull")] = (500, {"error": "boom"})
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None)
        self.assertIn("serwer odpowiedział 500: boom", str(ctx.exception))

    def test_stream_headers_have_one_deadline(self):
        import time

        def drip_headers(handler):
            try:
                for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 100:
                    handler.wfile.write(bytes([byte]))
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        server = start_server(self, {("POST", "/api/pull"): (200, drip_headers)})
        endpoint = models_config.Endpoint("local", server.url, "ollama")
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None, connect_timeout=0.5)
        self.assertLess(time.monotonic() - started, 2, "nagłówki sączone po bajcie nie trzymają wątku")
        self.assertIn("nie odpowiedział w 0.5 s", str(ctx.exception))

    def test_pull_cancel_and_idle_timeout(self):
        import threading
        import time

        def hang(handler):
            try:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                data = b'{"status": "pulling manifest"}\n'
                handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                handler.wfile.flush()
                time.sleep(3)
            except OSError:
                pass

        server = start_server(self, {("POST", "/api/pull"): (200, hang)})
        endpoint = models_config.Endpoint("local", server.url, "ollama")
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None, idle_timeout=0.3)
        self.assertIn("nie odpowiedział w 0.3 s", str(ctx.exception))

        cancel = Cancel()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None, cancel=cancel)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("usługa się zamyka", str(ctx.exception))
        with self.assertRaises(ModelError):
            models_pull.pull(endpoint, "qwen3:8b", lambda p: None, cancel=cancel)  # po set() nic nie startuje


class DescriberModeTest(unittest.TestCase):
    def test_set_mode_switches_without_restart(self):
        from tide_daemon.coordination import describe
        describer = describe.Describer(mode="claude")
        describer.set_mode("local")
        self.assertEqual((describer.mode, describer.timeout), ("local", describe.LOCAL_TIMEOUT))
        describer.set_mode("Local")  # nieznany tryb — bez zmiany
        self.assertEqual(describer.mode, "local")
        describer.set_mode("rules")
        self.assertEqual((describer.mode, describer.timeout), ("rules", describe.TIMEOUT))
        describer._notes["k"] = "Model lokalny niedostępny"
        describer._failed["k"] = 1.0
        describer.set_mode("rules")  # ten sam tryb — nic nie znika
        self.assertEqual((describer._notes, describer._failed), ({"k": "Model lokalny niedostępny"}, {"k": 1.0}))
        describer.set_mode("claude")
        self.assertEqual((describer._notes, describer._failed), ({}, {}), "nowy tryb — bez starych błędów")
        pinned = describe.Describer(mode="claude", timeout=5)
        pinned.set_mode("local")
        self.assertEqual(pinned.timeout, 5, "limit podany wprost zostaje")


if __name__ == "__main__":
    unittest.main()
