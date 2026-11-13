"""K-113 PT-466: tick bez ponownej oceny prób K-38 w uzgadnianiu sieci pudełek.

- `trialpolicy.blocked_event` — jedno wyrażenie skompilowane przy wczytaniu modułu, ten sam wynik co dawna reguła
  (referencja niżej, przepisana z kodu sprzed zmiany) na korpusie; moduł bez importów pakietu usługi (launcher Konsoli),
- `boxstep.recover_network` — filtr prób karty (zdarzenia bez prób i chronionych tożsamości oraz stan z nich złożony)
  pamiętany według wersji dziennika karty (`Journal.derive_events`, pamięć PT-411/PT-415).

Testy liczą wywołania oceny prób (`trialhold.held`, `blocked_event`) na granicy modułów; czasu nie mierzą."""
import ast
import collections
import contextlib
import json
import os
from pathlib import Path
import random
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon import trialpolicy
from tide_daemon.box import trialgate
from tide_daemon.cardflow import boxstep, trialhold
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks import launch

from .boxflow import BoxFlow, SLUG
from .test_cardflow_tick_reads import TickFixture, _external_event, _lines

TRIALS = sorted(trialpolicy.LEGACY | trialpolicy.EXCLUDED)


def reference_blocked_event(step) -> bool:
    """Dawna reguła (przed K-113 PT-466): osobne `re.search` dla każdego numeru i pola."""
    return str(step.get("ticket") or "").upper() in trialpolicy.LEGACY | trialpolicy.EXCLUDED or any(
        re.search(r"(?<![A-Za-z0-9])" + ticket + r"(?![0-9])", str(step.get(key) or ""), re.I)
        for ticket in trialpolicy.LEGACY | trialpolicy.EXCLUDED for key in ("step", "key", "branch"))


def _variants(number):
    low = number.lower()
    return [number, low, number.title(), number + "a", low + "b", number + "0", number + "9", "X" + number,
            "x" + low, "1" + number, "9" + low, "-" + number, "_" + number, " " + number, number + "-",
            number + "_x", "K-38-" + number + "-1", "ticket/" + low + "-old-proof", "a/" + number + "/b",
            number.replace("-", ""), number.replace("PT-", "PT_"), number[:-1], "PT-" + number[3:] + "1"]


class TrialPolicyEquivalenceTest(unittest.TestCase):
    def corpus(self):
        values = [None, "", " ", 0, 186, "PT-", "PT-1", "PT-17", "PT-187", "PT-1730", "K-38", "PT-274"]
        for number in TRIALS:
            values += _variants(number)
        rng = random.Random(466)
        alphabet = "PTpt-0123456789xX_/ "
        for _ in range(3000):
            number = rng.choice(TRIALS)
            prefix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 3)))
            suffix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 3)))
            body = number.lower() if rng.random() < 0.3 else number
            values.append(prefix + body + suffix)
        for _ in range(500):
            values.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12))))
        return values

    def test_same_result_as_the_previous_rule_for_every_field(self):
        checked = 0
        for value in self.corpus():
            for key in ("ticket", "step", "key", "branch"):
                for step in ({key: value}, {key: value, "other": "PT-186"}):
                    self.assertEqual(trialpolicy.blocked_event(step), reference_blocked_event(step), (key, value))
                    checked += 1
        self.assertEqual(trialpolicy.blocked_event({}), reference_blocked_event({}))
        self.assertGreater(checked, 20000)

    def test_boundaries_named_in_the_card(self):
        for number in TRIALS:
            for key in ("ticket", "step", "key", "branch"):
                with self.subTest(number=number, key=key):
                    self.assertTrue(trialpolicy.blocked_event({key: number}))
                    self.assertTrue(trialpolicy.blocked_event({key: number.lower()}))
                    self.assertFalse(trialpolicy.blocked_event({key: None}))
                    self.assertFalse(trialpolicy.blocked_event({key: ""}))
            for key in ("step", "key", "branch"):
                self.assertTrue(trialpolicy.blocked_event({key: number + "a"}), "sufiks literowy")
                self.assertFalse(trialpolicy.blocked_event({key: number + "0"}), "dłuższy numer")
                self.assertFalse(trialpolicy.blocked_event({key: "X" + number}), "prefiks litera")
                self.assertFalse(trialpolicy.blocked_event({key: "1" + number}), "prefiks cyfra")
            # Pole `ticket` rozstrzyga `blocked` (cały numer), bez wyszukiwania w tekście.
            self.assertFalse(trialpolicy.blocked_event({"ticket": number + "a"}))
        self.assertEqual(trialpolicy.blocked_event({"step": "PT-186a"}), True)
        self.assertEqual(trialpolicy.blocked_event({"step": "PT-1860"}), False)
        self.assertEqual(trialpolicy.blocked_event({"branch": "XPT-186"}), False)
        self.assertEqual(trialpolicy.blocked_event({"key": "1PT-186"}), False)

    def test_one_expression_compiled_when_the_module_loads(self):
        with mock.patch.object(trialpolicy.re, "search", side_effect=AssertionError("re.search per numer")), \
                mock.patch.object(trialpolicy.re, "compile", side_effect=AssertionError("kompilacja w wywołaniu")):
            self.assertTrue(trialpolicy.blocked_event({"step": "K-38-PT-176-1", "branch": "x"}))
            self.assertFalse(trialpolicy.blocked_event({"step": "K-1-PT-1-1", "key": "a", "branch": "ticket/pt-1"}))


