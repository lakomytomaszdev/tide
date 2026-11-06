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
wait()
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
