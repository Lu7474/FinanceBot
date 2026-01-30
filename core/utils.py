"""
Утилиты: парсинг дат, генерация графиков, rate-limiter, декораторы.
"""
import asyncio
import io
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from decimal import Decimal

import matplotlib

matplotlib.use("Agg")  # Отключаем GUI для работы на сервере
import matplotlib.pyplot as plt
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from core.database.models import Record
from config import (
    MAX_CATEGORIES_IN_PIE,
    CHART_TIMEOUT_SECONDS,
    CHART_DPI,
    MAX_CAPTION_LENGTH,
)


def format_money(amount: float | int) -> str:
    """Форматирует сумму с пробелами как разделителями тысяч (русская локаль)."""
    return f"{amount:,.0f}₽".replace(",", " ")

# Глобальный executor для CPU-bound задач (графики)
_chart_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart_")


def shutdown_executor() -> None:
    """Останавливает глобальный executor (вызывается при завершении бота)."""
    _chart_executor.shutdown(wait=True)
    logging.info("Chart executor остановлен")

# Словарь русских названий месяцев (для отчётов и UI)
RU_MONTHS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

# Словарь русских названий дней недели (0 = понедельник)
RU_WEEKDAYS = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}

# Цветовые палитры для диаграмм (уникальные цвета без повторяющихся оттенков)
INCOME_COLORS = [
    '#2ecc71',  # Зелёный
    '#3498db',  # Синий
    '#1abc9c',  # Бирюзовый
    '#9b59b6',  # Фиолетовый
    '#f39c12',  # Оранжевый
    '#e74c3c',  # Красный
    '#f1c40f',  # Жёлтый
    '#95a5a6',  # Серый
]
EXPENSE_COLORS = [
    '#e74c3c',  # Красный
    '#e67e22',  # Оранжевый
    '#f1c40f',  # Жёлтый
    '#2ecc71',  # Зелёный
    '#1abc9c',  # Бирюзовый
    '#3498db',  # Синий
    '#9b59b6',  # Фиолетовый
    '#95a5a6',  # Серый
]


# ==================== Генерация отчётов =====================

