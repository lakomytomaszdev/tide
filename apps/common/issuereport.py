"""Kontrolowany renderer raportu Qt: tylko obrazy z odczytanego manifestu, żadnego I/O."""
import re

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtQuick import QQuickImageProvider

LINK = re.compile(r"https?://[^\s<>\x00-\x1f]+", re.IGNORECASE)
ATTACHMENT = re.compile(r"A-[0-9a-f]{32}")
IMAGE_TYPES = {"image/png": b"png", "image/jpeg": b"jpeg", "image/webp": b"webp"}
MARKDOWN = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML


class InertDocument(QTextDocument):
    def loadResource(self, kind, url):
        # Parser nie może odczytać nawet lokalnego file:// podczas budowania dokumentu.
        return QByteArray()


def render(markdown, images, link):
    """images mapuje dokładne ścieżki manifestu na nieprzewidywalne adresy dostawcy QML."""
    doc = InertDocument()
    doc.setMarkdown(markdown or "", MARKDOWN)
    edits = []
    block = doc.begin()
    while block.isValid():
        fmt = block.blockFormat()
        if fmt.headingLevel():
            fmt.setTopMargin(0 if block.blockNumber() == 0 else 16)
            fmt.setBottomMargin(6)
            QTextCursor(block).setBlockFormat(fmt)
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            char = fragment.charFormat()
            if char.isImageFormat() or char.isAnchor():
                edits.append((fragment.position(), fragment.length(), char))
            it += 1
        block = block.next()
    for position, length, char in reversed(edits):
        cursor = QTextCursor(doc)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        if char.isImageFormat():
            image = char.toImageFormat()
            target = images.get(image.name())
            if target:
                image.setName(target[0])
                image.setWidth(target[1].width())
                image.setHeight(target[1].height())
                # Obraz nie dziedziczy odnośnika otaczającego go w Markdown.
                image.setAnchor(False)
                image.setAnchorHref("")
                cursor.setCharFormat(image)
            else:
                plain = QTextCharFormat()
                plain.setFontItalic(True)
                alt = str(char.property(QTextFormat.Property.ImageAltText) or "bez opisu")
                cursor.insertText("(obraz niedostępny: " + alt + ")", plain)
        else:
            if LINK.fullmatch(char.anchorHref() or ""):
                char.setForeground(link)
            else:
                char.setAnchor(False)
                char.setAnchorHref("")
                char.clearForeground()
                char.setFontUnderline(False)
            cursor.setCharFormat(char)
    return re.sub(r'<body style="[^"]*">', '<body>', doc.toHtml(), count=1)


def decode_image(raw, mime):
    """Zamknięta lista dekoderów i limit przed dekompresją; SVG nigdy nie trafia do Qt."""
    if mime not in IMAGE_TYPES:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, IMAGE_TYPES[mime])
    reader.setAutoDetectImageFormat(False)
    reader.setDecideFormatFromContent(False)
    size = reader.size()
    if (not size.isValid() or size.width() * size.height() > 40_000_000 or
            (reader.supportsAnimation() and reader.imageCount() != 1)):
        return QImage()
    if size.width() > 720 or size.height() > 600:
        reader.setScaledSize(size.scaled(720, 600, Qt.AspectRatioMode.KeepAspectRatio))
    previous_limit = QImageReader.allocationLimit()
    try:
        QImageReader.setAllocationLimit(320)
        image = reader.read()
    finally:
        QImageReader.setAllocationLimit(previous_limit)
    return image if reader.error() == QImageReader.ImageReaderError.UnknownError else QImage()


class ImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.images = {}

    def requestImage(self, ident, requestedSize):
        # Nie ma ścieżki zapasowej do dysku ani interpretacji ident jako URL.
        img = self.images.get(ident, QImage())
        return img, img.size() if not img.isNull() else QSize()
