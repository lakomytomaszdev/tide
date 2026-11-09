"""Zatwierdzenie kontraktu testów projektu (K-69, PT-285): magazyn odcisków w stanie usługi.

Kontrakt testów (`tests` w `projects/<slug>/box.yaml`, project_tests.py) leży w repozytorium tablicy, więc może
go zmienić każdy, kto pisze w plikach: krok agenta, ręczna edycja, cofnięcie commita. Zatwierdzenie to odcisk
kontraktu zapisany poza tablicą, w `$XDG_STATE_HOME/tide/test-contracts.json`:

    {"version": 1, "boards": {"<realpath tablicy>": {"<slug>": "<sha256 hex>"}}}

- Odcisk = SHA-256 reprezentacji kanonicznej `{"changed": <napis>, "full": <napis>}` (stała kolejność pól,
  JSON bez odstępów, UTF-8). Brak pola ≡ pusty napis; usuwane są wyłącznie białe znaki na brzegach każdego
  polecenia — odstępy wewnątrz polecenia (także w cytowanych argumentach) są znaczące.
- Jedno wejście odczytu: `approved(board, slug, contract)`. Każda niepewność daje False, nigdy True i nigdy
  wyjątek: brak pliku lub katalogu, dowiązanie (pliku albo katalogu `tide`; `O_NOFOLLOW`), właściciel inny niż
  użytkownik usługi, prawa inne niż 0700 (katalog) i 0600 (plik), nie zwykły plik, za duży plik, zły UTF-8
  albo JSON, nieznana wersja, zły kształt. Pusty kontrakt nie jest zatwierdzony.
- Jedno wejście zapisu: `record(board, slug, contract)` — zapisuje odcisk albo, dla pustego kontraktu, usuwa
  wpis. Zapis atomowy: plik tymczasowy w tym samym katalogu (`O_EXCL | O_NOFOLLOW`, 0600), `fsync`, `rename`
  względem uchwytu katalogu i `fsync` katalogu; przerwanie w połowie zostawia stary plik i sprząta plik
  tymczasowy. Kolejne zapisy szereguje `flock` na uchwycie katalogu. Nieczytelny plik zapis zastępuje nowym
  (ponowne zatwierdzenie naprawia utracony albo uszkodzony magazyn). Błąd — ApprovalError.

Moduł nie wie, kto woła zapis. Odcisk tworzy wyłącznie `BoardStore.approve_project_tests`, wywoływane przez
D-Bus `UpsertProject` z polem `tests` (okno projektu) po sprawdzeniu nadawcy (`call_origin`, PT-286).

Wejście dla kroku (PT-286): `approval_for_step(project, step, board)` → `(approved, reason)`. Zanim dopuści
kontrakt dla kroku, porównuje rzeczywiste ścieżki magazynu (plik i każdy katalog nadrzędny, przed i po
`realpath`) z obszarami zapisu kroku (`agentcmd.step_write_areas` — ta sama funkcja, z której powstają argumenty
piaskownicy) i z repozytorium Tablicy. Kolizja albo niepewność → `(False, powód)`; bez kolizji wynik daje `approved`.
"""
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading

FILE_NAME = "test-contracts.json"
VERSION = 1
FIELDS = ("changed", "full")
DIR_MODE = 0o700
FILE_MODE = 0o600
MAX_BYTES = 1 << 20
_HEX = re.compile(r"[0-9a-f]{64}")
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_write_lock = threading.Lock()


class ApprovalError(Exception):
    """Zapis odcisku się nie udał — komunikat po polsku."""


def state_dir(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tide")


def state_path(env=None) -> str:
    return os.path.join(state_dir(env), FILE_NAME)


def board_key(board: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(board)))


def canonical(contract: dict) -> str:
    """Reprezentacja kanoniczna kontraktu; ValueError dla wartości, które nie są napisami."""
    if not isinstance(contract, dict):
        raise ValueError("kontrakt musi być obiektem")
    out = {}
    for name in FIELDS:
        value = contract.get(name, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"tests.{name} musi być napisem")
        out[name] = value.strip()
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def fingerprint(contract: dict) -> str:
    return hashlib.sha256(canonical(contract).encode("utf-8")).hexdigest()


def is_empty(contract: dict) -> bool:
    return not any(str((contract or {}).get(name) or "").strip() for name in FIELDS)


def approved(board: str, slug: str, contract: dict, path: str | None = None) -> bool:
    """Czy kontrakt projektu `slug` tablicy `board` ma zapisany równy odcisk. Każda niepewność → False."""
    try:
        if is_empty(contract):
            return False
        wanted = fingerprint(contract)
        data = _load(path or state_path())
        if data is None:
            return False
        stored = data["boards"].get(board_key(board), {}).get(str(slug))
        return isinstance(stored, str) and hmac.compare_digest(stored, wanted)
    except Exception:  # odczyt zatwierdzenia nie może dać True z błędu ani zepsuć listy projektów
        return False


STORE_IN_WRITE_AREA = "magazyn zatwierdzeń leży w obszarze zapisu kroku"