# Формирует текстовый отчёт по категориям с деталями по датам
def make_report_text(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> str:
    month_name = RU_MONTHS[date.month]
    title_type = "Доходы" if report_type == "income" else "Расходы"
    icon = "💵" if report_type == "income" else "🛒"
    operation_sign = "+" if report_type == "income" else "-"

    lines = [f"📊 <b>{title_type}</b> • {month_name} {date.year}\n"]

    # Категории
    lines.append("📁 <b>По категориям:</b>")
    for name, amount in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"  {icon} {name} — {format_money(amount)}")

    # Детали по датам (если есть записи)
    if records:
        # Фильтруем по типу операции
        filtered = [r for r in records if (r.operation if hasattr(r, "operation") else r["operation"]) == operation_sign]
        if filtered:
            lines.append("\n📅 <b>По датам:</b>")
            for r in filtered:
                if hasattr(r, "amount"):
                    amount = r.amount
                    category = r.category
                    rec_date = r.created_at
                else:
                    amount = r["amount"]
                    category = r["category"]
                    rec_date = r["created_at"]
                short_date = rec_date.strftime("%d.%m")
                lines.append(f"  {short_date} — {operation_sign}{format_money(amount)} {category}")

    # Итого
    lines.append(f"\n💰 <b>Итого:</b> {format_money(total)}")

    result = "\n".join(lines)

    # Обрезка до лимита Telegram caption (1024 символа)
    if len(result) > MAX_CAPTION_LENGTH:
        result = result[:MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result


# Получает годы и месяцы, в которых есть записи пользователя (оптимизировано: DISTINCT)
# operation: "+" для доходов, "-" для расходов, None для всех записей
async def get_available_years_and_months(
    session: Any, user_id: int, operation: Optional[str] = None
) -> Dict[int, List[int]]:
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    current_year = now.year
    current_month = now.month

    # DISTINCT — возвращает только уникальные пары (год, месяц), а не все записи
    stmt = select(
        func.extract("year", Record.created_at).label("year"),
        func.extract("month", Record.created_at).label("month"),
    ).where(Record.user_id == user_id)

    # Фильтрация по типу операции (если указан)
    if operation is not None:
        stmt = stmt.where(Record.operation == operation)

    stmt = stmt.distinct()

    result = await session.execute(stmt)
    rows = result.fetchall()

    if not rows:
        return {}

    data = defaultdict(set)
    for row in rows:
        year = int(row.year)
        month = int(row.month)
        # Пропускаем будущие месяцы
        if year > current_year or (year == current_year and month > current_month):
            continue
        data[year].add(month)

    return {year: sorted(months) for year, months in data.items()}


# ==================== Построение графиков ====================

# Синхронная функция построения вертикальной столбчатой диаграммы (вызывается в executor)
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

    fig = None
    try:
        # Настройка шрифта для кириллицы
        plt.rcParams['font.family'] = 'DejaVu Sans'

        month_name = RU_MONTHS[date.month]
        title_type = "Доходы" if report_type == "income" else "Расходы"
        colors = INCOME_COLORS if report_type == "income" else EXPENSE_COLORS

        fig, ax = plt.subplots(figsize=(8, 5))

        # Сортировка по убыванию суммы
        sorted_categories = dict(sorted(categories.items(), key=lambda x: -x[1]))

        # Ограничение количества категорий
        if len(sorted_categories) > MAX_CATEGORIES_IN_PIE:
            other_sum = sum(sorted_categories.values()) - sum(
                list(sorted_categories.values())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories = dict(
                list(sorted_categories.items())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories["Прочее"] = other_sum

        names = list(sorted_categories.keys())
        values = [float(v) for v in sorted_categories.values()]

        # Вертикальная столбчатая диаграмма
        bars = ax.bar(
            names, values,
            color=colors[:len(values)],
            edgecolor='white',
            linewidth=1.5,
            width=0.7,
        )

        # Значения над столбиками
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + float(total) * 0.01,
                format_money(val),
                ha='center', va='bottom',
                fontsize=10, fontweight='bold',
            )

        # Настройка осей
        ax.set_ylim(0, max(values) * 1.15)
        ax.set_ylabel('')
        ax.set_title(f"{title_type} за {month_name} {date.year}", fontsize=14, fontweight='bold', pad=15)

        # Убираем лишние рамки
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        # Поворот подписей категорий
        ax.tick_params(axis='x', rotation=45)

        # Итого внизу
        fig.text(
            0.5, 0.02,
            f"Итого: {format_money(float(total))}",
            ha='center', fontsize=12, fontweight='bold', color='#2c3e50',
        )

        plt.tight_layout()
        fig.subplots_adjust(bottom=0.2)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor='white')
        buf.seek(0)

        caption = make_report_text(categories, total, date, report_type, records)
        return buf, caption

    except Exception:
        logging.exception("Ошибка при построении графика")
        return None, "Ошибка при построении отчета"
    finally:
        if fig is not None:
            plt.close(fig)


# Асинхронная обёртка с таймаутом для построения графика (использует глобальный executor)
async def build_report_pie(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> Tuple[Optional[io.BytesIO], str]:
    if not categories:
        return None, "Нет данных для построения отчета"

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,  # Используем глобальный executor вместо создания нового
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


# ==================== Сравнение периодов ====================

# Короткие названия месяцев для графика тренда
RU_MONTHS_SHORT = {
    1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр", 5: "Май", 6: "Июн",
    7: "Июл", 8: "Авг", 9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек",
}


def _build_trend_chart_sync(
    monthly_data: List[Tuple[int, int, Decimal]],
    report_type: str,
    current_month: Tuple[int, int],
    prev_month: Tuple[int, int],
) -> Optional[io.BytesIO]:
    """Строит линейный график тренда по месяцам.

    Args:
        monthly_data: Список [(год, месяц, сумма), ...]
        report_type: "income" или "expense"
        current_month: (год, месяц) текущего месяца
        prev_month: (год, месяц) предыдущего месяца

    Returns:
        BytesIO с PNG-изображением или None при ошибке
    """
    if not monthly_data:
        return None

    fig = None
    try:
        plt.rcParams['font.family'] = 'DejaVu Sans'

        fig, ax = plt.subplots(figsize=(8, 4))

        # Подготовка данных
        labels = [f"{RU_MONTHS_SHORT[m]}\n{y}" for y, m, _ in monthly_data]
        values = [float(v) for _, _, v in monthly_data]
        months_keys = [(y, m) for y, m, _ in monthly_data]

        # Цвет линии
        line_color = '#2ecc71' if report_type == "income" else '#e74c3c'

        # Линия тренда
        x = range(len(values))
        ax.plot(x, values, color=line_color, linewidth=2.5, marker='o', markersize=6)
        ax.fill_between(x, values, alpha=0.2, color=line_color)

        # Подсветка текущего и предыдущего месяцев
        for i, (y, m) in enumerate(months_keys):
            if (y, m) == current_month:
                ax.scatter([i], [values[i]], color=line_color, s=150, zorder=5, edgecolor='white', linewidth=2)
                ax.annotate(
                    format_money(values[i]),
                    (i, values[i]),
                    textcoords="offset points",
                    xytext=(0, 12),
                    ha='center',
                    fontsize=10,
                    fontweight='bold',
                    color=line_color,
                )
            elif (y, m) == prev_month:
                ax.scatter([i], [values[i]], color='#7f8c8d', s=100, zorder=5, edgecolor='white', linewidth=2)
                ax.annotate(
                    format_money(values[i]),
                    (i, values[i]),
                    textcoords="offset points",
                    xytext=(0, 12),
                    ha='center',
                    fontsize=9,
                    color='#7f8c8d',
                )

        # Настройка осей
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel('')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        # Заголовок
        title_type = "Доходы" if report_type == "income" else "Расходы"
        ax.set_title(f"{title_type} за последний год", fontsize=13, fontweight='bold', pad=15)

        # Средняя линия
        avg_value = sum(values) / len(values)
        ax.axhline(y=avg_value, color='#95a5a6', linestyle='--', linewidth=1, alpha=0.7)
        ax.text(
            len(values) - 1, avg_value,
            f"  Ср: {format_money(avg_value)}",
            va='center', fontsize=9, color='#7f8c8d',
        )

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight", facecolor='white')
        buf.seek(0)
        return buf

    except Exception:
        logging.exception("Ошибка при построении графика тренда")
        return None
    finally:
        if fig is not None:
            plt.close(fig)


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
        logging.error(f"Таймаут при построении графика тренда ({CHART_TIMEOUT_SECONDS}s)")
        return None
    except Exception:
        logging.exception("Ошибка при построении графика тренда")
        return None


def make_comparison_text(
    current_categories: Dict[str, Decimal],
    prev_categories: Dict[str, Decimal],
    current_total: Decimal,
    prev_total: Decimal,
    current_month: Tuple[int, int],
    prev_month: Tuple[int, int],
    report_type: str,
    avg_monthly: Optional[Decimal] = None,
) -> str:
    """Формирует текст сравнения двух месяцев.

    Args:
        current_categories: Категории текущего месяца
        prev_categories: Категории предыдущего месяца
        current_total: Итого за текущий месяц
        prev_total: Итого за предыдущий месяц
        current_month: (год, месяц) текущего
        prev_month: (год, месяц) предыдущего
        report_type: "income" или "expense"
        avg_monthly: Средний расход/доход за период (опционально)

    Returns:
        Отформатированный текст для caption
    """
    icon = "💵" if report_type == "income" else "🛒"

    cur_name = f"{RU_MONTHS[current_month[1]]} {current_month[0]}"
    prev_name = f"{RU_MONTHS[prev_month[1]]} {prev_month[0]}"

    # Разница
    diff = current_total - prev_total
    if prev_total > 0:
        diff_pct = (diff / prev_total) * 100
        pct_str = f" ({diff_pct:+.0f}%)"
    else:
        pct_str = ""

    if diff > 0:
        diff_icon = "📈"
        diff_sign = "+"
    elif diff < 0:
        diff_icon = "📉"
        diff_sign = ""
    else:
        diff_icon = "➡️"
        diff_sign = ""

    lines = [
        f"📊 <b>Сравнение: {cur_name} vs {prev_name}</b>\n",
        "💰 <b>Итого:</b>",
        f"   {cur_name}: {format_money(current_total)}",
        f"   {prev_name}: {format_money(prev_total)}",
        f"   Разница: {diff_sign}{format_money(abs(diff))}{pct_str} {diff_icon}\n",
    ]

    # Сравнение по категориям (топ изменений)
    all_categories = set(current_categories.keys()) | set(prev_categories.keys())
    changes = []
    for cat in all_categories:
        cur_val = current_categories.get(cat, Decimal(0))
        prev_val = prev_categories.get(cat, Decimal(0))
        cat_diff = cur_val - prev_val
        if cat_diff != 0:
            changes.append((cat, cur_val, prev_val, cat_diff))

    # Сортируем по абсолютному изменению
    changes.sort(key=lambda x: abs(x[3]), reverse=True)

    if changes:
        lines.append(f"{icon} <b>По категориям:</b>")
        is_income = report_type == "income"
        for cat, cur_val, prev_val, cat_diff in changes[:5]:  # Топ-5
            # Цвет: для расходов рост = 🔴, падение = 🟢; для доходов наоборот
            if cat_diff > 0:
                color = "🟢" if is_income else "🔴"
                sign = "+"
            else:
                color = "🔴" if is_income else "🟢"
                sign = ""
            lines.append(f"   {color} {cat}: {format_money(prev_val)} → {format_money(cur_val)} ({sign}{format_money(cat_diff)})")

    # Средний расход
    if avg_monthly:
        lines.append(f"\n📈 <b>Средний за период:</b> {format_money(avg_monthly)}/мес")

    result = "\n".join(lines)

    if len(result) > MAX_CAPTION_LENGTH:
        result = result[:MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result


# ==================== Декораторы ====================

# Декоратор для логирования ошибок и отправки сообщения пользователю
def log_exceptions(error_text: str) -> Callable:
    """Декоратор: логирует исключения и отправляет сообщение пользователю."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception:
                message_or_callback = args[0]
                state = args[1] if len(args) > 1 else None

                # Извлекаем user_id для логирования
                user_id = None
                if hasattr(message_or_callback, "from_user") and message_or_callback.from_user:
                    user_id = message_or_callback.from_user.id

                logging.exception(f"{error_text} [user_id={user_id}]")

                try:
                    if hasattr(message_or_callback, "edit_text"):
                        try:
                            await message_or_callback.edit_text(error_text)
                        except TelegramBadRequest:
                            await message_or_callback.answer(error_text)
                    else:
                        await message_or_callback.answer(error_text)
                except Exception:
                    pass
                if state:
                    await state.clear()
                return None

        return wrapper

    return decorator


# ==================== Rate Limiter ====================

# Ограничитель частоты запросов (защита от спама)
class RateLimiter:
    # Интервал автоочистки неактивных пользователей (сек)
    CLEANUP_INTERVAL = 300  # 5 минут

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests      # Макс. запросов за окно
        self.window_seconds = window_seconds  # Размер окна (сек)
        self._requests: Dict[int, list] = {}  # user_id -> [timestamps]
        self._last_cleanup = time.time()      # Время последней очистки

    # Удаляет неактивных пользователей (вызывается периодически)
    def _cleanup_inactive_users(self) -> None:
        now = time.time()
        # Проверяем интервал очистки
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return

        window_start = now - self.window_seconds
        # Удаляем пользователей без активных запросов
        inactive_users = [
            uid for uid, timestamps in self._requests.items()
            if not timestamps or max(timestamps) < window_start
        ]
        for uid in inactive_users:
            del self._requests[uid]

        self._last_cleanup = now
        if inactive_users:
            logging.debug(f"RateLimiter: очищено {len(inactive_users)} неактивных пользователей")

    # Проверяет, разрешён ли запрос для пользователя
    def is_allowed(self, user_id: int) -> bool:
        # Периодическая очистка памяти
        self._cleanup_inactive_users()

        now = time.time()
        window_start = now - self.window_seconds

        # Получаем историю запросов пользователя
        if user_id not in self._requests:
            self._requests[user_id] = []

        # Удаляем старые записи
        self._requests[user_id] = [
            ts for ts in self._requests[user_id] if ts > window_start
        ]

        # Проверяем лимит
        if len(self._requests[user_id]) >= self.max_requests:
            return False

        # Добавляем текущий запрос
        self._requests[user_id].append(now)
        return True

    # Возвращает секунды до следующего разрешённого запроса
    def get_retry_after(self, user_id: int) -> int:
        if user_id not in self._requests or not self._requests[user_id]:
            return 0

        now = time.time()
        oldest = min(self._requests[user_id])
        retry_after = int(oldest + self.window_seconds - now) + 1
        return max(0, retry_after)


# Глобальный rate limiter: 20 запросов/мин на пользователя
rate_limiter = RateLimiter(max_requests=20, window_seconds=60)


# ==================== Middleware ====================

# Middleware для проверки лимита запросов перед обработкой
class RateLimitMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id and not rate_limiter.is_allowed(user_id):
            retry_after = rate_limiter.get_retry_after(user_id)
            logging.warning(f"Rate limit для user_id={user_id}, retry_after={retry_after}s")
            if isinstance(event, Message):
                await event.answer(f"Слишком много запросов. Подождите {retry_after} сек.")
            elif isinstance(event, CallbackQuery):
                await event.answer(f"Подождите {retry_after} сек.", show_alert=True)
            return None

        return await handler(event, data)
