"""K-98 #1 (PT-342): odświeżenie Tablicy z jednym odczytem Board i aktualnym widokiem środowiska (box.enabled=true).

- jedno odświeżenie BoardMonitor wczytuje Tablicę najwyżej raz (licznik na granicy magazynu `BoardStore.load`),
  także z kartami plasma-tide i innych projektów; bez globalnego „bieżącego Board”,
- widok „Środowisko karty” odczytany przez publiczną migawkę (`Snapshot`) po każdej zmianie wejścia jest równy
  świeżo policzonemu modelowi; bez zmian wejść pamięć nie liczy modelu ponownie — także dla plasma-tide,
- pamięć jest wyłącznie prezentacją: operacja ze starą zgodą po rozgrzaniu pamięci jest odrzucona,
- błąd odczytu profilu daje komunikat w sekcji, nie pustą migawkę; przy box.enabled=false karta plasma-tide
  czekająca na środowisko nadal ma sekcję zgody.

Testy nie mierzą czasu (pomiar: `perf_board_refresh.py`), tylko liczniki kosztu i zachowanie.
"""
import copy
import json
import os
from unittest import mock

from tide_daemon.board.readcontext import BoardRead
from tide_daemon.box import exposure as ex
from tide_daemon.box import packs
from tide_daemon.box import profile as bp
from tide_daemon.box import state as bs
from tide_daemon.cardflow import boxwait, envconsent, projectconsent

from .boxflow import SLUG, BoxFlow
from .test_cardflow_dbus import git

PLASMA = ex.PROJECT
ROWS = [{"workId": "#1", "title": "A", "blockers": [], "executor": "Sonnet", "reviewer": "Opus",
         "class": "standard", "note": ""}]


