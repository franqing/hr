"""adapter：cookie 解析三格式、卡片抽取、风控码、浏览器路径探测、邀请未校准保护。

绝不实例化 LiepinSession（会启动 playwright 浏览器）。通过 SimpleNamespace 提供
方法所需的最小 self（仅 codes）来调用无副作用的方法。
"""
from types import SimpleNamespace

import pytest

from backend.liepin import adapter


def _fake(codes=None):
    fake = SimpleNamespace(codes=codes or adapter._CODES)
    fake._pick = adapter.LiepinSession._pick      # _extract_cards 内部用到 staticmethod
    return fake


# ---------- cookie 解析 ----------
def test_parse_cookie_header():
    out = adapter.parse_cookie_string("a=1; b=2; malformed", "header")
    assert [(c["name"], c["value"]) for c in out] == [("a", "1"), ("b", "2")]
    for c in out:
        assert c["domain"] == ".liepin.com"
        assert c["path"] == "/"


def test_parse_cookie_pairs():
    out = adapter.parse_cookie_string("a=1\nb=2\n\n", "pairs")
    assert [(c["name"], c["value"]) for c in out] == [("a", "1"), ("b", "2")]


def test_parse_cookie_json():
    raw = '[{"name":"a","value":"1","domain":"example.com","httpOnly":true},' \
          '{"name":"b","value":"2"}]'
    out = adapter.parse_cookie_string(raw, "json")
    assert out[0]["domain"] == "example.com"
    assert out[0]["httpOnly"] is True
    assert out[1]["domain"] == ".liepin.com"     # 缺省域兜底
    assert out[1]["secure"] is True


def test_parse_cookie_empty_raises():
    with pytest.raises(ValueError):
        adapter.parse_cookie_string("", "header")


def test_parse_cookie_no_pairs_raises():
    with pytest.raises(ValueError):
        adapter.parse_cookie_string("just text without equals", "header")


def test_parse_cookie_bad_json_raises():
    with pytest.raises(ValueError):
        adapter.parse_cookie_string("not json", "json")


def test_parse_cookie_json_not_list_raises():
    with pytest.raises(ValueError):
        adapter.parse_cookie_string('{"name":"a"}', "json")


def test_parse_cookie_json_no_valid_cookie_raises():
    with pytest.raises(ValueError):
        adapter.parse_cookie_string('[{"foo":"bar"}]', "json")


# ---------- 卡片抽取（_extract_cards） ----------
def test_extract_cards_normalize():
    data = {"data": {"data": [{
        "resumeId": "R1", "userName": "张三", "age": 40, "resumeUrl": "/cv/abc",
        "desiredTitle": "研发总监", "currentCompany": "三安光电",
    }], "total": 1}}
    cards, total = adapter.LiepinSession._extract_cards(_fake(), data)
    assert total == 1
    c = cards[0]
    assert c["resume_id"] == "R1"
    assert c["age"] == 40
    assert c["resume_url"].startswith("https://lpt.liepin.com")   # 相对路径补全
    assert c["current_company"] == "三安光电"


def test_extract_cards_age_tolerant():
    data = {"data": {"data": [{"resumeId": "R2", "age": "38", "userName": "李四"}], "total": 1}}
    cards, _ = adapter.LiepinSession._extract_cards(_fake(), data)
    assert cards[0]["age"] == 38
    data = {"data": {"data": [{"resumeId": "R3", "age": "未知", "userName": "王五"}], "total": 1}}
    cards, _ = adapter.LiepinSession._extract_cards(_fake(), data)
    assert cards[0]["age"] is None


def test_extract_cards_resume_id_fallback_url():
    data = {"data": {"data": [{"resumeUrl": "https://lpt.liepin.com/cv/xyz", "userName": "赵六"}],
                     "total": 1}}
    cards, _ = adapter.LiepinSession._extract_cards(_fake(), data)
    assert cards[0]["resume_id"] == "https://lpt.liepin.com/cv/xyz"


# ---------- 风控 / 登录失效码 ----------
def test_check_bff_risk_ok_code():
    adapter.LiepinSession._check_bff_risk(_fake(), {"code": "0"})   # 不抛


def test_check_bff_risk_risk_code():
    with pytest.raises(adapter.LiepinRiskError):
        adapter.LiepinSession._check_bff_risk(_fake(), {"code": "4010000"})


def test_check_bff_risk_401_login():
    with pytest.raises(adapter.LiepinLoginError):
        adapter.LiepinSession._check_bff_risk(_fake(), {"httpStatus": 401})


# ---------- 邀请未校准保护 ----------
def test_send_invite_placeholder_raises():
    """端点未校准（PLACEHOLDER）时明确报错，不静默发错请求。"""
    with pytest.raises(adapter.LiepinRiskError, match="尚未校准"):
        adapter.LiepinSession.send_invite(_fake(), "R1", "J1", "你好")


# ---------- WSL → Windows 路径 ----------
def test_wsl_to_windows():
    assert adapter._wsl_to_windows("/mnt/c/Users/foo") == "C:\\Users\\foo"
    assert adapter._wsl_to_windows("/mnt/d/x/y") == "D:\\x\\y"


def test_wsl_to_windows_non_mnt_replaces_slashes():
    assert adapter._wsl_to_windows("/home/user") == "\\home\\user"


# ---------- 浏览器自检（_resolve_browser） ----------
def test_resolve_browser_explicit_executable(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("")
    assert adapter._resolve_browser("chrome", str(exe)) == {"executable_path": str(exe)}


def test_resolve_browser_missing_executable_raises(tmp_path):
    with pytest.raises(adapter.LiepinLoginError):
        adapter._resolve_browser("chrome", str(tmp_path / "nope"))


def test_resolve_browser_channel_none_fallback():
    # 未显式指定、通道为 chromium → 兜底 bundled chromium（{} 让 launch 自己抛提示）
    assert adapter._resolve_browser(None) == {}
    assert adapter._resolve_browser("chromium") == {}


def test_resolve_browser_windows_chrome_if_present():
    """channel=chrome 在 Linux 上探测 Windows 浏览器；结果要么空、要么是候选之一。"""
    r = adapter._resolve_browser("chrome")
    if r:
        assert r["executable_path"] in adapter._WINDOWS_BROWSERS
