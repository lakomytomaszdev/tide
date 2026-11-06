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
odświeżenia, które są też w bieżącym. Świadomie tracimy dwa przypadki (decyzja 65 w
docs/decisions.md):
- `snap revert` — bieżąca jest znów starsza rewizja, a sesje z nowszej zostają w jej katalogu,
  dopóki snapd go nie usunie,
- proces Codexa ze snapa, który przeżył wymuszone odświeżenie (snapd odświeża mimo działającej
  aplikacji po terminie), dalej pisze do katalogu starej rewizji — do końca tej sesji Tide nie widzi
  jej nowych wpisów.

resolve() rozwiązuje dowiązania przy każdym wywołaniu: po odświeżeniu snapa `current` wskazuje
nową rewizję, a proces Codexa trzyma otwarty plik pod ścieżką z numerem rewizji
(`/proc/<pid>/fd` pokazuje ścieżkę bez dowiązań) — przy porównaniu ścieżek obie strony muszą
być rozwiązane tak samo.

Kopia między katalogami: ten sam plik sesji w dwóch katalogach danych (np. przeniesiony ręcznie)
rozpoznajemy po ścieżce względem sessions/ (file_key) — nazwa `rollout-<czas>-<wątek>[_<segment>].jsonl`
niesie id wątku i segmentu — i zostawiamy nowszy (unique_files()). To jedyne łączenie między
katalogami. Segmenty jednego wątku (Codex Desktop dzieli długi wątek na pliki z tym samym id i
przyrostkiem `_<segment>`) to różne pliki: raport i worktrees widzą je osobno jak dotąd, a karty
Agents i KRunner łączą je po id same (newest()).
"""
import os
from .box import logfiles

SESSIONS = "sessions"
SNAP_DIR = os.path.join("snap", "codex")
SNAP_CODEX = "/snap/bin/codex"  # program Codexa ze snapa (dowiązanie do /usr/bin/snap)
DESKTOP_CODEX = "/usr/lib/chatgpt/resources/codex"


def default_homes(env=None) -> list[str]:
    """$CODEX_HOME albo ~/.codex i ~/snap/codex/current (nierozwiązane — resolve() przy użyciu)."""
    env = os.environ if env is None else env
    extra = box_homes(env)
    if env.get("CODEX_HOME"):
        return [env["CODEX_HOME"], *extra]
    home = env.get("HOME") or os.path.expanduser("~")
    return [os.path.join(home, ".codex"), os.path.join(home, SNAP_DIR, "current"), *extra]


def box_homes(env=None) -> list[str]:
    from .box.homes import codex_roots
    return codex_roots(env)


def resolve(roots) -> list[str]:
    """Katalogi bez dowiązań i bez powtórzeń, w podanej kolejności. Przyjmuje napis albo listę."""
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    out: list[str] = []
    for root in roots or ():
        if not root:
            continue
        path = os.path.abspath(os.path.expanduser(os.fspath(root)))
        real = path if logfiles.is_box(path) else os.path.realpath(path)
        if real not in out:
            out.append(real)
    return out


def sessions_dirs(roots) -> list[str]:
    """<home>/sessions każdego katalogu danych."""
    return [os.path.join(home, SESSIONS) for home in resolve(roots)]


def is_snap(path: str, home: str | None = None) -> bool:
    """Plik albo katalog z danych Codexa ze snapa: <HOME>/snap/codex/<rewizja>/… (HOME z argumentu albo
    środowiska). Inny katalog z „snap/codex” w ścieżce (np. CODEX_HOME=/data/snap/codex/x) się nie liczy."""
    if not path:
        return False
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    prefix = os.path.realpath(os.path.join(home, SNAP_DIR)) + os.sep
    return os.path.realpath(path).startswith(prefix)


def file_key(path: str, dirs) -> str:
    """Tożsamość pliku sesji między katalogami danych: ścieżka względem katalogu sessions/, w którym
    leży (dirs — sessions_dirs()); plik spoza nich — pełna ścieżka."""
    for folder in dirs:
        if path.startswith(folder.rstrip(os.sep) + os.sep):
            return os.path.relpath(path, folder)
    return path


def unique_files(items, dirs, path=lambda i: i[0], stamp=lambda i: i[1]) -> list:
    """Pliki sesji bez kopii z drugiego katalogu danych (ten sam file_key) — zostaje nowszy (stamp, np. mtime)."""
    return newest(items, key=lambda i: file_key(path(i), dirs), stamp=stamp)


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
