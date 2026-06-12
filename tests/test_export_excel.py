"""Tests for the Excel workbook builders in core/export.py.

Covers _build_export_sync (records/summary/monthly/categories sheets) and
_build_backup_sync (records/budgets/snapshot/wealth sheets) by building the
xlsx in memory and reading it back with pandas. validate_import_row /
parse_import_file / _build_template_sync are covered in test_export_import.py.
"""

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.export import _build_backup_sync, _build_export_sync

# ==================== Helpers ====================


def _rows():
    return [
        {
            "Дата": "10.01.2025",
            "Тип": "Расход",
            "Сумма": 500.0,
            "Категория": "Еда",
            "Описание": "Обед",
            "Счёт": "Наличные",
        },
        {
            "Дата": "15.01.2025",
            "Тип": "Доход",
            "Сумма": 2000.0,
            "Категория": "Зарплата",
            "Описание": "",
            "Счёт": "Карта",
        },
        {
            "Дата": "05.02.2025",
            "Тип": "Расход",
            "Сумма": 300.0,
            "Категория": "Еда",
            "Описание": "",
            "Счёт": "Наличные",
        },
    ]


def _summary():
    return {
        "total_income": 2000.0,
        "total_expense": 800.0,
        "balance": 1200.0,
        "count_income": 1,
        "count_expense": 2,
        "max_expense": 500.0,
        "avg_daily_expense": 400.0,
        "avg_daily_income": 2000.0,
        "first_date": "10.01.2025",
        "last_date": "05.02.2025",
        "top5_expense": [("Еда", 800.0, 100.0)],
        "top5_income": [("Зарплата", 2000.0, 100.0)],
    }


def _read(buf: BytesIO) -> dict:
    return pd.read_excel(buf, sheet_name=None)


# ==================== _build_export_sync ====================


def test_build_export_sync_creates_all_sheets():
    buf = _build_export_sync(_rows(), _summary())
    sheets = _read(buf)
    assert set(sheets) >= {"Записи", "Итоги", "По месяцам", "По категориям"}


def test_build_export_sync_records_sheet_content():
    buf = _build_export_sync(_rows(), _summary())
    df = _read(buf)["Записи"]
    assert len(df) == 3
    assert list(df.columns) == ["Дата", "Тип", "Сумма", "Категория", "Описание", "Счёт"]
    assert "Зарплата" in df["Категория"].values


def test_build_export_sync_monthly_aggregation():
    buf = _build_export_sync(_rows(), _summary())
    df = _read(buf)["По месяцам"]
    # Two data months (01.2025, 02.2025) + ИТОГО row.
    assert "ИТОГО" in df["Месяц"].values
    total = df[df["Месяц"] == "ИТОГО"].iloc[0]
    assert total["Доходы"] == 2000.0
    assert total["Расходы"] == 800.0
    assert total["Баланс"] == 1200.0


def test_build_export_sync_categories_aggregation():
    buf = _build_export_sync(_rows(), _summary())
    df = _read(buf)["По категориям"]
    # Еда appears twice (500 + 300 = 800), aggregated into one row.
    eda = df[(df["Категория"] == "Еда") & (df["Тип"] == "Расход")]
    assert len(eda) == 1
    assert eda.iloc[0]["Сумма"] == 800.0
    assert eda.iloc[0]["Транзакций"] == 2


def test_build_export_sync_summary_values():
    buf = _build_export_sync(_rows(), _summary())
    # Read the Итоги sheet without a header (it's a free-form key/value layout).
    raw = pd.read_excel(buf, sheet_name="Итоги", header=None)
    flat = raw.astype(str).values.flatten().tolist()
    assert "Доходы" in flat
    assert "Общая статистика" in flat


def test_build_export_sync_empty_rows_minimal_sheets():
    buf = _build_export_sync([], _summary())
    sheets = _read(buf)
    # No rows → monthly/categories sheets are skipped.
    assert "Записи" in sheets
    assert "Итоги" in sheets
    assert "По месяцам" not in sheets
    assert "По категориям" not in sheets


def test_build_export_sync_returns_seekable_buffer():
    buf = _build_export_sync(_rows(), _summary())
    assert isinstance(buf, BytesIO)
    assert buf.tell() == 0  # rewound for the caller


# ==================== _build_backup_sync ====================


def test_build_backup_sync_all_sheets():
    buf = _build_backup_sync(
        records_rows=_rows(),
        budgets=[{"Категория": "Еда", "Лимит": 5000.0, "Активен": "Да"}],
        snapshot_items=[{"Тип": "Накопления", "Название": "Вклад", "Сумма": 100000.0}],
        wealth_items=[
            {"Тип": "Актив", "Название": "Квартира", "Сумма": 5000000.0, "Заметка": ""}
        ],
    )
    sheets = _read(buf)
    assert set(sheets) == {"Записи", "Бюджеты", "Снимок капитала", "Активы"}
    assert _read(buf)["Бюджеты"].iloc[0]["Категория"] == "Еда"


def test_build_backup_sync_only_records():
    buf = _build_backup_sync(
        records_rows=_rows(), budgets=[], snapshot_items=[], wealth_items=[]
    )
    sheets = _read(buf)
    # Optional sheets skipped when their data is empty.
    assert set(sheets) == {"Записи"}


def test_build_backup_sync_empty_records_still_has_sheet():
    buf = _build_backup_sync(
        records_rows=[], budgets=[], snapshot_items=[], wealth_items=[]
    )
    df = _read(buf)["Записи"]
    assert list(df.columns) == ["Дата", "Тип", "Сумма", "Категория", "Описание", "Счёт"]
    assert len(df) == 0
