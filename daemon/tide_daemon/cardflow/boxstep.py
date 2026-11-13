"""Krok karty w pudełku (PT-181, #18): wybór runnera, `boxWait`, polityka proxy, odmowa proxy → `denied`, zwolnienie.

Silnik (`engine.py`) woła stąd operacje kroku, reszta to szczegóły:

- `place(...)` w `_launch`, zanim cokolwiek zapisze: `None` — krok idzie na hosta (`HostRunner`, jak przed K-38),
  `Placement` — krok idzie do pudełka (`BoxRunner`), `BoxWaiting` — krok nie startuje i czeka (silnik zapisuje
  `step.waiting` i ponawia w następnym `tick`), `BoxError` — błąd karty (nigdy po cichu na hosta).
  Do pudełka idzie rodzaj kroku z `BOX_STEP_KINDS` (stała w `engine.py`), gdy `box.enabled`, projekt ma
  zatwierdzony profil i pudełko istnieje; wznowienie (`parent_step`) bierze runner z wpisu wznawianego kroku
  i niczego nie wybiera na nowo.
  Kolejno: `box.enabled: false` → host (wznowienie kroku z pudełka → `BoxError`), przed wszystkimi dalszymi
  kontrolami (PT-289); `boxWait: environment` albo odroczona zgoda środowiska (PT-290 → `environment`) → czeka; projekt z ekspozycją v1 (PT-264) → czeka; budowa/reset → czeka; brak pudełka → host; brak filtrowania NIC albo
  hash migawki różny od hasha profilu → `boxWait: rebuild` i czeka; `box: per-card` → `copy_for_card` (raz na
  kartę); pudełko zatrzymane → `boxWait: start`, `incus start`, `incus exec … true`, menedżer usług `agent`,
  adres IP, a potem `boxWait` czyszczone.
- `before_start(...)` tuż przed `BoxRunner.start`: `set_box_policy` w proxy z domenami profilu projektu i profili
  wszystkich kart z krokami w tym pudełku oraz rejestracja dziennika odmów **tego kroku** (`denied_log_path`). Dziennik
  leży w katalogu stanu usługi na hoście, nie w katalogu kroku: katalog kroku jest rw w pudełku, a proxy (proces
  usługi) dopisuje do pliku — dowiązanie podłożone z pudełka skierowałoby zapis usługi w dowolne miejsce, a wpis
  podrobiony przez agenta zmieniłby wynik kroku.
  Tu też `box.use` w dzienniku pudełka — przed startem, bo BoxRunner zatrzymuje pudełko na nowe montowania tylko,
  gdy według tego dziennika nic w nim nie biegnie (`start_failed` cofa wpis, gdy start się nie uda).
- `configure_background(...)` przekazuje identyfikator proxy w HTTP(S)_PROXY oraz atrapy K3 i argumenty programu;
  `recover_network(...)` odtwarza te same identyfikatory, atrapy i polityki po restarcie usługi.
- `started(...)` po starcie: (raz na kartę) `box.risk` przy `docker.enabled` bez K3.
- `after_step(...)` w `_finish`: wynik kroku zestawiony z jego dziennikiem odmów. Pytanie, które wymienia domenę
  z dziennika, albo błąd, którego dziennik (`step.log`, `box-exec.log`) wymienia taką domenę → `denied`
  z `command: "network: <domena>"` (pytanie o domenę z PT-172, bez `envAnswer`). Sam wpis w dzienniku, którego
  wynik kroku nie dotyczy, daje tylko notkę `proxy-denied.md` (przy `done`/`error`).
- `release(...)` na końcu `_finish`: `box.release` i polityka proxy przestawiona na pozostałe kroki w pudełku
  (albo zdjęta, gdy nie ma żadnego).

Jedno pudełko ma jedną politykę domen na adres IP. Identyfikator z Proxy-Authorization rozdziela dzienniki kroków;
ruch bez identyfikatora ma osobny dziennik pudełka i nie zmienia wyników kroków. Opis: docs/tide-daemon.md
(„Krok w pudełku w silniku”).
"""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field

from ..box import builder as bb
from ..box import k3
from ..box import profile as bp
from ..box.ports import PortPool, PoolExhausted
from ..box import proxy as bproxy
from ..box import settings as bsettings
from ..box import state as bs
from ..box import lifecycle
from ..box.incus import IncusError
from ..box.runner import BoxRunner, MountsBusy
from ..tasks import agentcmd
from . import boxaccess, boxwait
from .state import fold

RUNNER = "box"                       # wartość pola `runner` wpisu kroku (brak pola = host)
DENIED_LOG = "proxy-denied.log"
NOTE_FILE = "proxy-denied.md"
RISK_TEXT = k3.K1_NOTE
WAIT_ENVIRONMENT = "czeka na środowisko"
WAIT_BUILD = "czeka na pudełko (budowa)"
WAIT_REBUILD = "czeka na pudełko (do przebudowy)"
WAIT_START = "czeka na pudełko (start)"
NETWORK_KINDS = ("CONNECT", "HTTP")  # rodzaje odmów, które są „brakiem domeny na liście” (nie ADDRESS, K3…)
LOG_TAIL = 20_000


