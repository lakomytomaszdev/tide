"""PT-227: wybór próby i adaptery card; zestawy issues oraz daemon."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from . import test_cardflow_enter_unit as enterunit
from . import test_issues_card_cli as cardcli
from . import test_cardflow_dbus as flow
from .privatebus import wait_until
from tide_daemon.cardflow import state, ticketentry

cli = cardcli.cli
REPO = cardcli.REPO
PACKAGE = REPO / 'cli/agents'
XML = cardcli.XML.replace('TalkToCaretaker', 'EnterStep')
spec = importlib.util.spec_from_file_location('card_adapter', PACKAGE / 'shared/card_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class TicketEntryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = enterunit.EnterStepTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine
        self.step = self.fixture.step_id

    def view(self):
        return ticketentry.view(state.fold(self.engine.journal.read('K-1')))

    def request(self, ticket='PT-1', step=None):
        return json.dumps({'ticket': ticket, 'step': step or self.step, 'revision': self.view()['revision']})

    def append(self, event_type, **data):
        return self.engine.journal.append('K-1', event_type, **data)

    def start(self, name, ticket='PT-1', **extra):
        return self.append('step.started', step=name, ticket=ticket, kind='review',
                           worktree=extra.pop('worktree', self.fixture.worktree + '-review'), **extra)

    def test_guarded_entry_uses_existing_stop_and_resume(self):
        request = self.request()
        with mock.patch.object(self.engine, '_stop_for_enter', side_effect=lambda *_: self.fixture.order.append('stop') or True):
            reply = self.engine.enter('K-1', request)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.fixture.order, ['stop', 'open'])
        self.assertEqual(self.fixture.terminal.plan.argv[:3], ['/bin/claude', '--resume', self.fixture.SID])

    def test_new_attempt_order_base_and_finish_refuse_before_stopping(self):
        changes = [lambda: self.start('new'),
                   lambda: self.append('card.started', executor='Sonnet', program='claude'),
                   lambda: self.append('step.finished', step=self.step, result='question', question='Pytanie'),
                   lambda: self.append('card.paused', paused=True)]
        for change in changes:
            request = self.request()
            change()
            with mock.patch.object(self.engine, '_stop_for_enter') as stop:
                reply = self.engine.enter('K-1', request)
            self.assertEqual(reply['error'], 'stale-context', reply)
            self.assertIn('attempts', reply)
            self.assertEqual(self.fixture.order, [])
            stop.assert_not_called()

    def test_old_base_and_old_order_cannot_be_selected_with_fresh_revision(self):
        self.append('card.started', executor='Sonnet', program='claude', baseVersion=2)
        self.start('wrong-base', baseVersion=1)
        with mock.patch.object(self.engine, '_enter_step') as enter:
            reply = self.engine.enter('K-1', self.request(step='wrong-base'))
        self.assertEqual(reply['error'], 'no-step')
        self.assertFalse(any(a['available'] for a in reply['attempts']))
        enter.assert_not_called()

    def test_finished_predecessor_is_replaced_by_next_role_not_log_date(self):
        self.append('step.finished', step=self.step, result='done')
        self.start('review')
        self.append('step.activity', step=self.step, ts='2099-12-31T00:00:00Z')
        choices = {a['id']: a for a in self.view()['attempts']}
        self.assertFalse(choices[self.step]['currentAttempt'])
        self.assertTrue(choices['review']['available'])
        with mock.patch.object(self.engine, '_enter_step', return_value={'ok': True, 'step': 'review'}) as enter:
            self.assertTrue(self.engine.enter('K-1', self.request(step='review'))['ok'])
        enter.assert_called_once_with('K-1', 'review')

    def test_two_available_attempts_and_foreign_ticket_are_refused(self):
        self.start('parallel')
        for request in (self.request(), self.request(ticket='PT-2')):
            with mock.patch.object(self.engine, '_enter_step') as enter:
                reply = self.engine.enter('K-1', request)
            self.assertEqual(reply['error'], 'no-step')
            self.assertEqual(len(reply['attempts']), 2)
            enter.assert_not_called()

    def test_busy_worktree_and_known_enter_refusals_keep_their_codes(self):
        for error in ('no-session', 'no-session-source', 'not-stopped', 'no-terminal',
                      'interactive-evidence-unsafe', 'broken-evidence', 'no-worktree', 'executor-unavailable'):
            with mock.patch.object(self.engine, '_enter_step', side_effect=enterunit.FlowError(error, 'dowód')):
                reply = self.engine.enter('K-1', self.request())
            self.assertEqual((reply['error'], reply['message']), (error, 'dowód'))
            self.assertIn('attempts', reply)
        self.start('busy', ticket='PT-2', worktree=self.fixture.worktree)
        with mock.patch.object(self.engine, '_stop_for_enter') as stop:
            reply = self.engine.enter('K-1', self.request())
        self.assertEqual(reply['error'], 'worktree-busy')
        stop.assert_not_called()

    def test_activity_does_not_change_revision_but_base_version_does(self):
        before = self.view()['revision']
        self.append('step.activity', step=self.step, ts='2099-01-01T00:00:00Z')
        self.assertEqual(self.view()['revision'], before)
        st = state.fold(self.engine.journal.read('K-1'))
        st.base_version += 1
        self.assertNotEqual(ticketentry.view(st)['revision'], before)

    def outside(self, step):
        self.append('step.finished', step=step, result='denied', outside=True, command='make test')
        self.append('step.decision', step=step, choice='run')
        self.append('outside.started', step=step, unit='tide-outside-' + step)

    def assert_outside_busy(self, outside_step):
        projected = self.view()
        attempt = next(a for a in projected['attempts'] if a['id'] == self.step)
        self.assertFalse(attempt['available'], attempt)
        self.assertEqual(attempt['error'], 'worktree-busy')
        with mock.patch.object(self.engine, '_stop_for_enter') as stop:
            reply = self.engine.enter('K-1', self.request())
        self.assertEqual(reply['error'], 'worktree-busy', reply)
        self.assertEqual(reply['attempts'], projected['attempts'])
        self.assertEqual(self.fixture.order, [])
        self.assertIn(outside_step, state.fold(self.engine.journal.read('K-1')).outside_started)
        stop.assert_not_called()

    def test_fresh_selection_during_own_outside_refuses_then_allows_after_finish(self):
        self.outside(self.step)
        self.assertEqual(state.fold(self.engine.journal.read('K-1')).running(), [])
        self.assert_outside_busy(self.step)
        before = self.view()['revision']
        self.append('outside.finished', step=self.step, exitCode=0)
        self.assertNotEqual(self.view()['revision'], before)
        self.assertTrue(self.view()['attempts'][0]['available'])
        with mock.patch.object(self.engine, '_stop_for_enter') as stop:
            reply = self.engine.enter('K-1', self.request())
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.fixture.order, ['open'])
        task = json.loads(Path(self.fixture.terminal.plan.task_file).read_text())
        self.assertEqual(task['workdir'], self.fixture.worktree)
        self.assertEqual(self.fixture.terminal.plan.argv[:3], ['/bin/claude', '--resume', self.fixture.SID])
        stop.assert_not_called()

    def test_outside_in_same_worktree_blocks_other_ticket_but_not_separate_worktree(self):
        self.start('outside-other', ticket='PT-2', worktree=self.fixture.worktree)
        self.outside('outside-other')
        self.assert_outside_busy('outside-other')
        self.append('outside.finished', step='outside-other', exitCode=0)
        self.start('outside-separate', ticket='PT-3')
        self.outside('outside-separate')
        attempt = next(a for a in self.view()['attempts'] if a['id'] == self.step)
        self.assertTrue(attempt['available'], attempt)
        with mock.patch.object(self.engine, '_stop_for_enter', return_value=True):
            reply = self.engine.enter('K-1', self.request())
        self.assertTrue(reply['ok'], reply)

    def test_outside_started_after_selection_invalidates_request_then_fresh_selection_is_busy(self):
        self.append('step.finished', step=self.step, result='denied', outside=True, command='make test')
        self.append('step.decision', step=self.step, choice='run')
        request = self.request()
        self.append('outside.started', step=self.step, unit='tide-outside-' + self.step)
        with mock.patch.object(self.engine, '_stop_for_enter') as stop:
            reply = self.engine.enter('K-1', request)
        self.assertEqual(reply['error'], 'stale-context', reply)
        self.assertEqual(reply['attempts'], self.view()['attempts'])
        stop.assert_not_called()
        self.assert_outside_busy(self.step)

    def test_merged_rejected_and_finished_cards_have_no_available_attempt(self):
        st = state.fold(self.engine.journal.read('K-1'))
        for field, value in (('card_finished', True), ('rejected', {'PT-1'}),
                             ('results', {('merge', 'PT-1'): {'ok': True}})):
            with mock.patch.object(st, field, value):
                self.assertFalse(any(a['available'] for a in ticketentry.view(st)['attempts']))

    def test_public_projection_reuses_steps_and_does_not_expose_paths(self):
        st = state.fold(self.engine.journal.read('K-1'))
        out = state.derived(st, {'id': 'K-1', 'column': 'todo'})
        ticketentry.enrich(st, out)
        self.assertEqual(out['flow']['ticketEntryVersion'], 1)
        self.assertEqual(out['flow']['entryRevision'], self.view()['revision'])
        self.assertEqual(out['flow']['steps'][0]['id'], self.step)
        self.assertTrue(out['flow']['steps'][0]['available'])
        self.assertNotIn(self.fixture.worktree, json.dumps(self.view()))

    def test_malformed_guard_never_reaches_existing_entry(self):
        for raw in ('{', '{}', '{"ticket":1,"step":"x","revision":"r"}',
                    '{"ticket":"PT-1","step":"x","revision":"r","path":"/etc/passwd"}'):
            with mock.patch.object(self.engine, '_enter_step') as enter:
                with self.assertRaises(enterunit.FlowError) as err:
                    self.engine.enter('K-1', raw)
            self.assertEqual(err.exception.code, 'bad-request')
            enter.assert_not_called()


class TicketCliTest(cardcli.CardCliTest):
    # Dotychczasowe testy PT-226 pozostają częścią regresji.
    def snapshot(self, attempts=None):
        if attempts is None:
            attempts = [{'id': 'step', 'ticket': 'PT-1', 'kind': 'ticket', 'attempt': 2,
                         'round': 0, 'currentAttempt': True, 'available': True, 'error': ''}]
        return json.dumps({'cards': [{'id': 'K-1', 'derived': {'flow': {
            'ticketEntryVersion': 1, 'entryRevision': 'revision', 'steps': attempts}}}]})

    def test_ticket_uses_snapshot_and_guarded_enter(self):
        expected = {'ok': True, 'step': 'step'}
        code, reply, request = self.invoke([[XML], [self.snapshot()], [json.dumps(expected)]],
                                           'K-1', '--ticket', 'PT-1')
        self.assertEqual((code, reply), (0, expected))
        self.assertEqual([c.args[2] for c in request.call_args_list], ['Introspect', 'Snapshot', 'EnterStep'])
        self.assertEqual(json.loads(request.call_args.args[-1]),
                         {'ticket': 'PT-1', 'step': 'step', 'revision': 'revision'})

    def test_ambiguity_history_and_busy_refuse_without_enter(self):
        one = json.loads(self.snapshot())['cards'][0]['derived']['flow']['steps'][0]
        for attempts, ticket, error in (([one, {**one, 'id': 'other'}], 'PT-1', 'no-step'),
                                        ([{**one, 'currentAttempt': False, 'available': False}], 'PT-1', 'no-step'),
                                        ([one], 'PT-2', 'no-step'),
                                        ([{**one, 'available': False, 'error': 'worktree-busy'}], 'PT-1', 'worktree-busy')):
            code, reply, request = self.invoke([[XML], [self.snapshot(attempts)]], 'K-1', '--ticket', ticket)
            self.assertNotEqual(code, 0)
            self.assertEqual(reply['error'], error)
            self.assertEqual(reply['attempts'], attempts)
            self.assertEqual(request.call_count, 2)

    def test_state_change_and_enter_refusal_preserve_current_choices(self):
        for error, code in (('stale-context', 4), ('worktree-busy', 6), ('not-stopped', 6),
                            ('no-session', 5), ('already-entered', 4), ('console-open', 4), ('no-terminal', 3)):
            expected = {'ok': False, 'error': error, 'message': 'Odmowa', 'attempts': [], 'detail': 'dowód'}
            actual, reply, _ = self.invoke([[XML], [self.snapshot()], [json.dumps(expected)]],
                                           'K-1', '--ticket', 'PT-1')
            self.assertEqual((actual, reply), (code, expected))

    def test_no_legacy_fallback_or_arbitrary_path_read(self):
        for snapshot in ('{}', '[]', '{"cards":[]}', self.snapshot().replace('ticketEntryVersion', 'other')):
            code, reply, request = self.invoke([[XML], [snapshot]], 'K-1', '--ticket', 'PT-1')
            self.assertNotEqual(code, 0)
            self.assertLessEqual(request.call_count, 2)
        for args in (('K-1', '--ticket', '../PT-1'), ('K-1', '--ticket', ''),
                     ('K-1', '--ticket', 'PT-0'), ('K-1', '--ticket', 'PT-1', '--problem', 'x')):
            code, reply, request = self.invoke([], *args)
            self.assertEqual((code, reply['error']), (2, 'bad-request'))
            request.assert_not_called()

    def test_unstarted_card_returns_no_step(self):
        snapshot = json.dumps({'cards': [{'id': 'K-1', 'derived': None}]})
        code, reply, request = self.invoke([[XML], [snapshot]], 'K-1', '--ticket', 'PT-1')
        self.assertEqual((code, reply['error'], reply['attempts']), (5, 'no-step', []))
        self.assertEqual(request.call_count, 2)


class CardAdapterTest(unittest.TestCase):
    def test_exact_cli_command_and_exit_status_without_shell_or_output_rewrite(self):
        for ticket in (None, 'PT-1'):
            for code in (0, 3, 4, 5, 6):
                args = ['K-1'] + ([ticket] if ticket else [])
                expected = ['tide', 'card', 'open', 'K-1'] + (['--ticket', ticket] if ticket else []) + ['--json']
                with mock.patch.object(adapter.subprocess, 'run', return_value=mock.Mock(returncode=code)) as run:
                    self.assertEqual(adapter.main(args), code)
                run.assert_called_once_with(expected, check=False)

    def test_bad_arguments_never_execute_a_program(self):
        for args in ([], ['K-0'], ['K-1', '--problem', 'x'], ['K-1', '../PT-1'], ['K-1', 'PT-1', 'text']):
            with mock.patch.object(adapter.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(adapter.main(args), 2)
            run.assert_not_called()

    def test_install_both_adapters_collision_backup_and_uninstall(self):
        from .test_issues_adapters import make_env
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            command = [sys.executable, str(PACKAGE / 'install.py')]
            def install(*args, code=0):
                result = subprocess.run([*command, *args], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
            install()
            root = Path(env['XDG_DATA_HOME'], 'tide/agents')
            codex = Path(env['HOME'], '.codex/skills/tide-card')
            claude = root / 'claude/tide'
            self.assertTrue((claude / 'commands/card.md').is_file())
            self.assertEqual((codex / 'SKILL.md').read_bytes(), (PACKAGE / 'tide-card/SKILL.md').read_bytes())
            for target in (codex, claude, root / 'codex/tide-card'):
                self.assertEqual((target / 'references/card.md').read_bytes(), (PACKAGE / 'shared/card.md').read_bytes())
                self.assertEqual((target / 'scripts/card_adapter.py').read_bytes(), (PACKAGE / 'shared/card_adapter.py').read_bytes())
            owned = codex / 'SKILL.md'
            original = owned.read_bytes()
            owned.write_text('lokalna zmiana')
            install(code=1)
            install('--uninstall', code=1)
            self.assertEqual(owned.read_text(), 'lokalna zmiana')
            owned.write_bytes(original)
            install()
            self.assertTrue(list((root / 'backups').iterdir()))
            install('--uninstall')
            self.assertFalse(owned.exists())
            self.assertFalse((claude / 'commands/card.md').exists())


class CardEntryDbusTest(flow.CaretakerChatDbusTest):
    test_talk_opens_console_with_chat_context_and_only_one_session = None

    def setUp(self):
        super().setUp()
        self.cli_env = {**self.env, 'DBUS_SESSION_BUS_ADDRESS': self.service.address, 'PYTHONPATH': ''}
        subprocess.run(['make', '-s', 'install-cli'], cwd=REPO, env=self.cli_env,
                       check=True, capture_output=True, text=True)
        self.cli_path = str(Path(self.env['HOME'], '.local/bin/tide'))
        self.adapters = [Path(self.env['HOME'], '.codex/skills/tide-card/scripts/card_adapter.py'),
                         Path(self.env['HOME'], '.local/share/tide/agents/claude/tide/scripts/card_adapter.py')]

    def invoke(self, script, card, ticket=None, code=0):
        args = [sys.executable, str(script), card] + ([ticket] if ticket else [])
        proc = subprocess.run(args, cwd=self.base, env={**self.cli_env, 'PATH': str(Path(self.cli_path).parent) + ':' + self.cli_env['PATH']},
                              capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def attempt(self, card):
        return self.shown(card)['derived']['flow']['steps'][0]

    def test_both_installed_adapters_open_one_caretaker_and_preserve_refusals(self):
        for script in self.adapters:
            card = self.card(description='Eksport.\n\nATRAPA: blad-opiekun\nATRAPA-POWOD: test pada ATRAPA-OPIEKUN: escalate')
            self.assertTrue(self.start(card)['ok'])
            self.assertTrue(wait_until(lambda: self.of(card, 'caretaker.escalated'), timeout=30))
            self.wait_column(card, 'waiting')
            self.assertTrue(self.invoke(script, card)['ok'])
            self.assertEqual(self.invoke(script, card, code=4)['error'], 'console-open')
            unsupported = self.card()
            self.assertEqual(self.invoke(script, unsupported, code=5)['error'], 'no-step')

    def test_both_installed_adapters_stop_before_resume_and_never_duplicate(self):
        for script in self.adapters:
            card = self.card(description='Dodaj eksport.\n\nATRAPA: czekaj')
            self.assertTrue(self.start(card)['ok'])
            started = self.of(card, 'step.started')[0]
            self.assertTrue(wait_until(lambda: Path(started['stepDir'], 'out/fake-call.json').is_file()))
            pidfile = Path(self.runtime, 'fake-units', started['unit'] + '.pid')
            self.assertTrue(pidfile.is_file())
            pid = int(pidfile.read_text())
            # Konsole zapisuje dowód stanu jednostki dokładnie przed odebraniem zadania.
            konsole = Path(self.base, 'fakebin/konsole')
            original = konsole.read_text()
            observer = Path(self.base, 'observe-console.py')
            observer.write_text('import os,sys\n'
                                f'p={str(Path(self.base, "opening.json"))!r}\n'
                                f'pid={pid}\n'
                                'try:\n'
                                ' s=open(f"/proc/{pid}/stat").read().rpartition(")")[2].split()[0]\n'
                                'except OSError:\n'
                                ' s="gone"\n'
                                'open(p,"w").write(s)\n')
            konsole.write_text(original.replace('echo "konsole', f'/usr/bin/python3 {observer}\necho "konsole', 1))
            reply = self.invoke(script, card, started['ticket'])
            self.assertTrue(reply['ok'], reply)
            self.assertTrue(wait_until(lambda: Path(self.base, 'opening.json').exists()))
            self.assertIn(Path(self.base, 'opening.json').read_text(), ('gone', 'Z'))
            self.assertTrue(wait_until(lambda: any(t.get('workdir') == started['worktree'] for t in self.console_tasks())))
            task = next(t for t in self.console_tasks() if t.get('workdir') == started['worktree'])
            self.assertEqual(task['argv'][1:3], ['--resume', started['session']])
            self.assertEqual(reply['session'], started['session'])
            self.assertEqual(self.invoke(script, card, started['ticket'], code=4)['error'], 'already-entered')
            self.assertEqual(len([t for t in self.console_tasks() if t.get('workdir') == started['worktree']]), 1)
            konsole.write_text(original)

    def test_read_to_enter_race_refuses_without_stopping_unit(self):
        card = self.card(description='Dodaj eksport.\n\nATRAPA: czekaj')
        self.assertTrue(self.start(card)['ok'])
        started = self.of(card, 'step.started')[0]
        self.assertTrue(wait_until(lambda: Path(started['stepDir'], 'out/fake-call.json').exists()))
        shown = self.shown(card)['derived']['flow']
        request = {'ticket': started['ticket'], 'step': started['step'], 'revision': shown['entryRevision']}
        # EnterStep po ID zmienia stan w tej samej kolejce. Stary wybór nie otwiera drugiej karty.
        self.assertTrue(self.call('EnterStep', card, started['step'])['ok'])
        reply = self.call('EnterStep', card, json.dumps(request))
        self.assertEqual(reply['error'], 'stale-context')
        self.assertEqual(reply['attempts'][0]['error'], 'already-entered')
        self.assertEqual(len(self.of(card, 'step.entered')), 1)

    def test_foreign_ticket_is_rechecked_on_public_enter_boundary(self):
        # Nawet świeży odcisk z obcym ticketem nie może wybrać innej jednostki.
        card = self.card(description='Dodaj eksport.\n\nATRAPA: czekaj')
        self.assertTrue(self.start(card)['ok'])
        started = self.of(card, 'step.started')[0]
        revision = self.shown(card)['derived']['flow']['entryRevision']
        reply = self.call('EnterStep', card, json.dumps({'ticket': 'PT-999999', 'step': started['step'], 'revision': revision}))
        self.assertEqual(reply['error'], 'no-step')
        self.assertFalse(self.of(card, 'step.entered'))
        self.assertEqual(self.console_tasks(), [])
        self.assertTrue(self.call('EnterStep', card, started['step'])['ok'])

    def test_new_attempt_between_snapshot_and_enter_is_refused(self):
        binary = Path(self.base, 'fakebin/claude')
        binary.write_text(binary.read_text().replace(
            'if word == "pytanie":\n    if "Odpowiedź użytkownika:" in prompt:\n        finish("done")',
            'if word == "pytanie":\n    if "Odpowiedź użytkownika:" in prompt:\n        time.sleep(3600)'))
        for script in self.adapters:
            card = self.card(description='Dodaj eksport.\n\nATRAPA: pytanie')
            self.assertTrue(self.start(card)['ok'])
            shown = self.wait_column(card, 'waiting')['derived']['flow']
            first = self.of(card, 'step.started')[0]
            request = {'ticket': first['ticket'], 'step': first['step'], 'revision': shown['entryRevision']}
            self.assertTrue(self.call('ResolveCardStep', card, json.dumps(
                {'choice': 'answer', 'step': first['step'], 'answer': 'ISO 8601'}))['ok'])
            self.assertTrue(wait_until(lambda: len(self.of(card, 'step.started')) > 1))
            resumed = self.of(card, 'step.started')[1]
            self.assertTrue(wait_until(lambda: Path(resumed['stepDir'], 'out/fake-call.json').exists()))
            reply = self.call('EnterStep', card, json.dumps(request))
            self.assertEqual(reply['error'], 'stale-context')
            self.assertFalse(next(a for a in reply['attempts'] if a['id'] == first['step'])['available'])
            self.assertFalse(self.of(card, 'step.entered'))
            self.assertFalse([task for task in self.console_tasks() if task.get('workdir') == first['worktree']])
            # Po świadomym świeżym wyborze oba adaptery wybierają nową próbę spośród dwóch.
            reply = self.invoke(script, card, first['ticket'])
            self.assertTrue(reply['ok'], reply)
            self.assertEqual(reply['step'], resumed['step'])
            self.assertEqual(reply['session'], resumed['session'])
