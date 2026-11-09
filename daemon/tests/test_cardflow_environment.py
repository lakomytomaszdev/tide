"""Pakiet zgód (PT-172, karta K-38): sekcja „Środowisko karty”, `ApproveCardEnvironment`, `BoxStatus`, pole `boxWait`
i pytanie o domenę po odmowie proxy.

Silnik pracuje na prawdziwej tablicy i atrapie `claude -p` (FlowInProcess); dziennik karty składamy z publicznych
zdarzeń. Budowa pudełka idzie przez atrapę `box_launcher` (prawdziwy builder i Incus nie są potrzebne), a plan
zatwierdza atrapa `approve_plan` — jego własne testy są w test_cardflow_plans.py.
"""
import json
import os
import unittest
from unittest import mock

from tide_daemon.box import builder as bb
from tide_daemon.box import profile as bp
from tide_daemon.box import state as bs
from tide_daemon.box.settings import BoxSettings
from tide_daemon.cardflow import boxwait, envconsent
from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_decisions import Events

SLUG = "proj"
BOX_YAML = "tide-box: 1\nnetwork:\n  domains:\n    - api.example.org\n"
RED_YAML = "tide-box: 1\nnetwork:\n  domains:\n    - github.com\n    - api.example.org\n"
RED_ACCEPTED = RED_YAML + "accepted_red:\n  - github.com\n"


class EnvBase(FlowInProcess):
    def setUp(self):
        super().setUp()
        self.boxboard = os.path.join(self.base, "boxboard")
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.boxboard})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = self.flow.engine
        # PT-290 (D1): oczekiwanie i budowa tylko przy włączonych pudełkach; te testy opisują włączone pudełko.
        # Zachowanie przy `box.enabled: false` — test_cardflow_env_consent_switch.py.
        self.engine.box_settings_source = lambda: BoxSettings(enabled=True)


