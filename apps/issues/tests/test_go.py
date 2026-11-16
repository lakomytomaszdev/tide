"""Istniejące okno zlecenia: decyzja o przeczytanej wersji i osobne retry."""
import unittest

from . import test_view as fixture


SCENARIO = '''
backend.selectIssue('S-2')
calls, replies = live_script()
backend._capabilities.update(['issue.go', 'issue.retry-start'])
issue = dict(state()['issue'])
current_start = dict(intentId='intent', orderId='order', attemptId='attempt', startVersion=3,
                     state='start-error', retryable=True)
accepted_issue = dict(issue, state='accepted', version=2, card='K-8')
preview = dict(ok=True, snapshot=dict(projects=[dict(slug='plasma-tide', name='Tide', repo='/project',
    repoMissing=False, tool='claude')], cards=[dict(id='K-8', title=issue['title'],
    project='plasma-tide', description=issue['report'], tags=[], projects=[])]), card='K-8',
    defaults=dict(entry='planning', mode='light', executor='Sonnet', preferredProvider='anthropic'),
    branches=dict(ok=True, mainRef='refs/heads/master', branches=[dict(ref='refs/heads/master',
        name='master', commit='a' * 40, selectable=True)], warnings=[]))
models = dict(ok=True, executors=[dict(name='Sonnet', program='claude', provider='anthropic', model='sonnet'),
    dict(name='Opus', program='claude', provider='anthropic', model='opus')])
decision_reply = dict(ok=True, issue=accepted_issue, accepted=True, card='K-8', acceptedVersion=1,
    started=False, queued=False, startError=dict(code='failure'), start=current_start)
def transport(method, args, callback, *unused):
    calls.append([method, *args])
    if method == 'PreviewIssueLaunch': result = preview
    elif method == 'Status': result = models
    elif method in ('AcceptIssue', 'RetryIssueStart'): result = decision_reply
    elif method == 'Show': result = dict(ok=True, issue=accepted_issue, start=current_start)
    else: result = dict(ok=True)
    # Odpowiedź rejestrowana jak w live_script: settle() czeka na nią zamiast stałej pauzy.
    token = object()
    pending_callbacks.add(token)
    def done():
        try:
            callback(result, b'')
        finally:
            pending_callbacks.discard(token)
    QTimer.singleShot(1, done)
backend._call = transport
'''


class GoViewTest(unittest.TestCase):
    setUpClass = classmethod(fixture.ViewTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.ViewTest.tearDownClass.__func__)
    run_scenario = fixture.ViewTest.run_scenario

    def test_open_cancel_escape_and_confirm_pin_version_and_reuse_existing_dialog(self):
        out = self.run_scenario(SCENARIO + '''
from PyQt6.QtCore import Qt
obj('issueGo').clicked.emit()
settle()
out['opened'] = obj('issueLaunchDialog').property('visible')
out['mode'] = obj('issueLaunchDialog').property('mode')
out['readOnly'] = obj('launchTask').property('readOnly')
obj('launchCancel').clicked.emit()
wait()
out['cancelled'] = not obj('issueLaunchDialog').property('visible')
out['noAcceptance'] = not any(c[0] == 'AcceptIssue' for c in calls)
obj('issueGo').clicked.emit()
settle()
obj('issueLaunchDialog').forceActiveFocus()
QTest.keyClick(window, Qt.Key.Key_Escape)
wait()
out['escaped'] = not obj('issueLaunchDialog').property('visible')
out['stillNoAcceptance'] = not any(c[0] == 'AcceptIssue' for c in calls)
obj('issueGo').clicked.emit()
settle()
out['canStart'] = obj('launchStart').property('enabled')
obj('launchStart').clicked.emit()
settle()
out['accept'] = next(c for c in calls if c[0] == 'AcceptIssue')
out['current'] = state()['start']
out['retryVisible'] = obj('issueRetryStart').property('visible')
''')
        self.assertTrue(out['opened'] and out['cancelled'] and out['escaped'])
        self.assertEqual(out['mode'], 'light')
        self.assertTrue(out['readOnly'] and out['noAcceptance'] and out['stillNoAcceptance'])
        self.assertTrue(out['canStart'], out)
        import json
        payload = json.loads(out['accept'][2])
        self.assertEqual((payload['destination'], payload['expectedVersion']), ('go', 1))
        self.assertEqual((payload['launch']['entry'], payload['launch']['mode']), ('planning', 'light'))
        self.assertTrue(out['retryVisible'])

    def test_retry_is_a_separate_decision_using_current_show_and_preserves_historical_result(self):
        out = self.run_scenario(SCENARIO + '''
backend._shown('S-2', dict(ok=True, issue=accepted_issue, start=current_start), discard=True)
backend._buffers['S-2']['operation'] = decision_reply
backend.changed.emit()
obj('issueRetryStart').clicked.emit()
wait()
out['noRetryBeforeConfirmation'] = not any(c[0] == 'RetryIssueStart' for c in calls)
engine.rootContext().setContextProperty('retryPopup', obj('issueRetryDialog'))
js('retryPopup.close()')
wait()
out['noRetryOnCancel'] = not any(c[0] == 'RetryIssueStart' for c in calls)
obj('issueRetryStart').clicked.emit()
wait()
# Odpowiedź wcześniejszej operacji nadal zawiera błąd, lecz aktualny Show już sukces.
current_start = dict(current_start, state='started', retryable=False, startVersion=5)
obj('issueRetryDialog').accepted.emit()
settle()
out['calls'] = calls
out['operation'] = obj('issueOperationResult').property('text')
out['current'] = obj('issueCurrentStart').property('text')
out['retryHidden'] = not obj('issueRetryStart').property('visible')
''')
        self.assertTrue(out['noRetryBeforeConfirmation'] and out['noRetryOnCancel'])
        import json
        retry = next(c for c in out['calls'] if c[0] == 'RetryIssueStart')
        payload = json.loads(retry[2])
        self.assertEqual(set(payload), {'intentId', 'expectedStartVersion', 'requestId'})
        self.assertEqual(payload['expectedStartVersion'], 3)
        self.assertFalse(any(c[0] == 'AcceptIssue' for c in out['calls']))
        self.assertIn('start-error', out['operation'])
        self.assertIn('started', out['current'])
        self.assertTrue(out['retryHidden'])

    def test_report_change_during_dialog_cannot_accept_new_version(self):
        out = self.run_scenario(SCENARIO + '''
obj('issueGo').clicked.emit()
settle()
new = dict(issue, state='draft', version=2, report='Nowszy raport')
backend._shown('S-2', dict(ok=True, issue=new))
out['keptVersion'] = state()['issue']['version']
decision_reply = dict(ok=False, error='version-conflict', currentVersion=2, message='Konflikt wersji')
obj('launchStart').clicked.emit()
settle()
out['accept'] = next(c for c in calls if c[0] == 'AcceptIssue')
out['conflict'] = state()['conflict']
out['report'] = state()['issue']['report']
out['noRetry'] = not any(c[0] == 'RetryIssueStart' for c in calls)
''')
        import json
        self.assertEqual(json.loads(out['accept'][2])['expectedVersion'], 1)
        self.assertEqual(out['keptVersion'], 1)
        self.assertEqual(out['conflict'], 2)
        self.assertTrue(out['noRetry'])
