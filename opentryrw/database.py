from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .settings import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def close_database() -> None:
    await engine.dispose()