class BoxWaiting(Exception):
    """Krok nie startuje teraz; `reason` trafia do `step.waiting`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BoxError(Exception):
    """Krok, który musi iść do pudełka, nie może (np. wznowienie kroku z pudełka przy wyłączonym pudełku)."""


@dataclass
class Placement:
    slug: str
    box: str
    ip: str
    per_card: bool
    profile: dict
    runner: object
    docker: bool = False
    port_owner: str = ""
    port_env: dict = field(default_factory=dict)

    def entry(self) -> dict:
        """Pola wpisu kroku (`intent`, `result`, `step.started`): po nich silnik odnajduje krok po restarcie."""
        fields = {"runner": RUNNER, "box": self.box, "boxSlug": self.slug, "boxIp": self.ip, "boxAccess": 1}
        if self.port_owner:
            fields["boxPortOwner"] = self.port_owner
        return fields


def reserve_ports(engine, placement: Placement, card: str, step: str) -> None:
    card_profile = bp.load_card(placement.slug, card) or {"tide-box": bp.SCHEMA_VERSION}
    profile = bp.merge(placement.profile, card_profile).profile
    if not (profile.get("ports") or {}).get("pool"):
        return
    owner = f"{card}/{step}"
    # Zamiar przed rezerwacją: restart odzyska także przydział sprzed zamiaru step-start.
    if not any(e.get("type") == "box.ports" and e.get("owner") == owner
               for e in engine.journal.read(card)):
        engine.journal.append(card, "box.ports", step=step, box=placement.box, boxSlug=placement.slug, owner=owner)
    try:
        env = PortPool(placement.slug, placement.box).acquire(owner, profile)
    except PoolExhausted as exc:
        raise BoxWaiting(str(exc)) from None
    placement.port_owner, placement.port_env = owner, env


def release_ports(step: dict) -> None:
    if step.get("boxPortOwner"):
        PortPool(step["boxSlug"], step["box"]).release(step["boxPortOwner"])


# ------------------------------------------------------------ zależności (podmieniane w testach przez silnik)

def settings_of(engine) -> bsettings.BoxSettings:
    source = getattr(engine, "box_settings_source", None)
    return source() if source is not None else bsettings.load(engine.settings)


def builder_of(engine, settings) -> bb.Builder:
    factory = getattr(engine, "box_builder", None)
    builder = factory() if factory is not None else bb.Builder(settings)
    builder.steps_source = lambda slug, name: lifecycle.active_steps(builder, slug, name, engine)
    return builder


def proxy_of(engine):
    return getattr(engine, "box_proxy", None) or bproxy.running()


def runner_of(engine, settings, slug: str, card: str = "", per_card: bool = False):
    factory = getattr(engine, "box_runner_factory", None)
    if factory is not None:
        return factory(settings, slug, card, per_card)
    return BoxRunner(settings, slug, card=card, per_card=per_card, proxy_base=proxy_base(proxy_of(engine)),
                     journal=(lambda kind, fields: engine.journal.append(card, kind, **fields)) if card else None)


def proxy_base(proxy) -> str:
    if proxy is None or not getattr(proxy, "listen", "") or not getattr(proxy, "port", 0):
        return ""
    return f"http://{proxy.listen}:{proxy.port}"


def denied_log_path(slug: str, step_id: str) -> str:
    """Dziennik odmów kroku: `~/.local/state/tide/box/<slug>/steps/<krok>/proxy-denied.log` (poza pudełkiem)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", step_id or "") or step_id in (".", ".."):
        raise BoxError(f"niepoprawny identyfikator kroku: {step_id!r}")
    return os.path.join(bb.state_dir(slug), "steps", step_id, DENIED_LOG)


# ------------------------------------------------------------ wybór

@contextmanager
def _start_lock(slug):
    """Szeregowy wykonawca kart odracza start zamiast czekać na budowę w tle."""
    try:
        with bs.lifecycle_lock(slug, blocking=False):
            yield
    except bb.BoxBusy as exc:
        raise BoxWaiting(f"{WAIT_BUILD}: {exc}") from None


