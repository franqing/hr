"""画像 → 搜索关键词/筛选组。字段码见 liepin/codes.json（搜索请求体据此组）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import SearchQuery

# 城市码以 liepin/codes.json 为准（已对照 liepin-cli 校准；旧 int 码是 C 端码位，作废）
_CODES = json.loads((Path(__file__).resolve().parent / "liepin" / "codes.json")
                    .read_text(encoding="utf-8"))
CITY_CODE_MAP: dict[str, str] = _CODES["code_tables"]["city"]

# 兜底种子（首个默认画像未就绪或画像缺词时用）——对应《华联·技术1号位 V2》画像
DEFAULT_COMPANIES = ["瞻芯电子", "三安光电", "芯联集成", "华润微", "积塔半导体", "芯粤能"]
DEFAULT_TITLES = ["研发总监", "研发负责人", "技术总监", "技术负责人", "功率器件研发", "封装研发总监"]
DEFAULT_TECH = ["功率半导体", "碳化硅 SiC", "IGBT 器件", "光耦 封测", "芯片设计 流片", "车规 AEC-Q101"]

# 方案预设：full=公司+岗位+技术（上限内），title_only / tech_only 用于快速试跑
FILTER_PRESETS = {
    "full": {"company": True, "title": True, "tech": True},
    "companies": {"company": True, "title": False, "tech": False},
    "titles": {"company": False, "title": True, "tech": False},
    "tech": {"company": False, "title": False, "tech": True},
}

# 画像字段里可能出现的年限文本 → 猎聘 workyears 码
_WORKYEARS_MAP = [
    (r"10年以上", ["10年以上"]),
    (r"5[+-]?10|5\s*年|5年以|5-10", ["5-10年", "10年以上"]),
    (r"3-5|3年以", ["3-5年", "5-10年"]),
]

# 意愿性描述：可常驻/愿意去某地 ≠ 硬性工作地要求，遇到即跳过城市筛选
_REGION_WILLINGNESS = re.compile(r"可常驻|可接受|愿意|可到岗|接受调动|可外派|优先")


def _pick(profile: dict, *paths, default=None):
    """在画像 JSON（中文键）里按路径取第一个非空值。"""
    for path in paths:
        v = profile
        for key in path:
            if not isinstance(v, dict):
                v = None
                break
            v = v.get(key)
        if v not in (None, "", []):
            return v
    return default


def _derive_filters(profile: dict) -> dict:
    """从画像派生公共筛选（中文名，adapter._translate_filters 负责译成码值）。"""
    f: dict = {}
    age = _pick(profile, ["年龄范围"], default=None)
    if isinstance(age, (list, tuple)) and len(age) == 2:
        f["age"] = f"{age[0]},{age[1]}"
    years = str(_pick(profile, ["硬性门槛", "年限"], ["年限"], default="5年"))
    for pat, codes in _WORKYEARS_MAP:
        if re.search(pat, years):
            f["workyears"] = codes
            break
    edu = _pick(profile, ["学历要求"], default=None)
    if edu:
        text = str(edu)
        levels = []
        if "硕士" in text or "博士" in text or "研究生" in text:
            levels += ["硕士", "博士"]
        if "本科" in text or "本科及以上" in text:
            levels += ["本科"]
        if levels:
            f["eduLevels"] = list(dict.fromkeys(levels))
    region = _pick(profile, ["地域"], default=[])
    codes = _parse_region(region)
    if codes:
        f["dqs"], f["wantDqs"] = codes, codes
    # 主动求职 + 在职观望（看机会状态）
    f["userStatus"] = []
    return f


def _parse_region(region) -> list[str] | None:
    """地域 → 猎聘城市码（LPT 串码，如 上海 '020' / 深圳 '050090'）。
    解析不出/意愿性描述 → 返回 None（跳过筛选，不发垃圾值）。

    - '厦门'            → ['110030']
    - '厦门（可常驻）'  → None（意愿性，不构成硬性工作地要求）
    - '不限'/'全国'      → None
    """
    if isinstance(region, list):
        text = " ".join(str(r) for r in region)
    else:
        text = str(region or "").strip()
    if not text or _REGION_WILLINGNESS.search(text):
        return None
    cities = [c for c in CITY_CODE_MAP if c in text]
    if not cities:
        return None
    return [CITY_CODE_MAP[c] for c in cities]


def build_queries(profile: dict | None, preset: str = "full",
                  max_queries: int = 20) -> list[SearchQuery]:
    """生成搜索计划。profile 为画像 dict；为 None 时用种子词。"""
    profile = profile or {}
    presets = FILTER_PRESETS.get(preset, FILTER_PRESETS["full"])
    base_filters = _derive_filters(profile)

    companies = _pick(profile, ["硬性门槛", "目标公司"], default=DEFAULT_COMPANIES)
    titles = _pick(profile, ["岗位名称变体"], ["岗位名称"], default=DEFAULT_TITLES)
    tech = _pick(profile, ["技术关键词"], default=DEFAULT_TECH)

    if isinstance(companies, str):
        companies = [companies]
    if isinstance(titles, str):
        titles = [titles]
    if isinstance(tech, str):
        tech = [tech]

    queries: list[SearchQuery] = []
    if presets.get("company"):
        for c in companies:
            qf = dict(base_filters)          # 公司名作为关键词，不放进 companyKeys
            queries.append(SearchQuery(label="company", keyword=str(c), filters=qf))
    # 注：LPT 公司名精确过滤（companyKeys + compSearchFilter）未校准，暂不注入筛选——
    # company 型 query 直接用公司名做关键词检索简历内容；title/tech 型不限公司（先多召回，
    # 由 resume 抓取 + rule_score 收敛）。companyKeys 待单独校准后补回。
    if presets.get("title") and titles:
        for t in titles:
            queries.append(SearchQuery(label="title", keyword=str(t), filters=dict(base_filters)))
    if presets.get("tech") and tech:
        for kw in tech:
            queries.append(SearchQuery(label="tech", keyword=str(kw), filters=dict(base_filters)))
    return queries[:max_queries]
