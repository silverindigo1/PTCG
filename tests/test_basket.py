"""Runs the basket optimiser's JavaScript tests under Node, if Node is installed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js ikke installeret")
def test_basket_optimiser():
    result = subprocess.run(
        ["node", str(HERE / "basket_test.js")], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