def place(engine, card_id: str, slug: str, kind: str, parent_step: str, kinds) -> Placement | None:
    st = fold(engine.journal.read(card_id))
    forced = False
    if parent_step:
        # Wznowienie dziedziczy runner wznawianego kroku (wpis w dzienniku), bez ponownego wyboru.
        parent = st.steps.get(parent_step) or {}
        if not parent:
            raise BoxError("brak wpisu wznawianego kroku")
        forced = parent.get("runner") == RUNNER
    # PT-289: wyłącznik pudełka działa przed zgodą środowiska, ekspozycją projektu, profilem, admission
    # i Incusem — przy `box.enabled: false` nowy krok i wznowienie kroku hostowego idą na hosta (HostRunner),
    # także przy zapisanym `boxWait` (environment/rebuild) i niezatwierdzonej ekspozycji. Wznowienie kroku
    # z pudełka nigdy nie przechodzi na hosta: odmowa, zanim cokolwiek zostanie uruchomione.
    settings = settings_of(engine)
    if not settings.enabled:
        if forced:
            raise BoxError("wznawiany krok biegł w pudełku, a pudełko jest wyłączone (box.enabled)")
        return None
    # Zgoda na środowisko blokuje wykonanie przy włączonych pudełkach także bez profilu i na hoście.
    # Planowanie nadal może przygotować profil do zatwierdzenia.
    if (kind in kinds or forced) and boxwait.environment_pending(st):
        if boxwait.card_box_wait(st) != boxwait.ENVIRONMENT:
            # PT-290 (D1): plan zatwierdzony przy wyłączonym pudełku ma tylko odroczoną zgodę. Ponowne włączenie
            # nie zamienia samego zatwierdzenia planu w zgodę na środowisko: karta dostaje `environment`.
            boxwait.set_box_wait(engine.journal, card_id, boxwait.ENVIRONMENT, source="box-enabled")
        raise BoxWaiting(WAIT_ENVIRONMENT)
    if parent_step:
        if not forced:
            return None
    elif kind not in kinds:
        return None          # planowanie i inne rodzaje zostają na hoście (bwrap)
    # V1 wymaga projektu i odebranych urządzeń PT-264. Przy włączonych pudełkach ani dawny profil
    # karty, ani restart nie przywracają tu HostRunnera (wyłącznik pudełka: wyżej, PT-289).
    from ..box import exposure
    if slug == exposure.PROJECT:
        raise BoxWaiting("czeka na manifest projektu i odebrane montowania PT-264; "
                         "dawne zgody kart nie aktywują wariantu v1")
    rebuilding = boxwait.card_box_wait(st) == boxwait.REBUILD
    try:
        profile = bp.load_project(slug)
    except bp.ProfileError as exc:
        raise BoxError(f"profil środowiska projektu {slug} jest niepoprawny: {exc}") from None
    if profile is None:
        if forced:
            raise BoxError(f"wznawiany krok biegł w pudełku, a projekt {slug} nie ma już zatwierdzonego profilu")
        if rebuilding:
            raise BoxWaiting(WAIT_REBUILD)
        return None
    from ..box import refguard
    others = refguard.other_cards(engine._repo(card_id), card_id)
    if others:
        raise BoxWaiting("czeka na repozytorium (jedna karta naraz): " + ", ".join(others))
    builder = builder_of(engine, settings)
    try:
        effective = bp.merge(profile, bp.load_card(slug, card_id) or {"tide-box": 1}).profile
    except bp.ProfileError as exc:
        raise BoxError(f"profil środowiska karty {card_id} jest niepoprawny: {exc}") from None
    full = bp.with_defaults(profile)
    # Nawet zakończony krok może pozostawić procesy we wspólnym pudełku.
    # Paczki wymagają osobnej przestrzeni montowań, niezależnie od rezerwacji kroków.
    has_packs = bool(effective.get("packs"))
    per_card = effective.get("box", "per-project") == "per-card" or has_packs
    if forced:
        name = parent.get("box") or ""
        if parent.get("boxSlug") != slug or name not in (bs.box_name(slug), bs.card_box_name(slug, card_id)):
            raise BoxError("wznawiany krok ma niespójną tożsamość pudełka")
        per_card = name == bs.card_box_name(slug, card_id)
        if has_packs and not per_card:
            raise BoxError("paczki wymagają osobnego pudełka karty; nie można wznowić kroku w pudełku projektu")
    status = builder.status(slug, card=card_id if forced and per_card else "", profile=profile)
    if status.error:
        raise BoxWaiting(f"czeka na pudełko ({status.error})")
    if status.state in (bs.BUILDING, bs.RESETTING) or status.journal_state in (bs.BUILDING, bs.RESETTING):
        raise BoxWaiting(WAIT_BUILD)
    if not status.exists:
        # Nieudana przebudowa może usunąć starą instancję. To nadal oczekiwanie na pudełko,
        # a nie projekt, który nigdy go nie miał — brak instancji nie znosi zatwierdzonej granicy.
        if rebuilding or status.journal_state == bs.FAILED:
            boxwait.set_box_wait(engine.journal, card_id, boxwait.REBUILD, box=status.box,
                                 why="brak instancji po budowie lub podczas oczekiwania na przebudowę")
            raise BoxWaiting(WAIT_REBUILD)
        if forced:
            raise BoxWaiting(f"czeka na pudełko ({status.box} nie istnieje)")
        return None          # projekt z profilem, ale bez zbudowanego pudełka — jak dziś
    _require_compatible(engine, card_id, builder, status)
    name = status.box
    # Odczyt zgodnego pudełka nie rezerwuje projektu. Budowa mogła zdobyć blokadę
    # od tego czasu; wszystkie dalsze operacje przygotowania muszą wtedy ustąpić.
    with _start_lock(slug):
        if per_card and not forced:
            name = bs.card_box_name(slug, card_id)
            try:
                builder.copy_for_card(slug, card_id)        # nowy przepis odtwarza nieaktywną kopię
            except (bb.BoxBusy, bb.BuildError, IncusError, OSError) as exc:
                raise BoxWaiting(f"czeka na pudełko (kopia karty: {exc})") from None
            _require_compatible(engine, card_id, builder, builder.status(slug, card=card_id, profile=profile))
        try:
            builder.ensure_home(slug, card_id if per_card else "")
        except (bb.BuildError, IncusError, OSError, ValueError) as exc:
            raise BoxWaiting(f"czeka na pudełko (dom: {exc})") from None
        runner = runner_of(engine, settings, slug, card_id, per_card)
        ip = _ready(engine, card_id, builder, runner, settings, name, slug, per_card)
    if boxwait.card_box_wait(fold(engine.journal.read(card_id))) in (boxwait.START, boxwait.REBUILD):
        boxwait.set_box_wait(engine.journal, card_id, "", box=name)
    return Placement(slug, name, ip, per_card, profile, runner, bool(full["docker"]["enabled"]))


