"""Powolny silnik testów (K-100, PT-417): deterministyczne opóźnienia w fiksturach i atrapach `daemon/tests/`.

Obciążenie maszyny losuje kolejność zdarzeń (tick monitora, start procesu kroku, zapis `result.json`, odpowiedź
`systemctl`, wątek testu oglądający stan). Powolny silnik wymusza te same kolejności opóźnieniami po stronie testu,
bez palników i bez zmian w `daemon/tide_daemon/`.

Włącza go zmienna `TIDE_TEST_SLOW=<profil>:<ziarno>:<przebieg>` (np. `domyslny:417:3`), którą ustawia
`tools/module_loop.py --slow`. Opóźnienie punktu to czysta funkcja klucza (profil, ziarno, przebieg), nazwy punktu
i numeru jego wywołania (`draw`), więc ten sam przebieg da się powtórzyć: wystarczy ta sama wartość zmiennej.
Liczniki wywołań w procesie testu są osobne dla każdego testu (`begin`), a w atrapach — w katalogu atrapy.
Bez zmiennej każda funkcja tego modułu nic nie robi, a atrapy i fikstury zachowują się bit w bit jak wcześniej.

Punkt opóźnienia dodaje się wpisem w `PROFILES` i wywołaniem `sleep("<punkt>")` w fiksturze albo
`_slow("<punkt>")` w atrapie (`flowfakes.install` wkleja `preamble` z punktami danej atrapy). Instrukcja:
docs/testing.md, „Pętla przebiegów modułu pod obciążeniem”.
"""
import inspect
import os
import threading
import time

ENV = "TIDE_TEST_SLOW"

# Punkt → (prawdopodobieństwo opóźnienia, najdłuższe opóźnienie [s]). Wartości są małe względem limitów
# oczekiwania w testach (wait_until 5–60 s), ale większe niż typowy czas zdarzenia na spokojnej maszynie.
PROFILES = {
    "domyslny": {
        "fixture.tick": (0.5, 0.3),           # FlowInProcess: przed `engine.tick()` w wątku monitora
        "fixture.callback": (0.5, 0.3),       # FlowInProcess: po ticku, przed callbackiem monitora (commit, publikacja)
        "fixture.observe": (0.7, 1.2),        # FlowInProcess: wątek testu po `wait_column` (opóźniona obserwacja)
        "claude.start": (0.5, 0.8),           # atrapa `claude`/`codex`: start procesu kroku
        "claude.result": (0.7, 1.5),          # atrapa `claude`/`codex`: zapis `result.json`
        "systemd-run.start": (0.5, 0.5),      # atrapa `systemd-run`: przed `exec` programu jednostki
        "systemctl": (0.3, 0.3),              # atrapa `systemctl`: przed odpowiedzią is-active/kill
        "service.poll": (1.0, 0.8),           # usługa przez prywatną magistralę: dodatek do TIDE_FLOW_POLL_MS
    },
}


def draw(key, point, number, probability, longest):
    """Opóźnienie [s] wywołania `number` punktu `point` dla klucza `key`; czysta funkcja (także w atrapach)."""
    import hashlib as _hashlib
    digest = _hashlib.sha256(f"{key}:{point}:{number}".encode("utf-8")).digest()
    chance = int.from_bytes(digest[:8], "big") / 2 ** 64
    share = int.from_bytes(digest[8:16], "big") / 2 ** 64
    return round(longest * share, 3) if chance < probability else 0.0


class Slow:
    """Ustawienia powolnego silnika z wartości zmiennej."""

    def __init__(self, profile, seed, run):
        if profile not in PROFILES:
            raise ValueError(f"{ENV}: nieznany profil {profile!r} (znane: {', '.join(sorted(PROFILES))})")
        self.profile, self.seed, self.run = profile, seed, run
        self.key = f"{profile}:{seed}:{run}"
        self.points = PROFILES[profile]

    def delay(self, point, number, scope=""):
        probability, longest = self.points[point]
        return draw(f"{self.key}{':' + scope if scope else ''}", point, number, probability, longest)


def parse(value):
    """`<profil>:<ziarno>:<przebieg>` → Slow; pusta wartość → None; zły format → ValueError (głośno, nie po cichu)."""
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or not all(parts) or not parts[2].isdigit():
        raise ValueError(f"{ENV}={value!r}: oczekiwano <profil>:<ziarno>:<przebieg>, np. domyslny:417:3")
    return Slow(parts[0], parts[1], int(parts[2]))


def current():
    return parse(os.environ.get(ENV, ""))


def active():
    return current() is not None


_lock = threading.Lock()
_counters = {}
_scope = [""]


def begin(scope):
    """Nowy zakres liczników (np. id testu): opóźnienia testu nie zależą od liczby wywołań w poprzednich testach."""
    with _lock:
        _scope[0] = scope
        _counters.clear()


def sleep(point):
    """Opóźnienie punktu w procesie testu; bez zmiennej nic nie robi. Zwraca przespany czas."""
    slow = current()
    if slow is None:
        return 0.0
    with _lock:
        number = _counters.get(point, 0)
        _counters[point] = number + 1
        scope = _scope[0]
    delay = slow.delay(point, number, scope)
    if delay:
        time.sleep(delay)
    return delay


def poll_ms(default):
    """TIDE_FLOW_POLL_MS dla usługi testowej: bez zmiennej `default` bez zmian, z nią — dodatek z punktu."""
    slow = current()
    if slow is None:
        return default
    return str(int(default) + int(slow.delay("service.poll", 0) * 1000))


def preamble(points, counters):
    """Kod wklejany na początek atrapy (po linii #!): `_slow(punkt)` z licznikiem wywołań w pliku katalogu
    `counters` (atrapy to osobne procesy). Bez zmiennej zwraca pusty napis — atrapa jest bit w bit dawna."""
    slow = current()
    if slow is None:
        return ""
    table = {point: slow.points[point] for point in points}
    return (
        "# PT-417: powolny silnik (TIDE_TEST_SLOW), wpisany przy instalacji atrapy.\n"
        "import builtins as _slow_builtins, fcntl as _slow_fcntl, os as _slow_os, time as _slow_time\n"
        + inspect.getsource(draw).replace("def draw(", "def _slow_draw(") +
        f"_SLOW_KEY, _SLOW_POINTS, _SLOW_DIR = {slow.key!r}, {table!r}, {counters!r}\n"
        "def _slow(point):\n"
        "    _slow_os.makedirs(_SLOW_DIR, exist_ok=True)\n"
        "    with _slow_builtins.open(_slow_os.path.join(_SLOW_DIR, point), 'a+', encoding='utf-8') as fh:\n"
        "        _slow_fcntl.flock(fh, _slow_fcntl.LOCK_EX)\n"
        "        fh.seek(0)\n"
        "        number = len(fh.read())\n"
        "        fh.write('x')\n"
        "    delay = _slow_draw(_SLOW_KEY, point, number, *_SLOW_POINTS[point])\n"
        "    if delay:\n"
        "        _slow_time.sleep(delay)\n"
    )
