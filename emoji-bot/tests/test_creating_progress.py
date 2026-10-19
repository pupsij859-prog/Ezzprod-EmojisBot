"""Поведение настоящего JS-экрана: реальный процент и результат после долгой сборки."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_creating_screen_keeps_polling_and_uses_actual_progress():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the creating screen test")
    result = subprocess.run(
        [node, "--experimental-vm-modules", str(Path(__file__).parent / "frontend" / "creating_progress.mjs")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
