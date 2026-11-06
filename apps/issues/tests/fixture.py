import base64
import hashlib

IMAGE_ID = "A-" + "1" * 32
TEXT_ID = "A-" + "2" * 32
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


def entry(ident, raw, name, mime):
    return dict(id=ident, name=name, path="attachments/" + ident, type=mime, size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest())


def issue(ident="S-1", state="draft", version=1):
    return dict(id=ident, title="Raport rozmowy", report="# Raport\n\nBrak zrzutu opisany w raporcie.\n",
                project=None, state=state, version=version, created="2026-11-06", updated="2026-11-06", attachments=[])


def data():
    first = issue()
    first["attachments"] = [entry(IMAGE_ID, PNG, "zrzut.png", "image/png"),
                            entry(TEXT_ID, b"<script>secret()</script>", "dowod.html", "text/html")]
    first["report"] += "\n![Zrzut](attachments/" + IMAGE_ID + ")\n\n[Dokumentacja](https://example.org)\n"
    return dict(issues=[first, issue("S-2", "ready"), issue("S-3", "accepted"), issue("S-4", "rejected")],
                projects=[dict(slug="plasma-tide", name="Tide")],
                attachmentData={IMAGE_ID: base64.b64encode(PNG).decode(),
                                TEXT_ID: base64.b64encode(b"<script>secret()</script>").decode()})
