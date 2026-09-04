"""登录浏览器模块（spec §4.5）纯函数与路径断言：不拉起真实浏览器/不走网络。"""
from __future__ import annotations

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
