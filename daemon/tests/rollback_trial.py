"""Próba downgrade'u K-38 → przypięty master (PT-293, F5): wspólny harness testów wycofania.

1. Generowanie historii wyłącznie publicznymi ścieżkami kodu K-38 (BoxFlow: atrapy Incusa, jednostek pudełka,
   proxy, `claude`): oczekiwanie `box.wait=environment`, zgoda środowiska, `box.wait=rebuild`, krok w pudełku
   zatrzymany przez strażnika i wznowiony, pytanie kroku w pudełku. Dziennik nie jest pisany ręcznie.
2. Stary silnik: `git archive <przypięty master>` do katalogu tymczasowego, `rollback_old_driver.py` w osobnym
   interpreterze (`python3 -I`) czyta ten sam autorytatywny dziennik (mirror usługi w XDG_STATE_HOME) foldem
   i `FlowEngine.tick()` starego kodu. Atrapy hosta: `systemctl` z flowfakes (widzi tylko jednostki hosta),
   licznik `systemd-run` starego silnika (żaden program nie startuje naprawdę).
3. Skutki: liczniki startów, wywołania `systemctl` na jednostkach kroków pudełka, zdarzenia dopisane przez stary
   silnik, odcisk danych spoza dziennika (stan pudełek, katalogi kroków, refy repozytorium).

Przypięty stary kod: master w chwili startu PT-293. Przed rzeczywistym wycofaniem próbę powtarza się dla
pierwszego rodzica commita integracji (`TIDE_ROLLBACK_OLD_SHA`), patrz docs/box-decisions.md (PT-293).
"""
import hashlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
from unittest import mock

from tide_daemon.box import state as bs
from tide_daemon.cardflow.state import fold

from .boxflow import SLUG, BoxFlow
from .privatebus import wait_until

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLD_SHA = os.environ.get("TIDE_ROLLBACK_OLD_SHA") or "a6efe28cdfe2e727bbc1dec085ac3aebfb76abe6"
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rollback_old_driver.py")

# Krok startuje z czystym otoczeniem (`env -i`), więc ścieżka licznika jest wpisana w atrapę.
OLD_RUN = r'''#!/usr/bin/python3
import json, sys
with open(@LOG@, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
sys.exit(0)
'''


def git_sha(rev: str = "HEAD") -> str:
    return subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", rev], capture_output=True, text=True,
                          check=True).stdout.strip()


