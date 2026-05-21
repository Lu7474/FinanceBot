"""
Chart generation: bar and trend charts using matplotlib.
"""

import asyncio
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

from matplotlib.figure import Figure

matplotlib.rcParams["font.family"] = "DejaVu Sans"

from config import CHART_DPI, CHART_TIMEOUT_SECONDS, MAX_CATEGORIES_IN_PIE
from core.utils import RU_MONTHS, format_money

INCOME_COLORS = [
    "#2ecc71",
    "#3498db",
    "#1abc9c",
    "#9b59b6",
    "#f39c12",
    "#e74c3c",
    "#f1c40f",
    "#95a5a6",
]
EXPENSE_COLORS = [
    "#e74c3c",
    "#e67e22",
    "#f1c40f",
    "#2ecc71",
    "#1abc9c",
    "#3498db",
    "#9b59b6",
    "#95a5a6",
]

RU_MONTHS_SHORT = {
    1: "Янв",
    2: "Фев",
    3: "Мар",
    4: "Апр",
    5: "Май",
    6: "Июн",
    7: "Июл",
    8: "Авг",
    9: "Сен",
    10: "Окт",
    11: "Ноя",
    12: "Дек",
}

_chart_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart_")


def shutdown_executor() -> None:
    """Останавливает глобальный executor (вызывается при завершении бота)."""
    _chart_executor.shutdown(wait=True)
    logging.info("Chart executor остановлен")


def _build_report_pie_sync(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> Tuple[Optional[io.BytesIO], str]:
    """Строит вертикальную столбчатую диаграмму по категориям."""
    if not categories:
        return None, "Нет данных для построения отчета"

    from core.reports import make_report_text

    try:
        month_name = RU_MONTHS[date.month]
        title_type = "Доходы" if report_type == "income" else "Расходы"
        colors = INCOME_COLORS if report_type == "income" else EXPENSE_COLORS

        fig = Figure(figsize=(8, 5))
        ax = fig.subplots()

        sorted_categories = dict(sorted(categories.items(), key=lambda x: -x[1]))

        if len(sorted_categories) > MAX_CATEGORIES_IN_PIE:
            other_sum = sum(sorted_categories.values(), Decimal("0")) - sum(
                list(sorted_categories.values())[:MAX_CATEGORIES_IN_PIE], Decimal("0")
            )
            sorted_categories = dict(
                list(sorted_categories.items())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories["Прочее"] = other_sum

        names = list(sorted_categories.keys())
        values = [float(v) for v in sorted_categories.values()]

        bars = ax.bar(
            names,
            values,
            color=colors[: len(values)],
            edgecolor="white",
            linewidth=1.5,
            width=0.7,
        )

        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + float(total) * 0.01,
                format_money(val),
                ha="center",
                va="bottom",
                fontsize=10,
                fontweight="bold",
            )

        ax.set_ylim(0, max(values) * 1.15)
        ax.set_ylabel("")
        ax.set_title(
            f"{title_type} за {month_name} {date.year}",
            fontsize=14,
            fontweight="bold",
            pad=15,
        )

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="x", rotation=45)

        fig.text(
            0.5,
            0.02,
            f"Итого: {format_money(float(total))}",
            ha="center",
            fontsize=12,
            fontweight="bold",
            color="#2c3e50",
        )

        fig.tight_layout()
        fig.subplots_adjust(bottom=0.2)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
        buf.seek(0)

        caption = make_report_text(categories, total, date, report_type, records)
        return buf, caption

    except Exception:
        logging.exception("Ошибка при построении графика")
        return None, "Ошибка при построении отчета"


