"""PT-460: moduły z listy nie zależą od engine.py — bezpośrednio ani przechodnio (RAPORT.md §3).

Import w funkcji liczy się jak każdy inny, wzmianka o `engine.py` w docstringu nie (ImportGraph czyta AST).
Przy porażce komunikat pokazuje najkrótszy łańcuch importów moduł → … → engine.py.
Kolejne karty odsprzęgania (#2, #3) dopisują swoje moduły do ENGINE_FREE_MODULES oraz do importu niżej.
"""
from pathlib import Path
import tempfile
import unittest

from tools.test_dependencies import ENGINE, ImportGraph, import_chain

# Statyczny import każdego sprawdzanego modułu: dobór testów (make test-changed) wybiera ten moduł,
# gdy któryś z nich się zmieni. Nie zastępować importlib z nazwą ze zmiennej — to nierozstrzygalny import.
from tide_daemon import cardflow
from tide_daemon.cardflow import (
    basechange, basegeneration, baseproposal, basesuggestions, boxaccess, boxcontrol, boxstep, boxwait,
    caretaker, caretakerchat, caretakerctx, consent, decisions, envconsent, environment, errors, evidence,
    execution, faults, filesuggestions, findings, fullcard, gitops, guard, handoff, heavyqueue, history,
    historyaudit, historyaudit_driver, hostingcli, journal, labels, limits, linksuggestions, mergecheck,
    mergeop, mergepreview, planappend, planartifacts, planediting, planning, plans, processidentity,
    projectconsent, queueview, rest, reviewgate, riskview, startorder, state, stepnotes, stepqueue,
    stepresults, targetprep, testpolicy, testrerun, ticketentry, ticketlimit, tracker, trialhold, waiting)

ROOT = Path(__file__).resolve().parents[2]

# PT-461: lista z wyniku narzędzia (python3 tools/test_dependencies.py) po wyniesieniu wyjątków do errors.py:
# wszystkie moduły cardflow/ poza engine.py i rollback.py. rollback.py importuje FlowEngine (punkt wejścia
# narzędzia wiersza poleceń), więc sięga silnika z założenia: rollback.py → engine.py.
IMPORTED_MODULES = (
    cardflow, basechange, basegeneration, baseproposal, basesuggestions, boxaccess, boxcontrol, boxstep, boxwait,
    caretaker, caretakerchat, caretakerctx, consent, decisions, envconsent, environment, errors, evidence,
    execution, faults, filesuggestions, findings, fullcard, gitops, guard, handoff, heavyqueue, history,
    historyaudit, historyaudit_driver, hostingcli, journal, labels, limits, linksuggestions, mergecheck,
    mergeop, mergepreview, planappend, planartifacts, planediting, planning, plans, processidentity,
    projectconsent, queueview, rest, reviewgate, riskview, startorder, state, stepnotes, stepqueue,
    stepresults, targetprep, testpolicy, testrerun, ticketentry, ticketlimit, tracker, trialhold, waiting)
ENGINE_FREE_MODULES = tuple(sorted(Path(module.__file__).resolve().relative_to(ROOT).as_posix()
                                   for module in IMPORTED_MODULES))


def engine_dependency_chains(graph, modules, engine=ENGINE):
    """Moduły z listy, które zależą od silnika, z najkrótszym łańcuchem importów."""
    chains = {}
    for module in modules:
        chain = import_chain(graph, module, engine)
        if chain:
            chains[module] = chain
    return chains


def assert_engine_free(case, graph, modules, engine=ENGINE):
    unknown = [module for module in modules if module not in graph.dependencies]
    case.assertFalse(unknown, f"Brak modułów w grafie importów: {unknown}")
    chains = engine_dependency_chains(graph, modules, engine)
    case.assertFalse(chains, "Moduły nie mogą zależeć od engine.py:\n" +
                     "\n".join(f"  {module}: " + " → ".join(chain) for module, chain in chains.items()))


class EngineBoundaryTest(unittest.TestCase):
    def test_listed_modules_do_not_depend_on_engine(self):
        # Bez katalogów testów: reguła dotyczy tylko kodu usługi, a graf powstaje wielokrotnie szybciej.
        assert_engine_free(self, ImportGraph(ROOT, []), ENGINE_FREE_MODULES)

    def test_list_covers_every_cardflow_module_except_engine_and_rollback(self):
        # Nowy moduł cardflow/ musi trafić na listę albo świadomie zostać poza nią (wtedy dopisz go tu z łańcuchem).
        every = {p.relative_to(ROOT).as_posix() for p in (ROOT / "daemon/tide_daemon/cardflow").glob("*.py")}
        outside = {ENGINE, "daemon/tide_daemon/cardflow/rollback.py"}
        self.assertEqual(sorted(every - outside), sorted(ENGINE_FREE_MODULES))

    def test_rollback_is_the_only_module_reaching_engine(self):
        graph = ImportGraph(ROOT, [])
        every = sorted(p for p in graph.dependencies if p.startswith("daemon/tide_daemon/cardflow/") and p != ENGINE)
        self.assertEqual(sorted(engine_dependency_chains(graph, every)),
                         ["daemon/tide_daemon/cardflow/rollback.py"])


class EngineBoundaryLogicTest(unittest.TestCase):
    """Logika sprawdzenia na grafie fixture w katalogu tymczasowym."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source("engine", "")
        self.source("__init__", "")

    def source(self, name, body):
        file = self.root / "daemon/tide_daemon/cardflow" / (name + ".py")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
        return "daemon/tide_daemon/cardflow/" + name + ".py"

    def graph(self):
        return ImportGraph(self.root, [])

    def test_clean_module_and_docstring_mention_pass(self):
        clean = self.source("clean", "import json\n")
        mention = self.source("mention", '"""Silnik (`engine.py`) podaje dane: from tide_daemon.cardflow import engine."""\n'
                                         "# import engine\n")
        assert_engine_free(self, self.graph(), [clean, mention])

    def test_direct_import_fails_with_chain(self):
        bad = self.source("bad", "from tide_daemon.cardflow import engine\n")
        with self.assertRaises(AssertionError) as caught:
            assert_engine_free(self, self.graph(), [bad])
        self.assertIn(f"{bad} → {ENGINE}", str(caught.exception))

    def test_transitive_and_in_function_import_fail_with_chain(self):
        lazy = self.source("lazy", "def load():\n    from . import engine\n    return engine\n")
        middle = self.source("middle", "from . import lazy\n")
        top = self.source("top", "from . import middle\n")
        with self.assertRaises(AssertionError) as caught:
            assert_engine_free(self, self.graph(), [top])
        self.assertIn(" → ".join([top, middle, lazy, ENGINE]), str(caught.exception))

    def test_unknown_module_fails(self):
        with self.assertRaises(AssertionError) as caught:
            assert_engine_free(self, self.graph(), ["daemon/tide_daemon/cardflow/missing.py"])
        self.assertIn("missing.py", str(caught.exception))

    def test_only_offending_modules_are_reported(self):
        good = self.source("good", "")
        bad = self.source("bad", "import tide_daemon.cardflow.engine\n")
        self.assertEqual(engine_dependency_chains(self.graph(), [good, bad]), {bad: [bad, ENGINE]})


if __name__ == "__main__":
    unittest.main()
