"""Porty sond domen review 3 PT-288; skutki wyłącznie na atrapach."""
import itertools
import unittest
from unittest import mock

from daemon.tests import test_cardflow_trial_inventory as fixtures
from daemon.tests import cardflow_identity_generator as gen
from daemon.tests import test_cardflow_identity_matrix as matrix
from tide_daemon.cardflow.processidentity import process_identities


class Domains(unittest.TestCase):
    def fixture(self, history, restarted):
        f = fixtures.InventoryTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.project()
        for e in matrix._host(history):
            f.journal.append('K-38', e['type'], **{k: v for k, v in e.items() if k != 'type'})
        if restarted:
            f.restart()
        return f

    def test_start_from_previous_order_is_not_predecessor(self):
        for restarted in (False, True):
            history = [gen._card(), gen._start('PT-999'), gen._request('PT-999'), gen._decision('PT-999'),
                       gen._card(), gen._outside_start('PT-999'), gen._outside_finish('PT-999')]
            f = self.fixture(history, restarted)
            state = process_identities(f.journal.read_complete('K-38'))[gen.STEP].state
            independent = f.outside_history()
            with f.outside_environment() as spawn:
                try:
                    f.engine._start_outside('K-38', independent, 'echo ordinary-test')
                except fixtures.FlowError:
                    pass
                popen = spawn.call_count
            incus = matrix._running_until_stopped()
            builder, _ = matrix._builder(f, incus)
            with mock.patch('tide_daemon.box.admission.snapshot'):
                try:
                    fixtures.lifecycle.Lifecycle(builder).stop('proj')
                except fixtures.BoxBusy:
                    pass
            observed = dict(restarted=restarted, state=state, popen=popen, stop=incus.stop.call_count)
            with self.subTest(restarted=restarted):
                self.assertEqual((state, popen, incus.stop.call_count), ('unknown', 0, 0), observed)
                f.assert_lifecycle_preserved()

    def test_new_order_does_not_consume_previous_outside_completion(self):
        for project in ('proj', 'other-project'):
            for restarted in (False, True):
                start = gen._start('PT-999', attempt=1)
                history = [gen._card(), start, gen._request('PT-999'), gen._decision('PT-999'),
                           gen._outside_start('PT-999'), {**gen._outside_finish('PT-999'), 'exit': 7, 'tail': 'OLD'},
                           {**gen._card(), 'project': project}, start, gen._request('PT-999'), gen._decision('PT-999')]
                f = self.fixture(history, restarted)
                state = process_identities(f.journal.read_complete('K-38'))[gen.STEP].state
                with f.outside_environment() as spawn:
                    answer = f.engine._outside_ready('K-38', matrix._host([start])[0],
                                                     gen._request('PT-999'), gen._decision('PT-999'))
                    observed = dict(project=project, restarted=restarted, state=state, answer=answer,
                                    popen=spawn.call_count)
                with self.subTest(project=project, restarted=restarted):
                    self.assertEqual(state, 'ok')
                    self.assertIsNone(process_identities(f.journal.read_complete('K-38'))[gen.STEP].current_outside)
                    self.assertIsNone(answer)
                    spawn.assert_not_called()
                    self.assertNotIn('OLD', (answer or {}).get('answer', ''), observed)

    def test_new_order_consumes_its_own_completion(self):
        for project, restarted in itertools.product(('proj', 'other-project'), (False, True)):
            start = gen._start('PT-999', attempt=1)
            history = [gen._card(), start, gen._request('PT-999'), gen._decision('PT-999'),
                       gen._outside_start('PT-999'), {**gen._outside_finish('PT-999'), 'exit': 7, 'tail': 'OLD'},
                       {**gen._card(), 'project': project}, start, gen._request('PT-999'), gen._decision('PT-999'),
                       {**gen._outside_start('PT-999'), 'unit': 'new.scope'},
                       {**gen._outside_finish('PT-999'), 'exit': 0, 'tail': 'NEW'}]
            f = self.fixture(history, restarted)
            with self.subTest(project=project, restarted=restarted), f.outside_environment() as spawn:
                answer = f.engine._outside_ready('K-38', matrix._host([start])[0],
                                                 gen._request('PT-999'), gen._decision('PT-999'))
                self.assertEqual(answer['choice'], 'run')
                self.assertIn('Kod wyjścia: 0', answer['answer'])
                self.assertIn('NEW', answer['answer'])
                self.assertNotIn('OLD', answer['answer'])
                spawn.assert_not_called()