def extract_old(dest: str, sha: str = OLD_SHA) -> str:
    """Kod `daemon/` z zasobami (`docs/`, `tools/`) przypiętego mastera w `dest`, jak po `make install` tej wersji.
    Brak obiektu w repozytorium to błąd testu, nie pominięcie."""
    data = subprocess.run(["git", "-C", REPO_ROOT, "archive", "--format=tar", sha, "daemon", "docs", "tools"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest


class RollbackFlow(BoxFlow):
    """BoxFlow z harnessem próby wycofania. Strażnik krótki: zatrzymanie kroku `czekaj` w pudełku po ~3 s."""

    guard_minutes = 0.05

    def setUp(self):
        super().setUp()
        self.addCleanup(self.kill_box_units)

    def kill_box_units(self):
        """Procesy jednostek pudełka atrapy (żyją poza silnikiem) nie przeżywają testu."""
        try:
            names = os.listdir(self.units)
        except OSError:
            return
        for name in names:
            try:
                with open(os.path.join(self.units, name), encoding="utf-8") as fh:
                    os.killpg(int(fh.read()), signal.SIGKILL)
            except (OSError, ValueError):
                pass

    # --- K-38: generowanie historii -------------------------------------------------------------

    def only_plan(self, card_id, only_plan=True) -> dict:
        reply = []
        with mock.patch.object(self.flow.engine, "box_launcher", lambda *_args: None):
            self.flow.approve_environment(card_id, "tide-box: 1\n", only_plan, reply.append)
            self.assertTrue(wait_until(lambda: reply, timeout=20), "ApproveCardEnvironment bez odpowiedzi")
        data = json.loads(reply[0])
        self.assertTrue(data.get("ok"), data)
        return data

    def decide(self, card_id, **request) -> dict:
        reply = []
        self.board.resolve_card_step(card_id, json.dumps(request), reply.append)
        self.assertTrue(wait_until(lambda: reply, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(reply[0])

    def start_ok(self, card_id, **extra) -> dict:
        reply = self.start(card_id, **extra)
        self.assertTrue(reply.get("ok"), reply)
        return reply

    def finished_of(self, card_id, count=1, timeout=60) -> dict:
        found = wait_until(lambda: len(self.of(card_id, "step.finished")) >= count, timeout=timeout)
        self.assertTrue(found, json.dumps(self.events(card_id), ensure_ascii=False, indent=1))
        return self.of(card_id, "step.finished")[count - 1]

    def started_of(self, card_id, count=1, timeout=60) -> dict:
        found = wait_until(lambda: len(self.of(card_id, "step.started")) >= count, timeout=timeout)
        self.assertTrue(found, json.dumps(self.events(card_id), ensure_ascii=False, indent=1))
        return self.of(card_id, "step.started")[count - 1]

    def generate(self) -> dict:
        """Historia K-38 na atrapach. Zwraca {rola: karta}; procesy kroków pudełka mogą jeszcze żyć."""
        cards = self._cards = {}
        # Karty sprzed włączenia pudełka: krok na hoście kończy się pytaniem.
        self.box_enabled = False
        for role in ("host-only", "env-pending", "env-approved"):
            cards[role] = self.card(title=role, description="Dodaj eksport.\n\nATRAPA: pytanie")
            self.start_ok(cards[role])
            self.assertEqual(self.finished_of(cards[role])["result"], "question")
        self.box_enabled = True
        self.build_box()
        self.assertEqual(self.only_plan(cards["env-pending"])["boxWait"], "environment")
        self.assertEqual(self.only_plan(cards["env-approved"])["boxWait"], "environment")
        self.only_plan(cards["env-approved"], only_plan=False)
        # Krok w pudełku: pytanie (czeka na odpowiedź — wznowienie przez `--resume` sesji z domu pudełka).
        cards["box-question"] = self.card(title="box-question", description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.start_ok(cards["box-question"])
        self.assertEqual(self.finished_of(cards["box-question"])["result"], "question")
        # Przepis pudełka niezgodny z profilem: karta dostaje `box.wait=rebuild`; użytkownik ją wstrzymuje,
        # a pudełko wraca do zgodnego przepisu (oczekiwanie w dzienniku karty zostaje).
        self.restamp({"tide-box": 1, "apt": ["jq"]})
        cards["rebuild"] = self.card(title="rebuild", description="Dodaj eksport.\n\nATRAPA: gotowe")
        self.start_ok(cards["rebuild"])
        self.assertTrue(wait_until(lambda: fold(self.events(cards["rebuild"])).box_wait == "rebuild", timeout=30),
                        self.events(cards["rebuild"]))
        self.pause(cards["rebuild"])
        self.restamp(self.approve())
        # Krok w pudełku: start, zatrzymanie przez strażnika (bez postępu), ponowienie użytkownika w tym samym pudełku.
        cards["box-running"] = self.card(title="box-running", description="Dodaj eksport.\n\nATRAPA: czekaj")
        self.start_ok(cards["box-running"])
        first = self.started_of(cards["box-running"])
        self.assertEqual(first.get("runner"), "box", first)
        stopped = self.finished_of(cards["box-running"])
        self.assertEqual((stopped["result"], stopped["reason"]), ("error", "no-progress"), stopped)
        # Silnik K-38 sam ponawia krok bez postępu (`step.retry`) jako wznowienie z `parentStep` w tym samym pudełku.
        resumed = self.started_of(cards["box-running"], 2)
        self.assertTrue(self.of(cards["box-running"], "step.retry"), self.events(cards["box-running"]))
        self.assertEqual((resumed.get("parentStep"), resumed.get("runner")), (first["step"], "box"), resumed)
        self.assertTrue(wait_until(lambda: self.box_unit_pid(resumed), timeout=20))
        self.resumed_step = resumed
        return cards

    def restamp(self, profile):
        """Odcisk przepisu pudełka i migawki `clean` w stanie atrapy (jak po budowie z `profile`)."""
        from tide_daemon.box import profile as bp
        builder = self.builder()
        stamp = builder.stamp(bp.with_defaults(profile))
        value = json.dumps(stamp, sort_keys=True, separators=(",", ":"))
        builder.incus.config_set(SLUG, {bs.RECIPE_KEY: value})
        path = os.path.join(os.environ["TIDE_FAKE_INCUS_STATE"], "state.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for snap in data["instances"][SLUG]["snapshots"]:
            if snap.get("name") == bs.SNAPSHOT:
                snap.setdefault("config", {})[bs.RECIPE_KEY] = value
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def pause(self, card_id, paused=True) -> dict:
        reply = []
        self.flow.set_paused(card_id, paused, reply.append)
        self.assertTrue(wait_until(lambda: reply, timeout=20), "SetCardPaused bez odpowiedzi")
        data = json.loads(reply[0])
        self.assertTrue(data.get("ok"), data)
        return data

    def box_unit_pid(self, step) -> int:
        try:
            with open(os.path.join(self.units, step["unit"] + ".service"), encoding="utf-8") as fh:
                pid = int(fh.read())
        except (OSError, ValueError):
            return 0
        return pid if _alive(pid) else 0

    def snapshot(self, cards) -> dict:
        """Dowód generowania: SHA obu wersji, role kart, pełne zdarzenia w kolejności dziennika (z mirrora usługi)."""
        return {"newSha": git_sha(), "newTree": git_sha("HEAD^{tree}"), "oldSha": OLD_SHA, "cards": cards,
                "journals": {c: self.flow.engine.journal.read(c) for c in cards.values()}}

    def write_artifact(self, label, data) -> str:
        """Artefakt próby w `$TIDE_ROLLBACK_ARTIFACTS` (notka ticketu), inaczej w katalogu testu."""
        folder = os.environ.get("TIDE_ROLLBACK_ARTIFACTS") or os.path.join(self.base, "artifacts")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{self.id().rsplit('.', 1)[-1]}-{label}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
        return path

    def user_actions(self, cards) -> list:
        """Ruchy użytkownika na starym kodzie: odpowiedzi na pytania kroków i wznowienie wstrzymanej karty."""
        journal = self.flow.engine.journal
        actions = [{"op": "answer", "card": cards[role], "step": fold(journal.read(cards[role])).last_step}
                   for role in ("host-only", "env-pending", "env-approved", "box-question")]
        return actions + [{"op": "resume", "card": cards["rebuild"]}]

    def unsafe_effects(self, cards, report) -> list:
        """Skutki starego silnika sprzeczne ze znaczeniem historii K-38 (każdy opisany zdaniem).

        `report["before"]` — zdarzenia sprzed startu starego silnika. Start kroku karty `host-only` po odpowiedzi
        nie jest skutkiem niebezpiecznym (to samo zrobiłby K-38 przy `box.enabled: false`)."""
        effects = []
        box_steps = {e["step"]: e for events in report["before"].values() for e in events
                     if e["type"] == "step.started" and e.get("runner") == "box"}
        for unit in {s.get("unit") for s in box_steps.values()}:
            if any(line.split()[-1] == unit + ".scope" for line in report["systemctl"]):
                effects.append(f"hostowe sprawdzanie jednostki kroku z pudełka: systemctl --user is-active {unit}.scope")
        for card, events in report["appended"].items():
            for e in events:
                if e["type"] == "step.finished" and e.get("step") in box_steps:
                    effects.append(f"{card}: stary silnik zakończył krok z pudełka {e['step']} jako "
                                   f"{e.get('result')}/{e.get('reason')} bez dowodu końca procesu w pudełku")
        names = {v: k for k, v in cards.items()}
        for argv in report["starts"]:
            unit = next((a.split("=", 1)[1] for a in argv if a.startswith("--unit=")), "")
            card = "-".join(unit.split("-")[2:4]) if unit.startswith("tide-step-K-") else ""
            role = names.get(card, "")
            if role and role != "host-only":
                effects.append(f"{card} ({role}): start na hoście {unit}")
            if "--resume" in argv:
                session = argv[argv.index("--resume") + 1]
                if any(s.get("session") == session for s in box_steps.values()):
                    effects.append(f"{card}: wznowienie na hoście sesji kroku z pudełka ({session})")
        if report["foreignBefore"] != report["foreignAfter"]:
            effects.append("zmiana danych spoza dziennika (stan pudełek, Incus, refy, worktrees)")
        if not all(report["prefixKept"].values()):
            effects.append("zmiana wcześniejszych zdarzeń dziennika")
        return effects

    # --- przypięty master: stary silnik ---------------------------------------------------------

    def old_tree(self) -> str:
        tree = os.path.join(self.base, "old-master")
        if not os.path.isdir(tree):
            extract_old(tree)
        return tree

    def old_engine(self, label, *, ticks=3, actions=(), ticks_after=2) -> dict:
        """Jeden start starego silnika (proces) na bieżącym stanie; zwraca raport sterownika i liczniki skutków."""
        work = os.path.join(self.base, "old-" + label)
        bindir = os.path.join(work, "bin")
        os.makedirs(bindir)
        logs = {"run": os.path.join(work, "systemd-run.log"), "ctl": os.path.join(work, "systemctl.log")}
        run, ctl = os.path.join(bindir, "systemd-run"), os.path.join(bindir, "systemctl")
        with open(run, "w", encoding="utf-8") as fh:
            fh.write(OLD_RUN.replace("@LOG@", repr(logs["run"])))
        with open(ctl, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(logs['ctl'])}\n"
                     f"exec {shlex.quote(self.fakes['systemctl'])} \"$@\"\n")
        for path in (run, ctl):
            os.chmod(path, 0o755)
        before = {c: self.flow.engine.journal.read(c) for c in self.flow.engine.journal.cards()}
        foreign = foreign_fingerprint(self)
        cfg = {"oldTree": self.old_tree(), "board": self.root,
               "claudeProjects": os.path.join(self.base, "home", ".claude", "projects"), "systemdRun": run,
               "tmpRoot": os.path.join(self.base, "tmp"), "settings": self.settings,
               "fakes": {k: v for k, v in self.fakes.items() if k in ("claude", "codex", "konsole")},
               "ticks": ticks, "actions": list(actions), "ticksAfterAction": ticks_after,
               "report": os.path.join(work, "report.json")}
        with open(os.path.join(work, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
        done = subprocess.run(["/usr/bin/python3", "-I", DRIVER, os.path.join(work, "config.json")], env=env,
                              capture_output=True, text=True, timeout=300, cwd=work)
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-3000:])
        with open(cfg["report"], encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertTrue(report["module"].startswith(os.path.realpath(cfg["oldTree"])), report["module"])
        journal = self.flow.engine.journal
        after = {c: journal.read(c) for c in journal.cards()}
        report["appended"] = {c: [e for e in after[c][len(before.get(c, [])):]] for c in after}
        report["prefixKept"] = {c: after.get(c, [])[:len(events)] == events for c, events in before.items()}
        report["before"] = before
        report["starts"] = [json.loads(line) for line in _lines(logs["run"])]
        report["systemctl"] = _lines(logs["ctl"])
        report["foreignBefore"], report["foreignAfter"] = foreign, foreign_fingerprint(self)
        report["stdout"] = done.stdout[-4000:]
        return report


def _lines(path) -> list:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _tree_digest(root: str) -> dict:
    out = {}
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                out[os.path.relpath(path, root)] = "unreadable"
    return out


def foreign_fingerprint(test) -> dict:
    """Dane spoza dziennika kart: stan pudełek i ich domów (XDG_DATA_HOME atrapy), stan Incusa, refy i worktrees."""
    refs = subprocess.run(["git", "-C", test.repo, "for-each-ref", "--format=%(refname) %(objectname)"],
                          capture_output=True, text=True).stdout
    worktrees = subprocess.run(["git", "-C", test.repo, "worktree", "list", "--porcelain"],
                               capture_output=True, text=True).stdout
    return {"data": _tree_digest(os.environ["XDG_DATA_HOME"]),
            "incus": _tree_digest(os.environ["TIDE_FAKE_INCUS_STATE"]),
            "refs": refs, "worktrees": worktrees}
