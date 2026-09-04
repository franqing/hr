"""测试夹具：把数据目录整体指向 pytest 临时目录，保证任何用例都不碰真实 data/。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"
