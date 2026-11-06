"""Instrukcje i transport adapter → zainstalowany rzeczywisty CLI → prywatny daemon.

Testy nie oceniają syntezy modeli; treść raportu jest jawnym wejściem testowym.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from . import test_issues_dbus as integration
from .fakebin import make_env
from .privatebus import requires_dbus

REPO = integration.REPO
PACKAGE = REPO / 'cli/agents'


class PackageTest(unittest.TestCase):
    def install(self, env, *args, code=0):
        result = subprocess.run([sys.executable, str(PACKAGE / 'install.py'), *args],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def test_installed_instructions_and_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            self.install(env)
            root = Path(env['XDG_DATA_HOME']) / 'tide/agents'
            codex = Path(env['HOME']) / '.codex/skills/tide-issue'
            claude = root / 'claude/tide'
            common = (PACKAGE / 'shared/synthesis.md').read_text()
            for adapter in (codex, claude):
                self.assertEqual((adapter / 'references/synthesis.md').read_text(), common)
                self.assertEqual((adapter / 'scripts/issue_adapter.py').read_bytes(),
                                 (PACKAGE / 'shared/issue_adapter.py').read_bytes())
            for phrase in ('relacje użytkownika', 'oczekiwania', 'hipotezy/założenia', 'odrzucone warianty',
                           'przypadkami brzegowymi', 'Nie skanuj projektu', 'Nie wymyślaj',
                           'Nie twórz HTML-a, kart, ticketów ani planu implementacji',
                           'Nie akceptuj sprawy', 'expectedVersion', 'request-id', 'przed długą syntezą',
                           'Brak bajtów obrazu', 'nie oznaczają rezygnacji', 'spójność istniejącego manifestu'):
                self.assertIn(phrase.casefold(), common.casefold())
            self.assertIn('references/synthesis.md', (codex / 'SKILL.md').read_text())
            self.assertIn('references/synthesis.md', (claude / 'commands/issue.md').read_text())
            foreign = codex / 'moj-plik'
            foreign.write_text('obcy')
            self.install(env)
            self.assertTrue(list((root / 'backups').iterdir()))
            owned = codex / 'SKILL.md'
            original = owned.read_bytes()
            owned.write_text('lokalna zmiana')
            self.install(env, code=1)
            self.install(env, '--uninstall', code=1)
            self.assertEqual(owned.read_text(), 'lokalna zmiana')
            owned.write_bytes(original)
            self.install(env, '--uninstall')
            self.assertFalse(owned.exists())
            self.assertEqual(foreign.read_text(), 'obcy')

    def test_missing_programs_and_collision_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            for name in ('claude', 'codex'):
                Path(tmp, 'fakebin', name).unlink()
            self.install(env)
            self.assertFalse(Path(env['HOME'], '.codex/skills/tide-issue/SKILL.md').exists())
            self.assertTrue(Path(env['XDG_DATA_HOME'], 'tide/agents/claude/tide/commands/issue.md').exists())
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            conflict = Path(env['HOME'], '.codex/skills/tide-issue/SKILL.md')
            conflict.parent.mkdir(parents=True)
            conflict.write_text('obce')
            self.install(env, code=1)
            self.assertEqual(conflict.read_text(), 'obce')
            self.assertFalse(Path(env['XDG_DATA_HOME'], 'tide/agents/manifest.json').exists())


@requires_dbus
class AdapterTest(unittest.TestCase):
    setUp = integration.IssuesDbusTest.setUp
    start = integration.IssuesDbusTest.start
    run_cli = integration.IssuesDbusTest.run_cli
    board_call = integration.IssuesDbusTest.board_call

    def prepare(self):
        self.start()
        self.board_call('UpsertProject', '{"name":"Alfa"}')
        self.env['REAL_TIDE'] = self.cli
        self.env['ISSUE_CALLS'] = str(Path(self.tmp, 'calls.jsonl'))
        observer = Path(self.tmp, 'fakebin/tide')
        observer.write_text('#!/usr/bin/python3\n' + (REPO / 'daemon/tests/fixtures/issue_cli_observer.py').read_text())
        observer.chmod(0o755)
        # Okno izolowane: launcher zapisuje wybór, bez GUI i bez pulpitu.
        window = Path(self.env['XDG_DATA_HOME'], 'tide/tide-window/tide.py')
        window.parent.mkdir(parents=True)
        window.write_text('import pathlib,sys\npathlib.Path(' + repr(str(Path(self.tmp, 'opened'))) +
                          ').write_text(sys.argv[-1])\n')
        self.adapters = [Path(self.env['HOME'], '.codex/skills/tide-issue/scripts/issue_adapter.py'),
                         Path(self.env['XDG_DATA_HOME'], 'tide/agents/claude/tide/scripts/issue_adapter.py')]

    def adapter(self, script, *args, code=0):
        result = subprocess.run([sys.executable, str(script), *map(str, args)], env=self.env,
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def begin(self, script, folder, **kwargs):
        return self.adapter(script, 'begin', '--session', folder, '--title', 'Filtr projektu',
                            '--project', 'alfa', '--report', self.report, **kwargs)

    def finish(self, script, folder, *args, **kwargs):
        return self.adapter(script, 'finish', '--session', folder, '--title', 'Filtr projektu',
                            '--project', 'alfa', '--report', self.report, *args, **kwargs)

    def calls(self):
        return [json.loads(line) for line in Path(self.env['ISSUE_CALLS']).read_text().splitlines()]

    def test_both_adapters_missing_then_separate_waiver_and_manual_submit(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            with self.subTest(adapter=script):
                folder = Path(self.tmp, 'session-' + str(n))
                initial = self.begin(script, folder)
                source = Path(self.tmp, 'opis.txt')
                source.write_text('Jawne kroki użytkownika')
                draft = self.finish(script, folder, '--file', source, '--missing', 'drugi obraz')
                issue = self.run_cli('issue', 'show', draft['id'])['issue']
                self.assertEqual(issue['state'], 'draft')
                self.assertEqual(len(issue['attachments']), 1)
                self.assertIn('Brak dostępu do bajtów', issue['report'])
                self.assertFalse(any(c[:2] == ['issue', 'submit'] and c[2] == draft['id'] for c in self.calls()))
                # Ponowienie finish nie jest rezygnacją.
                self.assertEqual(self.finish(script, folder)['state'], 'draft')
                ready = self.adapter(script, 'waive', '--session', folder, '--evidence', 'drugi obraz',
                                     '--decision', 'rezygnuję z drugiego obrazu')
                self.assertEqual((ready['id'], ready['state']), (initial['id'], 'ready'))
                final = self.run_cli('issue', 'show', ready['id'])['issue']
                self.assertIn('rezygnuję z drugiego obrazu', final['report'])
                self.assertIn('pliku nie dołączono', final['report'])
                self.assertEqual(final['attachments'], issue['attachments'])
                self.assertTrue(any(c[:3] == ['issue', 'open', ready['id']] for c in self.calls()))
        # Daemon nie ma ukrytej listy brakujących dowodów wynikającej z prozy.
        fresh = self.run_cli('issue', 'create', '--title', 'Brak obrazu', '--report', self.report)['issue']
        self.assertEqual(self.run_cli('issue', 'submit', fresh['id'], '--expected-version', '1')['issue']['state'], 'ready')

    def test_both_adapters_attach_failure_and_conflict_after_waiver(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            folder = Path(self.tmp, 'error-' + str(n))
            ident = self.begin(script, folder)['id']
            self.finish(script, folder, '--file', str(Path(self.tmp, 'nie-ma.png')), code=1)
            issue = self.run_cli('issue', 'show', ident)['issue']
            self.assertEqual(issue['state'], 'draft')
            self.assertIn('Błąd attach', issue['report'])
            folder = Path(self.tmp, 'conflict-' + str(n))
            ident = self.begin(script, folder)['id']
            self.finish(script, folder, '--missing', 'drugi obraz')
            issue = self.run_cli('issue', 'show', ident)['issue']
            self.run_cli('issue', 'update', ident, '--expected-version', str(issue['version']), '--title', 'Inna zmiana')
            reply = self.adapter(script, 'waive', '--session', folder, '--evidence', 'drugi obraz',
                                 '--decision', 'rezygnuję z drugiego obrazu', code=1)
            self.assertIn('version-conflict', reply['error'])
            self.assertEqual(self.run_cli('issue', 'show', ident)['issue']['title'], 'Inna zmiana')
            self.assertFalse(any(c[:3] == ['issue', 'submit', ident] for c in self.calls()))

    def test_both_adapters_lost_create_reply_and_kill_after_create(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            folder = Path(self.tmp, 'lost-' + str(n))
            flag = Path(self.tmp, 'lose-reply')
            flag.touch()
            self.env['ISSUE_LOSE_REPLY'] = str(flag)
            self.begin(script, folder, code=1)
            request = json.loads((folder / 'session.json').read_text())['requestId']
            issue = self.run_cli('issue', 'list')['issues'][-1]
            result = self.begin(script, folder)
            self.assertEqual(result['id'], issue['id'])
            self.assertEqual(json.loads((folder / 'session.json').read_text())['requestId'], request)
            marker = Path(self.tmp, 'pause-' + str(n))
            self.env['ISSUE_PAUSE'] = str(marker)
            folder = Path(self.tmp, 'kill-' + str(n))
            process = subprocess.Popen([sys.executable, str(script), 'begin', '--session', str(folder),
                                       '--title', 'Przerwany', '--project', 'alfa', '--report', self.report],
                                       env=self.env, stdout=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(5)
                del self.env['ISSUE_PAUSE']
            saved = json.loads((folder / 'session.json').read_text())
            self.assertEqual(self.run_cli('issue', 'show', saved['id'])['issue']['state'], 'draft')
            self.assertEqual(self.finish(script, folder)['id'], saved['id'])

    def test_unavailable_api_creates_no_local_session(self):
        for script in (PACKAGE / 'shared/issue_adapter.py',):
            folder = Path(self.tmp, 'unavailable')
            self.begin(script, folder, code=1)
            self.assertFalse(folder.exists())
