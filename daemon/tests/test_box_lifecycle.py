"""Cykl życia pudełka z atrapą Incusa; aktywność niezależna od czasu ostatniego zdarzenia."""
import dataclasses
import time
import threading
import os
from types import SimpleNamespace
from unittest import mock

from tide_daemon.box import builder as bb
from tide_daemon.box import lifecycle as lc
from tide_daemon.box import profile as bp
from tide_daemon.box import settings as bset
from tide_daemon.box import state as bs
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow import boxstep
from tide_daemon.box.incus import IncusError

from .test_box_builder import BoxCase, PROFILE
from .test_box_runner import BoxRunnerCase


class LifecycleTest(BoxCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.b = self.builder()
        self.b.build("plasma-tide")
        self.life = lc.Lifecycle(self.b)

    def test_project_reset_refuses_a_live_card_copy_without_resetting_either_box(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        bs.mark_in_use("plasma-tide", name, "live-copy-step")
        with mock.patch.object(self.b.incus, "snapshot_restore") as restore:
            with self.assertRaises(bb.BoxBusy):
                self.b.reset("plasma-tide")
            restore.assert_not_called()

    def test_destructive_operations_exclude_a_concurrent_reservation(self):
        for operation in ("stop", "reset", "close"):
            with self.subTest(operation=operation):
                name = self.b.copy_for_card("plasma-tide", "K-38") if operation == "close" else "plasma-tide"
                attempted, reserved = threading.Event(), threading.Event()
                errors = []

                def reserve():
                    attempted.set()
                    try:
                        bs.mark_in_use("plasma-tide", name, "racing")
                        reserved.set()
                    except BaseException as exc:
                        errors.append(exc)

                original = self.b.require_idle
                threads = []

                def checked(slug, box):
                    original(slug, box)
                    thread = threading.Thread(target=reserve)
                    threads.append(thread)
                    thread.start()
                    self.assertTrue(attempted.wait(2))
                    self.assertFalse(reserved.wait(0.1), "rezerwacja weszła między kontrolę a operację")

                with mock.patch.object(self.b, "require_idle", side_effect=checked):
                    if operation == "stop":
                        self.life.stop("plasma-tide")
                    elif operation == "reset":
                        self.b.reset("plasma-tide")
                    else:
                        self.life.close_card("plasma-tide", "K-38")
                for thread in threads:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(reserved.is_set())
                # Odwrotna kolejność: rezerwacja już istnieje, więc operacja musi odmówić.
                if operation != "close":
                    with self.assertRaises(bb.BoxBusy):
                        self.life.stop("plasma-tide")
                    with self.assertRaises(bb.BoxBusy):
                        self.b.reset("plasma-tide")
                bs.mark_released("plasma-tide", name, "racing")

    def test_restricted_swap_key_is_refused_but_lifecycle_starts_without_it(self):
        with self.assertRaises(IncusError):
            self.b.incus.config_set("plasma-tide", {"limits.memory.swap": False})
        self.life.stop("plasma-tide")
        self.life.start("plasma-tide")
        ready = [e for e in bs.events("plasma-tide") if e["type"] == "box.ready"]
        with mock.patch.dict(os.environ, {"TIDE_FAKE_SWAP_MAX": "max"}):
            with self.assertRaisesRegex(bb.BuildError, "memory.swap.max=0"):
                self.life.start("plasma-tide")
        self.assertEqual([e for e in bs.events("plasma-tide") if e["type"] == "box.ready"], ready)

    def test_swap_enabled_is_refused_instead_of_silently_ignored(self):
        self.life.stop("plasma-tide")
        self.approve({**PROFILE, "resources": {"swap": True}})
        with self.assertRaisesRegex(bb.BuildError, "resources.swap=true"):
            self.life.start("plasma-tide")
        self.assertEqual(self.b.incus.instance("plasma-tide")["status"], "Stopped")

    def test_stop_start_readiness_limits_and_status_duration(self):
        self.life.stop("plasma-tide")
        profile = {**PROFILE, "resources": {"cpu": 3, "memory": "7GiB", "swap": False}}
        self.approve(profile)
        before = len(self.incus_calls())
        self.life.start("plasma-tide")
        calls = self.incus_calls()[before:]
        start = next(i for i, a in enumerate(calls) if a[0] == "start")
        limits = next(i for i, a in enumerate(calls) if a[:2] == ["config", "set"])
        self.assertLess(limits, start)
        self.assertTrue({"limits.cpu=8", "limits.memory=25769803776"}.issubset(calls[limits]))
        self.assertFalse(any("limits.memory.swap=" in arg for call in calls for arg in call))
        self.assertTrue(any(a[0] == "exec" and a[-2:] == ["--", "true"] for a in calls[start:]))
        self.assertFalse(any(a[:3] in (["config", "device", "unset"], ["config", "device", "set"],
                                      ["config", "device", "override"]) for a in calls))
        self.assertEqual(self.b.status("plasma-tide").diff, [])
        for kind in ("box.stop", "box.ready"):
            event = [e for e in bs.events("plasma-tide") if e["type"] == kind][-1]
            self.assertIsInstance(event["duration"], (int, float))
        self.assertTrue(any(e["type"] == "box.stop" for e in bs.events("plasma-tide")))

    def test_busy_refuses_stop_reset_build(self):
        bs.mark_in_use("plasma-tide", "plasma-tide", "PT-182")
        before = len(self.incus_calls())
        for command in ("stop", "reset", "build"):
            with self.assertRaises(bb.BoxBusy):
                if command == "stop":
                    self.life.stop("plasma-tide")
                else:
                    getattr(self.b, command)("plasma-tide")
        self.assertFalse(any(a[0] in ("stop", "delete", "snapshot") for a in self.incus_calls()[before:]))

    def age(self):
        self.b.wall = lambda: time.time() + 5 * 3600

    def test_idle_stops_once_after_limit_and_never_resets_project(self):
        self.life.idle("plasma-tide")
        self.assertEqual(self.b.incus.instance("plasma-tide")["status"], "Running")
        self.age()
        before = len(self.incus_calls())
        self.life.idle("plasma-tide")
        self.life.idle("plasma-tide")
        calls = self.incus_calls()[before:]
        self.assertEqual(sum(a[0] == "stop" for a in calls), 1)
        self.assertFalse(any(a[0] in ("delete", "snapshot") for a in calls))
        self.assertEqual(bs.events("plasma-tide")[-1]["reason"], "bezczynność")

    def runtime(self, unit_state="inactive", poll=0):
        journal = Journal(bp.board_dir())
        journal.append("K-38", "card.started", project="plasma-tide")
        journal.append("K-38", "step.started", step="long", runner="box", boxSlug="plasma-tide",
                       box="plasma-tide", unit="long.service", ticket="PT-1")
        engine = SimpleNamespace(journal=journal, procs={"long": SimpleNamespace(poll=lambda: poll)},
                                 _unit_state=mock.Mock(return_value=unit_state))
        self.b.steps_source = lambda slug, name: lc.active_steps(self.b, slug, name, engine)
        return engine

    def test_old_step_alive_in_procs_or_systemd_prevents_idle_stop(self):
        self.age()
        for unit_state, poll in (("inactive", None), ("active", 0), ("unknown", 0)):
            with self.subTest(unit_state=unit_state, poll=poll):
                engine = self.runtime(unit_state, poll)
                self.life.idle("plasma-tide")
                self.assertEqual(self.b.incus.instance("plasma-tide")["status"], "Running")
                engine._unit_state.assert_called()
                with self.assertRaises(bb.BoxBusy):
                    self.life.stop("plasma-tide")
                with self.assertRaises(bb.BoxBusy):
                    self.b.reset("plasma-tide")

    def test_stop_checks_persisted_unit_even_without_box_use(self):
        self.runtime()
        self.b.steps_source = None
        with mock.patch("tide_daemon.box.runner.BoxRunner.step_state", return_value="active") as check:
            with self.assertRaises(bb.BoxBusy):
                self.life.stop("plasma-tide")
        check.assert_called_once()

    def test_stopped_box_has_no_live_unit_but_live_client_still_blocks_reset(self):
        engine = self.runtime()
        self.b.incus.stop("plasma-tide")
        engine._unit_state.return_value = "unknown"
        self.b.reset("plasma-tide")
        engine._unit_state.assert_not_called()
        engine.procs["long"] = SimpleNamespace(poll=lambda: None)
        with self.assertRaises(bb.BoxBusy):
            self.b.reset("plasma-tide")

    def test_close_deletes_only_copy_and_defers_while_reserved(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        bs.mark_in_use("plasma-tide", name, "close")
        self.life.close_card("plasma-tide", "K-38")
        self.assertIsNotNone(self.b.incus.instance(name))
        event = bs.events("plasma-tide", name)[-1]
        self.assertEqual((event["result"], event["reason"]), ("deferred", "karta domknięta"))
        self.assertNotEqual(bs.journal_state("plasma-tide", name)[0], bs.RESETTING)
        bs.mark_released("plasma-tide", name, "close")
        self.life.close_card("plasma-tide", "K-38")
        self.assertIsNone(self.b.incus.instance(name))
        self.assertIsNotNone(self.b.incus.instance("plasma-tide"))
        event = bs.events("plasma-tide", name)[-1]
        self.assertTrue(event["removed"])
        self.assertIsInstance(event["duration"], (int, float))

    def test_close_rejects_unrelated_instance(self):
        name = bs.card_box_name("plasma-tide", "K-38")
        self.b.incus.init("base", name)
        with self.assertRaises(bb.BuildError):
            self.life.close_card("plasma-tide", "K-38")
        self.assertIsNotNone(self.b.incus.instance(name))

    def test_changed_recipe_rebuild_resets_project_and_copy_but_not_busy_copy(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        self.approve({**PROFILE, "setup": ["true"]})
        self.b.build("plasma-tide")
        event = [e for e in bs.events("plasma-tide", "plasma-tide") if e["type"] == "box.reset"][-1]
        self.assertEqual(event["reason"], "zmiana przepisu")
        self.assertIsInstance(event["duration"], (int, float))
        bs.mark_in_use("plasma-tide", name, "long")
        with self.assertRaises(bb.BoxBusy):
            self.b.copy_for_card("plasma-tide", "K-38")
        bs.mark_released("plasma-tide", name, "long")
        self.b.copy_for_card("plasma-tide", "K-38")
        self.assertEqual(self.b.incus.config_get(name, bs.RECIPE_KEY),
                         self.b.incus.config_get("plasma-tide", bs.RECIPE_KEY))

    def test_engine_tick_retries_closed_copy_cleanup(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        journal = Journal(bp.board_dir())
        journal.append("K-38", "card.started", project="plasma-tide")
        journal.append("K-38", "card.closed")
        engine = SimpleNamespace(journal=journal, procs={},
                                 box_settings_source=lambda: dataclasses.replace(self.settings, enabled=True),
                                 box_builder=lambda: self.b)
        bs.mark_in_use("plasma-tide", name, "close")
        lc.tick(engine)
        self.assertIsNotNone(self.b.incus.instance(name))
        bs.mark_released("plasma-tide", name, "close")
        lc.tick(engine)
        self.assertIsNone(self.b.incus.instance(name))
        self.assertIsNotNone(self.b.incus.instance("plasma-tide"))

    def test_engine_tick_removes_packs_of_closed_card_after_error(self):
        from tide_daemon.box import packs
        from pathlib import Path
        source = Path(self.tmp) / "logs.jsonl"
        source.write_text("dane")
        spec = {"name": "logs", "from": str(source)}
        packs.materialize("plasma-tide", "K-38", [spec])
        dev = packs.devices("plasma-tide", "K-38", [spec])[0]
        self.b.incus.device_add("plasma-tide", dev.name, "disk", dev.values())
        journal = Journal(bp.board_dir())
        journal.append("K-38", "card.started", project="plasma-tide")
        journal.append("K-38", "step.started", step="s1", ticket="PT-999")
        journal.append("K-38", "step.finished", step="s1", result="error")
        journal.append("K-38", "card.closed")
        engine = SimpleNamespace(journal=journal, procs={},
                                 box_settings_source=lambda: dataclasses.replace(self.settings, enabled=True),
                                 box_builder=lambda: self.b)
        bs.mark_in_use("plasma-tide", "plasma-tide", "busy")
        lc.tick(engine)
        self.assertTrue(os.path.exists(packs.root("plasma-tide", "K-38")))
        bs.mark_released("plasma-tide", "plasma-tide", "busy")
        lc.tick(engine)
        self.assertFalse(os.path.exists(packs.root("plasma-tide", "K-38")))
        self.assertNotIn(dev.name, self.b.incus.device_show("plasma-tide"))
        self.assertEqual(journal.read("K-38")[-1]["type"], "packs.removed")

    def test_stale_copy_is_kept_when_new_project_snapshot_is_missing(self):
        name = self.b.copy_for_card("plasma-tide", "K-38")
        self.approve({**PROFILE, "setup": ["true"]})
        self.b.build("plasma-tide")
        state = self.fake_state()
        state["instances"]["plasma-tide"]["snapshots"] = []
        self.write_state(state)
        with self.assertRaises(bb.BuildError):
            self.b.copy_for_card("plasma-tide", "K-38")
        self.assertIsNotNone(self.b.incus.instance(name))

    def test_long_slug_keeps_card_suffix_and_differs_from_project(self):
        slug = "a" * 63
        name = bs.card_box_name(slug, "K-38")
        self.assertNotEqual(name, bs.box_name(slug))
        self.assertTrue(name.endswith("-k-38"))
        self.assertLessEqual(len(name), 63)

    def test_idle_settings_and_swap_validation(self):
        self.assertEqual(bset.from_dict({}).idle_hours, 4)
        self.assertEqual(bset.from_dict({"box": {"idle-hours": 0.5}}).idle_hours, 0.5)
        for value in (-1, 0, True, "4", float("nan")):
            settings = bset.from_dict({"box": {"idle-hours": value}})
            self.assertEqual(settings.idle_hours, 4)
            self.assertTrue(settings.warnings)
        with self.assertRaises(bp.ProfileError):
            bp.validate({"tide-box": 1, "resources": {"swap": "false"}})


class LifecycleMountTest(BoxRunnerCase):
    def test_running_box_gets_new_limits_before_mount_restart_and_swap_check_after(self):
        bp.save_project("proj", {"tide-box": 1, "resources": {"cpu": 3, "memory": "7GiB"}})
        self.incus.config_set("proj", {"limits.cpu": 1, "limits.memory": "2GiB"})
        builder = bb.Builder(self.settings, self.incus)
        engine = SimpleNamespace(box_settings_source=lambda: self.settings, box_builder=lambda: builder)
        placement = boxstep.Placement("proj", "proj", "10.0.0.2", False, {}, self.runner())
        bs.mark_in_use("proj", "proj", "s1")
        boxstep.prepare_runner(engine, placement, self.step())
        self.assertEqual(self.incus.config_get("proj", "limits.cpu"), "8")
        self.assertEqual(self.incus.config_get("proj", "limits.memory"), "25769803776")
        import shlex
        from .fakebin import calls
        argv = [shlex.split(line)[1:] for line in calls(self.env) if line.startswith("incus ")]
        start = next(i for i, a in enumerate(argv) if a[0] == "start")
        config = next(i for i, a in enumerate(argv) if "limits.cpu=8" in a)
        check = next(i for i, a in enumerate(argv) if "tide-no-swap" in a)
        self.assertLess(config, start)
        self.assertLess(start, check)
