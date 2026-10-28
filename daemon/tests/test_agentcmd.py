"""Wspólne miejsce składania polecenia agenta (PT-76) — tasks/agentcmd.py.

- interaktywnie (Konsola): to samo, co dawało agent_argv + local.options — Claude Code i Codex × chmura,
  Ollama na tym komputerze, inny punkt z tokenem,
- w tle (`claude -p`, `codex exec`): argv i środowisko według wzoru piaskownicy z PT-75, polecenie przez
  stdin, środowisko bez agenta SSH, tokenów GitHuba i pomocników haseł, martwy D-Bus, token punktu nigdy
  w argv ani w środowisku polecenia,
- zatrzymanie kroku na atrapie programu (skrypt pisze pliki i czeka): proces kończy się, na gałęzi
  zostaje commit WIP z nowymi i zmienionymi plikami, wracają ostatnie wiadomości z atrapy dziennika.
Prawdziwy model nigdy nie jest wywoływany.
"""
import unittest

from tide_daemon.tasks import agentcmd, local

OLLAMA = local.LocalAgent("local", "http://127.0.0.1:11434", "ollama", "qwen3-coder:30b", 65536)
MAC = local.LocalAgent("mac", "http://mac.lan:11434", "ollama", "qwen3-coder:30b", 65536, "/home/u/.tok")


class InteractiveTest(unittest.TestCase):
    def test_cloud_command_is_plain_agent_argv(self):
        cmd = agentcmd.interactive("claude", "/bin/claude", "Popraw nagłówek", ["/t/1/a.png"], ["/b/docs"])
        self.assertEqual(cmd.argv, agentcmd.agent_argv("claude", "/bin/claude", "Popraw nagłówek", ["/t/1/a.png"],
                                                       ["/b/docs"]))
        self.assertEqual((cmd.env, cmd.secret_env, cmd.unset_env, cmd.token_files), ({}, {}, [], []))

    def test_local_command_is_model_options_then_agent_argv(self):
        for tool in ("claude", "codex"):
            for agent in (OLLAMA, MAC):
                opts = local.options(tool, agent)
                cmd = agentcmd.interactive(tool, "/bin/" + tool, "zrób to", [], [], agent)
                self.assertEqual(cmd.argv, agentcmd.agent_argv(tool, "/bin/" + tool, "zrób to", [], [], opts.argv))
                self.assertEqual((cmd.env, cmd.secret_env, cmd.unset_env, cmd.token_files),
                                 (opts.env, opts.secret_env, opts.unset_env, opts.token_files))

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(agentcmd.UnknownTool):
            agentcmd.interactive("vim", "vim", "a b")


if __name__ == "__main__":
    unittest.main()
