"""岗位要求 PDF → 结构化画像。

流程：pypdf 抽文本 → LLM 结构化解析（配置了 key 时）→ 解析失败或未配置则规则兜底。
解析结果不落库，返回给前端 ProfileEditor 人工核对后保存。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pypdf

from .llm_client import LLMUnavailable, chat_json
from . import profile as P
from .paths import data_dir

# 目标公司候选词库（规则兜底用；命中即入硬性门槛目标公司）
_COMPANY_LEXICON = [
    "瞻芯电子", "三安光电", "芯联集成", "华润微", "积塔半导体", "芯粤能",
    "斯达半导", "士兰微", "时代电气", "中车时代", "比亚迪半导体", "华虹",
    "中芯国际", "台积电", "意法半导体", "英飞凌", "安森美", "博世",
    "比亚迪", "宁德时代", "国轩高科", "吉利", "汇川技术", "英飞源",
]
# 技术关键词词库（规则兜底用）
_TECH_LEXICON = ["碳化硅", "SiC", "IGBT", "MOSFET", "功率器件", "功率半导体", "光耦",
                 "封测", "芯片设计", "流片", "tape-out", "AEC-Q101", "车规", "晶圆", "器件"]
# 学历词
_EDU_LEXICON = ["985/211", "985", "211", "硕士", "博士", "研究生", "本科"]
# 岗位词（命中文本里最靠前的）
_TITLE_LEXICON = ["研发负责人", "技术总监", "技术负责人", "研发总监", "技术一号位",
                  "研发主管", "工程总监", "技术经理", "产品总监"]

def upload_dir() -> Path:
    return data_dir() / "uploads"


class ScanPdfError(Exception):
    """PDF 是扫描件，pypdf 抽不出文本。"""


def _extract_text(path: Path) -> str:
    try:
        reader = pypdf.PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # noqa: BLE001
        raise ScanPdfError(f"PDF 读取失败：{e}") from e
    if len(text.strip()) < 20:
        raise ScanPdfError("该 PDF 无法抽取文字（可能是扫描件/图片版）。请上传文字版 PDF。")
    return text


def _normalize_llm(p: dict) -> dict:
    """LLM 输出 → 规范化画像（对齐 seed 结构；缺字段补默认）。"""
    out = dict(P.default_seed_profile())
    out.update(p or {})
    out["五维权重"] = dict(P.DEFAULT_WEIGHTS)
    out["五维权重"].update((p or {}).get("五维权重", {}) or {})
    out["硬性门槛"] = dict(P.default_seed_profile()["硬性门槛"])
    out["硬性门槛"].update((p or {}).get("硬性门槛", {}) or {})
    for key in ("技术关键词", "岗位名称变体", "加分项"):
        v = out.get(key)
        if isinstance(v, str):
            out[key] = [v]
        elif not isinstance(v, list):
            out[key] = []
    return out


def _rule_parse(text: str) -> dict:
    """规则兜底：从文本抽公司/岗位/技术词/学历/年龄/年限，权重用默认。"""
    # NFKC 归一：pypdf 常把"子"抽成康熙部首异体"⼦"，导致词库匹配失败
    text = unicodedata.normalize("NFKC", text)
    prof = P.default_seed_profile()
    hit_companies = []
    for c in _COMPANY_LEXICON:
        if c in text and c not in hit_companies:
            hit_companies.append(c)
    if hit_companies:
        prof["硬性门槛"]["目标公司"] = hit_companies

    # 岗位名称变体：命中的岗位词，靠前的优先
    found_titles = [t for t in _TITLE_LEXICON if t in text]
    if found_titles:
        prof["岗位名称"] = found_titles[0]
        prof["岗位名称变体"] = found_titles[:6]

    tech = [t for t in _TECH_LEXICON if t in text]
    if tech:
        prof["技术关键词"] = tech[:8]

    edu = [e for e in _EDU_LEXICON if e in text]
    if edu:
        prof["学历要求"] = " / ".join(edu[:4])

    m = re.search(r"年龄[：:\s]*([0-9]{2})\s*[-–—至]\s*([0-9]{2})", text)
    if m:
        prof["年龄范围"] = [int(m.group(1)), int(m.group(2))]

    m = re.search(r"([0-9]+)\s*年", text)
    if m:
        prof["硬性门槛"]["年限"] = f"≥{m.group(1)} 年"

    return prof


def parse_pdf(path: Path) -> dict:
    """解析一份岗位要求 PDF。返回 {profile, source, text}：
    source ∈ {"llm", "rules"}，profile 为可编辑画像，text 为抽取的原文（供核对）。
    """
    text = _extract_text(path)
    try:
        llm_out = chat_json(
            f"请从以下岗位要求文本中提取结构化画像，严格输出 JSON，键如下：\n"
            f"{{\"岗位名称\":\"\",\"岗位名称变体\":[],\"硬性门槛\":{{\"目标公司\":[],\"年限\":\"\","
            f"\"必备背景\":\"\"}},\"加分项\":[],\"年龄范围\":[0,0],\"学历要求\":\"\","
            f"\"管理要求\":\"\",\"地域\":\"\",\"五维权重\":{{\"专业能力\":0,\"技术领导力\":0,"
            f"\"经营思维\":0,\"资源整合\":0,\"岗位契合度\":0}},\"技术关键词\":[],\"附加分规则\":{{\"上限\":10,"
            f"\"说明\":\"\"}}}}\n"
            f"要求：五维权重和为 100；目标公司填文本中明确提到的公司；没有的字段填空或空数组。\n"
            f"---\n{text[:6000]}",
            system="你是招聘领域的数据提取助手，只输出合法 JSON。")
        return {"profile": _normalize_llm(llm_out), "source": "llm", "text": text}
    except LLMUnavailable:
        return {"profile": _rule_parse(text), "source": "rules", "text": text}


def save_upload(filename: str, data: bytes) -> Path:
    """落盘上传文件（扩展名白名单 .pdf）。"""
    name = filename.lower()
    if not name.endswith(".pdf"):
        raise ValueError("仅支持 .pdf 文件")
    dest = upload_dir() / f"upload-{int(__import__('time').time() * 1000)}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
