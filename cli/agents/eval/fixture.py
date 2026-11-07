"""Kontrolowany, jawny obraz testowy; brak zależności od fontów i pulpitu."""
import struct
import zlib


def png():
    width, height = 520, 260
    pixels = bytearray([245, 246, 248] * width * height)

    def rectangle(x, y, w, h, color):
        for row in range(y, y + h):
            for col in range(x, x + w):
                offset = (row * width + col) * 3
                pixels[offset:offset + 3] = bytes(color)

    glyphs = {
        'A': ['01110','10001','10001','11111','10001','10001','10001'],
        'E': ['11111','10000','10000','11110','10000','10000','11111'],
        'F': ['11111','10000','10000','11110','10000','10000','10000'],
        'I': ['11111','00100','00100','00100','00100','00100','11111'],
        'J': ['00111','00010','00010','00010','10010','10010','01100'],
        'K': ['10001','10010','10100','11000','10100','10010','10001'],
        'L': ['10000','10000','10000','10000','10000','10000','11111'],
        'O': ['01110','10001','10001','10001','10001','10001','01110'],
        'P': ['11110','10001','10001','11110','10000','10000','10000'],
        'R': ['11110','10001','10001','11110','10100','10010','10001'],
        'S': ['01111','10000','10000','01110','00001','00001','11110'],
        'T': ['11111','00100','00100','00100','00100','00100','00100'],
        'U': ['10001','10001','10001','10001','10001','10001','01110'],
        ' ': ['00000'] * 7,
    }

    def text(label, x, y, scale=3):
        for letter in label:
            for row, pattern in enumerate(glyphs[letter]):
                for col, bit in enumerate(pattern):
                    if bit == '1':
                        rectangle(x + col * scale, y + row * scale, scale, scale, (30, 40, 50))
            x += 6 * scale

    rectangle(12, 12, 496, 50, (212, 225, 239))
    text('PROJEKT ALFA', 28, 26)
    rectangle(12, 76, 496, 172, (255, 255, 255))
    text('LISTA PUSTA', 145, 150)

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    raw = b''.join(b'\x00' + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
