"""Tests for _build_trend_chart_sync and _build_weekday_chart_sync."""

import io
import sys
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.charts import _build_trend_chart_sync, _build_weekday_chart_sync

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


# ==================== _build_weekday_chart_sync ====================


def test_weekday_chart_returns_png_buffer():
    data = {i: Decimal(str((i + 1) * 1000)) for i in range(7)}
    buf = _build_weekday_chart_sync(data, "-", "Май 2025")
    assert isinstance(buf, io.BytesIO)
    assert buf.read(4) == _PNG_MAGIC


def test_weekday_chart_income_renders():
    data = {i: Decimal("5000") for i in range(7)}
    buf = _build_weekday_chart_sync(data, "+", "Май 2025")
    assert buf is not None
    assert buf.read(4) == _PNG_MAGIC


def test_weekday_chart_all_zeros():
    data = {i: Decimal("0") for i in range(7)}
    buf = _build_weekday_chart_sync(data, "-", "Май 2025")
    assert buf is not None


def test_weekday_chart_empty_dict():
    """Empty dict — all zero values, chart still renders."""
    buf = _build_weekday_chart_sync({}, "-", "Май 2025")
    assert buf is not None


def test_weekday_chart_partial_data():
    """Only some weekdays have data, rest default to 0."""
    data = {0: Decimal("3000"), 4: Decimal("8000")}
    buf = _build_weekday_chart_sync(data, "-", "Апрель 2025")
    assert buf is not None


def test_weekday_chart_buffer_nonempty():
    data = {i: Decimal(str(i * 500)) for i in range(7)}
    buf = _build_weekday_chart_sync(data, "-", "Q1 2025")
    assert buf is not None
    content = buf.read()
    assert len(content) > 1000
