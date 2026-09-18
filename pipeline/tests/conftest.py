"""Every pipeline test runs with the judgment calls off unless it turns them
on itself: classify.py would otherwise spend real money on a real model."""
import os

import pytest


@pytest.fixture(autouse=True)
def _classify_off(monkeypatch):
    if "CLASSIFY_TEST_ON" not in os.environ:
        monkeypatch.setenv("CLASSIFY_OFF", "1")
