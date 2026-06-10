"""
SQLAlchemy модели: User, Account, Record, SavingsSnapshot, SavingsItem, WealthItem,
Budget, UserCategory, CategoryKeyword, Goal, GoalDeposit, Debt, DebtPayment,
Payment, Family, FamilyMember.
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

engine = create_async_engine(
    url=DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


def setup_sqlite_engine(target_engine) -> None:
    """Registers FK enforcement and a Unicode-aware lower() on SQLite connections.

    SQLite's built-in lower() only folds ASCII, breaking case-insensitive
    search for Cyrillic. We override it with Python's str.lower(). On
    PostgreSQL native lower() is already Unicode-correct, so this is a no-op there.
    """
    from sqlalchemy import event

    @event.listens_for(target_engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")
        dbapi_conn.create_function(
            "lower", 1, lambda s: s.lower() if s is not None else s, deterministic=True
        )


if DATABASE_URL.startswith("sqlite"):
    setup_sqlite_engine(engine)


# Возвращает текущее время по Москве (для default в моделях)
def moscow_now():
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)


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
    name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # Имя пользователя
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    notify_weekly: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notify_monthly: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notify_daily: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notify_reminder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notify_debts: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notify_payments: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    description_mode: Mapped[str] = mapped_column(
        String(10), default="off", server_default="off"
    )  # off | brackets | button | auto
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
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    debts = relationship("Debt", back_populates="user", cascade="all, delete-orphan")
    payments = relationship(
        "Payment", back_populates="user", cascade="all, delete-orphan"
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
        DECIMAL(14, 2), default=Decimal("0"), server_default="0"
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
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2))  # Сумма
    category: Mapped[str] = mapped_column(
        String(50), default="не указано"
    )  # Категория (макс 50 символов)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=moscow_now, index=True
    )  # Индекс для сортировки
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Links the two records (expense + income) of one transfer. NULL for normal records.
    transfer_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
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
            "description": self.description,
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
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)

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
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
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
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
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
    # NULL → личная цель. Задан → общая семейная (goal.user_id = создатель = owner семьи).
    # ondelete=SET NULL: при роспуске семьи общая цель становится личной у ex-owner,
    # balance_offset/деньги целы (CASCADE удалил бы цель в обход delete_goal → порча балансов).
    family_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("families.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(14, 2), default=Decimal("0"), server_default="0"
    )
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="goals")
    deposits = relationship(
        "GoalDeposit", back_populates="goal", cascade="all, delete-orphan"
    )


class GoalDeposit(Base):
    __tablename__ = "goal_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    # Кто внёс/снял (для семейных целей: атрибуция вклада + запись расхода на нужный счёт).
    # NULL у старых строк → трактуется как взнос владельца цели.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    goal = relationship("Goal", back_populates="deposits")


# Долг пользователя (мне должны / я должен). Изолированная сущность —
# на баланс/отчёты/цели не влияет, погашение НЕ создаёт Record.
class Debt(Base):
    __tablename__ = "debts"
    __table_args__ = (
        Index("ix_debts_user_closed", "user_id", "is_closed"),
        Index("ix_debts_due_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(1), nullable=False)  # "I" / "O"
    person_name: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    remaining: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="debts")
    payments = relationship(
        "DebtPayment", back_populates="debt", cascade="all, delete-orphan"
    )


class DebtPayment(Base):
    __tablename__ = "debt_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(
        ForeignKey("debts.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    debt = relationship("Debt", back_populates="payments")


# Напоминание о платеже (налоги, страховки, ОСАГО, коммуналка, подписки).
# Изолированная сущность: на баланс/отчёты не влияет, оплата НЕ создаёт Record.
# Разовый (period='none') после оплаты → is_active=False; периодический → due_date
# переезжает на следующий цикл.
class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_user_active", "user_id", "is_active"),
        Index("ix_payments_due_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    # NULL → плавающая сумма («~», коммуналка): напоминаем без точной суммы.
    amount: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(
        String(10), default="none", server_default="none"
    )  # none | month | year
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    last_paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="payments")


# Семья — группа пользователей с общим доступом к истории и отчётам.
# Записи остаются личными (Record.user_id), семья только агрегирует их в scope.
class Family(Base):
    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # быстрый чек прав управления
    invite_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    members = relationship(
        "FamilyMember", back_populates="family", cascade="all, delete-orphan"
    )


# Членство пользователя в семье — единственный источник правды о составе.
# Один юзер может состоять только в одной семье (unique на user_id).
class FamilyMember(Base):
    __tablename__ = "family_members"
    __table_args__ = (Index("ix_family_members_family_id", "family_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # "owner" | "member"
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    family = relationship("Family", back_populates="members")


# ==================== Инициализация ====================


async def _migrate_postgres(conn) -> None:
    """Adds missing additive columns on PostgreSQL.

    create_all() never alters existing tables, so every additive column added
    to a model after first deploy must be listed here. IF NOT EXISTS makes it
    idempotent (requires PG 9.6+).
    """
    stmts = [
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS balance_offset DECIMAL(14,2) NOT NULL DEFAULT 0",
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reminded_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_weekly BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_monthly BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_daily BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_reminder BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_debts BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_payments BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS description_mode VARCHAR(10) NOT NULL DEFAULT 'off'",
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS description VARCHAR(255)",
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS transfer_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_records_transfer_id ON records(transfer_id)",
        "ALTER TABLE goals ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
        "ALTER TABLE goals ADD COLUMN IF NOT EXISTS family_id INTEGER REFERENCES families(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_goals_family_id ON goals(family_id)",
        "ALTER TABLE goal_deposits ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_goal_deposits_user_id ON goal_deposits(user_id)",
    ]
    for stmt in stmts:
        await conn.execute(text(stmt))


async def _migrate(conn) -> None:
    """Applies additive schema migrations for existing DBs (SQLite & PostgreSQL).

    create_all() builds only missing tables — it never adds columns to tables
    that already exist. Every additive column change has to be handled here for
    each dialect we deploy to.
    """
    dialect = conn.dialect.name
    if dialect == "postgresql":
        await _migrate_postgres(conn)
        return
    if dialect != "sqlite":
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
                amount DECIMAL(14,2) NOT NULL,
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
    if "description" not in columns:
        await conn.execute(
            text("ALTER TABLE records ADD COLUMN description VARCHAR(255)")
        )
    if "account_id" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE records ADD COLUMN account_id INTEGER REFERENCES accounts(id)"
            )
        )
    if "transfer_id" not in columns:
        await conn.execute(text("ALTER TABLE records ADD COLUMN transfer_id INTEGER"))
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_records_transfer_id ON records(transfer_id)"
        )
    )
    result = await conn.execute(text("PRAGMA table_info(accounts)"))
    acc_columns = {row[1] for row in result.fetchall()}
    if "balance_offset" not in acc_columns:
        await conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN balance_offset DECIMAL(14,2) NOT NULL DEFAULT 0"
            )
        )
    result = await conn.execute(text("PRAGMA table_info(users)"))
    user_columns = {row[1] for row in result.fetchall()}
    if "is_banned" not in user_columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
        )
    if "last_reminded_at" not in user_columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN last_reminded_at DATETIME")
        )
    if "notify_weekly" not in user_columns:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN notify_weekly INTEGER NOT NULL DEFAULT 0"
            )
        )
    if "notify_monthly" not in user_columns:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN notify_monthly INTEGER NOT NULL DEFAULT 0"
            )
        )
    if "notify_daily" not in user_columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN notify_daily INTEGER NOT NULL DEFAULT 0")
        )
    if "notify_reminder" not in user_columns:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN notify_reminder INTEGER NOT NULL DEFAULT 0"
            )
        )
    if "notify_debts" not in user_columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN notify_debts INTEGER NOT NULL DEFAULT 0")
        )
    if "notify_payments" not in user_columns:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN notify_payments INTEGER NOT NULL DEFAULT 0"
            )
        )
    if "description_mode" not in user_columns:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN description_mode VARCHAR(10) NOT NULL DEFAULT 'off'"
            )
        )

    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='goals'")
    )
    if not result.fetchone():
        await conn.execute(
            text("""
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name VARCHAR(100) NOT NULL,
                target_amount DECIMAL(14,2) NOT NULL,
                current_amount DECIMAL(14,2) NOT NULL DEFAULT 0,
                deadline DATE,
                is_completed INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                completed_at DATETIME
            )
        """)
        )
        await conn.execute(
            text("CREATE INDEX ix_goals_user_completed ON goals(user_id, is_completed)")
        )
    else:
        # Additive: add completed_at / family_id to existing goals table if missing
        cols = await conn.execute(text("PRAGMA table_info(goals)"))
        col_names = {row[1] for row in cols.fetchall()}
        if "completed_at" not in col_names:
            await conn.execute(
                text("ALTER TABLE goals ADD COLUMN completed_at DATETIME")
            )
        if "family_id" not in col_names:
            await conn.execute(
                text(
                    "ALTER TABLE goals ADD COLUMN family_id INTEGER "
                    "REFERENCES families(id) ON DELETE SET NULL"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_goals_family_id ON goals(family_id)"
                )
            )

    result = await conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='goal_deposits'"
        )
    )
    if not result.fetchone():
        await conn.execute(
            text("""
            CREATE TABLE goal_deposits (
                id INTEGER PRIMARY KEY,
                goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                amount DECIMAL(14,2) NOT NULL,
                note VARCHAR(200),
                created_at DATETIME NOT NULL
            )
        """)
        )
        await conn.execute(
            text("CREATE INDEX ix_goal_deposits_goal_id ON goal_deposits(goal_id)")
        )
    else:
        # Additive: add user_id (depositor) to existing goal_deposits table if missing
        cols = await conn.execute(text("PRAGMA table_info(goal_deposits)"))
        dep_cols = {row[1] for row in cols.fetchall()}
        if "user_id" not in dep_cols:
            await conn.execute(
                text(
                    "ALTER TABLE goal_deposits ADD COLUMN user_id INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_goal_deposits_user_id "
                    "ON goal_deposits(user_id)"
                )
            )


# Создаёт таблицы в БД и применяет миграции (вызывается при старте бота)
async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
