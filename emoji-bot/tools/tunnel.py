"""Временный публичный адрес для теста Mini App через localhost.run (нужен только ssh, есть в Windows).

    python tools/tunnel.py <файл_для_адреса> [порт]

Держит туннель: если соединение рвётся, переподключается и записывает новый адрес в файл.
Бот (PUBLIC_URL_FILE) подхватывает адрес на лету. Только для тестов: для прода нужен свой домен.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import sys
import time
from pathlib import Path

URL = re.compile(r"https://[a-z0-9-]+\.lhr\.life")


def proxy_args() -> list[str]:
    """TUNNEL_SOCKS=<порт> — ssh идёт через локальный SOCKS5 (VPN-клиент), если напрямую localhost.run недоступен.
    Помощник копируем во временную папку: ssh для Windows плохо понимает кириллицу в пути ProxyCommand."""
    if not os.environ.get("TUNNEL_SOCKS"):
        return []
    helper = Path(tempfile.gettempdir()) / "ezz_socks_connect.py"
    shutil.copyfile(Path(__file__).with_name("socks_connect.py"), helper)
    py = Path(sys.executable).as_posix()
    return ["-o", f"ProxyCommand={py} -I {helper.as_posix()} %h %p"]


def run(out: Path, port: int) -> None:
    while True:
        print("подключаюсь к localhost.run…", flush=True)
        proc = subprocess.Popen(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=15",
             "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
             *proxy_args(), "-R", f"80:localhost:{port}", "nokey@localhost.run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            m = URL.search(line)
            if m:
                tmp = out.with_suffix(".tmp")
                tmp.write_text(m.group(0), encoding="utf-8")
                tmp.replace(out)  # атомарно: бот не прочитает половину адреса
                print(f"адрес: {m.group(0)}", flush=True)
        code = proc.wait()
        print(f"туннель закрылся (код {code}), переподключение через 3 с", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 8000)
