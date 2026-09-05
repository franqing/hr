"""登录浏览器模块（spec §4.5）纯函数与路径断言：不拉起真实浏览器/不走网络。"""
from __future__ import annotations

import os
import socket

from backend.liepin import login_browser as lb


def _ck(name, value="v", domain=".liepin.com", expires=-1.0):
    return {"name": name, "value": value, "domain": domain, "path": "/",
            "expires": expires, "httpOnly": True, "secure": True, "sameSite": "Lax"}


def test_login_url_and_ports():
    assert lb._LOGIN_URL == "https://passport.liepin.com/login"
    assert lb._PORT_CANDIDATES == (53471, 9222)   # 启动优先 53471；与 mjs 的 PORTS 同集合


def test_build_header_keeps_session_and_drops_expired():
    now = 1_700_000_000_000
    cks = [
        _ck("lt_auth", expires=-1.0),                        # 会话 cookie → 保留
        _ck("smidV2", expires=None),                          # 无 expires → 保留
        _ck("acw_tc", expires=1_700_000_000),                 # 已过期 → 丢弃
        _ck("XSRF-TOKEN", expires=(now // 1000) + 3600),      # 未来过期 → 保留
    ]
    header = lb.build_cookie_header(cks, now)
    assert "lt_auth=v" in header and "XSRF-TOKEN=v" in header
    assert "acw_tc" not in header


def test_build_header_filters_non_liepin_domains():
    cks = [_ck("sessionid", domain="example.com"),
           _ck("lt_auth", domain="passport.liepin.com")]
    header = lb.build_cookie_header(cks, 1_700_000_000_000)
    assert "lt_auth" in header and "sessionid" not in header


def test_build_header_sorted_by_domain_then_name():
    now = 1_700_000_000_000
    cks = [
        _ck("UniqueKey", domain="liepin.com"),
        _ck("lt_auth", domain=".liepin.com"),
        _ck("_e_ld_auth_", domain=".passport.liepin.com"),
    ]
    # ASCII 升序（键 f"{domain}|{name}"）：'.'(0x2E) < 'l' → 两个点前缀域先排；
    # 同以 '.' 开头时 '.liepin.com' < '.passport.liepin.com'。期望顺序 lt_auth →
    # _e_ld_auth_ → UniqueKey —— 已用 node 实测确认与 mjs localeCompare 输出一致。
    assert lb.build_cookie_header(cks, now) == "lt_auth=v; _e_ld_auth_=v; UniqueKey=v"


def test_profile_and_cookie_paths_under_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert lb.profile_dir() == tmp_path / "d" / "login-profile"
    assert lb.cookie_lib_paths() == [
        tmp_path / "d" / "login-profile" / "Default" / "Network" / "Cookies",
        tmp_path / "d" / "login-profile" / "Default" / "Cookies",
    ]


# ---- 模块态 _browser 读写（Task 8 Windows 直启捕获：缺 global 声明 →
# UnboundLocalError；以下三个入口曾全部必炸，含 lifespan 关停路径）----
def test_close_login_browser_no_instance_is_noop():
    # 从未开过登录浏览器 → 幂等安静返回（回归：close 内 _browser 读写必须同一全局）
    assert lb.close_login_browser() is None


def test_cdp_base_url_no_instance_returns_none():
    assert lb.cdp_base_url() is None


def test_open_login_browser_no_instance_reports_cleanly(monkeypatch):
    # 原生门放行（模拟 os.name=='nt'）但解析可执行文件即失败 → 应如实返回
    # {"ok": False, "error": "打开登录浏览器失败: ..."}，绝不允许 UnboundLocalError
    # 从 _browser 读取处裸抛（该异常在 try 之外，会直接 500）。
    monkeypatch.setattr(os, "name", "nt")

    def boom(cfg):
        raise RuntimeError("stub: 无可执行文件")

    monkeypatch.setattr(lb, "_resolve_login_exe", boom)
    r = lb.open_login_browser({})
    assert r["ok"] is False
    assert r["error"].startswith("打开登录浏览器失败: RuntimeError: stub")


# ---- 端口/CDP 探测（Task 8 用户机 CDP 启动超时根因回归）----
def test_cdp_alive_socket_probe():
    # httpx GET /json/version 探测两个坑：① 对非 HTTP 的纯 TCP 端口空等 1s 超时才
    # 判死；② 受 HTTP(S)_PROXY 环境变量劫持——对 127.0.0.1 也走代理 → 误判不在跑。
    # CDP 服务器 bind 即 accept：socket connect 成功即可视为可连。此测试对
    # 「已监听端口」断言 connect 探测语义（旧 httpx 实现在此返回 False → 红灯）。
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        assert lb._cdp_alive(base) is True
    finally:
        s.close()
    assert lb._cdp_alive(base) is False   # 关掉后 → 不在跑


def test_port_free_bind_probe():
    # 修复核心语义：端口可用 = 本机能 bind 上它。connect 探测对 Hyper-V/WSL2
    # 保留段（netsh excludedportrange）误判——保留端口无监听、connect 被拒，
    # 但 bind 同样被拒（浏览器 debug 端口因此起不来）。WSL 上无法模拟 Windows
    # 保留段，此处以真实监听占用锁「占用 → False、释放 → True」的行为契约。
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert lb._port_free(port) is False   # 已被监听 → 不可用
    finally:
        s.close()
    assert lb._port_free(port) is True        # 释放 → 可用


# ---- 端口/CDP 探测（Task 8 用户机 CDP 启动超时根因回归）----
def test_cdp_alive_socket_probe():
    # httpx GET /json/version 探测两个坑：① 对非 HTTP 的纯 TCP 端口空等 1s 超时才
    # 判死；② 受 HTTP(S)_PROXY 环境变量劫持——对 127.0.0.1 也走代理 → 误判不在跑。
    # CDP 服务器 bind 即 accept：socket connect 成功即可视为可连。此测试对
    # 「已监听端口」断言 connect 探测语义（旧 httpx 实现在此返回 False → 红灯）。
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        assert lb._cdp_alive(base) is True
    finally:
        s.close()
    assert lb._cdp_alive(base) is False   # 关掉后 → 不在跑


def test_port_free_bind_probe():
    # 修复核心语义：端口可用 = 本机能 bind 上它。connect 探测对 Hyper-V/WSL2
    # 保留段（netsh excludedportrange）误判——保留端口无监听、connect 被拒，
    # 但 bind 同样被拒（浏览器 debug 端口因此起不来）。WSL 上无法模拟 Windows
    # 保留段，此处以真实监听占用锁「占用 → False、释放 → True」的行为契约。
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert lb._port_free(port) is False   # 已被监听 → 不可用
    finally:
        s.close()
    assert lb._port_free(port) is True        # 释放 → 可用
