"""Link źródłowej sprawy pozostaje osobny od opisu i specyfikacji karty."""
from . import test_view as base
from .decisionfix import full_c


class SourceIssueViewTest(base.ProbeCase):
    def test_source_report_links_in_panel_and_full_card(self):
        data = full_c(self.data)
        card = next(c for c in data['cards'] if c['id'] == 'K-8')
        card.update(sourceIssue='S-7', sourceIssueVersion=4)
        out = self.probe(base.FIND_PY + '''
navigation = []
view.navigateRequested.connect(lambda view_id, item: navigation.append([view_id, item]))
js('page.openCard("K-8")')
wait()
out['panelText'] = obj('cardSourceIssue').property('text')
obj('cardSourceIssue').clicked.emit()
js('page.closePanel(); page.openFull("K-8")')
wait()
# PT-502: „Raport i dowody” jest pozycją menu ⋯ paska górnego (z Repeatera — szukana w drzewie elementów).
out['fullText'] = js(FIND + "(page.cardFullView, 'fullSourceIssue').text")
js(FIND + "(page.cardFullView, 'fullSourceIssue').clicked()")
out['navigation'] = navigation
''', data)
        self.assertIn('S-7', out['panelText'])
        self.assertIn('wersja 4', out['fullText'])
        self.assertEqual(out['navigation'], [['issues', 'S-7'], ['issues', 'S-7']])