def approval_for_step(project, step, board: str, path: str | None = None) -> tuple[bool, str]:
    """Czy kontrakt testów projektu (`board.model.Project`) jest zatwierdzony dla kroku `step`
    (`tasks.agentcmd.Step`, ten sam, z którego powstają argumenty kroku). Zwraca `(approved, reason)`;
    `reason` pusty tylko przy `True`. Każda niepewność → False."""
    try:
        from ..tasks import agentcmd
        path = path or state_path()
        areas = [*agentcmd.step_write_areas(step).writable(), board]
        hit = store_collision(path, areas)
        if hit:
            return False, f"{STORE_IN_WRITE_AREA}: {hit}"
        if getattr(project, "tests_error", ""):
            return False, f"kontrakt testów projektu jest uszkodzony: {project.tests_error}"
        if is_empty(project.tests):
            return False, "projekt nie ma kontraktu testów"
        if not approved(board, project.slug, project.tests, path):
            return False, "kontrakt testów nie jest zatwierdzony (odcisk różny albo brak odcisku)"
        return True, ""
    except Exception as exc:  # niepewność nigdy nie daje zatwierdzenia
        return False, f"nie da się sprawdzić zatwierdzenia dla kroku: {type(exc).__name__}: {exc}"


def store_collision(path: str, areas) -> str:
    """Opis pierwszej kolizji magazynu odcisków z obszarami zapisu albo pusty napis.

    Kolizja: plik odcisków albo którykolwiek jego katalog nadrzędny (ścieżka podana i po `realpath`) leży
    w obszarze zapisu (podanym i po `realpath`) — krok mógłby zapisać, podmienić albo przenieść plik lub katalog —
    albo obszar zapisu leży w katalogu odcisków. Pusta lub względna ścieżka obszaru to niepewność → kolizja.

    Ścieżki magazynu nie normalizuje się leksykalnie przed rozwinięciem dowiązań: `link/..` w systemie plików to
    katalog nadrzędny celu dowiązania, a `abspath` usunąłby `link/..` i badał inne miejsce niż późniejszy odczyt.
    Dlatego ścieżka jest przechodzona składnik po składniku tak, jak robi to jądro (`_resolution_entries`):
    sprawdza się każdy wpis katalogu na drodze rozwinięcia — także wpisy z treści dowiązań, czyli dowiązania
    pośrednie (rzeczywisty katalog + nazwa; krok mógłby je podmienić albo przenieść) — oraz cele przedrostków
    (`realpath`). Nieudane rozwinięcie (pętla, brak dostępu) to niepewność → kolizja. Formy leksykalne zostają
    jako dodatkowe, zachowawcze kandydatki."""
    if not path:
        return "nieznana ścieżka magazynu odcisków"
    raw = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    try:
        chain, walked = _resolution_entries(raw)
    except OSError as exc:
        return f"nie da się rozwinąć ścieżki magazynu odcisków {raw}: {exc.strerror or exc}"
    parts = raw.split(os.sep)
    for n in range(2, len(parts) + 1):
        chain.append(os.path.realpath(os.sep.join(parts[:n])))            # cele przedrostków — zachowawczo
    real = os.path.realpath(raw)
    for current in dict.fromkeys((walked, real)):                         # katalogi nadrzędne pliku rzeczywistego
        while True:
            chain.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    lexical = os.path.abspath(raw)
    current = lexical
    while True:                                                           # formy leksykalne — tylko zachowawczo
        chain += [current, os.path.realpath(current)]
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    chain = list(dict.fromkeys(chain))
    folders = {os.path.realpath(os.path.dirname(raw)), os.path.dirname(real), os.path.dirname(walked),
               os.path.dirname(lexical), os.path.realpath(os.path.dirname(lexical))}
    for area in areas:
        if not area or not os.path.isabs(area):
            return f"nieznany obszar zapisu {area!r}"
        for form in dict.fromkeys((os.path.normpath(area), os.path.realpath(area))):
            if form == os.sep:
                return f"obszar zapisu {area} obejmuje cały system plików"
            for item in chain:
                if _inside(item, form):
                    return f"{item} leży w obszarze zapisu {area}"
            for item in folders:
                if _inside(form, item):
                    return f"obszar zapisu {area} leży w katalogu odcisków {item}"
    return ""


MAX_SYMLINKS = 40   # jak MAXSYMLINKS jądra Linux


