from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator

from app.core.config import settings

# Подключение к БД берём из настроек (env database_url), с приведением схемы к
# asyncpg. Фоллбэк — локальная БД на 5432 для обратной совместимости.
_raw_url = settings.database_url or "postgresql+asyncpg://postgres:postgres@localhost:5432/catalog_db"
if _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
DATABASE_URL = _raw_url

engine = create_async_engine(DATABASE_URL, echo=False)

async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()