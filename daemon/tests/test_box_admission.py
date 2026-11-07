"""Budżet v1: wyścigi, rzeczywiste zasoby po awarii i próg pamięci hosta."""
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.box import admission as ad
from tide_daemon.box import builder as bb
from tide_daemon.box.base import BaseBuilder
from tide_daemon.box.lifecycle import Lifecycle
from tide_daemon.box import lifecycle
from tide_daemon.cardflow.journal import Journal
from tide_daemon.box import profile as bp
from tide_daemon.box import state as bs
import dataclasses
from .test_box_builder import BoxCase, PROFILE

HOST_MEMORY = ad.read_memory


class HostMemoryTest(unittest.TestCase):
    def test_host_procfs_uses_no_swap_or_environment_override(self):
        data = 'MemTotal: 33554432 kB\nMemAvailable: 29360128 kB\nSwapFree: 999999999 kB\n'
        with mock.patch('builtins.open', mock.mock_open(read_data=data)) as opened:
            self.assertEqual(HOST_MEMORY(), (32 * ad.GiB, 28 * ad.GiB))
        opened.assert_called_once_with('/proc/meminfo', encoding='ascii')

    def test_incomplete_duplicate_and_invalid_measurements_are_unknown(self):
        for data in ('MemTotal: 1 kB\n', 'MemTotal: 5 GB\nMemAvailable: 1 kB\n',
                     'MemTotal: 5 kB\nMemAvailable: 6 kB\n',
                     'MemTotal: 5 kB\nMemAvailable: 1 kB\nMemTotal: 6 kB\n'):
            with self.subTest(data=data), mock.patch('builtins.open', mock.mock_open(read_data=data)):
                with self.assertRaises((ValueError, KeyError)):
                    HOST_MEMORY()