class EnvEngineTest(EnvBase):
    """Silnik wołany bezpośrednio (monitor kroków zatrzymany), dziennik składany ręcznie."""

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.launched, self.planned = [], []
        self.engine.box_launcher = lambda slug, profile, action: self.launched.append((slug, profile, action))
        self.engine.approve_plan = lambda card: (self.planned.append(card), {"ok": True, "id": card})[1]
        self.card_id = self.card(description="Dodaj eksport.")
        self.propose()

    def propose(self, box=None, reviewed=True):
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", entry="plan", executor="Sonnet", program="claude",
                       provider="anthropic")
        rows = [{"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
                 "class": "standard", "note": ""}]
        journal.append(self.card_id, "plan.proposed", title="Plan", prefix="PT", rows=rows, planner="Opus",
                       box=box)
        if reviewed:
            journal.append(self.card_id, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])

    def state(self):
        return fold(self.engine.journal.read(self.card_id))

    def section(self):
        out = self.engine.derived({"id": self.card_id, "column": "todo"})
        return out["flow"]["plan"]["environment"]

    def approve(self, text, only_plan=False):
        return self.engine.approve_environment(self.card_id, text, only_plan)

    def by_id(self, ident):
        return next(i for i in self.section()["items"] if i["id"] == ident)

    def box_files(self):
        return [os.path.join(d, f) for d, _s, files in os.walk(self.boxboard) for f in files if f == "box.yaml"]

    def test_pack_approval_copies_only_after_red_consent(self):
        from tide_daemon.box import packs
        path = os.path.join(self.base, "session.jsonl")
        with open(path, "w") as fh:
            fh.write("dziennik sesji")
        card = {"tide-box": 1, "packs": [{"name": "logs", "from": path}]}
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(self.base, "data")}):
            self.assertFalse(self.approve(bp.dump(card))["ok"])
            self.assertFalse(os.path.exists(packs.root(SLUG, self.card_id)))
            card["accepted_red"] = ["pack:logs"]
            self.assertTrue(self.approve(bp.dump(card))["ok"])
            devices = packs.devices(SLUG, self.card_id, card["packs"])
            self.assertTrue(devices[0].readonly)
            self.assertNotEqual(devices[0].source, path)
            self.assertEqual(bp.load_card(SLUG, self.card_id)["packs"], card["packs"])

    def test_secret_pack_cannot_be_approved_even_with_red_consent(self):
        from tide_daemon.box import packs
        path = os.path.join(self.base, "secret.jsonl")
        with open(path, "w") as fh:
            fh.write("ghp_hidden-value")
        card = {"tide-box": 1, "packs": [{"name": "logs", "from": path}], "accepted_red": ["pack:logs"]}
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(self.base, "data")}):
            result = self.approve(bp.dump(card))
            self.assertFalse(result["ok"])
            row = next(i for i in result["items"] if i["id"] == "pack:logs")
            self.assertTrue(row["blocked"])
            self.assertEqual(row["pack"]["hits"], [{"path": path, "rule": "ghp_"}])
            self.assertFalse(os.path.exists(packs.root(SLUG, self.card_id)))
            self.assertEqual(self.box_files(), [])

    # --- sekcja w planie ------------------------------------------------------------------------

    def test_plan_section_classifies_items_from_the_service_and_keeps_red_off(self):
        self.repropose({"tide-box": 1, "network": {"domains": ["api.example.org", "github.com", "10.20.1.5"],
                                                   "host-ollama": True}, "packs": ["dane"], "box": "per-card",
                        "docker": {"enabled": True, "images": ["postgres:16"]}, "ports": {"pool": [4300, 4399]}})
        section = self.section()
        self.assertEqual(section["state"], "build")
        self.assertEqual(section["stateLabel"], "do zbudowania")
        risks = {i["id"]: (i["risk"], i["token"], i["needsConsent"], i["enabled"]) for i in section["items"]}
        self.assertEqual(risks["api.example.org"], ("yellow", "waiting", False, True))
        self.assertEqual(risks["github.com"], ("red", "negative", True, False))
        self.assertEqual(risks["10.20.1.5"], ("red", "negative", True, False))
        self.assertEqual(risks["pack:dane"], ("red", "negative", True, False))
        self.assertEqual(risks["host-ollama"][0], "yellow")
        self.assertEqual(risks["docker"][0], "yellow")
        self.assertEqual(risks["image:postgres:16"][0], "yellow")
        self.assertEqual(risks["ports"][0], "green")
        self.assertEqual(risks["box:per-card"], ("green", "positive", False, True))
        self.assertEqual(self.by_id("box:per-card")["toggle"], envconsent.PER_CARD)
        self.assertEqual(section["acceptKey"], "accepted_red")

    def repropose(self, box):
        """Nowa propozycja planu z rozszerzeniem profilu (dziennik karty jest tylko do dopisywania)."""
        self.engine.journal.append(self.card_id, "plan.proposed", title="Plan", prefix="PT", rows=[
            {"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
             "class": "standard", "note": ""}], planner="Opus", box=box)
        self.engine.journal.append(self.card_id, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])

    def test_per_card_defaults_to_the_project_profile(self):
        bp.save_project(SLUG, {"tide-box": 1, "box": "per-card"})
        self.assertTrue(self.by_id("box:per-card")["enabled"])
        bp.save_project(SLUG, {"tide-box": 1, "box": "per-project"})
        self.assertFalse(self.by_id("box:per-card")["enabled"])

    def test_docker_item_shows_k1_risk_from_the_proposal_and_k3_state(self):
        bp.save_project(SLUG, {"tide-box": 1, "stacks": ["docker"],
                               "docker": {"enabled": True, "images": ["testcontainers/ryuk:0.11.0"]}})
        note = self.by_id("docker")["note"]
        self.assertIn("Ryzyko K1", note)
        self.assertIn("K3 aktywne", note)
        self._drop_cache()
        with mock.patch.dict("tide_daemon.box.k3.K3_PROGRAMS", {"claude": True, "codex": False}):
            self.assertIn("K3 nieaktywne", self.by_id("docker")["note"])

    def test_project_without_profile_and_pattern_gets_one_plan_question(self):
        section = self.section()
        self.assertEqual(section["question"], "Czego potrzebuje środowisko?")
        self.assertTrue(section["newProject"])
        with open(os.path.join(self.repo, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "x", "scripts": {"build": "tsc"}, "devDependencies": {}}, fh)
        self._drop_cache()
        self.assertEqual(self.section()["question"], "")

    def _drop_cache(self):
        self.engine._env_cache.clear()

    def test_internal_address_in_project_proposal_is_rejected_with_message(self):
        os.makedirs(os.path.join(self.repo, ".tide"))
        with open(os.path.join(self.repo, ".tide", "box.yaml"), "w", encoding="utf-8") as fh:
            fh.write("tide-box: 1\nnetwork:\n  domains:\n    - 10.20.1.5\n")
        notes = " ".join(self.section()["notes"])
        self.assertIn("adres wewnętrzny", notes)
        self.assertIn("10.20.1.5", notes)

    def test_rebuild_state_shows_item_difference_against_clean_snapshot(self):
        first = {"tide-box": 1, "stacks": ["docker"], "docker": {"enabled": True, "images": ["postgres:16"]}}
        bp.save_project(SLUG, first)
        recipe = envconsent._stamp(first)
        name = bs.box_name(SLUG)
        bs.record(SLUG, "box.approved", name, source="ui", accepted_red=[], items=["docker", "image:postgres:16"],
                  recipe=recipe)
        bs.record(SLUG, "box.ready", name, hash=recipe, duration=1.0)
        section = self.section()
        self.assertEqual(section["state"], "clean")
        self.assertRegex(section["stateLabel"], r"^clean \d{4}-\d\d-\d\d$")
        bp.save_project(SLUG, {**first, "docker": {"enabled": True, "images": ["postgres:17"]}})
        section = self.section()
        self.assertEqual((section["state"], section["stateLabel"]), ("rebuild", "do przebudowy"))
        self.assertEqual(section["diff"], {"added": ["image:postgres:17"], "removed": ["image:postgres:16"]})

    # --- ApproveCardEnvironment: walidacja w usłudze --------------------------------------------

    def assert_rejected(self, text, expected):
        result = self.approve(text)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "invalid-environment")
        self.assertIn(expected, result["message"])
        self.assertEqual(self.box_files(), [], "profil zapisany mimo błędu")
        self.assertEqual(bs.events(SLUG), [], "zdarzenie box.approved mimo błędu")
        self.assertEqual((self.launched, self.planned), ([], []), "budowa albo plan mimo błędu")
        self.assertEqual(self.state().box_wait, "")
        return result

    def test_unknown_key_wrong_type_and_unaccepted_red_item_are_rejected_without_save_or_build(self):
        self.assert_rejected("tide-box: 1\nmagic: true\n", "magic: nieznany klucz")
        self.assert_rejected("tide-box: 1\napt: 5\n", "apt: oczekiwano listy")
        result = self.assert_rejected(RED_YAML, "pozycja czerwona 'github.com' wymaga jawnego zaznaczenia")
        shown = {i["id"]: i for i in result["items"]}
        self.assertTrue(shown["github.com"]["needsConsent"] and not shown["github.com"]["enabled"])

    def test_accepted_item_that_is_not_red_or_not_in_profile_is_rejected(self):
        self.assert_rejected(BOX_YAML + "accepted_red:\n  - api.example.org\n", "nie jest czerwona")
        self.assert_rejected(BOX_YAML + "accepted_red:\n  - github.com\n", "'github.com'")

    def test_garbage_yaml_and_a_card_key_set_by_hand_are_rejected(self):
        self.assert_rejected("to nie jest: [yaml", "YAML")
        self.assert_rejected("tide-box: 1\nproject: ZLA NAZWA\n", "project")

    def test_internal_address_needs_consent_even_in_a_hand_edited_card_profile(self):
        self.assert_rejected("tide-box: 1\nnetwork:\n  domains:\n    - 10.20.1.5\n", "'10.20.1.5'")

    # --- ApproveCardEnvironment: zapis, zdarzenie, budowa --------------------------------------

    def test_approval_saves_project_and_card_records_event_clears_wait_and_builds_in_background(self):
        result = self.approve(RED_ACCEPTED)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["build"], result["acceptedRed"], result["boxWait"]), ("build", ["github.com"], ""))
        project = bp.load_project(SLUG)
        self.assertEqual(project["project"], SLUG)
        self.assertNotIn("accepted_red", project)
        card = bp.load_card(SLUG, self.card_id)
        self.assertEqual(card["accepted_red"], ["github.com"])
        self.assertIn("api.example.org", card["network"]["domains"])
        [approved] = [e for e in bs.events(SLUG) if e["type"] == "box.approved"]
        self.assertEqual((approved["source"], approved["accepted_red"], approved["card"]), ("ui", ["github.com"], "K-1"))
        self.assertIn("github.com", approved["items"])
        self.assertEqual([(s, a) for s, _p, a in self.launched], [(SLUG, "build")])
        self.assertEqual(self.planned, [self.card_id])
        self.assertEqual(self.state().box_wait, "")

    def test_only_plan_approves_the_plan_and_waits_for_environment_without_saving_or_building(self):
        result = self.approve(BOX_YAML, only_plan=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["onlyPlan"], result["boxWait"]), (True, "environment"))
        self.assertEqual(self.planned, [self.card_id])
        self.assertEqual(self.state().box_wait, "environment")
        self.assertEqual(boxwait.card_box_wait(self.state()), "environment")
        self.assertEqual((self.box_files(), self.launched, bs.events(SLUG)), ([], [], []))
        flow = self.engine.derived({"id": self.card_id, "column": "todo"})["flow"]
        self.assertEqual((flow["boxWait"], flow["boxWaitLabel"]), ("environment", "czeka na środowisko"))
        # zatwierdzenie środowiska później czyści pole i zleca budowę
        result = self.approve(BOX_YAML)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.state().box_wait, "")
        self.assertEqual([a for _s, _p, a in self.launched], ["build"])

    def test_only_plan_still_validates_the_yaml(self):
        result = self.approve("tide-box: 1\nmagic: 1\n", only_plan=True)
        self.assertFalse(result["ok"])
        self.assertEqual((self.planned, self.state().box_wait), ([], ""))

    def test_up_to_date_box_is_not_rebuilt_and_a_changed_recipe_is_rebuilt(self):
        self.assertEqual(self.approve(BOX_YAML)["build"], "build")
        project = bp.load_project(SLUG)
        bs.record(SLUG, "box.ready", bs.box_name(SLUG), hash=envconsent._stamp(project), duration=1.0)
        self.assertEqual(self.approve(BOX_YAML)["build"], "")
        bp.save_project(SLUG, {**project, "apt": ["xvfb"]})
        self.assertEqual(self.approve(BOX_YAML)["build"], "rebuild")

    def test_template_red_item_is_added_to_the_card_only_with_explicit_consent(self):
        item = {"kind": "domain", "value": "nuget.firma.example", "risk": "red", "description": "rejestr", "source": "x"}
        with mock.patch.object(envconsent.environment, "_proposal",
                               return_value=("z szablonów Tide", {"tide-box": 1, "stacks": []}, [item], "")):
            self.assertFalse(self.by_id("nuget.firma.example")["enabled"])
            result = self.approve(BOX_YAML + "accepted_red:\n  - nuget.firma.example\n")
        self.assertTrue(result["ok"], result)
        card = bp.load_card(SLUG, self.card_id)
        self.assertIn("nuget.firma.example", card["network"]["domains"])

    # --- boxWait ---------------------------------------------------------------------------------

    def test_card_box_wait_reads_state_dict_and_derived_in_one_place(self):
        self.assertEqual(boxwait.card_box_wait(self.state()), "")
        boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.REBUILD)
        self.assertEqual(boxwait.card_box_wait(self.state()), "rebuild")
        self.assertEqual(boxwait.card_box_wait({"boxWait": "start"}), "start")
        flow = self.engine.derived({"id": self.card_id, "column": "todo"})["flow"]
        self.assertEqual(boxwait.card_box_wait({"derived": {"flow": flow}}), "rebuild")
        self.assertEqual(boxwait.card_box_wait({"boxWait": "dziwne"}), "")
        self.assertEqual(boxwait.VALUES, ("environment", "start", "rebuild"))
        self.assertIsNone(boxwait.set_box_wait(self.engine.journal, self.card_id, boxwait.REBUILD))
        boxwait.set_box_wait(self.engine.journal, self.card_id, "")
        self.assertEqual(boxwait.card_box_wait(self.state()), "")
        with self.assertRaises(ValueError):
            boxwait.set_box_wait(self.engine.journal, self.card_id, "cokolwiek")

    # --- BoxStatus -------------------------------------------------------------------------------

    def test_box_status_returns_state_hash_nic_filtering_and_box_wait_of_project_cards(self):
        other = self.card(title="Druga")
        self.engine.journal.append(other, "card.started", entry="now", executor="Sonnet", program="claude",
                                   provider="anthropic")
        boxwait.set_box_wait(self.engine.journal, other, boxwait.START)

        class Builder:
            def __init__(self):
                self.incus = mock.Mock(project='user-1000')
                self.incus.operations.return_value = []
                self.incus.list.return_value = [{'name': SLUG, 'status': 'Running'}]

            def status(self, slug):
                return bs.BoxStatus(slug=slug, box=bs.box_name(slug), state=bs.CLEAN, exists=True, running=True,
                                    nic_filtering=True, recipe_hash="abc", profile_hash="abc")

        self.engine.box_builder = Builder
        out = self.engine.box_status(SLUG)
        self.assertEqual((out["state"], out["recipeHash"], out["profileHash"], out["nicFiltering"]),
                         ("clean", "abc", "abc", True))
        self.assertEqual(out["nicLabel"], "filtrowanie NIC: tak")
        self.assertEqual(out['admission']['owner']['name'], SLUG)
        self.assertEqual(out['admission']['incusProject'], 'user-1000')
        self.assertEqual({c["card"]: c["boxWait"] for c in out["cards"]}, {self.card_id: "", other: "start"})
        Builder.status = lambda self, slug: bs.BoxStatus(slug=slug, box="x", state=bs.NONCOMPLIANT)
        self.assertEqual(self.engine.box_status(SLUG)["nicLabel"], "filtrowanie NIC: nie")
        with self.assertRaises(Exception):
            self.engine.box_status("../zly")


