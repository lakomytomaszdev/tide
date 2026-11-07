"""Instrukcje i transport adapter → zainstalowany rzeczywisty CLI → prywatny daemon.

Testy nie oceniają syntezy modeli; treść raportu jest jawnym wejściem testowym.
"""
import json
import importlib.util
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_issues_dbus as integration
from .fakebin import make_env
from .privatebus import requires_dbus

REPO = integration.REPO
PACKAGE = REPO / 'cli/agents'
SPEC = importlib.util.spec_from_file_location('issue_fixture', PACKAGE / 'eval/fixture.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
EVAL_SPEC = importlib.util.spec_from_file_location('issue_evaluator', PACKAGE / 'evaluate.py')
EVALUATOR = importlib.util.module_from_spec(EVAL_SPEC)
EVAL_SPEC.loader.exec_module(EVALUATOR)
ADAPTER_SPEC = importlib.util.spec_from_file_location('issue_decision_adapter', PACKAGE / 'shared/issue_adapter.py')
ADAPTER = importlib.util.module_from_spec(ADAPTER_SPEC)
ADAPTER_SPEC.loader.exec_module(ADAPTER)


class DecisionAdapterTest(unittest.TestCase):
    """Kontrakt atrapowego CLI: potwierdzenie, tożsamość zapisu i ładunek replay."""

    def invoke(self, argv):
        return ADAPTER.run(ADAPTER.parser().parse_args(argv))

    def test_backlog_inspection_is_read_only_and_conflict_keeps_exact_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp, 'S-4')
            issue = {'id': 'S-4', 'version': 4, 'state': 'ready', 'title': 'Zażółć',
                     'project': 'plasma-tide', 'report': 'Pełny raport ; $(dane)', 'attachments': []}
            info = {'ok': True, 'apiVersion': 1, 'capabilities': ['issue.create', 'issue.show',
                'issue.update', 'issue.attach', 'issue.submit', 'issue.accept']}
            with mock.patch.object(ADAPTER, 'cli', side_effect=[info, {'ok': True, 'issue': issue}]) as cli:
                shown = self.invoke(['inspect', '--session', str(session), '--id', 'S-4', '--destination', 'backlog'])
            self.assertEqual(shown['issue']['report'], issue['report'])
            self.assertEqual(shown['confirmation'], 'Potwierdzam backlog S-4 v4')
            self.assertEqual(cli.call_count, 2)
            self.assertFalse((session / 'decisionOperation').exists())

            with mock.patch.object(ADAPTER, 'cli', return_value=info):
                with self.assertRaisesRegex(ADAPTER.AdapterError, 'dokładnej wersji'):
                    self.invoke(['backlog', '--session', str(session), '--id', 'S-4',
                                 '--expected-version', '6', '--decision', shown['confirmation']])

            def lose_at_dispatch(*argv):
                saved = json.loads((session / 'session.json').read_text())['decisionOperation']
                self.assertEqual((saved['id'], saved['expectedVersion'], saved['verb']), ('S-4', 4, 'backlog'))
                self.assertTrue(saved['requestId'])
                self.assertEqual(argv[0:2], ('backlog', 'S-4'))
                self.assertEqual(argv[argv.index('--expected-version') + 1], '4')
                raise ADAPTER.AdapterError('version-conflict')

            with mock.patch.object(ADAPTER, 'cli', return_value=info), \
                    mock.patch.object(ADAPTER, 'decision_cli', side_effect=lose_at_dispatch) as dispatch:
                with self.assertRaisesRegex(ADAPTER.AdapterError, 'version-conflict'):
                    self.invoke(['backlog', '--session', str(session), '--id', 'S-4',
                                 '--expected-version', '4', '--decision', shown['confirmation']])
            self.assertEqual(dispatch.call_count, 1)

    def test_explicit_new_version_decision_overrides_stale_presentation(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp, 'S-4')
            issue = {'id': 'S-4', 'version': 4, 'state': 'ready', 'report': 'Raport v4'}
            info = {'ok': True, 'apiVersion': 1, 'capabilities': ['issue.create', 'issue.show',
                'issue.update', 'issue.attach', 'issue.submit', 'issue.accept']}
            with mock.patch.object(ADAPTER, 'cli', side_effect=[info, {'ok': True, 'issue': issue}]):
                self.invoke(['inspect', '--session', str(session), '--id', 'S-4', '--destination', 'backlog'])

            def accept_v6(*argv):
                operation = json.loads((session / 'session.json').read_text())['decisionOperation']
                self.assertEqual(operation['expectedVersion'], 6)
                self.assertEqual(argv[argv.index('--expected-version') + 1], '6')
                return {'ok': True, 'accepted': True, 'card': 'K-6', 'acceptedVersion': 6}

            with mock.patch.object(ADAPTER, 'cli', return_value=info), \
                    mock.patch.object(ADAPTER, 'decision_cli', side_effect=accept_v6):
                accepted = self.invoke(['backlog', '--session', str(session), '--id', 'S-4',
                                        '--expected-version', '6', '--decision', 'backlog S-4 v6'])
            self.assertTrue(accepted['accepted'])

    def test_go_lost_answer_replays_same_request_launch_and_retry_has_new_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp, 'S-8')
            launch = Path(tmp, 'launch.json')
            launch.write_text('{"executor":"gęś ; $(dane)"}', encoding='utf-8')
            info = {'ok': True, 'apiVersion': 1, 'capabilities': ['issue.create', 'issue.show',
                'issue.update', 'issue.attach', 'issue.submit', 'issue.go', 'issue.retry-start']}
            issue = {'id': 'S-8', 'version': 4, 'state': 'ready', 'title': 'Go',
                     'project': 'plasma-tide', 'report': 'Raport', 'attachments': []}
            with mock.patch.object(ADAPTER, 'cli', side_effect=[info, {'ok': True, 'issue': issue}]):
                shown = self.invoke(['inspect', '--session', str(session), '--id', 'S-8', '--destination', 'go',
                                     '--launch', str(launch)])
            calls = []

            def lose_once(*argv):
                calls.append(argv)
                stored = json.loads((session / 'session.json').read_text())['decisionOperation']
                self.assertEqual(stored['expectedVersion'], 4)
                self.assertEqual(stored['launch'], json.loads(launch.read_text(encoding='utf-8')))
                if len(calls) == 1:
                    raise ADAPTER.AdapterError('utracona odpowiedź')
                return {'ok': True, 'accepted': True, 'card': 'K-2',
                        'start': {'state': 'start-error'}, 'currentStart': {'state': 'started'}}

            with mock.patch.object(ADAPTER, 'cli', return_value=info), \
                    mock.patch.object(ADAPTER, 'decision_cli', side_effect=lose_once):
                with self.assertRaisesRegex(ADAPTER.AdapterError, 'utracona odpowiedź'):
                    self.invoke(['go', '--session', str(session), '--id', 'S-8', '--expected-version', '4',
                                 '--decision', shown['confirmation'], '--launch', str(launch)])
                first_identity = json.loads((session / 'session.json').read_text())['decisionOperation']
                replay = self.invoke(['replay', '--session', str(session)])
            self.assertTrue(replay['accepted'])
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(json.loads((session / 'session.json').read_text())['decisionOperation'], first_identity)
            self.assertEqual(replay['start']['state'], 'start-error')
            self.assertEqual(replay['currentStart']['state'], 'started')

            issue['start'] = {'intentId': 'intent-1', 'startVersion': 7, 'state': 'start-error',
                              'retryable': True, 'error': {'message': 'błąd'}}
            with mock.patch.object(ADAPTER, 'cli', side_effect=[info,
                    {'ok': True, 'issue': issue, 'start': issue['start']}]) as cli:
                current = self.invoke(['retry-inspect', '--session', str(session), '--id', 'S-8'])
            self.assertEqual(current['currentStart']['startVersion'], 7)
            retry_calls = []

            def retry_lost(*argv):
                retry_calls.append(argv)
                saved = json.loads((session / 'session.json').read_text())['retryOperation']
                self.assertEqual((saved['intent'], saved['expectedStartVersion']), ('intent-1', 7))
                self.assertNotEqual(saved['requestId'], first_identity['requestId'])
                if len(retry_calls) == 1:
                    raise ADAPTER.AdapterError('utracona odpowiedź retry')
                return {'ok': True, 'accepted': True, 'card': 'K-2',
                        'start': {'state': 'started'}, 'currentStart': {'state': 'completed'}}

            decision = 'ponów start S-8 intent intent-1 v7'
            with mock.patch.object(ADAPTER, 'cli', return_value=info), \
                    mock.patch.object(ADAPTER, 'decision_cli', side_effect=retry_lost):
                with self.assertRaisesRegex(ADAPTER.AdapterError, 'utracona odpowiedź retry'):
                    self.invoke(['retry-start', '--session', str(session), '--id', 'S-8', '--intent', 'intent-1',
                                 '--expected-start-version', '7', '--decision', decision])
                retry_result = self.invoke(['retry-replay', '--session', str(session)])
            self.assertEqual(retry_calls[0], retry_calls[1])
            self.assertEqual(retry_result['currentStart']['state'], 'completed')

            original_go = {'ok': True, 'accepted': True, 'card': 'K-2', 'startError': {'code': 'failed'},
                           'start': {'state': 'start-error'}, 'currentStart': {'state': 'completed'}}
            with mock.patch.object(ADAPTER, 'cli', return_value=info), \
                    mock.patch.object(ADAPTER, 'decision_cli', return_value=original_go) as original_replay:
                history = self.invoke(['replay', '--session', str(session)])
            self.assertTrue(history['startError'])
            self.assertEqual(history['currentStart']['state'], 'completed')
            self.assertEqual(original_replay.call_args.args[original_replay.call_args.args.index('--request-id') + 1],
                             first_identity['requestId'])


