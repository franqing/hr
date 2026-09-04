"""search_planner：地域解析、预设生成、查询上限。"""
from backend import search_planner as sp
from backend.profile import default_seed_profile


# ---------- 地域 → 城市码 ----------
def test_parse_region_single():
    assert sp._parse_region("厦门") == [750]


def test_parse_region_multi():
    assert sp._parse_region("上海 / 深圳") == [680, 710]
    assert sp._parse_region("深圳、东莞") == [710, 709]


def test_parse_region_willingness_is_none():
    # 「厦门（可常驻）」是意愿描述，不是硬性工作地要求 → 跳过筛选
    assert sp._parse_region("厦门（可常驻）") is None
    assert sp._parse_region("可常驻厦门") is None
    assert sp._parse_region("愿意到北京") is None


def test_parse_region_unresolvable():
    assert sp._parse_region("不限") is None
    assert sp._parse_region("全国") is None
    assert sp._parse_region("") is None
    assert sp._parse_region(None) is None
    assert sp._parse_region("海外") is None


def test_parse_region_list():
    assert sp._parse_region(["厦门"]) == [750]


# ---------- 预设生成 ----------
def test_full_preset_has_all():
    qs = sp.build_queries(default_seed_profile(), preset="full", max_queries=100)
    labels = {q.label for q in qs}
    assert {"company", "title", "tech"} <= labels
    assert all(q.keyword for q in qs)


def test_companies_preset_only_company():
    qs = sp.build_queries(default_seed_profile(), preset="companies", max_queries=100)
    assert qs and all(q.label == "company" for q in qs)


def test_titles_preset_only_title():
    qs = sp.build_queries(default_seed_profile(), preset="titles", max_queries=100)
    assert qs and all(q.label == "title" for q in qs)


def test_tech_preset_only_tech():
    qs = sp.build_queries(default_seed_profile(), preset="tech", max_queries=100)
    assert qs and all(q.label == "tech" for q in qs)


def test_max_queries_truncated():
    full = sp.build_queries(default_seed_profile(), preset="full", max_queries=100)
    capped = sp.build_queries(default_seed_profile(), preset="full", max_queries=3)
    assert len(full) > 3
    assert len(capped) == 3
    assert capped == full[:3]


def test_seed_region_not_filtered():
    """默认种子画像地域='厦门（可常驻）' → 不应生成 dqs 筛选。"""
    f = sp._derive_filters(default_seed_profile())
    assert "dqs" not in f and "wantDqs" not in f


def test_hard_region_generates_dqs():
    prof = default_seed_profile()
    prof["地域"] = "厦门"
    f = sp._derive_filters(prof)
    assert f.get("dqs") == [750]
    assert f.get("wantDqs") == [750]