class AdmissionTest(BoxCase):
    def setUp(self):
        super().setUp()
        self.approve()
        bs.record('plasma-tide', 'box.approved', 'plasma-tide', source='ui')
        self.b = self.builder()
        ad.changed.clear()
        self.addCleanup(ad.changed.clear)

    def state(self):
        return ad.snapshot(self.b.incus)

    def test_unknown_later_measurement_preserves_request_and_retries(self):
        for kind in ('start', 'build', 'base'):
            for measurement in (2, 3):
                for error in (OSError('procfs'), ValueError('unknown'), KeyError('MemTotal')):
                    with self.subTest(kind=kind, measurement=measurement, error=type(error).__name__):
                        if kind == 'start':
                            self.b.build('plasma-tide')
                            Lifecycle(self.b).stop('plasma-tide')
                            run = lambda: Lifecycle(self.b).start('plasma-tide')
                        elif kind == 'build':
                            run = lambda: self.b.build('plasma-tide', PROFILE)
                        else:
                            run = BaseBuilder(self.settings, self.b.incus, self.policy).build
                        values = [(78 * ad.GiB, 60 * ad.GiB)] * (measurement - 1) + [error]
                        with mock.patch.object(ad, 'read_memory', side_effect=values), \
                                mock.patch.object(self.b.incus, 'start') as start:
                            with self.assertRaisesRegex(bb.SlotWaiting, 'pomiaru'):
                                run()
                            start.assert_not_called()
                        state = self.state()
                        self.assertIsNone(state['owner'])
                        self.assertEqual(state['history'], [])
                        self.assertEqual(len(state['queue']), 1)
                        self.assertEqual(state['queue'][0]['kind'], kind)
                        # Także automatyczne ponowienie musi zachować oczekiwanie.
                        with mock.patch.object(ad, 'read_memory', side_effect=values):
                            self.retry()
                        self.assertEqual(self.state()['queue'][0]['args'], state['queue'][0]['args'])
                        self.assertEqual(self.state()['queue'][0]['queued'], state['queue'][0]['queued'])
                        self.assertEqual(self.state()['history'], [])
                        with mock.patch.object(BaseBuilder, '_provision',
                                               side_effect=lambda name, *args: self.b.incus.stop(name)):
                            self.retry()
                        self.assertEqual(self.state()['queue'], [])
                        self.assertEqual(self.state()['history'], [])
                        if kind != 'base':
                            self.assertTrue(self.b.status('plasma-tide').running)
                            Lifecycle(self.b).stop('plasma-tide')

    def retry(self, builder=None):
        self.finish_retry(ad.tick(builder or self.b))

    def finish_retry(self, worker=None):
        worker = worker or ad._worker
        if worker is not None:
            worker.join(15)
            self.assertFalse(worker.is_alive(), 'klient kolejki nie zakończył próby')

    def test_unknown_later_measurement_preserves_fifo_position(self):
        with mock.patch.object(ad, 'read_memory', return_value=(78 * ad.GiB, 27 * ad.GiB)):
            for slug in ('plasma-tide', 'second'):
                with self.assertRaises(bb.SlotWaiting):
                    self.b.build(slug, PROFILE)
        original = self.state()['queue']
        with mock.patch.object(ad, 'read_memory', side_effect=[
                (78 * ad.GiB, 60 * ad.GiB), ValueError('unknown')]):
            self.retry()
        queued = self.state()['queue']
        self.assertEqual([q['id'] for q in queued], [q['id'] for q in original])
        self.assertEqual(queued[0]['queued'], original[0]['queued'])
        self.assertEqual(queued[0]['args'], original[0]['args'])
        self.assertIn('pomiaru', queued[0]['reason'])

    def test_queued_project_and_base_build_leave_monitor_responsive(self):
        from PyQt6.QtCore import QCoreApplication
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        from tide_daemon.cardflow import envconsent, boxcontrol
        from tide_daemon.cardflow.engine import FlowError

        app = QCoreApplication.instance() or QCoreApplication([])
        for kind in ('build', 'base'):
            with self.subTest(kind=kind):
                entered, release = threading.Event(), threading.Event()
                status_done, stop_done = threading.Event(), threading.Event()
                results = {}

                def provision(*args):
                    entered.set()
                    if not release.wait(10):
                        raise RuntimeError('timeout atrapy budowy')
                    if kind == 'base':
                        self.b.incus.stop(self.settings.base_image)
                    else:
                        raise bb.BuildError('koniec kontrolowanej budowy')

                engine = SimpleNamespace(journal=Journal(bp.board_dir()), procs={},
                    box_settings_source=lambda: dataclasses.replace(self.settings, enabled=True),
                    box_builder=lambda: self.b, take_notices=lambda: [],
                    store=SimpleNamespace(load=lambda: SimpleNamespace(
                        projects={'plasma-tide': {}, 'second': {}}, cards={})))
                engine.tick = lambda: lifecycle.tick(engine)

                def status(*args):
                    try:
                        results['status'] = envconsent.status(engine, *args)
                    finally:
                        status_done.set()

                def stop(*args):
                    try:
                        boxcontrol.stop(engine, *args)
                    except FlowError as exc:
                        results['stop'] = exc.code
                    finally:
                        stop_done.set()

                engine.box_status, engine.stop_box = status, stop
                monitor = CardFlowMonitor(engine)
                run = (lambda: self.b.build('plasma-tide', PROFILE)) if kind == 'build' else \
                    BaseBuilder(self.settings, self.b.incus, self.policy).build
                with mock.patch.object(ad, 'read_memory', return_value=(78 * ad.GiB, 27 * ad.GiB)):
                    with self.assertRaises(bb.SlotWaiting):
                        run()
                    with self.assertRaises(bb.SlotWaiting):
                        self.b.build('second', PROFILE)
                target = self.b if kind == 'build' else BaseBuilder
                worker = None
                with mock.patch.object(target, '_provision', side_effect=provision):
                    try:
                        monitor.tick()
                        self.assertTrue(entered.wait(5), 'budowa nie ruszyła')
                        # Kilka ticków nie może zlecić tej samej budowy drugi raz.
                        worker = ad.tick(self.b)
                        self.assertIs(ad.tick(self.b), worker)
                        monitor.box_status('plasma-tide', lambda result: None)
                        self.assertTrue(status_done.wait(2), 'BoxStatus czeka na budowę')
                        state = results['status']['admission']
                        self.assertEqual(state['owner']['kind'], kind)
                        self.assertEqual(state['queue'][0]['name'], 'second')
                        monitor.stop_box('plasma-tide', '', lambda result: None)
                        self.assertTrue(stop_done.wait(2), 'StopBox czeka na budowę')
                        self.assertEqual(results['stop'], 'box-busy' if kind == 'build' else 'unknown-box-owner')
                        self.assertEqual(self.state()['owner']['kind'], kind)
                        self.assertIsNone(self.b.incus.instance('second'))
                    finally:
                        release.set()
                        if monitor._pool is not None:
                            monitor._pool.shutdown(wait=True)
                            monitor._pool = None
                        self.finish_retry(worker)
                        app.processEvents()
                for req in self.state()['queue']:
                    ad.cancel(req['id'])
                if kind == 'build':
                    Lifecycle(self.b).stop('plasma-tide')

    def test_exact_limits_below_and_above_44_gib_ignore_profile(self):
        for total in (21, 32, 44, 78):
            with self.subTest(total=total), mock.patch.object(ad, 'read_memory', return_value=(total * ad.GiB, total * ad.GiB)):
                self.b.build('plasma-tide', {**PROFILE, 'resources': {'cpu': 64, 'memory': '96GiB'}})
                self.assertEqual(self.b.incus.config_get('plasma-tide', 'limits.cpu'), '8')
                self.assertEqual(self.b.incus.config_get('plasma-tide', 'limits.memory'), str(min(24, total - 20) * ad.GiB))
                Lifecycle(self.b).stop('plasma-tide')

    def test_low_total_refuses_without_queue_or_slot(self):
        for total in (19, 20):
            with mock.patch.object(ad, 'read_memory', return_value=(total * ad.GiB, total * ad.GiB)):
                with self.assertRaisesRegex(bb.BuildError, '≤ 20 GiB'):
                    self.b.build('plasma-tide')
                self.assertIsNone(self.state()['owner'])
                self.assertEqual(self.state()['queue'], [])

    def test_available_threshold_minus_at_plus_and_no_swap_credit(self):
        for delta in (-1024**2, 0, 1024**2):
            with self.subTest(delta=delta), mock.patch.object(ad, 'read_memory', return_value=(78 * ad.GiB, 28 * ad.GiB + delta)):
                if delta < 0:
                    with self.assertRaisesRegex(bb.SlotWaiting, 'MemAvailable'):
                        self.b.build('plasma-tide')
                    self.assertIsNone(self.state()['owner'])
                    self.assertIsNone(self.b.incus.instance('plasma-tide'))
                else:
                    self.b.build('plasma-tide')
                    Lifecycle(self.b).stop('plasma-tide')

    def test_unknown_measurement_waits_without_slot_then_tick_retries(self):
        with mock.patch.object(ad, 'read_memory', side_effect=ValueError('brak MemAvailable')):
            with self.assertRaisesRegex(bb.SlotWaiting, 'pomiaru'):
                self.b.build('plasma-tide')
        state = self.state()
        self.assertIsNone(state['owner'])
        self.assertIn('pomiaru', state['queue'][0]['reason'])
        self.retry()
        self.assertEqual(self.state()['owner']['name'], 'plasma-tide')

    def test_two_builds_queue_survives_new_builder_and_cancel_preserves_owner(self):
        self.b.build('plasma-tide')
        with self.assertRaisesRegex(bb.SlotWaiting, 'globalny slot'):
            self.b.build('second', PROFILE)
        self.assertEqual(len(self.state()['queue']), 1)
        # Nowy klient/usługa nie dziedziczą obiektów ani „procesu budowy”.
        restarted = self.builder()
        self.retry(restarted)
        self.assertIsNone(restarted.incus.instance('second'))
        identifier = self.state()['queue'][0]['id']
        self.assertTrue(ad.cancel(identifier))
        self.assertEqual(self.state()['owner']['name'], 'plasma-tide')
        self.assertFalse(ad.cancel(identifier))

    def test_second_build_runs_only_after_confirmed_stop_and_fresh_memory_check(self):
        self.b.build('plasma-tide')
        with self.assertRaises(bb.SlotWaiting):
            self.b.build('second', PROFILE)
        # Sukces klienta stop bez zatrzymania instancji nie zwalnia rezerwacji.
        with mock.patch.object(self.b.incus, 'stop'):
            with self.assertRaisesRegex(bb.BuildError, 'potwierdzenia zatrzymania'):
                Lifecycle(self.b).stop('plasma-tide')
        self.retry(self.builder())
        self.assertIsNone(self.b.incus.instance('second'))
        Lifecycle(self.b).stop('plasma-tide')
        with mock.patch.object(ad, 'read_memory', return_value=(78 * ad.GiB, 27 * ad.GiB)):
            self.retry()
        self.assertIsNone(self.state()['owner'])
        self.assertIsNone(self.b.incus.instance('second'))
        self.retry()
        self.assertEqual(self.state()['owner']['name'], 'second')

    def test_memory_can_drop_between_reservation_and_actual_start(self):
        original = self.b.apply_nic_filter
        memory = mock.Mock(return_value=(78 * ad.GiB, 60 * ad.GiB))
        def filter_and_consume(name):
            original(name)
            memory.return_value = (78 * ad.GiB, 27 * ad.GiB)
        with mock.patch.object(ad, 'read_memory', memory), mock.patch.object(self.b, 'apply_nic_filter', filter_and_consume):
            with self.assertRaisesRegex(bb.SlotWaiting, 'MemAvailable'):
                self.b.build('plasma-tide')
        self.assertEqual(self.b.incus.instance('plasma-tide')['status'], 'Stopped')
        self.assertIsNone(self.state()['owner'])
        self.assertEqual(self.state()['queue'][0]['kind'], 'build')

    def test_racing_different_projects_admit_exactly_one(self):
        self.b.build('plasma-tide')
        Lifecycle(self.b).stop('plasma-tide')
        barrier = threading.Barrier(2)
        def build(slug):
            barrier.wait(timeout=5)
            try:
                self.builder().build(slug, PROFILE)
                return 'running'
            except bb.SlotWaiting:
                return 'waiting'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(build, ['one', 'two']))
        self.assertCountEqual(results, ['running', 'waiting'])
        self.assertEqual(sum(i['status'] == 'Running' for i in self.b.incus.list()), 1)
        self.assertEqual(len(self.state()['queue']), 1)

    def test_live_reservation_survives_temporary_stop(self):
        req = ad.request(self.b.incus, 'build', 'one', slug='one', profile=PROFILE)
        with ad.operation(self.b.incus, req):
            # Nawet jeszcze bez instancji druga budowa nie może wejść.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.builder().build, 'two', PROFILE)
                with self.assertRaises(bb.SlotWaiting):
                    future.result(timeout=5)
            self.assertEqual(self.state()['owner']['name'], 'one')
        self.assertIsNone(self.state()['owner'])

    def test_crashed_service_keeps_running_resource_and_pending_build(self):
        # Fork dziedziczy wyłącznie jednorazową atrapę Incusa i hostowego meminfo.
        context = multiprocessing.get_context('fork')
        def crash_after_start():
            req = ad.request(self.b.incus, 'build', 'plasma-tide', slug='plasma-tide', profile=PROFILE)
            with ad.operation(self.b.incus, req):
                self.b.incus.init('tide-base', 'plasma-tide')
                ad.start(self.b.incus, 'plasma-tide')
                os._exit(17)
        child = context.Process(target=crash_after_start)
        child.start()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join(5)
            self.fail('proces atrapy nie zakończył się')
        self.assertEqual(child.exitcode, 17)
        # Nie ma już klienta ani lease, lecz instancja nadal pracuje.
        self.assertEqual(self.state()['owner']['name'], 'plasma-tide')
        req = ad.request(self.b.incus, 'build', 'second', slug='second', profile=PROFILE)
        with self.assertRaises(bb.SlotWaiting):
            with ad.operation(self.b.incus, req):
                self.fail('druga budowa została dopuszczona')
        self.retry(self.builder())
        self.assertIsNone(self.b.incus.instance('second'))

    def test_unknown_inventory_does_not_erase_owner(self):
        self.b.build('plasma-tide')
        with mock.patch.object(self.b.incus, 'operations', side_effect=OSError('Incus niedostępny')):
            state = self.state()
        self.assertEqual(state['owner']['name'], 'plasma-tide')
        self.assertIn('nieznany stan', state['error'])

    def test_status_exposes_fifo_and_reason(self):
        from tide_daemon.box.cli import format_status
        self.b.build('plasma-tide')
        with self.assertRaises(bb.SlotWaiting):
            self.b.build('second', PROFILE)
        rendered = format_status(dict(self.b.status('plasma-tide').to_dict(), admission=self.state()))
        self.assertIn('kolejka 1: second (build): zajęty globalny slot: plasma-tide', rendered)
        self.assertIn('jedno aktywne pudełko lub budowa', rendered)

    def test_service_retries_memory_every_five_seconds_and_after_release(self):
        engine = SimpleNamespace(journal=Journal(bp.board_dir()), procs={},
                                 box_settings_source=lambda: dataclasses.replace(self.settings, enabled=True),
                                 box_builder=lambda: self.b)
        with mock.patch.object(ad, 'read_memory', return_value=(78 * ad.GiB, 27 * ad.GiB)):
            with self.assertRaises(bb.SlotWaiting):
                self.b.build('plasma-tide')
            with mock.patch.object(lifecycle.time, 'monotonic', return_value=100) as clock, mock.patch.object(ad, 'tick', wraps=ad.tick) as attempt:
                lifecycle.tick(engine)
                self.finish_retry()
                self.assertEqual(attempt.call_count, 1)
                clock.return_value = 104.9
                lifecycle.tick(engine)
                self.finish_retry()
                self.assertEqual(attempt.call_count, 1)
                clock.return_value = 105
                lifecycle.tick(engine)
                self.finish_retry()
                self.assertEqual(attempt.call_count, 2)
        lifecycle.tick(engine)
        self.finish_retry()
        self.assertEqual(self.state()['owner']['name'], 'plasma-tide')
        with self.assertRaises(bb.SlotWaiting):
            self.b.build('second', PROFILE)
        Lifecycle(self.b).stop('plasma-tide')
        engine._box_admission_next = float('inf')
        lifecycle.tick(engine)  # sygnał zwolnienia nie czeka na upływ następnych 5 s
        self.finish_retry()
        self.assertEqual(self.state()['owner']['name'], 'second')

    def test_unknown_incus_state_and_inflight_server_operation_block_recovery(self):
        self.b.build('plasma-tide')
        data = self.fake_state()
        for status, operations in [('Frozen', []), ('Stopped', [{'status': 'Running'}])]:
            with self.subTest(status=status):
                data['instances']['plasma-tide']['status'] = status
                data['operations'] = operations
                self.write_state(data)
                with self.assertRaisesRegex(bb.SlotWaiting, 'nieznany stan'):
                    self.builder().build('second', PROFILE)
                self.assertEqual(self.state()['owner']['name'], 'plasma-tide')
                self.assertIsNone(self.b.incus.instance('second'))

    def test_failed_build_keeps_slot_while_docker_or_box_still_running(self):
        with mock.patch.object(self.b, '_provision', side_effect=RuntimeError('klient zakończony')):
            with self.assertRaises(RuntimeError):
                self.b.build('plasma-tide')
        with self.assertRaises(bb.SlotWaiting):
            self.builder().build('second', PROFILE)
        self.assertEqual(self.state()['owner']['name'], 'plasma-tide')

    def test_base_builder_uses_same_slot_and_exact_limits_before_start(self):
        self.b.build('plasma-tide')
        base = BaseBuilder(self.settings, self.b.incus, self.policy)
        with self.assertRaises(bb.SlotWaiting):
            base.build()
        self.assertEqual(self.state()['queue'][0]['kind'], 'base')
        ad.cancel(self.state()['queue'][0]['id'])
        Lifecycle(self.b).stop('plasma-tide')
        seen = []
        def provision(name, proxy, result):
            seen.append(self.b.incus.config_get(name, 'limits.memory'))
            self.assertEqual(self.b.incus.config_get(name, 'limits.cpu'), '8')
            with self.assertRaises(bb.SlotWaiting):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(self.builder().build, 'second', PROFILE).result(timeout=5)
            self.b.incus.stop(name)
        with mock.patch.object(base, '_provision', side_effect=provision):
            base.build()
        self.assertEqual(seen, [str(24 * ad.GiB)])
        self.assertIsNone(self.state()['owner'])

    def test_corrupt_state_cannot_be_reset_to_free(self):
        self.state()
        with open(os.path.join(ad.folder(), 'state.json'), 'w') as stream:
            stream.write('{')
        with self.assertRaises(ValueError):
            self.b.build('plasma-tide')
        self.assertIsNone(self.b.incus.instance('plasma-tide'))
