"""Domain exceptions for FinanceBot."""


class GoalError(ValueError):
    """Base class for goal operation errors."""


class GoalNotFoundOrCompleted(GoalError):
    """Raised by deposit_goal when goal is missing or already completed."""


class GoalNotFound(GoalError):
    """Raised by withdraw_goal when goal does not exist."""


class GoalCompleted(GoalError):
    """Raised when an operation is attempted on a completed goal."""


class InsufficientFundsInGoal(GoalError):
    """Raised when withdrawal amount exceeds goal's current balance."""