async def build_report_pie(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> Tuple[Optional[io.BytesIO], str]:
    """Асинхронная обёртка с таймаутом для построения графика."""
    if not categories:
        return None, "Нет данных для построения отчета"

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_report_pie_sync,
                categories,
                total,
                date,
                report_type,
                records,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        logging.error(f"Таймаут при построении графика ({CHART_TIMEOUT_SECONDS}s)")
        return None, "Превышено время ожидания построения графика"
    except Exception:
        logging.exception("Ошибка при построении графика")
        return None, "Ошибка при построении отчета"


def _build_trend_chart_sync(
    monthly_data: List[Tuple[int, int, Decimal]],
    report_type: str,
    current_month: Tuple[int, int],
    prev_month: Tuple[int, int],
) -> Optional[io.BytesIO]:
    """Строит линейный график тренда по месяцам."""
    if not monthly_data:
        return None

    try:
        fig = Figure(figsize=(8, 4))
        ax = fig.subplots()

        labels = [f"{RU_MONTHS_SHORT[m]}\n{y}" for y, m, _ in monthly_data]
        values = [float(v) for _, _, v in monthly_data]
        months_keys = [(y, m) for y, m, _ in monthly_data]

        line_color = "#2ecc71" if report_type == "income" else "#e74c3c"

        x = range(len(values))
        ax.plot(x, values, color=line_color, linewidth=2.5, marker="o", markersize=6)
        ax.fill_between(x, values, alpha=0.2, color=line_color)

        for i, (y, m) in enumerate(months_keys):
            if (y, m) == current_month:
                ax.scatter(
                    [i],
                    [values[i]],
                    color=line_color,
                    s=150,
                    zorder=5,
                    edgecolor="white",
                    linewidth=2,
                )
                ax.annotate(
                    format_money(values[i]),
                    (i, values[i]),
                    textcoords="offset points",
                    xytext=(0, 12),
                    ha="center",
                    fontsize=10,
                    fontweight="bold",
                    color=line_color,
                )
            elif (y, m) == prev_month:
                ax.scatter(
                    [i],
                    [values[i]],
                    color="#7f8c8d",
                    s=100,
                    zorder=5,
                    edgecolor="white",
                    linewidth=2,
                )
                ax.annotate(
                    format_money(values[i]),
                    (i, values[i]),
                    textcoords="offset points",
                    xytext=(0, 12),
                    ha="center",
                    fontsize=9,
                    color="#7f8c8d",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        title_type = "Доходы" if report_type == "income" else "Расходы"
        ax.set_title(
            f"{title_type} за последний год", fontsize=13, fontweight="bold", pad=15
        )

        avg_value = sum(values) / len(values)
        ax.axhline(y=avg_value, color="#95a5a6", linestyle="--", linewidth=1, alpha=0.7)
        ax.text(
            len(values) - 1,
            avg_value,
            f"  Ср: {format_money(avg_value)}",
            va="center",
            fontsize=9,
            color="#7f8c8d",
        )

        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении графика тренда")
        return None


def _build_weekday_chart_sync(
    data: dict[int, Decimal],
    operation: str,
    period_label: str,
) -> Optional[io.BytesIO]:
    """Builds a horizontal bar chart of spending/income by weekday (Mon–Sun)."""
    from core.utils import RU_WEEKDAYS

    try:
        values = [float(data.get(i, 0)) for i in range(7)]
        labels = [RU_WEEKDAYS[i] for i in range(7)]

        max_val = max(values) if values else 0
        base_color = "#e74c3c" if operation == "-" else "#2ecc71"
        colors = [
            "#e74c3c" if (v == max_val and max_val > 0) else base_color for v in values
        ]

        title_type = "Расходы" if operation == "-" else "Доходы"
        title = f"{title_type} по дням недели ({period_label})"

        fig = Figure(figsize=(8, 5))
        ax = fig.subplots()
        ax.barh(labels, values, color=colors, edgecolor="white", linewidth=1.2)
        ax.invert_yaxis()

        ax.set_title(title, fontsize=13, fontweight="bold", pad=12)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        for i, v in enumerate(values):
            if v > 0:
                ax.text(v + max_val * 0.01, i, format_money(v), va="center", fontsize=9)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white")
        buf.seek(0)
        return buf
    except Exception:
        logging.exception("Ошибка при построении weekday chart")
        return None


async def build_weekday_chart(
    data: dict[int, Decimal],
    operation: str,
    period_label: str,
) -> Optional[io.BytesIO]:
    """Async wrapper for weekday chart with timeout."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_weekday_chart_sync,
                data,
                operation,
                period_label,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут weekday chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении weekday chart")
        return None


async def build_trend_chart(
    monthly_data: List[Tuple[int, int, Decimal]],
    report_type: str,
    current_month: Tuple[int, int],
    prev_month: Tuple[int, int],
) -> Optional[io.BytesIO]:
    """Асинхронная обёртка для построения графика тренда."""
    if not monthly_data:
        return None

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_trend_chart_sync,
                monthly_data,
                report_type,
                current_month,
                prev_month,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        logging.error(
            f"Таймаут при построении графика тренда ({CHART_TIMEOUT_SECONDS}s)"
        )
        return None
    except Exception:
        logging.exception("Ошибка при построении графика тренда")
        return None
