"""Cykl życia pudełek: start, stop, bezczynność i sprzątanie kopii zamkniętych kart (D14)."""
from datetime import datetime
import os

from . import profile as bp
from . import state as bs


def limits(profile):
    resources = bp.with_defaults(profile)["resources"]
    if resources["swap"]:
        from .builder import BuildError
        raise BuildError("resources.swap=true jest niedostępne w ograniczonym projekcie Incusa")
    return {"limits.cpu": resources["cpu"], "limits.memory": resources["memory"]}


NO_SWAP_CHECK = ["sh", "-eu", "-c", 'test "$(cat "$1")" = 0', "tide-no-swap", "/sys/fs/cgroup/memory.swap.max"]


def require_no_swap(incus, name, env=None):
    from .builder import BuildError
    if incus.exec(name, NO_SWAP_CHECK, env) != 0:
        raise BuildError(f"{name}: brak potwierdzenia memory.swap.max=0 (wymagane cgroup v2 bez swapu)")


def active_steps(builder, slug, name, engine=None):
    """Suma rezerwacji dziennika, żywych klientów i jednostek systemd (także po restarcie).

    Nieznany stan jednostki blokuje operację. Brak `box.use` nie jest dowodem braku procesu.
    CLI czyta te same trwałe dzienniki co silnik, bez tworzenia drugiego źródła stanu.
    """
    from ..cardflow.journal import Journal
    from ..cardflow.state import fold
    from .runner import BoxRunner

    journal = engine.journal if engine is not None else Journal(bp.board_dir())
    busy = set(bs.running_steps(slug, name))
    instance = builder.incus.instance(name)
    running = instance is not None and str(instance.get("status", "")).lower() == "running"
    for card in journal.cards():
        st = fold(journal.read(card))
        for step in st.steps.values():
            if step.get("boxSlug") != slug or step.get("box") != name:
                continue
            key = step.get("step") or ""
            proc = engine.procs.get(key) if engine is not None else None
            if proc is not None and proc.poll() is None:
                busy.add(key)
            if step.get("unit") and running:
                state = (engine._unit_state(step) if engine is not None else
                         BoxRunner(builder.settings, slug, incus=builder.incus).step_state(step))
                if state != "inactive":
                    busy.add(key)
            elif not step.get("unit") and key not in st.finished:
                busy.add(key)
        for intent in st.open_intents(include_background=True):
            if intent.get("boxSlug") == slug and intent.get("box") == name:
                busy.add(intent.get("key") or "start kroku")
    return sorted(busy)


