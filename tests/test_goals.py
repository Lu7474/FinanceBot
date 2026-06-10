"""Tests for Goal CRUD, deposit/withdraw logic, and formatters."""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session

from core.database.models import Account, Goal, GoalDeposit, Record, User
from core.database.requests import (
    complete_goal,
    create_goal,
    delete_goal,
    deposit_goal,
    get_goal,
    get_goal_deposits,
    get_goal_monthly_pace,
    get_goals,
    update_goal,
    withdraw_goal,
)
from core.exceptions import GoalNotFoundOrCompleted, InsufficientFundsInGoal
from core.utils import (
    add_months,
    format_duration_short,
    format_goal_detail,
    format_goals_list,
    goal_emoji,
    goal_forecast,
    is_goal_overdue,
    monthly_deposit_amount,
    today_msk,
)

# ==================== Helpers ====================


async def _make_user(tg_id: int) -> int:
    async with test_session() as s:
        user = User(tg_id=tg_id, name="GoalTest")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _make_account(user_id: int, name: str = "Карта") -> int:
    async with test_session() as s:
        acc = Account(user_id=user_id, name=name)
        s.add(acc)
        await s.commit()
        await s.refresh(acc)
        return acc.id


async def _make_goal(user_id: int, name: str, target: Decimal, deadline=None) -> int:
    """Creates a goal and returns its id."""
    async with test_session() as s:
        goal = await create_goal(s, user_id, name, target, deadline)
        goal_id = goal.id  # read id before commit (flush already set it)
        await s.commit()
        return goal_id


# ==================== Tests: create_goal ====================


@pytest.mark.asyncio
async def test_create_goal_basic(session):
    user_id = await _make_user(901)
    goal_id = await _make_goal(user_id, "Машина", Decimal("500000"))

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        name = goal.name
        target = goal.target_amount
        current = goal.current_amount
        deadline = goal.deadline
        completed = goal.is_completed

    assert name == "Машина"
    assert target == Decimal("500000")
    assert current == Decimal("0")
    assert deadline is None
    assert completed is False


@pytest.mark.asyncio
async def test_create_goal_with_deadline(session):
    user_id = await _make_user(902)
    deadline = date.today() + timedelta(days=180)
    goal_id = await _make_goal(user_id, "Отпуск", Decimal("100000"), deadline)

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        dl = goal.deadline

    assert dl == deadline


# ==================== Tests: get_goals ====================


@pytest.mark.asyncio
async def test_get_goals_excludes_completed_by_default(session):
    user_id = await _make_user(903)
    await _make_goal(user_id, "Активная", Decimal("10000"))
    g2_id = await _make_goal(user_id, "Завершённая", Decimal("5000"))

    async with test_session() as s:
        await complete_goal(s, g2_id, user_id)
        await s.commit()

    async with test_session() as s:
        active = await get_goals(s, user_id)
        active_names = [g.name for g in active]

    assert len(active_names) == 1
    assert "Активная" in active_names

    async with test_session() as s:
        all_goals = await get_goals(s, user_id, include_completed=True)
        count = len(all_goals)

    assert count == 2


@pytest.mark.asyncio
async def test_get_goals_empty(session):
    user_id = await _make_user(904)
    async with test_session() as s:
        goals = await get_goals(s, user_id)
    assert goals == []


@pytest.mark.asyncio
async def test_get_goal_validates_ownership(session):
    user_id = await _make_user(905)
    other_id = await _make_user(906)
    goal_id = await _make_goal(user_id, "Чужая", Decimal("1000"))

    async with test_session() as s:
        result = await get_goal(s, goal_id, other_id)
    assert result is None

    async with test_session() as s:
        result = await get_goal(s, goal_id, user_id)
    assert result is not None


# ==================== Tests: deposit_goal ====================