def _require_compatible(engine, card_id: str, builder, status: bs.BoxStatus) -> None:
    """Brak filtrowania NIC albo hash migawki różny od hasha profilu → `boxWait: rebuild`; krok nie startuje."""
    if status.error:
        raise BoxWaiting(f"czeka na pudełko ({status.error})")
    same = bool(status.profile_hash) and status.recipe_hash == status.profile_hash and not status.diff
    why = "brak filtrowania NIC" if not status.nic_filtering else "hash przepisu niezgodny"
    if status.nic_filtering and same and status.state != bs.FAILED:
        try:
            clean = next((s for s in builder.incus.snapshot_list(status.box) if s.get("name") == bs.SNAPSHOT), None)
        except (IncusError, OSError, ValueError) as exc:
            raise BoxWaiting(f"czeka na pudełko (migawka clean: {exc})") from None
        # Odcisk live nie dowodzi istnienia ani zgodności punktu odtworzenia.
        stamp = bs.parse_stamp((clean.get("config") or {}).get(bs.RECIPE_KEY)) if clean is not None else None
        snapshot_hash = stamp.get("hash", "") if isinstance(stamp, dict) else (stamp or "")
        if snapshot_hash and snapshot_hash == status.profile_hash:
            return
        why = "brak migawki clean" if clean is None else "hash migawki clean niezgodny"
    boxwait.set_box_wait(engine.journal, card_id, boxwait.REBUILD, box=status.box, why=why)
    raise BoxWaiting(WAIT_REBUILD)


def _ready(engine, card_id: str, builder, runner, settings, name: str, slug: str, per_card: bool) -> str:
    """Pudełko działa i odpowiada; zwraca jego adres IP. Zatrzymane → `boxWait: start` i `incus start`."""
    incus = builder.incus
    try:
        inst = incus.instance(name)
    except (IncusError, OSError, ValueError) as exc:
        raise BoxWaiting(f"czeka na pudełko (incus: {exc})") from None
    if inst is None:
        raise BoxWaiting(f"czeka na pudełko ({name} nie istnieje)")
    sleep = getattr(engine, "box_sleep", None) or time.sleep
    deadline = time.monotonic() + max(1.0, float(settings.ip_wait_seconds))
    if str(inst.get("status", "")).lower() != "running":
        boxwait.set_box_wait(engine.journal, card_id, boxwait.START, box=name)
        try:
            lifecycle.Lifecycle(builder).start(slug, card_id if per_card else None)
            runner.wait_user_manager(name)
        except (bb.BuildError, IncusError, OSError, ValueError, agentcmd.StepError) as exc:
            raise BoxWaiting(f"{WAIT_START}: {exc}") from None
    else:
        # Także działające pudełko musi być jedynym zasobem; start po restarcie
        # nie może ominąć rekonstrukcji globalnej własności.
        from ..box import admission
        try:
            req = admission.request(incus, 'start', name, slug=slug, card=card_id if per_card else None)
            with admission.operation(incus, req):
                incus.config_set(name, lifecycle.limits(builder.load_profile(slug)))
        except (bb.BuildError, IncusError, OSError, ValueError) as exc:
            raise BoxWaiting(f'{WAIT_START}: {exc}') from None
    while True:
        try:
            ip = bb.instance_ip(incus.instance(name), settings.nic)
        except (IncusError, OSError, ValueError):
            ip = ""
        if ip:
            return ip
        if time.monotonic() >= deadline:
            raise BoxWaiting(f"czeka na pudełko ({name} bez adresu IPv4)")
        sleep(0.5)


