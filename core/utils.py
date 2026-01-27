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
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # Отключаем GUI для работы на сервере
import matplotlib.pyplot as plt
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from core.database.models import Record


# ==================== Константы ====================

MAX_CATEGORIES_IN_PIE = 7      # Лимит категорий на графике (остальное — "Прочее")
CHART_TIMEOUT_SECONDS = 10     # Таймаут генерации графика (сек)
CHART_DPI = 150                # DPI графика (150 достаточно для Telegram, экономит размер)


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


# ==================== Парсинг дат ====================

# Парсит дату из текста (форматы: 01.01.24, 01.01.2024, 01 января 2024)
def parse_date(text: str) -> Optional[datetime]:
    text = text.lower().strip()
    text = text.replace("г.", "").replace("г", "")

    # Форматы: день.месяц.год
    moscow_tz = ZoneInfo("Europe/Moscow")
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            dt = dt.replace(tzinfo=moscow_tz)
            if dt > datetime.now(moscow_tz):
                return None
            return dt
        except ValueError:
            continue

    # Форматы: день месяц год (на русском)
    for fmt in ("%d %B %Y", "%d %B %y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            dt = dt.replace(tzinfo=moscow_tz)
            if dt > datetime.now(moscow_tz):
                return None
            return dt
        except ValueError:
            continue

    return None


# ==================== Генерация отчётов ====================

# Формирует текстовый отчёт по категориям
def make_report_text(
    categories: dict,
    total: float,
    date: datetime,
    report_type: str,
) -> str:
    month_name = RU_MONTHS[date.month]
    title_type = "Доходы" if report_type == "income" else "Расходы"

    lines = [f"📊 {title_type} за {month_name} {date.year}\n"]

    for name, amount in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"{name} — {format_money(amount)}")

    lines.append(f"\nИтого: {format_money(total)}")
    return "\n".join(lines)


# Получает годы и месяцы, в которых есть записи пользователя (оптимизировано: DISTINCT)
async def get_available_years_and_months(session, user_id: int) -> dict[int, list[int]]:
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    current_year = now.year
    current_month = now.month

    # DISTINCT — возвращает только уникальные пары (год, месяц), а не все записи
    stmt = select(
        func.extract("year", Record.created_at).label("year"),
        func.extract("month", Record.created_at).label("month"),
    ).where(Record.user_id == user_id).distinct()

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

# Синхронная функция построения круговой диаграммы (вызывается в executor)
def _build_report_pie_sync(
    categories: dict,
    total: float,
    date: datetime,
    report_type: str,
) -> Tuple[Optional[io.BytesIO], str]:
    if not categories:
        return None, "Нет данных для построения отчета"

    fig = None
    try:
        month_name = RU_MONTHS[date.month]
        fig, ax = plt.subplots(figsize=(4, 4))

        sorted_categories = dict(sorted(categories.items(), key=lambda x: -x[1]))

        if len(sorted_categories) > MAX_CATEGORIES_IN_PIE:
            other_sum = sum(sorted_categories.values()) - sum(
                list(sorted_categories.values())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories = dict(
                list(sorted_categories.items())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories["Прочее"] = other_sum

        ax.pie(
            sorted_categories.values(),
            labels=sorted_categories.keys(),
            autopct="%1.1f%%",
        )

        title_type = "Доходы" if report_type == "income" else "Расходы"
        ax.set_title(f"{title_type} за {month_name} {date.year}")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight")
        buf.seek(0)

        caption = make_report_text(categories, total, date, report_type)
        return buf, caption

    except Exception:
        logging.exception("Ошибка при построении графика")
        return None, "Ошибка при построении отчета"
    finally:
        if fig is not None:
            plt.close(fig)


# Асинхронная обёртка с таймаутом для построения графика (использует глобальный executor)
async def build_report_pie(
    categories: dict,
    total: float,
    date: datetime,
    report_type: str,
) -> Tuple[Optional[io.BytesIO], str]:
    if not categories:
        return None, "Нет данных для построения отчета"

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,  # Используем глобальный executor вместо создания нового
                _build_report_pie_sync,
                categories,
                total,
                date,
                report_type,
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


# Формирует текстовый отчёт по записям (без повторного запроса к БД)
def make_records_report_text(records: list[Any], report_type: str) -> str:
    """Генерирует текстовый отчёт из уже загруженных записей.

    Args:
        records: Список записей (ORM-объекты или dict с полями operation, amount, category, created_at)
        report_type: "income" или "expense"
    """
    operation_sign = "+" if report_type == "income" else "-"
    type_name = "доходы" if report_type == "income" else "расходы"

    filtered = [r for r in records if (r.operation if hasattr(r, "operation") else r["operation"]) == operation_sign]

    if not filtered:
        return f"{'Доходов' if report_type == 'income' else 'Расходов'} не найдено."

    report = f"Ваши {type_name}:\n"
    for rec in filtered:
        if hasattr(rec, "amount"):
            amount = rec.amount
            category = rec.category
            date = rec.created_at
        else:
            amount = rec["amount"]
            category = rec["category"]
            date = rec["created_at"]
        report += f"{date:%d.%m.%y} — {format_money(amount)} {category}\n"

    return report


# Формирует текст истории операций с итогами
def make_history_text(records: list[Any]) -> str:
    if not records:
        return "Нет записей за указанный период."

    answer = "🕘 История операций:\n\n"
    sumadd = sum(r.amount for r in records if r.operation == "+")
    sumspent = sum(r.amount for r in records if r.operation == "-")
    remaining = sumadd - sumspent

    for r in records:
        category = f" - {r.category}" if getattr(r, "category", None) else ""
        symbol = "➖" if r.operation == "-" else "➕"
        answer += f"{symbol} {r.amount:,.0f}₽{category} ({r.created_at.strftime('%d.%m.%Y')})\n"

    answer += f"\nСумма доходов: {format_money(sumadd)}"
    answer += f"\nСумма расходов: {format_money(sumspent)}"
    answer += f"\nОстаток: {format_money(remaining)}"
    return answer


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