class MonitorPathTest(EnvBase):
    """Droga monitora (ta sama, którą woła adaptor D-Bus): odpowiedź JSON, zapisy w szeregowym wątku usługi."""

    def call(self, method, *args):
        box = []
        getattr(self.board, method)(*args, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=30), f"{method} bez odpowiedzi")
        return json.loads(box[0])

    def test_approve_and_status_answer_with_json_through_the_monitor(self):
        launched = []
        self.engine.box_launcher = lambda slug, profile, action: launched.append((slug, action))
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        bad = self.call("approve_card_environment", card_id, "tide-box: 1\nmagic: 1\n", False)
        self.assertEqual((bad["ok"], bad["error"]), (False, "invalid-environment"))
        self.assertEqual(launched, [])
        only = self.call("approve_card_environment", card_id, "tide-box: 1\n", True)
        self.assertEqual((only["ok"], only["boxWait"]), (True, "environment"))
        status = self.call("box_status", SLUG)
        self.assertEqual([(c["card"], c["boxWait"]) for c in status["cards"]], [(card_id, "environment")])
        self.assertEqual(self.call("box_status", "nie-ma")["error"], "no-project")
        done = self.call("approve_card_environment", card_id, "tide-box: 1\n", False)
        self.assertTrue(done["ok"], done)
        self.assertEqual(launched, [(SLUG, "build")])
        self.assertEqual(self.call("box_status", SLUG)["cards"][0]["boxWait"], "")


