"""Katalogi danych Codexa (CODEX_HOME) — może ich być kilka.

Codex pisze dzienniki, nazwy wątków i bazę stanu do swojego katalogu danych:
- Codex Desktop i Codex z npm/tarballa — `~/.codex` (albo `$CODEX_HOME`),
- Codex CLI ze snapa (`/snap/bin/codex`, confinement strict) — `$SNAP_USER_DATA`, czyli
  `~/snap/codex/<rewizja>`: snap ustawia aplikacji `CODEX_HOME: $SNAP_USER_DATA` w snap.yaml
  i nie ma ona dostępu do ukrytych katalogów domowych (`~/.codex`). `~/snap/codex/current`
  to dowiązanie do bieżącej rewizji.

Domyślna lista (default_homes): sam `$CODEX_HOME`, gdy usługa ma tę zmienną (jak dotąd — ktoś
świadomie wskazał jeden katalog), inaczej `~/.codex` i `~/snap/codex/current`.

Starszych rewizji snapa (`~/snap/codex/<n>` poza bieżącą) nie czytamy: snapd przy odświeżeniu
kopiuje dane poprzedniej rewizji do nowej, więc stare katalogi niosą tylko kopie sesji sprzed
odświeżenia, które są też w bieżącym. Wyjątek to `snap revert` — wtedy bieżąca jest znów starsza
rewizja, a sesje z nowszej zostają w jej katalogu, dopóki snapd go nie usunie (decyzja w
docs/decisions.md).

resolve() rozwiązuje dowiązania przy każdym wywołaniu: po odświeżeniu snapa `current` wskazuje
nową rewizję, a proces Codexa trzyma otwarty plik pod ścieżką z numerem rewizji
(`/proc/<pid>/fd` pokazuje ścieżkę bez dowiązań) — przy porównaniu ścieżek obie strony muszą
być rozwiązane tak samo. Ta sama sesja w dwóch katalogach (np. skopiowana ręcznie) to jedna
sesja — czytelnicy zostawiają nowszą (newest()).
"""
import os

SESSIONS = "sessions"
SNAP_DIR = os.path.join("snap", "codex")
# Fragment ścieżki, po którym rozpoznajemy katalog danych Codexa ze snapa (także w testach,
# gdzie leży w katalogu tymczasowym).
SNAP_MARK = os.sep + SNAP_DIR + os.sep


def default_homes(env=None) -> list[str]:
    """$CODEX_HOME albo ~/.codex i ~/snap/codex/current (nierozwiązane — resolve() przy użyciu)."""
    env = os.environ if env is None else env
    if env.get("CODEX_HOME"):
        return [env["CODEX_HOME"]]
    home = env.get("HOME") or os.path.expanduser("~")
    return [os.path.join(home, ".codex"), os.path.join(home, SNAP_DIR, "current")]


def resolve(roots) -> list[str]:
    """Katalogi bez dowiązań i bez powtórzeń, w podanej kolejności. Przyjmuje napis albo listę."""
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    out: list[str] = []
    for root in roots or ():
        if not root:
            continue
        real = os.path.realpath(os.path.expanduser(os.fspath(root)))
        if real not in out:
            out.append(real)
    return out


def sessions_dirs(roots) -> list[str]:
    """<home>/sessions każdego katalogu danych."""
    return [os.path.join(home, SESSIONS) for home in resolve(roots)]


def is_snap(path: str) -> bool:
    """Plik albo katalog z danych Codexa ze snapa (~/snap/codex/<rewizja>/…)."""
    return SNAP_MARK in os.path.realpath(path) + os.sep if path else False


def newest(items, key, stamp) -> list:
    """Jeden element na klucz (np. id sesji) — ten z największym stamp; kolejność pierwszych wystąpień."""
    best: dict = {}
    order: list = []
    for item in items:
        k = key(item)
        if k not in best:
            order.append(k)
            best[k] = item
        elif stamp(item) > stamp(best[k]):
            best[k] = item
    return [best[k] for k in order]
