"""cookie_import 原生分支（spec §4.5）dispatch 纯测：不跑真实轮询/不连浏览器。"""
from __future__ import annotations

import os

from backend import cookie_import as ci
from backend.liepin import login_browser as lb


def test_native_flag_is_os_gate():
    # §4.4 机械判定；真实平台差异由 Task 8 Windows 清单覆盖
    assert ci._native() is (os.name == "nt")


def test_watch_paths_native_uses_login_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setattr(ci, "_native", lambda: True)
    assert ci._watch_paths() == [
        tmp_path / "d" / "login-profile" / "Default" / "Network" / "Cookies",
        tmp_path / "d" / "login-profile" / "Default" / "Cookies",
    ]


def test_export_header_native_dispatches_to_python_cdp(monkeypatch):
    monkeypatch.setattr(ci, "_native", lambda: True)
    monkeypatch.setattr(ci, "_ensure_ready", lambda: None)
    calls = {}

    def fake_export():
        calls["hit"] = True
        return (True, "lt_auth=abc", "ok")

    monkeypatch.setattr(lb, "export_login_cookies", fake_export)
    ok, header, info = ci._export_header()
    assert ok and header == "lt_auth=abc" and calls.get("hit")