class TrialPolicyLauncherTest(unittest.TestCase):
    PATH = Path(trialpolicy.__file__)

    def test_policy_module_imports_no_service_package(self):
        tree = ast.parse(self.PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "import względny pakietu usługi")
                imported.add(node.module or "")
        self.assertEqual(imported, {"re"})

    def test_console_launcher_loads_the_policy_through_runpy(self):
        import runpy
        real = runpy.run_path
        paths = []
        with mock.patch("runpy.run_path", side_effect=lambda path, *a, **k: paths.append(path) or real(path, *a, **k)):
            for number in TRIALS:
                self.assertFalse(launch.trial_console_allowed({"title": "K-38 · " + number}))
                self.assertFalse(launch.trial_console_allowed({"flowTicket": "PT-9", "flowStep": "K-9-" + number + "-1"}))
            self.assertTrue(launch.trial_console_allowed({"flowCard": "K-9", "flowTicket": "PT-9",
                                                          "flowStep": "K-9-PT-1860-1"}))
        self.assertTrue(paths)
        self.assertTrue(all(os.path.realpath(p) == os.path.realpath(self.PATH) for p in paths), paths)
        loaded = real(str(self.PATH))
        self.assertTrue(loaded["blocked_event"]({"branch": "ticket/pt-191-x"}))
        self.assertFalse(loaded["blocked_event"]({"branch": "ticket/pt-1910-x"}))


class Evaluations:
    """Ocena prób (`trialhold.held`, `trialgate.blocked_event`, `trialpolicy.blocked_event`) według karty zdarzenia —
    liczona tylko w trakcie `boxstep.recover_network`."""

    def __init__(self):
        self.held, self.policy = collections.Counter(), collections.Counter()
        self.inside = 0

    @contextlib.contextmanager
    def __call__(self):
        real_held, real_gate, real_policy = trialhold.held, trialgate.blocked_event, trialpolicy.blocked_event
        real_network = boxstep.recover_network

        def card_of(step):
            return step.get("card") if hasattr(step, "get") else None

        def held(step):
            if self.inside:
                self.held[card_of(step)] += 1
            return real_held(step)

        def gate(step):
            if self.inside:
                self.policy[card_of(step)] += 1
            return real_gate(step)

        def policy(step):
            if self.inside:
                self.policy[card_of(step)] += 1
            return real_policy(step)

        def network(*args, **kwargs):
            self.inside += 1
            try:
                return real_network(*args, **kwargs)
            finally:
                self.inside -= 1
        with mock.patch.object(trialhold, "held", side_effect=held), \
                mock.patch.object(trialgate, "blocked_event", side_effect=gate), \
                mock.patch.object(trialpolicy, "blocked_event", side_effect=policy), \
                mock.patch.object(boxstep, "recover_network", side_effect=network):
            yield self

    def cards(self):
        return set(self.held) | set(self.policy)


