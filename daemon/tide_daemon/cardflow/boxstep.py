"""Krok karty w pudełku (PT-181, #18): wybór runnera, `boxWait`, polityka proxy, odmowa proxy → `denied`, zwolnienie.

Silnik (`engine.py`) woła stąd pięć rzeczy, reszta to szczegóły:

- `place(...)` w `_launch`, zanim cokolwiek zapisze: `None` — krok idzie na hosta (`HostRunner`, jak przed K-38),
  `Placement` — krok idzie do pudełka (`BoxRunner`), `BoxWaiting` — krok nie startuje i czeka (silnik zapisuje
  `step.waiting` i ponawia w następnym `tick`), `BoxError` — błąd karty (nigdy po cichu na hosta).
  Do pudełka idzie rodzaj kroku z `BOX_STEP_KINDS` (stała w `engine.py`), gdy `box.enabled`, projekt ma
  zatwierdzony profil i pudełko istnieje; wznowienie (`parent_step`) bierze runner z wpisu wznawianego kroku
  i niczego nie wybiera na nowo.
  Kolejno: `boxWait: environment` → czeka; budowa/reset → czeka; brak pudełka → host; brak filtrowania NIC albo
  hash migawki różny od hasha profilu → `boxWait: rebuild` i czeka; `box: per-card` → `copy_for_card` (raz na
  kartę); pudełko zatrzymane → `boxWait: start`, `incus start`, `incus exec … true`, menedżer usług `agent`,
  adres IP, a potem `boxWait` czyszczone.
- `before_start(...)` tuż przed `BoxRunner.start`: `set_box_policy` w proxy z domenami profilu projektu i profili
  wszystkich kart z krokami w tym pudełku oraz z dziennikiem odmów **tego kroku** (`denied_log_path`). Dziennik
  leży w katalogu stanu usługi na hoście, nie w katalogu kroku: katalog kroku jest rw w pudełku, a proxy (proces
  usługi) dopisuje do pliku — dowiązanie podłożone z pudełka skierowałoby zapis usługi w dowolne miejsce, a wpis
  podrobiony przez agenta zmieniłby wynik kroku.
  Tu też `box.use` w dzienniku pudełka — przed startem, bo BoxRunner zatrzymuje pudełko na nowe montowania tylko,
  gdy według tego dziennika nic w nim nie biegnie (`start_failed` cofa wpis, gdy start się nie uda).
- `started(...)` po starcie: (raz na kartę) `box.risk` przy `docker.enabled` bez K3.
- `after_step(...)` w `_finish`: wynik kroku zestawiony z jego dziennikiem odmów. Pytanie, które wymienia domenę
  z dziennika, albo błąd, którego dziennik (`step.log`, `box-exec.log`) wymienia taką domenę → `denied`
  z `command: "network: <domena>"` (pytanie o domenę z PT-172, bez `envAnswer`). Sam wpis w dzienniku, którego
  wynik kroku nie dotyczy, daje tylko notkę `proxy-denied.md` (przy `done`/`error`).
- `release(...)` na końcu `_finish`: `box.release` i polityka proxy przestawiona na pozostałe kroki w pudełku
  (albo zdjęta, gdy nie ma żadnego).

Jedno pudełko ma jeden adres IP, a proxy jedną politykę na adres — w pudełku projektu (`box: per-project`) kroki
kilku kart dzielą dziennik odmów ostatnio ustawionej polityki. Dlatego `denied` nigdy nie powstaje z samego wpisu:
wynik kroku (jego pytanie albo jego dziennik) musi wskazywać tę domenę. Opis: docs/tide-daemon.md („Krok
w pudełku w silniku”).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from ..box import builder as bb
from ..box import k3
from ..box import profile as bp
from ..box import proxy as bproxy
from ..box import settings as bsettings
from ..box import state as bs
from ..box.incus import IncusError
from ..box.runner import BoxRunner
from ..tasks import agentcmd
from . import boxwait
from .state import fold

RUNNER = "box"                       # wartość pola `runner` wpisu kroku (brak pola = host)
DENIED_LOG = "proxy-denied.log"
NOTE_FILE = "proxy-denied.md"
RISK_TEXT = "poświadczenia agenta czytelne dla kodu w pudełku (grupa docker); pełna ochrona: K3"
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
    fields: dict = field(default_factory=dict)

    def entry(self) -> dict:
        """Pola wpisu kroku (`intent`, `result`, `step.started`): po nich silnik odnajduje krok po restarcie."""
        return {"runner": RUNNER, "box": self.box, "boxSlug": self.slug, "boxIp": self.ip}


# ------------------------------------------------------------ zależności (podmieniane w testach przez silnik)

def settings_of(engine) -> bsettings.BoxSettings:
    source = getattr(engine, "box_settings_source", None)
    return source() if source is not None else bsettings.load(engine.settings)


def builder_of(engine, settings) -> bb.Builder:
    factory = getattr(engine, "box_builder", None)
    return factory() if factory is not None else bb.Builder(settings)


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

def place(engine, card_id: str, slug: str, kind: str, parent_step: str, kinds) -> Placement | None:
    st = fold(engine.journal.read(card_id))
    forced = False
    if parent_step:
        # Wznowienie dziedziczy runner wznawianego kroku (wpis w dzienniku), bez ponownego wyboru.
        parent = st.steps.get(parent_step) or {}
        if parent.get("runner") != RUNNER:
            return None
        forced = True
    elif kind not in kinds:
        return None          # planowanie i inne rodzaje zostają na hoście (bwrap)
    settings = settings_of(engine)
    if not settings.enabled:
        if forced:
            raise BoxError("wznawiany krok biegł w pudełku, a pudełko jest wyłączone (box.enabled)")
        return None
    try:
        profile = bp.load_project(slug)
    except bp.ProfileError as exc:
        raise BoxError(f"profil środowiska projektu {slug} jest niepoprawny: {exc}") from None
    if profile is None:
        if forced:
            raise BoxError(f"wznawiany krok biegł w pudełku, a projekt {slug} nie ma już zatwierdzonego profilu")
        return None
    if boxwait.card_box_wait(st) == boxwait.ENVIRONMENT:
        raise BoxWaiting(WAIT_ENVIRONMENT)
    builder = builder_of(engine, settings)
    status = builder.status(slug, profile=profile)
    if status.error:
        raise BoxWaiting(f"czeka na pudełko ({status.error})")
    if status.state in (bs.BUILDING, bs.RESETTING) or status.journal_state in (bs.BUILDING, bs.RESETTING):
        raise BoxWaiting(WAIT_BUILD)
    if not status.exists:
        if forced:
            raise BoxWaiting(f"czeka na pudełko ({status.box} nie istnieje)")
        return None          # projekt z profilem, ale bez zbudowanego pudełka — jak dziś
    _require_compatible(engine, card_id, status)
    full = bp.with_defaults(profile)
    per_card = full.get("box") == "per-card"
    name = status.box
    if per_card:
        name = bs.card_box_name(slug, card_id)
        try:
            if builder.incus.instance(name) is None:
                builder.copy_for_card(slug, card_id)        # raz na kartę: kopia z migawki `clean`
        except (bb.BuildError, IncusError, OSError) as exc:
            raise BoxWaiting(f"czeka na pudełko (kopia karty: {exc})") from None
        _require_compatible(engine, card_id, builder.status(slug, card=card_id, profile=profile))
    runner = runner_of(engine, settings, slug, card_id, per_card)
    ip = _ready(engine, card_id, builder, runner, settings, name)
    if boxwait.card_box_wait(fold(engine.journal.read(card_id))) in (boxwait.START, boxwait.REBUILD):
        boxwait.set_box_wait(engine.journal, card_id, "", box=name)
    return Placement(slug, name, ip, per_card, profile, runner, bool(full["docker"]["enabled"]))


def _require_compatible(engine, card_id: str, status: bs.BoxStatus) -> None:
    """Brak filtrowania NIC albo hash migawki różny od hasha profilu → `boxWait: rebuild`; krok nie startuje."""
    if status.error:
        raise BoxWaiting(f"czeka na pudełko ({status.error})")
    same = bool(status.profile_hash) and status.recipe_hash == status.profile_hash and not status.diff
    if status.nic_filtering and same and status.state != bs.FAILED:
        return
    why = "brak filtrowania NIC" if not status.nic_filtering else "hash przepisu niezgodny"
    boxwait.set_box_wait(engine.journal, card_id, boxwait.REBUILD, box=status.box, why=why)
    raise BoxWaiting(WAIT_REBUILD)


def _ready(engine, card_id: str, builder, runner, settings, name: str) -> str:
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
            incus.start(name)
            while incus.exec(name, ["true"]) != 0:
                if time.monotonic() >= deadline:
                    raise BoxWaiting(WAIT_START)
                sleep(0.5)
            runner.wait_user_manager(name)
        except (IncusError, OSError, agentcmd.StepError) as exc:
            raise BoxWaiting(f"{WAIT_START}: {exc}") from None
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


def _apply_policy(engine, slug: str, box: str, ip: str, cards: list, log_path: str) -> None:
    proxy = proxy_of(engine)
    if proxy is None:
        raise agentcmd.StepError("proxy pudełka (#5) nie działa — krok w pudełku bez proxy odmówiony")
    profile = bp.load_project(slug)
    card_profiles = {}
    for card in dict.fromkeys(cards):
        try:
            loaded = bp.load_card(slug, card)
        except bp.ProfileError as exc:
            print(f"tide-daemon: profil karty {card}: {exc}", flush=True)
            continue
        if loaded is not None:           # karta bez własnego profilu = sam profil projektu
            card_profiles[card] = loaded
    policy = bproxy.compose_policy(profile, card_profiles)
    proxy.set_box_policy(ip, list(policy.domains), policy.ollama, log_path, full=policy.full, box=box)


def before_start(engine, placement: Placement, card_id: str, step_id: str) -> str:
    """`set_box_policy` przed startem kroku; zwraca ścieżkę dziennika odmów kroku."""
    log_path = denied_log_path(placement.slug, step_id)
    os.makedirs(os.path.dirname(log_path), mode=0o700, exist_ok=True)
    try:
        os.unlink(log_path)              # ponowiony krok o tym samym id zaczyna z pustym dziennikiem
    except FileNotFoundError:
        pass
    cards = [card for card, _ in _box_steps(engine, placement.box)] + [card_id]
    _apply_policy(engine, placement.slug, placement.box, placement.ip, cards, log_path)
    # `box.use` przed `BoxRunner.start`: runner zatrzymuje pudełko na nowe montowania tylko, gdy według dziennika
    # pudełka nic w nim nie biegnie (poza własnym krokiem) — wpis musi istnieć, zanim krok naprawdę wystartuje.
    bs.mark_in_use(placement.slug, placement.box, step_id)
    return log_path


def start_failed(placement: Placement, step_id: str) -> None:
    if step_id in bs.running_steps(placement.slug, placement.box):
        bs.mark_released(placement.slug, placement.box, step_id)


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
    if name in bs.running_steps(slug, box):
        bs.mark_released(slug, box, name)
    proxy = proxy_of(engine)
    if proxy is None or not ip:
        return
    rest = _box_steps(engine, box, exclude=name)
    if not rest:
        proxy.clear_box_policy(ip)
        return
    last_card, last = rest[-1]
    _apply_policy(engine, slug, box, ip, [card for card, _ in rest],
                  denied_log_path(slug, last.get("step") or ""))


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
        entries = read_denied(denied_log_path(slug, name))
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
