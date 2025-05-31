from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, DECIMAL, ForeignKey, BigInteger, DateTime

engine = create_async_engine(url="sqlite+aiosqlite:///db.sqlite3")
async_session = async_sessionmaker(engine)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True, default="no_phone")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    records = relationship("Record", back_populates="user")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(1))  # "+" или "-"
    amount: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))
    category: Mapped[str] = mapped_column(String, default="не указано")  # новое
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="records")


async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