def _plain(events):
    return [{k: v for k, v in e.items() if k not in ("type", "seq", "v", "ts", "card")} for e in events]


class NetworkMemoryCase(unittest.TestCase):
    """Dwie karty: K-38 z dawną próbą w pudełku (zakończoną) i chronioną tożsamością, K-2 ze zwykłym krokiem pudełka."""

    TRIAL = "K-38-PT-176-1"
    ORDINARY = "K-2-PT-9-1"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.store = SimpleNamespace(root=str(self.root / "board"))
        (Path(self.store.root) / "projects").mkdir(parents=True)
        self.journal = Journal(self.store.root, mirror=str(self.root / "mirror"))
        self.engine = FlowEngine(self.store, journal=self.journal, settings=str(self.root / "settings.json"),
                                 codex_roots=[])
        box = dict(runner="box", box=SLUG, boxSlug=SLUG, boxIp="10.0.0.2")
        append = self.journal.append
        append("K-38", "card.started", repo=str(self.root), project=SLUG)
        append("K-38", "box.ports", step=self.TRIAL, owner=self.TRIAL, ticket="PT-176", box=SLUG, boxSlug=SLUG)
        append("K-38", "step.started", step=self.TRIAL, ticket="PT-176", kind="ticket", unit="old-unit",
               worktree=str(self.root / "PT-176"), stepDir=str(self.root / "PT-176.step"), branch="ticket/pt-176",
               **box)
        append("K-38", "step.finished", step=self.TRIAL, ticket="PT-176", result="done")
        # Chroniona tożsamość: start z numerem próby, a koniec bez numeru (sam `held` zdarzenia go nie rozpozna).
        append("K-38", "step.started", step="old-proof", ticket="PT-186", kind="ticket", unit="proof-unit",
               worktree=str(self.root / "proof"), stepDir=str(self.root / "proof.step"), branch="ticket/proof",
               **box)
        append("K-38", "step.finished", step="old-proof", result="done")
        append("K-2", "card.started", repo=str(self.root), project=SLUG)
        append("K-2", "step.started", step=self.ORDINARY, ticket="PT-9", kind="ticket", unit="u-9",
               worktree=str(self.root / "PT-9"), stepDir=str(self.root / "PT-9.step"), branch="ticket/pt-9", **box)

    def finish_ordinary(self):
        self.journal.append("K-2", "step.finished", step=self.ORDINARY, ticket="PT-9", result="done")

    def external_append(self, card, event=None):
        events = self.journal.read_fresh(card)
        with open(self.journal.mirror_path(card), "a", encoding="utf-8") as fh:
            fh.write(_lines([event or _external_event(events, card)]))
        return events

    @contextlib.contextmanager
    def effects(self, proxy=None):
        """Atrapy skutków uzgadniania: porty, refguard, zasoby kroku i proxy."""
        with mock.patch.object(boxstep, "proxy_of", return_value=proxy), \
                mock.patch.object(boxstep, "release_ports") as ports, \
                mock.patch.object(boxstep, "_release_resources") as resources, \
                mock.patch("tide_daemon.box.refguard.finish", return_value={}) as refs:
            yield SimpleNamespace(ports=ports, resources=resources, refs=refs)

    def network(self, proxy=None):
        """Jeden tick uzgadniania sieci z nową fazą odczytu; zwraca atrapy skutków."""
        phase = trialhold.HistoryPhase(self.journal)
        for card in self.journal.cards_complete():
            with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
                trialhold.read_process_history(self.journal, card, phase)
        with self.effects(proxy) as done:
            boxstep.recover_network(self.engine, history=phase)
        return done

    def without_memory(self, card):
        events = self.journal.read_fresh(card)
        whole = fold(events)
        protected = {n for n, i in whole.process_identities.items() if i.held and isinstance(n, str)}
        kept = [e for e in events if not trialhold.held(e) and e.get("step") not in protected
                and e.get("key") not in protected]
        return kept, fold(kept)


