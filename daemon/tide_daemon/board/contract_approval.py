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
D-Bus `UpsertProject` z polem `tests` (okno projektu); sprawdzenie nadawcy i kolizje ścieżek z obszarami zapisu
kroków to PT-286.
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
