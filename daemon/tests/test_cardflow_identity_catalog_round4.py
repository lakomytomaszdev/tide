"""Regresje M1–M3 review PT-288: rzeczywiste kontrole kompletności na zmienionym AST."""
import ast
import io
import unittest

from daemon.tests import cardflow_identity_catalog as cat
from daemon.tests import test_cardflow_identity_catalog as checks


class CatalogRound4(unittest.TestCase):
    def test_dynamic_update_unpack_cannot_hide_explicit_field(self):
        path = 'cardflow/engine.py'
        tree = cat.link(ast.parse((cat.PACKAGE / path).read_text()))
        call = next(n for n in ast.walk(tree) if cat._is_card_append(n)
                    and cat.qualname(n) == 'FlowEngine._guard_baseline'
                    and any(k.arg is None and ast.unparse(k.value) == 'base' for k in n.keywords))
        statement = call
        while not isinstance(statement, ast.stmt):
            statement = statement._parent
        block = next(value for _, value in ast.iter_fields(statement._parent)
                     if isinstance(value, list) and statement in value)
        block.insert(block.index(statement), ast.parse('base.update(**{"pid": 123})').body[0])
        with cat.overridden({path: tree}):
            catalog = cat.writes()
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertEqual(catalog.unresolved, [])
        self.assertTrue(any('.pid bez klasyfikacji' in p for p in problems))

    def test_static_payload_alias_cannot_hide_field(self):
        path = 'cardflow/review4_probe.py'
        source = '''def save(journal):
    extra = {'step': 'x'}
    alias = extra
    alias['pid'] = 123
    journal.append('K-38', 'outside.finished', **extra)
'''
        with cat.overridden({path: ast.parse(source)}):
            catalog = cat.writes()
            problems = checks.mismatches(catalog, checks.model_view())
        self.assertEqual(catalog.unresolved, [])
        self.assertTrue(any(path in p and '.pid bez klasyfikacji' in p for p in problems))

    def test_bound_append_cannot_hide_new_writer(self):
        sources = {}
        for method in ('append', 'append_complete'):
            for binding in ('append = journal.METHOD',
                            'log = journal; append: object = log.METHOD; save = append',
                            'forward(journal.METHOD)'):
                path = f'cardflow/escape_{len(sources)}.py'
                sources[path] = ast.parse('def write(journal):\n    '
                                         + binding.replace('METHOD', method)
                                         + "\n    append('K-38', 'outside.finished', step='x', pid=123)\n")
        with cat.overridden(sources):
            catalog = cat.writes()
        for path in sources:
            with self.subTest(path=path):
                self.assertTrue(any(u[0] == path and 'poza wywołaniem' in u[3]
                                    for u in catalog.unresolved))

    def test_named_kind_cannot_hide_new_reader(self):
        path = 'cardflow/review4_probe.py'
        source = '''OUTSIDE_STARTED = 'outside.started'
def current_unit(event):
    if event.get('type') == OUTSIDE_STARTED:
        return event['unit']
'''
        with cat.overridden({path: ast.parse(source)}):
            names = ['test_every_raw_evidence_read_is_a_consumer_or_justified',
                     'test_every_delegation_from_code_has_a_barrier_on_its_path',
                     'test_every_ambiguous_call_to_a_consumer_has_a_receiver_rule',
                     'test_report_matches_catalog']
            result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite(
                checks.ConsumerCatalog(n) for n in names))
            readers = {(c.path, c.function) for c in cat.raw_readers()}
        self.assertIn((path, 'current_unit'), readers)
        self.assertFalse(result.wasSuccessful(), 'nowy odczyt musi odrzucić kontrola kompletności')
        self.assertEqual(result.errors, [])
        self.assertTrue(any('current_unit' in failure for _, failure in result.failures))

    def test_payload_aliases_and_nested_updates_preserve_known_keys(self):
        for binding in ('alias = extra', 'first = extra; alias: dict = first',
                        'alias = first = extra', 'alias, other = extra, 0'):
            for change in ('alias["pid"] = 123', 'alias["pid"] += 1', 'alias.setdefault("pid", 123)',
                           'alias.update(**{"pid": 123})', 'alias |= {"pid": 123}',
                           'patch = {"pid": 123}; alias.update(**{**patch})'):
                with self.subTest(binding=binding, change=change):
                    tree = cat.link(ast.parse('def save():\n    extra = {"step": "x"}\n    '
                                             + binding + '\n    ' + change + '\n    return extra\n'))
                    value = tree.body[0].body[-1].value
                    resolver = cat.Resolver(cat.Catalog())
                    self.assertEqual(resolver.keys('cardflow/probe.py', 'save', value), {'step', 'pid'})
                    # Ta sama analiza jawnych pól służy także wpisom DYNAMIC.
                    self.assertEqual(cat.literal_keys(value), {'step', 'pid'})

    def test_static_alias_with_unknown_source_or_key_is_unresolved(self):
        for setup in ('alias = payload; alias["pid"] = 123',
                      'extra = {"step": "x"}; alias = extra; alias[key] = 123',
                      'extra = {"step": "x"}; alias = extra; alias.setdefault(key, 123)',
                      'extra = {"step": "x"}; alias = extra; alias.update(**payload)'):
            with self.subTest(setup=setup):
                tree = cat.link(ast.parse('def save(payload, key):\n    ' + setup + '\n    return alias\n'))
                with self.assertRaises(cat.Unresolved):
                    cat.Resolver(cat.Catalog()).keys('cardflow/probe.py', 'save', tree.body[0].body[-1].value)

    def test_non_journal_bound_append_is_not_an_escape(self):
        tree = cat.link(ast.parse('def save():\n    items = []\n    append = items.append\n'))
        self.assertFalse(any(cat._card_append_escapes(n) for n in ast.walk(tree)))

    def test_named_kinds_in_collections_imports_and_local_aliases(self):
        source = '''from .kinds_probe import START as IMPORTED
from . import kinds_probe as kinds
START = 'outside.started'
CHAIN: str = START
KINDS = ('outside.started', 'outside.finished')
'''
        expressions = ('START', 'CHAIN', 'IMPORTED', 'kinds.START', 'KINDS', '(START, IMPORTED)', 'alias')
        for expression in expressions:
            with self.subTest(expression=expression):
                path = 'cardflow/reader_probe.py'
                tree = ast.parse(source + 'def read(event):\n    local = START\n    alias: str = local\n'
                                 + f'    if event["type"] in {expression}:\n        return event["unit"]\n')
                with cat.overridden({path: tree, 'cardflow/kinds_probe.py': ast.parse("START = 'outside.started'")}):
                    reads = cat.raw_reads(tree.body[-1], path)
                self.assertIn('outside.started', reads)
