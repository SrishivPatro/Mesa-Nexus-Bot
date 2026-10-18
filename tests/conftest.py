import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mesabot.config import Config  # noqa: E402
from mesabot.services import Services  # noqa: E402
from mesabot.timeutil import parse_iso  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def make_svc(tmp_path):
    def _make(now: str = "2026-10-19T08:00:00+05:30", **overrides) -> Services:
        cfg = dataclasses.replace(Config(), data_dir=str(tmp_path), dry_run=True, **overrides)
        svc = Services.from_config(cfg, fixtures=FIXTURES, now=parse_iso(now))
        svc.sender.printer = lambda _text: None
        return svc
    return _make