def _resolution_entries(raw: str) -> tuple[list, str]:
    """Rozwija bezwzględną ścieżkę tak jak jądro i zwraca `(wpisy, ścieżka rzeczywista)`.

    `wpisy` to każdy wpis katalogu, przez który przechodzi rozwinięcie: składniki podanej ścieżki **oraz
    składniki treści każdego napotkanego dowiązania** (także dowiązań pośrednich, które pojawiają się dopiero
    w celu poprzedniego — review PT-286 r2, BLOCKER 1), każdy jako rzeczywisty katalog + nazwa, oraz katalogi
    osiągnięte przez `..`. Kto może zapisać którykolwiek z tych wpisów (albo jego katalog), ten może przestawić
    miejsce, z którego czyta się magazyn. `..` działa na katalogu już rozwiniętym (semantyka `link/..`).
    Brak składnika (ENOENT/ENOTDIR) kończy rozwijanie dowiązań — reszta ścieżki idzie leksykalnie, jak dla
    nieistniejącego pliku. Inny błąd odczytu dowiązania albo pętla (ELOOP) — OSError (niepewność)."""
    entries = []
    pending = list(reversed(raw.split(os.sep)))
    current = os.sep
    links = 0
    missing = False
    while pending:
        part = pending.pop()
        if not part or part == os.curdir:
            continue
        if part == os.pardir:
            current = os.path.dirname(current)
            entries.append(current)
            continue
        entry = os.path.join(current, part)
        entries.append(entry)
        current = entry
        if missing:
            continue
        try:
            target = os.readlink(entry)
        except OSError as exc:
            if exc.errno == errno.EINVAL:          # nie dowiązanie
                continue
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                missing = True
                continue
            raise
        links += 1
        if links > MAX_SYMLINKS:
            raise OSError(errno.ELOOP, "za dużo poziomów dowiązań")
        current = os.sep if os.path.isabs(target) else os.path.dirname(entry)
        pending.extend(reversed(target.split(os.sep)))
    return list(dict.fromkeys(entries)), current


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normpath(path), os.path.normpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def record(board: str, slug: str, contract: dict, path: str | None = None) -> None:
    """Zapisuje odcisk kontraktu (pusty kontrakt — usuwa wpis). ApprovalError, gdy się nie da."""
    try:
        wanted = "" if is_empty(contract) else fingerprint(contract)
    except ValueError as exc:
        raise ApprovalError(str(exc)) from None
    if not isinstance(slug, str) or not slug:
        raise ApprovalError("brak identyfikatora projektu")
    path = path or state_path()
    folder, name = os.path.split(path)
    key = board_key(board)
    with _write_lock:
        try:
            dfd = _open_dir(folder, create=True)
        except OSError as exc:
            raise ApprovalError(f"katalog stanu {folder}: {exc.strerror or exc}") from None
        try:
            fcntl.flock(dfd, fcntl.LOCK_EX)
            data = _read(dfd, name)
            if data is None and not wanted:
                return  # nic do usunięcia — nie zakładamy magazynu (nieczytelny i tak daje False)
            data = data or {"version": VERSION, "boards": {}}
            entries = dict(data["boards"].get(key, {}))
            if wanted:
                entries[slug] = wanted
            else:
                entries.pop(slug, None)
            boards = {k: v for k, v in data["boards"].items() if k != key}
            if entries:
                boards[key] = entries
            _write_atomic(dfd, name, {"version": VERSION, "boards": boards})
        except OSError as exc:
            raise ApprovalError(f"zapis {path}: {exc.strerror or exc}") from None
        finally:
            os.close(dfd)  # zwalnia też flock


# --- wnętrze -------------------------------------------------------------------------------------


def _open_dir(folder: str, create: bool) -> int:
    """Uchwyt katalogu stanu bez podążania za dowiązaniem; właściciel = użytkownik usługi, prawa 0700.
    Przy zapisie zakłada katalog i poprawia jego prawa (katalog `tide` dzielą inne pliki stanu)."""
    if create:
        os.makedirs(os.path.dirname(folder), mode=DIR_MODE, exist_ok=True)
        try:
            os.mkdir(folder, DIR_MODE)
        except FileExistsError:
            pass
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError(errno.EPERM, "katalog stanu ma innego właściciela")
        if stat.S_IMODE(info.st_mode) != DIR_MODE:
            if not create:
                raise OSError(errno.EPERM, "katalog stanu ma prawa inne niż 0700")
            os.fchmod(fd, DIR_MODE)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _load(path: str) -> dict | None:
    folder, name = os.path.split(path)
    try:
        dfd = _open_dir(folder, create=False)
    except OSError:
        return None
    try:
        return _read(dfd, name)
    finally:
        os.close(dfd)


def _read(dfd: int, name: str) -> dict | None:
    """Zawartość magazynu albo None przy każdej niepewności (brak, dowiązanie, właściciel, prawa, treść)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC, dir_fd=dfd)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != FILE_MODE or info.st_size > MAX_BYTES):
            return None
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                return None
            chunks.append(chunk)
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        os.close(fd)
    return data if _valid(data) else None


def _valid(data) -> bool:
    if not isinstance(data, dict) or set(data) != {"version", "boards"}:
        return False
    if type(data["version"]) is not int or data["version"] != VERSION or not isinstance(data["boards"], dict):
        return False
    for key, entries in data["boards"].items():
        if not isinstance(key, str) or not key or not isinstance(entries, dict):
            return False
        for slug, value in entries.items():
            if not isinstance(slug, str) or not slug or not isinstance(value, str) or not _HEX.fullmatch(value):
                return False
    return True


def _write_atomic(dfd: int, name: str, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC, FILE_MODE, dir_fd=dfd)
    try:
        try:
            os.fchmod(fd, FILE_MODE)   # niezależnie od umask
            view = memoryview(text.encode("utf-8"))
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temporary, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=dfd)
        except OSError:
            pass
        raise
    os.fsync(dfd)
