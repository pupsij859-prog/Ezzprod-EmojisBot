"""ProxyCommand for SSH tunnels through a local SOCKS5 proxy."""
from __future__ import annotations

import socket
import os
import sys
import threading


def main() -> None:
    host = sys.argv[1].encode("idna")
    port = int(sys.argv[2])
    proxy = socket.create_connection(("127.0.0.1", 10808), timeout=10)
    proxy.sendall(b"\x05\x01\x00")
    if proxy.recv(2) != b"\x05\x00":
        raise RuntimeError("SOCKS5 proxy refused no-auth connection")
    proxy.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port.to_bytes(2, "big"))
    reply = proxy.recv(10)
    if len(reply) < 2 or reply[1] != 0:
        raise RuntimeError("SOCKS5 proxy could not connect")
    proxy.settimeout(None)

    def send_stdin() -> None:
        try:
            while data := os.read(0, 16384):
                proxy.sendall(data)
        finally:
            try:
                proxy.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=send_stdin, daemon=True).start()
    try:
        while data := proxy.recv(16384):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    finally:
        proxy.close()


if __name__ == "__main__":
    main()