class RefreshContextCase(BoxFlow):
    def setUp(self):
        super().setUp()
        self.flow.stop()      # bez ticku w tle: liczymy wyłącznie odświeżenia
        self.plasma_repo = os.path.join(self.base, "ws", PLASMA)
        os.makedirs(self.plasma_repo)
        git(self.plasma_repo, "init", "-q")
        with open(os.path.join(self.plasma_repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("plasma\n")
        git(self.plasma_repo, "add", "-A")
        git(self.plasma_repo, "commit", "-q", "-m", "init")
        reply = json.loads(self.board.upsert_project(json.dumps({"repo": self.plasma_repo})))
        self.assertTrue(reply["ok"], reply)
        self.assertIn(PLASMA, self.store.load().projects)
        bp.save_project(PLASMA, {"tide-box": 1, "project": PLASMA, "network": {"domains": ["pypi.org"]}})
        self.proj_cards = [self.started(SLUG, f"Proj {i}") for i in range(2)]
        self.plasma_cards = [self.started(PLASMA, f"Plasma {i}") for i in range(2)]
        # Zgoda środowiska zatwierdza też czekający plan (rezerwacja numerów w repozytorium) — tu poza zakresem.
        self.engine.approve_plan = mock.Mock(return_value={})

    def started(self, project, title):
        reply = json.loads(self.board.create_card(json.dumps({"title": title, "project": project,
                                                              "description": "Opis.", "column": "todo"})))
        self.assertTrue(reply["ok"], reply)
        card = reply["id"]
        journal = self.engine.journal
        journal.append(card, "card.started", entry="plan", executor="Sonnet", program="claude", provider="anthropic")
        journal.append(card, "plan.proposed", title="Plan", prefix="PT", rows=ROWS, planner="Opus", box=None)
        journal.append(card, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
        return card

    def refresh(self):
        """Odświeżenie BoardMonitor → (migawka z publicznego `Snapshot`, wczytania Tablicy w odświeżeniu,
        karty, dla których liczono model sekcji)."""
        loads, models = [], []
        real_load, real_model = self.store.load, envconsent.model

        def load(*args, **kwargs):
            loads.append(1)
            return real_load(*args, **kwargs)

        def model(engine, card, st, *rest):
            models.append(card)
            return real_model(engine, card, st, *rest)
        with mock.patch.object(self.store, "load", side_effect=load), \
                mock.patch.object(envconsent, "model", side_effect=model):
            self.board.publish()
        return json.loads(self.board._json), len(loads), models

    @staticmethod
    def card_of(snapshot, card):
        return next(c for c in snapshot["cards"] if c["id"] == card)

    def view(self, snapshot, card):
        flow = self.card_of(snapshot, card)["derived"]["flow"]
        return flow.get("environment") or (flow.get("plan") or {}).get("environment")

    def fresh(self, card):
        """Model sekcji policzony od zera: świeży odczyt Tablicy, bez kontekstu i bez pamięci."""
        return json.loads(json.dumps(envconsent.model(self.engine, card, self.engine.journal.state(card))))

    @staticmethod
    def yaml_of(view):
        from tide_daemon.box import yamlmini
        return yamlmini.loads(view["yaml"])

    def consent_text(self, view, *, accepted):
        return bp.dump({**self.yaml_of(view), projectconsent.ACTION: "approve",
                        projectconsent.EXPECTED: view["exposure"]["expected"], projectconsent.ACCEPTED: accepted})


class SingleReadTest(RefreshContextCase):
    def test_refresh_reads_the_board_at_most_once_for_all_cards(self):
        self.engine._env_cache.clear()
        for label in ("zimne", "ciepłe", "ciepłe 2"):
            with self.subTest(label):
                snapshot, loads, _ = self.refresh()
                self.assertLessEqual(loads, 1, f"{label}: odświeżenie wczytało Tablicę {loads} razy")
                for card in self.proj_cards + self.plasma_cards:
                    self.assertTrue(self.view(snapshot, card), f"{card}: brak sekcji środowiska")
                for card in self.plasma_cards:
                    self.assertTrue(self.view(snapshot, card)["projectExposure"])
                    self.assertTrue(self.view(snapshot, card)["exposure"]["expected"])

    def test_snapshot_read_also_reads_the_board_at_most_once(self):
        counted = []
        real = self.store.load
        with mock.patch.object(self.store, "load", side_effect=lambda: counted.append(1) or real()):
            self.board._json = ""
            self.board.snapshot_json()
        # Pusta migawka → jedno `publish()` → dokładnie jedno wczytanie (regresja do dwóch byłaby widoczna).
        self.assertEqual(len(counted), 1, "Snapshot wczytał Tablicę więcej niż raz")

    def test_snapshot_with_a_deferred_publication_and_changed_plans_reads_the_board_once(self):
        """PT-385 (N3): odłożona publikacja ponowiona w Snapshot() już wczytała plany — zmieniony znacznik planów
        nie publikuje drugi raz w tym samym wywołaniu."""
        counted = []
        real = self.store.load
        before = json.loads(self.board.snapshot_json())["version"]
        self.board._pending = "publish"       # publikacja odłożona przez akcję z kolejki
        self.board._plans_mark = None         # jak nowy plan w repozytorium projektu
        with mock.patch.object(self.store, "load", side_effect=lambda: counted.append(1) or real()):
            snap = json.loads(self.board.snapshot_json())
        self.assertEqual(len(counted), 1, "Snapshot wczytał Tablicę więcej niż raz")
        self.assertGreaterEqual(snap["version"], before)
        self.assertIsNone(self.board._pending)
        self.assertEqual(self.board._plans_mark, self.board._plans_stamp(), "znacznik planów po ponowieniu ustawiony")
        with mock.patch.object(self.store, "load", side_effect=lambda: counted.append(1) or real()):
            self.board.snapshot_json()
        self.assertEqual(len(counted), 1, "kolejny Snapshot bez zmian nie wczytuje Tablicy")

    def test_pack_store_metadata_is_walked_once_per_project_in_a_refresh(self):
        os.makedirs(os.path.join(packs.snapshot_root(PLASMA), "kopia", "data"), exist_ok=True)
        walks = []
        real = envconsent._tree_sig

        def tree_sig(root, budget):
            walks.append(root)
            return real(root, budget)
        with mock.patch.object(envconsent, "_tree_sig", side_effect=tree_sig):
            self.refresh()
        self.assertEqual(walks, [packs.snapshot_root(PLASMA)],
                         "magazyn kopii paczek przechodzony więcej niż raz na projekt w jednym odświeżeniu")
        # Następne odświeżenie liczy go od nowa (kontekst nie przeżywa odświeżenia).
        with mock.patch.object(envconsent, "_tree_sig", side_effect=tree_sig):
            self.refresh()
        self.assertEqual(len(walks), 2)

    def test_no_global_current_board_survives_the_refresh(self):
        self.refresh()

        def holders(obj):
            return [name for name, value in vars(obj).items() if isinstance(value, BoardRead)]
        for obj in (self.engine, self.board, self.flow, self.store):
            self.assertEqual(holders(obj), [], f"{type(obj).__name__} trzyma kontekst odczytu po odświeżeniu")
        for key, _view in self.engine._env_cache.values():
            self.assertFalse(any(isinstance(part, BoardRead) for part in key))

    def test_context_is_a_single_lazy_read_and_helpers_use_it(self):
        counted = []
        real = self.store.load
        with mock.patch.object(self.store, "load", side_effect=lambda: counted.append(1) or real()):
            read = BoardRead(self.store)
            self.assertEqual(counted, [])
            card = self.proj_cards[0]
            self.engine._project(card, read)
            self.engine._review_policy(card, read=read)
            self.engine._plan_review_policy(card, read)(PLASMA, "standard")
            envconsent._project_state(self.engine, card, read)
            projectconsent.service(self.engine, self.plasma_cards[0], read)
            envconsent.model(self.engine, self.plasma_cards[0], self.engine.journal.state(self.plasma_cards[0]), read)
            self.assertEqual(len(counted), 1)
            # Bez kontekstu — dotychczasowy świeży odczyt przy każdym wywołaniu.
            self.engine._project(card)
            self.engine._project(card)
            self.assertEqual(len(counted), 3)

    def test_failed_board_read_is_not_repeated_within_one_refresh(self):
        calls = []

        def broken():
            calls.append(1)
            raise OSError("dysk")
        read = BoardRead(mock.Mock(load=broken))
        for _ in range(3):
            with self.assertRaises(OSError):
                read.board
        self.assertEqual(calls, [1])


class FreshViewTest(RefreshContextCase):
    def assert_fresh_after(self, card, change, *, recomputed=True):
        before, _, _ = self.refresh()
        _, _, models = self.refresh()
        self.assertNotIn(card, models, "bez zmian wejść model liczony ponownie — pamięć nie działa")
        during = []
        real = envconsent.model
        # Zapis przez /Board (np. UpsertProject) sam publikuje migawkę — model liczony wtedy też się liczy.
        with mock.patch.object(envconsent, "model",
                               side_effect=lambda engine, c, st, *rest: during.append(c) or real(engine, c, st, *rest)):
            change()
        snapshot, loads, models = self.refresh()
        self.assertLessEqual(loads, 1)
        if recomputed:
            self.assertIn(card, during + models, "zmiana wejścia nie unieważniła pamięci")
        self.assertEqual(self.view(snapshot, card), self.fresh(card), "migawka pokazuje nieaktualny widok")
        return self.view(before, card), self.view(snapshot, card)

    def test_project_profile_change_is_visible_after_next_refresh(self):
        card = self.proj_cards[0]
        old, new = self.assert_fresh_after(card, lambda: bp.save_project(
            SLUG, {"tide-box": 1, "network": {"domains": ["fresh.example.org"]}}))
        self.assertNotIn("fresh.example.org", [i["id"] for i in old["items"]])
        self.assertIn("fresh.example.org", [i["id"] for i in new["items"]])

    def test_card_profile_change_is_visible(self):
        card = self.proj_cards[0]
        _, new = self.assert_fresh_after(card, lambda: bp.save_card(
            SLUG, card, {"tide-box": 1, "network": {"domains": ["card.example.org"]}}))
        self.assertIn("card.example.org", [i["id"] for i in new["items"]])

    def test_box_journal_change_is_visible(self):
        card = self.proj_cards[0]
        old, new = self.assert_fresh_after(card, lambda: bs.record(SLUG, "box.ready", bs.box_name(SLUG),
                                                                    hash="clean-hash", warning=False,
                                                                    duration=0.1))
        self.assertNotEqual(old["state"], new["state"])

    def test_new_plan_version_is_visible(self):
        card = self.proj_cards[1]
        def change():
            journal = self.engine.journal
            journal.append(card, "plan.proposed", title="Plan 2", prefix="PT", rows=ROWS, planner="Opus",
                           box={"tide-box": 1, "network": {"domains": ["plan2.example.org"]}})
            journal.append(card, "plan.reviewed", reviewer="Opus", verdict="APPROVE", findings=[])
        _, new = self.assert_fresh_after(card, change)
        self.assertIn("plan2.example.org", [i["id"] for i in new["items"]])

    def test_card_state_change_is_visible(self):
        card = self.proj_cards[0]
        _, new = self.assert_fresh_after(card, lambda: boxwait.set_box_wait(
            self.engine.journal, card, boxwait.ENVIRONMENT, source="ui"))
        self.assertEqual(new["boxWait"], boxwait.ENVIRONMENT)

    def test_effective_settings_change_invalidates_the_view(self):
        card = self.proj_cards[0]

        def change():
            with open(self.settings, encoding="utf-8") as fh:
                data = json.load(fh)
            data["stepGuardMinutes"] = data.get("stepGuardMinutes", 15) + 1
            with open(self.settings, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        self.assert_fresh_after(card, change)

    def test_project_repository_change_invalidates_the_view(self):
        card = self.proj_cards[0]
        other = os.path.join(self.base, "ws", "proj-moved")
        os.makedirs(other)
        git(other, "init", "-q")

        def change():
            reply = json.loads(self.board.upsert_project(json.dumps({"slug": SLUG, "repo": other})))
            self.assertTrue(reply["ok"], reply)
        self.assert_fresh_after(card, change)

    def test_repository_proposal_change_invalidates_the_view(self):
        card = self.proj_cards[0]

        def change():
            os.makedirs(os.path.join(self.repo, ".tide"), exist_ok=True)
            with open(os.path.join(self.repo, ".tide", "box.yaml"), "w", encoding="utf-8") as fh:
                fh.write("tide-box: 1\n")
        self.assert_fresh_after(card, change)

    def test_plasma_tide_uses_the_same_cache_and_invalidation(self):
        card = self.plasma_cards[0]
        old, new = self.assert_fresh_after(card, lambda: bp.save_project(
            PLASMA, {"tide-box": 1, "project": PLASMA, "network": {"domains": ["files.pythonhosted.org"]}}))
        self.assertNotEqual(old["exposure"]["expected"], new["exposure"]["expected"])
        self.assertIn(card, self.engine._env_cache)

    def test_plasma_tide_consent_and_revocation_are_visible(self):
        card = self.plasma_cards[0]
        self.refresh()
        view = self.view(json.loads(self.board._json), card)
        self.assertFalse(view["exposure"]["approved"])

        def approve():
            reply = self.engine.approve_environment(card, self.consent_text(view, accepted=True), False)
            self.assertTrue(reply["ok"], reply)
        _, approved = self.assert_fresh_after(card, approve)
        self.assertTrue(approved["exposure"]["approved"])
        self.assertTrue(approved["approvedVersion"])
        version = approved["approvedVersion"]

        def revoke():
            text = bp.dump({**self.yaml_of(approved), projectconsent.ACTION: "revoke",
                            projectconsent.EXPECTED: version})
            reply = self.engine.approve_environment(card, text, False)
            self.assertTrue(reply["ok"], reply)
        _, revoked = self.assert_fresh_after(card, revoke)
        self.assertFalse(revoked["approved"])
        self.assertFalse(revoked["exposure"]["approved"])

    def test_plasma_tide_input_manifest_store_change_invalidates_the_view(self):
        card = self.plasma_cards[0]
        self.assert_fresh_after(card, lambda: os.makedirs(os.path.join(packs.snapshot_root(PLASMA), "pending"),
                                                          exist_ok=True))


class PresentationOnlyTest(RefreshContextCase):
    """Pamięć widoku nie jest zgodą: operacja przelicza odcisk i prawa świeżo przed skutkiem."""

    def warm(self, card):
        self.refresh()
        self.refresh()
        self.assertIn(card, self.engine._env_cache)
        return copy.deepcopy(self.view(json.loads(self.board._json), card))

    def test_stale_consent_after_warm_cache_is_refused(self):
        card = self.plasma_cards[0]
        stale = self.warm(card)
        # Wejście zmienia się bez odświeżenia: pamięć i migawka pokazują stary odcisk.
        bp.save_project(PLASMA, {"tide-box": 1, "project": PLASMA, "network": {"domains": ["github.com"]}})
        self.assertEqual(self.view(json.loads(self.board._json), card), stale)
        with mock.patch.object(envconsent, "model", side_effect=AssertionError("operacja czyta model prezentacji")), \
                mock.patch.object(envconsent, "view_key", side_effect=AssertionError("operacja czyta pamięć")):
            reply = self.engine.approve_environment(card, self.consent_text(stale, accepted=True), False)
        self.assertFalse(reply["ok"], reply)
        self.assertIsNone(ex.current(PLASMA), "zgoda zapisana na podstawie starego podglądu")
        self.assertEqual([e for e in self.engine.journal.read(card) if e.get("type") == "plan.approved"], [])

    def test_revoked_consent_is_refused_even_while_the_cached_view_says_approved(self):
        card = self.plasma_cards[0]
        view = self.warm(card)
        reply = self.engine.approve_environment(card, self.consent_text(view, accepted=True), False)
        self.assertTrue(reply["ok"], reply)
        approved = self.warm(card)
        version = approved["approvedVersion"]
        with ex.approved_access(PLASMA, version) as manifest:
            self.assertEqual(manifest["project"], PLASMA)
        ex.revoke(PLASMA, version)
        # Bez odświeżenia pamięć wciąż pokazuje zgodę; bariera czyta magazyn świeżo i odmawia.
        self.assertTrue(self.view(json.loads(self.board._json), card)["exposure"]["approved"])
        with self.assertRaises(ex.ExposureError):
            with ex.approved_access(PLASMA, version):
                pass
        again = self.engine.approve_environment(card, bp.dump({
            **self.yaml_of(approved), projectconsent.ACTION: "approve",
            projectconsent.EXPECTED: approved["exposure"]["expected"]}), False)
        self.assertFalse(again["ok"], "stary podgląd bez nowej świadomej zgody przywrócił zgodę")
        self.assertIsNone(ex.current(PLASMA))

    def test_other_project_approval_validates_freshly_not_from_cache(self):
        card = self.proj_cards[0]
        stale = self.warm(card)
        bp.save_project(SLUG, {"tide-box": 1, "network": {"domains": ["evil.example.com"]}})
        self.engine._env_cache[card] = (self.engine._env_cache[card][0], {"items": [], "poisoned": True})
        with mock.patch.object(envconsent, "model", side_effect=AssertionError("operacja czyta model prezentacji")):
            reply = self.engine.approve_environment(card, bp.dump({"tide-box": 1, **self.yaml_of(stale)}), False)
        # Projekt jest już zatwierdzony (profil w Tablicy); zapis karty przechodzi pełną świeżą walidację.
        self.assertTrue(reply["ok"], reply)
        events = [e for e in bs.events(SLUG) if e.get("type") == "box.approved" and e.get("card") == card]
        self.assertTrue(events)
        self.assertIn("evil.example.com", events[-1]["items"], "zgoda policzona ze starego widoku, nie z Tablicy")


class ErrorAndDisabledTest(RefreshContextCase):
    def test_unreadable_project_profile_gives_a_message_in_the_section(self):
        for slug, card in ((SLUG, self.proj_cards[0]), (PLASMA, self.plasma_cards[0])):
            with self.subTest(slug):
                self.refresh()
                os.makedirs(os.path.dirname(bp.project_path(slug)), exist_ok=True)
                with open(bp.project_path(slug), "w", encoding="utf-8") as fh:
                    fh.write("tide-box: [nie: yaml\n")
                snapshot, loads, _ = self.refresh()
                self.assertLessEqual(loads, 1)
                self.assertEqual({c["id"] for c in snapshot["cards"]} >= set(self.proj_cards + self.plasma_cards),
                                 True, "migawka bez kart po błędzie profilu")
                view = self.view(snapshot, card)
                self.assertTrue(view, "brak sekcji środowiska przy błędzie profilu")
                self.assertTrue(" ".join(view.get("notes") or []), f"brak komunikatu w sekcji: {view}")

    def test_disabled_box_waiting_plasma_card_keeps_the_consent_section(self):
        waiting, fresh = self.plasma_cards
        boxwait.set_box_wait(self.engine.journal, waiting, boxwait.ENVIRONMENT, source="ui")
        self.box_enabled = False
        snapshot, loads, _ = self.refresh()
        self.assertLessEqual(loads, 1)
        self.assertTrue(self.view(snapshot, waiting)["projectExposure"])
        flow = self.card_of(snapshot, fresh)["derived"]["flow"]
        self.assertEqual(flow["boxWait"], "")
        self.assertNotIn("environment", flow)
        for card in self.proj_cards:
            self.assertEqual(self.card_of(snapshot, card)["derived"]["flow"]["boxWait"], "")

    def test_disabled_box_refresh_reads_no_board_for_flow_cards(self):
        self.box_enabled = False
        loads = []
        real = self.store.load
        with mock.patch.object(self.store, "load", side_effect=lambda: loads.append(1) or real()):
            for card in self.proj_cards + self.plasma_cards:
                self.engine.derived({"id": card, "column": "todo"})
        self.assertEqual(loads, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
