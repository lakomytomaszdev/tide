"""Granice niezaufanych dowodów; moduł używany wyłącznie przez usługę."""
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .board.model import BoardError

FILE_MAX = 20 * 1024 * 1024
TOTAL_MAX = 100 * 1024 * 1024
COUNT_MAX = 20
PIXELS_MAX = 40_000_000
ATTACHMENT_RE = re.compile(r"A-[0-9a-f]{32}\Z")
MANIFEST_RE = re.compile(r"manifests/([0-9a-f]{64})\.json\Z")
FIELDS = {"id", "name", "path", "type", "size", "sha256"}


def reject(message, code="bad-request"):
    raise BoardError(message, code)


def name_valid(name):
    return (isinstance(name, str) and 0 < len(name) <= 255 and name not in (".", "..") and
            not any(c in name for c in "/\\:") and not any(ord(c) < 32 for c in name) and
            not any(0xD800 <= ord(c) <= 0xDFFF for c in name))


def request(data, field):
    if (not isinstance(data, dict) or data.keys() != {"expectedVersion", field} or
            type(data.get("expectedVersion")) is not int or data["expectedVersion"] < 1):
        reject(f"Podaj wyłącznie {field} i dodatnie całkowite expectedVersion.")
    if field == "name":
        if not name_valid(data[field]):
            reject("Niepoprawna nazwa załącznika.")
    elif not isinstance(data[field], str) or not ATTACHMENT_RE.fullmatch(data[field]):
        reject("Niepoprawny identyfikator załącznika.")
    return data


def manifest_valid(manifest):
    if (not isinstance(manifest, dict) or manifest.keys() != {"version", "attachments"} or
            type(manifest["version"]) is not int or manifest["version"] != 1 or
            not isinstance(manifest["attachments"], list) or len(manifest["attachments"]) > COUNT_MAX):
        reject("Niepoprawny manifest załączników.", "broken-file")
    seen, total = set(), 0
    for entry in manifest["attachments"]:
        if (not isinstance(entry, dict) or entry.keys() != FIELDS or
                not isinstance(entry["id"], str) or not ATTACHMENT_RE.fullmatch(entry["id"]) or
                entry["id"] in seen or not name_valid(entry["name"]) or
                entry["path"] != "attachments/" + entry["id"] or
                type(entry["size"]) is not int or not 0 <= entry["size"] <= FILE_MAX or
                not isinstance(entry["type"], str) or
                not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", entry["type"]) or
                not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            reject("Niepoprawna pozycja manifestu.", "broken-file")
        seen.add(entry["id"])
        total += entry["size"]
    if total > TOTAL_MAX:
        reject("Manifest przekracza 100 MiB.", "broken-file")
    return manifest["attachments"]


def read_file(root, relative, limit):
    """Otwiera każdy komponent przez dirfd: podmiana katalogu na symlink nie zmienia celu."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ("..", ".") for p in parts):
        reject("Niedozwolona ścieżka kopii.", "broken-file")
    # Kontrola realpath jest dodatkowa; właściwą granicą są uchwyty i O_NOFOLLOW.
    root = Path(root).absolute()
    try:
        contained = (root / relative).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        reject("Nie można ustalić bezpiecznej ścieżki kopii.", "broken-file")
    if not contained:
        reject("Kopia wychodzi poza sprawę.", "broken-file")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], *parts[:-1]):
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                reject("Kopia nie jest zwykłym plikiem w limicie.", "broken-file")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                reject("Kopia zmieniła się podczas odczytu.", "broken-file")
            return raw
    finally:
        os.close(fd)


def checked_copy(root, entry):
    try:
        data = read_file(root, entry["path"], FILE_MAX)
    except (OSError, ValueError) as exc:
        reject(f"Nie można odczytać kopii załącznika: {exc}.", "broken-file")
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        reject("Rozmiar lub SHA-256 kopii jest niezgodny z manifestem.", "broken-file")
    return data


def media_type(name, data):
    """Tylko PNG/JPEG/WebP przechodzą przez dekoder; SVG/HTML pozostają zwykłymi dowodami."""
    suffix = Path(name).suffix.lower()
    expected = {".png": b"png", ".jpg": b"jpeg", ".jpeg": b"jpeg", ".webp": b"webp"}.get(suffix)
    detected = (b"png" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                b"jpeg" if data.startswith(b"\xff\xd8") else
                b"webp" if data.startswith(b"RIFF") and data[8:12] == b"WEBP" else None)
    if expected or detected:
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
        from PyQt6.QtGui import QImageReader
        if detected is None or (expected is not None and expected != detected):
            reject("Nazwa obrazu nie odpowiada jego zawartości.")
        if ((detected == b"png" and not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")) or
                (detected == b"jpeg" and not data.endswith(b"\xff\xd9")) or
                (detected == b"webp" and int.from_bytes(data[4:8], "little") + 8 != len(data))):
            reject("Obraz jest niekompletny.")
        if detected == b"jpeg":
            # Qt potrafi zwrócić obraz mimo ostrzeżenia libjpeg o niepełnym skanie.
            # Osobny proces izoluje globalny handler Qt i reguły logowania.
            env = {**os.environ, "QT_LOGGING_RULES": "*.warning=true"}
            try:
                decoded = subprocess.run(
                    [sys.executable, "-s", "-m", "tide_daemon.issue_jpeg"], input=data,
                    cwd=Path(__file__).resolve().parent.parent, env=env,
                    capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                reject("Nie udało się sprawdzić obrazu JPEG.")
            if decoded.returncode != 0:
                reject("Dekoder odmówił odczytu obrazu JPEG.")
            return "image/jpeg"
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, QByteArray(detected))
        size = reader.size()
        if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > PIXELS_MAX:
            reject("Obraz jest uszkodzony albo przekracza 40 megapikseli.")
        if reader.supportsAnimation() and reader.imageCount() != 1:
            reject("Animowane obrazy nie są obsługiwane.")
        # Domyślne 128 MiB Qt odrzuciłoby poprawne 40 MP RGB(A). Ograniczamy
        # pojedynczą alokację do 8 bajtów/piksel także dla obrazów 16-bitowych.
        previous_limit = QImageReader.allocationLimit()
        try:
            QImageReader.setAllocationLimit(320)
            image = reader.read()
        finally:
            QImageReader.setAllocationLimit(previous_limit)
        if image.isNull() or image.size() != size or reader.error() != QImageReader.ImageReaderError.UnknownError:
            reject("Dekoder odmówił odczytu obrazu.")
        return "image/" + detected.decode("ascii")
    guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # Nie pozwalamy rozszerzeniu np. .svgz zadeklarować typu obrazu obsługiwanego jako podgląd.
    return guessed if guessed not in {"image/png", "image/jpeg", "image/webp"} else "application/octet-stream"
