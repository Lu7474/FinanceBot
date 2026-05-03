"""
SQLAlchemy модели: User, Account и Record. Настройка подключения к БД.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import DATABASE_URL, TIMEZONE

# ==================== Подключение к БД ====================

engine = create_async_engine(url=DATABASE_URL)
async_session = async_sessionmaker(engine)


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
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)  # Telegram ID
    name: Mapped[str] = mapped_column(String, nullable=True)       # Имя пользователя
    phone: Mapped[str] = mapped_column(String, nullable=True, default=None)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    records = relationship("Record", back_populates="user")        # Связь с записями
    accounts = relationship("Account", back_populates="user", cascade="all, delete-orphan")


# Счёт пользователя (Наличные, Карта и т.д.)
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    balance_offset: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=Decimal("0"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)

    user = relationship("User", back_populates="accounts")
    records = relationship("Record", back_populates="account")


# Запись дохода или расхода
class Record(Base):
    __tablename__ = "records"

    # Индексы для ускорения запросов по периодам и пользователям
    __table_args__ = (
        Index("ix_records_user_created", "user_id", "created_at"),  # Для выборки по периоду
        Index("ix_records_user_operation", "user_id", "operation"),  # Для отчётов по типу
        Index("ix_records_user_op_cat", "user_id", "operation", "category"),  # Для GROUP BY категориям
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # Индекс на FK
    operation: Mapped[str] = mapped_column(String(1))              # "+" (доход) или "-" (расход)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))        # Сумма
    category: Mapped[str] = mapped_column(String(50), default="не указано")  # Категория (макс 50 символов)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now, index=True)  # Индекс для сортировки
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


# ==================== Инициализация ====================

async def _migrate(conn) -> None:
    """Applies additive schema migrations safe for existing DBs."""
    # Add account_id to records if missing (SQLite supports ADD COLUMN)
    result = await conn.execute(text("PRAGMA table_info(records)"))
    columns = {row[1] for row in result.fetchall()}
    if "account_id" not in columns:
        await conn.execute(
            text("ALTER TABLE records ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
        )
    result = await conn.execute(text("PRAGMA table_info(accounts)"))
    acc_columns = {row[1] for row in result.fetchall()}
    if "balance_offset" not in acc_columns:
        await conn.execute(text("ALTER TABLE accounts ADD COLUMN balance_offset DECIMAL(10,2) NOT NULL DEFAULT 0"))
    result = await conn.execute(text("PRAGMA table_info(users)"))
    user_columns = {row[1] for row in result.fetchall()}
    if "is_banned" not in user_columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0"))


# Создаёт таблицы в БД и применяет миграции (вызывается при старте бота)
async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
