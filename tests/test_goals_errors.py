"""Tests that goal deposit/withdraw ValueError strings are mapped to user messages."""
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.handlers.goals import _GOAL_ERROR_MESSAGES


@pytest.mark.parametrize(
    "raised_text",
    [
        "Goal not found or completed",
        "Goal not found",
        "Goal is completed",
        "Insufficient funds in goal",
    ],
)
def test_goal_error_messages_cover_all_raises(raised_text):
    """Every ValueError raised by deposit_goal/withdraw_goal must have a Russian translation."""
    msg = _GOAL_ERROR_MESSAGES.get(raised_text)
    assert msg is not None, f"No user-facing message for: {raised_text}"
    assert msg.strip()
    assert any("А" <= ch <= "я" for ch in msg), "Message must be Russian"


def test_goal_error_fallback_for_unknown_error_is_handled():
    """Unknown ValueError text — helper falls back to default in source; mapping returns None."""
    assert _GOAL_ERROR_MESSAGES.get("unrelated string") is None
