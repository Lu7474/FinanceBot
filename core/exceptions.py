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


class DebtError(ValueError):
    """Base class for debt operation errors."""


class DebtNotFound(DebtError):
    """Raised when debt does not exist or does not belong to user."""


class DebtAlreadyClosed(DebtError):
    """Raised when attempting to pay/modify an already-closed debt."""


class PaymentExceedsRemaining(DebtError):
    """Raised when payment amount exceeds debt's remaining balance."""


class PaymentError(ValueError):
    """Base class for payment-reminder operation errors."""


class PaymentNotFound(PaymentError):
    """Raised when a payment does not exist or does not belong to user."""


class PaymentAlreadyPaid(PaymentError):
    """Raised by mark_paid when the expected_due token does not match: the
    payment was already paid (double tap / stale keyboard)."""
