"""存储层：SQLAlchemy 2.0 引擎 / Session / Base。

依据：03_BUILD_PLAN §3 M0、架构 §14.1（`DATABASE_URL`）。
M0 只需提供可用的引擎与 Session 工厂；建表清单见 `store/schema.py`。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

__all__ = ["Base", "make_engine", "get_engine", "get_sessionmaker", "session_scope", "init_db"]


class Base(DeclarativeBase):
    """全部 ORM 表的声明基类。"""


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """按 `DATABASE_URL` 创建引擎；SQLite 自动补目录、打开外键与内存库支持。"""
    url = url or get_settings().database_url
    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in url:
            # 内存库需共享同一连接，否则每次 Session 都是新库。
            kwargs["poolclass"] = StaticPool
        else:
            # sqlite:///./data/buddy.db → 确保父目录存在
            target = url.split("///", 1)[-1]
            if target and target != ":memory:":
                Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 驱动回调
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """进程内单例引擎（配置来自 `get_settings()`）。"""
    return make_engine()


def get_sessionmaker(bind: Engine | None = None) -> sessionmaker[Session]:
    """返回 Session 工厂。"""
    return sessionmaker(bind=bind or get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope(bind: Engine | None = None) -> Iterator[Session]:
    """事务性 Session 上下文：正常提交、异常回滚、最终关闭。"""
    session = get_sessionmaker(bind)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(bind: Engine | None = None) -> None:
    """按 ORM 元数据建表（15 张）。生产路径用 Alembic 迁移，此处供测试与本地引导。"""
    from app.store import schema  # noqa: F401 —— 触发 schema 注册到 Base.metadata

    Base.metadata.create_all(bind=bind or get_engine())
