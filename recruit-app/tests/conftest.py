"""测试夹具：把 db._DB_PATH 指向临时文件，保证测试不碰真实 data/app.db。

不触网、不启动浏览器，全部为本地确定性断言。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from backend import db
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    return db
