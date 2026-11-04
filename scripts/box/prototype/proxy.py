#!/usr/bin/python3
"""Chwilowe proxy prób pudełka (PT-164) — zastępca proxy Tide (#5/PT-168) na czas prób P-1 i P-4.

Użycie: python3 scripts/box/prototype/proxy.py <adres> <port> <dziennik>

Słucha na adresie mostka i porcie proxy Tide (jedyny port, który tabela inet tide-box przepuszcza z incusbr0),
więc kontener dostaje sieć bez żadnej zmiany w nftables. Obsługuje CONNECT (HTTPS, tunel bez MITM) i zwykłe
żądania HTTP z pełnym adresem (apt). Każde połączenie dopisuje do dziennika wiersz „METODA host:port” — z tego
powstaje lista domen dla notki (materiał dla P-6). Bez listy dozwolonych domen: to tylko narzędzie prób,
działa wyłącznie, gdy skrypt próby trzyma je przy życiu. Po starcie dopisuje do dziennika wiersz „ready”.
"""
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

LOCK = threading.Lock()


def relay(a, b):
    """Przepisuje bajty w obie strony, aż któraś strona zamknie połączenie."""
    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (dst, src):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t = threading.Thread(target=pump, args=(b, a), daemon=True)
    t.start()
    pump(a, b)
    t.join()


class Handler(socketserver.BaseRequestHandler):
    def note(self, line):
        with LOCK, open(self.server.journal, "a") as fh:
            fh.write(line + "\n")

    def handle(self):
        conn = self.request
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(65536)
            if not chunk:
                return
            head += chunk
            if len(head) > 65536:
                return
        header, _, rest = head.partition(b"\r\n\r\n")
        lines = header.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            port = int(port or 443)
            self.note(f"CONNECT {host}:{port}")
            try:
                upstream = socket.create_connection((host, port), timeout=30)
            except OSError as exc:
                self.note(f"BŁĄD {host}:{port} {exc}")
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            upstream.settimeout(None)
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            relay(conn, upstream)
            return
        url = urlsplit(target)
        if url.scheme != "http" or not url.hostname:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        port = url.port or 80
        self.note(f"{method} {url.hostname}:{port}")
        path = url.path or "/"
        if url.query:
            path += "?" + url.query
        kept = [h for h in lines[1:] if not h.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
        request = "\r\n".join([f"{method} {path} {version}", *kept, "Connection: close", "", ""])
        try:
            upstream = socket.create_connection((url.hostname, port), timeout=30)
        except OSError as exc:
            self.note(f"BŁĄD {url.hostname}:{port} {exc}")
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        upstream.settimeout(None)
        upstream.sendall(request.encode("latin-1") + rest)
        relay(conn, upstream)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    address, port, journal = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    server = Server((address, port), Handler)
    server.journal = journal
    with open(journal, "a") as fh:
        fh.write("ready\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