class NetworkMemoryTest(NetworkMemoryCase):
    def test_second_network_reconciliation_without_changes_evaluates_no_trial(self):
        self.network()
        self.engine._box_network_state = None          # także bez skrótu sygnatury: ponownie przez całą pętlę kart
        with Evaluations()() as count:
            self.network()
        self.assertEqual((dict(count.held), dict(count.policy)), ({}, {}))

    def test_append_to_one_card_evaluates_only_that_card_also_from_outside_the_process(self):
        self.network()
        self.network()
        for card in ("K-2", "K-38"):
            with self.subTest(card=card):
                self.external_append(card)
                with Evaluations()() as count:
                    self.network()
                self.assertEqual(count.cards(), {card})
                with Evaluations()() as count:
                    self.network()
                self.assertEqual(count.cards(), set(), "po ponownym wyliczeniu znowu z pamięci")
        self.journal.append("K-2", "card.paused")       # własny zapis
        with Evaluations()() as count:
            self.network()
        self.assertEqual(count.cards(), {"K-2"})

    def test_truncation_or_replacement_of_the_journal_invalidates_only_that_card(self):
        self.external_append("K-38")
        self.network()
        path = self.journal.mirror_path("K-38")
        events = self.journal.read_fresh("K-38")
        with open(path, "w", encoding="utf-8") as fh:            # ucięcie (ostatnie zdarzenie spoza procesu)
            fh.write(_lines(events[:-1]))
        with Evaluations()() as count:
            self.network()
        self.assertEqual(count.cards(), {"K-38"})
        self.assert_matches_fresh("K-38")
        replacement = path + ".new"
        with open(replacement, "w", encoding="utf-8") as fh:     # podmiana pliku tą samą treścią (nowy i-węzeł)
            fh.write(_lines(events[:-1]))
        os.replace(replacement, path)
        with Evaluations()() as count:
            self.network()
        self.assertEqual(count.cards(), {"K-38"})
        self.assert_matches_fresh("K-38")

    def assert_matches_fresh(self, card):
        events, kept, st = trialhold.network_state(self.journal, card)
        fresh_kept, fresh_st = self.without_memory(card)
        self.assertEqual(events, self.journal.read_fresh(card))
        self.assertEqual(kept, fresh_kept)
        self.assertEqual(vars(st), vars(fresh_st))

    def test_memory_key_is_the_journal_version_and_the_value_equals_the_result_without_memory(self):
        for card in ("K-2", "K-38"):
            trialhold.process_state(self.journal, card)
            self.assert_matches_fresh(card)
            entry, version, value = self.journal._derived_slots[trialhold.NETWORK][card]
            self.assertIs(entry, self.journal._strict_memory[card])
            self.assertEqual(version, entry.version)
            self.assertEqual((version.size, version.generation), (entry.version.size, self.journal.generation(card)))
        kept, _ = self.journal._derived_slots[trialhold.NETWORK]["K-38"][2]
        self.assertFalse(any(e.get("step") in (self.TRIAL, "old-proof") for e in kept))

    def test_caller_gets_own_containers(self):
        events, kept, st = trialhold.network_state(self.journal, "K-38")
        events.clear()
        kept.append({"type": "obce"})
        st.steps["obcy"] = {"step": "obcy"}
        st.finished.clear()
        again = trialhold.network_state(self.journal, "K-38")
        fresh_kept, fresh_st = self.without_memory("K-38")
        self.assertEqual(again[0], self.journal.read_fresh("K-38"))
        self.assertEqual(again[1], fresh_kept)
        self.assertEqual(vars(again[2]), vars(fresh_st))

    def test_forget_except_and_own_write_drop_the_network_memory(self):
        for card in ("K-2", "K-38"):
            trialhold.network_state(self.journal, card)
        self.journal.forget_except(["K-2"])
        self.assertEqual(set(self.journal._derived_slots[trialhold.NETWORK]), {"K-2"})
        self.journal.append("K-2", "card.paused")
        self.assertEqual(self.journal._derived_slots[trialhold.NETWORK], {})

    def test_without_phase_the_network_reads_fresh_and_bypasses_the_memory(self):
        trialhold.network_state(self.journal, "K-38")
        before = dict(self.journal._derived_slots[trialhold.NETWORK])
        reads = []
        real = self.journal._read_strict
        with mock.patch.object(self.journal, "_read_strict", side_effect=lambda card: reads.append(card) or real(card)), \
                self.effects():
            boxstep.recover_network(self.engine)
        self.assertEqual(sorted(set(reads)), ["K-2", "K-38"])
        self.assertEqual(self.journal._derived_slots[trialhold.NETWORK], before)

    def test_unreadable_history_or_unknown_identity_is_not_remembered_and_has_no_effects(self):
        self.finish_ordinary()
        self.journal.append("K-2", "box.ports", step="orphan", owner="orphan", box=SLUG, boxSlug=SLUG)
        broken = {
            "unknown": lambda events: _lines([{"v": 1, "seq": len(events) + 1, "ts": "2026-11-13T12:00:00+01:00",
                                               "card": "K-2", "type": "outside.finished", "step": "K-2-PT-1-1",
                                               "ticket": "PT-1", "exit": 0}]),
            "unreadable": lambda events: '{"v": 1, "seq": %d, "card": "K-2", "type": "card.paused"' % (len(events) + 1),
        }
        for name, tail in broken.items():
            with self.subTest(name):
                path = self.journal.mirror_path("K-2")
                with open(path, encoding="utf-8") as fh:
                    good = fh.read()
                self.addCleanup(lambda text=good: Path(path).write_text(text, encoding="utf-8"))
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(tail(self.journal.read_fresh("K-2")))
                proxy = mock.Mock()
                for _tick in range(2):
                    self.engine._box_network_state = None
                    done = self.network(proxy)
                    done.ports.assert_not_called()
                    done.resources.assert_not_called()
                    done.refs.assert_not_called()
                    proxy.set_box_policy.assert_not_called()
                    proxy.clear_box_policy.assert_not_called()
                    self.assertNotIn("K-2", self.journal._derived_slots.get(trialhold.NETWORK, {}))
                    self.assertNotIn("K-2", self.journal._derived)
                Path(path).write_text(good, encoding="utf-8")
        # Naprawiona historia: kolejny tick zwalnia jak dotąd.
        self.engine._box_network_state = None
        done = self.network()
        released = [c.args[1]["step"] for c in done.resources.call_args_list]
        self.assertEqual(released, [self.ORDINARY])
        done.ports.assert_called_once()

    def test_after_a_warm_tick_trials_and_protected_identities_are_still_not_released(self):
        self.finish_ordinary()
        for _tick in range(3):
            self.engine._box_network_state = None
            done = self.network()
            released = [c.args[1]["step"] for c in done.resources.call_args_list]
            self.assertEqual(released, [self.ORDINARY])
            self.assertEqual([c.args[2] for c in done.refs.call_args_list], [self.ORDINARY])
            done.ports.assert_not_called()      # rezerwacja próby K-38 bez wpisu kroku nie jest zwalniana

    def test_history_change_between_phase_and_mutation_is_refused_after_a_warm_tick(self):
        self.finish_ordinary()
        self.network()
        self.engine._box_network_state = None
        phase = trialhold.HistoryPhase(self.journal)
        events = trialhold.read_process_history(self.journal, "K-2", phase)
        trialhold.read_process_history(self.journal, "K-38", phase)
        with open(self.journal.mirror_path("K-2"), "a", encoding="utf-8") as fh:
            fh.write(_lines([_external_event(events, "K-2")]))
        with self.effects() as done:
            boxstep.recover_network(self.engine, history=phase)
        done.resources.assert_not_called()
        done.refs.assert_not_called()
        self.assertIsNone(self.engine._box_network_state, "odmowa: stan sieci nie jest zapamiętany")


