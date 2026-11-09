"""PT-72: zewnętrzny przepływ karty na atrapie REST YouTracka."""
import json
import os
import urllib.parse

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

    def test_plan_revision_creates_the_new_ticket_in_youtrack(self):
        # 03.11, K-16: nowy ticket z poprawki planu dostawał lokalny numer zamiast zgłoszenia w YouTracku.
        card = self.card()
        path = self.proposal(card)
        # Pierwszy ticket czeka na odpowiedź, więc drugi nie wystartuje w trakcie poprawki planu.
        ticket_path = os.path.join(os.path.dirname(path), 'tickets', '1.md')
        with open(ticket_path, encoding='utf-8') as fh:
            body = fh.read()
        with open(ticket_path, 'w', encoding='utf-8') as fh:
            fh.write(body.replace('ATRAPA: gotowe', 'ATRAPA: pytanie'))
        with open(path, encoding='utf-8') as fh:
            proposal = fh.read().replace('tracker: local',
                                         'tracker: youtrack\ntracker-project: LOG\ntracker-url: ' + self.fake.url)
            proposal = proposal.replace('ticket-prefix: PT', 'ticket-prefix: LOG')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(proposal)
        # Pierwszy ticket czeka na odpowiedź: poprawka dodaje trzeci bez wyścigu z końcem całego planu.
        ticket_path = os.path.join(os.path.dirname(path), 'tickets', '1.md')
        with open(ticket_path, encoding='utf-8') as fh:
            body = fh.read()
        with open(ticket_path, 'w', encoding='utf-8') as fh:
            fh.write(body.replace('ATRAPA: gotowe', 'ATRAPA: pytanie-bez-pracy'))
        self.assertTrue(self.submit(card, path)['ok'])
        initial = self.approve(card)
        self.assertTrue(initial['ok'], initial)
        self.assertTrue(wait_until(lambda: any(e.get('ticket') == initial['map']['#1']
                                               and e.get('result') == 'question'
                                               for e in self.of(card, 'step.finished')), timeout=15), self.events(card))
        box = []
        self.flow.edit_plan(card, json.dumps({'action': 'return', 'feedback': 'Dodaj ticket z poprawką'}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])['ok'], box)
        self.assertTrue(wait_until(lambda: self.of(card, 'plan.revision-ready'), timeout=25), self.events(card))
        self.assertTrue(wait_until(lambda: len(self.of(card, 'plan.reviewed')) >= 2, timeout=25), self.events(card))
        reply = self.approve(card)
        self.assertTrue(reply['ok'], reply)
        approved = self.of(card, 'plan.revision-approved')[-1]
        self.assertEqual(approved['plan']['map'].get('#3'), 'LOG-3')
        self.assertIn('LOG-3', self.fake.issues)
        self.assertFalse(any(s.get('ticket') == initial['map']['#2'] for s in self.of(card, 'step.started')))

    def test_local_reservation_intent_does_not_block_youtrack_creation(self):
        # 03.11, K-16: lokalna rezerwacja pod tym samym kluczem dawała wieczne „czekam na potwierdzenie”.
        card = self.card()
        journal = self.flow.engine.journal
        journal.append(card, 'card.started', entry='plan', repo=self.repo, trackerProject='LOG', trackerUrl=self.fake.url)
        journal.append(card, 'intent', action='reserve', key='plan:9:#1', repo=self.repo)
        journal.append(card, 'result', action='reserve', key='plan:9:#1', ok=False, error='lokalnie odmówione')
        result = self.flow.engine.tracker.run(card, 'reserve', 'plan:9:#1',
                                              {'op': 'issue', 'url': self.fake.url, 'project': 'LOG', 'number': 1,
                                               'title': 'Nowy', 'description': 'Opis'})
        self.assertEqual(result['ticket'], 'LOG-1')
        self.assertIn('LOG-1', self.fake.issues)

    def test_light_planning_uses_card_youtrack_metadata(self):
        card = self.card()
        reply = self.start(card, entry='planning', mode='light')
        self.assertTrue(reply['ok'], reply)
        started = self.of(card, 'card.started')[0]
        self.assertEqual((started['trackerProject'], started['trackerUrl'], started['prefix']),
                         ('LOG', self.fake.url, 'LOG'))
        step = self.started(card)
        call = os.path.join(step['stepDir'], 'out', 'fake-call.json')
        # atrapa agenta zapisuje plik już po step.started — czekamy, zamiast liczyć na kolejność
        self.assertTrue(wait_until(lambda: os.path.exists(call), timeout=15), self.events(card))
        with open(call, encoding='utf-8') as fh:
            prompt = json.load(fh)['prompt']
        for expected in ('tracker: youtrack', 'tracker-project: LOG',
                         'tracker-url: ' + self.fake.url, 'ticket-prefix: LOG'):
            self.assertIn(expected, prompt)

    def test_lost_create_reply_with_delayed_index_survives_restart_without_duplicate(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.flow.stop()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5),
                        'kończymy tick monitora przed bezpośrednim zatwierdzaniem planu')
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
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5),
                        'po restarcie kończymy już rozpoczęty tick przed ustawieniem zegara testu')
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

    def test_offline_result_comment_does_not_block_next_ticket(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.stop_flow_worker()
        self.assertTrue(wait_until(lambda: not self.flow._ticking, timeout=5),
                        'kończymy tick monitora przed bezpośrednim zatwierdzaniem planu')
        self.flow.engine.approve_plan(card)
        self.approve_environment(card)
        # Wyłącznie komentarze są niedostępne; zgłoszenia i lokalna praca pozostają dostępne.
        self.fake.comment_status = 503
        self.restart()
        self.flow.engine.tracker.clock = lambda: 10**12
        self.wait_column(card, 'review')
        self.assertEqual({s['ticket'] for s in self.of(card, 'step.started') if s['kind'] == 'ticket'},
                         {'LOG-1', 'LOG-2'})
        self.assertTrue(self.of(card, 'youtrack.retry'))
        self.fake.comment_status = 200
        self.flow.engine.tracker.clock = lambda: 10**12 + 60
        self.assertTrue(wait_until(lambda: len(self.fake.comments) == 2, timeout=10))
        self.assertTrue(all(len(items) == 1 for items in self.fake.comments.values()))

    def test_plan_tracker_project_and_server_must_match_card_project(self):
        for tracker_project, tracker_url in (('LEGAL', self.fake.url), ('LOG', self.fake.url + '/other')):
            with self.subTest(project=tracker_project, url=tracker_url):
                card = self.card()
                path = self.proposal(card)
                with open(path, encoding='utf-8') as fh:
                    text = fh.read().replace('tracker: local', 'tracker: youtrack\ntracker-project: ' +
                                             tracker_project + '\ntracker-url: ' + tracker_url)
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(text)
                reply = self.submit(card, path)
                self.assertFalse(reply['ok'], reply)
                self.assertEqual(reply['error'], 'invalid-plan')
                self.assertFalse(self.fake.issues)

    def test_permanent_tracker_error_is_visible_and_retried_only_by_user(self):
        for entry in ('now', 'plan'):
            with self.subTest(entry=entry):
                card = self.card()
                if entry == 'plan':
                    self.assertTrue(self.submit(card, self.proposal(card))['ok'])
                self.fake.status = 400
                reply = self.start(card) if entry == 'now' else self.approve(card)
                self.assertFalse(reply['ok'], reply)
                shown = self.shown(card)
                self.assertEqual(shown['boardColumn'], 'waiting')
                self.assertEqual(shown['derived']['flow']['decisions'][0]['kind'], 'tracker')
                self.assertTrue(shown['derived']['flow']['decisions'][0]['id'].startswith('tracker:'))
                self.assertTrue(shown['derived']['flow']['decisions'][0]['technical']['error'])
                # Komentarz poprzedniej karty może jeszcze ponawiać się w tle.
                # Liczymy wyłącznie operacje z identyfikatorem aktualnej karty.
                def card_calls():
                    return sum(card in urllib.parse.unquote(path) or card in body
                               for _method, path, _auth, body in self.fake.calls)
                count = card_calls()
                self.flow.stop()
                for _ in range(3):
                    self.flow.engine.tick()
                self.assertEqual(card_calls(), count)
                self.fake.status = 200
                result = self.flow.engine.resolve_step(card, {'choice': 'retry'})
                self.assertTrue(result['ok'], result)
                self.flow = self.make_flow()
                if entry == 'plan':
                    self.assertTrue(wait_until(lambda: self.of(card, 'plan.approved'), timeout=10))
                    self.approve_environment(card)
                self.wait_column(card, 'review')

    def test_retry_after_permanent_lookup_error_keeps_uncertain_create_read_only(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.flow.stop()
        self.fake.lose_replies = 1
        self.fake.search_delay_after_create = 100
        self.assertTrue(self.flow.engine.approve_plan(card)['queued'])
        self.assertEqual(len(self.fake.issues), 1)
        self.fake.status = 400
        self.flow.engine.tracker.clock = lambda: 10**12
        with self.assertRaises(FlowError):
            self.flow.engine.approve_plan(card)
        self.assertTrue(self.flow.engine.resolve_step(card, {'choice': 'retry'})['ok'])
        self.fake.status = 200
        reply = self.flow.engine.approve_plan(card)
        self.assertTrue(reply['queued'], reply)
        self.assertEqual(len(self.fake.issues), 1)

    def test_permanent_create_rejection_can_be_retried_after_project_is_fixed(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))['ok'])
        self.fake.projects = {'OTHER'}
        self.assertFalse(self.approve(card)['ok'])
        self.assertTrue(self.of(card, 'intent'))
        self.assertFalse(self.fake.issues)
        self.fake.projects = {'LOG'}
        self.assertTrue(self.flow.engine.resolve_step(card, {'choice': 'retry'})['ok'])
        self.assertTrue(wait_until(lambda: self.of(card, 'plan.approved'), timeout=10))
        self.approve_environment(card)
        self.wait_column(card, 'review')
        self.assertEqual(len(self.fake.issues), 2)
