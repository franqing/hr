"""matcher：quick_gate / hard_gate（含简历级一票否决）/ rule_score 边界。"""
from backend import matcher
from backend.profile import default_seed_profile


def card(**over):
    base = {
        "name": "测试", "desired_title": "研发总监", "current_company": "三安光电",
        "current_city": "厦门", "school": "厦门大学", "desired_salary": "60-80K",
        "active_status": "在职", "age": 40, "work_years": "12年", "edu": "硕士",
    }
    base.update(over)
    return base


# ---------- quick_gate ----------
def test_quick_gate_target_company_hits():
    ok, reason = matcher.quick_gate(card())
    assert ok is True
    assert "命中目标公司" in reason


def test_quick_gate_title_word_hits():
    ok, reason = matcher.quick_gate(card(current_company="某科技公司", desired_title="研发负责人"))
    assert ok is True
    assert "期望职位命中岗位词" in reason


def test_quick_gate_accumulates_reasons():
    ok, reason = matcher.quick_gate(card(
        age=28, work_years="3年", edu="本科", school="某职业技术学院",
        current_company="某互联网公司", desired_title="软件工程师"))
    assert ok is False
    for frag in ("年龄 28 不在 35-45", "年限 3年 不足 5 年",
                 "学校非 985/211", "未命中目标公司/岗位词"):
        assert frag in reason


def test_quick_gate_age_boundary_ok():
    ok, _ = matcher.quick_gate(card(age=35))
    assert ok is True
    ok, _ = matcher.quick_gate(card(age=45))
    assert ok is True


# ---------- hard_gate（无简历：卡片级宽松降级） ----------
def test_hard_gate_no_resume_non_target_defer():
    c = card(current_company="某科技公司")
    ok, reason = matcher.hard_gate(c, None)
    assert ok is True
    assert "需简历详情复核" in reason


def test_hard_gate_no_resume_target_pass():
    ok, reason = matcher.hard_gate(card(), None)
    assert ok is True
    assert "命中目标公司" in reason


def test_hard_gate_no_resume_target_too_short():
    ok, reason = matcher.hard_gate(card(work_years="3年"), None)
    assert ok is False
    assert "不足 5 年" in reason


# ---------- hard_gate（有简历：严格一票否决） ----------
def _resume(text):
    return {"text": text}


def test_hard_gate_resume_no_chip_background():
    ok, reason = matcher.hard_gate(
        card(), _resume("负责公司财务与行政事务，长期带领团队，管理经验丰富。"))
    assert ok is False
    assert "未见芯片/半导体从业痕迹" in reason


def test_hard_gate_resume_non_target_veto():
    ok, reason = matcher.hard_gate(
        card(current_company="某互联网公司"),
        _resume("负责碳化硅功率器件研发，流片验证，带团队。"))
    assert ok is False
    assert "现公司未命中目标公司" in reason


def test_hard_gate_resume_target_too_short_veto():
    ok, reason = matcher.hard_gate(
        card(work_years="3年"),
        _resume("负责碳化硅功率器件研发，流片验证，主导项目。"))
    assert ok is False
    assert "不足 5 年" in reason


def test_hard_gate_resume_no_lead_words_veto():
    # 有芯片背景、命中目标公司、年限够，但未见项目主导/带队痕迹 → 一票否决
    ok, reason = matcher.hard_gate(
        card(desired_title="器件工程师"),
        _resume("在三安光电从事碳化硅器件设计、流片与封测工作。"))
    assert ok is False
    assert "未见项目主导/带队痕迹" in reason


def test_hard_gate_resume_full_pass():
    ok, reason = matcher.hard_gate(
        card(),
        _resume("负责三安光电碳化硅功率器件研发团队，主导 1200V 产品流片，带 40 人梯队。"))
    assert ok is True
    assert "命中目标公司" in reason


# ---------- rule_score ----------
def test_rule_score_shape():
    r = matcher.rule_score(None, card())
    assert set(r["dims"]) == {"专业能力", "技术领导力", "经营思维", "资源整合", "岗位契合度"}
    assert 0 <= r["total"] <= 100
    assert r["bonus"] >= 0
    assert r["explanation"]["附加分"].startswith("+")


def test_rule_score_target_company_boosts():
    strong = matcher.rule_score(None, card(current_company="三安光电"))
    weak = matcher.rule_score(None, card(current_company="某贸易公司"))
    assert strong["total"] > weak["total"]


def test_rule_score_resume_text_adds_signal():
    no_resume = matcher.rule_score(None, card())
    with_resume = matcher.rule_score(
        None, card(),
        _resume("芯片 设计 流片 碳化硅 SiC IGBT 光耦 封测 MOSFET 团队 管理 带领 主导 AEC-Q101 车规 量产 厦门 常驻"))
    assert with_resume["total"] > no_resume["total"]


def test_rule_score_total_capped_at_100():
    r = matcher.rule_score(None, card(), _resume(
        "芯片 设计 流片 碳化硅 SiC IGBT 功率器件 光耦 封测 MOSFET 器件 晶圆 半导体 功率开关 "
        "团队 管理 带领 搭建 梯队 负责人 总监 "
        "ROI 算账 成本 风险 预判 结果导向 保供 断点 "
        "供应链 建链 第二货源 客户 BMS OBC 对接 外协 厂商 "
        "厦门 常驻 稳定 抗压 执行力 价值观 "
        "AEC-Q101 车规 量产 SiC 1700V 主导 负责"))
    assert r["total"] <= 100
    assert r["bonus"] == 10          # 附加分封顶（上限 10）


def test_rule_score_custom_weights():
    prof = default_seed_profile()
    prof["五维权重"] = {"专业能力": 40, "技术领导力": 20, "经营思维": 15,
                     "资源整合": 15, "岗位契合度": 10}
    r = matcher.rule_score(prof, card())
    assert 0 <= r["total"] <= 100