# ------------------------------------------------------------ polityka proxy

def _box_steps(engine, box: str, exclude: str = "") -> list:
    """(karta, wpis kroku) wszystkich trwających kroków w pudełku `box`, od najstarszego."""
    out = []
    for card in engine.journal.cards():
        for step in fold(engine.journal.read(card)).running():
            if step.get("runner") == RUNNER and step.get("box") == box and step.get("step") != exclude:
                out.append((card, step))
    out.sort(key=lambda pair: int(pair[1].get("seq") or 0))
    return out


def _policy_for(slug: str, cards: list):
    profile = bp.load_project(slug)
    card_profiles = {}
    for card in dict.fromkeys(cards):
        loaded = bp.load_card(slug, card)
        if loaded is not None:
            card_profiles[card] = loaded
    return bproxy.compose_policy(profile, card_profiles)


def _apply_policy(engine, slug: str, box: str, ip: str, cards: list, contexts=()):
    proxy = proxy_of(engine)
    if proxy is None:
        raise agentcmd.StepError("proxy pudełka (#5) nie działa — krok w pudełku bez proxy odmówiony")
    if contexts:
        domains = list(dict.fromkeys(d for ctx in contexts for d in ctx["domains"]))
        ollama, full = any(ctx["ollama"] for ctx in contexts), any(ctx["full"] for ctx in contexts)
    else:
        policy = _policy_for(slug, cards)
        domains, ollama, full = list(policy.domains), policy.ollama, policy.full
    # Ruch bez tożsamości kroku (np. dockerd pudełka) ma osobny dziennik. Nie wolno przypisać go
    # ostatniemu startującemu krokowi. `register_step` wybiera dziennik tylko z identyfikatora żądania.
    log_path = os.path.join(bb.state_dir(slug), "proxy-denied.log")
    proxy.set_box_policy(ip, domains, ollama, log_path, full=full, box=box)


def before_start(engine, placement: Placement, card_id: str, step_id: str, program: str = "claude") -> str:
    """`set_box_policy` przed startem kroku; zwraca ścieżkę dziennika odmów kroku."""
    try:
        return _reserve_start(engine, placement, card_id, step_id, program)
    except BoxWaiting as exc:
        # Po zapisaniu intent trzeba utrwalić także nieudany start przez standardową ścieżkę _act.
        raise agentcmd.StepError(exc.reason) from exc


def _reserve_start(engine, placement, card_id, step_id, program):
    with _start_lock(placement.slug):
        # Od wyboru miejsca CLI mogło zatrzymać/resetować/usunąć pudełko. Ponowna kontrola
        # i gotowość są atomowe względem operacji destrukcyjnych aż do trwałego box.use.
        builder = builder_of(engine, settings_of(engine))
        _require_compatible(engine, card_id, builder,
                            builder.status(placement.slug, card_id if placement.per_card else None))
        placement.ip = _ready(engine, card_id, builder, placement.runner, settings_of(engine),
                              placement.box, placement.slug, placement.per_card)
        if boxwait.card_box_wait(fold(engine.journal.read(card_id))) == boxwait.START:
            boxwait.set_box_wait(engine.journal, card_id, "", box=placement.box)
        return _before_start(engine, placement, card_id, step_id, program)


def start_lock(placement):
    """Od sprawdzenia montowań do startu inny proces nie może zarezerwować pudełka."""
    return _start_lock(placement.slug) if placement is not None else nullcontext()


def check_mounts(placement, step):
    """Zajęte pudełko to oczekiwanie, zanim powstanie zamiar step-start."""
    try:
        placement.runner.ensure_devices(step, check_only=True)
    except MountsBusy as exc:
        raise BoxWaiting("czeka na zwolnienie pudełka: " + str(exc)) from None


def prepare_runner(engine, placement, step):
    """Limity z aktualnego profilu przed ewentualnym restartem na montowania, kontrola swapu po nim."""
    try:
        with _start_lock(placement.slug):
            builder = builder_of(engine, settings_of(engine))
            try:
                builder.incus.config_set(placement.box, lifecycle.limits(builder.load_profile(placement.slug)))
                placement.runner.ensure_devices(step)
                lifecycle.require_no_swap(builder.incus, placement.box)
            except (bb.BuildError, IncusError) as exc:
                raise agentcmd.StepError(f"przygotowanie pudełka {placement.box}: {exc}") from exc
    except BoxWaiting as exc:
        raise agentcmd.StepError(exc.reason) from exc