class NetworkMemoryTickTest(TickFixture):
    """Prawdziwy `FlowEngine.tick` (wzór testów odczytów ticku): druga karta z dawną próbą K-38 w dzienniku."""

    def trial_card(self):
        append = self.journal.append
        append("K-900", "card.started", project="proj")
        append("K-900", "step.started", step="K-900-PT-176-1", ticket="PT-176", kind="ticket", unit="old-unit",
               worktree="/wt/PT-176", stepDir="/wt/PT-176.step", branch="ticket/pt-176", runner="box", box="proj",
               boxSlug="proj", boxIp="10.0.0.9")
        append("K-900", "step.finished", step="K-900-PT-176-1", ticket="PT-176", result="done")

    def versions(self):
        return {card: self.journal.version(card) for card in self.journal.cards_complete()}

    def settled(self):
        """Ticki aż do pierwszego, który nie zmienił żadnego dziennika (pierwszy tick dopisuje inwentaryzację K-38
        karty z próbą — to zmiana dziennika tej karty, więc kolejny tick słusznie liczy jej filtr od nowa)."""
        for _ in range(4):
            before = self.versions()
            self.engine.tick()
            if self.versions() == before:
                return
        self.fail("tick zmienia dzienniki przy każdym przebiegu")

    def test_second_tick_without_changes_evaluates_no_trial_in_the_network_reconciliation(self):
        self.trial_card()
        self.engine.tick()
        self.assertTrue(any(e["type"] == "k38.inventory" for e in self.journal.read_complete("K-900")))
        self.settled()
        before = self.versions()
        with Evaluations()() as count:
            self.engine.tick()
        self.assertEqual(self.versions(), before, "tick bez zmian dzienników")
        self.assertEqual(count.cards(), set(), (count.held, count.policy))

    def test_append_to_one_card_evaluates_only_that_card_in_the_next_tick(self):
        self.trial_card()
        self.settled()
        events = self.journal.read_complete("K-900")
        with open(self.journal.mirror_path("K-900"), "a", encoding="utf-8") as fh:
            fh.write(_lines([_external_event(events, "K-900")]))
        with Evaluations()() as count:
            self.engine.tick()
        self.assertEqual(count.cards(), {"K-900"})