class NetworkQuestionTest(EnvBase):
    """Wynik kroku `denied` z `command: "network: <domena>"` (atrapa claude: ATRAPA: siec)."""

    def decide(self, card_id, **request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def asked(self):
        card_id = self.card(description="Pobierz ustawy.\n\nATRAPA: siec")
        self.assertTrue(self.start(card_id)["ok"])
        shown = self.wait_column(card_id, "waiting")
        [decision] = shown["derived"]["flow"]["decisions"]
        return card_id, decision

    def resumed_prompt(self, card_id):
        started = wait_until(lambda: len([s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]) > 1,
                             timeout=30)
        self.assertTrue(started, self.events(card_id))
        step = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"][1]
        call = os.path.join(step["stepDir"], "out", "fake-call.json")
        self.assertTrue(wait_until(lambda: os.path.isfile(call), timeout=20))
        with open(call, encoding="utf-8") as fh:
            return json.load(fh)["prompt"]

    def test_denied_network_becomes_a_question_with_two_options_and_no_recommendation(self):
        card_id, decision = self.asked()
        self.assertEqual(decision["kind"], "permission")
        self.assertIn("api.sejm.gov.pl", decision["happened"])
        self.assertEqual([(o["id"], o["recommended"]) for o in decision["options"]],
                         [("box-allow", False), ("deny", False)])
        self.assertEqual([o["title"] for o in decision["options"]],
                         ["Dopisz do profilu karty i przebuduj", "Nie, wznów bez"])
        self.assertFalse(decision["freeText"])
        self.assertEqual(self.of(card_id, "step.decision"), [], "Tide nie odmawia sam")

    def test_first_option_adds_domain_to_card_profile_requests_rebuild_and_resumes(self):
        card_id, decision = self.asked()
        option = decision["options"][0]
        self.assertEqual(option["method"], "ResolveCardStep")
        answered = self.decide(card_id, **option["params"])
        self.assertTrue(answered["ok"], answered)
        card = bp.load_card(SLUG, card_id)
        self.assertEqual(card["network"]["domains"], ["api.sejm.gov.pl"])
        [rebuild] = self.of(card_id, "box.rebuild-requested")
        self.assertEqual((rebuild["domain"], rebuild["project"]), ("api.sejm.gov.pl", SLUG))
        self.assertIn("dopisał tę domenę do profilu karty", self.resumed_prompt(card_id))
        envconsent.add_card_domain(SLUG, card_id, "api.sejm.gov.pl")      # dopisanie jest idempotentne
        self.assertEqual(bp.load_card(SLUG, card_id)["network"]["domains"], ["api.sejm.gov.pl"])

    def test_second_option_resumes_with_denial_and_leaves_the_profile_alone(self):
        card_id, decision = self.asked()
        self.assertTrue(self.decide(card_id, **decision["options"][1]["params"])["ok"])
        self.assertIsNone(bp.load_card(SLUG, card_id))
        self.assertEqual(self.of(card_id, "box.rebuild-requested"), [])
        self.assertIn("nie dopisał domeny", self.resumed_prompt(card_id))


class RecipeStampAgreementTest(unittest.TestCase):
    """Pakiet zgód (PT-172) i budowa (PT-179) liczą ten sam hash przepisu — jedno źródło wersji agentów."""

    def setUp(self):
        import tempfile
        self.config = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.config, True)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile = bp.parse(BOX_YAML, "project")

    def test_default_settings_give_the_same_hash_as_the_builder(self):
        from tide_daemon.box import agents
        from tide_daemon.box import settings as box_settings
        builder = bb.Builder(box_settings.load(), incus=object(), policy=object())
        self.assertEqual(builder.agents_version, agents.AGENTS_VERSION)
        self.assertEqual(builder.stamp(bp.with_defaults(self.profile))["hash"], envconsent._stamp(self.profile))

    def test_settings_override_is_seen_by_both(self):
        from tide_daemon.box import settings as box_settings
        os.makedirs(os.path.join(self.config, "tide"))
        with open(os.path.join(self.config, "tide", "cardflow.json"), "w", encoding="utf-8") as fh:
            json.dump({"box": {"agents-version": "claude-9.9.9"}}, fh)
        settings = box_settings.load()
        self.assertEqual(settings.agents_version, "claude-9.9.9")
        builder = bb.Builder(settings, incus=object(), policy=object())
        self.assertEqual(builder.stamp(self.profile)["hash"], envconsent._stamp(self.profile))

    def test_unreadable_settings_fall_back_to_the_pinned_version(self):
        from tide_daemon.box import agents
        self.assertEqual(bb.agents_version(None), agents.AGENTS_VERSION)


