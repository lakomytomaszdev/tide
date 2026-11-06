"""Ścisły odczyt JPEG w procesie izolującym globalną diagnostykę Qt."""
import sys

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QImageReader

from .issue_attachments import FILE_MAX, PIXELS_MAX


def valid_jpeg(data):
    warned = False

    def diagnostic(kind, context, message):
        nonlocal warned
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            warned = True

    previous_handler = qInstallMessageHandler(diagnostic)
    previous_limit = QImageReader.allocationLimit()
    try:
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, QByteArray(b"jpeg"))
        size = reader.size()
        if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > PIXELS_MAX:
            return False
        QImageReader.setAllocationLimit(320)
        image = reader.read()
        return (not warned and not image.isNull() and image.size() == size and
                reader.error() == QImageReader.ImageReaderError.UnknownError)
    finally:
        QImageReader.setAllocationLimit(previous_limit)
        qInstallMessageHandler(previous_handler)


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(FILE_MAX + 1)
    sys.exit(0 if len(raw) <= FILE_MAX and valid_jpeg(raw) else 1)
