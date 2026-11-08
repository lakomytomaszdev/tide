"""Sondy kompletności PT-288; zmiany wyłącznie AST, bez edycji produktu."""
import ast
import io
import unittest

from daemon.tests import cardflow_identity_catalog as cat
from daemon.tests import test_cardflow_identity_catalog as checks


class CompletenessProbes(unittest.TestCase):
    def test_qualified_import_and_chained_journal_aliases(self):
        sources = {
            'box/qualified_probe.py': "import tide_daemon.box.state\ndef save():\n    tide_daemon.box.state.record('proj', 'box.use', 'proj', step='x', pid=123)\n",
            'cardflow/chained_probe.py': "def save(journal):\n    log = journal\n    alias: object = log\n    alias.append_complete('K-38', 'outside.finished', step='x', pid=123)\n",
        }
        with cat.overridden({path: ast.parse(source) for path, source in sources.items()}):
            catalog = cat.writes()
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertEqual(catalog.unresolved, [])
        for path in sources:
            with self.subTest(path=path):
                self.assertTrue(any(w.path == path and 'pid' in w.fields for w in catalog.writes))
                self.assertTrue(any(path in p and '.pid bez klasyfikacji' in p for p in problems))

    def test_non_journal_append_is_not_a_writer(self):
        tree = cat.link(ast.parse("def save():\n    items = []\n    alias = items\n    alias.append('x')\n"))
        self.assertFalse(any(cat._is_card_append(n) for n in ast.walk(tree)))

    def test_static_payload_updates_resolve_keys_or_refuse(self):
        changes = ('extra.setdefault("pid", 123)', 'extra.update(**{"pid": 123})',
                   'extra |= {"pid": 123}', 'extra.setdefault(key, 123)')
        for change in changes:
            source = f"def save(journal, key):\n    extra = {{'step': 'x'}}\n    {change}\n    journal.append('K-38', 'outside.finished', **extra)\n"
            path = 'cardflow/payload_probe.py'
            with cat.overridden({path: ast.parse(source)}):
                catalog = cat.writes()
                problems = checks.mismatches(catalog, checks.model_view())
            with self.subTest(change=change):
                if 'key,' in change:
                    self.assertTrue(any(u[0] == path for u in catalog.unresolved))
                else:
                    self.assertEqual(catalog.unresolved, [])
                    self.assertTrue(any(path in p and '.pid bez klasyfikacji' in p for p in problems))

    def test_module_import_of_box_record_cannot_disappear(self):
        source = "import tide_daemon.box.state as bs\ndef save():\n    bs.record('proj', 'box.use', 'proj', step='x', pid=123)\n"
        with cat.overridden({'box/review_probe.py': ast.parse(source)}):
            catalog = cat.writes()
            found = [w for w in catalog.writes if w.path == 'box/review_probe.py']
            unresolved = [u for u in catalog.unresolved if u[0] == 'box/review_probe.py']
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertTrue(found or unresolved, 'zapis przez import modułu całkowicie pominięty')
        self.assertTrue(any('.pid bez klasyfikacji' in p for p in problems), problems)

    def test_alias_of_card_journal_cannot_disappear(self):
        source = "from .journal import Journal\ndef save(journal: Journal):\n    log = journal\n    log.append('K-38', 'outside.finished', step='x', pid=123)\n"
        with cat.overridden({'cardflow/review_probe.py': ast.parse(source)}):
            catalog = cat.writes()
            found = [w for w in catalog.writes if w.path == 'cardflow/review_probe.py']
            unresolved = [u for u in catalog.unresolved if u[0] == 'cardflow/review_probe.py']
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertTrue(found or unresolved, 'alias zapisującego dziennika pominięty')
        self.assertTrue(any('.pid bez klasyfikacji' in p for p in problems), problems)

    def test_new_setdefault_field_in_existing_static_payload_is_seen(self):
        path = 'cardflow/engine.py'
        tree = cat.link(ast.parse((cat.PACKAGE / path).read_text()))
        calls = [n for n in ast.walk(tree) if cat._is_card_append(n)
                 and cat.qualname(n) == 'FlowEngine.resolve_step'
                 and any(k.arg is None and ast.unparse(k.value) == 'extra' for k in n.keywords)]
        self.assertTrue(calls)
        call = calls[0]
        statement = call
        while not isinstance(statement, ast.stmt):
            statement = statement._parent
        parent = statement._parent
        block = next(value for _, value in ast.iter_fields(parent)
                     if isinstance(value, list) and statement in value)
        block.insert(block.index(statement), ast.parse('extra.setdefault("pid", 123)').body[0])
        with cat.overridden({path: tree}):
            catalog = cat.writes()
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertTrue(any('.pid bez klasyfikacji' in p for p in problems), 'nowe pole nie powoduje porażki')

    def test_type_read_through_local_variable_is_catalogued(self):
        path = 'cardflow/review_probe.py'
        source = "def current_unit(events):\n    for event in events:\n        kind = event.get('type')\n        if kind == 'outside.started':\n            return event['unit']\n"
        with cat.overridden({path: ast.parse(source)}):
            found = [(c.path, c.function) for c in cat.raw_readers()]
            names = ['test_every_raw_evidence_read_is_a_consumer_or_justified']
            case = next(c for c in vars(checks).values() if isinstance(c, type)
                        and issubclass(c, unittest.TestCase) and hasattr(c, names[0]))
            result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite(case(n) for n in names))
            self.assertFalse(result.wasSuccessful(), 'kontrola kompletności musi odrzucić nowy odczyt')
            self.assertEqual(len(result.errors), 0)
            self.assertIn('current_unit', result.failures[0][1])
        self.assertIn((path, 'current_unit'), found, 'nowy konsument nie ma wpisu ani odmowy')

    def test_chained_and_unpacked_type_aliases_are_readers(self):
        for binding in ("kind = event['type']; alias = kind", "kind, ticket = event.get('type'), ''; alias = kind"):
            source = f"def read(event):\n    {binding}\n    if alias == 'outside.started':\n        return event['unit']\n"
            function = ast.parse(source).body[0]
            with self.subTest(binding=binding):
                self.assertEqual(cat.raw_reads(function), {'outside.started'})

