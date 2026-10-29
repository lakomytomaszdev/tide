"""PT-72: zewnętrzny przepływ karty na atrapie REST YouTracka."""
import json
import os

from .fake_youtrack import FakeYouTrack
from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from . import test_cardflow_plan_integration as _plans


class YouTrackFlowTest(FlowInProcess):
    proposal = _plans.PlanFlowTest.proposal
    submit = _plans.PlanFlowTest.submit
    approve = _plans.PlanFlowTest.approve

    def setUp(self):
        super().setUp()
        self.fake = FakeYouTrack('test-token')
        self.addCleanup(self.fake.stop)
        token = os.path.join(self.base, 'token')
        with open(token, 'w') as fh:
            fh.write('test-token')
        with open(os.path.join(self.base, 'config/tide/youtrack.json'), 'w') as fh:
            json.dump({'url': self.fake.url, 'tokenFile': token}, fh)
        reply = json.loads(self.board.upsert_project(json.dumps({'repo': self.repo, 'youtrack': 'LOG',
                                                               'youtrackSync': True})))
        self.assertTrue(reply['ok'], reply)

    def test_approved_plan_creates_issues_and_durable_map(self):
        card = self.card()
        path = self.proposal(card)
        with open(path, encoding='utf-8') as fh:
            proposal = fh.read().replace('tracker: local',
                                         'tracker: youtrack\ntracker-project: LOG\ntracker-url: ' + self.fake.url)
            proposal = proposal.replace('ticket-prefix: PT', 'ticket-prefix: LOG')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(proposal)
        self.assertTrue(self.submit(card, path)['ok'])
        reply = self.approve(card)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['map'], {'#1': 'LOG-1', '#2': 'LOG-2'})
        self.assertIn('Wynik 1', self.fake.issues['LOG-1']['description'])
        self.assertEqual(self.approve(card)['map'], reply['map'])
        self.assertEqual(len(self.fake.issues), 2)
        self.wait_column(card, 'review')
        self.assertTrue(wait_until(lambda: len(self.fake.comments) == 2, timeout=10))
        from .test_cardflow_dbus import git
        approved = self.of(card, 'plan.approved')[0]
        branch = self.results(card, 'card-branch')[0]['branch']
        saved = git(self.repo, 'show', f"{branch}:{approved['planFile']}")
        for expected in ('tracker: youtrack', 'tracker-project: LOG', 'ticket-prefix: LOG'):
            self.assertIn(expected, saved)

    def test_lost_create_reply_with_delayed_index_survives_restart_without_duplicate(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.flow.stop()
        self.fake.lose_replies = 1
        self.fake.search_delay_after_create = 3
        reply = self.flow.engine.approve_plan(card)
        self.assertTrue(reply['queued'], reply)
        self.assertEqual(len(self.fake.issues), 1)
        before = len(self.fake.gets())
        self.assertTrue(self.flow.engine.approve_plan(card)['queued'])
        self.assertEqual(len(self.fake.gets()), before, 'odczyt czeka na termin ponowienia')
        shown = self.flow.engine.derived({'id': card, 'column': 'todo'})
        self.assertIn('potwierdzenie', shown['note'])
        self.fake.hidden_searches = 100
        self.flow = self.make_flow()
        self.flow.stop()
        clock = [self.flow.engine.clock() + 31]
        self.flow.engine.tracker.clock = lambda: clock[0]
        for _ in range(3):
            before = len(self.fake.gets())
            reply = self.flow.engine.approve_plan(card)
            self.assertGreater(len(self.fake.gets()), before, 'po terminie ponawiamy odczyt')
            self.assertTrue(reply['queued'], reply)
            self.assertEqual(len(self.fake.issues), 1)
            clock[0] += 31
        self.fake.search_delay_after_create = 0
        self.fake.hidden_searches = 0
        reply = self.flow.engine.approve_plan(card)
        self.assertEqual(reply['map'], {'#1': 'LOG-1', '#2': 'LOG-2'})
        self.assertEqual(len(self.fake.issues), 2)

    def test_offline_approval_is_recovered_by_worker_after_restart(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.fake.status = 503
        reply = self.approve(card)
        self.assertTrue(reply['queued'], reply)
        self.fake.status = 200
        self.restart()
        self.flow.engine.tracker.clock = lambda: 10**12
        self.assertTrue(wait_until(lambda: self.of(card, 'plan.approved'), timeout=10))
        self.assertEqual(len(self.fake.issues), 2)

    def test_now_creates_linked_issue_and_one_result_comment(self):
        self.fake.add('LOG-5')
        self.fake.lose_comment_replies = 1
        card = self.card()
        self.assertTrue(json.loads(self.board.update_card(card, json.dumps({'youtrack': 'LOG-5'})))['ok'])
        reply = self.start(card)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['ticket'], 'LOG-6')
        self.assertEqual(reply['map'], {'#1': 'LOG-6'})
        self.wait_column(card, 'review')
        self.assertTrue(wait_until(lambda: self.fake.comments.get('LOG-6'), timeout=10))
        self.restart()
        self.flow.engine.tracker.clock = lambda: 10**12
        self.assertTrue(wait_until(lambda: self.results(card, 'youtrack-note'), timeout=10))
        self.assertEqual(len(self.fake.comments['LOG-6']), 1)
        text = self.fake.comments['LOG-6'][0]['text']
        for expected in ('Atrapa zrobiła ticket.', 'Gałąź: ticket/log-6', 'Review: APPROVE', 'Wykonawca: Sonnet'):
            self.assertIn(expected, text)
        self.assertEqual(self.fake.links, [('LOG-6', 'relates to LOG-5')])

    def test_offline_now_resumes_without_second_launch(self):
        card = self.card()
        self.fake.status = 503
        reply = self.start(card)
        self.assertTrue(reply['queued'], reply)
        self.fake.status = 200
        self.restart()
        self.flow.engine.tracker.clock = lambda: 10**12
        self.wait_column(card, 'review')
        self.assertEqual(len(self.fake.issues), 1)
        self.assertEqual(len([s for s in self.of(card, 'step.started') if s['kind'] == 'ticket']), 1)

    def test_journal_tickets_override_plan_and_keep_manual_youtrack_change(self):
        from tide_daemon.board import ytsync
        card = self.card(description='ATRAPA: powoli 2')
        reply = self.start(card)
        self.assertTrue(reply['ok'], reply)
        issue = reply['ticket']
        snapshot = json.loads(self.board.snapshot_json())
        plans = [{'plan': {'tracker': 'youtrack'}, 'tickets': [{'id': issue, 'state': 'gotowe'}]}]
        wants = ytsync.compute_wants(snapshot, plans, {'sync': True}, {})
        self.assertEqual(wants[issue].key, 'progress')
        self.fake.human_sets(issue, 'Open')
        client = ytsync.Client(timeout=1)
        now = [self.fake.clock_ms / 1000 + 1000]
        sync = ytsync.SyncEngine(path=os.path.join(self.base, 'yt-state.json'), client=client,
                                settings_loader=lambda: {'sync': True, 'stableMinutes': 0}, clock=lambda: now[0])
        sync.observe(snapshot, plans)
        now[0] += 10
        sync.observe(snapshot, plans)
        sync.process(sync.due())
        self.fake.human_sets(issue, 'To be discussed')
        # Nowa zmiana dziennika (scalenie) konkuruje z ręcznym stanem zgłoszenia.
        self.wait_column(card, 'review')
        snapshot = json.loads(self.board.snapshot_json())
        sync.observe(snapshot, plans)
        now[0] += 10
        sync.observe(snapshot, plans)
        sync.process(sync.due())
        self.assertEqual(self.fake.state(issue), 'To be discussed')
        self.assertEqual(sync.status(issue)['status'], 'conflict')
