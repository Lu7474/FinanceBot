"""
Chart generation: bar and trend charts using matplotlib.
"""

import asyncio
import calendar
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

matplotlib.rcParams["font.family"] = "DejaVu Sans"

from config import CHART_DPI, CHART_TIMEOUT_SECONDS, MAX_CATEGORIES_IN_PIE  # noqa: E402
from core.utils import RU_MONTHS, format_money  # noqa: E402

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


def _render_category_bars_sync(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    title: str,
    report_type: str,
) -> Optional[io.BytesIO]:
    """Renders a vertical bar chart by category. Returns PNG buffer (no caption)."""
    if not categories:
        return None

    try:
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
        ax.set_title(title, fontsize=14, fontweight="bold", pad=15)

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
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при рендере столбчатой диаграммы")
        return None


def _build_report_pie_sync(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> Tuple[Optional[io.BytesIO], str]:
    """Строит вертикальную столбчатую диаграмму по категориям за месяц + caption."""
    if not categories:
        return None, "Нет данных для построения отчета"

    from core.reports import make_report_text

    month_name = RU_MONTHS[date.month]
    title_type = "Доходы" if report_type == "income" else "Расходы"
    title = f"{title_type} за {month_name} {date.year}"

    buf = _render_category_bars_sync(categories, total, title, report_type)
    if buf is None:
        return None, "Ошибка при построении отчета"

    caption = make_report_text(categories, total, date, report_type, records)
    return buf, caption


async def build_category_chart(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    title: str,
    report_type: str,
) -> Optional[io.BytesIO]:
    """Async wrapper: category bar chart with a custom title (for period switching)."""
    if not categories:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _render_category_bars_sync,
                categories,
                total,
                title,
                report_type,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут category chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении category chart")
        return None


def _build_stacked_bar_chart_sync(
    data: List[Dict[str, Any]],
    operation: str,
) -> Optional[io.BytesIO]:
    """Builds a stacked bar chart: X = months, stacks = categories.

    data: [{year, month, category, total}, ...]. Top MAX_CATEGORIES_IN_PIE
    categories kept, rest folded into "Прочее".
    """
    if not data:
        return None

    try:
        colors = INCOME_COLORS if operation == "+" else EXPENSE_COLORS

        # Ordered unique months
        months: List[Tuple[int, int]] = []
        seen: set[Tuple[int, int]] = set()
        for d in data:
            key = (d["year"], d["month"])
            if key not in seen:
                seen.add(key)
                months.append(key)

        # Category totals across whole period → pick top N
        cat_totals: Dict[str, float] = {}
        for d in data:
            cat_totals[d["category"]] = cat_totals.get(d["category"], 0.0) + float(
                d["total"]
            )
        top_cats = [
            c
            for c, _ in sorted(cat_totals.items(), key=lambda x: -x[1])[
                :MAX_CATEGORIES_IN_PIE
            ]
        ]
        use_other = len(cat_totals) > MAX_CATEGORIES_IN_PIE
        cat_list = top_cats + (["Прочее"] if use_other else [])

        # Build matrix category → [value per month]
        month_index = {m: i for i, m in enumerate(months)}
        matrix: Dict[str, List[float]] = {c: [0.0] * len(months) for c in cat_list}
        for d in data:
            i = month_index[(d["year"], d["month"])]
            if d["category"] in top_cats:
                cat = d["category"]
            elif use_other:
                cat = "Прочее"
            else:
                continue
            matrix[cat][i] += float(d["total"])

        labels = [f"{RU_MONTHS_SHORT[m]}\n{y}" for (y, m) in months]

        fig = Figure(figsize=(max(8, len(months) * 1.3), 5))
        ax = fig.subplots()
        x = list(range(len(months)))

        bottom = [0.0] * len(months)
        for idx, cat in enumerate(cat_list):
            vals = matrix[cat]
            ax.bar(
                x,
                vals,
                bottom=bottom,
                label=cat,
                color=colors[idx % len(colors)],
                edgecolor="white",
                linewidth=0.8,
                width=0.6,
            )
            bottom = [b + v for b, v in zip(bottom, vals)]

        max_total = max(bottom) if bottom else 0.0
        for i, total_v in enumerate(bottom):
            if total_v > 0:
                ax.text(
                    i,
                    total_v + max_total * 0.01,
                    format_money(total_v),
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylim(0, max_total * 1.18 if max_total > 0 else 1)
        ax.set_ylabel("")

        title_type = "Доходы" if operation == "+" else "Расходы"
        ax.set_title(
            f"Структура: {title_type.lower()} по месяцам",
            fontsize=13,
            fontweight="bold",
            pad=15,
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении stacked bar chart")
        return None


async def build_stacked_bar_chart(
    data: List[Dict[str, Any]],
    operation: str,
) -> Optional[io.BytesIO]:
    """Async wrapper for stacked bar chart with timeout."""
    if not data:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_stacked_bar_chart_sync,
                data,
                operation,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут stacked chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении stacked chart")
        return None


# Per-member palette for family charts (matches MEMBER_MARKERS order in keyboards).
FAMILY_MEMBER_COLORS = ["#3498db", "#2ecc71", "#e67e22", "#9b59b6", "#f1c40f"]


def _build_family_stacked_chart_sync(
    data: List[Dict[str, Any]],
    member_meta: List[Tuple[int, str]],
    operation: str,
) -> Optional[io.BytesIO]:
    """Horizontal stacked bar: Y = categories, segment colour = family member.

    data: [{category, user_id, total}, ...]. member_meta: ordered [(user_id, name)]
    (defines colour by position). Top MAX_CATEGORIES_IN_PIE categories kept.
    """
    if not data or not member_meta:
        return None

    try:
        # Category totals → pick top N, fold the rest into "Прочее"
        cat_totals: Dict[str, float] = {}
        for d in data:
            cat_totals[d["category"]] = cat_totals.get(d["category"], 0.0) + float(
                d["total"]
            )
        top_cats = [
            c
            for c, _ in sorted(cat_totals.items(), key=lambda x: -x[1])[
                :MAX_CATEGORIES_IN_PIE
            ]
        ]
        use_other = len(cat_totals) > MAX_CATEGORIES_IN_PIE
        cat_list = top_cats + (["Прочее"] if use_other else [])

        member_ids = [uid for uid, _ in member_meta]
        member_index = {uid: i for i, uid in enumerate(member_ids)}
        cat_index = {c: i for i, c in enumerate(cat_list)}

        # matrix[member_pos] = [value per category]
        matrix: List[List[float]] = [[0.0] * len(cat_list) for _ in member_meta]
        member_totals = [0.0] * len(member_meta)
        for d in data:
            uid = d["user_id"]
            if uid not in member_index:
                continue
            if d["category"] in top_cats:
                ci = cat_index[d["category"]]
            elif use_other:
                ci = cat_index["Прочее"]
            else:
                continue
            mi = member_index[uid]
            val = float(d["total"])
            matrix[mi][ci] += val
            member_totals[mi] += val

        y = list(range(len(cat_list)))
        fig = Figure(figsize=(8, max(4, len(cat_list) * 0.6)))
        ax = fig.subplots()

        left = [0.0] * len(cat_list)
        for mi, (uid, name) in enumerate(member_meta):
            vals = matrix[mi]
            color = FAMILY_MEMBER_COLORS[mi % len(FAMILY_MEMBER_COLORS)]
            label = f"{name} ({format_money(member_totals[mi])})"
            ax.barh(
                y,
                vals,
                left=left,
                label=label,
                color=color,
                edgecolor="white",
                linewidth=0.8,
                height=0.6,
            )
            left = [b + v for b, v in zip(left, vals)]

        ax.set_yticks(y)
        ax.set_yticklabels(cat_list, fontsize=10)
        ax.invert_yaxis()

        max_total = max(left) if left else 0.0
        for i, total_v in enumerate(left):
            if total_v > 0:
                ax.text(
                    total_v + max_total * 0.01,
                    i,
                    format_money(total_v),
                    va="center",
                    fontsize=9,
                    fontweight="bold",
                )

        ax.set_xlim(0, max_total * 1.18 if max_total > 0 else 1)
        title_type = "Доходы" if operation == "+" else "Расходы"
        ax.set_title(
            f"{title_type} по категориям (семья)",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="lower right", fontsize=8, framealpha=0.9)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении family stacked chart")
        return None


async def build_family_stacked_chart(
    data: List[Dict[str, Any]],
    member_meta: List[Tuple[int, str]],
    operation: str,
) -> Optional[io.BytesIO]:
    """Async wrapper for the family stacked chart with timeout."""
    if not data or not member_meta:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_family_stacked_chart_sync,
                data,
                member_meta,
                operation,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут family stacked chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении family stacked chart")
        return None


def _build_yearly_chart_sync(
    data: List[Dict[str, Any]],
    operation: str,
    year: Optional[int],
) -> Optional[io.BytesIO]:
    """Stacked bar chart for the yearly report.

    year set    → X = months of that year.
    year=None   → X = years (all-time view, keeps it readable vs 24-36 months).
    Top MAX_CATEGORIES_IN_PIE categories kept, rest folded into "Прочее".
    """
    if not data:
        return None

    try:
        colors = INCOME_COLORS if operation == "+" else EXPENSE_COLORS

        if year is None:
            x_keys = sorted({d["year"] for d in data})
            labels = [str(k) for k in x_keys]

            def key_of(d: Dict[str, Any]) -> int:
                return d["year"]
        else:
            x_keys = sorted({d["month"] for d in data})
            labels = [RU_MONTHS_SHORT[m] for m in x_keys]

            def key_of(d: Dict[str, Any]) -> int:
                return d["month"]

        # Category totals across whole period → pick top N
        cat_totals: Dict[str, float] = {}
        for d in data:
            cat_totals[d["category"]] = cat_totals.get(d["category"], 0.0) + float(
                d["total"]
            )
        top_cats = [
            c
            for c, _ in sorted(cat_totals.items(), key=lambda x: -x[1])[
                :MAX_CATEGORIES_IN_PIE
            ]
        ]
        use_other = len(cat_totals) > MAX_CATEGORIES_IN_PIE
        cat_list = top_cats + (["Прочее"] if use_other else [])

        key_index = {k: i for i, k in enumerate(x_keys)}
        matrix: Dict[str, List[float]] = {c: [0.0] * len(x_keys) for c in cat_list}
        for d in data:
            i = key_index[key_of(d)]
            if d["category"] in top_cats:
                cat = d["category"]
            elif use_other:
                cat = "Прочее"
            else:
                continue
            matrix[cat][i] += float(d["total"])

        fig = Figure(figsize=(max(8, len(x_keys) * 1.3), 5))
        ax = fig.subplots()
        x = list(range(len(x_keys)))

        bottom = [0.0] * len(x_keys)
        for idx, cat in enumerate(cat_list):
            vals = matrix[cat]
            ax.bar(
                x,
                vals,
                bottom=bottom,
                label=cat,
                color=colors[idx % len(colors)],
                edgecolor="white",
                linewidth=0.8,
                width=0.6,
            )
            bottom = [b + v for b, v in zip(bottom, vals)]

        max_total = max(bottom) if bottom else 0.0
        for i, total_v in enumerate(bottom):
            if total_v > 0:
                ax.text(
                    i,
                    total_v + max_total * 0.01,
                    format_money(total_v),
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylim(0, max_total * 1.18 if max_total > 0 else 1)
        ax.set_ylabel("")

        title_type = "Доходы" if operation == "+" else "Расходы"
        subtitle = "за всё время" if year is None else str(year)
        ax.set_title(
            f"{title_type} — {subtitle}", fontsize=13, fontweight="bold", pad=15
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении yearly chart")
        return None


async def build_yearly_chart(
    data: List[Dict[str, Any]],
    operation: str,
    year: Optional[int],
) -> Optional[io.BytesIO]:
    """Async wrapper for the yearly chart with timeout."""
    if not data:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_yearly_chart_sync,
                data,
                operation,
                year,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут yearly chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении yearly chart")
        return None


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
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении графика тренда")
        return None


def _build_balance_line_chart_sync(
    daily_data: List[Tuple[int, Decimal]],
    year: int,
    month: int,
) -> Optional[io.BytesIO]:
    """Cumulative net balance line for a single month (starts at 0).

    daily_data: [(day, net), ...] — only days with operations. Missing days
    contribute 0; the line is the running sum across every day of the month.
    """
    if not daily_data:
        return None

    try:
        days_in_month = calendar.monthrange(year, month)[1]
        net_by_day = {d: float(v) for d, v in daily_data}

        days = list(range(1, days_in_month + 1))
        cum: List[float] = []
        running = 0.0
        for d in days:
            running += net_by_day.get(d, 0.0)
            cum.append(running)

        final = cum[-1]
        line_color = INCOME_COLORS[0] if final >= 0 else EXPENSE_COLORS[0]

        fig = Figure(figsize=(8, 4))
        ax = fig.subplots()

        ax.plot(days, cum, color=line_color, linewidth=2.5)
        ax.fill_between(days, cum, alpha=0.18, color=line_color)
        ax.axhline(y=0, color="#95a5a6", linestyle="--", linewidth=1, alpha=0.7)

        # Mark the final point + annotate cumulative balance.
        ax.scatter(
            [days[-1]],
            [final],
            color=line_color,
            s=120,
            zorder=5,
            edgecolor="white",
            linewidth=2,
        )
        ax.annotate(
            format_money(final),
            (days[-1], final),
            textcoords="offset points",
            xytext=(0, 12),
            ha="right",
            fontsize=10,
            fontweight="bold",
            color=line_color,
        )

        # X ticks every 5 days (plus the last day) to avoid clutter.
        ticks = [d for d in days if d == 1 or d % 5 == 0]
        if days[-1] not in ticks:
            ticks.append(days[-1])
        ax.set_xticks(ticks)
        ax.set_xlim(1, days_in_month)
        ax.set_ylabel("")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        ax.set_title(
            f"Динамика баланса — {RU_MONTHS[month]} {year}",
            fontsize=13,
            fontweight="bold",
            pad=15,
        )

        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении графика баланса")
        return None


async def build_balance_line_chart(
    daily_data: List[Tuple[int, Decimal]],
    year: int,
    month: int,
) -> Optional[io.BytesIO]:
    """Async wrapper for the monthly balance line chart with timeout."""
    if not daily_data:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_balance_line_chart_sync,
                daily_data,
                year,
                month,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут balance line chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении balance line chart")
        return None


def _build_admin_growth_chart_sync(
    reg: List[Tuple[str, int]],
    dau: List[Tuple[str, int]],
    days: int,
) -> Optional[io.BytesIO]:
    """Admin growth chart: new registrations (bars) + DAU (line) over `days`.

    reg/dau: [(date_str 'YYYY-MM-DD', count), ...] — only days with data.
    Missing days are filled with 0 to keep a continuous daily axis.
    """
    if not reg and not dau:
        return None

    try:
        reg_by_day = {date.fromisoformat(d): n for d, n in reg}
        dau_by_day = {date.fromisoformat(d): n for d, n in dau}

        today = date.today()
        axis = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        reg_vals = [reg_by_day.get(d, 0) for d in axis]
        dau_vals = [dau_by_day.get(d, 0) for d in axis]

        x = list(range(len(axis)))
        fig = Figure(figsize=(max(8, days * 0.3), 4.5))
        ax = fig.subplots()

        ax.bar(
            x,
            reg_vals,
            color="#3498db",
            width=0.7,
            label="Регистрации",
            zorder=2,
        )
        ax.set_ylabel("Регистрации", color="#3498db", fontsize=10)
        ax.tick_params(axis="y", labelcolor="#3498db")
        ax.set_ylim(0, max(reg_vals) * 1.2 if any(reg_vals) else 1)

        ax2 = ax.twinx()
        ax2.plot(
            x,
            dau_vals,
            color="#e74c3c",
            linewidth=2.2,
            marker="o",
            markersize=4,
            label="DAU",
            zorder=3,
        )
        ax2.set_ylabel("DAU", color="#e74c3c", fontsize=10)
        ax2.tick_params(axis="y", labelcolor="#e74c3c")
        ax2.set_ylim(0, max(dau_vals) * 1.2 if any(dau_vals) else 1)

        # X ticks every 5th day to avoid clutter.
        ticks = [i for i in x if i % 5 == 0]
        if x and x[-1] not in ticks:
            ticks.append(x[-1])
        ax.set_xticks(ticks)
        ax.set_xticklabels([axis[i].strftime("%d.%m") for i in ticks], fontsize=9)
        ax.set_xlim(-0.5, len(axis) - 0.5)

        ax.spines["top"].set_visible(False)
        ax.set_title(
            f"Рост и активность за {days} дней",
            fontsize=13,
            fontweight="bold",
            pad=15,
        )

        # Combined legend from both axes.
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении admin growth chart")
        return None


async def build_admin_growth_chart(
    reg: List[Tuple[str, int]],
    dau: List[Tuple[str, int]],
    days: int = 30,
) -> Optional[io.BytesIO]:
    """Async wrapper for the admin growth chart with timeout."""
    if not reg and not dau:
        return None

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_admin_growth_chart_sync,
                reg,
                dau,
                days,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logging.error(f"Таймаут admin growth chart ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении admin growth chart")
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
