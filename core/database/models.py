"""
SQLAlchemy модели: User и Record. Настройка подключения к БД.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import String, DECIMAL, ForeignKey, BigInteger, DateTime, Index
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import DATABASE_URL


# ==================== Подключение к БД ====================

engine = create_async_engine(url=DATABASE_URL)
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
    user = relationship("User", back_populates="records")

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

# Создаёт таблицы в БД (вызывается при старте бота)
async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