class EvaluatorTest(unittest.TestCase):
    """Wybór programu i sprzątanie poświadczeń; bez uruchamiania jakiegokolwiek modelu."""

    def test_long_output_directory_does_not_exceed_unix_socket_name_limit(self):
        from xml.etree import ElementTree
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / ('dlugi-worktree-' * 12) / 'runtime'
            directory.mkdir(parents=True)
            config = directory / 'bus.conf'
            EVALUATOR.write_bus_config(config)
            address = ElementTree.fromstring(config.read_text()).findtext('listen')
            if address.startswith('unix:tmpdir='):
                socket_name = address.removeprefix('unix:tmpdir=') + '/dbus-XXXXXXXXXX'
            else:
                self.assertTrue(address.startswith('unix:abstract='))
                socket_name = '\x00' + address.removeprefix('unix:abstract=')
            self.assertLess(len(os.fsencode(socket_name)), 108, 'Socket name too long')
            first = address
            EVALUATOR.write_bus_config(config)
            root = ElementTree.fromstring(config.read_text())
            self.assertNotEqual(first, root.findtext('listen'))
            self.assertIsNotNone(root.find('./policy/deny[@user="*"]'))
            self.assertIsNotNone(root.find('./policy/allow[@user="' + str(os.getuid()) + '"]'))

    def test_codex_only_uses_explicit_binary_without_searching_path_for_claude(self):
        args = SimpleNamespace(skip_claude=True, skip_codex=False, codex_bin=Path(sys.executable))
        with mock.patch.object(EVALUATOR.shutil, 'which', side_effect=AssertionError('Nie szukaj w PATH')):
            self.assertEqual(EVALUATOR.selected_programs(args), {'codex': str(Path(sys.executable).absolute())})
        args.codex_bin = Path('/nonexistent/explicit-codex')
        with mock.patch.object(EVALUATOR.shutil, 'which', return_value='/snap/bin/codex') as search:
            with self.assertRaisesRegex(RuntimeError, 'explicit-codex'):
                EVALUATOR.selected_programs(args)
            search.assert_not_called()

    def test_credentials_for_selected_program_are_private_and_removed_on_exit(self):
        for failure in (None, RuntimeError, SystemExit, KeyboardInterrupt):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original_home = root / 'original'
                original_codex = original_home / '.codex'
                original_codex.mkdir(parents=True)
                (original_codex / 'auth.json').write_text('testowe poświadczenie')
                (original_home / '.claude').mkdir()
                (original_home / '.claude/.credentials.json').write_text('niewybrany program')
                env = {'CODEX_HOME': str(root / 'runtime/codex'),
                       'CLAUDE_CONFIG_DIR': str(root / 'runtime/claude')}
                for name in env.values():
                    Path(name).mkdir(parents=True)
                destination = Path(env['CODEX_HOME']) / 'auth.json'
                try:
                    with EVALUATOR.credentials(original_home, original_codex, env, {'codex': '/explicit/codex'}):
                        self.assertEqual(destination.read_text(), 'testowe poświadczenie')
                        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                        self.assertFalse(Path(env['CLAUDE_CONFIG_DIR'], '.credentials.json').exists())
                        if failure:
                            raise failure()
                except (RuntimeError, SystemExit, KeyboardInterrupt):
                    if failure is None:
                        raise
                self.assertFalse(destination.exists())
                self.assertTrue((original_codex / 'auth.json').is_file())

    def test_partial_credential_copy_is_removed_after_read_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'original'
            source.mkdir()
            (source / 'auth.json').write_text('testowe poświadczenie')
            destination = root / 'runtime'
            destination.mkdir()
            env = {'CODEX_HOME': str(destination), 'CLAUDE_CONFIG_DIR': str(root / 'no-claude')}
            with mock.patch.object(Path, 'read_bytes', side_effect=OSError('kontrolowany błąd odczytu')):
                with self.assertRaises(OSError):
                    with EVALUATOR.credentials(root, source, env, {'codex': '/explicit/codex'}):
                        self.fail('Nie wolno kontynuować po błędzie kopiowania')
            self.assertFalse((destination / 'auth.json').exists())


