"""Every pipeline test runs with the judgment calls off unless it turns them
on itself: classify.py would otherwise spend real money on a real model.

`playwright_ok()` is the one guard for live browser tests: they skip when
the kit's own loader (js/pw.cjs) cannot load Playwright on this machine,
the same check `install.sh --check` makes. Use it as
    from conftest import playwright_ok
    pytestmark = pytest.mark.skipif(not playwright_ok(), reason=PLAYWRIGHT_MISSING)
"""
import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PW_CJS = Path(__file__).resolve().parent.parent / "js" / "pw.cjs"
PLAYWRIGHT_MISSING = "Playwright can't be loaded by pipeline/js/pw.cjs (npm install -g playwright && npx playwright install chromium)"


@functools.lru_cache(maxsize=1)
def playwright_ok():
    """True when `node -e "require('<kit>/js/pw.cjs').loadPlaywright()"` exits 0."""
    if shutil.which("node") is None:
        return False
    try:
        proc = subprocess.run(["node", "-e", f"require({json.dumps(str(PW_CJS))}).loadPlaywright()"],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@pytest.fixture(autouse=True)
def _classify_off(monkeypatch):
    if "CLASSIFY_TEST_ON" not in os.environ:
        monkeypatch.setenv("CLASSIFY_OFF", "1")