class NetworkDecisionUnitTest(unittest.TestCase):
    def decisions(self, domain):
        events = Events().step("K-1-PT-1-1", result="denied", command=f"network: {domain}", network=domain,
                               canAllow=False)
        return events.decisions()

    def test_red_domain_has_only_the_refusal_and_cannot_be_added_in_a_step(self):
        [decision] = self.decisions("nuget.firma.example")
        self.assertEqual([o["id"] for o in decision["options"]], ["deny"])
        self.assertIn("jawnej zgody w planie", decision["why"])
        with mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": "/nonexistent-box-board"}):
            with self.assertRaises(bp.ProfileError):
                envconsent.add_card_domain(SLUG, "K-1", "nuget.firma.example")

    def test_yellow_domain_has_both_options(self):
        [decision] = self.decisions("api.sejm.gov.pl")
        self.assertEqual([o["id"] for o in decision["options"]], ["box-allow", "deny"])

    def test_network_domain_parses_only_the_documented_form(self):
        self.assertEqual(envconsent.network_domain("network: API.Sejm.gov.pl."), "api.sejm.gov.pl")
        for text in ("", "git fetch origin", "network:", "network: a b", "network: ; rm -rf /", "net: x.org"):
            self.assertEqual(envconsent.network_domain(text), "", text)


if __name__ == "__main__":
    unittest.main()
