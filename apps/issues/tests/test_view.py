"""Zainstalowany widok Qt: obrazy, niezaufany Markdown, metadane, konflikty i brak skutków podglądu."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from widgets.stage import stage_app
from .fixture import data

REPO = Path(__file__).resolve().parents[3]
PROBLEMS = r"TypeError|ReferenceError|is not a type|is not defined|Cannot read|Invalid property|Binding loop|Unable to assign|unavailable"


class ViewTest(unittest.TestCase):
    def test_pending_refresh_cannot_replace_report_while_acceptance_is_in_flight(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script(callback_delay=10)
issue = dict(state()['issue'])
new = dict(issue, version=2, report='Nowa wersja', state='draft')
replies.extend([dict(ok=False, error='version-conflict', currentVersion=2, message='Konflikt'), dict(ok=True, issue=new)])
backend.acceptIssue(1)
# Odczyt odświeżenia rozpoczął się przed kliknięciem, lecz kończy po wysłaniu decyzji.
backend._shown('S-2', dict(ok=True, issue=new))
settle()
out['state'] = state()
out['request'] = calls[0]
''')
        self.assertEqual(out['state']['issue']['version'], 1)
        self.assertEqual(out['state']['conflict'], 2)
        self.assertEqual(out['state']['current']['report'], 'Nowa wersja')
        self.assertEqual(json.loads(out['request'][2])['expectedVersion'], 1)

    def test_accept_button_pins_rendered_version_and_retries_same_request(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script()
issue = dict(state()['issue'])
out['ready'] = obj('issueAccept').property('visible') and obj('issueAccept').property('enabled')
replies.append(dict(ok=False, error='unavailable', message='Utracona odpowiedź'))
js('view.editing = false')
obj('issueAccept').clicked.emit()
settle()
out['first'] = calls[-1]
replies.append(dict(ok=True, issue=dict(issue, state='accepted', version=2, card='K-8'), card='K-8'))
obj('issueAccept').clicked.emit()
settle()
out['second'] = calls[-1]
out['accepted'] = state()['issue']
out['hidden'] = not obj('issueAccept').property('visible')
out['link'] = obj('issueCardLink').property('text')
navigation = []
view.navigateRequested.connect(lambda view_id, item: navigation.append([view_id, item]))
obj('issueCardLink').clicked.emit()
out['navigation'] = navigation
''')
        self.assertTrue(out['ready'])
        self.assertEqual(out['first'], out['second'])
        self.assertEqual(out['first'][:2], ['AcceptIssue', 'S-2'])
        payload = json.loads(out['first'][2])
        self.assertEqual((payload['expectedVersion'], payload['destination']), (1, 'backlog'))
        self.assertTrue(payload['requestId'])
        self.assertTrue(out['hidden'])
        self.assertIn('K-8', out['link'])
        self.assertEqual(out['navigation'], [['board', 'K-8']])

    def test_accept_conflict_keeps_rendered_report_and_cannot_accept_draft_or_dirty(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script()
issue = dict(state()['issue'])
backend.updateDraft('report', 'Niezapisana treść')
backend.acceptIssue(1)
out['dirtyBlocked'] = len(calls) == 0 and not obj('issueAccept').property('enabled')
backend.updateDraft('report', issue['report'])
new = dict(issue, version=2, report='Nowa wersja', state='draft')
replies.extend([dict(ok=False, error='version-conflict', currentVersion=2, message='Konflikt'), dict(ok=True, issue=new)])
obj('issueAccept').clicked.emit()
settle()
out['state'] = state()
out['blocked'] = not obj('issueAccept').property('enabled')
out['request'] = calls[0]
''')
        self.assertTrue(out['dirtyBlocked'] and out['blocked'])
        self.assertEqual(out['state']['issue']['version'], 1)
        self.assertEqual(out['state']['current']['report'], 'Nowa wersja')
        self.assertEqual(json.loads(out['request'][2])['expectedVersion'], 1)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="tide-issue-view-")
        cls.pkg = str(Path(cls.tmp.name) / "pkg")
        stage_app(str(REPO / "apps/tide"), cls.pkg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_scenario(self, code, fixture=None):
        with tempfile.TemporaryDirectory(prefix="tide-issue-scenario-") as tmp:
            Path(tmp, "data.json").write_text(json.dumps(fixture or data()))
            Path(tmp, "scenario.py").write_text(code)
            result = subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name("probe.py")),
                self.pkg, str(Path(tmp, "data.json")), str(Path(tmp, "scenario.py"))],
                env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software"),
                capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIsNone(re.search(PROBLEMS, result.stderr), result.stderr)
            out = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertNotIn("error", out, out)
            return out

    def test_list_filter_open_metadata_and_readable_report(self):
        out = self.run_scenario('''
out['rows'] = js('view.rows.map(i => i.id)')
out['badge'] = backend.badgeText
out['plain'] = obj('issueReport').property('text')
out['images'] = len(backend._provider.images)
obj('issueSearch').setProperty('text', 'S-2')
wait()
out['search'] = js('view.rows.map(i => i.id)')
obj('issueStateFilter').setProperty('currentIndex', 3)
obj('issueSearch').setProperty('text', '')
wait()
out['accepted'] = js('view.rows.map(i => i.id)')
js('view.showItem("S-4")')
wait()
out['selected'] = state()['issue']['id']
out['metadata'] = obj('issueMetadata').property('text')
out['reopen'] = obj('issueReopen').property('visible')
out['save'] = obj('issueSave').property('visible')
''')
        self.assertEqual(out["rows"], ["S-1", "S-2"])
        self.assertEqual(out["search"], ["S-2"])
        self.assertEqual(out["accepted"], ["S-3"])
        self.assertEqual((out["badge"], out["images"]), ("1", 1))
        self.assertIn("<h1", out["plain"])
        self.assertIn('src="image://issue-evidence/', out["plain"])
        self.assertEqual(out["selected"], "S-4")
        self.assertIn("Odrzucona", out["metadata"])
        self.assertTrue(out["reopen"])
        self.assertFalse(out["save"])

    def test_preview_never_writes_reads_files_or_opens_system(self):
        out = self.run_scenario('''
from unittest.mock import patch
with patch.object(module.QDesktopServices, 'openUrl') as opened, patch.object(module, 'read_source') as source:
    backend._call = lambda *a: (_ for _ in ()).throw(AssertionError('D-Bus w podglądzie'))
    backend.updateDraft('title', 'Lokalnie')
    backend.save()
    backend.transition('Submit')
    backend.attach(QUrl.fromLocalFile('/etc/passwd'))
    backend.detach('A-' + '1' * 32)
    backend.openAttachment('A-' + '1' * 32)
    backend.openLink('https://example.org')
    backend.reloadSelected()
    out['opened'] = opened.call_count
    out['source'] = source.call_count
    out['title'] = state()['issue']['title']
    out['dirty'] = state()['dirty']
    out['disabled'] = not obj('issueSave').property('enabled')
''')
        self.assertEqual((out["opened"], out["source"]), (0, 0))
        self.assertEqual(out["title"], "Raport rozmowy")
        self.assertTrue(out["dirty"] and out["disabled"])

    def test_patch_only_changed_fields_null_clear_and_ready_returns_to_draft(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
wait()
calls, replies = live_script()
backend.updateDraft('project', 'plasma-tide')
updated = dict(state()['issue'], version=2, project='plasma-tide', state='draft')
replies.append(dict(ok=True, issue=updated))
backend.save()
wait()
out['assign'] = json.loads(calls[0][2])
out['state'] = state()['issue']['state']
backend.updateDraft('project', '')
replies.append(dict(ok=True, issue=dict(updated, version=3, project=None)))
backend.save()
wait()
out['clear'] = json.loads(calls[-1][2])
out['report'] = state()['issue']['report']
''')
        self.assertEqual(out["assign"], {"expectedVersion": 1, "project": "plasma-tide"})
        self.assertEqual(out["clear"], {"expectedVersion": 2, "project": None})
        self.assertEqual(out["state"], "draft")
        self.assertIn("Brak zrzutu", out["report"])

    def test_conflict_preserves_local_text_and_current_version_until_explicit_reload(self):
        out = self.run_scenario('''
calls, replies = live_script()
backend.updateDraft('report', 'Lokalna treść do zachowania')
current = dict(state()['issue'], report='Nowszy raport', version=2, attachments=[])
replies.extend([dict(ok=False, error='version-conflict', currentVersion=2, message='Konflikt'), dict(ok=True, issue=current)])
backend.save()
wait()
out['conflict'] = state()
out['label'] = obj('issueConflict').property('text')
before = len(calls)
backend.save()
out['blocked'] = len(calls) == before
replies.append(dict(ok=True, issue=current))
backend.reloadSelected()
wait()
out['reloaded'] = state()
''')
        self.assertEqual(out["conflict"]["draft"]["report"], "Lokalna treść do zachowania")
        self.assertEqual(out["conflict"]["current"]["report"], "Nowszy raport")
        self.assertEqual(out["conflict"]["conflict"], 2)
        self.assertIn("aktualna wersja 2", out["label"])
        self.assertTrue(out["blocked"])
        self.assertEqual(out["reloaded"]["draft"]["report"], "Nowszy raport")
        self.assertFalse(out["reloaded"]["dirty"])

    def test_unknown_broken_service_and_data_errors_keep_draft(self):
        fixture = data()
        fixture["errors"] = [{"id": "S-8", "message": "Uszkodzony manifest"}]
        out = self.run_scenario('''
backend.selectIssue('S-99')
out['missing'] = state()['error']
backend.selectIssue('S-2')
calls, replies = live_script()
backend.updateDraft('title', 'Zachowaj')
backend.updateDraft('project', 'nieznany')
replies.append(dict(ok=False, error='bad-request', message='Nieznany projekt'))
backend.save()
settle()
out['state'] = state()
backend._lost()
out['lost'] = state()
''', fixture)
        self.assertIn("S-99", out["missing"])
        self.assertEqual(out["state"]["issue"]["title"], "Raport rozmowy")
        self.assertEqual(out["state"]["draft"]["title"], "Zachowaj")
        self.assertEqual(out["state"]["notice"], "Nieznany projekt")
        self.assertEqual(out["state"]["listErrors"][0]["id"], "S-8")
        self.assertFalse(out["lost"]["available"])
        self.assertEqual(out["lost"]["draft"]["title"], "Zachowaj")

    def test_successful_show_after_read_failure_keeps_local_report_and_unblocks_save(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script()
issue = dict(state()['issue'])
backend.updateDraft('report', 'Lokalny raport po awarii')
replies.append(dict(ok=False, error='unavailable', message='Chwilowa awaria'))
js('view.showItem("S-2")')
wait()
out['failed'] = state()
out['blocked'] = not obj('issueSave').property('enabled')
replies.append(dict(ok=True, issue=issue))
js('view.showItem("S-2")')
wait()
out['recovered'] = state()
out['enabled'] = obj('issueSave').property('enabled')
replies.append(dict(ok=True, issue=dict(issue, report='Lokalny raport po awarii', version=2)))
backend.save()
wait()
out['calls'] = calls
out['saved'] = state()
''')
        self.assertEqual(out['failed']['error'], 'Chwilowa awaria')
        self.assertTrue(out['blocked'])
        self.assertEqual(out['recovered']['error'], '')
        self.assertEqual(out['recovered']['draft']['report'], 'Lokalny raport po awarii')
        self.assertTrue(out['recovered']['dirty'])
        self.assertTrue(out['enabled'])
        self.assertEqual(out['calls'][-1][:2], ['Update', 'S-2'])
        self.assertEqual(json.loads(out['calls'][-1][2]),
                         {'expectedVersion': 1, 'report': 'Lokalny raport po awarii'})
        self.assertEqual(out['saved']['issue']['report'], 'Lokalny raport po awarii')
        self.assertFalse(out['saved']['dirty'])

    def test_successful_show_clears_read_error_without_clearing_version_conflict(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script()
backend.updateDraft('report', 'Lokalny raport')
current = dict(state()['issue'], report='Nowsza treść', version=2)
replies.extend([dict(ok=True, issue=current),
               dict(ok=False, error='unavailable', message='Chwilowa awaria'),
               dict(ok=True, issue=current)])
for _ in range(3):
    js('view.showItem("S-2")')
    wait()
out['recovered'] = state()
before = len(calls)
backend.save()
out['blocked'] = len(calls) == before and not obj('issueSave').property('enabled')
''')
        self.assertEqual(out['recovered']['error'], '')
        self.assertEqual(out['recovered']['draft']['report'], 'Lokalny raport')
        self.assertEqual(out['recovered']['current']['report'], 'Nowsza treść')
        self.assertEqual(out['recovered']['conflict'], 2)
        self.assertTrue(out['blocked'])

    def test_external_selection_cancels_pending_dialogs_without_acting_on_another_issue(self):
        for dialog in ('issueRejectDialog', 'issueDiscardDialog', 'issueDetachDialog', 'issueAttachmentDialog'):
            with self.subTest(dialog=dialog):
                out = self.run_scenario('''
from unittest.mock import patch
dialog_name = %r
discard = dialog_name == 'issueDiscardDialog'
first_report = state()['draft']['report']
if discard:
    backend.updateDraft('report', 'Lokalny raport S-1')
backend.selectIssue('S-2')
second_report = state()['draft']['report']
if discard:
    backend.updateDraft('report', 'Lokalny raport S-2')
backend.selectIssue('S-1')
calls, replies = live_script()
engine.rootContext().setContextProperty('pendingDialog', obj(dialog_name))
js('view.pendingDetach = "A-11111111111111111111111111111111"')
js('pendingDialog.open()')
wait()
out['opened'] = js('pendingDialog.visible')
replies.append(dict(ok=True, issue=dict(backend._issues[1])))
js('view.showItem("S-2")')
settle()
out['closed'] = not js('pendingDialog.visible')
out['selected'] = state()['selected']
calls.clear()
# Opóźnione potwierdzenie, także z natywnego selektora, nie może zmienić nowego celu.
with patch.object(module, 'read_source') as source:
    js('pendingDialog.accepted()')
    settle()
    out['sourceReads'] = source.call_count
out['calls'] = calls[:]
out['draft'] = state()['draft']['report']
out['expectedDraft'] = 'Lokalny raport S-2' if discard else second_report
replies.append(dict(ok=True, issue=dict(backend._issues[0], attachments=[])))
js('view.showItem("S-1")')
settle()
out['firstDraft'] = state()['draft']['report']
out['expectedFirstDraft'] = 'Lokalny raport S-1' if discard else first_report
calls.clear()
with patch.object(module, 'read_source') as source:
    js('pendingDialog.accepted()')
    settle()
    out['staleSourceReads'] = source.call_count
out['staleCalls'] = calls[:]
''' % dialog)
                self.assertTrue(out['opened'])
                self.assertTrue(out['closed'])
                self.assertEqual(out['selected'], 'S-2')
                self.assertEqual(out['calls'], [])
                self.assertEqual(out['sourceReads'], 0)
                self.assertEqual(out['draft'], out['expectedDraft'])
                self.assertEqual(out['firstDraft'], out['expectedFirstDraft'])
                self.assertEqual(out['staleCalls'], [])
                self.assertEqual(out['staleSourceReads'], 0)

    def test_dialog_confirmation_still_acts_on_the_issue_it_was_opened_for(self):
        for dialog, method in (('issueRejectDialog', 'Reject'), ('issueDiscardDialog', 'Show'),
                               ('issueDetachDialog', 'Detach'), ('issueAttachmentDialog', 'Attach')):
            with self.subTest(dialog=dialog):
                out = self.run_scenario('''
from unittest.mock import patch
backend.selectIssue('S-1')
calls, replies = live_script(callback_delay=20)
issue = dict(state()['issue'])
engine.rootContext().setContextProperty('pendingDialog', obj(%r))
js('view.pendingDetach = "A-11111111111111111111111111111111"')
if %r == 'Show':
    backend.updateDraft('report', 'Lokalny szkic')
js('pendingDialog.open()')
wait()
# Ten sam cel może być ponownie otwarty przez CLI bez unieważniania potwierdzenia.
replies.append(dict(ok=True, issue=issue))
js('view.showItem("S-1")')
settle()
out['stillOpen'] = js('pendingDialog.visible')
calls.clear()
replies.append(dict(ok=True, issue=dict(issue, version=2, report='Odczytany raport', attachments=[])))
with patch.object(module, 'read_source', return_value=('dowod.txt', b'dowod')) as source:
    js('pendingDialog.accepted()')
    settle()
    out['sourceReads'] = source.call_count
out['calls'] = [call[:3] for call in calls]
out['report'] = state()['draft']['report']
''' % (dialog, method))
                self.assertTrue(out['stillOpen'])
                self.assertEqual(len(out['calls']), 1, out['calls'])
                self.assertEqual(out['calls'][0][:2], [method, 'S-1'])
                if method == 'Show':
                    self.assertEqual(out['report'], 'Odczytany raport')
                else:
                    self.assertEqual(json.loads(out['calls'][0][2])['expectedVersion'], 1)
                self.assertEqual(out['sourceReads'], int(method == 'Attach'))

    def test_renderer_denies_foreign_images_html_and_provider_paths(self):
        out = self.run_scenario('''
from PyQt6.QtGui import QColor
from PyQt6.QtCore import QSize
malicious = """# Nagłówek
<script>alert(1)</script><img src="file:///etc/passwd"><iframe src="https://bad.test"></iframe>
![sieć](https://bad.test/x.png)
![plik](file:///etc/passwd)
![względny](../../secret.png)
![SVG](attachments/not.svg)
![obcy](image://issue-evidence/../../secret)
![brak](attachments/A-ffffffffffffffffffffffffffffffff)
![zakodowany](attachments/%41-11111111111111111111111111111111)
[zły](javascript:alert)
[dysk](file:///etc/passwd)
[dobry](https://example.org)
"""
out['html'] = backend.renderReport(malicious, QColor('cyan'))
out['provider'] = backend._provider.requestImage('../../etc/passwd', QSize())[0].isNull()
out['svg'] = module.report.decode_image(b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'image/svg+xml').isNull()
''')
        self.assertNotRegex(out["html"], r"<(img|script|iframe)\b")
        self.assertNotRegex(out["html"], r'href="(?:file|javascript):')
        self.assertIn('href="https://example.org"', out["html"])
        self.assertIn("&lt;script&gt;", out["html"])
        self.assertTrue(out["provider"] and out["svg"])

    def test_keyboard_edit_wiring_and_buffers_survive_navigation(self):
        out = self.run_scenario('''
from PyQt6.QtCore import Qt
js('view.editing = true')
wait()
engine.rootContext().setContextProperty('field', obj('issueReportEditor'))
js('field.forceActiveFocus()')
QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
for char in 'Lokalny raport':
    QTest.keyClick(window, char)
wait()
out['typed'] = state()['draft']['report']
engine.rootContext().setContextProperty('titleField', obj('issueTitle'))
js('titleField.forceActiveFocus()')
QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
for char in 'Nowy tytul':
    QTest.keyClick(window, char)
wait()
out['title'] = state()['draft']['title']
backend.selectIssue('S-2')
backend.selectIssue('S-1')
wait()
out['returned'] = state()['draft']
out['dirty'] = state()['dirty']
''')
        self.assertEqual(out['typed'], 'Lokalny raport')
        self.assertEqual(out['title'], 'Nowy tytul')
        self.assertEqual(out['returned']['report'], out['typed'])
        self.assertEqual(out['returned']['title'], out['title'])
        self.assertTrue(out['dirty'])

    def test_lifecycle_uses_version_only_and_dirty_buffer_blocks_actions(self):
        out = self.run_scenario('''
backend.selectIssue('S-2')
calls, replies = live_script()
issue = dict(state()['issue'], attachments=[])
replies.append(dict(ok=True, issue=dict(issue, state='rejected', version=2)))
backend.transition('Reject')
wait()
out['reject'] = calls[-1]
replies.append(dict(ok=True, issue=dict(issue, state='draft', version=3)))
backend.transition('Reopen')
wait()
out['reopen'] = calls[-1]
backend.updateDraft('report', 'lokalnie')
before = len(calls)
backend.transition('Submit')
out['dirtyBlocked'] = len(calls) == before
backend.updateDraft('report', issue['report'])
replies.append(dict(ok=True, issue=dict(issue, state='ready', version=4)))
backend.transition('Submit')
wait()
out['submit'] = calls[-1]
''')
        for method, version in [('reject', 1), ('reopen', 2), ('submit', 3)]:
            call = out[method]
            self.assertEqual(call[:2], [method.capitalize(), 'S-2'])
            self.assertEqual(json.loads(call[2]), {'expectedVersion': version})
        self.assertTrue(out['dirtyBlocked'])

    def test_links_require_activation_and_malicious_schemes_never_open(self):
        out = self.run_scenario('''
from unittest.mock import patch
backend._preview = False
with patch.object(module.QDesktopServices, 'openUrl', return_value=True) as opened:
    wait()
    out['before'] = opened.call_count
    for url in ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,bad', 'ftp://example.org']:
        backend.openLink(url)
    out['bad'] = opened.call_count
    backend.openLink('https://example.org/docs')
    out['after'] = [args.args[0].toString() for args in opened.call_args_list]
''')
        self.assertEqual((out['before'], out['bad']), (0, 0))
        self.assertEqual(out['after'], ['https://example.org/docs'])

    def test_integrity_failures_block_provider_and_newer_version_does_not_erase_text(self):
        fixture = data()
        image = fixture['issues'][0]['attachments'][0]
        image['sha256'] = '0' * 64
        out = self.run_scenario('''
out['images'] = len(backend._provider.images)
out['status'] = list(state()['evidence'].values())
backend.updateDraft('report', 'Wpisany tekst')
new = dict(state()['issue'], version=2, report='Obcy zapis', attachments=[])
backend._shown('S-1', dict(ok=True, issue=new))
out['kept'] = state()['draft']['report']
out['current'] = state()['current']['report']
out['conflict'] = state()['conflict']
''', fixture)
        self.assertEqual(out['images'], 0)
        self.assertIn('Dowód jest niezgodny z manifestem.', out['status'])
        self.assertEqual((out['kept'], out['current'], out['conflict']), ('Wpisany tekst', 'Obcy zapis', 2))

    def test_layout_paints_report_in_staged_view(self):
        target = REPO / 'build' / 'issues-preview.png'
        target.parent.mkdir(exist_ok=True)
        out = self.run_scenario('''
wait(100)
out['saved'] = window.grabWindow().save(%r)
out['height'] = obj('issueReport').property('height')
''' % str(target))
        self.assertTrue(out['saved'])
        self.assertGreater(out['height'], 50)
        self.assertGreater(target.stat().st_size, 15000)

    def test_manifest_path_and_read_identity_are_not_interchangeable(self):
        fixture = data()
        fixture['issues'][0]['attachments'][0]['path'] = '../../secret.png'
        out = self.run_scenario('''
out['images'] = len(backend._provider.images)
out['statuses'] = list(state()['evidence'].values())
issue = state()['issue']
entry = issue['attachments'][1]
raw = b'<script>secret()</script>'
good = dict(ok=True, id=issue['id'], version=issue['version'], attachment=entry)
out['valid'] = bool(module.validate_read(issue, entry, good, raw))
out['foreign'] = bool(module.validate_read(issue, entry, dict(good, id='S-2'), raw))
out['version'] = bool(module.validate_read(issue, entry, dict(good, version=2), raw))
out['bytes'] = bool(module.validate_read(issue, entry, good, b'obce bajty'))
out['entry'] = bool(module.validate_read(issue, entry, dict(good, attachment=dict(entry, name='obcy')), raw))
''', fixture)
        self.assertEqual(out['images'], 0)
        self.assertIn('Niedozwolona ścieżka manifestu.', out['statuses'])
        self.assertTrue(out['valid'])
        for key in ('foreign', 'version', 'bytes', 'entry'):
            self.assertFalse(out[key], key)