@requires_dbus
class EvaluatorBusTest(unittest.TestCase):
    def test_generated_policy_allows_real_client_to_receive_bus_replies(self):
        """Rzeczywisty D-Bus na konfiguracji runnera; bez modeli i bez magistrali pulpitu."""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, 'bus.conf')
            EVALUATOR.write_bus_config(config)
            with Path(tmp, 'bus.stderr').open('w+') as errors:
                process = subprocess.Popen(['dbus-daemon', '--config-file=' + str(config),
                                            '--nofork', '--nopidfile', '--print-address=1'],
                                           stdout=subprocess.PIPE, stderr=errors, text=True)
                try:
                    address = process.stdout.readline().strip()
                    errors.seek(0)
                    self.assertTrue(address, errors.read())
                    probe = '''
import sys
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
app = QCoreApplication([])
bus = QDBusConnection.connectToBus(sys.argv[1], 'issue-eval-probe')
assert bus.isConnected(), bus.lastError().message()
message = QDBusMessage.createMethodCall('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                      'org.freedesktop.DBus', 'GetId')
reply = bus.call(message, QDBus.CallMode.Block, 1500)
assert reply.type() == QDBusMessage.MessageType.ReplyMessage, reply.errorMessage()
assert len(reply.arguments()) == 1 and reply.arguments()[0], reply.arguments()
QDBusConnection.disconnectFromBus('issue-eval-probe')
print('received-bus-reply')
'''
                    client = subprocess.run([sys.executable, '-c', probe, address],
                                            capture_output=True, text=True, timeout=8)
                    self.assertEqual(client.returncode, 0, client.stdout + client.stderr)
                    self.assertEqual(client.stdout.strip(), 'received-bus-reply')
                    errors.flush()
                    errors.seek(0)
                    self.assertNotIn('Rejected receive message', errors.read())
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    process.stdout.close()


