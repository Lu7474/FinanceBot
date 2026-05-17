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


def test_goal_repository_raises_match_handler_translation():
    """Critical #3: every raise ValueError("...") in requests/goals.py must be in _GOAL_ERROR_MESSAGES.

    Любое переименование строки в репозитории тихо сломает русский перевод
    и пользователь увидит generic «Не удалось выполнить операцию». Тест ловит этот рассинхрон.
    """
    import inspect
    import re

    from core.database.requests import goals as goals_repo

    src = inspect.getsource(goals_repo)
    raised = set(re.findall(r'raise\s+ValueError\(\s*"([^"]+)"\s*\)', src))
    assert raised, (
        'Не нашли ни одного raise ValueError("...") в requests/goals.py — обнови regex'
    )

    missing = raised - set(_GOAL_ERROR_MESSAGES.keys())
    assert not missing, (
        f"Строки ValueError, не имеющие перевода в _GOAL_ERROR_MESSAGES: {missing}. "
        "Либо добавь ключ в словарь, либо введи типизированные исключения."
    )
