"""候选人匹配打分（确定性规则，无需 LLM）。

- quick_gate：卡片级初筛（便宜，直接过不过都落库，不过的置灰）
- hard_gate ：简历级一票否决（芯片背景 / 目标公司年限 / 项目主导）
- rule_score：五维 0–20 分按权重汇总 + 附加分（无 LLM 也能跑）

输入约定：
- card 为 adapter 规范化的候选字段（猎聘卡片）
- resume 可选，为简历详情 dict（M4 起提供）；缺省时用 card 字段降级推断
"""
from __future__ import annotations

import re

from . import profile as P
from .profile import is_top_school


def _num(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# ---------- 工具 ----------
def _card_text(card: dict) -> str:
    """把卡片可读字段拼成文本，供关键词扫描。"""
    parts = [str(card.get(k) or "") for k in (
        "name", "desired_title", "current_company", "current_city", "school",
        "desired_salary", "active_status")]
    return " ".join(parts)


def _resume_text(resume: dict | None) -> str:
    if not resume:
        return ""
    if isinstance(resume, dict) and "text" in resume:
        return str(resume["text"] or "")
    return str(resume)


# ---------- quick_gate（卡片级） ----------
def quick_gate(card: dict, profile: dict | None = None) -> tuple[bool, str]:
    """便宜的卡片级初筛。不过仅置灰，不删除。返回 (通过, 原因)。"""
    prof = profile or P.default_seed_profile()
    reasons: list[str] = []

    # 年龄范围
    age_range = prof.get("年龄范围") or []
    if len(age_range) == 2:
        age = _num(card.get("age"))
        if age is not None and not (int(age_range[0]) <= age <= int(age_range[1])):
            reasons.append(f"年龄 {age} 不在 {age_range[0]}-{age_range[1]}")

    # 工作年限（字符串容错：'5-10年' / '10年以上' / '≥5 年'）
    years = str(card.get("work_years") or "")
    m = re.search(r"([0-9]+)", years)
    if m:
        y = int(m.group(1))
        m2 = re.search(r"([0-9]+)", str(prof.get("硬性门槛", {}).get("年限") or "5"))
        min_years = int(m2.group(1)) if m2 else 5
        if y < min_years:
            reasons.append(f"年限 {years} 不足 {min_years} 年")

    # 学校 985/211（或硕博）——仅参考，不否决
    school = str(card.get("school") or "")
    if school and not (is_top_school(school) or "硕士" in str(card.get("edu") or "")
                       or "博士" in str(card.get("edu") or "")):
        reasons.append("学校非 985/211（参考）")

    # 现公司 ∈ 目标公司，或期望职位命中岗位词 —— 命中直接通过
    target = prof.get("硬性门槛", {}).get("目标公司") or []
    cur_co = str(card.get("current_company") or "")
    if any(t in cur_co for t in target if t):
        return True, "现公司命中目标公司"

    title_words = prof.get("岗位名称变体") or [prof.get("岗位名称", "")]
    desired = str(card.get("desired_title") or "")
    if any(w and w in desired for w in title_words):
        return True, "期望职位命中岗位词"

    reasons.append("未命中目标公司/岗位词")
    return (not reasons), ";".join(reasons) if reasons else "通过"


# ---------- hard_gate（简历级，一票否决） ----------
def hard_gate(card: dict, resume: dict | None = None, profile: dict | None = None) -> tuple[bool, str]:
    """一票否决：芯片从业背景 + 目标公司年限 + 项目主导。

    简历详情（M4）缺失时用卡片字段**宽松降级**：卡片拿不到从业描述与完整履历，
    强校验只对"命中目标公司"放行，其余 defer 到简历详情复核，不轻易否决。
    """
    prof = profile or P.default_seed_profile()
    gates = prof.get("硬性门槛", {})
    target = gates.get("目标公司") or []
    resume_text = _resume_text(resume)

    cur_co = str(card.get("current_company") or "")
    years = str(card.get("work_years") or "")
    y = _num(re.search(r"([0-9]+)", years).group(1)) if re.search(r"([0-9]+)", years) else None
    in_target = any(t in cur_co for t in target if t)

    # ---------- 卡片级（无简历详情）：宽松降级 ----------
    if not resume_text:
        if not in_target:
            return True, "卡片级通过；现公司未命中目标公司，需简历详情复核（M4）"
        if y is not None and y < 5:
            return False, f"目标公司 {cur_co} 年限 {years} 不足 5 年"
        return True, f"卡片级通过：现公司命中目标公司 {cur_co}（年限{'' if y is None else years}）"

    # ---------- 简历级：严格一票否决 ----------
    text = _card_text(card) + "\n" + resume_text

    # 1) 芯片从业背景：必须出现芯片行业词
    chip_words = P.CHIP_BACKGROUND_WORDS
    hits = [w for w in chip_words if w.lower() in text.lower()]
    if not hits:
        return False, f"未见芯片/半导体从业痕迹（扫描词：{'/'.join(chip_words[:8])}）"

    # 2) 目标公司任职 ≥5 年且主导过项目（核心 sourcing 条件）
    if not in_target:
        return False, f"现公司未命中目标公司（{'/'.join(target) or '无'}），不满足建链 sourcing 条件"
    if y is not None and y < 5:
        return False, f"目标公司 {cur_co} 年限 {years} 不足 5 年"
    lead = [w for w in P.TARGET_COMPANY_HINT if w in text]
    if not lead:
        return False, "未见项目主导/带队痕迹（负责/主导/项目/带领）"
    return True, f"命中目标公司 {cur_co}（年限{'' if y is None else years}）且含项目主导词"


# ---------- rule_score（五维权重汇总，确定性） ----------
def rule_score(profile: dict | None, card: dict, resume: dict | None = None) -> dict:
    """五维 0–20 分按权重汇总。返回 {dims, total, bonus, explanation}。
    权重取画像，缺省用默认。total = min(100, 5*Σ(w·dim) + bonus)。"""
    prof = profile or P.default_seed_profile()
    weights = prof.get("五维权重", {}) or P.DEFAULT_WEIGHTS

    text = _card_text(card)
    if resume:
        text += "\n" + _resume_text(resume)
    tl = text.lower()

    # 各维 0-20 评分：命中关键词组合加分，封顶 20
    def clamp(x): return max(0, min(20, x))

    def count(*words):
        return sum(1 for w in words if w.lower() in tl)

    # 卡片级最强信号：现公司命中目标公司 / 期望职位命中岗位词
    cur_co = str(card.get("current_company") or "")
    desired = str(card.get("desired_title") or "")
    target = prof.get("硬性门槛", {}).get("目标公司") or []
    in_target = any(t in cur_co for t in target if t)
    title_words = prof.get("岗位名称变体") or [prof.get("岗位名称", "")]
    title_hit = any(w and w in desired for w in title_words)

    # 专业能力（技术深度）：目标公司≈真实芯片深度，直接加权
    prof_hits = count("芯片", "设计", "流片", "tape-out", "器件", "碳化硅", "sic", "igbt",
                      "功率器件", "光耦", "封测", "mosfet")
    prof_score = clamp(4 + 3 * prof_hits + (14 if in_target else 0))

    # 技术领导力（团队/管理）：岗位词命中≈技术管理岗
    lead_hits = count("团队", "管理", "带领", "负责人", "总监", "搭建", "梯队")
    lead_score = clamp(3 + 3 * lead_hits + (6 if title_hit else 0))

    # 经营思维（算账/风险/结果）
    ops_hits = count("ROI", "算账", "成本", "风险", "预判", "结果导向", "保供", "断点")
    ops_score = clamp(2 + 3 * ops_hits)

    # 资源整合（供应链/客户/外部对接）：目标公司自带供应链/客户网络
    res_hits = count("供应链", "建链", "第二货源", "客户", "BMS", "OBC", "对接", "外协", "厂商")
    res_score = clamp(2 + 3 * res_hits + (8 if in_target else 0))

    # 岗位契合度（行业/文化/稳定性）
    fit_hits = count("厦门", "常驻", "稳定", "抗压", "执行力", "价值观")
    fit_score = clamp(4 + 2 * fit_hits)

    dims = {"专业能力": prof_score, "技术领导力": lead_score,
            "经营思维": ops_score, "资源整合": res_score, "岗位契合度": fit_score}

    # 附加分（AEC-Q101 车规量产等），≤10
    bonus = 0
    if "aec-q101" in tl or ("车规" in tl and "量产" in tl):
        bonus += 5
    if "sic" in tl or "1700v" in tl:
        bonus += 3
    if "主导" in tl or "负责" in tl:
        bonus += 2
    bonus = min(bonus, int(prof.get("附加分规则", {}).get("上限", 10) or 10))

    total = min(100.0, round(5 * sum(int(weights.get(d, 0) or 0) * dims[d] for d in dims) / 100.0
                             + bonus, 1))
    # 说明：记录每维命中关键词（可审计）
    explanation = {
        "专业能力": f"命中 {prof_hits} 词（芯片/设计/流片/器件…）",
        "技术领导力": f"命中 {lead_hits} 词（团队/管理/带领…）",
        "经营思维": f"命中 {ops_hits} 词（ROI/风险/成本…）",
        "资源整合": f"命中 {res_hits} 词（供应链/客户/BMS/OBC…）",
        "岗位契合度": f"命中 {fit_hits} 词（厦门/稳定/抗压…）",
        "附加分": f"+{bonus}（AEC-Q101/车规量产/SiC 等）",
    }
    return {"dims": dims, "total": total, "bonus": bonus, "explanation": explanation}