def _before_start(engine, placement, card_id, step_id, program):
    log_path = denied_log_path(placement.slug, step_id)
    os.makedirs(os.path.dirname(log_path), mode=0o700, exist_ok=True)
    try:
        os.unlink(log_path)              # ponowiony krok o tym samym id zaczyna z pustym dziennikiem
    except FileNotFoundError:
        pass
    cards = [card for card, _ in _box_steps(engine, placement.box)] + [card_id]
    proxy = proxy_of(engine)
    if proxy is None:
        raise agentcmd.StepError("proxy pudełka nie działa")
    policy = _policy_for(placement.slug, [card_id])
    boxaccess.create(proxy, placement, step_id, program, log_path, policy)
    _apply_policy(engine, placement.slug, placement.box, placement.ip, cards)
    # `box.use` przed `BoxRunner.start`: runner zatrzymuje pudełko na nowe montowania tylko, gdy według dziennika
    # pudełka nic w nim nie biegnie (poza własnym krokiem) — wpis musi istnieć, zanim krok naprawdę wystartuje.
    bs.mark_in_use(placement.slug, placement.box, step_id)
    return log_path


def start_failed(engine, placement: Placement, step_id: str) -> None:
    release_ports(placement.entry())
    boxaccess.revoke(proxy_of(engine), step_id, denied_log_path(placement.slug, step_id))
    if step_id in bs.running_steps(placement.slug, placement.box):
        bs.mark_released(placement.slug, placement.box, step_id)
    proxy = proxy_of(engine)
    if proxy is not None:
        rest = _box_steps(engine, placement.box, exclude=step_id)
        if rest:
            _apply_policy(engine, placement.slug, placement.box, placement.ip, [card for card, _ in rest])
        else:
            proxy.clear_box_policy(placement.ip)


def started(engine, placement: Placement, card_id: str, step_id: str, program: str) -> None:
    if placement.docker and not k3.k3_active(program) and not any(
            e.get("type") == "box.risk" and e.get("risk") == "K1" for e in engine.journal.read(card_id)):
        engine.journal.append(card_id, "box.risk", risk="K1", box=placement.box, step=step_id, program=program,
                              text=RISK_TEXT)


def release(engine, card_id: str, step: dict) -> None:
    """Koniec kroku w pudełku: `box.release` i polityka proxy dla pozostałych kroków (albo zdjęta)."""
    name, slug, box, ip = step.get("step") or "", step.get("boxSlug") or "", step.get("box") or "", step.get("boxIp") or ""
    if not slug or not box:
        return
    _release_resources(engine, step)
    proxy = proxy_of(engine)
    if proxy is None or not ip:
        return
    rest = _box_steps(engine, box, exclude=name)
    if not rest:
        proxy.clear_box_policy(ip)
        return
    _apply_policy(engine, slug, box, ip, [card for card, _ in rest])


def _release_resources(engine, step: dict) -> None:
    """Idempotentne sprzątanie, także bez proxy; trwały wynik kroku jest podstawą ponowienia."""
    name, slug, box = step.get("step") or "", step.get("boxSlug") or "", step.get("box") or ""
    if not slug or not box:
        return
    release_ports(step)
    boxaccess.revoke(proxy_of(engine), name, denied_log_path(slug, name))
    if name in bs.running_steps(slug, box):
        bs.mark_released(slug, box, name)


def configure_background(engine, placement: Placement, card: str, step: str, parent: str, bg) -> None:
    bg.env.update(placement.port_env)
    root = ""
    st = fold(engine.journal.read(card))
    # Codex zapisuje sesję w CODEX_HOME. Wznowienie zachowuje pierwotny katalog, nawet po kilku próbach.
    while parent:
        saved = st.steps.get(parent) or {}
        root = saved.get("stepDir") or root
        parent = saved.get("parentStep") or ""
    boxaccess.configure(proxy_of(engine), placement, step, denied_log_path(placement.slug, step), bg,
                        codex_step_dir=root)
    from ..tasks import preparation
    preparation.prepare(placement.runner, bg)


