"""M0 测试公共夹具：内存 SQLite 引擎与会话。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.store.db import init_db, make_engine, session_scope


@pytest.fixture()
def engine() -> Iterator[Engine]:
    """独立内存库（每个用例一份），建齐 15 张表。"""
    eng = make_engine("sqlite+pysqlite:///:memory:")
    init_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    with session_scope(engine) as sess:
        yield sess