class DisabledBoxNetworkTest(BoxFlow):
    """`box.enabled=false` nie wyłącza uzgadniania: krok pudełka sprzed wyłączenia dostaje zwolnienie i refguard."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.build_box()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", program="claude",
                                   provider="anthropic", repo=self.repo, project=SLUG)

    def test_finished_box_step_is_released_with_refguard_after_the_box_is_disabled(self):
        from tide_daemon.box import state as bs
        from tide_daemon.cardflow import boxaccess
        from tide_daemon.cardflow.engine import BOX_STEP_KINDS
        name = "K-1-PT-1-1"
        placement = boxstep.place(self.engine, self.card_id, SLUG, "ticket", "", BOX_STEP_KINDS)
        log = boxstep.before_start(self.engine, placement, self.card_id, name)
        self.engine.journal.append(self.card_id, "step.started", step=name, kind="ticket", ticket="PT-1",
                                   unit="tide-step-" + name, stepDir=os.path.join(self.base, name + ".step"),
                                   program="claude", branch="ticket/pt-1", **placement.entry())
        self.assertIn(name, bs.running_steps(SLUG, SLUG))
        self.engine.journal.append(self.card_id, "step.finished", step=name, ticket="PT-1", result="done")
        self.box_enabled = False
        self.assertFalse(boxstep.settings_of(self.engine).enabled)
        calls = []
        real = boxstep.recover_network
        # Bez dalszej obsługi karty (start kroków): test dotyczy uzgadniania sieci ticku.
        with mock.patch("tide_daemon.box.refguard.finish", return_value={}) as refs, \
                mock.patch.object(self.engine, "_tick_card"), \
                mock.patch.object(boxstep, "recover_network",
                                  side_effect=lambda *a, **k: calls.append(k.get("history")) or real(*a, **k)):
            self.engine.tick()
        self.assertTrue(calls and calls[0] is not None, f"tick bez uzgadniania z fazy: {self.engine._warnings}")
        self.assertIn(name, [c.args[2] for c in refs.call_args_list])
        self.assertNotIn(name, bs.running_steps(SLUG, SLUG))
        self.assertNotIn(name, self.proxy.steps)
        with self.assertRaises(FileNotFoundError):
            boxaccess.read(log)


if __name__ == "__main__":
    unittest.main()