class Lifecycle:
    def __init__(self, builder):
        self.builder = builder
        self.incus = builder.incus

    @bs.serialized
    def start(self, slug, card=None):
        from .builder import BuildError
        b = self.builder
        name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
        st = b.status(slug, card)
        if not st.usable:
            raise BuildError(f"pudełko {name} nie jest gotowe do startu: {st.error or st.state}")
        started = b.clock()
        if not st.running:
            self.incus.config_set(name, limits(b.load_profile(slug)))
            self.incus.start(name)
        deadline = b.clock() + max(1.0, b.settings.ip_wait_seconds)
        while self.incus.exec(name, ["true"]) != 0:
            if b.clock() >= deadline:
                raise BuildError(f"pudełko {name} nie odpowiada po starcie")
            b.sleep(0.5)
        require_no_swap(self.incus, name)
        duration = round(b.clock() - started, 3)
        bs.record(slug, "box.ready", name, b.wall, duration=duration, hash=st.recipe_hash,
                  warning=st.warning, reason="start")
        return duration

    @bs.serialized
    def stop(self, slug, card=None, reason="użytkownik"):
        from .builder import BuildError
        b = self.builder
        name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
        b.require_idle(slug, name)
        inst = self.incus.instance(name)
        if inst is None:
            raise BuildError(f"pudełko {name} nie istnieje")
        started = b.clock()
        if str(inst.get("status", "")).lower() == "running":
            self.incus.stop(name)
        duration = round(b.clock() - started, 3)
        bs.record(slug, "box.stop", name, b.wall, duration=duration, reason=reason)
        return duration

    @bs.serialized
    def close_card(self, slug, card):
        from .builder import BoxBusy, BuildError
        b = self.builder
        name = bs.card_box_name(slug, card)
        source = bs.box_name(slug)
        inst = self.incus.instance(name)
        if inst is None:
            return
        config = inst.get("config") or {}
        if name == source or config.get(bs.COPY_OF_KEY) != source or config.get(bs.CARD_KEY) != card.upper():
            raise BuildError(f"{name}: nie jest kopią pudełka karty {card}")
        try:
            b.require_idle(slug, name)
        except BoxBusy as exc:
            previous = bs.events(slug, name)
            if not previous or previous[-1].get("result") != "deferred":
                bs.record(slug, "box.reset", name, b.wall, duration=0.0, result="deferred",
                          reason="karta domknięta", error=str(exc))
            return
        started = b.clock()
        b.ensure_home(slug, card)
        self.incus.delete(name, force=True)
        # Dom, eksport i dawny wolumen pozostają do jawnego sprzątania (PT-189).
        bs.record(slug, "box.reset", name, b.wall, duration=round(b.clock() - started, 3),
                  result="ok", reason="karta domknięta", removed=True)

    @bs.serialized
    def idle(self, slug, card=None):
        b = self.builder
        name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
        inst = self.incus.instance(name)
        if inst is None or str(inst.get("status", "")).lower() != "running":
            return
        if b.running_steps(slug, name):
            return
        events = [e for e in bs.events(slug, name)
                  if e.get("type") in ("box.ready", "box.release", "box.use", "box.reset", "box.build")]
        if not events or events[-1].get("phase") == "start":
            return
        try:
            last = datetime.fromisoformat(events[-1]["ts"]).timestamp()
        except (KeyError, ValueError, TypeError):
            return
        if b.wall() - last >= b.settings.idle_hours * 3600:
            self.stop(slug, card, reason="bezczynność")


def tick(engine):
    """Wywoływane po obsłużeniu kroków; odroczone sprzątanie jest ponawiane także po restarcie."""
    from ..cardflow import boxstep
    from ..cardflow.state import fold
    settings = boxstep.settings_of(engine)
    if not settings.enabled:
        return
    projects = os.path.join(bp.board_dir(), "projects")
    try:
        slugs = [s for s in os.listdir(projects) if os.path.isfile(os.path.join(projects, s, "box.yaml"))]
    except FileNotFoundError:
        return
    closed = {}
    for card in engine.journal.cards():
        st = fold(engine.journal.read(card))
        if st.closed and st.started:
            closed.setdefault(st.started.get("project"), set()).add(card)
    for slug in slugs:
        try:
            b = boxstep.builder_of(engine, settings)
            lifecycle = Lifecycle(b)
            from . import packs
            for card in closed.get(slug, set()):
                # Stan ostatniego kroku (także error) nie zmienia obowiązku sprzątania.
                names = (bs.box_name(slug), bs.card_box_name(slug, card))
                with bs.lifecycle_lock(slug):
                    if not any(active_steps(b, slug, name, engine) for name in names):
                        packs.cleanup(slug, card, b.incus, engine.journal)
            lifecycle.idle(slug)
            for inst in b.incus.list():
                config = inst.get("config") or {}
                card = config.get(bs.CARD_KEY)
                if not card or config.get(bs.COPY_OF_KEY) != bs.box_name(slug):
                    continue
                if card in closed.get(slug, set()):
                    lifecycle.close_card(slug, card)
                else:
                    lifecycle.idle(slug, card)
        except Exception as exc:
            print(f"tide-daemon: cykl życia pudełka {slug}: {exc!r}", flush=True)
