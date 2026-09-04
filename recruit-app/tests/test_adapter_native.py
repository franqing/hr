"""adapter 原生门控（spec §4.4）：_win_native 恒等于 os.name == 'nt' 的机械判定。
真实浏览器差异由 Task 8 Windows 手工清单覆盖（本文件只保 WSL 回归确定性）。"""
from __future__ import annotations

import os

from backend.liepin import adapter as a


def test_win_native_is_os_name_gate():
    assert a._win_native() is (os.name == "nt")