@pytest.mark.asyncio
async def test_deposit_increases_current_amount(session):
    user_id = await _make_user(907)
    goal_id = await _make_goal(user_id, "Квартира", Decimal("3000000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("50000"), None, None)
        await s.commit()

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        current = goal.current_amount

    assert current == Decimal("50000")


@pytest.mark.asyncio
async def test_deposit_no_record_but_offsets_balance(session):
    """Deposit to goal with account: NO Record created, account.balance_offset decreased."""
    from sqlalchemy import select

    user_id = await _make_user(908)
    account_id = await _make_account(user_id)
    goal_id = await _make_goal(user_id, "Ноутбук", Decimal("100000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("10000"), None, account_id)
        await s.commit()

    async with test_session() as s:
        records = list(await s.scalars(select(Record).where(Record.user_id == user_id)))
        acc = await s.get(Account, account_id)
        offset = acc.balance_offset

    assert len(records) == 0  # история чистая
    assert offset == Decimal("-10000")  # счёт «потерял» 10к


@pytest.mark.asyncio
async def test_deposit_no_record_without_account(session):
    from sqlalchemy import select

    user_id = await _make_user(909)
    goal_id = await _make_goal(user_id, "Телефон", Decimal("50000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("5000"), None, None)
        await s.commit()

    async with test_session() as s:
        records = list(await s.scalars(select(Record).where(Record.user_id == user_id)))

    assert len(records) == 0


@pytest.mark.asyncio
async def test_deposit_to_completed_goal_raises(session):
    user_id = await _make_user(910)
    goal_id = await _make_goal(user_id, "Завершённая", Decimal("1000"))

    async with test_session() as s:
        await complete_goal(s, goal_id, user_id)
        await s.commit()

    async with test_session() as s:
        with pytest.raises(GoalNotFoundOrCompleted):
            await deposit_goal(s, goal_id, user_id, Decimal("100"), None, None)


@pytest.mark.asyncio
async def test_deposit_saves_note(session):
    user_id = await _make_user(911)
    goal_id = await _make_goal(user_id, "Подушка", Decimal("200000"))

    async with test_session() as s:
        await deposit_goal(
            s, goal_id, user_id, Decimal("20000"), "зарплата за май", None
        )
        await s.commit()

    async with test_session() as s:
        deposits = await get_goal_deposits(s, goal_id)
        note = deposits[0].note if deposits else None

    assert note == "зарплата за май"


# ==================== Tests: withdraw_goal ====================


@pytest.mark.asyncio
async def test_withdraw_decreases_current_amount(session):
    user_id = await _make_user(912)
    goal_id = await _make_goal(user_id, "Запас", Decimal("100000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("30000"), None, None)
        await s.commit()

    async with test_session() as s:
        await withdraw_goal(s, goal_id, user_id, Decimal("10000"), None, None)
        await s.commit()

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        current = goal.current_amount

    assert current == Decimal("20000")


@pytest.mark.asyncio
async def test_withdraw_no_record_but_offsets_balance(session):
    """Withdraw from goal with account: NO Record created, account.balance_offset increased."""
    from sqlalchemy import select

    user_id = await _make_user(913)
    account_id = await _make_account(user_id)
    goal_id = await _make_goal(user_id, "Резерв", Decimal("50000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("50000"), None, None)
        await s.commit()

    async with test_session() as s:
        await withdraw_goal(s, goal_id, user_id, Decimal("15000"), None, account_id)
        await s.commit()

    async with test_session() as s:
        records = list(await s.scalars(select(Record).where(Record.user_id == user_id)))
        acc = await s.get(Account, account_id)
        offset = acc.balance_offset

    assert len(records) == 0  # история чистая
    assert offset == Decimal("15000")  # счёт «получил» 15к обратно


@pytest.mark.asyncio
async def test_withdraw_more_than_available_raises(session):
    user_id = await _make_user(914)
    goal_id = await _make_goal(user_id, "Мало денег", Decimal("100000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("5000"), None, None)
        await s.commit()

    async with test_session() as s:
        with pytest.raises(InsufficientFundsInGoal):
            await withdraw_goal(s, goal_id, user_id, Decimal("10000"), None, None)


@pytest.mark.asyncio
async def test_withdraw_stores_negative_deposit(session):
    user_id = await _make_user(915)
    goal_id = await _make_goal(user_id, "Копилка", Decimal("100000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("50000"), None, None)
        await s.commit()

    async with test_session() as s:
        await withdraw_goal(s, goal_id, user_id, Decimal("20000"), None, None)
        await s.commit()

    async with test_session() as s:
        deposits = await get_goal_deposits(s, goal_id)
        amounts = [d.amount for d in deposits]

    assert Decimal("-20000") in amounts
    assert Decimal("50000") in amounts


# ==================== Tests: complete_goal ====================


@pytest.mark.asyncio
async def test_complete_goal(session):
    user_id = await _make_user(916)
    goal_id = await _make_goal(user_id, "Цель", Decimal("10000"))

    async with test_session() as s:
        await complete_goal(s, goal_id, user_id)
        await s.commit()

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        completed = goal.is_completed

    assert completed is True


@pytest.mark.asyncio
async def test_complete_goal_wrong_user_no_effect(session):
    user_id = await _make_user(917)
    other_id = await _make_user(918)
    goal_id = await _make_goal(user_id, "Чужая цель", Decimal("1000"))

    async with test_session() as s:
        await complete_goal(s, goal_id, other_id)
        await s.commit()

    async with test_session() as s:
        goal = await get_goal(s, goal_id, user_id)
        completed = goal.is_completed

    assert completed is False


# ==================== Tests: delete_goal ====================


@pytest.mark.asyncio
async def test_delete_goal_removes_deposits(session):
    from sqlalchemy import select

    user_id = await _make_user(919)
    goal_id = await _make_goal(user_id, "Удалить", Decimal("10000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("3000"), None, None)
        await s.commit()

    async with test_session() as s:
        await delete_goal(s, goal_id, user_id)
        await s.commit()

    async with test_session() as s:
        goals = await get_goals(s, user_id)
        deposits = list(
            await s.scalars(select(GoalDeposit).where(GoalDeposit.goal_id == goal_id))
        )

    assert len(goals) == 0
    assert len(deposits) == 0


@pytest.mark.asyncio
async def test_delete_goal_wrong_user_no_effect(session):
    user_id = await _make_user(920)
    other_id = await _make_user(921)
    goal_id = await _make_goal(user_id, "Защищённая", Decimal("5000"))

    async with test_session() as s:
        await delete_goal(s, goal_id, other_id)
        await s.commit()

    async with test_session() as s:
        goals = await get_goals(s, user_id)

    assert len(goals) == 1


# ==================== Tests: get_goal_deposits ====================


@pytest.mark.asyncio
async def test_get_goal_deposits_order_and_limit(session):
    user_id = await _make_user(922)
    goal_id = await _make_goal(user_id, "Многократная", Decimal("1000000"))

    async with test_session() as s:
        for i in range(15):
            await deposit_goal(
                s, goal_id, user_id, Decimal(str(1000 * (i + 1))), None, None
            )
        await s.commit()

    async with test_session() as s:
        deposits = await get_goal_deposits(s, goal_id, limit=10)
        amounts = [d.amount for d in deposits]

    assert len(amounts) == 10
    assert amounts[0] == Decimal("15000")  # newest first


# ==================== Tests: format_goals_list ====================


def _make_mock_goal(
    name: str,
    target: float,
    current: float,
    deadline=None,
    is_completed: bool = False,
    created_days_ago: int = 30,
):
    from datetime import datetime

    class MockGoal:
        pass

    g = MockGoal()
    g.name = name
    g.target_amount = Decimal(str(target))
    g.current_amount = Decimal(str(current))
    g.deadline = deadline
    g.is_completed = is_completed
    g.created_at = datetime.combine(
        today_msk() - timedelta(days=created_days_ago), datetime.min.time()
    )
    return g


def test_format_goals_list_basic():
    goals = [_make_mock_goal("Машина", 500000, 100000)]
    text = format_goals_list(goals)
    assert "Машина" in text
    assert "20%" in text
    assert "Мои цели" in text


def test_format_goals_list_achieved():
    goals = [_make_mock_goal("Выполнено", 10000, 10000)]
    text = format_goals_list(goals)
    assert "100%" in text
    assert "достигнута" in text


def test_format_goals_list_with_deadline():
    deadline = date.today() + timedelta(days=90)
    goals = [_make_mock_goal("Отпуск", 50000, 10000, deadline=deadline)]
    text = format_goals_list(goals)
    assert deadline.strftime("%d.%m.%Y") in text


def test_format_goals_list_escapes_html():
    goals = [_make_mock_goal("<script>xss</script>", 1000, 0)]
    text = format_goals_list(goals)
    assert "<script>" not in text


# ==================== Tests: format_goal_detail ====================


def _make_mock_deposit(amount: float, note: str | None = None):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    class MockDeposit:
        pass

    d = MockDeposit()
    d.amount = Decimal(str(amount))
    d.note = note
    d.created_at = datetime(2025, 5, 10, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    return d


def test_format_goal_detail_shows_progress():
    goal = _make_mock_goal("Квартира", 5000000, 1000000)
    text = format_goal_detail(goal, [])
    assert "Квартира" in text
    assert "20%" in text
    assert "Собрано" in text
    assert "Осталось" in text


def test_format_goal_detail_shows_deposits():
    goal = _make_mock_goal("Копилка", 100000, 30000)
    deposits = [
        _make_mock_deposit(20000, "аванс"),
        _make_mock_deposit(10000, None),
    ]
    text = format_goal_detail(goal, deposits)
    assert "Последние операции" in text
    assert "аванс" in text


def test_format_goal_detail_with_deadline():
    deadline = date.today() + timedelta(days=60)
    goal = _make_mock_goal("Телефон", 80000, 20000, deadline=deadline)
    text = format_goal_detail(goal, [])
    assert "Дедлайн" in text
    assert deadline.strftime("%d.%m.%Y") in text


def test_format_goal_detail_deposit_sign():
    goal = _make_mock_goal("Резерв", 100000, 30000)
    deposits = [_make_mock_deposit(10000), _make_mock_deposit(-5000)]
    text = format_goal_detail(goal, deposits)
    assert "+" in text
    assert "-" in text


# ==================== goal_emoji ====================


def test_goal_emoji_keyword_match():
    """Семантический матч по ключевым словам."""
    assert goal_emoji("Машина") == "🚗"
    assert goal_emoji("Новая квартира") == "🏠"
    assert goal_emoji("Отпуск в Турцию") == "✈️"
    assert goal_emoji("iPhone 15") == "📱"
    assert goal_emoji("Макароны") == "🍝"
    assert goal_emoji("Подушка безопасности") == "🛡"


def test_goal_emoji_no_false_positive_short_fragments():
    """Короткие фрагменты (тв, тур, чай, кот, …) не должны ловить случайные слова."""
    # "тв" не матчится в произвольных словах
    assert goal_emoji("Творог на завтрак") != "📺"
    # "тур" с word-boundary: "литература" не должна стать ✈️
    assert goal_emoji("Литература по архитектуре") != "✈️"
    # "чай" не матчит "случай"
    assert goal_emoji("Особый случай") != "☕"
    # "кот" не матчит "котлета"
    assert goal_emoji("Котлета по-киевски") != "🐾"
    # "пес" больше не паттерн → "песок" не ловится
    assert goal_emoji("Песок для строительства") != "🐾"
    # "дет" больше не паттерн → "детально" не ловится
    assert goal_emoji("Изучить детально") != "👶"


def test_goal_emoji_word_boundaries_still_match_real_words():
    """Word-boundary не ломает нормальные совпадения."""
    assert goal_emoji("Новый ТВ") == "📺"
    assert goal_emoji("Тур по Европе") == "✈️"
    assert goal_emoji("Чай с лимоном") == "☕"
    assert goal_emoji("Кот мейн-кун") == "🐾"
    assert goal_emoji("Кошка") == "🐾"


def test_goal_emoji_fallback_deterministic():
    """Без семантического матча — стабильный эмодзи из пула, одинаковый для одного имени."""
    name = "Скрипка Страдивари"
    e1 = goal_emoji(name)
    e2 = goal_emoji(name)
    assert e1 == e2
    assert len(e1) > 0


def test_goal_emoji_case_insensitive():
    """Регистр не важен."""
    assert goal_emoji("МАШИНА") == "🚗"
    assert goal_emoji("машина") == "🚗"
    assert goal_emoji("МаШиНа") == "🚗"


# ==================== Tests: is_goal_overdue ====================


def test_is_goal_overdue_past_deadline():
    g = _make_mock_goal("X", 100, 0, deadline=date.today() - timedelta(days=1))
    assert is_goal_overdue(g) is True


def test_is_goal_overdue_future_deadline():
    g = _make_mock_goal("X", 100, 0, deadline=date.today() + timedelta(days=10))
    assert is_goal_overdue(g) is False


def test_is_goal_overdue_no_deadline():
    g = _make_mock_goal("X", 100, 0)
    assert is_goal_overdue(g) is False


def test_is_goal_overdue_completed_ignored():
    """Завершённая цель не считается просроченной даже с прошедшим дедлайном."""
    g = _make_mock_goal(
        "X", 100, 100, deadline=date.today() - timedelta(days=5), is_completed=True
    )
    assert is_goal_overdue(g) is False


# ==================== Tests: goal_forecast ====================


def test_goal_forecast_basic():
    """Остаток 90к, темп 10к/мес → ceil(90000/10000) = 9 месяцев вперёд."""
    g = _make_mock_goal("X", 100000, 10000)
    fc = goal_forecast(g, rate_per_month=10000)
    assert fc is not None
    assert fc["months"] == 9
    assert fc["eta"] == add_months(today_msk(), 9)
    assert fc["rate"] == 10000


def test_goal_forecast_completed_returns_none():
    g = _make_mock_goal("X", 100, 50, is_completed=True)
    assert goal_forecast(g, 10) is None


def test_goal_forecast_achieved_returns_none():
    g = _make_mock_goal("X", 100, 100)
    assert goal_forecast(g, 10) is None


def test_goal_forecast_no_pace_returns_none():
    """Темпа нет (мало данных) → прогноз не показываем."""
    g = _make_mock_goal("X", 100, 10)
    assert goal_forecast(g, None) is None


def test_goal_forecast_zero_or_negative_pace_returns_none():
    """Копилка не растёт (снятий ≥ взносов) → прогноз не показываем."""
    g = _make_mock_goal("X", 100, 10)
    assert goal_forecast(g, 0) is None
    assert goal_forecast(g, -500) is None


# ==================== Tests: add_months ====================


def test_add_months_simple():
    assert add_months(date(2026, 6, 10), 5) == date(2026, 11, 10)


def test_add_months_year_rollover():
    assert add_months(date(2026, 10, 15), 4) == date(2027, 2, 15)


def test_add_months_day_clamp_to_february():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)  # leap


def test_add_months_december_boundary():
    assert add_months(date(2026, 11, 30), 1) == date(2026, 12, 30)
    assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)


# ==================== Tests: get_goal_monthly_pace ====================


async def _backdate_goal(goal_id: int, days_ago: int) -> None:
    """Sets goal.created_at N days in the past (pace needs age >= 14d)."""
    from datetime import datetime

    async with test_session() as s:
        goal = await s.get(Goal, goal_id)
        goal.created_at = datetime.combine(
            today_msk() - timedelta(days=days_ago), datetime.min.time()
        )
        await s.commit()


async def _add_deposit_at(goal_id: int, amount: Decimal, days_ago: int) -> None:
    """Inserts a GoalDeposit with a backdated created_at."""
    from datetime import datetime

    async with test_session() as s:
        s.add(
            GoalDeposit(
                goal_id=goal_id,
                amount=amount,
                created_at=datetime.combine(
                    today_msk() - timedelta(days=days_ago), datetime.min.time()
                ),
            )
        )
        await s.commit()


async def _pace(goal_id: int):
    async with test_session() as s:
        goal = await s.get(Goal, goal_id)
        return await get_goal_monthly_pace(s, goal_id, goal.created_at)


@pytest.mark.asyncio
async def test_pace_basic(session):
    """Возраст 60 дн, два взноса по 5к → 10к / (60/30) = 5к/мес."""
    user_id = await _make_user(950)
    goal_id = await _make_goal(user_id, "Цель", Decimal("100000"))
    await _backdate_goal(goal_id, 60)
    await _add_deposit_at(goal_id, Decimal("5000"), 10)
    await _add_deposit_at(goal_id, Decimal("5000"), 20)

    result = await _pace(goal_id)
    assert result is not None
    rate, cnt = result
    assert cnt == 2
    assert rate == pytest.approx(5000)


@pytest.mark.asyncio
async def test_pace_too_few_deposits_returns_none(session):
    """Один взнос → недостаточно данных."""
    user_id = await _make_user(951)
    goal_id = await _make_goal(user_id, "Цель", Decimal("100000"))
    await _backdate_goal(goal_id, 60)
    await _add_deposit_at(goal_id, Decimal("5000"), 10)
    assert await _pace(goal_id) is None


@pytest.mark.asyncio
async def test_pace_young_goal_returns_none(session):
    """Возраст < 14 дн → прогноз преждевременный."""
    user_id = await _make_user(952)
    goal_id = await _make_goal(user_id, "Цель", Decimal("100000"))
    await _backdate_goal(goal_id, 10)
    await _add_deposit_at(goal_id, Decimal("5000"), 2)
    await _add_deposit_at(goal_id, Decimal("5000"), 5)
    assert await _pace(goal_id) is None


@pytest.mark.asyncio
async def test_pace_net_negative_when_withdrawals_exceed(session):
    """Снятий больше взносов → темп отрицательный (None отдаст уже forecast)."""
    user_id = await _make_user(953)
    goal_id = await _make_goal(user_id, "Цель", Decimal("100000"))
    await _backdate_goal(goal_id, 60)
    await _add_deposit_at(goal_id, Decimal("5000"), 20)
    await _add_deposit_at(goal_id, Decimal("5000"), 15)
    await _add_deposit_at(goal_id, Decimal("-12000"), 5)  # withdrawal

    result = await _pace(goal_id)
    assert result is not None
    rate, cnt = result
    assert cnt == 2  # отрицательные не считаются взносами
    assert rate < 0


@pytest.mark.asyncio
async def test_pace_excludes_deposits_outside_window(session):
    """Взнос старше окна (90 дн) не входит в темп."""
    user_id = await _make_user(954)
    goal_id = await _make_goal(user_id, "Цель", Decimal("500000"))
    await _backdate_goal(goal_id, 120)
    await _add_deposit_at(goal_id, Decimal("99999"), 100)  # вне окна
    await _add_deposit_at(goal_id, Decimal("5000"), 10)
    await _add_deposit_at(goal_id, Decimal("5000"), 30)

    result = await _pace(goal_id)
    assert result is not None
    rate, cnt = result
    assert cnt == 2
    # net в окне = 10000, months = min(90,120)/30 = 3 → ~3333, старый взнос НЕ задрал темп
    assert rate == pytest.approx(10000 / 3)


# ==================== Tests: monthly_deposit_amount ====================


def test_monthly_deposit_amount_basic():
    # Deadline ровно на 6 календарных месяцев вперёд (функция считает по
    # year*12+month, а не по дням — поэтому фиксируем месяцы, а не timedelta).
    today = today_msk()
    m = today.month - 1 + 6
    deadline = date(today.year + m // 12, m % 12 + 1, 15)
    g = _make_mock_goal("X", 60000, 0, deadline=deadline)
    amount = monthly_deposit_amount(g)
    assert amount is not None
    assert amount == 10000  # 60000 / 6 месяцев


def test_monthly_deposit_amount_no_deadline():
    g = _make_mock_goal("X", 60000, 0)
    assert monthly_deposit_amount(g) is None


# ==================== Tests: smart sort in get_goals ====================


@pytest.mark.asyncio
async def test_get_goals_smart_sort_order(session):
    """Overdue → with deadline (asc) → no deadline (by progress desc)."""
    user_id = await _make_user(950)
    # overdue
    overdue_id = await _make_goal(
        user_id, "Overdue", Decimal("10000"), deadline=date.today() - timedelta(days=5)
    )
    # future deadlines
    far_id = await _make_goal(
        user_id, "Far", Decimal("10000"), deadline=date.today() + timedelta(days=180)
    )
    near_id = await _make_goal(
        user_id, "Near", Decimal("10000"), deadline=date.today() + timedelta(days=10)
    )
    # no deadlines, different progress
    high_id = await _make_goal(user_id, "HighProgress", Decimal("10000"))
    low_id = await _make_goal(user_id, "LowProgress", Decimal("10000"))

    async with test_session() as s:
        await deposit_goal(s, high_id, user_id, Decimal("8000"), None, None)
        await deposit_goal(s, low_id, user_id, Decimal("1000"), None, None)
        await s.commit()

    async with test_session() as s:
        goals = await get_goals(s, user_id)
        order = [g.id for g in goals]

    # Expected: overdue → near → far → high-progress → low-progress
    assert order == [overdue_id, near_id, far_id, high_id, low_id]


# ==================== Tests: update_goal ====================


@pytest.mark.asyncio
async def test_update_goal_name(session):
    user_id = await _make_user(951)
    goal_id = await _make_goal(user_id, "Старое имя", Decimal("1000"))

    async with test_session() as s:
        ok = await update_goal(s, goal_id, user_id, name="Новое имя")
        await s.commit()
        goal = await get_goal(s, goal_id, user_id)
        new_name = goal.name

    assert ok is True
    assert new_name == "Новое имя"


@pytest.mark.asyncio
async def test_update_goal_amount(session):
    user_id = await _make_user(952)
    goal_id = await _make_goal(user_id, "X", Decimal("1000"))

    async with test_session() as s:
        await update_goal(s, goal_id, user_id, target_amount=Decimal("5000"))
        await s.commit()
        goal = await get_goal(s, goal_id, user_id)
        new_target = goal.target_amount

    assert new_target == Decimal("5000")


@pytest.mark.asyncio
async def test_update_goal_set_deadline(session):
    user_id = await _make_user(953)
    goal_id = await _make_goal(user_id, "X", Decimal("1000"))
    new_deadline = date.today() + timedelta(days=30)

    async with test_session() as s:
        await update_goal(s, goal_id, user_id, deadline=new_deadline)
        await s.commit()
        goal = await get_goal(s, goal_id, user_id)
        result_deadline = goal.deadline

    assert result_deadline == new_deadline


@pytest.mark.asyncio
async def test_update_goal_clear_deadline(session):
    user_id = await _make_user(954)
    deadline = date.today() + timedelta(days=30)
    goal_id = await _make_goal(user_id, "X", Decimal("1000"), deadline=deadline)

    async with test_session() as s:
        await update_goal(s, goal_id, user_id, clear_deadline=True)
        await s.commit()
        goal = await get_goal(s, goal_id, user_id)
        result_deadline = goal.deadline

    assert result_deadline is None


@pytest.mark.asyncio
async def test_update_goal_wrong_user_returns_false(session):
    owner_id = await _make_user(955)
    other_id = await _make_user(956)
    goal_id = await _make_goal(owner_id, "X", Decimal("1000"))

    async with test_session() as s:
        ok = await update_goal(s, goal_id, other_id, name="Hacked")
        await s.commit()
        goal = await get_goal(s, goal_id, owner_id)
        name = goal.name

    assert ok is False
    assert name == "X"


# ==================== Tests: format with overdue ====================


def test_format_goals_list_marks_overdue():
    overdue = _make_mock_goal(
        "Просрочка", 10000, 500, deadline=date.today() - timedelta(days=3)
    )
    text = format_goals_list([overdue])
    assert "⚠️" in text
    assert "просрочено" in text


def test_format_goal_detail_marks_overdue():
    overdue = _make_mock_goal(
        "Просрочка", 10000, 500, deadline=date.today() - timedelta(days=3)
    )
    text = format_goal_detail(overdue, [])
    assert "⚠️" in text
    assert "просрочено" in text


def test_format_goal_detail_shows_eta_forecast():
    """ETA-строка с темпом появляется, когда передан честный pace."""
    g = _make_mock_goal("X", 100000, 10000)
    text = format_goal_detail(g, [], pace_per_month=10000)
    assert "При текущем темпе" in text
    assert "достигнешь через 9 мес" in text
    assert "10 000₽/мес" in text


def test_format_goal_detail_no_forecast_without_pace():
    """Без темпа (мало данных) прогноз не показывается."""
    g = _make_mock_goal("X", 100000, 10000)
    text = format_goal_detail(g, [], pace_per_month=None)
    assert "При текущем темпе" not in text


def test_format_goal_detail_forecast_on_time_marker():
    """С дедлайном в будущем и достаточным темпом — маркер ✓."""
    deadline = add_months(today_msk(), 24)
    g = _make_mock_goal("X", 100000, 10000, deadline=deadline)
    text = format_goal_detail(g, [], pace_per_month=10000)
    assert "✓" in text


def test_format_goal_detail_forecast_late_marker():
    """С близким дедлайном и слабым темпом — маркер ⚠️."""
    deadline = add_months(today_msk(), 2)
    g = _make_mock_goal("X", 100000, 10000, deadline=deadline)
    text = format_goal_detail(g, [], pace_per_month=10000)
    assert "⚠️" in text


# ==================== Tests: format_duration_short ====================


def test_format_duration_short_days():
    assert format_duration_short(0) == "0 дн"
    assert format_duration_short(3) == "3 дн"
    assert format_duration_short(6) == "6 дн"


def test_format_duration_short_weeks():
    assert format_duration_short(7) == "1 нед"
    assert format_duration_short(14) == "2 нед"
    assert format_duration_short(29) == "4 нед"


def test_format_duration_short_months():
    assert format_duration_short(30) == "1 мес"
    assert format_duration_short(180) == "6 мес"
    assert format_duration_short(364) == "12 мес"


def test_format_duration_short_years():
    assert format_duration_short(365) == "1 г"
    assert format_duration_short(730) == "2 г"
    # 1 year + 2 months = ~425 days
    assert format_duration_short(425) == "1г 2мес"


def test_format_duration_short_negative_clamped():
    assert format_duration_short(-5) == "0 дн"


# ==================== Tests: complete_goal sets completed_at + achievement bump ====================


@pytest.mark.asyncio
async def test_complete_goal_stamps_completed_at(session):
    """complete_goal должен заполнить completed_at."""
    user_id = await _make_user(960)
    goal_id = await _make_goal(user_id, "Стампим", Decimal("1000"))

    async with test_session() as s:
        await complete_goal(s, goal_id, user_id)
        await s.commit()
        goal = await get_goal(s, goal_id, user_id)
        completed_at = goal.completed_at

    assert completed_at is not None


@pytest.mark.asyncio
async def test_get_goals_achieved_bumped_to_top(session):
    """Достигнутая, но не закрытая цель — выше overdue и прочих."""
    user_id = await _make_user(961)
    overdue_id = await _make_goal(
        user_id, "Overdue", Decimal("10000"), deadline=date.today() - timedelta(days=5)
    )
    near_id = await _make_goal(
        user_id, "Near", Decimal("10000"), deadline=date.today() + timedelta(days=10)
    )
    achieved_id = await _make_goal(user_id, "Achieved", Decimal("5000"))

    async with test_session() as s:
        await deposit_goal(s, achieved_id, user_id, Decimal("5000"), None, None)
        await s.commit()

    async with test_session() as s:
        goals = await get_goals(s, user_id)
        order = [g.id for g in goals]

    # Achieved сверху, затем overdue, затем near
    assert order == [achieved_id, overdue_id, near_id]


# ==================== Tests: regressions from PROJECT_REVIEW.md ====================


@pytest.mark.asyncio
async def test_delete_goal_restores_account_balance_offset(session):
    """Critical #1: deleting a goal must restore balance_offset on all linked accounts."""
    from sqlalchemy import select

    user_id = await _make_user(970)
    acc_id = await _make_account(user_id, "Карта")

    async with test_session() as s:
        await deposit_goal(
            s,
            (await _make_goal_inline(s, user_id, "Машина", Decimal("100000"))),
            user_id,
            Decimal("10000"),
            None,
            acc_id,
        )
        await s.commit()

    # find the goal id we just made
    async with test_session() as s:
        goals = await get_goals(s, user_id)
        goal_id = goals[0].id

    async with test_session() as s:
        acc = await s.scalar(select(Account).where(Account.id == acc_id))
        assert Decimal(str(acc.balance_offset)) == Decimal("-10000")

    async with test_session() as s:
        await delete_goal(s, goal_id, user_id)
        await s.commit()

    async with test_session() as s:
        acc = await s.scalar(select(Account).where(Account.id == acc_id))
        assert Decimal(str(acc.balance_offset)) == Decimal("0"), (
            "delete_goal должен вернуть деньги: было +10 000 на цели, "
            "после удаления balance_offset обязан стать 0, иначе деньги «потеряны»"
        )


async def _make_goal_inline(s, user_id: int, name: str, target: Decimal):
    """Helper: create goal inside an existing session, return id."""
    g = await create_goal(s, user_id, name, target, None)
    await s.flush()
    return g.id


@pytest.mark.asyncio
async def test_goal_deposit_does_not_create_record(session):
    """deposit_goal — перекладка денег в конверт, не расход. Record не создаётся."""
    from sqlalchemy import select

    user_id = await _make_user(971)
    acc_id = await _make_account(user_id, "Карта")
    goal_id = await _make_goal(user_id, "Отпуск", Decimal("50000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("5000"), None, acc_id)
        await s.commit()

    async with test_session() as s:
        records = list(await s.scalars(select(Record).where(Record.user_id == user_id)))

    assert not records, (
        "deposit_goal не должен создавать Record — деньги просто переложены в «конверт», "
        "расход появляется только при завершении цели (complete_goal)"
    )


@pytest.mark.asyncio
async def test_complete_goal_creates_expense_record(session):
    """complete_goal создаёт Record('-', category='Цели') и восстанавливает balance_offset."""
    from sqlalchemy import select

    user_id = await _make_user(972)
    acc_id = await _make_account(user_id, "Карта")
    goal_id = await _make_goal(user_id, "Машина", Decimal("10000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("10000"), None, acc_id)
        await s.commit()

    async with test_session() as s:
        await complete_goal(s, goal_id, user_id)
        await s.commit()

    async with test_session() as s:
        acc = await s.scalar(select(Account).where(Account.id == acc_id))
        records = list(
            await s.scalars(
                select(Record).where(
                    Record.user_id == user_id, Record.category == "Цели"
                )
            )
        )

    assert Decimal(str(acc.balance_offset)) == Decimal("0"), (
        "complete_goal должен восстановить balance_offset (снять earmark)"
    )
    assert len(records) == 1, (
        "complete_goal должен создать одну запись-расход с категорией 'Цели'"
    )
    assert records[0].operation == "-"
    assert Decimal(str(records[0].amount)) == Decimal("10000")
