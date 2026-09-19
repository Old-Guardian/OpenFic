# -*- coding: utf-8 -*-
"""
数据库连接与 session 管理。
"""

from pathlib import Path
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from loguru import logger
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.pinyin import to_pinyin, to_pinyin_initials
from app.settings import settings

_engine = None
_async_session_factory = None
_VACUUM_MIN_FREE_BYTES = 64 * 1024 * 1024
ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite 连接建立时设置 WAL 模式和并发优化。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
    dbapi_connection.create_function("pinyin_full", 1, to_pinyin, deterministic=True)
    dbapi_connection.create_function("pinyin_initials", 1, to_pinyin_initials, deterministic=True)


event.listen(Engine, "connect", _set_sqlite_pragma)


def _disable_implicit_sqlite_transactions(dbapi_connection, connection_record):
    """关闭 pysqlite 的隐式事务管理。

    pysqlite 默认只在 DML 前隐式发送 BEGIN，纯 SELECT 不进入真实读事务，
    因而拿不到 WAL 一致读快照。交由 :func:`_begin_sqlite_transaction` 显式开启。
    """
    dbapi_connection.isolation_level = None


def _begin_sqlite_transaction(conn):
    """事务开始时显式发送 BEGIN，使只读查询也持有 WAL 一致读快照。"""
    conn.exec_driver_sql("BEGIN")


def enable_sqlite_transactions(engine) -> None:
    """为指定 SQLite 引擎启用显式 BEGIN，使只读查询获得一致读快照。

    需与 ``isolation_level = None`` 成对使用：关闭驱动的隐式事务后必须由
    SQLAlchemy 显式发送 BEGIN，否则写操作会退化为自动提交。
    """
    event.listen(engine.sync_engine, "connect", _disable_implicit_sqlite_transactions)
    event.listen(engine.sync_engine, "begin", _begin_sqlite_transaction)


def _get_engine():
    """获取或创建数据库引擎。"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            future=True,
            connect_args={
                "check_same_thread": False,
            },
            pool_pre_ping=True,
        )
        enable_sqlite_transactions(_engine)
    return _engine


def _get_session_factory():
    """获取或创建 session 工厂。"""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


def _upgrade_db_to_head() -> None:
    """使用 Alembic 将数据库升级到最新版本。"""
    config = Config(str(ALEMBIC_INI_PATH))
    command.upgrade(config, "head")

async def init_db() -> None:
    """初始化数据库。"""
    logger.info("Database initialization or migration started. Please wait...")
    _upgrade_db_to_head()
    logger.info("Database initialization or migration completed.")


async def close_db() -> None:
    """
    关闭数据库连接。

    应在应用关闭时调用。
    """
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None


async def vacuum_database_if_needed(
    min_free_bytes: int = _VACUUM_MIN_FREE_BYTES,
) -> bool:
    """Reclaim main database file space when deletion leaves enough free pages."""
    db_path = settings.database_url.removeprefix("sqlite+aiosqlite:///")
    if not Path(db_path).exists():
        return False

    import aiosqlite

    conn = await aiosqlite.connect(db_path)
    try:
        page_size_cursor = await conn.execute("PRAGMA page_size")
        try:
            page_size_row = await page_size_cursor.fetchone()
        finally:
            await page_size_cursor.close()
        freelist_cursor = await conn.execute("PRAGMA freelist_count")
        try:
            freelist_row = await freelist_cursor.fetchone()
        finally:
            await freelist_cursor.close()
        page_size = int(page_size_row[0]) if page_size_row else 0
        free_pages = int(freelist_row[0]) if freelist_row else 0
        if page_size * free_pages < min_free_bytes:
            return False
        await conn.execute("VACUUM")
        return True
    finally:
        await conn.close()


async def create_session() -> AsyncSession:
    """创建独立的数据库 session。"""
    session_factory = _get_session_factory()
    return session_factory()


@asynccontextmanager
async def read_snapshot(session: AsyncSession) -> AsyncIterator[None]:
    """让一段只读查询共享同一数据库读快照。

    未处于事务中时开启只读事务并在退出时提交，及时释放快照；已处于事务中
    则直接复用，由调用方决定提交时机。
    """
    if session.in_transaction():
        yield
        return
    async with session.begin():
        yield


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库 session 的依赖注入函数。

    Yields:
        AsyncSession: 异步数据库 session。
    """
    session_factory = _get_session_factory()
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