class PackageTest(unittest.TestCase):
    def test_evaluator_and_fixture_are_valid_without_running_models(self):
        import ast
        import struct
        for name in ('evaluate.py', 'eval/fixture.py', 'shared/issue_adapter.py', 'install.py'):
            ast.parse((PACKAGE / name).read_text())
        image = FIXTURE.png()
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('!II', image[16:24]), (520, 260))
        self.assertEqual(image, FIXTURE.png())
        self.assertEqual((PACKAGE / 'eval/filtr.png').read_bytes(), image)
        from PyQt6.QtGui import QImage
        decoded = QImage.fromData(image)
        self.assertFalse(decoded.isNull())
        artifact = REPO / 'build/test-results/pt-224/filtr.png'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(image)
        rubric = json.loads((PACKAGE / 'eval/rubric.json').read_text())
        self.assertEqual([row['id'] for row in rubric], list(range(1, 8)))
        conversation = (PACKAGE / 'eval/conversation.txt').read_text()
        self.assertIn('bez restartu zmiana działa', conversation)
        self.assertIn('odrzucam ten wariant', conversation)
        self.assertIn('bez pliku', conversation)

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
                self.assertEqual((adapter / 'references/decisions.md').read_bytes(),
                                 (PACKAGE / 'shared/decisions.md').read_bytes())
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
            self.assertTrue((codex.parent / 'tide-backlog/SKILL.md').is_file())
            self.assertTrue((codex.parent / 'tide-go/SKILL.md').is_file())
            self.assertTrue((claude / 'commands/backlog.md').is_file())
            self.assertTrue((claude / 'commands/go.md').is_file())
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
            self.assertTrue(Path(env['XDG_DATA_HOME'], 'tide/agents/codex/tide-issue/SKILL.md').exists())
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            conflict = Path(env['HOME'], '.codex/skills/tide-issue/SKILL.md')
            conflict.parent.mkdir(parents=True)
            conflict.write_text('obce')
            self.install(env, code=1)
            self.assertEqual(conflict.read_text(), 'obce')
            self.assertFalse(Path(env['XDG_DATA_HOME'], 'tide/agents/manifest.json').exists())

    def test_each_program_can_be_missing_and_later_added(self):
        for absent in ('claude', 'codex'):
            with self.subTest(absent=absent), tempfile.TemporaryDirectory() as tmp:
                env = make_env(tmp)
                binary = Path(tmp, 'fakebin', absent)
                content = binary.read_bytes()
                binary.unlink()
                self.install(env)
                root = Path(env['XDG_DATA_HOME'], 'tide/agents')
                self.assertTrue((root / 'claude/tide/commands/issue.md').exists())
                self.assertTrue((root / 'codex/tide-issue/SKILL.md').exists())
                discoverable = Path(env['HOME'], '.codex/skills/tide-issue/SKILL.md')
                self.assertEqual(discoverable.exists(), absent != 'codex')
                binary.write_bytes(content)
                binary.chmod(0o755)
                self.install(env)
                self.assertTrue(discoverable.exists())

    def test_update_backs_up_previous_version_and_keeps_foreign_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            self.install(env)
            owned = Path(env['HOME'], '.codex/skills/tide-issue/SKILL.md')
            old = owned.read_bytes()
            foreign = owned.parent / 'osobisty.md'
            foreign.write_text('zachowaj')
            package_copy = Path(tmp, 'package')
            shutil.copytree(PACKAGE, package_copy)
            (package_copy / 'codex/SKILL.md').write_bytes(old + b'\nDodatkowa instrukcja testowa.\n')
            result = subprocess.run([sys.executable, str(package_copy / 'install.py')], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(owned.read_bytes(), old)
            self.assertEqual(foreign.read_text(), 'zachowaj')
            backups = Path(env['XDG_DATA_HOME'], 'tide/agents/backups')
            self.assertTrue(any(p.read_bytes() == old for p in backups.rglob('*') if p.is_file()))

    def test_symlink_does_not_write_outside_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            destination = Path(env['HOME'], '.codex/skills/tide-issue')
            destination.parent.mkdir(parents=True)
            outside = Path(tmp, 'outside')
            outside.mkdir()
            destination.symlink_to(outside, target_is_directory=True)
            self.install(env, code=1)
            self.assertEqual(list(outside.iterdir()), [])


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

    def ready_issue(self, title='Decyzja backlog/go'):
        issue = self.run_cli('issue', 'create', '--title', title, '--project', 'alfa',
                             '--report', self.report)['issue']
        return self.run_cli('issue', 'submit', issue['id'], '--expected-version',
                            str(issue['version']))['issue']

    def ready_issue_v4(self):
        issue = self.run_cli('issue', 'create', '--title', 'Raport przeczytany v4', '--project', 'alfa',
                             '--report', self.report)['issue']
        for index in range(2):
            source = Path(self.tmp, f'dowod-{index}.txt')
            source.write_text(f'dowód {index}', encoding='utf-8')
            issue = self.run_cli('issue', 'attach', issue['id'], '--file', str(source),
                                 '--expected-version', str(issue['version']))['issue']
        return self.run_cli('issue', 'submit', issue['id'], '--expected-version',
                            str(issue['version']))['issue']

    def test_both_adapters_keep_presented_version_across_external_change(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            with self.subTest(adapter=script):
                ready = self.ready_issue_v4()
                self.assertEqual(ready['version'], 4)
                folder = Path(self.tmp, 'backlog-decision-' + str(n))
                shown = self.adapter(script, 'inspect', '--session', folder, '--id', ready['id'],
                                     '--destination', 'backlog')
                self.assertTrue(shown['presented'])
                self.assertEqual(shown['issue']['version'], 4)
                self.assertIn(self.report.read_text(), shown['issue']['report'])
                self.assertEqual(self.run_cli('issue', 'show', ready['id'])['issue']['state'], 'ready')

                changed = self.run_cli('issue', 'update', ready['id'], '--expected-version', '4',
                                       '--title', 'Zmieniono w innej sesji')['issue']
                current = self.run_cli('issue', 'submit', ready['id'], '--expected-version',
                                       str(changed['version']))['issue']
                self.assertEqual(current['version'], 6)
                result = self.adapter(script, 'backlog', '--session', folder, '--id', ready['id'],
                                      '--expected-version', '4', '--decision',
                                      'Potwierdzam backlog ' + ready['id'] + ' v4', code=1)
                self.assertIn('version-conflict', result['error'])
                saved = json.loads((folder / 'session.json').read_text())['decisionOperation']
                self.assertEqual((saved['expectedVersion'], saved['verb'], saved['id']), (4, 'backlog', ready['id']))
                self.assertTrue(saved['requestId'])
                self.assertEqual(self.run_cli('issue', 'show', ready['id'])['issue']['state'], 'ready')
                calls = [call for call in self.calls() if call[:2] == ['backlog', ready['id']]]
                self.assertEqual(len(calls), 1)
                self.assertIn(['--expected-version', '4'], [calls[0][i:i + 2] for i in range(len(calls[0]) - 1)])

                # Świeży odczyt pokazuje wersję bieżącą, ale nie zatwierdza jej sam.
                latest = self.adapter(script, 'inspect', '--session', folder, '--id', ready['id'],
                                      '--destination', 'backlog')
                self.assertEqual(latest['issue']['version'], 6)
                self.assertTrue(latest['confirmation'].endswith('v6'))
                accepted = self.adapter(script, 'backlog', '--session', folder, '--id', ready['id'],
                                        '--expected-version', '6', '--decision', latest['confirmation'])
                self.assertTrue(accepted['accepted'])
                self.assertRegex(accepted['card'], r'^K-[1-9][0-9]*$')

    def test_both_adapters_go_replays_identical_launch_and_request_after_lost_reply(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            with self.subTest(adapter=script):
                ready = self.ready_issue('Go: zażółć / argument ; dane')
                folder = Path(self.tmp, 'go-decision-' + str(n))
                launch = Path(self.tmp, 'launch-' + str(n) + '.json')
                launch.write_text('{"planning":"light","provider":"gęś ; echo nope"}', encoding='utf-8')
                shown = self.adapter(script, 'inspect', '--session', folder, '--id', ready['id'],
                                     '--destination', 'go', '--launch', launch)
                self.assertEqual(shown['launch'], json.loads(launch.read_text(encoding='utf-8')))
                flag = Path(self.tmp, 'lose-go-' + str(n))
                flag.touch()
                self.env['ISSUE_LOSE_REPLY'] = str(flag)
                self.env['ISSUE_LOSE_REPLY_COMMAND'] = 'go'
                lost = self.adapter(script, 'go', '--session', folder, '--id', ready['id'],
                                    '--expected-version', str(ready['version']), '--decision', shown['confirmation'],
                                    '--launch', launch, code=1)
                self.assertIn('utracona odpowiedź', lost['error'])
                saved = json.loads((folder / 'session.json').read_text())['decisionOperation']
                self.assertEqual(saved['expectedVersion'], ready['version'])
                self.assertEqual(saved['launch'], json.loads(launch.read_text(encoding='utf-8')))
                request_id = saved['requestId']
                replay = self.adapter(script, 'replay', '--session', folder)
                self.assertTrue(replay['accepted'])
                self.assertRegex(replay['card'], r'^K-[1-9][0-9]*$')
                self.assertIn('currentStart', replay)
                calls = [call for call in self.calls() if call[:2] == ['go', ready['id']]]
                self.assertEqual(len(calls), 2)
                for call in calls:
                    self.assertEqual(call[call.index('--request-id') + 1], request_id)
                    self.assertEqual(json.loads(Path(call[call.index('--launch') + 1]).read_text(encoding='utf-8')),
                                     saved['launch'])
                self.assertEqual(self.run_cli('issue', 'show', ready['id'])['issue']['state'], 'accepted')
                self.env.pop('ISSUE_LOSE_REPLY', None)
                self.env.pop('ISSUE_LOSE_REPLY_COMMAND', None)

    def test_both_adapters_missing_then_separate_waiver_and_manual_submit(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            with self.subTest(adapter=script):
                folder = Path(self.tmp, 'session-' + str(n))
                initial = self.begin(script, folder)
                source = Path(self.tmp, 'opis.txt')
                source.write_bytes((PACKAGE / 'eval/opis.txt').read_bytes())
                image = Path(self.tmp, 'filtr.png')
                image.write_bytes(FIXTURE.png())
                draft = self.finish(script, folder, '--file', source, '--file', image, '--missing', 'drugi obraz')
                issue = self.run_cli('issue', 'show', draft['id'])['issue']
                self.assertEqual(issue['state'], 'draft')
                self.assertEqual(len(issue['attachments']), 2)
                for entry in issue['attachments']:
                    copied = Path(self.tmp, entry['id'])
                    self.run_cli('issue', 'attachment', issue['id'], '--attachment', entry['id'], '--output', str(copied))
                    self.assertEqual(copied.read_bytes(), Path(self.tmp, entry['name']).read_bytes())
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
                self.assertNotIn('Pozostaje draft.', final['report'])
                self.assertNotIn('Brak dostępu do bajtów dowodów:', final['report'])
                self.assertEqual(final['attachments'], issue['attachments'])
                self.assertTrue(any(c[:3] == ['issue', 'open', ready['id']] for c in self.calls()))
                # Ponowienie identycznego raportu zachowuje gotową wersję bez drugiego submit.
                submitted = [c for c in self.calls() if c[:3] == ['issue', 'submit', ready['id']]]
                corrected = self.finish(script, folder)
                self.assertEqual((corrected['id'], corrected['state'], corrected['version']),
                                 (ready['id'], 'ready', ready['version']))
                self.assertEqual([c for c in self.calls() if c[:3] == ['issue', 'submit', ready['id']]], submitted)
                retained = self.run_cli('issue', 'show', corrected['id'])['issue']
                self.assertIn('rezygnuję z drugiego obrazu', retained['report'])
                self.assertNotIn('Pozostaje draft.', retained['report'])
                self.assertEqual(retained['attachments'], issue['attachments'])
        # Daemon nie ma ukrytej listy brakujących dowodów wynikającej z prozy.
        fresh = self.run_cli('issue', 'create', '--title', 'Brak obrazu', '--report', self.report)['issue']
        self.assertEqual(self.run_cli('issue', 'submit', fresh['id'], '--expected-version', '1')['issue']['state'], 'ready')

    def test_both_adapters_partial_waiver_keeps_only_remaining_blocker(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            with self.subTest(adapter=script):
                folder = Path(self.tmp, 'partial-waiver-' + str(n))
                initial = self.begin(script, folder)
                self.finish(script, folder, '--missing', 'drugi obraz', '--missing', 'trzeci obraz')
                partial = self.adapter(script, 'waive', '--session', folder, '--evidence', 'drugi obraz',
                                       '--decision', 'rezygnuję z drugiego obrazu')
                self.assertEqual((partial['id'], partial['state']), (initial['id'], 'draft'))
                report = self.run_cli('issue', 'show', initial['id'])['issue']['report']
                self.assertIn('Brak dostępu do bajtów dowodów: trzeci obraz. Pozostaje draft.', report)
                self.assertNotIn('Brak dostępu do bajtów dowodów: drugi obraz', report)
                self.assertIn('rezygnuję z drugiego obrazu', report)
                self.assertFalse(any(c[:3] == ['issue', 'submit', initial['id']] for c in self.calls()))
                final = self.adapter(script, 'waive', '--session', folder, '--evidence', 'trzeci obraz',
                                     '--decision', 'rezygnuję z trzeciego obrazu')
                self.assertEqual((final['id'], final['state']), (initial['id'], 'ready'))
                report = self.run_cli('issue', 'show', initial['id'])['issue']['report']
                self.assertNotIn('Pozostaje draft.', report)
                for decision in ('rezygnuję z drugiego obrazu', 'rezygnuję z trzeciego obrazu'):
                    self.assertIn(decision, report)
                self.assertIn('pliku nie dołączono', report)

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
        scripts = [Path(self.env['HOME'], '.codex/skills/tide-issue/scripts/issue_adapter.py'),
                   Path(self.env['XDG_DATA_HOME'], 'tide/agents/claude/tide/scripts/issue_adapter.py')]
        for n, script in enumerate(scripts):
            folder = Path(self.tmp, 'unavailable-' + str(n))
            self.begin(script, folder, code=1)
            self.assertFalse(folder.exists())

    def test_both_adapters_resume_updates_version_and_resubmits_existing_issue(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            issue = self.run_cli('issue', 'create', '--title', 'Przed poprawką', '--project', 'alfa',
                                 '--report', self.report)['issue']
            ready = self.run_cli('issue', 'submit', issue['id'], '--expected-version', str(issue['version']))['issue']
            folder = Path(self.tmp, 'resume-' + str(n))
            resumed = self.adapter(script, 'resume', '--session', folder, '--id', ready['id'])
            self.assertEqual(resumed['version'], ready['version'])
            final = self.finish(script, folder)
            self.assertEqual((final['id'], final['state']), (ready['id'], 'ready'))
            self.assertGreater(final['version'], ready['version'])
            self.assertTrue(any(c[:3] == ['issue', 'submit', ready['id']] for c in self.calls()))
            stored = self.run_cli('issue', 'show', final['id'])['issue']
            self.assertEqual(stored['title'], 'Filtr projektu')
            self.assertEqual(stored['project'], 'alfa')
            self.assertEqual(stored['created'], issue['created'])

    def test_both_adapters_frontmatter_and_unknown_project_cannot_create(self):
        self.prepare()
        for n, script in enumerate(self.adapters):
            Path(self.report).write_text('---\nid: S-99\n---\nChronione metadane')
            self.begin(script, Path(self.tmp, 'frontmatter-' + str(n)), code=1)
            Path(self.report).write_text('Raport')
            self.adapter(script, 'begin', '--session', Path(self.tmp, 'project-' + str(n)),
                         '--title', 'Jawny tytuł', '--project', 'nieznany', '--report', self.report, code=1)
        self.assertEqual(self.run_cli('issue', 'list')['issues'], [])

    def test_both_adapters_open_failure_keeps_identifiable_draft(self):
        self.prepare()
        Path(self.env['XDG_DATA_HOME'], 'tide/tide-window/tide.py').unlink()
        for n, script in enumerate(self.adapters):
            folder = Path(self.tmp, 'open-failed-' + str(n))
            reply = self.begin(script, folder, code=1)
            state = json.loads((folder / 'session.json').read_text())
            self.assertIn(state['id'], reply['error'])
            self.assertEqual(self.run_cli('issue', 'show', state['id'])['issue']['state'], 'draft')
            self.assertFalse(any(c[:3] == ['issue', 'submit', state['id']] for c in self.calls()))
