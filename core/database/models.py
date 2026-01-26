"""
SQLAlchemy модели: User и Record. Настройка подключения к БД.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import String, DECIMAL, ForeignKey, BigInteger, DateTime
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ==================== Подключение к БД ====================

engine = create_async_engine(url="sqlite+aiosqlite:///db.sqlite3")
async_session = async_sessionmaker(engine)


# Возвращает текущее время по Москве (для default в моделях)
def moscow_now():
    return datetime.now(ZoneInfo("Europe/Moscow"))


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    records = relationship("Record", back_populates="user")        # Связь с записями


# Запись дохода или расхода
class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))   # Владелец записи
    operation: Mapped[str] = mapped_column(String(1))              # "+" (доход) или "-" (расход)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))        # Сумма
    category: Mapped[str] = mapped_column(String, default="не указано")  # Категория
    created_at: Mapped[datetime] = mapped_column(DateTime, default=moscow_now)
    user = relationship("User", back_populates="records")


# ==================== Инициализация ====================

# Создаёт таблицы в БД (вызывается при старте бота)
async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