def recover_network(engine, history=None) -> None:
    """Uzgadnia zwolnienia oraz polityki/K3; obejmuje oba okna awarii startu i końca kroku.

    `history` (K-98 #3, `trialhold.HistoryPhase` ticku): historia kart z odczytu fazy, bez osobnego odczytu.
    Przed pierwszą mutacją dotyczącą karty (zwolnienie portów, refguard i zasoby kroku, polityka sieci) świeży
    pełny odczyt musi być równy odczytowi fazy; inaczej mutacje tej karty są odmówione bez skutku, a stan sieci
    nie jest zapamiętany, więc kolejny tick uzgadnia go od nowa."""
    from . import trialhold
    proxy = proxy_of(engine)
    active, ended = [], []
    basis, confirmed = {}, {}

    def fresh(card) -> bool:
        """Świeży pełny odczyt przed mutacją karty (raz na kartę w tym uzgodnieniu)."""
        if history is None:
            return True
        if card not in confirmed:
            confirmed[card] = history.unchanged(card, basis[card])
            if not confirmed[card]:
                print(f"tide-daemon: przepływ karty {card}: historia zmieniła się po odczycie — "
                      f"uzgodnienie sieci pudełka odmówione", flush=True)
        return confirmed[card]

    for card in engine.journal.cards():
        try:
            # PT-288: wspólny ścisły odczyt i model tożsamości zamiast lokalnej reguły zdarzeń.
            # Nieznana lub niespójna historia karty: bez zwolnień, refguard ani polityk sieci.
            # K-105 PT-415: stan procesu karty (model tożsamości i fold) z pamięci według wersji dziennika, gdy mutacje
            # porównuje świeżo faza ticku (`fresh`); bez fazy (odtworzenie zamiaru startu) świeży odczyt bez pamięci.
            events, whole = trialhold.process_state(engine.journal, card, history, fresh=history is None)
            basis[card] = events
        except (OSError, ValueError, TypeError, KeyError):
            continue
        # Zachowujemy rezerwacje i migawki dawnych prób; odtwarzanie nie może
        # uruchamiać refguard.finish ani zwalniać WIP po zwykłym done.
        protected = {name for name, identity in whole.process_identities.items()
                     if identity.held and isinstance(name, str)}
        kept = [e for e in events if not trialhold.held(e) and e.get("step") not in protected
                and e.get("key") not in protected]
        # Bez pominiętych zdarzeń stan jest stanem procesu karty; nowe złożenie tylko dla historii z próbami K-38.
        st = whole if len(kept) == len(events) else fold(kept)
        events = kept
        # Rezerwacja mogła poprzedzić przerwany start, bez wpisu step-start. Brak kroku i zamiaru
        # oznacza, że nie uruchomiono procesu. Nie dotykamy rezerwacji trwającego zamiaru.
        for event in events:
            if event.get("type") == "box.ports" and event["step"] not in st.steps and (
                    "step-start", event["step"]) not in st.intents and fresh(card):
                release_ports({**event, "boxPortOwner": event["owner"]})
        active.extend((card, s) for s in st.running() if s.get("runner") == RUNNER)
        active.extend((card, {**i, "step": i["key"]}) for i in st.open_intents(include_background=True)
                      if i.get("action") == "step-start" and i.get("runner") == RUNNER and i["key"] not in st.steps)
        ended.extend((card, s) for name, s in st.steps.items() if name in st.finished and s.get("runner") == RUNNER)
        for (action, key), intent in st.intents.items():
            if (action == "step-start" and intent.get("runner") == RUNNER and key not in st.steps
                    and (action, key) in st.results and not st.results[(action, key)].get("ok")):
                # Dawny wynik ok=False mógł oznaczać proces zakończony przed potwierdzeniem startu.
                # Bez dowodu końca nie zwalniamy migawki ani zasobów; potwierdzony brak startu
                # obsługuje synchronicznie BoxRunner.start, anulując migawkę po błędzie Popen.
                from ..tasks import stepstop
                if engine._unit_state({**intent, "step": key}) != "inactive" or \
                        stepstop.read_exit_code(intent.get("stepDir") or "") is None:
                    continue
                ended.append((card, {**intent, "step": key}))
    signature = (proxy, tuple(sorted(s["step"] for _card, s in active)),
                 tuple(sorted(s["step"] for _card, s in ended)))
    if getattr(engine, "_box_network_state", None) == signature:
        return
    released = True
    for card, step in ended:
        if not fresh(card):
            released = False    # odmowa bez skutku; bez zapamiętania stanu kolejny tick uzgadnia od nowa
            continue
        try:
            from ..box import refguard
            repo = step.get("repo") or (fold(engine.journal.read(card)).started or {}).get("repo")
            if repo:
                guarded = refguard.finish(repo, card, step["step"], branch=step.get("branch") or "",
                                          active_only=True)
                if guarded:
                    engine.journal.append(card, "refs.guard", step=step["step"],
                                          ticket=step.get("ticket") or "", **guarded)
                    if guarded.get("errors"):
                        released = False  # Zachowujemy oryginalną migawkę i ponawiamy w każdym ticku.
        except (OSError, ValueError) as exc:
            released = False
            print(f"tide-daemon: przepływ karty {card}: kontrola refów po {step['step']}: {exc!r}", flush=True)
        try:
            _release_resources(engine, step)
        except (OSError, ValueError) as exc:
            released = False  # Nie zapamiętujemy sukcesu: następny tick ponawia także bez nowych zdarzeń.
            print(f"tide-daemon: przepływ karty {card}: zwolnienie pudełka po {step['step']}: {exc!r}", flush=True)
    if proxy is None:
        return
    stale = {card for card, _step in [*active, *ended] if not fresh(card)}
    if stale:
        # Polityka pudełka obejmuje kroki wielu kart; bez potwierdzonej historii każdej z nich nie zmieniamy
        # polityki ani jej nie zdejmujemy — zostaje dotychczasowa, a kolejny tick uzgadnia od nowa.
        return
    groups = {}
    errors = {}
    for card, step in active:
        slug, box, ip = step["boxSlug"], step["box"], step["boxIp"]
        cards, contexts = groups.setdefault((slug, box, ip), ([], []))
        cards.append(card)
        try:
            contexts.append(boxaccess.restore(proxy, step, denied_log_path(slug, step["step"])))
        except FileNotFoundError:
            # Starsze kroki nie miały tego pliku. Nowego klucza nie można podmienić biegnącemu agentowi.
            if step.get("boxAccess"):
                errors[step["step"]] = "brak zapisanego kontekstu sieci kroku"
        except (OSError, ValueError, KeyError) as exc:
            errors[step["step"]] = f"nie da się odtworzyć sieci kroku: {type(exc).__name__}"
    for (slug, box, ip), (cards, contexts) in groups.items():
        _apply_policy(engine, slug, box, ip, cards, contexts)
    active_ips = {ip for _slug, _box, ip in groups}
    for ip in {s.get("boxIp") for _card, s in ended} - active_ips - {None, ""}:
        proxy.clear_box_policy(ip)
    if released:
        engine._box_network_state = signature
    engine._box_network_errors = errors


