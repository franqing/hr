"""登录浏览器模块（spec §4.5）纯函数与路径断言：不拉起真实浏览器/不走网络。"""
from __future__ import annotations

import os
import socket

import pytest

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


# ---- _resolve_login_exe：channel → 本机可执行文件（msedge 默认通道回归）。----
# 只测显式 executable 与 chrome/msedge 本机探测/报错路径；绝不触发 chromium 分支
# （会真拉起 playwright）。env 探测须先清掉三个候选键（WSL 上可能未设/指向 Windows
# 路径——Path("") 会退化成相对当前目录，误命中就假绿了）。
def _fake_local_browser(monkeypatch, tmp_path, env_key, rel):
    """把三个标准安装 env 键先指到空目录，再把 env_key 指到 tmp_path 并造出
    rel 相对路径的假浏览器文件 → 返回其绝对路径（该键后续探测必命中它）。"""
    for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(k, str(tmp_path / "empty"))
    exe = tmp_path / rel
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setenv(env_key, str(tmp_path))
    return exe


def test_resolve_msedge_finds_program_files_x86(monkeypatch, tmp_path):
    # Edge 多为 Windows 自带、装在 Program Files (x86) → 候选第一项
    exe = _fake_local_browser(monkeypatch, tmp_path, "PROGRAMFILES(X86)",
                              "Microsoft/Edge/Application/msedge.exe")
    assert lb._resolve_login_exe({"browser_channel": "msedge"}) == str(exe)


def test_resolve_msedge_missing_raises_guidance(monkeypatch, tmp_path):
    for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(k, str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="本机 Edge"):
        lb._resolve_login_exe({"browser_channel": "msedge"})


def test_resolve_chrome_still_probes_local_install(monkeypatch, tmp_path):
    # chrome 候选顺序未破坏（PROGRAMFILES 第一）——msedge 重构不得影响既有通道
    exe = _fake_local_browser(monkeypatch, tmp_path, "PROGRAMFILES",
                              "Google/Chrome/Application/chrome.exe")
    assert lb._resolve_login_exe({"browser_channel": "chrome"}) == str(exe)


def test_resolve_unsupported_channel_raises_with_supported_list():
    with pytest.raises(RuntimeError,
                       match=r"不支持的浏览器通道: safari.*chromium/chrome/msedge"):
        lb._resolve_login_exe({"browser_channel": "safari"})


def test_resolve_explicit_executable_takes_precedence(monkeypatch, tmp_path):
    # 显式 executable > channel 探测：即使 msedge 探测本会命中，仍以显式路径为准
    exe = tmp_path / "my-browser.exe"
    exe.write_text("stub", encoding="utf-8")
    hit = _fake_local_browser(monkeypatch, tmp_path, "PROGRAMFILES(X86)",
                              "Microsoft/Edge/Application/msedge.exe")
    assert hit != exe
    assert lb._resolve_login_exe({"browser_channel": "msedge",
                                  "browser_executable": str(exe)}) == str(exe)


def test_resolve_missing_explicit_executable_raises(tmp_path):
    missing = tmp_path / "nope.exe"
    with pytest.raises(RuntimeError, match="浏览器可执行文件不存在"):
        lb._resolve_login_exe({"browser_channel": "msedge",
                               "browser_executable": str(missing)})
