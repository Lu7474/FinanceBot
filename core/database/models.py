"""
SQLAlchemy модели: User, Account, Record, SavingsSnapshot, SavingsItem, WealthItem.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import DATABASE_URL, TIMEZONE

# ==================== Подключение к БД ====================

engine = create_async_engine(url=DATABASE_URL)
async_session = async_sessionmaker(engine, expire_on_commit=False)


# Возвращает текущее время по Москве (для default в моделях)
def moscow_now():
    return datetime.now(ZoneInfo(TIMEZONE))


# ==================== Модели ====================


class Base(AsyncAttrs, DeclarativeBase):
    pass


# Пользователь Telegram
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )  # Telegram ID
    name: Mapped[str] = mapped_column(String, nullable=True)  # Имя пользователя
    phone: Mapped[str] = mapped_column(String, nullable=True, default=None)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    records = relationship(
        "Record", back_populates="user", cascade="all, delete-orphan"
    )
    accounts = relationship(
        "Account", back_populates="user", cascade="all, delete-orphan"
    )
    savings_snapshots = relationship(
        "SavingsSnapshot", back_populates="user", cascade="all, delete-orphan"
    )
    wealth_items = relationship(
        "WealthItem", back_populates="user", cascade="all, delete-orphan"
    )
    budgets = relationship(
        "Budget", back_populates="user", cascade="all, delete-orphan"
    )
    user_categories = relationship(
        "UserCategory", back_populates="user", cascade="all, delete-orphan"
    )
    category_keywords = relationship(
        "CategoryKeyword", back_populates="user", cascade="all, delete-orphan"
    )
    goals = relationship(
        "Goal", back_populates="user", cascade="all, delete-orphan"
    )


# Счёт пользователя (Наличные, Карта и т.д.)
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    balance_offset: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0"), server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="accounts")
    records = relationship("Record", back_populates="account")


# Запись дохода или расхода
class Record(Base):
    __tablename__ = "records"

    # Индексы для ускорения запросов по периодам и пользователям
    __table_args__ = (
        Index(
            "ix_records_user_created", "user_id", "created_at"
        ),  # Для выборки по периоду
        Index(
            "ix_records_user_operation", "user_id", "operation"
        ),  # Для отчётов по типу
        Index(
            "ix_records_user_op_cat", "user_id", "operation", "category"
        ),  # Для GROUP BY категориям
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # Индекс на FK
    operation: Mapped[str] = mapped_column(String(1))  # "+" (доход) или "-" (расход)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))  # Сумма
    category: Mapped[str] = mapped_column(
        String(50), default="не указано"
    )  # Категория (макс 50 символов)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=moscow_now, index=True
    )  # Индекс для сортировки
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user = relationship("User", back_populates="records")
    account = relationship("Account", back_populates="records")

    def to_dict(self, include_id: bool = False) -> dict:
        """Конвертирует запись в словарь для передачи в UI."""
        result = {
            "operation": self.operation,
            "amount": float(self.amount),
            "category": self.category,
            "date": self.created_at.strftime("%d.%m.%Y"),
            "created_at": self.created_at,
        }
        if include_id:
            result["id"] = self.id
        return result


# Снимок накоплений за один день
class SavingsSnapshot(Base):
    __tablename__ = "savings_snapshots"
    __table_args__ = (Index("ix_savings_user_date", "user_id", "date", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="savings_snapshots")
    items = relationship(
        "SavingsItem", back_populates="snapshot", cascade="all, delete-orphan"
    )


# Одна строка снимка (название счёта + сумма)
class SavingsItem(Base):
    __tablename__ = "savings_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("savings_snapshots.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)

    snapshot = relationship("SavingsSnapshot", back_populates="items")


# Актив или пассив пользователя
class WealthItem(Base):
    __tablename__ = "wealth_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(
        String(1), nullable=False
    )  # "A" = актив, "P" = пассив
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="wealth_items")


# Месячный бюджет пользователя по категории
class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_user_category", "user_id", "category", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    alerted_80: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    alerted_100: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    last_reset_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user = relationship("User", back_populates="budgets")


# Пользовательская категория (расход / доход / оба)
class UserCategory(Base):
    __tablename__ = "user_categories"
    __table_args__ = (
        Index("ix_user_categories_user_name", "user_id", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    cat_type: Mapped[str] = mapped_column(
        String(1), nullable=False
    )  # "+" доход, "-" расход, "*" оба
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="user_categories")


# Ключевое слово → категория (для умных подсказок)
class CategoryKeyword(Base):
    __tablename__ = "category_keywords"
    __table_args__ = (
        Index("ix_category_keywords_user_keyword", "user_id", "keyword", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("user_categories.id", ondelete="CASCADE"), index=True
    )
    keyword: Mapped[str] = mapped_column(String(50), nullable=False)

    user = relationship("User", back_populates="category_keywords")


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (Index("ix_goals_user_completed", "user_id", "is_completed"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0"), server_default="0")
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="goals")
    deposits = relationship("GoalDeposit", back_populates="goal", cascade="all, delete-orphan")


class GoalDeposit(Base):
    __tablename__ = "goal_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    goal = relationship("Goal", back_populates="deposits")


# ==================== Инициализация ====================


async def _migrate(conn) -> None:
    """Applies additive schema migrations safe for existing SQLite DBs.
    Skipped on PostgreSQL — create_all builds schema from scratch there."""
    if conn.dialect.name != "sqlite":
        return
    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='budgets'")
    )
    if not result.fetchone():
        await conn.execute(
            text("""
            CREATE TABLE budgets (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                category VARCHAR(50) NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                alerted_80 INTEGER NOT NULL DEFAULT 0,
                alerted_100 INTEGER NOT NULL DEFAULT 0,
                last_reset_month INTEGER
            )
        """)
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_budgets_user_category ON budgets(user_id, category)"
            )
        )

    # Add account_id to records if missing (SQLite supports ADD COLUMN)
    result = await conn.execute(text("PRAGMA table_info(records)"))
    columns = {row[1] for row in result.fetchall()}
    if "account_id" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE records ADD COLUMN account_id INTEGER REFERENCES accounts(id)"
            )
        )
    result = await conn.execute(text("PRAGMA table_info(accounts)"))
    acc_columns = {row[1] for row in result.fetchall()}
    if "balance_offset" not in acc_columns:
        await conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN balance_offset DECIMAL(10,2) NOT NULL DEFAULT 0"
            )
        )
    result = await conn.execute(text("PRAGMA table_info(users)"))
    user_columns = {row[1] for row in result.fetchall()}
    if "is_banned" not in user_columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
        )

    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='goals'")
    )
    if not result.fetchone():
        await conn.execute(text("""
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name VARCHAR(100) NOT NULL,
                target_amount DECIMAL(10,2) NOT NULL,
                current_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                deadline DATE,
                is_completed INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                completed_at DATETIME
            )
        """))
        await conn.execute(text(
            "CREATE INDEX ix_goals_user_completed ON goals(user_id, is_completed)"
        ))
    else:
        # Additive: add completed_at to existing goals table if missing
        cols = await conn.execute(text("PRAGMA table_info(goals)"))
        col_names = {row[1] for row in cols.fetchall()}
        if "completed_at" not in col_names:
            await conn.execute(text("ALTER TABLE goals ADD COLUMN completed_at DATETIME"))

    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='goal_deposits'")
    )
    if not result.fetchone():
        await conn.execute(text("""
            CREATE TABLE goal_deposits (
                id INTEGER PRIMARY KEY,
                goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                amount DECIMAL(10,2) NOT NULL,
                note VARCHAR(200),
                created_at DATETIME NOT NULL
            )
        """))
        await conn.execute(text(
            "CREATE INDEX ix_goal_deposits_goal_id ON goal_deposits(goal_id)"
        ))


# Создаёт таблицы в БД и применяет миграции (вызывается при старте бота)
async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