# ------------------------------------------------------------ odmowa proxy → wynik kroku

def read_denied(path: str) -> list:
    """Wpisy dziennika odmów: [(domena, port, rodzaj)] w kolejności pliku."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return []
    with os.fdopen(fd, encoding="ascii", errors="replace") as fh:
        lines = fh.read(256 * 1024).splitlines()
    out = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 6 and parts[3]:
            out.append((parts[3].strip().lower(), parts[4].strip(), parts[5].strip()))
    return out


def _tail(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return ""
    with os.fdopen(fd, "rb") as fh:
        try:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - LOG_TAIL))
            return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""


def _mentions(text: str, host: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9.-])" + re.escape(host) + r"(?![a-z0-9-])", text.casefold()))


def after_step(step: dict, data: dict, out_dir: str) -> tuple:
    """(wynik, notka): wynik po zestawieniu z dziennikiem odmów kroku, notka Markdown albo `""`."""
    slug, name = step.get("boxSlug") or "", step.get("step") or ""
    if not slug or not name:
        return data, ""
    try:
        log_path = denied_log_path(slug, name)
        entries = read_denied(log_path)
        if entries:
            # Kopia do katalogu kroku dla recenzenta; nie podążamy za dowiązaniami
            # podłożonymi w out przez proces w pudełku.
            from ..box.runner import StepDir
            try:
                fd = os.open(log_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                with os.fdopen(fd, encoding="ascii", errors="replace") as source:
                    step_dir = os.path.dirname(out_dir)
                    StepDir(os.path.dirname(step_dir), step_dir).write(
                        os.path.join(out_dir, DENIED_LOG), source.read())
            except (OSError, agentcmd.StepError) as exc:
                print(f"tide-daemon: nie zapisano artefaktu odmów kroku {name}: {exc}", flush=True)
    except BoxError:
        return data, ""
    if not entries:
        return data, ""
    from .envconsent import network_domain
    hosts = [h for h in dict.fromkeys(h for h, _p, k in entries if k in NETWORK_KINDS) if network_domain(f"network: {h}")]
    result = data.get("result")
    if result == "question":
        hit = next((h for h in hosts if _mentions(str(data.get("question") or ""), h)), "")
        if hit:
            return {"result": "denied", "command": f"network: {hit}", "notes": data.get("notes") or []}, ""
    if result == "error":
        logs = _tail(os.path.join(out_dir, "step.log")) + "\n" + _tail(os.path.join(out_dir, "box-exec.log"))
        hit = next((h for h in hosts if _mentions(logs, h)), "")
        if hit:
            return {"result": "denied", "command": f"network: {hit}", "notes": data.get("notes") or []}, ""
    if result not in ("done", "error"):
        return data, ""
    counts: dict = {}
    for host, port, kind in entries:
        counts[(host, port, kind)] = counts.get((host, port, kind), 0) + 1
    lines = ["# Odmowy proxy w kroku", "",
             "Proxy pudełka odmówiło połączeń, których wynik kroku nie wskazuje jako przyczyny "
             "(dziennik odmów kroku):", ""]
    lines += [f"- `{host}:{port}` ({kind}) × {count}" for (host, port, kind), count in counts.items()]
    lines += ["", "Gdy domena jest potrzebna, dopisz ją do profilu karty (Środowisko karty).", ""]
    return data, "\n".join(lines)
