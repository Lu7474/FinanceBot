"""
Common utilities: money formatting, locale constants, exception decorator.
"""

import calendar
import html
import logging
import re
import unicodedata
from datetime import date as date_type
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from math import ceil
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest

from config import TIMEZONE


def today_msk() -> date_type:
    """Current date in Moscow timezone (Europe/Moscow)."""
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def format_money(amount: Decimal | float | int) -> str:
    """Форматирует сумму с пробелами как разделителями тысяч (русская локаль)."""
    return f"{amount:,.0f}₽".replace(",", " ")


# Невидимые/опасные символы Юникода: soft hyphen, zero-width, word joiner,
# bidi-override (RTL/LTR) и BOM. Срезаются с пользовательского ввода.
_UNSAFE_CHARS_RE = re.compile(
    "[\u00ad\u200b-\u200f\u2060-\u2064\u202a-\u202e\u2066-\u2069\ufeff]"
)


def clean_text(text: str) -> str:
    """Sanitize user input: NFKC-normalize, drop hidden/bidi chars, trim.

    Защита от Unicode-abuse (zero-width «пустые» строки, RTL-разворот UI,
    визуальные дубли). Может вернуть пустую строку — проверяй после вызова.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _UNSAFE_CHARS_RE.sub("", text)
    return text.strip()


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

RU_WEEKDAYS = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


RU_MONTHS_GEN = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


# Dative case for "к <месяцу>" in the goal ETA forecast ("достигнешь к ноябрю").
RU_MONTHS_DAT = {
    1: "январю",
    2: "февралю",
    3: "марту",
    4: "апрелю",
    5: "маю",
    6: "июню",
    7: "июлю",
    8: "августу",
    9: "сентябрю",
    10: "октябрю",
    11: "ноябрю",
    12: "декабрю",
}


# Short month names for compact debt UI: "1 апр" / "15 июн".
# Distinct from scheduler._RU_MONTHS_SHORT which is Capitalized for table headers.
RU_MONTHS_SHORT = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "май",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}


def format_date_ru(d: date_type) -> str:
    """Formats date as '15 марта 2025'."""
    return f"{d.day} {RU_MONTHS_GEN[d.month]} {d.year}"


def format_duration_short(days: int) -> str:
    """Compact RU duration: '5 дн', '3 нед', '7 мес', '1г 2мес', '2 г'."""
    if days < 0:
        days = 0
    if days < 7:
        return f"{days} дн"
    if days < 30:
        weeks = days // 7
        return f"{weeks} нед"
    if days < 365:
        months = days // 30
        return f"{months} мес"
    years = days // 365
    rem_months = (days % 365) // 30
    if rem_months == 0:
        return f"{years} г"
    return f"{years}г {rem_months}мес"


# Семантические правила: ключевые слова → эмодзи.
# Короткие фрагменты обёрнуты в \b чтобы не ловить лишнее (тв, тур, чай, кот, …).
_GOAL_EMOJI_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"машин|авто|тачк|байк|мотоцикл|скутер", re.I), "🚗"),
    (re.compile(r"квартир|жиль|ипотек|комнат", re.I), "🏠"),
    (re.compile(r"дач|загород", re.I), "🏡"),
    (re.compile(r"ремонт|кухн|ванн|мебел", re.I), "🔨"),
    (re.compile(r"отпуск|путешеств|поездк|\bтур|море|отдых", re.I), "✈️"),
    (re.compile(r"телефон|смартфон|айфон|iphone", re.I), "📱"),
    (re.compile(r"ноут|компьютер|\bпк\b|макбук|macbook", re.I), "💻"),
    (re.compile(r"планшет|ipad|айпад", re.I), "📲"),
    (re.compile(r"наушник|airpods|колонк", re.I), "🎧"),
    (re.compile(r"\bкамер|фотоаппарат|фотик", re.I), "📷"),
    (re.compile(r"\bтв\b|телевизор|монитор", re.I), "📺"),
    (re.compile(r"учёб|учеб|\bкурс|образован|универ|диплом|школ", re.I), "🎓"),
    (re.compile(r"книг|читалк", re.I), "📚"),
    (re.compile(r"свадьб|кольц|помолвк", re.I), "💍"),
    (re.compile(r"подушк|резерв|накоплени|финпод|сбережени", re.I), "🛡"),
    (re.compile(r"велосипед|велик", re.I), "🚴"),
    (re.compile(r"одежд|шуб|пальто|кроссовк|обув", re.I), "👕"),
    (re.compile(r"подарок|подарк|сюрприз", re.I), "🎁"),
    (re.compile(r"ребен|роды|коляск|детск|малыш", re.I), "👶"),
    (re.compile(r"спортзал|трениров|фитнес|\bспорт\b", re.I), "🏋"),
    (re.compile(r"бизнес|стартап", re.I), "💼"),
    (re.compile(r"инвест|акци|облигаци|портфел|брокер", re.I), "📈"),
    (re.compile(r"\bеда\b|продукт|макарон|пицц|ресторан", re.I), "🍝"),
    (re.compile(r"\bкофе|\bчай\b|чайник", re.I), "☕"),
    (re.compile(r"\bигр[ауыео]|playstation|xbox|консол|приставк", re.I), "🎮"),
    (re.compile(r"лечен|стоматолог|\bзуб|медицин|операц", re.I), "💊"),
    (re.compile(r"животн|кошк|котят|котёнок|\bкот\b|собак|щенок|питомц", re.I), "🐾"),
    (re.compile(r"гитар|пианин|музык", re.I), "🎸"),
]

# Пул для fallback по хэшу названия
_GOAL_EMOJI_POOL: list[str] = [
    "🎁",
    "🎨",
    "🚀",
    "⭐",
    "💎",
    "🔥",
    "🌟",
    "🎈",
    "🎪",
    "🎭",
    "🍀",
    "🌈",
    "💫",
    "🎵",
    "🏆",
    "🧸",
    "🪄",
    "🎲",
    "🛍",
    "🌻",
]


def goal_emoji(name: str) -> str:
    """Подбирает эмодзи для цели: сначала по ключевым словам, иначе детерминированно из пула."""
    for pattern, emoji in _GOAL_EMOJI_RULES:
        if pattern.search(name):
            return emoji
    # Стабильный хэш по сумме codepoints — не зависит от PYTHONHASHSEED
    idx = sum(ord(c) for c in name.lower()) % len(_GOAL_EMOJI_POOL)
    return _GOAL_EMOJI_POOL[idx]


def _diff_suffix(diff: Decimal, *, is_new: bool) -> str:
    """Renders a parenthesised diff marker for a snapshot row/total."""
    if is_new:
        return "  <i>(новое)</i>"
    if diff > 0:
        return f"  <i>(+{format_money(float(diff))})</i>"
    if diff < 0:
        return f"  <i>(−{format_money(float(abs(diff)))})</i>"
    return "  <i>(=)</i>"


def format_capital(
    wealth_items: list,
    debts: list,
    balances: list,
    last_snapshot=None,
) -> str:
    """Formats the live capital view: assets/liabilities, net worth, last-snapshot diff.

    Virtual rows (💳, read-only) are pulled from open debts and account balances:
      debt I → asset «Мне должны: {person}»; debt O → liability «Долг: {person}».
      account balance > 0 → asset; < 0 → liability (abs); == 0 → skipped.
    """
    assets: list[tuple[str, Decimal, str]] = []  # (label, amount, note)
    liabilities: list[tuple[str, Decimal, str]] = []

    for item in wealth_items:
        note = f"  <i>{html.escape(item.note)}</i>" if item.note else ""
        row = (html.escape(item.name), item.amount, note)
        (assets if item.type == "A" else liabilities).append(row)

    for acc, balance in balances:
        if balance > 0:
            assets.append((f"💳 {html.escape(acc.name)}", balance, ""))
        elif balance < 0:
            liabilities.append((f"💳 {html.escape(acc.name)}", -balance, ""))

    for d in debts:
        if d.direction == "I":
            assets.append((f"💳 Мне должны: {html.escape(d.person_name)}", d.remaining, ""))
        else:
            liabilities.append((f"💳 Долг: {html.escape(d.person_name)}", d.remaining, ""))

    lines = ["📊 <b>Капитал</b>\n"]

    def _section(title: str, rows: list[tuple[str, Decimal, str]]) -> Decimal:
        lines.append(title)
        total = Decimal("0")
        if rows:
            for label, amount, note in rows:
                lines.append(f"  {label}  —  {format_money(float(amount))}{note}")
                total += amount
        else:
            lines.append("  <i>Нет данных</i>")
        return total

    total_assets = _section("💚 <b>АКТИВЫ</b>", assets)
    lines.append(f"  <b>Итого активов:  {format_money(float(total_assets))}</b>")
    lines.append("")
    total_liabilities = _section("🔴 <b>ПАССИВЫ</b>", liabilities)
    lines.append(f"  <b>Итого пассивов:  {format_money(float(total_liabilities))}</b>")

    net = total_assets - total_liabilities
    sign = "+" if net >= 0 else ""
    lines.append(f"\n<b>Чистый капитал:  {sign}{format_money(float(net))}</b>")

    if last_snapshot is not None:
        prev_net = Decimal("0")
        for it in last_snapshot.items:
            prev_net += it.amount if it.type == "A" else -it.amount
        diff = net - prev_net
        dsign = "+" if diff >= 0 else "−"
        lines.append(
            f"📸 <i>Последний снимок: {format_date_ru(last_snapshot.date)} "
            f"({dsign}{format_money(float(abs(diff)))})</i>"
        )

    if debts or balances:
        lines.append("<i>💳 Счета и долги меняются в своих разделах.</i>")

    return "\n".join(lines)


def format_capital_snapshot(
    items: list, prev_items: list | None, snapshot_date: date_type
) -> str:
    """Formats a frozen capital snapshot grouped by assets/liabilities with diffs."""
    prev_map: dict[tuple[str, str], Decimal] = {}
    if prev_items:
        for item in prev_items:
            prev_map[(item.type, item.name)] = item.amount

    has_prev = bool(prev_items)
    date_str = format_date_ru(snapshot_date)
    lines = [f"📸 <b>Снимок капитала</b>\n\n📅 {date_str}\n"]

    assets = [i for i in items if i.type == "A"]
    liabilities = [i for i in items if i.type == "P"]

    def _section(title: str, rows: list) -> tuple[Decimal, Decimal]:
        lines.append(title)
        total = Decimal("0")
        prev_total = Decimal("0")
        for item in rows:
            key = (item.type, item.name)
            if not has_prev:
                suffix = ""
            elif key in prev_map:
                suffix = _diff_suffix(item.amount - prev_map[key], is_new=False)
                prev_total += prev_map[key]
            else:
                suffix = _diff_suffix(Decimal("0"), is_new=True)
            lines.append(
                f"  {html.escape(item.name)}:  "
                f"<b>{format_money(float(item.amount))}</b>{suffix}"
            )
            total += item.amount
        if not rows:
            lines.append("  <i>Нет данных</i>")
        return total, prev_total

    total_a, prev_a = _section("💚 <b>АКТИВЫ</b>", assets)
    a_suffix = _diff_suffix(total_a - prev_a, is_new=False) if has_prev else ""
    lines.append(f"  <b>Итого активов:  {format_money(float(total_a))}</b>{a_suffix}")

    lines.append("")
    total_l, prev_l = _section("🔴 <b>ПАССИВЫ</b>", liabilities)
    l_suffix = _diff_suffix(total_l - prev_l, is_new=False) if has_prev else ""
    lines.append(f"  <b>Итого пассивов:  {format_money(float(total_l))}</b>{l_suffix}")

    net = total_a - total_l
    prev_net = prev_a - prev_l
    sign = "+" if net >= 0 else ""
    net_suffix = _diff_suffix(net - prev_net, is_new=False) if has_prev else ""
    lines.append(
        f"\n<b>Чистый капитал:  {sign}{format_money(float(net))}</b>{net_suffix}"
    )
    return "\n".join(lines)


SYSTEM_KEYWORDS: dict[str, str] = {
    # Транспорт
    "такси": "Транспорт",
    "метро": "Транспорт",
    "автобус": "Транспорт",
    "маршрутка": "Транспорт",
    "бензин": "Транспорт",
    "заправка": "Транспорт",
    # Кафе
    "кафе": "Кафе",
    "ресторан": "Кафе",
    "кофе": "Кафе",
    "макдак": "Кафе",
    # Еда (продукты)
    "продукты": "Еда",
    "пятёрочка": "Еда",
    "дикси": "Еда",
    "магнит": "Еда",
    # Здоровье
    "аптека": "Здоровье",
    "лекарства": "Здоровье",
    # Развлечения
    "кино": "Развлечения",
    "театр": "Развлечения",
    "концерт": "Развлечения",
    # Связь
    "интернет": "Связь",
    "мтс": "Связь",
    "билайн": "Связь",
    "мегафон": "Связь",
    # Зарплата
    "оклад": "Зарплата",
    "аванс": "Зарплата",
}


def parse_search_query(query: str) -> dict:
    """Parses raw search string into structured filter.

    Returns:
        {"type": "gt" | "lt" | "eq" | "text", "value": float | str, "operation": "+" | "-"}
    """
    q = query.strip()
    operation = None
    amount_query = q

    if q[:1] in {"+", "-"} and q[1:].lstrip().startswith((">", "<", "=")):
        operation = q[0]
        amount_query = q[1:].strip()
    else:
        aliases = {
            "income": "+",
            "доход": "+",
            "доходы": "+",
            "expense": "-",
            "expenses": "-",
            "расход": "-",
            "расходы": "-",
        }
        q_lower = q.casefold()
        for prefix, op in aliases.items():
            if q_lower.startswith(prefix):
                rest = q[len(prefix) :].strip()
                if rest.startswith((">", "<", "=")):
                    operation = op
                    amount_query = rest
                    break

    if amount_query.startswith(">"):
        try:
            result = {"type": "gt", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    elif amount_query.startswith("<"):
        try:
            result = {"type": "lt", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    elif amount_query.startswith("="):
        try:
            result = {"type": "eq", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    return {"type": "text", "value": q}


def normalize_category(text: str) -> str:
    """Sanitizes (NFKC + strip hidden chars) and capitalizes first letter."""
    text = clean_text(text)
    return text[0].upper() + text[1:] if text else text


def parse_edit_amount(text: str):
    """Parse amount string: '1500', '1 500', '1500.50', '1500,50'.

    Returns Decimal on success or None if invalid.
    """
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value


def parse_edit_date(text: str, tz: str):
    """Parse 'DD.MM' (current year) or 'DD.MM.YY' date strings.

    Returns naive datetime at 12:00 (tz-local) or None if invalid/future.
    Naive to match how records are stored (moscow_now / record creation).
    """
    from zoneinfo import ZoneInfo as _ZI

    text = text.strip()
    parts = text.split(".")
    now = datetime.now(_ZI(tz)).replace(tzinfo=None)

    if len(parts) == 2:
        fmt = "%d.%m"
        try:
            parsed = datetime.strptime(text, fmt).replace(year=now.year)
        except ValueError:
            return None
    elif len(parts) == 3 and len(parts[2]) == 2:
        fmt = "%d.%m.%y"
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            return None
    elif len(parts) == 3 and len(parts[2]) == 4:
        fmt = "%d.%m.%Y"
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            return None
    else:
        return None

    result = parsed.replace(hour=12, minute=0, second=0, microsecond=0)
    if result > now:
        return None
    return result


def parse_flex_date(text: str) -> date_type | None:
    """Parse 'DD.MM.YY' or 'DD.MM.YYYY' into a date. No past/future check.

    Used for forward-looking dates (debt due date, goal deadline) where the
    caller validates the past/future constraint itself.
    """
    text = text.strip()
    parts = text.split(".")
    if len(parts) != 3:
        return None
    if len(parts[2]) == 2:
        fmt = "%d.%m.%y"
    elif len(parts[2]) == 4:
        fmt = "%d.%m.%Y"
    else:
        return None
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def format_record_card(record) -> str:
    """Render a record detail card for Telegram HTML mode."""
    op_label = "Доход" if record.operation == "+" else "Расход"
    sign = "+" if record.operation == "+" else "−"
    amount_str = f"{sign}{float(record.amount):,.0f}₽".replace(",", " ")

    date_str = record.created_at.strftime("%d.%m.%Y")
    category = html.escape(record.category or "не указано")
    account_str = html.escape(record.account.name) if record.account else "—"

    desc_line = ""
    if record.description:
        desc_line = f"\nОписание: {html.escape(record.description)}"

    return (
        f"📋 <b>Запись #{record.id}</b>\n\n"
        f"Тип: {op_label}\n"
        f"Сумма: <b>{amount_str}</b>\n"
        f"Категория: {category}{desc_line}\n"
        f"Дата: {date_str}\n"
        f"Счёт: {account_str}"
    )


def is_goal_overdue(goal) -> bool:
    """True если у цели просрочен дедлайн и она не завершена."""
    if not goal.deadline or goal.is_completed:
        return False
    return goal.deadline < today_msk()


def add_months(d: date_type, months: int) -> date_type:
    """Adds N months to a date, clamping the day to the target month's length.

    31 янв + 1 мес → 28/29 фев. Без зависимости от dateutil.
    """
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Длина целевого месяца: день 1 следующего месяца минус день.
    if month == 12:
        last_day = 31
    else:
        last_day = (date_type(year, month + 1, 1) - timedelta(days=1)).day
    return date_type(year, month, min(d.day, last_day))


def goal_forecast(goal, rate_per_month: float | None) -> dict | None:
    """ETA-прогноз достижения цели по честному месячному темпу.

    rate_per_month считается отдельно (по реальным взносам, см. get_goal_monthly_pace).
    Возвращает {'months': int, 'eta': date, 'rate': float} или None если:
    завершена / уже достигнута / темпа нет / темп ≤ 0.
    """
    if goal.is_completed:
        return None
    remaining = goal.target_amount - goal.current_amount
    if remaining <= 0:
        return None
    if rate_per_month is None or rate_per_month <= 0:
        return None
    months = ceil(float(remaining) / rate_per_month)
    eta = add_months(today_msk(), months)
    return {"months": months, "eta": eta, "rate": rate_per_month}


def format_goals_list(goals: list) -> str:
    """Formats the list of goals for display. Overdue goals get ⚠️ marker."""
    lines = ["🎯 <b>Мои цели</b>\n"]
    for goal in goals:
        pct = (
            int(float(goal.current_amount) / float(goal.target_amount) * 100)
            if goal.target_amount
            else 0
        )
        pct = min(pct, 100)
        current = format_money(float(goal.current_amount))
        target = format_money(float(goal.target_amount))
        remaining = format_money(float(goal.target_amount - goal.current_amount))

        overdue = is_goal_overdue(goal)
        if goal.current_amount >= goal.target_amount:
            emoji = "✅"
        elif overdue:
            emoji = "⚠️"
        else:
            emoji = goal_emoji(goal.name)
        fam = " 👨‍👩‍👧" if getattr(goal, "family_id", None) else ""

        if goal.current_amount >= goal.target_amount:
            lines.append(f"{emoji} <b>{html.escape(goal.name)}</b>{fam}")
            lines.append(
                f"   {current} / {target}  (100%) — <b>Цель достигнута! 🎉</b>\n"
            )
        else:
            lines.append(f"{emoji} <b>{html.escape(goal.name)}</b>{fam}")
            line = f"   {current} / {target}  ({pct}%)\n   Осталось: {remaining}"
            if goal.deadline:
                deadline_str = goal.deadline.strftime("%d.%m.%Y")
                if overdue:
                    line += f" | <b>просрочено</b> ({deadline_str})"
                else:
                    line += f" | до {deadline_str}"
            lines.append(line + "\n")
    return "\n".join(lines)


def format_goal_detail(
    goal,
    deposits: list,
    pace_per_month: float | None = None,
    contributions: list[tuple[str, "Decimal"]] | None = None,
) -> str:
    """Formats the detailed goal card with overdue marker and ETA forecast.

    pace_per_month — честный месячный темп (см. get_goal_monthly_pace). None → прогноз
    не показывается (мало данных / темп не растёт).
    contributions — для семейной цели: [(имя, net)], показывается блок «Вклады».
    """
    pct = (
        int(float(goal.current_amount) / float(goal.target_amount) * 100)
        if goal.target_amount
        else 0
    )
    pct = min(pct, 100)

    overdue = is_goal_overdue(goal)
    if goal.current_amount >= goal.target_amount:
        emoji = "✅"
    elif overdue:
        emoji = "⚠️"
    else:
        emoji = goal_emoji(goal.name)
    fam = " 👨‍👩‍👧" if getattr(goal, "family_id", None) else ""

    lines = [
        f"{emoji} <b>{html.escape(goal.name)}</b>{fam}",
        "─" * 20,
        f"Цель:     {format_money(float(goal.target_amount))}",
        f"Собрано:  {format_money(float(goal.current_amount))}  ({pct}%)",
        f"Осталось: {format_money(float(goal.target_amount - goal.current_amount))}",
    ]
    if goal.deadline:
        days_left = (goal.deadline - today_msk()).days
        deadline_str = goal.deadline.strftime("%d.%m.%Y")
        if overdue:
            lines.append(
                f"Дедлайн:  {deadline_str} (<b>просрочено на {-days_left} дн.</b>)"
            )
        else:
            lines.append(f"Дедлайн:  {deadline_str} ({days_left} дн.)")
        month_part = _monthly_deposit_str(goal)
        if month_part:
            lines.append(f"Откладывать: ~{month_part}/мес")

    # ETA-прогноз по честному месячному темпу (реальные взносы за окно)
    forecast = goal_forecast(goal, pace_per_month)
    if forecast:
        eta = forecast["eta"]
        month_part = f"к {RU_MONTHS_DAT[eta.month]}"
        if eta.year != today_msk().year:
            month_part += f" {eta.year}"
        rate_str = format_money(forecast["rate"])
        eta_line = f"   достигнешь через {forecast['months']} мес — {month_part}"
        if goal.deadline:
            eta_line += " ✓" if eta <= goal.deadline else " ⚠️"
        lines.append(f"📈 При текущем темпе (+{rate_str}/мес)")
        lines.append(eta_line)

    if contributions:
        lines.append("\n<b>Вклады:</b>")
        for name, net in contributions:
            lines.append(f"  {html.escape(name)}: {format_money(float(net))}")

    if deposits:
        lines.append("\n<b>Последние операции:</b>")
        for d in reversed(deposits):
            sign = "+" if d.amount > 0 else ""
            amount_str = format_money(abs(float(d.amount)))
            date_str = f"{d.created_at.day} {RU_MONTHS_GEN[d.created_at.month]}"
            note_str = f"  «{html.escape(d.note)}»" if d.note else ""
            lines.append(
                f"  {date_str}  {sign}{'-' if d.amount < 0 else ''}{amount_str}{note_str}"
            )

    return "\n".join(lines)


def monthly_deposit_amount(goal) -> float | None:
    """Returns raw monthly deposit needed to hit deadline, or None if no deadline/passed."""
    if not goal.deadline:
        return None
    today = today_msk()
    months_left = (goal.deadline.year - today.year) * 12 + (
        goal.deadline.month - today.month
    )
    if months_left <= 0:
        return None
    return float((goal.target_amount - goal.current_amount) / months_left)


def _monthly_deposit_str(goal) -> str | None:
    """Formatted monthly deposit, or None if not applicable."""
    amount = monthly_deposit_amount(goal)
    return format_money(amount) if amount is not None else None


def format_debt_date_short(d: date_type, today: date_type) -> str:
    """Short date for debt lists: '1 апр' if current year, else '1 апр 2025'."""
    if d.year == today.year:
        return f"{d.day} {RU_MONTHS_SHORT[d.month]}"
    return f"{d.day} {RU_MONTHS_SHORT[d.month]} {d.year}"


def _debt_warn(due_date: date_type | None, today: date_type) -> bool:
    """⚠️ marker condition: overdue or ≤ 3 days left."""
    if due_date is None:
        return False
    return (due_date - today).days <= 3


def _debt_second_line(debt, today: date_type) -> str:
    """Detail line for two-line debt entry: 'из 15 000, до 15 июн' / 'без срока'."""
    parts: list[str] = []
    partial = debt.remaining < debt.amount
    if partial:
        parts.append(f"из {format_money(float(debt.amount))}")
    if debt.due_date:
        prefix = "📅 до " if not partial else "до "
        parts.append(f"{prefix}{format_debt_date_short(debt.due_date, today)}")
    elif not partial:
        parts.append("без срока")
    return ", ".join(parts) if parts else "без срока"


def format_debts_list(debts: list, today: date_type) -> str:
    """Renders the '💸 Долги и займы' block: two sections 📥/📤, two-line per debt."""
    incoming = [d for d in debts if d.direction == "I"]
    outgoing = [d for d in debts if d.direction == "O"]

    lines: list[str] = ["💸 <b>Долги и займы</b>"]

    def _section(items: list, header: str, emoji: str) -> None:
        if not items:
            return
        total = sum(d.remaining for d in items)
        lines.append("")
        lines.append(f"{emoji} <b>{header}: {format_money(float(total))}</b>")
        lines.append("")
        for d in items:
            warn = " ⚠️" if _debt_warn(d.due_date, today) else ""
            lines.append(
                f"{html.escape(d.person_name)} — "
                f"<b>{format_money(float(d.remaining))}</b>{warn}"
            )
            lines.append(f"  {_debt_second_line(d, today)}")

    _section(incoming, "Мне должны", "📥")
    _section(outgoing, "Я должен", "📤")
    return "\n".join(lines)


def format_debt_detail(debt, payments: list, today: date_type) -> str:
    """Renders debt card: full dates for main fields, short for payment history."""
    direction_label = (
        f"{html.escape(debt.person_name)} должен мне"
        if debt.direction == "I"
        else f"Я должен {html.escape(debt.person_name)}"
    )

    lines = [
        f"📋 <b>Долг: {direction_label}</b>",
        "",
        f"Исходная сумма: {format_money(float(debt.amount))}",
    ]
    if debt.remaining < debt.amount:
        lines.append(f"Остаток:        <b>{format_money(float(debt.remaining))}</b>")
    if debt.description:
        lines.append(f"Описание:       {html.escape(debt.description)}")
    lines.append(f"Создан:         {format_date_ru(debt.created_at.date())}")
    if debt.due_date:
        days_left = (debt.due_date - today).days
        due_full = format_date_ru(debt.due_date)
        if days_left < 0:
            tail = f"<b>просрочено на {-days_left} дн.</b>"
        elif days_left == 0:
            tail = "<b>сегодня</b>"
        else:
            tail = f"осталось {days_left} дн."
        lines.append(f"Срок:           {due_full} ({tail})")
    if debt.is_closed and debt.closed_at:
        lines.append(f"Закрыт:         {format_date_ru(debt.closed_at.date())}")

    if payments:
        lines.append("")
        lines.append("<b>История погашений:</b>")
        total_paid = Decimal("0")
        for p in payments:
            d_str = format_debt_date_short(p.paid_at.date(), today)
            note = f" ({html.escape(p.note)})" if p.note else ""
            lines.append(f"  {d_str} — {format_money(float(p.amount))}{note}")
            total_paid += p.amount
        lines.append(f"  <i>Итого выплачено: {format_money(float(total_paid))}</i>")

    return "\n".join(lines)


# ==================== Платежи (напоминания) ====================

PAYMENT_PERIOD_LABELS = {
    "none": "разовый",
    "month": "ежемесячно",
    "year": "ежегодно",
}


def next_due_date(due: date_type, period: str) -> date_type:
    """Next occurrence of a recurring payment. Clamps day to month length.

    'month': +1 month (31 Jan → 28/29 Feb). 'year': +1 year (29 Feb → 28 Feb).
    Raises ValueError for non-recurring periods.
    """
    if period == "month":
        month = due.month + 1
        year = due.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
    elif period == "year":
        year = due.year + 1
        month = due.month
    else:
        raise ValueError(f"period must be 'month' or 'year', got {period!r}")
    last_day = calendar.monthrange(year, month)[1]
    return date_type(year, month, min(due.day, last_day))


def _payment_icon(due: date_type, today: date_type) -> str:
    """Urgency marker: overdue 🔴, due today/tomorrow 🟡, later ⚪."""
    delta = (due - today).days
    if delta < 0:
        return "🔴"
    if delta <= 1:
        return "🟡"
    return "⚪"


def _payment_due_str(due: date_type, today: date_type) -> str:
    """Human due line: '1 июля (завтра)' / '1 июня (просрочено на 3 дн.)'."""
    delta = (due - today).days
    base = f"{due.day} {RU_MONTHS_GEN[due.month]}"
    if delta < 0:
        return f"{base} (просрочено на {-delta} дн.)"
    if delta == 0:
        return f"{base} (сегодня)"
    if delta == 1:
        return f"{base} (завтра)"
    return f"{base} (через {delta} дн.)"


def _payment_amount_str(amount: Decimal | None) -> str:
    """Amount or 'по факту' for floating-sum payments (amount is None)."""
    return format_money(float(amount)) if amount is not None else "по факту"


def format_payments_list(payments: list, today: date_type) -> str:
    """Renders the '💳 Платежи' block: two lines per payment, sorted by caller."""
    lines: list[str] = ["💳 <b>Платежи</b>", ""]
    for p in payments:
        icon = _payment_icon(p.due_date, today)
        lines.append(
            f"{icon} {html.escape(p.title)} — <b>{_payment_amount_str(p.amount)}</b>"
        )
        period = PAYMENT_PERIOD_LABELS.get(p.period, "")
        lines.append(f"  {_payment_due_str(p.due_date, today)} · {period}")
    return "\n".join(lines)


def format_payment_detail(payment, today: date_type) -> str:
    """Renders a single payment card."""
    lines = [f"💳 <b>{html.escape(payment.title)}</b>", ""]
    lines.append(f"Сумма:    {_payment_amount_str(payment.amount)}")

    days_left = (payment.due_date - today).days
    if days_left < 0:
        tail = f"<b>просрочено на {-days_left} дн.</b>"
    elif days_left == 0:
        tail = "<b>сегодня</b>"
    elif days_left == 1:
        tail = "завтра"
    else:
        tail = f"через {days_left} дн."
    lines.append(f"Срок:     {format_date_ru(payment.due_date)} ({tail})")
    lines.append(f"Период:   {PAYMENT_PERIOD_LABELS.get(payment.period, '')}")
    if payment.category:
        lines.append(f"Категория: {html.escape(payment.category)}")
    if payment.last_paid_at:
        lines.append(f"Оплачен:  {format_date_ru(payment.last_paid_at.date())}")
    return "\n".join(lines)


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

                user_id = None
                if (
                    hasattr(message_or_callback, "from_user")
                    and message_or_callback.from_user
                ):
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
