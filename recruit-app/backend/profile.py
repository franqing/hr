"""人才画像：schema 常量、默认种子、校验与 985/211 词库。

画像以中文键存储（与《华联·技术1号位 V2》一致），五维权重和为 100。
- 硬性门槛：搜索计划与 hard_gate 用
- 五维权重 + 加分项 + 附加分规则：rule_score 用
"""
from __future__ import annotations

import json
from pathlib import Path

# 五维评估模型（沿用公司《面试评估表》框架）。键为中文（UI/画像一致），值为权重 0-100。
DIMENSIONS = [
    ("专业能力", "技术深度：功率/光耦器件技术栈，芯片设计+流片真实经验"),
    ("技术领导力", "团队搭建：研发团队（设计+封测协同）搭建、人才梯队、跨产品线拉通"),
    ("经营思维", "研发 ROI / 项目算账、风险预判（保供断点）、结果导向"),
    ("资源整合", "对接设计厂+流片厂建自主供应链、与销售/客户（BMS·OBC）技术对话"),
    ("岗位契合度", "行业/文化/价值观匹配、抗压、执行力"),
]
DEFAULT_WEIGHTS = {"专业能力": 30, "技术领导力": 25, "经营思维": 20, "资源整合": 15, "岗位契合度": 10}


def _load_universities() -> dict:
    path = Path(__file__).resolve().parent / "universities.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"985": [], "211": []}


_UNIVERSITIES = _load_universities()
_SET_985 = set(_UNIVERSITIES.get("985", []))
_SET_211 = set(_UNIVERSITIES.get("211", []))
_SET_211.update(_SET_985)  # 985 必然是 211


def is_top_school(school: str | None) -> bool:
    """学校是否 985/211。仅作辅助参考，硬性门槛按画像而定。"""
    if not school:
        return False
    s = str(school).strip()
    return s in _SET_985 or s in _SET_211


# ---------- 默认种子画像 ----------
# 依据《华联半导体·技术1号位（研发负责人）人才画像（V2）》提炼。
def default_seed_profile() -> dict:
    return {
        "岗位名称": "技术1号位（研发负责人）",
        "岗位名称变体": ["研发总监", "研发负责人", "技术总监", "技术负责人",
                      "功率器件研发", "封装研发总监"],
        "硬性门槛": {
            "目标公司": ["瞻芯电子", "三安光电", "芯联集成", "华润微", "积塔半导体", "芯粤能"],
            "年限": "≥5 年（在目标公司之一）",
            "必备背景": "半导体芯片从业（设计/流片/器件）真实背景；纯封测管理、纯软件、纯销售 → 否",
        },
        "加分项": [
            "光耦封装厂经验（光耦/功率器件封测厂）",
            "光耦下游客户经验：BMS/OBC 厂家（比亚迪、CATL、国轩、吉利、汇川、英飞源）",
        ],
        "年龄范围": [35, 45],
        "学历要求": "985/211 工科本科及以上；微电子/半导体/电力电子/电子信息/自动化优先",
        "管理要求": "≥5 年技术管理，带过 ≥30 人研发/器件团队",
        "地域": "厦门（可常驻）",
        "五维权重": dict(DEFAULT_WEIGHTS),
        "技术关键词": ["功率半导体", "碳化硅 SiC", "IGBT 器件", "光耦 封测",
                    "芯片设计 流片", "车规 AEC-Q101"],
        "附加分规则": {"上限": 10, "说明": "工科思维、逻辑性强、有车规（AEC-Q101）量产经验"},
    }


# 匹配时需要的关键词表（hard_gate 用，可被画像字段覆盖）
CHIP_BACKGROUND_WORDS = ["设计", "流片", "tape-out", "器件", "芯片", "晶圆", "SiC", "碳化硅",
                         "IGBT", "功率器件", "半导体", "光耦", "封测", "MOSFET", "功率开关"]
TARGET_COMPANY_HINT = ["负责", "主导", "项目", "团队", "带领", "管理", "负责人"]


def validate_profile(p: dict) -> list[str]:
    """轻量校验，返回问题列表（空 = 通过）。不强制完整性，允许缺省字段。"""
    problems: list[str] = []
    if not isinstance(p, dict):
        return ["画像必须是 JSON 对象"]
    gates = p.get("硬性门槛") or {}
    if isinstance(gates, dict) and not gates.get("目标公司") and not gates.get("年限"):
        problems.append("硬性门槛至少包含目标公司或年限之一")
    w = p.get("五维权重") or {}
    if w:
        for dim, _ in DIMENSIONS:
            if dim not in w:
                problems.append(f"缺少五维权重「{dim}」")
        if sum(int(v or 0) for v in w.values()) != 100:
            problems.append(f"五维权重和应为 100（当前 {sum(int(v or 0) for v in w.values())}）")
    return problems
