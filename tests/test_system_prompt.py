"""Tests for src/api/system_prompt.py."""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.system_prompt import build_system_prompt


def test_system_prompt_contains_today():
    today = datetime.date.today().isoformat()
    prompt = build_system_prompt()
    assert today in prompt


def test_system_prompt_contains_write_ops_instruction():
    prompt = build_system_prompt()
    assert "preview_bill_payment" in prompt
    assert "confirmation_token" in prompt
    assert "user_confirmed" in prompt


def test_system_prompt_is_string():
    assert isinstance(build_system_prompt(), str)
