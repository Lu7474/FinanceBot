"""Tests for _build_trend_chart_sync."""

import io
import sys
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.charts import _build_balance_line_chart_sync, _build_trend_chart_sync

_PNG_MAGIC = b"\x89PNG"

# ==================== _build_trend_chart_sync ====================


def test_trend_chart_empty_data_returns_none():
    result = _build_trend_chart_sync([], "expense", (2025, 5), (2025, 4))
    assert result is None


def test_trend_chart_returns_png_buffer():
    data = [
        (2025, 1, Decimal("10000")),
        (2025, 2, Decimal("12000")),
        (2025, 3, Decimal("9500")),
    ]
    buf = _build_trend_chart_sync(data, "expense", (2025, 3), (2025, 2))
    assert isinstance(buf, io.BytesIO)
    assert buf.read(4) == _PNG_MAGIC


def test_trend_chart_income_type_renders():
    data = [(2025, 4, Decimal("50000")), (2025, 5, Decimal("60000"))]
    buf = _build_trend_chart_sync(data, "income", (2025, 5), (2025, 4))
    assert buf is not None
    assert buf.read(4) == _PNG_MAGIC


def test_trend_chart_single_point():
    data = [(2025, 5, Decimal("15000"))]
    buf = _build_trend_chart_sync(data, "expense", (2025, 5), (2025, 4))
    assert buf is not None


def test_trend_chart_12_months():
    data = [(2024, m, Decimal(str(m * 1000))) for m in range(1, 13)]
    buf = _build_trend_chart_sync(data, "expense", (2024, 12), (2024, 11))
    assert buf is not None
    assert buf.read(4) == _PNG_MAGIC


def test_trend_chart_current_month_not_in_data():
    """current_month/prev_month outside data range — no crash, chart renders."""
    data = [(2025, 1, Decimal("5000")), (2025, 2, Decimal("7000"))]
    buf = _build_trend_chart_sync(data, "expense", (2025, 12), (2025, 11))
    assert buf is not None


def test_trend_chart_zero_values():
    data = [(2025, m, Decimal("0")) for m in range(1, 4)]
    buf = _build_trend_chart_sync(data, "expense", (2025, 3), (2025, 2))
    assert buf is not None


# ==================== _build_balance_line_chart_sync ====================


def test_balance_chart_empty_data_returns_none():
    assert _build_balance_line_chart_sync([], 2025, 5) is None


def test_balance_chart_returns_png_buffer():
    data = [(1, Decimal("-500")), (10, Decimal("50000")), (20, Decimal("-3000"))]
    buf = _build_balance_line_chart_sync(data, 2025, 5)
    assert isinstance(buf, io.BytesIO)
    assert buf.read(4) == _PNG_MAGIC


def test_balance_chart_sparse_month_no_crash():
    """Most days have no operations — cumsum must still render across the month."""
    data = [(3, Decimal("12000")), (28, Decimal("-4000"))]
    buf = _build_balance_line_chart_sync(data, 2024, 2)  # leap Feb → 29 days
    assert buf is not None
    assert buf.read(4) == _PNG_MAGIC


def test_balance_chart_negative_final_balance():
    data = [(5, Decimal("1000")), (15, Decimal("-9000"))]
    buf = _build_balance_line_chart_sync(data, 2025, 11)
    assert buf is not None


def test_balance_chart_single_day():
    buf = _build_balance_line_chart_sync([(15, Decimal("7000"))], 2025, 6)
    assert buf is not None
    assert buf.read(4) == _PNG_MAGIC
