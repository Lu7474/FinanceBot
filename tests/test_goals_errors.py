"""Tests for typed goal exceptions and their handler mapping."""

import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.exceptions import (
    GoalCompleted,
    GoalError,
    GoalNotFound,
    GoalNotFoundOrCompleted,
    InsufficientFundsInGoal,
)
from core.handlers.goals import _GOAL_ERROR_MESSAGES

# --- Exception hierarchy ---


def test_all_goal_exceptions_are_value_errors():
    """GoalError subclasses must be catchable as ValueError for handler compatibility."""
    for exc_type in (
        GoalNotFoundOrCompleted,
        GoalCompleted,
        GoalNotFound,
        InsufficientFundsInGoal,
    ):
        assert issubclass(exc_type, ValueError), f"{exc_type} must inherit ValueError"


# --- Mapping coverage ---


@pytest.mark.parametrize(
    "exc_type",
    [GoalNotFoundOrCompleted, GoalCompleted, GoalNotFound, InsufficientFundsInGoal],
)
def test_every_goal_exception_has_russian_translation(exc_type):
    """Every typed exception must be matched by _GOAL_ERROR_MESSAGES."""
    instance = exc_type()
    matched = next(
        (msg for t, msg in _GOAL_ERROR_MESSAGES if isinstance(instance, t)),
        None,
    )
    assert matched is not None, f"No translation for {exc_type.__name__}"
    assert matched.strip()
    assert any("А" <= ch <= "я" for ch in matched), (
        f"Message for {exc_type.__name__} must be Russian"
    )


def test_unknown_goal_error_returns_none_from_mapping():
    """An unknown GoalError subclass must not accidentally match a translation."""

    class UnknownGoalError(GoalError):
        pass

    instance = UnknownGoalError()
    matched = next(
        (msg for t, msg in _GOAL_ERROR_MESSAGES if isinstance(instance, t)),
        None,
    )
    assert matched is None


# --- isinstance ordering: more specific before base ---


def test_goal_not_found_or_completed_does_not_match_goal_not_found():
    """GoalNotFoundOrCompleted must NOT be matched as GoalNotFound."""
    instance = GoalNotFoundOrCompleted()
    assert not isinstance(instance, GoalNotFound)


def test_goal_completed_does_not_match_goal_not_found():
    """GoalCompleted must NOT be matched as GoalNotFound."""
    instance = GoalCompleted()
    assert not isinstance(instance, GoalNotFound)


# --- Source-level: every raise in requests/goals.py must have a translation ---


def test_all_raised_goal_exceptions_covered_in_handler():
    """Every GoalError subclass raised in requests/goals.py must have an entry in _GOAL_ERROR_MESSAGES.

    Catches the drift where a new exception is added to the data layer
    but the handler mapping is not updated.
    """
    import core.database.requests.goals as goals_module

    source = inspect.getsource(goals_module)
    raised_names = set(re.findall(r"\braise\s+(\w+)\s*\(", source))
    module_ns = vars(goals_module)

    uncovered = []
    for name in sorted(raised_names):
        cls = module_ns.get(name)
        if cls is None or not (isinstance(cls, type) and issubclass(cls, GoalError)):
            continue
        instance = cls()
        matched = next(
            (
                msg
                for exc_type, msg in _GOAL_ERROR_MESSAGES
                if isinstance(instance, exc_type)
            ),
            None,
        )
        if matched is None:
            uncovered.append(name)

    assert not uncovered, (
        f"These exceptions are raised in requests/goals.py but missing from "
        f"_GOAL_ERROR_MESSAGES: {uncovered}"
    )
