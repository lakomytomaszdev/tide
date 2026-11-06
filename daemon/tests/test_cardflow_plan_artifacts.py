"""PT-247: szkice przechodzą przez migawkę, review, poprawkę i commit gałęzi karty."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit

from tide_daemon.cardflow import planartifacts
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import FlowState, fold, ticket_of
from .test_cardflow import FlowInProcess
from . import test_cardflow_plan_integration as integration
from .test_cardflow_dbus import git


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put('plan.md', '[szkic](followups/nested/draft.md#zakres)\n')
        self.put('tickets/1.md', '- [ ] Działa. [szkic][scope]\n\n[scope]: ../followups/nested/draft.md\n')
        self.put('followups/nested/draft.md', '# Zakres\nPełna treść szkicu.\n')

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_snapshot_keeps_nested_structure_and_is_independent(self):
        board = self.root / 'board'
        journal = Journal(str(board), mirror=str(self.root / 'mirror'))
        engine = mock.Mock(journal=journal, store=mock.Mock(root=str(board)))
        snapshot = FlowEngine._freeze_planning_output(engine, 'K-1', 'step', str(self.root))
        files = planartifacts.read(snapshot)
        self.assertEqual(set(files), {'plan.md', 'tickets/1.md', 'followups/nested/draft.md'})
        self.put('followups/nested/draft.md', 'zmiana autora')
        self.assertEqual(planartifacts.read(snapshot), files)
        text = planartifacts.prepare(snapshot, str(self.root / 'revision'), seed=True)
        self.assertIn('Zamrożony komplet', text)
        self.assertEqual((self.root / 'revision/out/followups/nested/draft.md').read_bytes(), files['followups/nested/draft.md'])

    def test_plan_files_relocate_every_validated_ticket_link_syntax(self):
        for link in ('[szkic](../followups/nested/draft.md#zakres)',
                     '[szkic](<../followups/nested/draft.md#zakres> "Tytuł")',
                     '[szkic][scope]\n\n[scope]: ../followups/nested/draft.md#zakres "Tytuł"',
                     '[szkic][scope]\n\n[scope]: <../followups/nested/draft.md#zakres>',
                     '[szkic]( ./../followups/nested/%64raft.md?view=1#zakres )'):
            with self.subTest(link=link):
                self.put('tickets/1.md', link)
                artifacts = planartifacts.read(self.root)
                row = dict(id='PT-1', title='Szkic', executor='Sonnet', reviewer='Opus',
                           blockers=[], body=artifacts['tickets/1.md'].decode())
                state = FlowState(plan_proposed=dict(artifactPath=str(self.root)),
                                  plan_approved=dict(planFile='docs/agents/plans/k-1.md', rows=[row]))
                engine = FlowEngine.__new__(FlowEngine)
                files = engine._plan_files('K-1', state, set())
                body = files['docs/agents/tickets/k-1-flow/pt-1.md'].decode()
                expected = link.replace('./../followups/nested/%64raft.md', '../../plans/followups/nested/draft.md')
                expected = expected.replace('../followups/', '../../plans/followups/')
                self.assertEqual(body, expected)

    def test_new_proposal_text_replaces_old_plan_only_until_committed(self):
        from .test_cardflow_plan_extra import PLAN
        self.put('plan.md', PLAN.replace('Wstęp planu.', 'Nowy wstęp.').replace(
            'followups/replay-code.md', 'followups/nested/draft.md'))
        plan_file = 'docs/agents/plans/k-1.md'
        self.put('work/' + plan_file, PLAN.replace('Wstęp planu.', 'Stary wstęp.'))
        row = dict(id='PT-1', title='Szkic', executor='Sonnet', reviewer='Opus', blockers=[], body='- [ ] Działa.')
        st = FlowState(plan_proposed=dict(seq=10, planPath=str(self.root / 'plan.md'), artifactPath=str(self.root)),
                       plan_approved=dict(planFile=plan_file, rows=[row]))
        st.results[('plan-commit', 'old')] = dict(seq=9, ok=True)
        engine = FlowEngine.__new__(FlowEngine)

        def body():
            return engine._plan_files('K-1', st, set(), worktree=str(self.root / 'work'))[plan_file].decode()

        self.assertIn('Nowy wstęp.', body())
        self.assertNotIn('Stary wstęp.', body())
        st.results[('plan-commit', 'failed')] = dict(seq=11, ok=False)
        self.assertIn('Nowy wstęp.', body())
        st.results[('plan-commit', 'new')] = dict(seq=12, ok=True)
        self.put('work/' + plan_file, body().replace('Stary wstęp.', 'Ręczny wstęp.'))
        self.assertIn('Ręczny wstęp.', body())

    def test_relocation_keeps_other_links_and_text_unchanged(self):
        text = ('Zwykły tekst ../followups/nested/draft.md\n'
                '[www](https://example.org/../followups/nested/draft.md#zakres)\n'
                '[plik](../other.md) [fragment](#zakres)\n'
                '[zewnętrzny]: //example.org/followups/draft.md\n')
        self.assertEqual(planartifacts.relocate_ticket_links(text, '../../plans/followups'), text)

    def test_sketch_links_use_the_committed_plan_name_and_ticket_ids(self):
        self.put('custom.md', '[szkic](followups/nested/draft.md)\n')
        text = ('[plan](../../custom.md#plan)\n'
                '[kartka](<../../tickets/%31.md?view=1#test> "Tytuł")\n'
                '[inna][ticket]\n\n[ticket]: ../../tickets/PT-8.md#test "Tytuł"\n'
                '[szkic](./draft.md#zakres) [www](https://example.org/plan.md)\n')
        self.put('tickets/PT-8.md', '- [ ] Istniejący ticket.\n')
        self.put('followups/nested/draft.md', text)
        plan_file = 'docs/agents/plans/2026-11-06-k-1-flow.md'
        rows = [dict(id=ident, workId=work, title='Szkic', executor='Sonnet', reviewer='Opus',
                     blockers=[], body='- [ ] Działa.')
                for ident, work in (('PT-42', '#1'), ('PT-8', 'PT-8'))]
        state = fold([
            dict(type='plan.proposed', seq=1, planPath=str(self.root / 'custom.md'), artifactPath=str(self.root)),
            dict(type='plan.approved', seq=2, planFile=plan_file, rows=rows),
        ])
        files = FlowEngine.__new__(FlowEngine)._plan_files('K-1', state, set())
        self.assertEqual(files['docs/agents/plans/followups/nested/draft.md'].decode(),
                         text.replace('../../custom.md', '../../2026-11-06-k-1-flow.md')
                         .replace('../../tickets/%31.md', '../../../tickets/k-1-flow/pt-42.md')
                         .replace('../../tickets/PT-8.md', '../../../tickets/k-1-flow/pt-8.md'))

    def test_journal_replay_selects_only_the_approved_artifact_package(self):
        from .test_cardflow_plan_extra import PLAN
        old_plan = PLAN.replace('followups/replay-code.md', 'followups/nested/draft.md')
        self.put('plan.md', old_plan)
        old_files = planartifacts.read(self.root)
        for name, body in old_files.items():
            self.put('revision/' + name, body.decode())
        self.put('revision/followups/nested/draft.md', '# Zakres\nTreść B.\n')
        self.put('revision/followups/new.md', '# Nowy szkic\n')
        self.put('revision/plan.md', old_plan + '\n[Nowy szkic](followups/new.md)\n')
        plan_file = 'docs/agents/plans/k-1.md'
        row = dict(id='PT-1', title='Szkic', executor='Sonnet', reviewer='Opus',
                   blockers=[], body=old_files['tickets/1.md'].decode())
        events = [
            dict(type='plan.proposed', seq=1, planPath=str(self.root / 'plan.md'), artifactPath=str(self.root)),
            dict(type='plan.approved', seq=2, planFile=plan_file, rows=[row]),
            dict(type='plan.returned', seq=3),
            dict(type='plan.proposed', seq=4, planPath=str(self.root / 'revision/plan.md'),
                 artifactPath=str(self.root / 'revision')),
            dict(type='plan.revision-ready', seq=5, pending=True),
        ]
        engine = FlowEngine.__new__(FlowEngine)

        def files(extra):
            return engine._plan_files('K-1', fold(events + extra), set())

        draft_file = 'docs/agents/plans/followups/nested/draft.md'
        new_file = 'docs/agents/plans/followups/new.md'
        for extra in ([], [dict(type='plan.revision-discarded', seq=6)]):
            with self.subTest(events=extra):
                package = files(extra)
                self.assertEqual(package[draft_file], old_files['followups/nested/draft.md'])
                self.assertNotIn(new_file, package)
                self.assertNotIn(b'[Nowy szkic]', package[plan_file])
        package = files([dict(type='plan.revision-approved', seq=6, plan={})])
        self.assertEqual(package[draft_file], '# Zakres\nTreść B.\n'.encode('utf-8'))
        self.assertEqual(package[new_file], b'# Nowy szkic\n')
        self.assertIn(b'[Nowy szkic](followups/new.md)', package[plan_file])

    def test_retaining_artifact_source_preserves_the_approval_event(self):
        proposal = dict(type='plan.proposed', seq=1, planPath=str(self.root / 'plan.md'),
                        artifactPath=str(self.root))
        approval = dict(type='plan.approved', seq=2, rows=[], planFile='plan.md')
        state = fold([proposal, approval])
        self.assertEqual(state.plan_approved, approval)
        self.assertEqual(planartifacts.source_for_commit(state), proposal)
        restarted = fold([proposal, approval, dict(type='card.started', seq=3)])
        self.assertEqual(planartifacts.source_for_commit(restarted), {})

    def test_pending_commit_is_not_a_commit_of_the_approved_revision(self):
        from .test_cardflow_plan_extra import PLAN
        plan_file = 'docs/agents/plans/k-1.md'
        old = PLAN.replace('Wstęp planu.', 'Stary wstęp.').replace(
            'followups/replay-code.md', 'followups/nested/draft.md')
        self.put('work/' + plan_file, old)
        self.put('plan.md', old.replace('Stary wstęp.', 'Nowy wstęp.') +
                 '\n[Nowy zakres](followups/new.md)\n')
        self.put('followups/new.md', '# Nowy zakres\n')
        row = dict(id='PT-1', title='Szkic', executor='Sonnet', reviewer='Opus', blockers=[], body='- [ ] Działa.')
        events = [
            dict(type='plan.proposed', seq=1),
            dict(type='plan.approved', seq=2, planFile=plan_file, rows=[row]),
            dict(type='plan.proposed', seq=3, planPath=str(self.root / 'plan.md'), artifactPath=str(self.root)),
            dict(type='plan.revision-ready', seq=4, pending=True),
            dict(type='result', seq=5, action='plan-commit', key='2:4', ok=True),
        ]
        engine = FlowEngine.__new__(FlowEngine)

        def body():
            return engine._plan_files('K-1', fold(events), set(), worktree=str(self.root / 'work'))[plan_file].decode()

        self.assertTrue(planartifacts.keep_committed_plan_text(fold(events)))
        self.assertIn('Stary wstęp.', body())
        self.assertNotIn('[Nowy zakres]', body())
        events.append(dict(type='plan.revision-approved', seq=6, plan={}))
        self.assertFalse(planartifacts.keep_committed_plan_text(fold(events)))
        self.assertIn('Nowy wstęp.', body())
        self.assertIn('[Nowy zakres](followups/new.md)', body())
        self.assertNotIn('Stary wstęp.', body())
        events.append(dict(type='result', seq=7, action='plan-commit', key='2:6', ok=False))
        self.assertFalse(planartifacts.keep_committed_plan_text(fold(events)))
        self.assertIn('Nowy wstęp.', body())
        self.put('work/' + plan_file, body() + '\nRęczny dopisek.\n')
        events.append(dict(type='result', seq=8, action='plan-commit', key='2:6', ok=True))
        self.assertTrue(planartifacts.keep_committed_plan_text(fold(events)))
        self.assertIn('Nowy wstęp.', body())
        self.assertIn('Ręczny dopisek.', body())

    def test_symlinks_are_rejected_instead_of_skipped(self):
        for name in ('followups/nested/draft.md', 'tickets/1.md', 'plan.md'):
            with self.subTest(name=name):
                path = self.root / name
                body = path.read_bytes()
                path.unlink()
                path.symlink_to('/etc/passwd')
                with self.assertRaisesRegex(ValueError, 'dowiązanie'):
                    planartifacts.read(self.root)
                path.unlink()
                path.write_bytes(body)
        (self.root / 'followups/escape').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'dowiązanie'):
            planartifacts.read(self.root)

    def test_missing_and_escaping_targets_fail_in_plan_ticket_and_sketch(self):
        for source, target in (('plan.md', 'followups/missing.md'),
                               ('plan.md', 'followups/../../outside.md'),
                               ('tickets/1.md', '../../followups/nested/draft.md'),
                               ('plan.md', '/followups/nested/draft.md'),
                               ('plan.md', 'followups/%2e%2e/%2e%2e/outside.md'),
                               ('followups/nested/draft.md', '../../../outside.md')):
            with self.subTest(source=source, target=target):
                old = (self.root / source).read_text()
                self.put(source, f'[szkic]({target})\n')
                with self.assertRaisesRegex(ValueError, 'celu followups'):
                    planartifacts.read(self.root)
                self.put(source, old)

    def test_file_and_total_limits_apply_before_any_snapshot_write(self):
        self.put('followups/large.md', 'x' * (512 * 1024 + 1))
        journal = mock.Mock()
        engine = mock.Mock(journal=journal)
        with self.assertRaisesRegex(ValueError, '512 KiB'):
            FlowEngine._freeze_planning_output(engine, 'K-1', 'step', self.root)
        journal.write_file.assert_not_called()
        (self.root / 'followups/large.md').unlink()
        for n in range(32):
            self.put(f'followups/{n}.md', 'x' * (512 * 1024))
        with self.assertRaisesRegex(ValueError, '16 MiB'):
            FlowEngine._freeze_planning_output(engine, 'K-1', 'step', self.root)
        journal.write_file.assert_not_called()


class ArtifactsFlowTest(FlowInProcess):
    proposal = integration.PlanFlowTest.proposal
    submit = integration.PlanFlowTest.submit

    def test_approved_amendment_commits_new_text_then_preserves_manual_additions(self):
        card = self.card()
        path = Path(self.proposal(card))
        draft = path.parent / 'followups/nested/scope.md'
        draft.parent.mkdir(parents=True)
        draft.write_text('# Szkic\nZatwierdzona treść A.\n')
        with path.open('a') as fh:
            fh.write('\nStary tekst planu. [szkic](followups/nested/scope.md)\n')
        with (path.parent / 'tickets/1.md').open('a') as fh:
            fh.write('\n[szkic](../followups/nested/scope.md)\n')
        self.assertTrue(self.submit(card, str(path))['ok'])
        self.flow.stop()
        engine = self.flow.engine
        mapping = engine.approve_plan(card)['map']
        with mock.patch.object(engine, '_launch'):
            engine._plan_next(card, fold(engine.journal.read(card)))
        st = fold(engine.journal.read(card))
        info = ticket_of(st)
        plan_file = st.plan_approved['planFile']
        worktree = Path(info['cardWorktree'])
        ticket_file = next(p for p in engine._plan_files(card, st, set())
                           if p.endswith(mapping['#1'].lower() + '.md'))
        with (worktree / ticket_file).open('a') as fh:
            fh.write('\nRęczny dopisek kartki.\n')

        # Rzeczywiste przygotowanie plan-amend; zastępujemy tylko wykonawcę kroku.
        with mock.patch.object(engine, '_launch') as launch:
            self.assertTrue(engine.edit_plan(card, {'action': 'return', 'feedback': 'Dodaj nowy zakres.'})['ok'])
        prompt = launch.call_args.kwargs['prompt_text']
        out = Path(prompt.split('Katalog wyjściowy: ')[1].split('\n')[0])
        amendment = (worktree / plan_file).read_text().replace('Stary tekst planu.',
                            'Nowy zakres: [szkic](followups/new.md).')
        amendment = amendment.replace('---\n\n', '---\n\nNowy wstęp.\n\n', 1)
        (out / 'plan.md').write_text(amendment)
        (out / 'followups').mkdir(exist_ok=True)
        (out / 'followups/nested/scope.md').write_text('# Szkic\nNowa treść B.\n')
        (out / 'followups/new.md').write_text('# Nowy szkic\nPełna treść.\n')
        step = launch.call_args.args[6]
        engine.journal.append(card, 'step.started', step=step, kind='plan-amend',
                              stepDir=str(out.parent), executor='Sonnet')
        snapshot = engine._freeze_planning_output(card, step, str(out))
        engine.journal.append(card, 'step.finished', step=step, result='done', snapshotDir=snapshot)
        engine._advance_user_revision(card, fold(engine.journal.read(card)))
        self.assertTrue(self.of(card, 'plan.revision-ready')[-1]['pending'])
        proposal = fold(engine.journal.read(card)).plan_proposed
        self.assertNotEqual(proposal['artifactPath'], str(out))
        engine.journal.append(card, 'plan.reviewed', verdict='APPROVE')
        # Monitor zapisuje stan starego zatwierdzonego planu także podczas pending.
        with mock.patch.object(engine, '_launch'):
            engine._plan_next(card, fold(engine.journal.read(card)))
        pending_commit = self.results(card, 'plan-commit')[-1]
        self.assertTrue(pending_commit['ok'])
        self.assertGreater(pending_commit['seq'], proposal['seq'])
        pending_body = git(self.repo, 'show', f"{info['cardBranch']}:{plan_file}")
        self.assertIn('Stary tekst planu.', pending_body)
        self.assertNotIn('Nowy wstęp.', pending_body)
        self.assertNotIn('[szkic](followups/new.md)', pending_body)
        scope_file = str(Path(plan_file).parent / 'followups/nested/scope.md')
        new_file = str(Path(plan_file).parent / 'followups/new.md')
        with self.subTest('zatwierdzony szkic podczas pending'):
            self.assertEqual(git(self.repo, 'show', f"{info['cardBranch']}:{scope_file}"),
                             '# Szkic\nZatwierdzona treść A.\n')
        with self.subTest('brak nowego szkicu podczas pending'):
            with self.assertRaises(subprocess.CalledProcessError):
                git(self.repo, 'show', f"{info['cardBranch']}:{new_file}")
        self.assertIn('](../../plans/followups/nested/scope.md)',
                      git(self.repo, 'show', f"{info['cardBranch']}:{ticket_file}"))
        self.assertTrue(engine.approve_plan(card)['ok'])
        (out / 'followups/nested/scope.md').write_text('zmiana po zamrożeniu')
        (out / 'followups/new.md').write_text('zmiana po zamrożeniu')

        def commit_and_check():
            engine._plan_commit(card, fold(engine.journal.read(card)), self.repo,
                                info['cardBranch'], info['cardWorktree'])
            body = git(self.repo, 'show', f"{info['cardBranch']}:{plan_file}")
            self.assertIn('Nowy wstęp.', body)
            self.assertIn('Nowy zakres: [szkic](followups/new.md).', body)
            self.assertNotIn('Stary tekst planu.', body)
            target = str(Path(plan_file).parent / 'followups/new.md')
            self.assertEqual(git(self.repo, 'show', f"{info['cardBranch']}:{target}"),
                             '# Nowy szkic\nPełna treść.\n')
            self.assertEqual(git(self.repo, 'show', f"{info['cardBranch']}:{scope_file}"),
                             '# Szkic\nNowa treść B.\n')
            self.assertIn('Ręczny dopisek kartki.',
                          git(self.repo, 'show', f"{info['cardBranch']}:{ticket_file}"))
            return body

        commit_and_check()
        with (worktree / plan_file).open('a') as fh:
            fh.write('\nRęczny dopisek planu po zatwierdzeniu.\n')
        engine.journal.append(card, 'ticket.rejected', ticket=mapping['#2'])
        body = commit_and_check()
        self.assertIn('Ręczny dopisek planu po zatwierdzeniu.', body)
        self.assertIn('| odrzucone |', body)

    def test_missing_followup_does_not_become_a_proposal(self):
        card = self.card()
        path = self.proposal(card)
        with open(path, 'a') as fh:
            fh.write('\n[szkic](followups/missing.md)\n')
        reply = self.submit(card, path)
        self.assertFalse(reply['ok'])
        self.assertIn('followups', ' '.join(reply['errors']))
        self.assertEqual(self.of(card, 'plan.proposed'), [])
        self.assertEqual(self.results(card, 'reserve'), [])

    def test_existing_unpublished_sketch_target_does_not_become_a_proposal(self):
        for target in ('spec.md', 'tickets/99.md'):
            with self.subTest(target=target):
                card = self.card()
                path = Path(self.proposal(card))
                (path.parent / target).write_text('- [ ] Poza publikowanym planem.\n')
                draft = path.parent / 'followups/draft.md'
                draft.parent.mkdir(exist_ok=True)
                draft.write_text(f'[cel](../{target})\n')
                reply = self.submit(card, str(path))
                self.assertFalse(reply['ok'])
                self.assertIn('nie trafia do commita', ' '.join(reply['errors']))
                self.assertEqual(self.of(card, 'plan.proposed'), [])
                self.assertEqual(self.results(card, 'reserve'), [])

    def test_frozen_review_revision_and_committed_links_survive_state_change(self):
        card = self.card()
        path = Path(self.proposal(card))
        draft = path.parent / 'followups/nested/scope.md'
        draft.parent.mkdir(parents=True)
        draft.write_text('# Szkic\nOryginalna treść.\n'
                         f'[plan](../../{path.name}#plan)\n'
                         '[kartka](<../../tickets/%31.md?view=1#test> "Tytuł")\n'
                         '[druga][ticket]\n\n[ticket]: ../../tickets/2.md#test "Tytuł"\n', encoding='utf-8')
        original_draft = draft.read_bytes()
        with path.open('a') as fh:
            fh.write('\nZakres poza v1: [szkic](followups/nested/scope.md).\n')
        ticket = path.parent / 'tickets/1.md'
        with ticket.open('a') as fh:
            fh.write('\n[szkic](../followups/nested/scope.md)\n'
                     '[szkic][scope]\n\n'
                     '[scope]: ../followups/nested/scope.md#zakres "Zakres"\n'
                     '[szkic](<../followups/nested/scope.md#zakres> "Zakres")\n')
        self.assertTrue(self.submit(card, str(path))['ok'])
        self.flow.stop()
        engine = self.flow.engine
        st = fold(engine.journal.read(card))
        proposal = st.plan_proposed
        frozen = Path(proposal['artifactPath'])
        draft.write_text('zmienione wyjście autora', encoding='utf-8')
        path.write_text('zmieniony plan autora', encoding='utf-8')
        self.assertEqual((frozen / 'followups/nested/scope.md').read_bytes(), original_draft)
        review = next(s for s in st.steps.values() if s.get('kind') == 'plan-review')
        self.assertEqual((Path(review['stepDir']) / 'in/proposal/followups/nested/scope.md').read_bytes(), draft_bytes := (frozen / 'followups/nested/scope.md').read_bytes())
        with mock.patch.object(engine, '_launch') as launch:
            engine._plan_step(card, st, mock.Mock(), 'plan-revise', engine._revision_prompt(card, proposal, 'Uwagi'))
        prompt = launch.call_args.kwargs['prompt_text']
        self.assertIn('Zamrożony komplet', prompt)
        revision_out = prompt.split('Katalog wyjściowy: ')[1].split('\n')[0]
        self.assertEqual((Path(revision_out) / 'followups/nested/scope.md').read_bytes(), draft_bytes)
        mapping = engine.approve_plan(card)['map']
        with mock.patch.object(engine, '_launch'):
            engine._plan_next(card, fold(engine.journal.read(card)))
        st = fold(engine.journal.read(card))
        info = ticket_of(st)
        plan_file = st.plan_approved['planFile']
        sketch_file = str(Path(plan_file).parent / 'followups/nested/scope.md')
        committed_draft = draft_bytes.decode().replace('../../' + path.name, '../../' + Path(plan_file).name)
        for work_id, ident in mapping.items():
            old_name = '%31' if work_id == '#1' else work_id.lstrip('#')
            committed_draft = committed_draft.replace('../../tickets/' + old_name + '.md',
                                                     '../../../tickets/' + card.lower() + '-flow/' + ident.lower() + '.md')
        files = engine._plan_files(card, st, set(), worktree=info['cardWorktree'])
        ticket_file = next(p for p in files if p.endswith(mapping['#1'].lower() + '.md'))

        def check_committed_links():
            for file, link in ((plan_file, 'followups/nested/scope.md'),
                               (ticket_file, '../../plans/followups/nested/scope.md')):
                body = git(self.repo, 'show', f"{info['cardBranch']}:{file}")
                self.assertIn(f']({link})', body)
                if file == ticket_file:
                    self.assertIn(f'[scope]: {link}#zakres "Zakres"', body)
                    self.assertIn(f'](<{link}#zakres> "Zakres")', body)
                target = os.path.normpath(str(Path(file).parent / link))
                self.assertEqual(git(self.repo, 'show', f"{info['cardBranch']}:{target}"), committed_draft)
            sketch = git(self.repo, 'show', f"{info['cardBranch']}:{sketch_file}")
            self.assertEqual(sketch, committed_draft)
            targets = []
            for match in planartifacts.LINK.finditer(sketch):
                url = urlsplit((match.group(1) or match.group(2)).strip('<>'))
                target = os.path.normpath(str(Path(sketch_file).parent / unquote(url.path)))
                targets.append(target)
                body = git(self.repo, 'show', f"{info['cardBranch']}:{target}")
                self.assertTrue(body)
            self.assertEqual(set(targets), {plan_file, ticket_file,
                             str(Path(ticket_file).with_name(mapping['#2'].lower() + '.md'))})

        check_committed_links()
        for file, link in ((plan_file, 'followups/nested/scope.md'),
                           (ticket_file, '../../plans/followups/nested/scope.md')):
            self.assertIn(f']({link})', files[file].decode())
            self.assertTrue((Path(info['cardWorktree']) / Path(file).parent / link).is_file())
        with (Path(info['cardWorktree']) / ticket_file).open('a') as fh:
            fh.write('\nRęczny dopisek kartki.\n')
        engine.journal.append(card, 'plan.ticket-edited', ticket=mapping['#2'], changes={'note': 'aktualizacja'})
        engine._plan_commit(card, fold(engine.journal.read(card)), self.repo, info['cardBranch'], info['cardWorktree'])
        check_committed_links()
        self.assertIn('Ręczny dopisek', git(self.repo, 'show', f"{info['cardBranch']}:{ticket_file}"))
        self.assertIn('Zakres poza v1', git(self.repo, 'show', f"{info['cardBranch']}:{plan_file}"))
        self.assertEqual(git(self.repo, 'show', f"{info['cardBranch']}:{sketch_file}"), committed_draft)
